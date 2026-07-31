"""SMTP 邮件探活 + 发码 — stdlib smtplib（无外部依赖）。

- probe: login 探活（从 0.8.100 email_configs.py 的 _smtp_login 搬过来）
- send: 发送 MIMEText 邮件
成功 return None；失败 raise Exception（smtplib.SMTPAuthenticationError / SMTPConnectError / 等）。
"""

import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr


def probe(cfg, secrets: dict) -> None:
    """SMTP login 探活。secrets = {"password": "..."}"""
    password = secrets["password"]
    if cfg.encryption == "ssl":
        client = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15)
    else:
        client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
        if cfg.encryption == "starttls":
            client.starttls()
    try:
        client.login(cfg.username, password)
    finally:
        try:
            client.quit()
        except Exception:
            pass


def send(cfg, secrets: dict, to: str, subject: str, html_body: str) -> None:
    """SMTP 发邮件。secrets = {"password": "..."}

    cfg.username 是发件邮箱（也是 SMTP login 用户名）。cfg.from_name 是显示名。
    成功 return None；失败 raise Exception。
    """
    password = secrets["password"]
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.from_name or "知行平台", cfg.username))
    msg["To"] = to

    if cfg.encryption == "ssl":
        client = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15)
    else:
        client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
        if cfg.encryption == "starttls":
            client.starttls()
    try:
        client.login(cfg.username, password)
        client.sendmail(cfg.username, [to], msg.as_string())
    finally:
        try:
            client.quit()
        except Exception:
            pass
