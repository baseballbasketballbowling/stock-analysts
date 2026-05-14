"""J-Quants API クライアント（公式ライブラリ jquantsapi のラッパー）。

V2 API のカラム名を V1 互換名にマッピングして返す。
認証: JQUANTS_API_KEY 環境変数にマイページの「現在のAPI Key」をセット。
"""

import logging
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
        else:
            df = self._cli.get_eq_bars_daily_range(
                start_dt=date_from, end_dt=date_to
            )
        df = df.rename(columns=_QUOTE_RENAME)
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
        else:
            df = self._cli.get_fin_summary_range(
                start_dt=date_from, end_dt=date_to
            )
        df = df.rename(columns=_FIN_RENAME)

        if "OperatingProfit" in df.columns and "OperatingProfitPriorYear" not in df.columns:
            df = df.sort_values(["Code", "DisclosedDate"])
            df["OperatingProfitPriorYear"] = (
                df.groupby("Code")["OperatingProfit"].shift(4)
            )

        return df.to_dict(orient="records")
