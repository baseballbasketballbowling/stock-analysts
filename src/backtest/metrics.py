"""バックテストのパフォーマンス指標を計算・出力するモジュール。"""

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from config.settings import INITIAL_CAPITAL, POSITION_SIZE, RESULTS_DIR

logger = logging.getLogger(__name__)


def trades_to_df(trades) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([t.to_dict() for t in trades])


def compute_metrics(trades_df: pd.DataFrame) -> dict:
    """主要パフォーマンス指標を計算して辞書で返す。"""
    if trades_df.empty:
        return {}

    df = trades_df.dropna(subset=["PnLPct"]).copy()
    n = len(df)

    wins = df[df["PnLPct"] > 0]
    losses = df[df["PnLPct"] <= 0]

    win_rate = len(wins) / n if n > 0 else 0.0
    avg_win = wins["PnLPct"].mean() if not wins.empty else 0.0
    avg_loss = losses["PnLPct"].mean() if not losses.empty else 0.0

    # Profit Factor
    gross_profit = (wins["PnLPct"] * POSITION_SIZE).sum()
    gross_loss = abs((losses["PnLPct"] * POSITION_SIZE).sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # 期待値
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    # 資産曲線（順番にエントリー）
    df_sorted = df.sort_values("EntryDate").copy()
    df_sorted["PnLAbs"] = df_sorted["PnLPct"] * POSITION_SIZE
    df_sorted["CumPnL"] = df_sorted["PnLAbs"].cumsum()
    df_sorted["Equity"] = INITIAL_CAPITAL + df_sorted["CumPnL"]

    # 最大ドローダウン
    equity = df_sorted["Equity"].values
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_dd = drawdown.min()

    # 総損益
    total_pnl = df_sorted["PnLAbs"].sum()
    total_return = total_pnl / INITIAL_CAPITAL

    # エグジット理由別集計
    exit_counts = df["ExitReason"].value_counts().to_dict()

    metrics = {
        "total_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_pct": round(expectancy, 4),
        "total_pnl_jpy": round(total_pnl, 0),
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_dd, 4),
        "exit_reasons": exit_counts,
    }
    return metrics


def print_metrics(metrics: dict) -> None:
    print("\n" + "=" * 50)
    print("  バックテスト結果サマリー")
    print("=" * 50)
    print(f"  総トレード数        : {metrics.get('total_trades', 0):>8}")
    print(f"  勝率                : {metrics.get('win_rate', 0):>8.1%}")
    print(f"  平均利益 (勝ち)     : {metrics.get('avg_win_pct', 0):>8.2%}")
    print(f"  平均損失 (負け)     : {metrics.get('avg_loss_pct', 0):>8.2%}")
    print(f"  プロフィットファクタ: {metrics.get('profit_factor', 0):>8.2f}")
    print(f"  期待値              : {metrics.get('expectancy_pct', 0):>8.2%}")
    print(f"  総損益              : {metrics.get('total_pnl_jpy', 0):>10,.0f} 円")
    print(f"  総リターン          : {metrics.get('total_return', 0):>8.1%}")
    print(f"  最大ドローダウン    : {metrics.get('max_drawdown', 0):>8.1%}")
    print("  エグジット理由:")
    for reason, count in metrics.get("exit_reasons", {}).items():
        label = {"take_profit": "利確", "stop_loss": "損切り", "time_exit": "期間切れ"}.get(reason, reason)
        print(f"    {label:<12}: {count}")
    print("=" * 50 + "\n")


def save_results(trades_df: pd.DataFrame, metrics: dict) -> None:
    """結果ファイルを results/ に保存する。"""
    # CSV
    csv_path = RESULTS_DIR / "backtest_summary.csv"
    trades_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"トレード一覧を保存: {csv_path}")

    # JSON
    json_path = RESULTS_DIR / "backtest_metrics.json"
    json_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    logger.info(f"指標を保存: {json_path}")

    # 資産曲線
    _plot_equity_curve(trades_df)


def _plot_equity_curve(trades_df: pd.DataFrame) -> None:
    df = trades_df.dropna(subset=["PnLPct", "EntryDate"]).sort_values("EntryDate").copy()
    if df.empty:
        return

    df["PnLAbs"] = df["PnLPct"] * POSITION_SIZE
    df["CumPnL"] = df["PnLAbs"].cumsum()
    df["Equity"] = INITIAL_CAPITAL + df["CumPnL"]

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=False)
    fig.suptitle("バックテスト結果", fontsize=14, fontfamily="DejaVu Sans")

    # 資産曲線
    ax1 = axes[0]
    ax1.plot(range(len(df)), df["Equity"] / 1e6, color="steelblue", linewidth=1.5)
    ax1.axhline(INITIAL_CAPITAL / 1e6, color="gray", linestyle="--", linewidth=0.8)
    ax1.set_ylabel("Equity (million JPY)")
    ax1.set_title("Equity Curve")
    ax1.grid(True, alpha=0.3)

    # ドローダウン
    equity = df["Equity"].values
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak * 100

    ax2 = axes[1]
    ax2.fill_between(range(len(df)), dd, 0, color="tomato", alpha=0.5)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Trade #")
    ax2.set_title("Drawdown")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = RESULTS_DIR / "equity_curve.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"資産曲線を保存: {out_path}")
