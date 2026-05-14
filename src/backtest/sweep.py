"""パラメータスイープモジュール。

TP / SL / 最大保有日数 / 成長率フィルタ の組み合わせを全網羅し、
バックテスト結果を一覧で比較する。
"""

import itertools
import logging
from typing import Optional

import pandas as pd

from config.settings import RESULTS_DIR
from src.backtest.engine import build_price_dict, run_backtest
from src.backtest.metrics import trades_to_df, compute_metrics

logger = logging.getLogger(__name__)

PARAM_GRID = {
    "take_profit": [0.05, 0.07, 0.10, 0.15],
    "stop_loss":   [-0.02, -0.03, -0.05],
    "max_hold":    [5, 10, 20],
    "growth_min":  [0.10, 0.20, 0.30, 0.50],
}


def run_parameter_sweep(
    candidates_full: pd.DataFrame,
    quotes_df: pd.DataFrame,
    param_grid: Optional[dict] = None,
) -> pd.DataFrame:
    """
    candidates_full に対してパラメータグリッドでバックテストを実行する。

    candidates_full は screen_candidates を最も緩い growth_min で呼び出した結果。
    growth_min を変えるときは candidates_full をフィルタするだけでよい。

    Returns
    -------
    DataFrame with one row per parameter combination, sorted by PF desc.
    """
    if param_grid is None:
        param_grid = PARAM_GRID

    tp_list     = param_grid.get("take_profit", [0.07])
    sl_list     = param_grid.get("stop_loss",   [-0.03])
    hold_list   = param_grid.get("max_hold",    [10])
    growth_list = param_grid.get("growth_min",  [0.20])

    combos = list(itertools.product(tp_list, sl_list, hold_list, growth_list))
    logger.info(f"パラメータスイープ: {len(combos)} 通り")

    # price_dict は一度だけ構築して全組み合わせで共有（高速化）
    price_dict = build_price_dict(quotes_df)

    rows = []
    for i, (tp, sl, hold, growth) in enumerate(combos, 1):
        cands = candidates_full[candidates_full["EarningsGrowth"] >= growth].copy()
        if cands.empty:
            continue

        trades = run_backtest(
            cands, quotes_df,
            take_profit=tp, stop_loss=sl, max_hold=hold,
            price_dict=price_dict,
        )
        if not trades:
            continue

        m = compute_metrics(trades_to_df(trades))
        er = m.get("exit_reasons", {})

        rows.append({
            "TP":       f"{tp:.0%}",
            "SL":       f"{sl:.0%}",
            "MaxHold":  hold,
            "Growth":   f"{growth:.0%}",
            "Trades":   m.get("total_trades", 0),
            "WinRate":  round(m.get("win_rate", 0), 4),
            "AvgWin":   round(m.get("avg_win_pct", 0), 4),
            "AvgLoss":  round(m.get("avg_loss_pct", 0), 4),
            "PF":       round(m.get("profit_factor", 0), 3),
            "Expect":   round(m.get("expectancy_pct", 0), 4),
            "Return":   round(m.get("total_return", 0), 4),
            "MaxDD":    round(m.get("max_drawdown", 0), 4),
            "TP_cnt":   er.get("take_profit", 0),
            "SL_cnt":   er.get("stop_loss", 0),
            "Time_cnt": er.get("time_exit", 0),
        })

        if i % 20 == 0 or i == len(combos):
            logger.info(f"  スイープ進捗: {i}/{len(combos)}")

    if not rows:
        return pd.DataFrame()

    result_df = pd.DataFrame(rows).sort_values("PF", ascending=False).reset_index(drop=True)
    return result_df


def save_sweep_results(df: pd.DataFrame) -> None:
    path = RESULTS_DIR / "sweep_results.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"スイープ結果を保存: {path}")


def print_sweep_top(df: pd.DataFrame, n: int = 20) -> None:
    print(f"\n{'='*90}")
    print(f"  パラメータスイープ TOP {n}  (PF 降順)")
    print(f"{'='*90}")
    cols = ["TP", "SL", "MaxHold", "Growth", "Trades", "WinRate", "PF", "Expect", "Return", "MaxDD"]
    top = df[cols].head(n)
    # 読みやすい形式に変換
    fmt = top.copy()
    for c in ["WinRate", "Expect", "Return", "MaxDD"]:
        fmt[c] = fmt[c].apply(lambda x: f"{x:.1%}")
    print(fmt.to_string(index=True))
    print(f"{'='*90}\n")
