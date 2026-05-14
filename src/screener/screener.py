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
    for col in ["OperatingProfit", "OperatingProfitPriorYear",
                "Sales", "NP", "Eq", "EqAR", "NxFOP"]:
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

def compute_financial_metrics(stmt_df: pd.DataFrame) -> pd.DataFrame:
    """
    各決算開示の財務指標をまとめて計算して返す。

    Returns columns (利用可能なもの):
      Code, DisclosedDate, FiscalPeriodEnd, TypeOfDocument, CurPerType,
      OperatingProfit, EarningsGrowth,
      Sales, SalesGrowth, OperatingMargin,
      ROE, EquityRatio, ForwardGuidance
    """
    if stmt_df.empty:
        return pd.DataFrame()

    wanted = [
        "Code", "DisclosedDate", "FiscalPeriodEnd", "TypeOfDocument", "CurPerType",
        "OperatingProfit", "OperatingProfitPriorYear",
        "Sales", "NP", "Eq", "EqAR", "NxFOP",
    ]
    df = stmt_df[[c for c in wanted if c in stmt_df.columns]].copy()

    sort_by = [c for c in ["Code", "FiscalPeriodEnd"] if c in df.columns]
    if sort_by:
        df = df.sort_values(sort_by)

    # --- 営業利益 YoY ---
    if "OperatingProfitPriorYear" in df.columns:
        df["EarningsGrowth"] = (
            df["OperatingProfit"] / df["OperatingProfitPriorYear"].replace(0, float("nan")) - 1
        )
    else:
        df["OperatingProfitPriorYear"] = df.groupby("Code")["OperatingProfit"].shift(4)
        df["EarningsGrowth"] = (
            df["OperatingProfit"] / df["OperatingProfitPriorYear"].replace(0, float("nan")) - 1
        )

    # --- 売上高 YoY ---
    if "Sales" in df.columns:
        df["SalesPriorYear"] = df.groupby("Code")["Sales"].shift(4)
        df["SalesGrowth"] = (
            df["Sales"] / df["SalesPriorYear"].replace(0, float("nan")) - 1
        )

    # --- 営業利益率 (OP / Sales) ---
    if "Sales" in df.columns and "OperatingProfit" in df.columns:
        df["OperatingMargin"] = (
            df["OperatingProfit"] / df["Sales"].replace(0, float("nan"))
        )

    # --- ROE (純利益 / 純資産) ---
    if "NP" in df.columns and "Eq" in df.columns:
        df["ROE"] = df["NP"] / df["Eq"].replace(0, float("nan"))

    # --- 自己資本比率 (EqAR は % 単位 → 比率に変換) ---
    if "EqAR" in df.columns:
        df["EquityRatio"] = df["EqAR"] / 100.0

    # --- 来期予想増益率 (NxFOP / |OP| - 1) ---
    if "NxFOP" in df.columns and "OperatingProfit" in df.columns:
        op_abs = df["OperatingProfit"].abs().replace(0, float("nan"))
        df["ForwardGuidance"] = df["NxFOP"] / op_abs - 1

    return df.dropna(subset=["EarningsGrowth"])


def compute_earnings_growth(stmt_df: pd.DataFrame) -> pd.DataFrame:
    """後方互換エイリアス。compute_financial_metrics を呼ぶ。"""
    return compute_financial_metrics(stmt_df)


