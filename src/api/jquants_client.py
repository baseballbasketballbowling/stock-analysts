"""J-Quants API クライアント（公式ライブラリ jquantsapi のラッパー）。

V2 API のカラム名を V1 互換名にマッピングして返す。
認証: JQUANTS_API_KEY 環境変数にマイページの「現在のAPI Key」をセット。

データ取得戦略:
  1. バルクダウンロードAPI（/bulk/list + /bulk/get）を優先使用
     → 数十APIコールで数年分のデータを一括取得
  2. バルク不可の場合は逐次フェッチ（1.2s/req + 429時60秒バックオフ）
"""

import logging
import os
import re
import tempfile
import time
from typing import Optional

import pandas as pd
import jquantsapi
from jquantsapi import BulkEndpoint

logger = logging.getLogger(__name__)

_FIN_RENAME = {
    "DiscDate": "DisclosedDate",
    "CurPerEn": "FiscalPeriodEnd",
    "DocType": "TypeOfDocument",
    "OP": "OperatingProfit",
}

_QUOTE_RENAME = {
    # V2 API の略称 → 長い名前
    "O": "Open",
    "H": "High",
    "L": "Low",
    "C": "Close",
    "AdjO": "AdjustmentOpen",
    "AdjH": "AdjustmentHigh",
    "AdjL": "AdjustmentLow",
    "AdjC": "AdjustmentClose",
    "Vo": "Volume",
    "Va": "TurnoverValue",
    "MktCap": "MarketCapitalization",
    # バルクCSVが別の略称を使う場合のフォールバック
    "AdjOpen":  "AdjustmentOpen",
    "AdjHigh":  "AdjustmentHigh",
    "AdjLow":   "AdjustmentLow",
    "AdjClose": "AdjustmentClose",
}

_BULK_REQUEST_INTERVAL = 0.5   # バルクURL取得間隔（秒）
_SEQ_REQUEST_INTERVAL  = 1.2   # 逐次フェッチ間隔（秒） = 約50 req/min
_BACKOFF_INITIAL       = 60    # 429発生時の初期待機秒数


