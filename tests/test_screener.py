"""スクリーニングロジックの単体テスト（モックデータ使用）。"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import pytest

from src.screener.screener import compute_earnings_growth, screen_candidates
from config.settings import MARKET_CAP_MIN, MARKET_CAP_MAX, EARNINGS_GROWTH_MIN


def make_stmt_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["DisclosedDate"] = pd.to_datetime(df["DisclosedDate"])
    df["OperatingProfit"] = df["OperatingProfit"].astype(float)
    df["OperatingProfitPriorYear"] = df["OperatingProfitPriorYear"].astype(float)
    return df


def make_quotes_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    for col in ["Open", "AdjustmentOpen", "High", "AdjustmentHigh",
                "Low", "AdjustmentLow", "Close", "AdjustmentClose", "MarketCapitalization"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


# ------------------------------------------------------------------
# compute_earnings_growth
# ------------------------------------------------------------------

class TestComputeEarningsGrowth:
    def test_basic_growth(self):
        df = make_stmt_df([{
            "Code": "1234",
            "DisclosedDate": "2024-02-14",
            "FiscalPeriodEnd": "2023-12-31",
            "TypeOfDocument": "Q4",
            "OperatingProfit": 120,
            "OperatingProfitPriorYear": 100,
        }])
        result = compute_earnings_growth(df)
        assert len(result) == 1
        assert abs(result.iloc[0]["EarningsGrowth"] - 0.20) < 1e-9

    def test_negative_growth_included(self):
        df = make_stmt_df([{
            "Code": "1234",
            "DisclosedDate": "2024-02-14",
            "FiscalPeriodEnd": "2023-12-31",
            "TypeOfDocument": "Q4",
            "OperatingProfit": 80,
            "OperatingProfitPriorYear": 100,
        }])
        result = compute_earnings_growth(df)
        assert len(result) == 1
        assert result.iloc[0]["EarningsGrowth"] == pytest.approx(-0.20)

    def test_zero_prior_year_dropped(self):
        df = make_stmt_df([{
            "Code": "1234",
            "DisclosedDate": "2024-02-14",
            "FiscalPeriodEnd": "2023-12-31",
            "TypeOfDocument": "Q4",
            "OperatingProfit": 120,
            "OperatingProfitPriorYear": 0,
        }])
        result = compute_earnings_growth(df)
        # 0除算でNaN → dropnaで除外される
        assert len(result) == 0

    def test_empty_input(self):
        result = compute_earnings_growth(pd.DataFrame())
        assert result.empty


# ------------------------------------------------------------------
# screen_candidates
# ------------------------------------------------------------------

class TestScreenCandidates:
    def _base_data(self):
        cap_in_range = (MARKET_CAP_MIN + MARKET_CAP_MAX) / 2  # 1650億円

        stmt_df = make_stmt_df([{
            "Code": "1234",
            "DisclosedDate": "2024-02-14",
            "FiscalPeriodEnd": "2023-12-31",
            "TypeOfDocument": "Q4",
            "OperatingProfit": 130,
            "OperatingProfitPriorYear": 100,  # +30%
        }])

        # 2/14 (開示日) の翌営業日 = 2/15
        quotes = []
        for d in pd.date_range("2024-02-13", "2024-02-20"):
            quotes.append({
                "Code": "1234",
                "Date": d.strftime("%Y-%m-%d"),
                "Open": 1000.0,
                "AdjustmentOpen": 1000.0,
                "High": 1050.0,
                "AdjustmentHigh": 1050.0,
                "Low": 970.0,
                "AdjustmentLow": 970.0,
                "Close": 1020.0,
                "AdjustmentClose": 1020.0,
                "MarketCapitalization": cap_in_range,
            })
        quotes_df = make_quotes_df(quotes)
        listed_df = pd.DataFrame([{"Code": "1234", "TotalMarketCap": cap_in_range}])
        return stmt_df, quotes_df, listed_df

    def test_passes_all_conditions(self):
        stmt_df, quotes_df, listed_df = self._base_data()
        result = screen_candidates(stmt_df, quotes_df, listed_df)
        assert len(result) == 1
        assert result.iloc[0]["Code"] == "1234"

    def test_fails_market_cap_too_small(self):
        stmt_df, quotes_df, listed_df = self._base_data()
        quotes_df["MarketCapitalization"] = MARKET_CAP_MIN / 2
        listed_df["TotalMarketCap"] = MARKET_CAP_MIN / 2
        result = screen_candidates(stmt_df, quotes_df, listed_df)
        assert result.empty

    def test_fails_market_cap_too_large(self):
        stmt_df, quotes_df, listed_df = self._base_data()
        quotes_df["MarketCapitalization"] = MARKET_CAP_MAX * 2
        listed_df["TotalMarketCap"] = MARKET_CAP_MAX * 2
        result = screen_candidates(stmt_df, quotes_df, listed_df)
        assert result.empty

    def test_fails_earnings_growth_below_threshold(self):
        stmt_df, quotes_df, listed_df = self._base_data()
        # +10% → 閾値20%未満
        stmt_df["OperatingProfit"] = 110
        result = screen_candidates(stmt_df, quotes_df, listed_df)
        assert result.empty

    def test_empty_stmt(self):
        _, quotes_df, listed_df = self._base_data()
        result = screen_candidates(pd.DataFrame(), quotes_df, listed_df)
        assert result.empty


# ------------------------------------------------------------------
# バックテストエンジン
# ------------------------------------------------------------------

class TestBacktestEngine:
    def _make_price_df(self, code: str, entry_price: float, daily_returns: list[float]) -> pd.DataFrame:
        records = []
        dates = pd.date_range("2024-01-05", periods=len(daily_returns) + 1, freq="B")
        prices = [entry_price]
        for r in daily_returns:
            prices.append(prices[-1] * (1 + r))

        for i, (d, p) in enumerate(zip(dates, prices)):
            records.append({
                "Code": code,
                "Date": d,
                "Open": p,
                "AdjustmentOpen": p,
                "High": p * 1.01,
                "AdjustmentHigh": p * 1.01,
                "Low": p * 0.99,
                "AdjustmentLow": p * 0.99,
                "Close": p,
                "AdjustmentClose": p,
                "MarketCapitalization": 500e8,
            })
        return pd.DataFrame(records)

    def test_take_profit_triggered(self):
        from src.backtest.engine import run_backtest
        from src.screener.screener import screen_candidates

        # 毎日+2%上昇 → 4日目で +8% 超えて利確
        daily_rets = [0.02] * 15
        price_df = self._make_price_df("9999", 1000.0, daily_rets)

        candidates = pd.DataFrame([{
            "Code": "9999",
            "DisclosedDate": pd.Timestamp("2024-01-04"),
            "FiscalPeriodEnd": "2023-12-31",
            "EarningsGrowth": 0.30,
            "MarketCap": 500e8,
            "EntryDate": pd.Timestamp("2024-01-05"),
            "EntryOpen": 1000.0,
        }])

        trades = run_backtest(candidates, price_df)
        assert len(trades) == 1
        assert trades[0].exit_reason == "take_profit"
        assert trades[0].pnl_pct == pytest.approx(0.07, abs=1e-6)

    def test_stop_loss_triggered(self):
        from src.backtest.engine import run_backtest

        # 毎日-1.5%下落 → 2日目で -3% 超えて損切り
        daily_rets = [-0.015] * 15
        price_df = self._make_price_df("9998", 1000.0, daily_rets)
        # 安値を SL 以下に設定
        price_df["AdjustmentLow"] = price_df["AdjustmentLow"] * 0.96

        candidates = pd.DataFrame([{
            "Code": "9998",
            "DisclosedDate": pd.Timestamp("2024-01-04"),
            "FiscalPeriodEnd": "2023-12-31",
            "EarningsGrowth": 0.30,
            "MarketCap": 500e8,
            "EntryDate": pd.Timestamp("2024-01-05"),
            "EntryOpen": 1000.0,
        }])

        trades = run_backtest(candidates, price_df)
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].pnl_pct == pytest.approx(-0.03, abs=1e-6)

    def test_time_exit_triggered(self):
        from src.backtest.engine import run_backtest

        # ほぼ横ばい（0.1%ずつ）→ MAX_HOLD_DAYS で強制決済
        daily_rets = [0.001] * 20
        price_df = self._make_price_df("9997", 1000.0, daily_rets)

        candidates = pd.DataFrame([{
            "Code": "9997",
            "DisclosedDate": pd.Timestamp("2024-01-04"),
            "FiscalPeriodEnd": "2023-12-31",
            "EarningsGrowth": 0.30,
            "MarketCap": 500e8,
            "EntryDate": pd.Timestamp("2024-01-05"),
            "EntryOpen": 1000.0,
        }])

        trades = run_backtest(candidates, price_df)
        assert len(trades) == 1
        assert trades[0].exit_reason == "time_exit"
