"""
Controller Pod Management API 测试。

覆盖：
  - GET  /api/controller/engine-instances/{id}/pods
  - GET  /api/controller/engine-instances/{id}/pods/{name}/logs
  - POST /api/controller/engine-instances/{id}/pods/{name}/restart
  - K8sManager.get_pods_for_instance()
  - K8sManager.get_pod_logs()
  - K8sManager.delete_pod()
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4
from datetime import datetime, timezone

EI_ID = str(uuid4())
POD_NAME = "engine-hermes-test1234-x1a2b"


# ═══════════════════════════════════════════════════════════════
# GET /api/controller/engine-instances/{id}/pods
# ═══════════════════════════════════════════════════════════════

class TestGetInstancePods:

    @pytest.mark.asyncio
    async def test_success_with_pods(self, client, mock_db_session, mock_k8s):
        """正常返回 Pod 列表"""
        mock_k8s.get_pods_for_instance = AsyncMock(return_value=[])
        mock_db_session.execute.return_value = MagicMock()
        mock_db_session.execute.return_value.scalars.return_value.all.return_value = []

        resp = await client.get(f"/api/controller/engine-instances/{EI_ID}/pods")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_empty(self, client, mock_db_session, mock_k8s):
        """无 Pod 时返回空列表"""
        mock_k8s.get_pods_for_instance = AsyncMock(return_value=[])
        mock_db_session.execute.return_value = MagicMock()
        mock_db_session.execute.return_value.scalars.return_value.all.return_value = []

        resp = await client.get(f"/api/controller/engine-instances/{EI_ID}/pods")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    @pytest.mark.asyncio
    async def test_invalid_instance_id(self, client, mock_db_session, mock_k8s):
        """无效 ID 应正常返回（不抛 500）"""
        mock_k8s.get_pods_for_instance = AsyncMock(return_value=[])
        mock_db_session.execute.return_value = MagicMock()
        mock_db_session.execute.return_value.scalars.return_value.all.return_value = []

        resp = await client.get(f"/api/controller/engine-instances/not-a-uuid/pods")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════
# GET /api/controller/engine-instances/{id}/pods/{name}/logs
# ═══════════════════════════════════════════════════════════════

class TestGetPodLogs:

    @pytest.mark.asyncio
    async def test_success(self, client, mock_db_session, mock_k8s):
        """正常返回 Pod 日志"""
        mock_k8s.get_pod_logs = AsyncMock(return_value="[INFO] test log line 1\n[INFO] test log line 2")

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pod_name"] == POD_NAME
        assert "test log line 1" in data["logs"]
        assert "test log line 2" in data["logs"]

    @pytest.mark.asyncio
    async def test_with_tail_lines(self, client, mock_db_session, mock_k8s):
        """tail_lines 参数正确传递"""
        mock_k8s.get_pod_logs = AsyncMock(return_value="line1")
        await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs?tail_lines=50"
        )
        mock_k8s.get_pod_logs.assert_called_once_with(POD_NAME, 50)

    @pytest.mark.asyncio
    async def test_pod_not_found(self, client, mock_db_session, mock_k8s):
        """Pod 不存在返回 404"""
        mock_k8s.get_pod_logs = AsyncMock(side_effect=ValueError(f"Pod {POD_NAME} not found"))

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tail_lines_validation(self, client, mock_db_session):
        """tail_lines 超出范围应 422"""
        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs?tail_lines=5"
        )
        assert resp.status_code == 422

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs?tail_lines=6000"
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════
# POST /api/controller/engine-instances/{id}/pods/{name}/restart
# ═══════════════════════════════════════════════════════════════

class TestRestartPod:

    @pytest.mark.asyncio
    async def test_success(self, client, mock_db_session, mock_k8s):
        """重启成功"""
        mock_k8s.delete_pod = AsyncMock()

        resp = await client.post(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/restart"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert data["pod_name"] == POD_NAME
        assert "重建" in data["message"]

    @pytest.mark.asyncio
    async def test_pod_not_found(self, client, mock_db_session, mock_k8s):
        """Pod 不存在返回 404"""
        mock_k8s.delete_pod = AsyncMock(side_effect=ValueError(f"Pod {POD_NAME} not found"))

        resp = await client.post(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/restart"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_server_error(self, client, mock_db_session, mock_k8s):
        """K8s API 错误返回 500"""
        mock_k8s.delete_pod = AsyncMock(side_effect=Exception("K8s API error"))

        resp = await client.post(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/restart"
        )
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════
# K8sManager.get_pod_logs (unit test on real singleton)
# ═══════════════════════════════════════════════════════════════

class _FakeLogResp:
    """模拟 _preload_content=False 的 urllib3 HTTPResponse（含 .data bytes）。"""

    def __init__(self, data: bytes):
        self.data = data


class TestK8sGetPodLogs:

    async def test_success(self):
        """正常获取日志（_preload_content=False 返回 bytes，解码为 str）"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.read_namespaced_pod_log.return_value = _FakeLogResp(b"line1\nline2\n")

            result = await k8s_manager.get_pod_logs("test-pod", 100)
            assert result == "line1\nline2\n"
            mock_core.read_namespaced_pod_log.assert_called_once_with(
                name="test-pod",
                namespace=k8s_manager.namespace,
                tail_lines=100,
                _preload_content=False,
            )

    async def test_pod_not_found(self):
        """Pod 不存在时抛出 ValueError"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        exc = ApiException()
        exc.status = 404

        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.read_namespaced_pod_log.side_effect = exc

            with pytest.raises(ValueError, match="not found"):
                await k8s_manager.get_pod_logs("missing-pod")

    async def test_empty_logs(self):
        """Pod 刚启动无日志时返回空字符串"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        exc = ApiException()
        exc.status = 400

        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.read_namespaced_pod_log.side_effect = exc

            result = await k8s_manager.get_pod_logs("new-pod")
            assert result == ""

    async def test_bytes_logs_decoded(self):
        """bytes 日志解码为 str，避免 JSON 序列化成 b"..." 字面量"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.read_namespaced_pod_log.return_value = _FakeLogResp(b"line1\nline2\n")

            result = await k8s_manager.get_pod_logs("test-pod", 100)
            assert result == "line1\nline2\n"
            assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════
# K8sManager.delete_pod (unit test on real singleton)
# ═══════════════════════════════════════════════════════════════

class TestK8sDeletePod:
    async def test_success(self):
        """正常删除 Pod"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "core_v1") as mock_core:
            await k8s_manager.delete_pod("test-pod")
            mock_core.delete_namespaced_pod.assert_called_once_with(
                "test-pod", k8s_manager.namespace
            )

    async def test_pod_not_found(self):
        """Pod 不存在时抛出 ValueError"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        exc = ApiException()
        exc.status = 404

        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.delete_namespaced_pod.side_effect = exc

            with pytest.raises(ValueError, match="not found"):
                await k8s_manager.delete_pod("missing-pod")


# ═══════════════════════════════════════════════════════════════
# Profile 网关日志（source=gateway）+ logs/sources 端点
# ═══════════════════════════════════════════════════════════════

class TestProfileGatewayLogs:

    @pytest.mark.asyncio
    async def test_gateway_logs_with_profile(self, client, mock_db_session, mock_k8s):
        """source=gateway&profile=X 返回该 profile 网关日志"""
        mock_k8s.get_profile_gateway_logs = AsyncMock(return_value="gw-line1\ngw-line2\n")

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs"
            "?source=gateway&profile=base"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "gateway"
        assert data["profile"] == "base"
        assert "gw-line1" in data["logs"]
        mock_k8s.get_profile_gateway_logs.assert_called_once_with(POD_NAME, "base", 200)

    @pytest.mark.asyncio
    async def test_gateway_logs_without_profile_lists_profiles(self, client, mock_db_session, mock_k8s):
        """source=gateway 且无 profile 时返回可用 profile 列表"""
        mock_k8s.list_profile_log_files = AsyncMock(return_value=["base", "agent-x-y"])

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs?source=gateway"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["profiles"] == ["base", "agent-x-y"]
        assert data["logs"] == ""

    @pytest.mark.asyncio
    async def test_logs_sources_endpoint(self, client, mock_db_session, mock_k8s):
        """/logs/sources 返回 {engine, profiles}"""
        mock_k8s.list_profile_log_files = AsyncMock(return_value=["base"])

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs/sources"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] is True
        assert data["profiles"] == ["base"]

    @pytest.mark.asyncio
    async def test_gateway_logs_invalid_profile_rejected(self, client, mock_db_session, mock_k8s):
        """k8s_manager 对非法 profile 名抛 ValueError → 端点转 404（白名单在 k8s_manager 层）"""
        mock_k8s.get_profile_gateway_logs = AsyncMock(
            side_effect=ValueError("invalid profile name: ../etc/passwd")
        )

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs"
            "?source=gateway&profile=..%2Fetc%2Fpasswd"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_engine_source_still_works(self, client, mock_db_session, mock_k8s):
        """source=engine（默认）走原 read_namespaced_pod_log 路径"""
        mock_k8s.get_pod_logs = AsyncMock(return_value="nginx-line\n")

        resp = await client.get(
            f"/api/controller/engine-instances/{EI_ID}/pods/{POD_NAME}/logs?source=engine"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "engine"
        assert "nginx-line" in data["logs"]
        mock_k8s.get_pod_logs.assert_called_once_with(POD_NAME, 200)


# ═══════════════════════════════════════════════════════════════
# K8sManager.list_profile_log_files / get_profile_gateway_logs (real singleton)
# ═══════════════════════════════════════════════════════════════

class TestK8sProfileLogHelpers:

    async def test_list_profile_log_files_parses_names(self):
        """解析 /tmp/gateway-*.log 输出为 profile 名列表"""
        from app.worker.k8s_manager import k8s_manager

        fake_stdout = (
            "/tmp/gateway-base.log\n"
            "/tmp/gateway-agent12-abcd-user34.log\n"
            "/tmp/notgateway.log\n"
            "/tmp/gateway-.log\n"
        )
        with patch.object(k8s_manager, "_ws_exec_sync") as mock_ws:
            mock_ws.return_value = (fake_stdout, 0, "")
            profiles = await k8s_manager.list_profile_log_files("pod-x")
        # 只保留合法 profile 名（gateway- 后非空、无非法字符）
        assert "base" in profiles
        assert "agent12-abcd-user34" in profiles
        assert "notgateway.log" not in profiles
        assert "" not in profiles

    async def test_get_profile_gateway_logs_invalid_name(self):
        """非法 profile 名直接 ValueError，不调 exec"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "_ws_exec_sync") as mock_ws:
            with pytest.raises(ValueError, match="invalid profile name"):
                await k8s_manager.get_profile_gateway_logs("pod-x", "base;rm -rf /", 100)
            mock_ws.assert_not_called()

    async def test_get_profile_gateway_logs_success(self):
        """合法 profile 名走 exec tail 并返回 stdout"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "_ws_exec_sync") as mock_ws:
            mock_ws.return_value = (b"line1\nline2\n", 0, "")
            result = await k8s_manager.get_profile_gateway_logs("pod-x", "base", 50)
            assert result == "line1\nline2\n"
            # 命令为 tail -n 50 /tmp/gateway-base.log（无 shell 注入面）
            cmd = mock_ws.call_args.args[1]
            assert cmd == ["tail", "-n", "50", "/tmp/gateway-base.log"]

