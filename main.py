#!/usr/bin/env python3
"""
株式スクリーニング & バックテスト CLI

使い方:
  python main.py backtest [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  python main.py screen   [--date YYYY-MM-DD]
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

# dotenv があれば .env を読み込む
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config.settings import BACKTEST_START, BACKTEST_END

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_backtest(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
    )
    from src.backtest.engine import run_backtest
    from src.backtest.metrics import trades_to_df, compute_metrics, print_metrics, save_results

    start = args.start
    end = args.end

    logger.info(f"バックテスト期間: {start} 〜 {end}")

    try:
        logger.info("STEP1: クライアント初期化")
        client = JQuantsClient()

        logger.info("STEP2: 上場銘柄取得")
        listed_df = load_listed_info(client)
        logger.info(f"上場銘柄数: {len(listed_df)}")

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        logger.info(f"STEP3: 財務データ取得 ({stmt_start} 〜 {end})")
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        logger.info(f"財務データ件数: {len(stmt_df)}")

        logger.info(f"STEP4: 日足データ取得 ({start} 〜 {end})")
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)
        logger.info(f"日足データ件数: {len(quotes_df)}")

        logger.info("STEP5: スクリーニング")
        candidates = screen_candidates(stmt_df, quotes_df, listed_df, roe_min=0.12)

        if candidates.empty:
            logger.warning("候補銘柄が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"候補銘柄数: {len(candidates)}")

        logger.info("STEP6: バックテスト")
        trades = run_backtest(candidates, quotes_df)

        if not trades:
            logger.warning("トレードが生成されませんでした。")
            sys.exit(1)

        trades_df = trades_to_df(trades)
        metrics = compute_metrics(trades_df)
        print_metrics(metrics)
        save_results(trades_df, metrics)
        logger.info("完了。results/ フォルダに結果を保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_screen_sweep(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.screening_sweep import (
        run_screening_sweep, save_screening_sweep_results, print_screening_sweep_top,
    )

    start = args.start
    end   = args.end
    logger.info(f"スクリーニング条件スイープ期間: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df   = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        # 緩い閾値で全候補を取得（追加指標カラムも含む）
        logger.info("ベーススクリーニング（成長率 >= 5%）")
        all_candidates = screen_candidates(
            stmt_df, quotes_df, listed_df,
            earnings_growth_min=0.05,
        )

        if all_candidates.empty:
            logger.warning("候補が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"全候補数: {len(all_candidates)}")
        logger.info(f"利用可能な指標列: {[c for c in all_candidates.columns if c not in ('Code','DisclosedDate','FiscalPeriodEnd','MarketCap','EntryDate','EntryOpen')]}")

        sweep_df = run_screening_sweep(all_candidates, quotes_df)

        if sweep_df.empty:
            logger.warning("スイープ結果が空です。")
            sys.exit(1)

        save_screening_sweep_results(sweep_df)
        print_screening_sweep_top(sweep_df, n=30)
        logger.info("完了。results/screening_sweep_results.csv に保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_sweep(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.sweep import PARAM_GRID, run_parameter_sweep, save_sweep_results, print_sweep_top

    start = args.start
    end   = args.end
    logger.info(f"パラメータスイープ期間: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        # 最も緩い成長率フィルタで候補を取得（スイープ内で再フィルタする）
        min_growth = min(PARAM_GRID["growth_min"])
        logger.info(f"ベーススクリーニング（成長率 >= {min_growth:.0%}）")
        candidates_full = screen_candidates(
            stmt_df, quotes_df, listed_df,
            earnings_growth_min=min_growth,
        )

        if candidates_full.empty:
            logger.warning("候補銘柄が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"ベース候補数: {len(candidates_full)}")
        sweep_df = run_parameter_sweep(candidates_full, quotes_df)

        if sweep_df.empty:
            logger.warning("スイープ結果が空です。")
            sys.exit(1)

        save_sweep_results(sweep_df)
        print_sweep_top(sweep_df, n=20)
        logger.info("完了。results/sweep_results.csv に保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_screen(args) -> None:
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
    )

    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    start = str(int(target_date[:4]) - 2) + target_date[4:]  # 2年前から取得

    client = JQuantsClient()
    listed_df = load_listed_info(client)
    stmt_df = load_statements(client, date_from=start, date_to=target_date)
    quotes_df = load_daily_quotes(client, date_from=start, date_to=target_date)

    candidates = screen_candidates(stmt_df, quotes_df, listed_df, target_date=target_date)

    if candidates.empty:
        print("候補銘柄なし")
        return

    pd_import()
    print(f"\n=== スクリーニング結果 ({target_date}) ===")
    print(candidates.to_string(index=False))
    print(f"\n計: {len(candidates)} 銘柄")


def _build_notify_message(candidates, target_date: str, lookback_days: int, listed_df=None) -> str:
    """メール本文を組み立てる。"""
    import pandas as pd
    from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS

    # 企業名マップ（5桁/4桁コード両対応）
    name_map = {}
    if listed_df is not None:
        for col in ["CompanyName", "Name", "会社名"]:
            if col in listed_df.columns:
                for _, r in listed_df[["Code", col]].iterrows():
                    c = str(r["Code"])
                    name_map[c] = r[col]
                    if len(c) == 5 and c.endswith("0"):
                        name_map[c[:-1]] = r[col]
                break

    n = len(candidates)
    date_label = target_date if lookback_days == 1 else f"直近{lookback_days}日"
    lines = [
        f"[株式スクリーニング {target_date}]",
        f"{date_label}の決算開示から {n}件 が条件に合致しました。",
        "",
    ]

    for _, row in candidates.iterrows():
        code = str(row.get("Code", "????"))
        company = name_map.get(code, "")
        disclosed = pd.to_datetime(row.get("DisclosedDate")).strftime("%m/%d")
        per_type = row.get("CurPerType", "")
        eg   = row.get("EarningsGrowth", float("nan"))
        sg   = row.get("SalesGrowth", float("nan"))
        om   = row.get("OperatingMargin", float("nan"))
        roe  = row.get("ROE", float("nan"))
        cap  = row.get("MarketCap", float("nan"))
        rsi  = row.get("RSI14", float("nan"))
        fg   = row.get("ForwardGuidance", float("nan"))
        s17  = row.get("S17Nm", "")
        entry_date  = row.get("EntryDate")
        entry_open  = row.get("EntryOpen")
        prev_close  = row.get("PrevClose")

        def pct(v, plus=True):
            if pd.isna(v): return "-"
            return (f"+{v:.1%}" if v >= 0 and plus else f"{v:.1%}")

        cap_str   = f"{cap/1e8:.0f}億円" if pd.notna(cap) else "-"
        rsi_str   = f"{rsi:.0f}" if pd.notna(rsi) else "-"
        entry_str = pd.to_datetime(entry_date).strftime("%m/%d") if pd.notna(entry_date) else "TBD"
        price_str = f"  {entry_open:,.0f}円" if pd.notna(entry_open) else ""

        gap_str = ""
        if pd.notna(entry_open) and pd.notna(prev_close) and prev_close > 0:
            gap = (entry_open / prev_close) - 1
            direction = "↑GU" if gap > 0.01 else ("↓GD" if gap < -0.01 else "→FL")
            gap_str = f"  前日比{gap:+.1%} {direction}"

        header = f"◆ {code}"
        if company:
            header += f"  {company}"
        header += f"  ({per_type} / {disclosed}開示)"
        lines.append(header)
        if s17:
            lines.append(f"  業種: {s17}  時価総額: {cap_str}")
        else:
            lines.append(f"  時価総額: {cap_str}")
        lines.append(f"  営業利益成長: {pct(eg)}  売上成長: {pct(sg)}")
        lines.append(f"  営業利益率: {pct(om, False)}  ROE: {pct(roe, False)}")
        rsi_fg = f"  RSI14: {rsi_str}"
        if pd.notna(fg):
            rsi_fg += f"  来期予想: {pct(fg)}"
        lines.append(rsi_fg)
        lines.append(f"  エントリー予定: {entry_str}寄付き{price_str}{gap_str}")
        lines.append("")

    lines += [
        "─" * 40,
        f"利確: +{TAKE_PROFIT:.0%}  損切り: {STOP_LOSS:.0%}  最大保有: {MAX_HOLD_DAYS}日",
        "※ 翌営業日始値でエントリー。価格未確定の場合は TBD。",
    ]
    return "\n".join(lines)


def cmd_notify(args) -> None:
    import os
    import traceback as _tb
    from datetime import timedelta
    import pandas as pd
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
    )
    from src.notifier.email_notify import send_email
    from config.settings import RESULTS_DIR

    gmail_user = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    notify_to = os.environ.get("NOTIFY_TO") or gmail_user
    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    lookback_days = args.lookback_days
    target_dt = pd.to_datetime(target_date)

    logger.info(f"毎日スクリーニング: {target_date} (遡及{lookback_days}日)")

    # 財務YoY比較のため2年分のstatement、market cap/RSI用に90日分のquotes
    stmt_start = str(int(target_date[:4]) - 2) + target_date[4:]
    quotes_start = (target_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        logger.info(f"財務データ取得: {stmt_start} 〜 {target_date}")
        stmt_df = load_statements(client, date_from=stmt_start, date_to=target_date)

        logger.info(f"日足データ取得: {quotes_start} 〜 {target_date}")
        quotes_df = load_daily_quotes(client, date_from=quotes_start, date_to=target_date)

        if stmt_df.empty or quotes_df.empty:
            logger.info("データが空です。終了。")
            return

        # 全候補スクリーニング（EntryOpenなしも含む = 当日開示銘柄対応）
        all_candidates = screen_candidates(
            stmt_df, quotes_df, listed_df,
            roe_min=0.12,
            require_entry_price=False,
        )

        if all_candidates.empty:
            logger.info("条件に合致する銘柄なし。通知しません。")
            return

        # 指定期間内の開示に絞る
        cutoff = target_dt - timedelta(days=lookback_days - 1)
        recent = all_candidates[
            (all_candidates["DisclosedDate"] >= cutoff) &
            (all_candidates["DisclosedDate"] <= target_dt)
        ].copy()

        logger.info(f"直近{lookback_days}日の候補: {len(recent)} 件")

        # CSV保存
        RESULTS_DIR.mkdir(exist_ok=True)
        csv_path = RESULTS_DIR / f"daily_screen_{target_date}.csv"
        recent.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"保存: {csv_path}")

        if recent.empty:
            logger.info(f"直近{lookback_days}日の開示で条件に合致する銘柄なし。通知しません。")
            return

        body = _build_notify_message(recent, target_date, lookback_days, listed_df=listed_df)
        subject = f"【株式スクリーニング】{len(recent)}件ヒット ({target_date})"
        print(body)

        if not gmail_user or not app_password:
            logger.warning("GMAIL_USER / GMAIL_APP_PASSWORD 未設定。メール送信をスキップ（結果はCSVに保存済み）。")
            return

        send_email(gmail_user, app_password, notify_to, subject, body)

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_entry_delay(args) -> None:
    """決算翌日寄り vs 2日後寄り の比較分析。"""
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.engine import run_backtest, build_price_dict
    from src.backtest.metrics import trades_to_df, compute_metrics

    start = args.start
    end = args.end
    logger.info(f"エントリータイミング比較分析: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        candidates = screen_candidates(stmt_df, quotes_df, listed_df, roe_min=0.12)
        if candidates.empty:
            logger.warning("候補銘柄なし")
            return

        price_dict = build_price_dict(quotes_df)
        logger.info(f"候補銘柄数: {len(candidates)}")

        print("\n" + "=" * 65)
        print("  【比較】エントリータイミング: 決算翌日寄り vs 2日後寄り")
        print("=" * 65)

        label_map = {"take_profit": "利確  ", "stop_loss": "損切り", "time_exit": "期間切れ"}
        for delay, label in [(0, "決算翌日寄り (現行, Day+1)"), (1, "2日後寄り  (1日待ち, Day+2)")]:
            trades = run_backtest(
                candidates, quotes_df,
                price_dict=price_dict, entry_delay=delay,
            )
            if not trades:
                print(f"\n[{label}] トレードなし")
                continue
            df = trades_to_df(trades)
            m = compute_metrics(df)
            print(f"\n--- {label} ---")
            print(f"  トレード数   : {m.get('total_trades', 0)}")
            print(f"  勝率         : {m.get('win_rate', 0):.1%}")
            print(f"  PF           : {m.get('profit_factor', 0):.2f}")
            print(f"  期待値       : {m.get('expectancy_pct', 0):+.2%}")
            print(f"  総リターン   : {m.get('total_return', 0):+.1%}")
            print(f"  最大DD       : {m.get('max_drawdown', 0):.1%}")
            print(f"  平均利益     : {m.get('avg_win_pct', 0):+.2%}")
            print(f"  平均損失     : {m.get('avg_loss_pct', 0):+.2%}")
            exit_d = m.get("exit_detail", {})
            if exit_d:
                print("  エグジット内訳 (件数 / 勝率 / 平均損益 / 平均保有):")
                for reason, d in exit_d.items():
                    rl = label_map.get(reason, reason)
                    hold = f"{d['avg_hold_days']:.0f}日" if d.get("avg_hold_days") else "-"
                    print(f"    {rl}: {d['count']:>3}件  勝率{d['win_rate']:.1%}  avg{d['avg_pnl']:+.2%}  保有{hold}")

        print("\n" + "=" * 65)
        logger.info("完了")

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def pd_import():
    import pandas as pd
    return pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="J-Quants 株式スクリーニング & バックテスト",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest サブコマンド
    bt = sub.add_parser("backtest", help="バックテスト実行")
    bt.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    bt.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    bt.set_defaults(func=cmd_backtest)

    # screen_sweep サブコマンド
    ss = sub.add_parser("screen_sweep", help="スクリーニング条件スイープ実行")
    ss.add_argument("--start", default=BACKTEST_START)
    ss.add_argument("--end",   default=BACKTEST_END)
    ss.set_defaults(func=cmd_screen_sweep)

    # sweep サブコマンド
    sw = sub.add_parser("sweep", help="パラメータスイープ実行")
    sw.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    sw.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    sw.set_defaults(func=cmd_sweep)

    # screen サブコマンド
    sc = sub.add_parser("screen", help="スクリーニング実行")
    sc.add_argument("--date", default=None, help="基準日 (YYYY-MM-DD、省略時は今日)")
    sc.set_defaults(func=cmd_screen)

    # entry_delay サブコマンド
    ed = sub.add_parser("entry_delay", help="エントリータイミング比較 (決算翌日 vs 2日後)")
    ed.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    ed.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    ed.set_defaults(func=cmd_entry_delay)

    # notify サブコマンド
    nt = sub.add_parser("notify", help="毎日スクリーニング & LINE通知")
    nt.add_argument("--date", default=None, help="スクリーニング基準日 (YYYY-MM-DD、省略時は今日)")
    nt.add_argument("--lookback-days", type=int, default=1,
                    help="何日前まで遡って開示をチェックするか (デフォルト: 1)")
    nt.set_defaults(func=cmd_notify)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
