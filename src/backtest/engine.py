"""バックテストエンジン。

エントリー条件:
  - 決算発表翌営業日の寄り付き（始値）でロング

エグジット条件 (優先順位順に評価):
  1. 高値が EntryOpen * (1 + TAKE_PROFIT) に到達 → 利確
  2. 安値が EntryOpen * (1 + STOP_LOSS) に到達   → 損切り
  3. MAX_HOLD_DAYS 経過 → 強制決済（終値）
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    code: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "take_profit" | "stop_loss" | "time_exit"
    pnl_pct: Optional[float] = None
    disclosed_date: Optional[pd.Timestamp] = None
    earnings_growth: Optional[float] = None
    market_cap: Optional[float] = None

    def close(self, exit_date: pd.Timestamp, exit_price: float, reason: str) -> None:
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.exit_reason = reason
        self.pnl_pct = (exit_price / self.entry_price) - 1

    def to_dict(self) -> dict:
        return {
            "Code": self.code,
            "DisclosedDate": self.disclosed_date,
            "EntryDate": self.entry_date,
            "EntryPrice": self.entry_price,
            "ExitDate": self.exit_date,
            "ExitPrice": self.exit_price,
            "ExitReason": self.exit_reason,
            "PnLPct": self.pnl_pct,
            "EarningsGrowth": self.earnings_growth,
            "MarketCap": self.market_cap,
        }


def _simulate_trade(
    code: str,
    entry_date: pd.Timestamp,
    entry_price: float,
    price_df: pd.DataFrame,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
    max_hold: int = MAX_HOLD_DAYS,
) -> tuple[pd.Timestamp, float, str]:
    """
    1銘柄のエグジットを日足ベースでシミュレートする。

    日中の判定順序:
      - まず損切り判定（安値が SL ライン以下）
      - 次に利確判定（高値が TP ライン以上）
      - 両方発動する日は損切り優先（最悪ケース保守的評価）

    Returns: (exit_date, exit_price, exit_reason)
    """
    tp_price = entry_price * (1 + take_profit)
    sl_price = entry_price * (1 + stop_loss)

    # エントリー日以降の行（エントリー日当日も保有中）
    mask = (price_df["Code"] == code) & (price_df["Date"] >= entry_date)
    holding = price_df[mask].sort_values("Date").reset_index(drop=True)

    for i, row in holding.iterrows():
        # MAX_HOLD_DAYS を超えたら強制決済（終値）
        if i >= max_hold:
            close_col = "AdjustmentClose" if "AdjustmentClose" in row.index else "Close"
            return row["Date"], float(row[close_col]), "time_exit"

        low_col = "AdjustmentLow" if "AdjustmentLow" in row.index else "Low"
        high_col = "AdjustmentHigh" if "AdjustmentHigh" in row.index else "High"
        close_col = "AdjustmentClose" if "AdjustmentClose" in row.index else "Close"

        day_low = float(row[low_col]) if not pd.isna(row.get(low_col)) else float(row[close_col])
        day_high = float(row[high_col]) if not pd.isna(row.get(high_col)) else float(row[close_col])
        day_close = float(row[close_col])

        # 損切り優先
        if day_low <= sl_price:
            return row["Date"], sl_price, "stop_loss"
        if day_high >= tp_price:
            return row["Date"], tp_price, "take_profit"

    # 保有期間内にデータが尽きた場合は最終日終値で決済
    if not holding.empty:
        last = holding.iloc[-1]
        close_col = "AdjustmentClose" if "AdjustmentClose" in last.index else "Close"
        return last["Date"], float(last[close_col]), "time_exit"

    return entry_date, entry_price, "no_data"


def run_backtest(
    candidates: pd.DataFrame,
    quotes_df: pd.DataFrame,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
    max_hold: int = MAX_HOLD_DAYS,
) -> list[Trade]:
    """
    候補銘柄リストに対してバックテストを実行し Trade リストを返す。

    Parameters
    ----------
    candidates : screen_candidates() の出力 DataFrame
    quotes_df  : 全期間・全銘柄の日足データ
    """
    trades: list[Trade] = []

    # 高速化：価格テーブルを必要カラムに絞る
    price_cols = ["Code", "Date"]
    for col in ["AdjustmentOpen", "AdjustmentClose", "AdjustmentHigh", "AdjustmentLow",
                "Open", "Close", "High", "Low"]:
        if col in quotes_df.columns:
            price_cols.append(col)
    price_cols = list(dict.fromkeys(price_cols))  # 重複除去・順序保持
    price_df = quotes_df[price_cols].copy()

    logger.info(f"バックテスト開始: {len(candidates)} 候補")

    for _, row in candidates.iterrows():
        code = str(row["Code"])
        entry_date = pd.to_datetime(row["EntryDate"])
        entry_price = float(row["EntryOpen"])

        if entry_price <= 0:
            continue

        exit_date, exit_price, reason = _simulate_trade(
            code, entry_date, entry_price, price_df,
            take_profit, stop_loss, max_hold,
        )

        trade = Trade(
            code=code,
            entry_date=entry_date,
            entry_price=entry_price,
            disclosed_date=row.get("DisclosedDate"),
            earnings_growth=row.get("EarningsGrowth"),
            market_cap=row.get("MarketCap"),
        )
        trade.close(exit_date, exit_price, reason)
        trades.append(trade)

    logger.info(f"バックテスト完了: {len(trades)} トレード")
    return trades
