"""J-Quants REST API クライアント（V2対応）。

V2 API 認証:
  x-api-key ヘッダーにマイページの「現在のAPI Key」をセット。
  トークン交換不要。

環境変数:
  JQUANTS_API_KEY  マイページの「現在のAPI Key」
"""

import logging
from typing import Optional

import requests

from config.settings import (
    JQUANTS_API_KEY,
    JQUANTS_BASE_URL,
    DATA_DIR,
)

logger = logging.getLogger(__name__)


class JQuantsClient:
    def __init__(self, api_key: str = JQUANTS_API_KEY):
        if not api_key:
            raise RuntimeError(
                "J-Quants API Keyが未設定です。\n"
                "export JQUANTS_API_KEY=<マイページの「現在のAPI Key」>"
            )
        self._headers = {"x-api-key": api_key.strip()}

    # ------------------------------------------------------------------
    # 汎用GETラッパー（ページネーション対応）
    # ------------------------------------------------------------------
    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        url = f"{JQUANTS_BASE_URL}/{endpoint}"
        resp = requests.get(url, headers=self._headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, endpoint: str, key: str, params: Optional[dict] = None) -> list:
        params = params or {}
        results = []
        while True:
            data = self._get(endpoint, params)
            results.extend(data.get(key, []))
            pagination_key = data.get("pagination_key")
            if not pagination_key:
                break
            params = {**params, "pagination_key": pagination_key}
        return results

    # ------------------------------------------------------------------
    # 上場銘柄一覧
    # ------------------------------------------------------------------
    def get_listed_info(self) -> list[dict]:
        return self._get_paginated("equities/master", "items")

    # ------------------------------------------------------------------
    # 日足株価
    # ------------------------------------------------------------------
    def get_daily_quotes(
        self,
        code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        params: dict = {}
        if code:
            params["code"] = code
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return self._get_paginated("equities/bars/daily", "items")

    # ------------------------------------------------------------------
    # 財務情報（決算サマリー）
    # ------------------------------------------------------------------
    def get_statements(
        self,
        code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        params: dict = {}
        if code:
            params["code"] = code
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return self._get_paginated("fins/summary", "items", params)

    # ------------------------------------------------------------------
    # 株式分割・併合
    # ------------------------------------------------------------------
    def get_splits(
        self,
        code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        params: dict = {}
        if code:
            params["code"] = code
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return self._get_paginated("equities/adjustments/splits", "items", params)
