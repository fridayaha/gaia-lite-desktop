"""E2E Webhook 路由测试 — 模拟 IM 平台回调 + Gateway 路由 + 消息调度

使用 FastAPI TestClient（不需要单独启动服务进程）。
channel 配置查询（``get_channel_config``）通过 fixture mock，使测试自包含、
不依赖外部 PG；仍完整覆盖 router → adapter → 签名 → 验证 → parse → dispatch 链路。
（真实 PG + 种子数据的端到端验证在 k3s 部署联调时进行。）

用法:
  pytest services/gateway/tests/test_e2e_webhook.py -v
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from app.channel.dispatcher import dispatcher
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient — 模块级共享，避免 event loop 冲突"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def mock_channel_config():
    """Mock DB channel config 查询，使 e2e 路由测试自包含（无需 PG）。

    feishu → 空 config（无 encrypt_key → verify_signature 信任网络层；challenge 走通）；
    其他渠道（dingtalk 等）→ None（router 返回 404，验证无配置降级）。
    """
    async def fake_get_channel_config(agent_id: str, channel_type: str):
        if channel_type == "feishu":
            # 非空 config（无 encrypt_key → verify_signature 信任网络层；challenge 走通）
            return {"app_id": "test-app", "app_secret": "test-secret",
                    "verification_token": "test-token"}
        return None
    with patch("app.channel.router.get_channel_config",
               new=fake_get_channel_config):
        yield


class TestWebhookE2E:

    @pytest.mark.asyncio(loop_scope="module")
    async def test_health(self, client):
        """Gateway 存活检测"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio(loop_scope="module")
    async def test_feishu_challenge(self, client):
        """飞书 Challenge 验证 — 直达路由层"""
        resp = await client.post(
            "/api/gateway/channel/feishu/00000000-0000-0000-0000-000000000002/callback",
            json={"type": "url_verification", "challenge": "test_challenge_value"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "challenge" in data

    @pytest.mark.asyncio(loop_scope="module")
    async def test_feishu_webhook_routing(self, client):
        """验证飞书 Webhook 路由 + 消息到达 dispatcher

        使用 patch dispatcher.dispatch 跳过 async 调度的时序问题。
        """
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_test_e2e",
                "event_type": "im.message.receive_v1",
                "create_time": "1718000000",
            },
            "event": {
                "message": {
                    "chat_id": "oc_test_e2e",
                    "chat_type": "group",
                    "message_id": "om_test_e2e",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好，E2E 测试"}),
                },
                "sender": {
                    "sender_id": {"open_id": "ou_test_e2e"},
                    "name": "测试用户",
                },
            },
        }

        with patch.object(dispatcher, "dispatch", new_callable=AsyncMock) as mock_dispatch:
            resp = await client.post(
                "/api/gateway/channel/feishu/00000000-0000-0000-0000-000000000002/callback",
                json=payload,
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # 验证消息已到达 dispatcher.dispatch
        mock_dispatch.assert_called_once()
        event_arg = mock_dispatch.call_args[0][0]
        assert event_arg.text == "你好，E2E 测试"
        assert event_arg.chat_id == "oc_test_e2e"
        assert event_arg.channel_type == "feishu"
        assert event_arg.agent_id == "00000000-0000-0000-0000-000000000002"

    @pytest.mark.asyncio(loop_scope="module")
    async def test_no_config_returns_404(self, client):
        """未配置的 channel_type（DB 中无 dingtalk 配置）→ 404"""
        resp = await client.post(
            "/api/gateway/channel/dingtalk/00000000-0000-0000-0000-000000000002/callback",
            json={},
        )
        assert resp.status_code == 404
