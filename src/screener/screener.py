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


def _compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
    """終値SeriesからRSI（デフォルト14日）を計算する。"""
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)

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
    for col in ["TotalMarketCap", "MarketCapitalization", "TotalSharesIssued"]:
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

    sort_by = [c for c in ["Code", "CurPerType", "FiscalPeriodEnd"] if c in df.columns]
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

    # 黒字転換（前年赤字→当期黒字）: 通常計算だとマイナスになるため高成長扱いに補正
    turnaround = (
        df["OperatingProfitPriorYear"].fillna(0) < 0
    ) & (df["OperatingProfit"].fillna(0) > 0)
    if turnaround.any():
        df.loc[turnaround, "EarningsGrowth"] = 9.99  # 999%成長として扱う
        logger.info(f"  黒字転換補正: {int(turnaround.sum())} 件")

    # --- 売上高 YoY ---
    if "Sales" in df.columns:
        if "CurPerType" in df.columns:
            df["SalesPriorYear"] = df.groupby(["Code", "CurPerType"])["Sales"].shift(1)
        else:
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
    roe_min: Optional[float] = None,
    period_types: Optional[list] = None,
    sectors: Optional[list] = None,
    require_entry_price: bool = True,
    gap_min: Optional[float] = -0.05,
    market_cap_max: Optional[float] = MARKET_CAP_MAX,
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

    if roe_min is not None and "ROE" in passed.columns:
        before = len(passed)
        passed = passed[passed["ROE"] >= roe_min].copy()
        logger.info(f"  ROE >= {roe_min:.0%}: {before} → {len(passed)} 件")

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

    # 2. 時価総額フィルタ（開示日時点の時価総額を使用）
    # merge_asof で開示日以前の直近取引日の時価総額を取得
    cap_df = (
        quotes_df[["Code", "Date", "MarketCapitalization"]]
        .dropna(subset=["MarketCapitalization"])
        .sort_values("Date")
    )
    if not cap_df.empty and not passed.empty:
        passed = passed.reset_index(drop=True)
        _pf = passed[["Code", "DisclosedDate"]].copy().reset_index()
        _pf = _pf.sort_values("DisclosedDate")
        _merged = pd.merge_asof(
            _pf,
            cap_df.rename(columns={"Date": "DisclosedDate"}),
            on="DisclosedDate",
            by="Code",
            direction="backward",
        )
        passed["MarketCapitalization"] = (
            _merged.set_index("index")["MarketCapitalization"]
            .reindex(passed.index)
            .values
        )
    else:
        passed["MarketCapitalization"] = float("nan")

    # MarketCapitalization が欠損の場合は listed_info の TotalMarketCap を補完
    if "TotalMarketCap" in listed_df.columns:
        listed_cap = listed_df[["Code", "TotalMarketCap"]].rename(
            columns={"TotalMarketCap": "_cap_listed"}
        )
        passed = passed.merge(listed_cap, on="Code", how="left")
        mask = passed["MarketCapitalization"].isna()
        passed.loc[mask, "MarketCapitalization"] = passed.loc[mask, "_cap_listed"]
        passed.drop(columns=["_cap_listed"], inplace=True, errors="ignore")

    # TotalMarketCapも欠損の場合: 発行済株式数 × 開示日前後の終値で算出
    mask_no_cap = passed["MarketCapitalization"].isna()
    if mask_no_cap.any() and "TotalSharesIssued" in listed_df.columns:
        shares_ser = listed_df.set_index("Code")["TotalSharesIssued"]
        _close_col_mc = next(
            (c for c in ["AdjustmentClose", "Close"] if c in quotes_df.columns), None
        )
        if _close_col_mc:
            _mc_df = (
                quotes_df[["Code", "Date", _close_col_mc]]
                .dropna(subset=[_close_col_mc])
                .sort_values("Date")
            )
            _pf_mc = passed.loc[mask_no_cap, ["Code", "DisclosedDate"]].copy().reset_index()
            _pf_mc = _pf_mc.sort_values("DisclosedDate")
            _mc_merged = pd.merge_asof(
                _pf_mc,
                _mc_df.rename(columns={"Date": "DisclosedDate", _close_col_mc: "_Close"}),
                on="DisclosedDate", by="Code", direction="backward",
            )
            _mc_merged["_Shares"] = _mc_merged["Code"].map(shares_ser)
            _mc_merged["_Cap"] = _mc_merged["_Close"] * _mc_merged["_Shares"]
            cap_vals = _mc_merged.set_index("index")["_Cap"].reindex(
                passed.loc[mask_no_cap].index
            ).values
            passed.loc[mask_no_cap, "MarketCapitalization"] = cap_vals
            n_computed = int(pd.Series(cap_vals).notna().sum())
            if n_computed:
                logger.info(f"  時価総額算出(株価×発行株数): {n_computed} 件補完")

    has_cap = passed["MarketCapitalization"].notna().any()
    if has_cap:
        _cap_max = market_cap_max if market_cap_max is not None else float("inf")
        cond_cap = (passed["MarketCapitalization"] >= MARKET_CAP_MIN) & (passed["MarketCapitalization"] <= _cap_max)
        passed = passed[cond_cap].copy()
        cap_max_label = f"{_cap_max/1e8:.0f}億円" if _cap_max != float("inf") else "上限なし"
        logger.info(f"  時価総額 {MARKET_CAP_MIN/1e8:.0f}億〜{cap_max_label}: {len(passed)} 件")
    else:
        # 時価総額なし → ScaleCat (規模別分類) で代替フィルタ
        # market_cap_max=None のとき Core30(超大型)も含む、それ以外は Mid400+Large70 のみ
        include_core30 = (market_cap_max is None)

        if "ScaleCat" in listed_df.columns:
            listed_code_sample = str(listed_df["Code"].iloc[0]) if len(listed_df) > 0 else ""
            passed_code_sample = str(passed["Code"].iloc[0]) if len(passed) > 0 else ""
            logger.info(f"  Code形式 listed={listed_code_sample!r}, passed={passed_code_sample!r}")

            def normalize_code(c):
                s = str(c)
                if len(s) == 5 and s.endswith("0"):
                    return s[:-1]
                return s

            scale_map_raw = listed_df.set_index("Code")["ScaleCat"].to_dict()
            scale_map_norm = {normalize_code(k): v for k, v in scale_map_raw.items()}

            def lookup_scale(code):
                v = scale_map_raw.get(code)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    v = scale_map_norm.get(normalize_code(code))
                return v

            passed["_ScaleCat"] = passed["Code"].apply(lookup_scale)
            # ScaleCat を結果列として保持（メトリクス分析用）
            passed["ScaleCat"] = passed["_ScaleCat"]

            val_counts = passed["_ScaleCat"].value_counts(dropna=False).head(10).to_dict()
            logger.info(f"  ScaleCat値分布: {val_counts}")

            nan_ratio = passed["_ScaleCat"].isna().mean()
            if nan_ratio > 0.9:
                logger.warning(f"  ScaleCatマッチ率低({1-nan_ratio:.1%}) → フィルタをスキップ")
                passed.drop(columns=["_ScaleCat"], inplace=True, errors="ignore")
            else:
                unique_vals = passed["_ScaleCat"].dropna().unique().tolist()

                def _is_target_scale(v):
                    sv = str(v)
                    if sv in ("1", "2") or "Mid" in sv or "Large" in sv:
                        return True
                    if include_core30 and "Core" in sv:
                        return True
                    return False

                target_vals = [v for v in unique_vals if _is_target_scale(v)]

                if not target_vals:
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
                    passed = passed[passed["_ScaleCat"].isin(target_vals)].copy()
                    passed.drop(columns=["_ScaleCat"], inplace=True, errors="ignore")
                    core_note = "+Core30" if include_core30 else ""
                    logger.info(f"  ScaleCat Mid400+Large70{core_note}({target_vals}): {before} → {len(passed)} 件")
        else:
            logger.warning("  時価総額データなし（V2 API非対応）: 時価総額フィルタをスキップ")

    # 3. エントリー日（翌営業日）と始値を付与
    trading_dates = sorted(quotes_df["Date"].unique())
    trading_dates_ser = pd.Series(trading_dates)

    def next_trading_day(disclosed_date: pd.Timestamp) -> Optional[pd.Timestamp]:
        future = trading_dates_ser[trading_dates_ser > disclosed_date]
        return future.iloc[0] if not future.empty else None

    passed["EntryDate"] = passed["DisclosedDate"].apply(next_trading_day)
    if require_entry_price:
        passed = passed.dropna(subset=["EntryDate"])
    else:
        # 翌営業日が quotes_df の範囲外の場合はカレンダー上の次営業日で補完
        from pandas.tseries.offsets import BDay
        mask_no_entry = passed["EntryDate"].isna()
        if mask_no_entry.any():
            passed.loc[mask_no_entry, "EntryDate"] = (
                passed.loc[mask_no_entry, "DisclosedDate"] + BDay(1)
            )

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
    if require_entry_price:
        passed = passed.dropna(subset=["EntryOpen"])

    # 大幅ギャップダウンフィルタ (EntryOpen と PrevClose が両方ある場合のみ適用)
    if gap_min is not None and "PrevClose" not in passed.columns:
        # PrevCloseはこの後で付与するため、ここでは一時計算用に取得
        _close_col_tmp = next(
            (c for c in ["AdjustmentClose", "Close"] if c in quotes_df.columns), None
        )
        if _close_col_tmp:
            _tmp_close = quotes_df[["Code", "Date", _close_col_tmp]].dropna(subset=[_close_col_tmp]).sort_values("Date")
            _tmp = passed[["Code", "DisclosedDate"]].copy().reset_index()
            _tmp = _tmp.sort_values("DisclosedDate")
            _tmp_m = pd.merge_asof(
                _tmp,
                _tmp_close.rename(columns={"Date": "DisclosedDate", _close_col_tmp: "_PC"}),
                on="DisclosedDate", by="Code", direction="backward",
            )
            passed["_TmpPrevClose"] = _tmp_m.set_index("index")["_PC"].reindex(passed.index).values

    if gap_min is not None:
        pc_col = "PrevClose" if "PrevClose" in passed.columns else "_TmpPrevClose"
        if pc_col in passed.columns and "EntryOpen" in passed.columns:
            gap_ser = passed["EntryOpen"] / passed[pc_col] - 1
            before = len(passed)
            passed = passed[gap_ser.isna() | (gap_ser >= gap_min)].copy()
            passed.drop(columns=["_TmpPrevClose"], inplace=True, errors="ignore")
            if len(passed) < before:
                logger.info(f"  大幅GDフィルタ(gap>={gap_min:.0%}): {before} → {len(passed)} 件")

    # 開示日の終値 (PrevClose) — 翌日寄付きとのギャップ率算出用
    _close_col_gap = next(
        (c for c in ["AdjustmentClose", "Close"] if c in quotes_df.columns), None
    )
    if _close_col_gap:
        _gap_df = (
            quotes_df[["Code", "Date", _close_col_gap]]
            .dropna(subset=[_close_col_gap])
            .sort_values("Date")
        )
        _pg = passed[["Code", "DisclosedDate"]].copy().reset_index()
        _pg = _pg.sort_values("DisclosedDate")
        _pg_merged = pd.merge_asof(
            _pg,
            _gap_df.rename(columns={"Date": "DisclosedDate", _close_col_gap: "PrevClose"}),
            on="DisclosedDate", by="Code", direction="backward",
        )
        passed["PrevClose"] = (
            _pg_merged.set_index("index")["PrevClose"].reindex(passed.index).values
        )

    # RSI(14日) — 開示日以前の直近値を付与
    _close_col = next(
        (c for c in ["AdjustmentClose", "Close"] if c in quotes_df.columns), None
    )
    if _close_col is not None:
        _rsi_price = (
            quotes_df[["Code", "Date", _close_col]]
            .copy()
            .sort_values(["Code", "Date"])
        )
        _rsi_price["RSI14"] = _rsi_price.groupby("Code")[_close_col].transform(
            _compute_rsi
        )
        _rsi_lookup = (
            _rsi_price[["Code", "Date", "RSI14"]]
            .dropna(subset=["RSI14"])
            .sort_values("Date")
        )
        if not _rsi_lookup.empty:
            _rp = passed[["Code", "DisclosedDate"]].copy().reset_index()
            _rp = _rp.sort_values("DisclosedDate")
            _rp_merged = pd.merge_asof(
                _rp,
                _rsi_lookup.rename(columns={"Date": "DisclosedDate"}),
                on="DisclosedDate",
                by="Code",
                direction="backward",
            )
            passed["RSI14"] = (
                _rp_merged.set_index("index")["RSI14"]
                .reindex(passed.index)
                .values
            )
            logger.info(f"  RSI14: {passed['RSI14'].notna().sum()} 件に付与")

    # S17Nm（業種）を追加（後でスイープ用フィルタに使う）
    if "S17Nm" in listed_df.columns and "S17Nm" not in passed.columns:
        s17_map = listed_df.set_index("Code")["S17Nm"].to_dict()
        passed["S17Nm"] = passed["Code"].map(s17_map)

    base_cols = ["Code", "DisclosedDate"]
    if "FiscalPeriodEnd" in passed.columns:
        base_cols.append("FiscalPeriodEnd")
    metric_cols = [c for c in [
        "EarningsGrowth", "SalesGrowth", "OperatingMargin",
        "ROE", "EquityRatio", "ForwardGuidance", "CurPerType", "S17Nm", "RSI14", "ScaleCat",
    ] if c in passed.columns]
    tail_cols = [c for c in ["MarketCapitalization", "EntryDate", "EntryOpen", "PrevClose"]
                 if c in passed.columns]

    result = passed[base_cols + metric_cols + tail_cols].copy()
    result.rename(columns={"MarketCapitalization": "MarketCap"}, inplace=True)
    result.reset_index(drop=True, inplace=True)

    logger.info(f"  最終候補: {len(result)} 件")
    return result