def screen_candidates(
    stmt_df: pd.DataFrame,
    quotes_df: pd.DataFrame,
    listed_df: pd.DataFrame,
    target_date: Optional[str] = None,
    earnings_growth_min: Optional[float] = None,
    sales_growth_min: Optional[float] = None,
    op_margin_min: Optional[float] = None,
    equity_ratio_min: Optional[float] = None,
    fwd_guidance_min: Optional[float] = None,
    period_types: Optional[list] = None,
    sectors: Optional[list] = None,
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

    growth_df = compute_financial_metrics(stmt_df)
    if growth_df.empty:
        return pd.DataFrame()

    # 1. 決算成長率フィルタ
    _growth_min = EARNINGS_GROWTH_MIN if earnings_growth_min is None else earnings_growth_min
    cond_growth = growth_df["EarningsGrowth"] >= _growth_min
    passed = growth_df[cond_growth].copy()
    logger.info(f"  決算成長率 >= {_growth_min:.0%}: {len(passed)} 件")

    # 1b. 追加スクリーニング条件
    if sales_growth_min is not None and "SalesGrowth" in passed.columns:
        before = len(passed)
        passed = passed[passed["SalesGrowth"] >= sales_growth_min].copy()
        logger.info(f"  売上高成長率 >= {sales_growth_min:.0%}: {before} → {len(passed)} 件")

    if op_margin_min is not None and "OperatingMargin" in passed.columns:
        before = len(passed)
        passed = passed[passed["OperatingMargin"] >= op_margin_min].copy()
        logger.info(f"  営業利益率 >= {op_margin_min:.0%}: {before} → {len(passed)} 件")

    if equity_ratio_min is not None and "EquityRatio" in passed.columns:
        before = len(passed)
        passed = passed[passed["EquityRatio"] >= equity_ratio_min].copy()
        logger.info(f"  自己資本比率 >= {equity_ratio_min:.0%}: {before} → {len(passed)} 件")

    if fwd_guidance_min is not None and "ForwardGuidance" in passed.columns:
        before = len(passed)
        passed = passed[passed["ForwardGuidance"] >= fwd_guidance_min].copy()
        logger.info(f"  来期予想増益率 >= {fwd_guidance_min:.0%}: {before} → {len(passed)} 件")

    if period_types is not None and "CurPerType" in passed.columns:
        before = len(passed)
        passed = passed[passed["CurPerType"].isin(period_types)].copy()
        logger.info(f"  決算種別 {period_types}: {before} → {len(passed)} 件")

    if sectors is not None and "S17Nm" in listed_df.columns:
        s17_map = listed_df.set_index("Code")["S17Nm"].to_dict()
        passed["_S17Nm"] = passed["Code"].map(s17_map)
        before = len(passed)
        passed = passed[passed["_S17Nm"].isin(sectors)].copy()
        passed.drop(columns=["_S17Nm"], inplace=True, errors="ignore")
        logger.info(f"  業種フィルタ {sectors}: {before} → {len(passed)} 件")

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
        # 時価総額なし → ScaleCat (規模別分類) で代替フィルタ
        if "ScaleCat" in listed_df.columns:
            # eq_master の Code は 4桁 ("1301") / fin_summary は 5桁 ("13010") の場合がある
            # 両方のマッピングを試みる
            listed_code_sample = str(listed_df["Code"].iloc[0]) if len(listed_df) > 0 else ""
            passed_code_sample = str(passed["Code"].iloc[0]) if len(passed) > 0 else ""
            logger.info(f"  Code形式 listed={listed_code_sample!r}, passed={passed_code_sample!r}")

            # 正規化: 5桁コードの末尾"0"を除いた4桁版と両方試す
            def normalize_code(c):
                s = str(c)
                if len(s) == 5 and s.endswith("0"):
                    return s[:-1]
                return s

            scale_map_raw = listed_df.set_index("Code")["ScaleCat"].to_dict()
            # 4桁版のマップも作成
            scale_map_4 = {normalize_code(k): v for k, v in scale_map_raw.items()}

            def lookup_scale(code):
                v = scale_map_raw.get(code)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    v = scale_map_4.get(normalize_code(code))
                return v

            passed["_ScaleCat"] = passed["Code"].apply(lookup_scale)

            # 実際の ScaleCat 値を確認してからフィルタ
            val_counts = passed["_ScaleCat"].value_counts(dropna=False).head(10).to_dict()
            logger.info(f"  ScaleCat値分布: {val_counts}")

            nan_ratio = passed["_ScaleCat"].isna().mean()
            if nan_ratio > 0.9:
                # Code マッチ率が低すぎる → スキップ
                logger.warning(f"  ScaleCatマッチ率低({1-nan_ratio:.1%}) → フィルタをスキップ")
                passed.drop(columns=["_ScaleCat"], inplace=True, errors="ignore")
            else:
                unique_vals = passed["_ScaleCat"].dropna().unique().tolist()
                # "TOPIX Large70" / "TOPIX Mid400" / "TOPIX Small" 等の文字列対応
                mid_large_vals = [
                    v for v in unique_vals
                    if str(v) in ("1", "2") or "Large" in str(v) or "Mid" in str(v)
                ]
                if not mid_large_vals:
                    # 値不明 → 小型(3/"Small"/"TOPIX Small"等)を除外
                    small_vals = [v for v in unique_vals if "Small" in str(v) or str(v) == "3"]
                    if small_vals:
                        before = len(passed)
                        passed = passed[~passed["_ScaleCat"].isin(small_vals)].copy()
                        logger.info(f"  小型除外フィルタ({small_vals}): {before} → {len(passed)} 件")
                    else:
                        logger.warning(f"  ScaleCat値不明({unique_vals[:5]}) → フィルタをスキップ")
                        passed.drop(columns=["_ScaleCat"], inplace=True, errors="ignore")
                else:
                    before = len(passed)
                    passed = passed[passed["_ScaleCat"].isin(mid_large_vals)].copy()
                    passed.drop(columns=["_ScaleCat"], inplace=True, errors="ignore")
                    logger.info(f"  ScaleCat大型/中型({mid_large_vals}): {before} → {len(passed)} 件")
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

    # エントリー日の始値取得（調整後始値 → 通常始値 の優先順）
    adj_col = next(
        (c for c in ["AdjustmentOpen", "AdjO", "AdjOpen"] if c in quotes_df.columns),
        None,
    )
    idx = quotes_df.set_index(["Code", "Date"])
    adj_open_map = idx[adj_col].to_dict() if adj_col else {}
    open_map = idx["Open"].to_dict() if "Open" in quotes_df.columns else {}
    if adj_col:
        logger.info(f"  始値カラム: {adj_col}")
    else:
        logger.warning("  調整後始値カラムなし → 通常始値(Open)を使用")

    def get_open(row):
        key = (row["Code"], row["EntryDate"])
        val = adj_open_map.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            val = open_map.get(key)
        return val

    passed["EntryOpen"] = passed.apply(get_open, axis=1)
    passed = passed.dropna(subset=["EntryOpen"])

    # S17Nm（業種）を追加（後でスイープ用フィルタに使う）
    if "S17Nm" in listed_df.columns and "S17Nm" not in passed.columns:
        s17_map = listed_df.set_index("Code")["S17Nm"].to_dict()
        passed["S17Nm"] = passed["Code"].map(s17_map)

    base_cols = ["Code", "DisclosedDate"]
    if "FiscalPeriodEnd" in passed.columns:
        base_cols.append("FiscalPeriodEnd")
    metric_cols = [c for c in [
        "EarningsGrowth", "SalesGrowth", "OperatingMargin",
        "ROE", "EquityRatio", "ForwardGuidance", "CurPerType", "S17Nm",
    ] if c in passed.columns]
    tail_cols = ["MarketCapitalization", "EntryDate", "EntryOpen"]

    result = passed[base_cols + metric_cols + tail_cols].copy()
    result.rename(columns={"MarketCapitalization": "MarketCap"}, inplace=True)
    result.reset_index(drop=True, inplace=True)

    logger.info(f"  最終候補: {len(result)} 件")
    return result
