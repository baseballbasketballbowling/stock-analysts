"""J-Quants API クライアント（公式ライブラリ jquantsapi のラッパー）。

認証: JQUANTS_API_KEY 環境変数にマイページの「現在のAPI Key」をセット。
      ClientV2() は JQUANTS_API_KEY を自動で読む。
"""

import logging
from typing import Optional

import jquantsapi

logger = logging.getLogger(__name__)


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
        return df.to_dict(orient="records")
