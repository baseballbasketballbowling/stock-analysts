"""保有ポジションの自動管理（約定・売却判定）と Threads 投稿モジュール。

ポジションのライフサイクル:
  pending（スクリーニングヒットで自動登録・約定待ち）
    → open（エントリー日の寄り付きで約定、TP/SL自動設定）
    → closed（TP/SL/保有期限のいずれかで自動クローズ → 売却シグナル投稿）
"""

import json
import logging
from pathlib import Path

import pandas as pd

from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = Path(__file__).parents[2] / "portfolio.json"

EXIT_REASON_JA = {
    "take_profit": "利確ライン到達",
    "stop_loss": "損切りライン到達",
    "time_exit": f"保有期限（{MAX_HOLD_DAYS}営業日）到達",
}


def load_portfolio() -> dict:
    if not PORTFOLIO_PATH.exists():
        return {"positions": [], "closed": []}
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save_portfolio(portfolio: dict) -> None:
    PORTFOLIO_PATH.write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def add_pending_position(
    portfolio: dict, code: str, company: str, entry_date: str, shares: int = 1
) -> bool:
    """スクリーニングヒット銘柄を約定待ちとして登録する。重複時は False。"""
    code = str(code)
    active = {str(p["code"]) for p in portfolio.get("positions", [])}
    if code in active:
        return False
    # 直近30日以内にクローズした銘柄は再登録しない（同一決算での重複防止）
    for c in portfolio.get("closed", []):
        if str(c.get("code")) == code and c.get("exit_date"):
            try:
                if abs((pd.Timestamp(entry_date) - pd.Timestamp(c["exit_date"])).days) <= 30:
                    return False
            except (ValueError, TypeError):
                pass
    portfolio.setdefault("positions", []).append({
        "code": code,
        "company": company,
        "shares": shares,
        "entry_price": None,
        "entry_date": str(entry_date)[:10],
        "tp_price": None,
        "sl_price": None,
        "status": "pending",
        "source": "auto",
    })
    return True


def fill_pending_positions(portfolio: dict, history: dict) -> list[dict]:
    """約定待ちポジションをエントリー日の寄り付きで約定させる。

    history: code → DataFrame(Date, Open, High, Low, Close) 昇順ソート済み
    """
    filled = []
    for pos in portfolio.get("positions", []):
        if pos.get("status") != "pending":
            continue
        df = history.get(str(pos["code"]))
        if df is None or df.empty:
            continue
        bars = df[df["Date"] >= pd.Timestamp(pos["entry_date"])]
        if bars.empty:
            continue  # エントリー日の日足がまだない（約定待ち継続）
        bar = bars.iloc[0]
        open_px = bar["Open"]
        if pd.isna(open_px) or open_px <= 0:
            continue
        pos["entry_date"] = bar["Date"].strftime("%Y-%m-%d")  # 休場ずれを実営業日に補正
        pos["entry_price"] = float(open_px)
        pos["tp_price"] = round(open_px * (1 + TAKE_PROFIT))
        pos["sl_price"] = round(open_px * (1 + STOP_LOSS))
        pos["status"] = "open"
        filled.append(pos)
    return filled


def check_exits(portfolio: dict, history: dict, max_hold_days: int = MAX_HOLD_DAYS) -> list[dict]:
    """openポジションのTP/SL/期限到達を日足High/Lowで判定し、クローズ処理する。

    判定はバックテストエンジンと同一（同日両到達は損切り優先、i >= max_hold で期限切れ）。
    エントリー日以降の全日足を毎回スキャンするため、実行が数日空いても取りこぼさない。
    """
    exits = []
    remaining = []
    for pos in portfolio.get("positions", []):
        if pos.get("status", "open") != "open" or not pos.get("entry_price"):
            remaining.append(pos)
            continue
        df = history.get(str(pos["code"]))
        if df is None or df.empty:
            remaining.append(pos)
            continue
        entry = float(pos["entry_price"])
        tp = float(pos.get("tp_price") or entry * (1 + TAKE_PROFIT))
        sl = float(pos.get("sl_price") or entry * (1 + STOP_LOSS))
        bars = df[df["Date"] >= pd.Timestamp(pos["entry_date"])].reset_index(drop=True)

        exit_info = None
        for i, bar in bars.iterrows():
            if i >= max_hold_days:
                exit_info = (bar["Date"], float(bar["Close"]), "time_exit")
                break
            low = bar["Low"] if pd.notna(bar["Low"]) else bar["Close"]
            high = bar["High"] if pd.notna(bar["High"]) else bar["Close"]
            if low <= sl:
                exit_info = (bar["Date"], sl, "stop_loss")
                break
            if high >= tp:
                exit_info = (bar["Date"], tp, "take_profit")
                break

        if exit_info:
            exit_date, exit_price, reason = exit_info
            closed = dict(pos)
            closed.pop("hold_days", None)
            closed.update({
                "exit_date": exit_date.strftime("%Y-%m-%d"),
                "exit_price": round(float(exit_price), 1),
                "exit_reason": reason,
                "pnl_pct": round(float(exit_price) / entry - 1, 4),
            })
            portfolio.setdefault("closed", []).append(closed)
            exits.append(closed)
        else:
            pos["hold_days"] = len(bars)  # 経過営業日（表示用）
            remaining.append(pos)

    portfolio["positions"] = remaining
    return exits


