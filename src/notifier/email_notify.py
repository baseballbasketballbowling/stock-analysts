"""Gmail SMTPを使ったメール通知モジュール。

必要な環境変数 (GitHub Secrets に設定):
  GMAIL_USER         : 送信元Gmailアドレス (例: your@gmail.com)
  GMAIL_APP_PASSWORD : Googleアカウントで発行したアプリパスワード (16文字)
  NOTIFY_TO          : 送信先メールアドレス (省略時は GMAIL_USER と同じ)
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(
    gmail_user: str,
    app_password: str,
    to_address: str,
    subject: str,
    body: str,
) -> bool:
    """Gmail SMTP (TLS) でメールを送信する。"""
    try:
        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, to_address, msg.as_string())

        logger.info(f"メール送信成功 → {to_address}")
        return True
    except Exception as e:
        logger.error(f"メール送信失敗: {e}")
        return False
