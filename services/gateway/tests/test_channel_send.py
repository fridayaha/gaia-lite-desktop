"""出站 send 端点测试 — POST /{channel_type}/{agent_id}/send

覆盖鉴权、参数校验、msgtype 路由、adapter 调用、错误降级。
使用 FastAPI TestClient，mock get_channel_config + get_adapter，自包含不依赖 PG/企微。

用法:
  pytest services/gateway/tests/test_channel_send.py -v
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from app.main import app
from app.settings import settings
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="module")
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


AGENT = "00000000-0000-0000-0000-000000000002"
URL = f"/api/gateway/channel/wecom/{AGENT}/send"
AUTH = {"Authorization": f"Bearer {settings.api_server_key}"}


def _mock_adapter():
    """mock wecom adapter：send_message / send_card_message 返回成功。"""
    adapter = AsyncMock()
    adapter.send_message = AsyncMock(return_value=True)
    adapter.send_card_message = AsyncMock(
        return_value={"errcode": 0, "errmsg": "ok"})
    return adapter


class TestChannelSend:

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_no_auth_returns_401(self, client):
        """缺少 Authorization header → 401"""
        resp = await client.post(URL, json={"touser": "LiuWei", "content": "x"})
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_wrong_auth_returns_401(self, client):
        """错误的 Bearer key → 401"""
        resp = await client.post(
            URL, json={"touser": "LiuWei", "content": "x"},
            headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_no_config_returns_404(self, client):
        """channel 未配置 → 404"""
        async def fake_cfg(agent_id, channel_type):
            return None
        with patch("app.channel.router.get_channel_config", new=fake_cfg):
            resp = await client.post(
                URL, json={"touser": "LiuWei", "content": "x"}, headers=AUTH)
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_missing_touser_returns_400(self, client):
        """缺少 touser/chat_id → 400"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL, json={"content": "x"}, headers=AUTH)
        assert resp.status_code == 400
        assert "touser" in resp.json()["error"]

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_missing_content_returns_400(self, client):
        """缺少 content → 400"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL, json={"touser": "LiuWei"}, headers=AUTH)
        assert resp.status_code == 400
        assert "content" in resp.json()["error"]

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_markdown_calls_send_message(self, client):
        """msgtype=markdown → 调 adapter.send_message，返回 ok=True"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "msgtype": "markdown",
                      "content": "## 日报\n- 完成"},
                headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        adapter.send_message.assert_awaited_once_with("LiuWei", "## 日报\n- 完成")

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_text_calls_send_message(self, client):
        """msgtype=text → 同样调 send_message"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "msgtype": "text", "content": "通知"},
                headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        adapter.send_message.assert_awaited_once_with("LiuWei", "通知")

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_template_card_calls_send_card(self, client):
        """msgtype=template_card → 调 send_card_message，content 解析为 JSON"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        card = {"template_card": {"card_type": "text_notice",
                                  "main_title": {"title": "测试卡"}}}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "msgtype": "template_card",
                      "content": json.dumps(card)},
                headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        adapter.send_card_message.assert_awaited_once_with(
            "LiuWei", "template_card", card)

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_template_card_invalid_json_returns_400(self, client):
        """template_card content 非 JSON → 400"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "msgtype": "template_card",
                      "content": "not-json"},
                headers=AUTH)
        assert resp.status_code == 400
        assert "card JSON" in resp.json()["error"]

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_unsupported_msgtype_returns_400(self, client):
        """不支持的 msgtype → 400"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "msgtype": "image",
                      "content": "x"},
                headers=AUTH)
        assert resp.status_code == 400
        assert "msgtype" in resp.json()["error"]

    @pytest.mark.asyncio(loop_scope="module")
    async def test_send_chat_id_returns_501(self, client):
        """群聊发送暂未支持：传 chat_id 显式 501，而非把群 ID 当 touser 静默丢消息"""
        adapter = _mock_adapter()
        async def fake_cfg(agent_id, channel_type):
            return {"corp_id": "x", "secret": "x", "agent_id": "1000001"}
        with patch("app.channel.router.get_channel_config", new=fake_cfg), \
             patch("app.channel.router.get_adapter", return_value=adapter):
            resp = await client.post(
                URL,
                json={"touser": "LiuWei", "chat_id": "group123",
                      "content": "群消息"},
                headers=AUTH)
        assert resp.status_code == 501
        adapter.send_message.assert_not_awaited()
