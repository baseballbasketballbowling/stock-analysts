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
        candidates = screen_candidates(stmt_df, quotes_df, listed_df)

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

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
