"""J-Quants REST API クライアント。

認証フロー（3通り対応）:
  A. APIキー直接指定（Googleアカウント登録ユーザー）:
       マイページの「API Key」を JQUANTS_API_KEY にセット → Bearer トークンとして直接使用
  B. リフレッシュトークン直接指定:
       JQUANTS_REFRESH_TOKEN → idToken
  C. メール+パスワード方式（通常登録ユーザー）:
       メール+パスワード → refreshToken → idToken
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

import requests

from config.settings import (
    JQUANTS_API_KEY,
    JQUANTS_REFRESH_TOKEN,
    JQUANTS_EMAIL,
    JQUANTS_PASSWORD,
    JQUANTS_BASE_URL,
    JQUANTS_TOKEN_URL,
    JQUANTS_REFRESH_URL,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

_TOKEN_CACHE = DATA_DIR / ".token_cache.json"


class JQuantsClient:
    def __init__(
        self,
        api_key: str = JQUANTS_API_KEY,
        refresh_token: str = JQUANTS_REFRESH_TOKEN,
        email: str = JQUANTS_EMAIL,
        password: str = JQUANTS_PASSWORD,
    ):
        self._api_key = api_key.strip()
        self._refresh_token = refresh_token.strip()
        self.email = email
        self.password = password
        self._id_token: Optional[str] = None
        self._token_expires: float = 0.0
        if not self._api_key:
            self._load_token_cache()

    # ------------------------------------------------------------------
    # 認証
    # ------------------------------------------------------------------
    def _load_token_cache(self) -> None:
        if _TOKEN_CACHE.exists():
            try:
                data = json.loads(_TOKEN_CACHE.read_text())
                if data.get("expires", 0) > time.time() + 60:
                    self._id_token = data["id_token"]
                    self._token_expires = data["expires"]
            except Exception:
                pass

    def _save_token_cache(self) -> None:
        _TOKEN_CACHE.write_text(json.dumps({
            "id_token": self._id_token,
            "expires": self._token_expires,
        }))

    def _get_refresh_token_from_password(self) -> str:
        resp = requests.post(JQUANTS_TOKEN_URL, json={
            "mailaddress": self.email,
            "password": self.password,
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["refreshToken"]

    def _exchange_refresh_token(self, refresh_token: str) -> str:
        resp = requests.post(
            JQUANTS_REFRESH_URL,
            params={"refreshtoken": refresh_token},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["idToken"]

    def _ensure_token(self) -> None:
        if self._id_token and time.time() < self._token_expires:
            return

        logger.info("J-Quants: IDトークン取得中...")

        if self._api_key:
            # A. APIキー → idToken（Googleアカウント登録ユーザー）
            resp = requests.post(JQUANTS_TOKEN_URL, json={
                "apikey": self._api_key,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            refresh_token = data.get("refreshToken") or data.get("idToken")
            if data.get("idToken"):
                self._id_token = data["idToken"]
            else:
                self._id_token = self._exchange_refresh_token(refresh_token)
        elif self._refresh_token:
            # B. リフレッシュトークン直接指定
            self._id_token = self._exchange_refresh_token(self._refresh_token)
        elif self.email and self.password:
            # C. メール+パスワード方式
            refresh_token = self._get_refresh_token_from_password()
            self._id_token = self._exchange_refresh_token(refresh_token)
        else:
            raise RuntimeError(
                "J-Quants 認証情報が未設定です。\n"
                "Googleアカウント登録の場合: export JQUANTS_API_KEY=<マイページのAPI Key>\n"
                "メール登録の場合: export JQUANTS_EMAIL=... JQUANTS_PASSWORD=..."
            )

        self._token_expires = time.time() + 23 * 3600
        self._save_token_cache()

    # ------------------------------------------------------------------
    # 汎用GETラッパー（ページネーション対応）
    # ------------------------------------------------------------------
    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        self._ensure_token()
        url = f"{JQUANTS_BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Bearer {self._id_token}"}
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, endpoint: str, key: str, params: Optional[dict] = None) -> list:
        """pagination_key を辿って全ページ取得する。"""
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
        """上場全銘柄の基本情報を返す。"""
        return self._get_paginated("listed/info", "info")

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
        return self._get_paginated("prices/daily_quotes", "daily_quotes", params)

    # ------------------------------------------------------------------
    # 財務情報（決算）
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
        return self._get_paginated("fins/statements", "statements", params)

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
        return self._get_paginated("prices/adjustments/splits", "splits", params)
