"""lifecycle.py 单元测试 — F-GW-030 健康检查/降级 + F-GW-031 冷启动

覆盖：
  - check_engine_health：200→True、非200→False、连接错误/超时→False
  - trigger_deploy：controller 200→True、超时/连接错误→False（不抛）
  - ensure_engine_ready：热启动(True,True)、冷启动 deploy+轮询(True,False)、
    永不就绪(False,False)
  - resolve_engine_url：DB pod_name 优先、无 pod_name 降级 DNS 约定、DB 异常降级
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ConnectError, ReadTimeout

# ── check_engine_health ────────────────────────────────────


class TestCheckEngineHealth:
    @pytest.mark.asyncio
    async def test_healthy_returns_true(self):
        from app.lifecycle import check_engine_health
        mock_resp = MagicMock(status_code=200)
        with patch("app.lifecycle.resolve_engine_url",
                   new=AsyncMock(return_value="http://engine:8642")), \
             patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
            assert await check_engine_health("agent-1") is True

    @pytest.mark.asyncio
    async def test_non_200_returns_false(self):
        from app.lifecycle import check_engine_health
        mock_resp = MagicMock(status_code=503)
        with patch("app.lifecycle.resolve_engine_url",
                   new=AsyncMock(return_value="http://engine:8642")), \
             patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
            assert await check_engine_health("agent-1") is False

    @pytest.mark.asyncio
    async def test_connect_error_returns_false(self):
        from app.lifecycle import check_engine_health
        with patch("app.lifecycle.resolve_engine_url",
                   new=AsyncMock(return_value="http://engine:8642")), \
             patch("app.lifecycle.httpx.AsyncClient") as mc:
            client = mc.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=ConnectError("refused"))
            assert await check_engine_health("agent-1") is False

    @pytest.mark.asyncio
    async def test_read_timeout_returns_false(self):
        from app.lifecycle import check_engine_health
        with patch("app.lifecycle.resolve_engine_url",
                   new=AsyncMock(return_value="http://engine:8642")), \
             patch("app.lifecycle.httpx.AsyncClient") as mc:
            client = mc.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=ReadTimeout("slow"))
            assert await check_engine_health("agent-1") is False


# ── trigger_deploy ─────────────────────────────────────────


class TestTriggerDeploy:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        from app.lifecycle import trigger_deploy
        mock_resp = MagicMock(status_code=200)
        with patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            assert await trigger_deploy("agent-1") is True

    @pytest.mark.asyncio
    async def test_non_200_returns_false(self):
        from app.lifecycle import trigger_deploy
        mock_resp = MagicMock(status_code=500, text="err")
        with patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            assert await trigger_deploy("agent-1") is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false_not_raise(self):
        """超时不视为致命失败（Controller 可能仍在工作），返回 False 不抛"""
        from app.lifecycle import trigger_deploy
        with patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("slow"))
            assert await trigger_deploy("agent-1") is False

    @pytest.mark.asyncio
    async def test_connect_error_returns_false(self):
        from app.lifecycle import trigger_deploy
        with patch("app.lifecycle.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ConnectError("no controller"))
            assert await trigger_deploy("agent-1") is False


# ── ensure_engine_ready ────────────────────────────────────


class TestEnsureEngineReady:
    @pytest.mark.asyncio
    async def test_hot_start(self):
        """引擎已运行 → (ready=True, was_already_running=True)，不触发 deploy"""
        health = AsyncMock(return_value=True)
        deploy = AsyncMock(return_value=True)
        with patch("app.lifecycle.check_engine_health", new=health), \
             patch("app.lifecycle.trigger_deploy", new=deploy):
            from app.lifecycle import ensure_engine_ready
            ready, running = await ensure_engine_ready("a1")
        assert (ready, running) == (True, True)
        assert health.await_count == 1
        deploy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cold_start_triggers_deploy_then_ready(self):
        """冷启动：首次健康检查失败 → trigger_deploy → 轮询就绪 → (True, False)"""
        health = AsyncMock(side_effect=[False, True])
        with patch("app.lifecycle.check_engine_health", new=health), \
             patch("app.lifecycle.trigger_deploy", new=AsyncMock(return_value=True)) as deploy, \
             patch("app.lifecycle.asyncio.sleep", new=AsyncMock()):
            from app.lifecycle import ensure_engine_ready
            ready, running = await ensure_engine_ready("a1", max_wait=5)
        assert (ready, running) == (True, False)
        deploy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_never_ready_returns_false(self):
        """部署后轮询 max_wait 次仍未就绪 → (False, False)"""
        health = AsyncMock(return_value=False)
        with patch("app.lifecycle.check_engine_health", new=health), \
             patch("app.lifecycle.trigger_deploy", new=AsyncMock(return_value=True)), \
             patch("app.lifecycle.asyncio.sleep", new=AsyncMock()):
            from app.lifecycle import ensure_engine_ready
            ready, running = await ensure_engine_ready("a1", max_wait=3)
        assert (ready, running) == (False, False)
        # 1（step1）+ 3（轮询）= 4 次健康检查
        assert health.await_count == 4


# ── resolve_engine_url ─────────────────────────────────────


def _mock_db_session(pod_name):
    """构造 mock async_session：async with async_session() as db → db.execute 返回 pod_name 行"""
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = (
        {"pod_name": pod_name} if pod_name is not None else None
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_db
    return mock_cm


class TestResolveEngineUrl:
    @pytest.mark.asyncio
    async def test_uses_db_pod_name(self):
        from app.lifecycle import resolve_engine_url
        cm = _mock_db_session("engine-hermes-abc12345")
        with patch("app.lifecycle.async_session", return_value=cm):
            url = await resolve_engine_url("agent-1")
        assert url == "http://engine-hermes-abc12345.unionagents.svc.cluster.local:8642"

    @pytest.mark.asyncio
    async def test_fallback_to_dns_when_no_pod(self):
        """DB 无 pod_name → 降级 build_engine_url（DNS 约定 engine-hermes-{short_id}）"""
        from app.lifecycle import resolve_engine_url
        with patch("app.lifecycle.async_session", return_value=_mock_db_session(None)):
            url = await resolve_engine_url("550e8400-e29b-41d4-a716-446655440000")
        assert "engine-hermes-550e8400.unionagents.svc.cluster.local:8642" in url

    @pytest.mark.asyncio
    async def test_fallback_on_db_exception(self):
        """DB 异常 → 降级 DNS 约定（可用性优先）"""
        from app.lifecycle import resolve_engine_url
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB down"))
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_db
        with patch("app.lifecycle.async_session", return_value=mock_cm):
            url = await resolve_engine_url("550e8400-e29b-41d4-a716-446655440000")
        assert "engine-hermes-550e8400" in url
