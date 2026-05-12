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

    client = JQuantsClient()

    # データ取得
    listed_df = load_listed_info(client)
    logger.info(f"上場銘柄数: {len(listed_df)}")

    # 決算データは1年余分に取得（前年同期比計算のため）
    stmt_start = str(int(start[:4]) - 1) + start[4:]
    stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
    logger.info(f"財務データ件数: {len(stmt_df)}")

    quotes_df = load_daily_quotes(client, date_from=start, date_to=end)
    logger.info(f"日足データ件数: {len(quotes_df)}")

    # スクリーニング
    logger.info("スクリーニング実行中...")
    candidates = screen_candidates(stmt_df, quotes_df, listed_df)

    if candidates.empty:
        logger.warning("候補銘柄が見つかりませんでした。APIキー・データを確認してください。")
        sys.exit(1)

    logger.info(f"候補銘柄数: {len(candidates)}")

    # バックテスト
    trades = run_backtest(candidates, quotes_df)

    if not trades:
        logger.warning("トレードが生成されませんでした。")
        sys.exit(1)

    trades_df = trades_to_df(trades)
    metrics = compute_metrics(trades_df)

    print_metrics(metrics)
    save_results(trades_df, metrics)

    logger.info("完了。results/ フォルダに結果を保存しました。")


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

    # screen サブコマンド
    sc = sub.add_parser("screen", help="スクリーニング実行")
    sc.add_argument("--date", default=None, help="基準日 (YYYY-MM-DD、省略時は今日)")
    sc.set_defaults(func=cmd_screen)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
