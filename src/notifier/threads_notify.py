"""Threads API を使ったテキスト投稿モジュール。

必要な環境変数:
  THREADS_ACCESS_TOKEN : Threads API の長期アクセストークン（60日有効）
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

_BASE = "https://graph.threads.net/v1.0"


MAX_TEXT_LEN = 500  # Threads のテキスト投稿上限


def post_to_threads(access_token: str, text: str) -> bool:
    """Threads にテキスト投稿する。成功時 True。"""
    if len(text) > MAX_TEXT_LEN:
        logger.warning(f"投稿文が{len(text)}文字 → {MAX_TEXT_LEN}文字に切り詰め")
        text = text[: MAX_TEXT_LEN - 1] + "…"
    try:
        # ユーザー ID 取得
        me = requests.get(
            f"{_BASE}/me",
            params={"fields": "id,username", "access_token": access_token},
            timeout=10,
        )
        me.raise_for_status()
        user_id = me.json()["id"]

        # メディアコンテナ作成
        create = requests.post(
            f"{_BASE}/{user_id}/threads",
            params={"media_type": "TEXT", "text": text, "access_token": access_token},
            timeout=10,
        )
        create.raise_for_status()
        creation_id = create.json()["id"]

        time.sleep(2)

        # 公開
        publish = requests.post(
            f"{_BASE}/{user_id}/threads_publish",
            params={"creation_id": creation_id, "access_token": access_token},
            timeout=10,
        )
        publish.raise_for_status()
        logger.info(f"Threads投稿成功: id={publish.json().get('id', '')}")
        return True

    except Exception as e:
        logger.error(f"Threads投稿失敗: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"レスポンス: {e.response.text}")
        return False
