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
    prev_close: Optional[float] = None
    gap_pct: Optional[float] = None

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
            "PrevClose": self.prev_close,
            "GapPct": self.gap_pct,
        }


def _simulate_trade(
    entry_date: pd.Timestamp,
    entry_price: float,
    code_prices: pd.DataFrame,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
    max_hold: int = MAX_HOLD_DAYS,
) -> tuple[pd.Timestamp, float, str]:
    """
    1銘柄のエグジットを日足ベースでシミュレートする。
    code_prices は当該 Code のみ Date 昇順ソート済みの DataFrame。

    日中の判定順序:
      - まず損切り判定（安値が SL ライン以下）
      - 次に利確判定（高値が TP ライン以上）
      - 両方発動する日は損切り優先（最悪ケース保守的評価）

    Returns: (exit_date, exit_price, exit_reason)
    """
    tp_price = entry_price * (1 + take_profit)
    sl_price = entry_price * (1 + stop_loss)

    holding = code_prices[code_prices["Date"] >= entry_date].reset_index(drop=True)

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


def build_price_dict(quotes_df: pd.DataFrame) -> dict:
    """quotes_df を Code 別に事前インデックス化して返す（スイープ高速化用）。"""
    price_cols = ["Code", "Date"]
    for col in ["AdjustmentOpen", "AdjustmentClose", "AdjustmentHigh", "AdjustmentLow",
                "Open", "Close", "High", "Low"]:
        if col in quotes_df.columns:
            price_cols.append(col)
    price_cols = list(dict.fromkeys(price_cols))
    price_df = quotes_df[price_cols].copy()
    return {
        code: grp.sort_values("Date").reset_index(drop=True)
        for code, grp in price_df.groupby("Code")
    }


def run_backtest(
    candidates: pd.DataFrame,
    quotes_df: pd.DataFrame,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
    max_hold: int = MAX_HOLD_DAYS,
    price_dict: Optional[dict] = None,
    entry_delay: int = 0,
) -> list[Trade]:
    """
    候補銘柄リストに対してバックテストを実行し Trade リストを返す。

    Parameters
    ----------
    candidates  : screen_candidates() の出力 DataFrame
    quotes_df   : 全期間・全銘柄の日足データ
    price_dict  : build_price_dict() の出力（スイープ時は外から渡して再利用）
    entry_delay : 0=決算翌日寄り(現行), 1=2日後寄り(初動を見てから入る)
    """
    trades: list[Trade] = []

    if price_dict is None:
        price_dict = build_price_dict(quotes_df)

    logger.info(f"バックテスト開始: {len(candidates)} 候補 (entry_delay={entry_delay})")

    for _, row in candidates.iterrows():
        code = str(row["Code"])
        orig_entry_date = pd.to_datetime(row["EntryDate"])

        code_prices = price_dict.get(code, pd.DataFrame())
        if code_prices.empty:
            continue

        if entry_delay > 0:
            # orig_entry_date 以降の行から entry_delay 日ずらした日の始値を使う
            future = code_prices[code_prices["Date"] >= orig_entry_date].reset_index(drop=True)
            if len(future) <= entry_delay:
                continue
            delayed_row = future.iloc[entry_delay]
            entry_date = delayed_row["Date"]
            open_col = "AdjustmentOpen" if "AdjustmentOpen" in delayed_row.index else "Open"
            _open = delayed_row.get(open_col)
            if _open is None or pd.isna(_open):
                close_col = "AdjustmentClose" if "AdjustmentClose" in delayed_row.index else "Close"
                _open = delayed_row.get(close_col, 0)
            entry_price = float(_open)
        else:
            entry_date = orig_entry_date
            entry_price = float(row["EntryOpen"])

        if entry_price <= 0:
            continue

        exit_date, exit_price, reason = _simulate_trade(
            entry_date, entry_price, code_prices,
            take_profit, stop_loss, max_hold,
        )

        prev_close = row.get("PrevClose")
        gap_pct = (
            (entry_price / prev_close) - 1
            if prev_close and prev_close > 0
            else None
        )
        trade = Trade(
            code=code,
            entry_date=entry_date,
            entry_price=entry_price,
            disclosed_date=row.get("DisclosedDate"),
            earnings_growth=row.get("EarningsGrowth"),
            market_cap=row.get("MarketCap"),
            prev_close=prev_close,
            gap_pct=gap_pct,
        )
        trade.close(exit_date, exit_price, reason)
        trades.append(trade)

    logger.info(f"バックテスト完了: {len(trades)} トレード")
    return trades
