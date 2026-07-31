"""告警通知渠道发送 service — 飞书/钉钉/企微 webhook + SMTP 邮件。

独立于 AgentInstance 的 IM ChannelType 枚举（场景不同：IM 双向通信 vs 告警单向推送）。
所有发送函数 best-effort：失败返回 {ok: False, error}，不抛异常，单渠道失败不阻塞其他。
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

import httpx

from pkg.common.config import settings

logger = logging.getLogger(__name__)


async def send_feishu(webhook_url: str, text: str) -> None:
    """飞书自定义机器人 webhook。payload: {"msg_type":"text","content":{"text":"..."}}"""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            webhook_url,
            json={"msg_type": "text", "content": {"text": text}},
        )
        resp.raise_for_status()


async def send_dingtalk(webhook_url: str, text: str) -> None:
    """钉钉自定义机器人 webhook。payload: {"msgtype":"text","text":{"content":"..."}}"""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": text}},
        )
        resp.raise_for_status()


async def send_wecom(webhook_url: str, text: str) -> None:
    """企业微信群机器人 webhook。payload: {"msgtype":"text","text":{"content":"..."}}"""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": text}},
        )
        resp.raise_for_status()


def send_email(to: list[str], subject: str, body: str) -> None:
    """SMTP 发邮件（同步，调用方用 asyncio.to_thread 包）。

    凭据从 settings.smtp_* 拿。host 为空直接 raise ValueError（上层捕获转 ok=False）。
    """
    if not settings.smtp_host:
        raise ValueError("SMTP 未配置（UA_SMTP_HOST 为空）")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_username))
    msg["To"] = ", ".join(to)

    security = (settings.smtp_security or "ssl").lower()
    if security == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(settings.smtp_username or settings.smtp_host, to, msg.as_string())
    elif security == "starttls":
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(settings.smtp_username or settings.smtp_host, to, msg.as_string())
    else:  # none
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(settings.smtp_username or settings.smtp_host, to, msg.as_string())


async def notify_channels(
    channels: list[dict],
    alert_text: str,
    alert_subject: str,
) -> list[dict[str, Any]]:
    """遍历渠道发送告警，返回 [{type, name?, ok, error?}]。

    - feishu/dingtalk/wecom: 调对应 async sender
    - email: 用 asyncio.to_thread 包同步 SMTP 调用
    单渠道失败 best-effort，不影响其他渠道。
    """
    results: list[dict[str, Any]] = []
    for ch in channels:
        ctype = ch.get("type")
        name = ch.get("name")
        entry: dict[str, Any] = {"type": ctype, "name": name, "ok": False}
        try:
            if ctype == "feishu":
                await send_feishu(ch["webhook_url"], alert_text)
            elif ctype == "dingtalk":
                await send_dingtalk(ch["webhook_url"], alert_text)
            elif ctype == "wecom":
                await send_wecom(ch["webhook_url"], alert_text)
            elif ctype == "email":
                await asyncio.to_thread(
                    send_email, ch.get("to", []), alert_subject, alert_text
                )
            else:
                raise ValueError(f"未知渠道类型: {ctype}")
            entry["ok"] = True
        except Exception as e:
            entry["error"] = str(e)[:200]
            logger.warning(
                "[notify] channel %s (%s) send failed: %s", ctype, name or "?", e
            )
        results.append(entry)
    return results
