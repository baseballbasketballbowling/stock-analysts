"""LINE Notify APIを使ったメッセージ送信モジュール。

LINE Notify トークンは GitHub Secrets の LINE_NOTIFY_TOKEN に設定してください。
"""

import logging

import requests

logger = logging.getLogger(__name__)

LINE_NOTIFY_API = "https://notify-api.line.me/api/notify"


def send_line_message(token: str, message: str) -> bool:
    """LINE Notify APIでメッセージを送信する。

    Parameters
    ----------
    token   : LINE Notify のアクセストークン
    message : 送信するテキスト（1000文字以内）

    Returns
    -------
    True if successful, False otherwise
    """
    try:
        resp = requests.post(
            LINE_NOTIFY_API,
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("LINE Notify: 送信成功")
            return True
        logger.error(
            f"LINE Notify: 失敗 HTTP {resp.status_code}: {resp.text[:200]}"
        )
        return False
    except Exception as e:
        logger.error(f"LINE Notify: 送信例外: {e}")
        return False