class JQuantsClient:
    def __init__(self):
        self._cli = jquantsapi.ClientV2()

    def get_listed_info(self) -> list[dict]:
        df = self._cli.get_eq_master()
        return df.to_dict(orient="records")

    def get_daily_quotes(
        self,
        code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        if code:
            df = self._cli.get_eq_bars_daily(code=code)
            df = df.rename(columns=_QUOTE_RENAME)
        else:
            df = self._fetch_bulk_or_sequential(
                bulk_endpoint=BulkEndpoint.EQ_BARS_DAILY,
                date_from=date_from,
                date_to=date_to,
                rename_map=_QUOTE_RENAME,
                date_col_in="Date",
                seq_fetch_fn=self._seq_fetch_eq_bars,
                label="eq_bars_daily",
            )
        if "MarketCapitalization" not in df.columns:
            df["MarketCapitalization"] = float("nan")
        return df.to_dict(orient="records")

    def get_statements(
        self,
        code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        if code:
            df = self._cli.get_fin_summary(code=code)
            df = df.rename(columns=_FIN_RENAME)
        else:
            df = self._fetch_bulk_or_sequential(
                bulk_endpoint=BulkEndpoint.FIN_SUMMARY,
                date_from=date_from,
                date_to=date_to,
                rename_map=_FIN_RENAME,
                date_col_in="DiscDate",
                date_col_out="DisclosedDate",
                seq_fetch_fn=self._seq_fetch_fin_summary,
                label="fin_summary",
            )

        if "OperatingProfit" in df.columns and "OperatingProfitPriorYear" not in df.columns:
            df = df.sort_values(["Code", "DisclosedDate"])
            df["OperatingProfitPriorYear"] = (
                df.groupby("Code")["OperatingProfit"].shift(4)
            )

        return df.to_dict(orient="records")

    # ------------------------------------------------------------------
    # バルク or 逐次フェッチ 統合ルーター
    # ------------------------------------------------------------------

    def _fetch_bulk_or_sequential(
        self,
        bulk_endpoint: BulkEndpoint,
        date_from: str,
        date_to: str,
        rename_map: dict,
        date_col_in: str,
        seq_fetch_fn,
        label: str,
        date_col_out: Optional[str] = None,
    ) -> pd.DataFrame:
        """バルクAPIを試行、失敗時は逐次フェッチにフォールバックする。"""
        try:
            df = self._fetch_bulk(
                bulk_endpoint, date_from, date_to, rename_map,
                date_col_out or date_col_in, label
            )
            if not df.empty:
                return df
            logger.warning(f"{label}: バルク結果が空 → 逐次フェッチ")
        except Exception as e:
            logger.warning(f"{label}: バルク失敗({e}) → 逐次フェッチ")

        return self._fetch_sequential(seq_fetch_fn, date_from, date_to, rename_map, label)

    # ------------------------------------------------------------------
    # バルクダウンロード
    # ------------------------------------------------------------------

    def _fetch_bulk(
        self,
        endpoint: BulkEndpoint,
        date_from: str,
        date_to: str,
        rename_map: dict,
        date_col: str,
        label: str,
    ) -> pd.DataFrame:
        """バルクAPIでデータを取得する（数十リクエストで数年分を取得）。"""
        bulk_list = self._cli.get_bulk_list(endpoint)
        if bulk_list.empty:
            raise ValueError("バルクリストが空")

        from_dt = pd.Timestamp(date_from)
        to_dt = pd.Timestamp(date_to)
        keys = self._filter_bulk_keys(bulk_list["Key"].tolist(), from_dt, to_dt)
        logger.info(f"  {label} バルク: {len(keys)} ファイル")

        # 日次ファイルが多すぎる場合はバルク使用を中止（APIコール数が逐次と変わらない）
        if len(keys) > 300:
            raise ValueError(f"バルクキー数 {len(keys)} > 300 (日次ファイル形式) → 逐次を使用")

        results = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, key in enumerate(keys, 1):
                try:
                    ext = self._detect_ext(key)
                    outpath = os.path.join(tmpdir, f"bulk_{i:04d}{ext}")
                    self._cli.download_bulk(key, outpath)
                    df = self._load_bulk_file(outpath, ext)
                    if not df.empty:
                        if i == 1:
                            logger.info(f"    バルクCSV列名: {list(df.columns)}")
                        results.append(df)
                    logger.info(f"    {i}/{len(keys)}: {key} ({len(df)}件)")
                    time.sleep(_BULK_REQUEST_INTERVAL)
                except Exception as e:
                    logger.warning(f"    {label} bulk {key}: {e}")

        if not results:
            raise ValueError("バルクファイルの読み込みが全て失敗")

        combined = pd.concat(results, ignore_index=True)
        combined = combined.rename(columns=rename_map)

        # 日付カラムで絞り込み
        if date_col in combined.columns:
            combined[date_col] = pd.to_datetime(combined[date_col], errors="coerce")
            combined = combined[
                (combined[date_col] >= from_dt) &
                (combined[date_col] <= to_dt)
            ]

        return combined

    def _filter_bulk_keys(
        self, keys: list[str], from_dt: pd.Timestamp, to_dt: pd.Timestamp
    ) -> list[str]:
        """キー一覧を日付範囲でフィルタ。日付が判定できないキーは全件含める。"""
        filtered = []
        undated = []
        for key in keys:
            dt = self._key_to_date(key)
            if dt is None:
                undated.append(key)
            elif from_dt <= dt <= to_dt + pd.DateOffset(months=1):
                filtered.append(key)
        return filtered + undated

    @staticmethod
    def _key_to_date(key: str) -> Optional[pd.Timestamp]:
        """キー文字列から代表日付を抽出する。"""
        m = re.search(r'(\d{4}-\d{2}-\d{2}|\d{8}|\d{4}-\d{2}|\d{6})', key)
        if not m:
            return None
        s = m.group(1).replace("-", "")
        try:
            if len(s) == 8:
                return pd.Timestamp(s)
            elif len(s) == 6:
                return pd.Timestamp(s + "01")
        except Exception:
            pass
        return None

    @staticmethod
    def _detect_ext(key: str) -> str:
        if ".parquet" in key:
            return ".parquet"
        if ".csv" in key:
            return ".csv.gz"
        return ".bin"

    @staticmethod
    def _load_bulk_file(path: str, ext: str) -> pd.DataFrame:
        if ext == ".parquet":
            return pd.read_parquet(path)
        if ext == ".csv.gz":
            return pd.read_csv(path, compression="gzip", dtype=str)
        # 拡張子不明: Parquet → CSV の順で試行
        try:
            return pd.read_parquet(path)
        except Exception:
            try:
                return pd.read_csv(path, dtype=str)
            except Exception:
                return pd.DataFrame()

    # ------------------------------------------------------------------
    # 逐次フェッチ（バルク非対応プランへのフォールバック）
    # ------------------------------------------------------------------

    def _fetch_sequential(
        self,
        fetch_fn,
        date_from: str,
        date_to: str,
        rename_map: dict,
        label: str,
    ) -> pd.DataFrame:
        """営業日ごとに逐次取得。429発生時は60秒バックオフ後リトライ。"""
        dates = pd.bdate_range(date_from, date_to)
        total = len(dates)
        logger.info(f"  {label} 逐次取得: {total} 営業日")

        results = []
        backoff = _BACKOFF_INITIAL

        for i, date in enumerate(dates, 1):
            if i % 100 == 0:
                logger.info(f"  {label}: {i}/{total}")

            df = self._fetch_one_with_backoff(fetch_fn, date, label)
            if df is not None and not df.empty:
                results.append(df)
            time.sleep(_SEQ_REQUEST_INTERVAL)

        if not results:
            return pd.DataFrame()

        combined = pd.concat(results, ignore_index=True)
        return combined.rename(columns=rename_map)

    def _fetch_one_with_backoff(self, fetch_fn, date, label: str):
        """1回分のフェッチ。429には最大3回バックオフリトライ。"""
        for attempt in range(4):
            try:
                return fetch_fn(date)
            except Exception as e:
                is_429 = "429" in str(e) or "RetryError" in type(e).__name__
                if not is_429:
                    logger.warning(f"  {label} {date}: {type(e).__name__} スキップ")
                    return None
                if attempt < 3:
                    wait = _BACKOFF_INITIAL * (attempt + 1)
                    logger.info(f"  {label} {date}: 429 → {wait}s待機")
                    time.sleep(wait)
                else:
                    logger.warning(f"  {label} {date}: 429 全リトライ失敗 スキップ")
                    return None
        return None

    # ------------------------------------------------------------------
    # 逐次フェッチ用の個別取得関数
    # ------------------------------------------------------------------

    def _seq_fetch_fin_summary(self, date) -> pd.DataFrame:
        yyyymmdd = pd.Timestamp(date).strftime("%Y%m%d")
        return self._cli.get_fin_summary(date_yyyymmdd=yyyymmdd)

    def _seq_fetch_eq_bars(self, date) -> pd.DataFrame:
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        return self._cli.get_eq_bars_daily(date_yyyymmdd=date_str)
