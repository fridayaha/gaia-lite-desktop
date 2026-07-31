"""notify_service 各渠道发送 + 容错测试。

- send_feishu/dingtalk/wecom：mock httpx.AsyncClient，断言 webhook URL + payload 结构 + raise_for_status
- send_email：mock smtplib，验证 SSL/STARTTLS/none 三种 security 路径 + login/sendmail 调用
- notify_channels：多渠道并行 + 单渠道失败不阻塞其他 + 未知 type 返回 ok=False 不抛
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.services import notify_service
from app.services.notify_service import (
    notify_channels,
    send_email,
    send_feishu,
    send_dingtalk,
    send_wecom,
)


# ── webhook 渠道（feishu/dingtalk/wecom）──


class _FakeResp:
    """模拟 httpx Response：raise_for_status 在 status_code >= 400 时抛 HTTPStatusError。"""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class _FakeClient:
    """捕获 post 调用的 mock AsyncClient，支持 async context manager 协议。"""

    def __init__(self, *args, **kwargs):
        self.captured = {}
        self._post_status = kwargs.pop("_post_status", 200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        self.captured = {"url": url, "json": json}
        return _FakeResp(self._post_status)


@pytest.mark.asyncio
async def test_send_feishu_payload_and_raises_on_non_2xx(monkeypatch):
    """飞书：payload {msg_type:text, content:{text}}；status>=400 时 raise_for_status 抛错。"""
    captured: dict = {}

    class _OkClient(_FakeClient):
        async def post(self, url, json=None):
            captured.update({"url": url, "json": json})
            return _FakeResp(200)

    monkeypatch.setattr(notify_service.httpx, "AsyncClient", _OkClient)
    await send_feishu("https://open.feishu.cn/webhook/xxx", "hello")
    assert captured["url"] == "https://open.feishu.cn/webhook/xxx"
    assert captured["json"] == {"msg_type": "text", "content": {"text": "hello"}}

    # 4xx 抛 HTTPStatusError（上层 notify_channels 转 ok=False）
    class _FailClient(_FakeClient):
        async def post(self, url, json=None):
            return _FakeResp(403)

    monkeypatch.setattr(notify_service.httpx, "AsyncClient", _FailClient)
    with pytest.raises(Exception):  # HTTPStatusError
        await send_feishu("https://open.feishu.cn/webhook/xxx", "hello")


@pytest.mark.asyncio
async def test_send_dingtalk_payload(monkeypatch):
    """钉钉：payload {msgtype:text, text:{content}}。"""
    captured: dict = {}

    class _Client(_FakeClient):
        async def post(self, url, json=None):
            captured.update({"url": url, "json": json})
            return _FakeResp(200)

    monkeypatch.setattr(notify_service.httpx, "AsyncClient", _Client)
    await send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=x", "ding")
    assert captured["json"] == {"msgtype": "text", "text": {"content": "ding"}}


@pytest.mark.asyncio
async def test_send_wecom_payload(monkeypatch):
    """企微：payload {msgtype:text, text:{content}}（与钉钉同结构，URL 不同）。"""
    captured: dict = {}

    class _Client(_FakeClient):
        async def post(self, url, json=None):
            captured.update({"url": url, "json": json})
            return _FakeResp(200)

    monkeypatch.setattr(notify_service.httpx, "AsyncClient", _Client)
    await send_wecom("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "wecom")
    assert captured["url"] == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x"
    assert captured["json"] == {"msgtype": "text", "text": {"content": "wecom"}}


# ── send_email（SMTP）──


@pytest.mark.asyncio
async def test_send_email_raises_when_smtp_host_empty(monkeypatch):
    """settings.smtp_host 为空时直接 raise ValueError（上层 notify_channels 转 ok=False）。"""
    monkeypatch.setattr(notify_service.settings, "smtp_host", "")
    with pytest.raises(ValueError, match="SMTP 未配置"):
        send_email(["a@b.com"], "subject", "body")


def _make_smtp_mock():
    """构造 smtplib.SMTP_SSL/SMTP 的 MagicMock，记录 login/sendmail/__enter__/__exit__ 调用。"""
    inst = MagicMock()
    inst.__enter__ = MagicMock(return_value=inst)
    inst.__exit__ = MagicMock(return_value=False)
    return inst


@pytest.mark.asyncio
async def test_send_email_ssl_path(monkeypatch):
    """security=ssl：走 SMTP_SSL，login + sendmail 都被调用，From/To 头格式正确。"""
    monkeypatch.setattr(notify_service.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notify_service.settings, "smtp_port", 465)
    monkeypatch.setattr(notify_service.settings, "smtp_security", "ssl")
    monkeypatch.setattr(notify_service.settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(notify_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(notify_service.settings, "smtp_from_name", "UnionAgents 告警")

    smtp_inst = _make_smtp_mock()
    monkeypatch.setattr(notify_service.smtplib, "SMTP_SSL", lambda host, port, timeout: smtp_inst)

    send_email(["ops@example.com"], "subject", "body")

    smtp_inst.login.assert_called_once_with("user@example.com", "secret")
    assert smtp_inst.sendmail.called
    args = smtp_inst.sendmail.call_args.args
    assert args[0] == "user@example.com"  # from
    assert args[1] == ["ops@example.com"]  # to
    # 邮件内容含 Subject/From/To 头
    msg_str = args[2]
    assert "Subject: subject" in msg_str
    assert "From: =?utf-8?b?" in msg_str or "UnionAgents" in msg_str  # 中文 from_name 走 base64 编码
    assert "To: ops@example.com" in msg_str


@pytest.mark.asyncio
async def test_send_email_starttls_path(monkeypatch):
    """security=starttls：走 SMTP + starttls()。"""
    monkeypatch.setattr(notify_service.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notify_service.settings, "smtp_port", 587)
    monkeypatch.setattr(notify_service.settings, "smtp_security", "starttls")
    monkeypatch.setattr(notify_service.settings, "smtp_username", "u")
    monkeypatch.setattr(notify_service.settings, "smtp_password", "p")
    monkeypatch.setattr(notify_service.settings, "smtp_from_name", "Alert")

    smtp_inst = _make_smtp_mock()
    monkeypatch.setattr(notify_service.smtplib, "SMTP", lambda host, port, timeout: smtp_inst)

    send_email(["a@b.com"], "s", "b")
    smtp_inst.starttls.assert_called_once()
    smtp_inst.login.assert_called_once_with("u", "p")
    assert smtp_inst.sendmail.called


@pytest.mark.asyncio
async def test_send_email_none_security_path(monkeypatch):
    """security=none：走 SMTP，不调 starttls。"""
    monkeypatch.setattr(notify_service.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notify_service.settings, "smtp_port", 25)
    monkeypatch.setattr(notify_service.settings, "smtp_security", "none")
    monkeypatch.setattr(notify_service.settings, "smtp_username", "")
    monkeypatch.setattr(notify_service.settings, "smtp_password", "")
    monkeypatch.setattr(notify_service.settings, "smtp_from_name", "Alert")

    smtp_inst = _make_smtp_mock()
    monkeypatch.setattr(notify_service.smtplib, "SMTP", lambda host, port, timeout: smtp_inst)

    send_email(["a@b.com"], "s", "b")
    smtp_inst.starttls.assert_not_called()
    smtp_inst.login.assert_not_called()  # username 为空不 login
    assert smtp_inst.sendmail.called


# ── notify_channels：多渠道容错 ──


@pytest.mark.asyncio
async def test_notify_channels_multi_channel_one_fails_others_succeed(monkeypatch):
    """3 渠道（飞书成功 / 钉钉 4xx 失败 / 企微成功），单失败不阻塞其他，
    返回结果列表 3 条，每条带 type/name/ok/error?。"""
    async def _ok_feishu(url, text):
        return  # 不抛

    async def _fail_dingtalk(url, text):
        import httpx
        raise httpx.HTTPStatusError("403", request=None, response=MagicMock(status_code=403))

    async def _ok_wecom(url, text):
        return

    monkeypatch.setattr(notify_service, "send_feishu", _ok_feishu)
    monkeypatch.setattr(notify_service, "send_dingtalk", _fail_dingtalk)
    monkeypatch.setattr(notify_service, "send_wecom", _ok_wecom)
    # email 不在 channels 里，不参与本次测试

    channels = [
        {"type": "feishu", "name": "feishu-bot", "webhook_url": "https://x"},
        {"type": "dingtalk", "name": "dd-bot", "webhook_url": "https://y"},
        {"type": "wecom", "name": "wx-bot", "webhook_url": "https://z"},
    ]
    results = await notify_channels(channels, "alert text", "alert subject")
    assert len(results) == 3
    by_type = {r["type"]: r for r in results}
    assert by_type["feishu"]["ok"] is True
    assert by_type["feishu"]["name"] == "feishu-bot"
    assert by_type["dingtalk"]["ok"] is False
    assert "error" in by_type["dingtalk"]
    assert by_type["wecom"]["ok"] is True


@pytest.mark.asyncio
async def test_notify_channels_unknown_type_returns_ok_false(monkeypatch):
    """未知 type 不抛，返回 ok=False + error 字段。"""
    results = await notify_channels(
        [{"type": "telegram", "name": "tg", "webhook_url": "https://x"}],
        "text", "subject",
    )
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "未知渠道类型" in results[0]["error"]


@pytest.mark.asyncio
async def test_notify_channels_email_uses_send_email(monkeypatch):
    """email 渠道走 asyncio.to_thread 调同步 send_email，mock send_email 验证被调用。"""
    called: dict = {}

    def _fake_send_email(to, subject, body):
        called["to"] = to
        called["subject"] = subject
        called["body"] = body

    monkeypatch.setattr(notify_service, "send_email", _fake_send_email)
    results = await notify_channels(
        [{"type": "email", "to": ["ops@example.com"]}],
        "alert body",
        "alert subject",
    )
    assert results[0]["ok"] is True
    assert called["to"] == ["ops@example.com"]
    assert called["subject"] == "alert subject"
    assert called["body"] == "alert body"