def _proximity_warn(price: float, tp: float, sl: float) -> str:
    if tp and price >= tp * 0.98:
        return " ⚠️TP接近"
    if sl and price <= sl * 1.02:
        return " ⚠️SL接近"
    return ""


def build_exit_alert_message(exits: list[dict], price_date: str) -> str:
    """売却シグナルのThreads投稿文。"""
    dt = pd.to_datetime(price_date).strftime("%m/%d")
    lines = [f"【🔔売却シグナル {dt}】", ""]
    for e in exits:
        company = e.get("company", e["code"])
        reason = EXIT_REASON_JA.get(e["exit_reason"], e["exit_reason"])
        exit_dt = pd.to_datetime(e["exit_date"]).strftime("%m/%d")
        lines.append(f"◆{company}")
        lines.append(f"  {exit_dt} {reason}")
        lines.append(
            f"  {e['entry_price']:,.0f}円 → {e['exit_price']:,.0f}円 ({e['pnl_pct']:+.1%})"
        )
        lines.append("")
    lines.append("ルール: 未売却なら翌営業日 寄成で売却")
    lines.append("#株式投資 #AIトレード #売却シグナル")
    return "\n".join(lines)


def build_morning_plan_message(portfolio: dict, prices: dict, price_date: str) -> str:
    """朝9:15用・今日の注文プラン（OCO指値の具体額を提示）。"""
    dt = pd.to_datetime(price_date).strftime("%m/%d")
    positions = portfolio.get("positions", [])
    pendings = [p for p in positions if p.get("status") == "pending"]
    opens = [p for p in positions if p.get("status", "open") == "open"]

    if not pendings and not opens:
        return f"【今日の注文プラン {dt}】\n本日の注文なし\n#株式投資 #AIトレード"

    lines = [f"【今日の注文プラン {dt}】", ""]

    if pendings:
        lines.append("▼新規買い")
        for p in pendings:
            lines.append(f"◆{p.get('company', p['code'])}  寄付き成行で買い")
        lines.append("")

    if opens:
        lines.append("▼売り注文（OCOセット推奨）")
        for p in opens:
            code = str(p["code"])
            tp, sl = p.get("tp_price"), p.get("sl_price")
            price = prices.get(code)
            lines.append(f"◆{p.get('company', code)}")
            if tp and sl:
                lines.append(f"  利確指値 {tp:,.0f}円 / 逆指値 {sl:,.0f}円")
            if price and p.get("entry_price"):
                pnl = price / p["entry_price"] - 1
                warn = _proximity_warn(price, tp or 0, sl or 0)
                lines.append(f"  前日終値 {price:,.0f}円 ({pnl:+.1%}){warn}")
            hold = p.get("hold_days")
            if hold:
                lines.append(f"  保有 {hold}/{MAX_HOLD_DAYS}日（残り{max(0, MAX_HOLD_DAYS - hold)}日）")
            lines.append("")

    lines.append("#株式投資 #AIトレード")
    return "\n".join(lines)


def build_portfolio_threads_message(portfolio: dict, prices: dict, price_date: str) -> str:
    """夜用・保有状況のThreads投稿文。"""
    positions = portfolio.get("positions", [])
    closed = portfolio.get("closed", [])
    dt = pd.to_datetime(price_date).strftime("%m/%d")

    if not positions and not closed:
        return f"【保有状況 {dt}】\n現在ポジションなし"

    lines = [f"【保有状況 {dt}】", ""]
    total_cost = 0.0
    total_value = 0.0

    for pos in positions:
        code = str(pos["code"])
        company = pos.get("company", code)
        if pos.get("status") == "pending":
            lines.append(f"◆{company}（{pos['entry_date']} 寄成 約定待ち）")
            lines.append("")
            continue

        shares = pos["shares"]
        entry = pos["entry_price"]
        tp, sl = pos.get("tp_price"), pos.get("sl_price")
        price = prices.get(code)
        cost = entry * shares
        total_cost += cost

        if price:
            value = price * shares
            total_value += value
            pnl_pct = (price / entry) - 1
            warn = _proximity_warn(price, tp or 0, sl or 0)
            pnl_str = f"{value - cost:+,.0f}円 ({pnl_pct:+.1%}){warn}"
            price_str = f"{price:,.0f}円"
        else:
            total_value += cost
            pnl_str = "取得中..."
            price_str = "-"

        hold = pos.get("hold_days")
        hold_str = f"  保有{hold}/{MAX_HOLD_DAYS}日" if hold else ""
        lines.append(f"◆{company} × {shares}株{hold_str}")
        lines.append(f"  取得 {entry:,.0f}円 → 現在 {price_str}")
        lines.append(f"  含み損益: {pnl_str}")
        if tp:
            lines.append(f"  TP {tp:,.0f}円 / SL {sl:,.0f}円")
        lines.append("")

    if total_cost > 0:
        total_pnl = total_value - total_cost
        lines.append(f"合計含み損益: {total_pnl:+,.0f}円 ({total_value / total_cost - 1:+.1%})")

    if closed:
        realized = sum(
            (c["exit_price"] - c["entry_price"]) * c.get("shares", 1)
            for c in closed
            if c.get("exit_price") and c.get("entry_price")
        )
        wins = sum(1 for c in closed if (c.get("pnl_pct") or 0) > 0)
        lines.append(f"確定損益累計: {realized:+,.0f}円（{wins}勝{len(closed) - wins}敗）")

    lines.append("#株式投資 #AIトレード #保有状況")
    return "\n".join(lines)
