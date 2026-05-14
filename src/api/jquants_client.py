"""J-Quants API クライアント（公式ライブラリ jquantsapi のラッパー）。

V2 API のカラム名を V1 互換名にマッピングして返す。
認証: JQUANTS_API_KEY 環境変数にマイページの「現在のAPI Key」をセット。
"""

import logging
import time
from typing import Optional

import pandas as pd
import jquantsapi

logger = logging.getLogger(__name__)

_FIN_RENAME = {
    "DiscDate": "DisclosedDate",
    "CurPerEn": "FiscalPeriodEnd",
    "DocType": "TypeOfDocument",
    "OP": "OperatingProfit",
}

_QUOTE_RENAME = {
    "O": "Open",
    "H": "High",
    "L": "Low",
    "C": "Close",
    "AdjO": "AdjustmentOpen",
    "AdjH": "AdjustmentHigh",
    "AdjL": "AdjustmentLow",
    "AdjC": "AdjustmentClose",
    "Vo": "Volume",
}

# J-Quants V2 rate limit: 並列リクエストを避けて逐次処理 + ウェイト
_REQUEST_INTERVAL = 0.4  # 秒 (= 最大 2.5 req/sec)


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
            df = self._fetch_eq_bars_sequential(date_from, date_to)

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
            df = self._fetch_fin_summary_sequential(date_from, date_to)

        if "OperatingProfit" in df.columns and "OperatingProfitPriorYear" not in df.columns:
            df = df.sort_values(["Code", "DisclosedDate"])
            df["OperatingProfitPriorYear"] = (
                df.groupby("Code")["OperatingProfit"].shift(4)
            )

        return df.to_dict(orient="records")

    # ------------------------------------------------------------------
    # 逐次フェッチャー（レートリミット対策）
    # ------------------------------------------------------------------

    def _fetch_fin_summary_sequential(self, date_from: str, date_to: str) -> pd.DataFrame:
        """fin/summary を営業日ごとに逐次取得してレートリミットを回避する。"""
        dates = pd.bdate_range(date_from, date_to)  # 平日のみ（週末スキップ）
        total = len(dates)
        logger.info(f"  fin_summary 逐次取得: {total} 営業日分")

        results = []
        for i, date in enumerate(dates, 1):
            yyyymmdd = date.strftime("%Y%m%d")
            if i % 50 == 0:
                logger.info(f"  fin_summary: {i}/{total} ({yyyymmdd})")
            try:
                df = self._cli.get_fin_summary(date_yyyymmdd=yyyymmdd)
                if not df.empty:
                    results.append(df)
            except Exception as e:
                logger.warning(f"  fin_summary {yyyymmdd} スキップ: {e}")
            time.sleep(_REQUEST_INTERVAL)

        if not results:
            return pd.DataFrame()

        combined = pd.concat(results, ignore_index=True)
        return combined.rename(columns=_FIN_RENAME)

    def _fetch_eq_bars_sequential(self, date_from: str, date_to: str) -> pd.DataFrame:
        """equities/bars/daily を営業日ごとに逐次取得してレートリミットを回避する。"""
        dates = pd.bdate_range(date_from, date_to)  # 平日のみ
        total = len(dates)
        logger.info(f"  eq_bars_daily 逐次取得: {total} 営業日分")

        results = []
        for i, date in enumerate(dates, 1):
            date_str = date.strftime("%Y-%m-%d")
            if i % 50 == 0:
                logger.info(f"  eq_bars: {i}/{total} ({date_str})")
            try:
                df = self._cli.get_eq_bars_daily(date_yyyymmdd=date_str)
                if not df.empty:
                    results.append(df)
            except Exception as e:
                logger.warning(f"  eq_bars {date_str} スキップ: {e}")
            time.sleep(_REQUEST_INTERVAL)

        if not results:
            return pd.DataFrame()

        combined = pd.concat(results, ignore_index=True)
        return combined.rename(columns=_QUOTE_RENAME)
