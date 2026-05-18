"""保有ポジションの損益計算と Threads 投稿モジュール。"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = Path(__file__).parents[2] / "portfolio.json"


def load_portfolio() -> dict:
    if not PORTFOLIO_PATH.exists():
        return {"positions": [], "closed": []}
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save_portfolio(portfolio: dict) -> None:
    PORTFOLIO_PATH.write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def get_latest_prices(codes: list[str], quotes_df: pd.DataFrame) -> dict[str, float]:
    """quotes_df から各コードの最新終値を返す。"""
    prices = {}
    for code in codes:
        rows = quotes_df[quotes_df["Code"].astype(str) == str(code)]
        if rows.empty:
            # 4桁コードで再検索（末尾0除去）
            code4 = str(code).rstrip("0")
            rows = quotes_df[quotes_df["Code"].astype(str).str.startswith(code4)]
        if rows.empty:
            continue
        close_col = "AdjustmentClose" if "AdjustmentClose" in rows.columns else "Close"
        latest = rows.sort_values("Date").iloc[-1]
        prices[str(code)] = float(latest[close_col])
    return prices


def build_portfolio_threads_message(portfolio: dict, prices: dict, price_date: str) -> str:
    """保有状況のThreads投稿文を生成する。"""
    positions = portfolio.get("positions", [])
    if not positions:
        return f"【保有状況 {price_date}】\n現在ポジションなし"

    dt = pd.to_datetime(price_date).strftime("%m/%d")
    lines = [f"【保有状況 {dt}】", ""]

    total_cost = 0.0
    total_value = 0.0

    for pos in positions:
        code = str(pos["code"])
        company = pos.get("company", code)
        shares = pos["shares"]
        entry = pos["entry_price"]
        tp = pos.get("tp_price")
        sl = pos.get("sl_price")

        price = prices.get(code)
        cost = entry * shares
        total_cost += cost

        if price:
            value = price * shares
            total_value += value
            pnl = value - cost
            pnl_pct = (price / entry) - 1
            pnl_str = f"{pnl:+,.0f}円 ({pnl_pct:+.1%})"
            price_str = f"{price:,.0f}円"
        else:
            total_value += cost
            pnl_str = "取得中..."
            price_str = "-"

        lines.append(f"◆{company} × {shares}株")
        lines.append(f"  取得 {entry:,.0f}円 → 現在 {price_str}")
        lines.append(f"  含み損益: {pnl_str}")
        if tp:
            lines.append(f"  TP {tp:,.0f}円 / SL {sl:,.0f}円")
        lines.append("")

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_value / total_cost - 1) if total_cost > 0 else 0
    lines.append(f"合計含み損益: {total_pnl:+,.0f}円 ({total_pnl_pct:+.1%})")
    lines.append("#株式投資 #AIトレード #保有状況")
    return "\n".join(lines)
