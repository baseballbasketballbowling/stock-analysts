"""株式スクリーニングモジュール。

スクリーニング条件:
  1. 時価総額 300〜3,000億円
  2. 直近決算の営業利益が前年同期比 +20% 以上
"""

import logging
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional
import json

import pandas as pd

from config.settings import (
    MARKET_CAP_MIN,
    MARKET_CAP_MAX,
    EARNINGS_GROWTH_MIN,
    DATA_DIR,
)

logger = logging.getLogger(__name__)


class DataCache:
    """ディスクへのシンプルな JSON キャッシュ。"""

    def __init__(self, name: str):
        self.path = DATA_DIR / f"{name}.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> list:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: list) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def load_listed_info(client) -> pd.DataFrame:
    """上場銘柄基本情報をDataFrameで返す（キャッシュあり）。"""
    cache = DataCache("listed_info")
    if cache.exists():
        logger.info("上場銘柄情報をキャッシュから読み込み")
        raw = cache.load()
    else:
        logger.info("J-Quants: 上場銘柄情報を取得中...")
        raw = client.get_listed_info()
        cache.save(raw)

    df = pd.DataFrame(raw)
    # 数値型に変換
    for col in ["TotalMarketCap", "MarketCapitalization"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_statements(client, date_from: str, date_to: str) -> pd.DataFrame:
    """財務諸表データをDataFrameで返す（キャッシュあり）。"""
    cache_key = f"statements_{date_from}_{date_to}"
    cache = DataCache(cache_key)
    if cache.exists():
        logger.info("財務データをキャッシュから読み込み")
        raw = cache.load()
    else:
        logger.info(f"J-Quants: 財務データを取得中 ({date_from} 〜 {date_to})...")
        raw = client.get_statements(date_from=date_from, date_to=date_to)
        cache.save(raw)

    df = pd.DataFrame(raw)
    if df.empty:
        return df

    df["DisclosedDate"] = pd.to_datetime(df["DisclosedDate"])
    for col in ["OperatingProfit", "OperatingProfitPriorYear"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def load_daily_quotes(client, date_from: str, date_to: str) -> pd.DataFrame:
    """日足データをDataFrameで返す（キャッシュあり）。"""
    cache_key = f"quotes_{date_from}_{date_to}"
    cache = DataCache(cache_key)
    if cache.exists():
        logger.info("日足データをキャッシュから読み込み")
        raw = cache.load()
    else:
        logger.info(f"J-Quants: 日足データを取得中 ({date_from} 〜 {date_to})...")
        raw = client.get_daily_quotes(date_from=date_from, date_to=date_to)
        cache.save(raw)

    df = pd.DataFrame(raw)
    if df.empty:
        return df

    df["Date"] = pd.to_datetime(df["Date"])
    for col in ["Open", "High", "Low", "Close", "AdjustmentOpen",
                "AdjustmentClose", "Volume", "MarketCapitalization"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ------------------------------------------------------------------
# コア スクリーニング関数
# ------------------------------------------------------------------

def compute_earnings_growth(stmt_df: pd.DataFrame) -> pd.DataFrame:
    """
    各決算開示について営業利益の前年同期比を計算して返す。

    戻り値のカラム:
      Code, DisclosedDate, FiscalPeriodEnd, TypeOfDocument,
      OperatingProfit, OperatingProfitPriorYear, EarningsGrowth
    """
    if stmt_df.empty:
        return pd.DataFrame()

    needed = ["Code", "DisclosedDate", "FiscalPeriodEnd",
              "TypeOfDocument", "OperatingProfit", "OperatingProfitPriorYear"]
    missing = [c for c in needed if c not in stmt_df.columns]
    if missing:
        logger.warning(f"財務データに不足カラム: {missing}")
        # 利用可能なカラムで続行
        needed = [c for c in needed if c in stmt_df.columns]

    df = stmt_df[needed].copy()

    # 前年同期利益カラムが存在する場合はそのまま使用
    if "OperatingProfitPriorYear" in df.columns:
        df["EarningsGrowth"] = (
            df["OperatingProfit"] / df["OperatingProfitPriorYear"].replace(0, float("nan")) - 1
        )
    else:
        # フォールバック: 同一コード・同期を前年比で自前計算
        df = df.sort_values(["Code", "FiscalPeriodEnd"])
        df["OperatingProfitPriorYear"] = df.groupby("Code")["OperatingProfit"].shift(4)
        df["EarningsGrowth"] = (
            df["OperatingProfit"] / df["OperatingProfitPriorYear"] - 1
        )

    return df.dropna(subset=["EarningsGrowth"])


def screen_candidates(
    stmt_df: pd.DataFrame,
    quotes_df: pd.DataFrame,
    listed_df: pd.DataFrame,
    target_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    スクリーニング条件を適用して候補銘柄を返す。

    Parameters
    ----------
    stmt_df     : 財務データ
    quotes_df   : 日足株価データ
    listed_df   : 上場銘柄基本情報
    target_date : スクリーニング基準日 (YYYY-MM-DD)。省略時は全期間。

    Returns
    -------
    DataFrame with columns:
      Code, DisclosedDate, EarningsGrowth, MarketCap,
      EntryDate (翌営業日), EntryOpen
    """
    if stmt_df.empty or quotes_df.empty:
        logger.warning("データが空のため候補なし")
        return pd.DataFrame()

    growth_df = compute_earnings_growth(stmt_df)
    if growth_df.empty:
        return pd.DataFrame()

    # 1. 決算成長率フィルタ
    cond_growth = growth_df["EarningsGrowth"] >= EARNINGS_GROWTH_MIN
    passed = growth_df[cond_growth].copy()
    logger.info(f"  決算成長率 >= {EARNINGS_GROWTH_MIN:.0%}: {len(passed)} 件")

    if target_date:
        td = pd.to_datetime(target_date)
        passed = passed[passed["DisclosedDate"] <= td]

    # 2. 時価総額フィルタ（日足データの MarketCapitalization を使用）
    # 開示日の直近取引日の時価総額を参照
    cap_map = (
        quotes_df.sort_values("Date")
        .groupby("Code")[["Date", "MarketCapitalization"]]
        .last()
        .reset_index()
    )
    passed = passed.merge(cap_map[["Code", "MarketCapitalization"]], on="Code", how="left")

    # MarketCapitalization が欠損の場合は listed_info の TotalMarketCap を補完
    if "TotalMarketCap" in listed_df.columns:
        listed_cap = listed_df[["Code", "TotalMarketCap"]].rename(
            columns={"TotalMarketCap": "_cap_listed"}
        )
        passed = passed.merge(listed_cap, on="Code", how="left")
        mask = passed["MarketCapitalization"].isna()
        passed.loc[mask, "MarketCapitalization"] = passed.loc[mask, "_cap_listed"]
        passed.drop(columns=["_cap_listed"], inplace=True, errors="ignore")

    has_cap = passed["MarketCapitalization"].notna().any()
    if has_cap:
        cond_cap = passed["MarketCapitalization"].between(MARKET_CAP_MIN, MARKET_CAP_MAX)
        passed = passed[cond_cap].copy()
        logger.info(f"  時価総額 {MARKET_CAP_MIN/1e8:.0f}〜{MARKET_CAP_MAX/1e8:.0f}億円: {len(passed)} 件")
    else:
        logger.warning("  時価総額データなし（V2 API非対応）: 時価総額フィルタをスキップ")

    # 3. エントリー日（翌営業日）と始値を付与
    trading_dates = sorted(quotes_df["Date"].unique())
    trading_dates_ser = pd.Series(trading_dates)

    def next_trading_day(disclosed_date: pd.Timestamp) -> Optional[pd.Timestamp]:
        future = trading_dates_ser[trading_dates_ser > disclosed_date]
        return future.iloc[0] if not future.empty else None

    passed["EntryDate"] = passed["DisclosedDate"].apply(next_trading_day)
    passed = passed.dropna(subset=["EntryDate"])

    # エントリー日の始値取得
    open_map = quotes_df.set_index(["Code", "Date"])["AdjustmentOpen"].to_dict()
    # AdjustmentOpen がない場合は Open を使う
    if not open_map:
        open_map = quotes_df.set_index(["Code", "Date"])["Open"].to_dict()

    def get_open(row):
        key = (row["Code"], row["EntryDate"])
        val = open_map.get(key)
        if val is None or pd.isna(val):
            # AdjustmentOpen 不在 → Open を試みる
            open_map2 = quotes_df.set_index(["Code", "Date"])["Open"].to_dict()
            val = open_map2.get(key)
        return val

    passed["EntryOpen"] = passed.apply(get_open, axis=1)
    passed = passed.dropna(subset=["EntryOpen"])

    result = passed[[
        "Code", "DisclosedDate", "FiscalPeriodEnd" if "FiscalPeriodEnd" in passed.columns else "Code",
        "EarningsGrowth", "MarketCapitalization", "EntryDate", "EntryOpen",
    ]].copy()

    # FiscalPeriodEnd が結果にない場合は除外
    if "FiscalPeriodEnd" not in result.columns:
        result = passed[["Code", "DisclosedDate", "EarningsGrowth",
                          "MarketCapitalization", "EntryDate", "EntryOpen"]].copy()

    result.rename(columns={"MarketCapitalization": "MarketCap"}, inplace=True)
    result.reset_index(drop=True, inplace=True)

    logger.info(f"  最終候補: {len(result)} 件")
    return result
