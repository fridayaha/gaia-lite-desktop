"""k8s_manager.py 方法测试

使用 mock kubernetes client 测试真实 K8sManager 方法。
"""

import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone


class TestK8sExecTarData:
    """exec_tar_data 方法测试（使用 WSClient WebSocket exec）"""

    async def test_exec_tar_data_success(self):
        """正常 tar 备份 Pod 数据（tar → cat → rm 三次 exec）"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True,
            "phase": "Running",
            "pod_name": "engine-hermes-test1234-abcde",
        })

        tar_bytes = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03fake-tar-data"
        # tar(写文件,返回EXIT=0) / cat(读 tar 流) / rm(清理)
        side = [(b"EXIT=0\n", 0, b""), (tar_bytes, 0, b""), (b"", 0, b"")]

        with patch.object(k8s_manager, "_ws_exec_sync", side_effect=side):
            result = await k8s_manager.exec_tar_data(
                "550e8400e29b41d4a716446655440000",
            )

            assert result == tar_bytes
            assert k8s_manager._ws_exec_sync.call_count == 3
            # V2：tar 备份只打包 opt/data（不再含 root/.hermes）
            tar_cmd = k8s_manager._ws_exec_sync.call_args_list[0].args[1]
            assert "opt/data" in " ".join(tar_cmd)
            assert "root/.hermes" not in " ".join(tar_cmd)
            # 退出码兜底：echo EXIT=$? 必须让 shell 展开 $?（误写 \$? 会输出字面 "EXIT=$?"，
            # 导致退出码校验恒失败 → "tar backup may have failed" 误告警）
            assert "echo EXIT=$?" in " ".join(tar_cmd)
            assert "EXIT=\\$?" not in " ".join(tar_cmd)

    async def test_exec_tar_data_pod_not_found(self):
        """Pod 不存在时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": False, "phase": "NotFound", "pod_name": None,
        })

        with pytest.raises(RuntimeError, match="Pod not found"):
            await k8s_manager.exec_tar_data("test-id")

    async def test_exec_tar_data_failure(self):
        """cat 读取 tar 失败时应抛异常（tar 命令本身用 echo EXIT=$? 兜底 rc=0）"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True, "phase": "Running", "pod_name": "pod-test",
        })
        # tar 成功(EXIT=0) / cat 失败 / rm 清理（rm 在 rc 检查前执行，需提供第 3 个返回值）
        side = [(b"EXIT=0\n", 0, b""), (b"", 1, b"cat: error"), (b"", 0, b"")]

        with patch.object(k8s_manager, "_ws_exec_sync", side_effect=side):
            with pytest.raises(RuntimeError, match="tar read failed"):
                await k8s_manager.exec_tar_data("test-id")


class TestK8sExecUntarData:
    """exec_untar_data 方法测试（使用 WSClient WebSocket exec）"""

    async def test_exec_untar_data_success(self):
        """正常 untar 数据到 Pod（分块 base64 heredoc，不走 stdin 避免 WSClient 挂死）"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True,
            "phase": "Running",
            "pod_name": "engine-hermes-test1234-abcde",
        })

        tar_data = b"\x1f\x8b\x08\x00fake-tar"

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 0, b"")):
            await k8s_manager.exec_untar_data(
                "550e8400e29b41d4a716446655440000",
                tar_data,
            )

            calls = k8s_manager._ws_exec_sync.call_args_list
            assert len(calls) >= 1
            # 所有调用都不走 stdin（stdin_data=None），规避 WSClient stdin 挂死
            for call in calls:
                assert call.kwargs.get("stdin_data") is None

            def _cmd_str(c):
                cmd = c.args[1]
                return cmd[-1] if isinstance(cmd, list) else str(cmd)

            # 找 base64 -d | tar 解压调用（finally 的 rm 在它之后，不能取 [-1]）
            untar_calls = [c for c in calls if "base64 -d" in _cmd_str(c)]
            assert len(untar_calls) == 1
            assert "tar xzf" in _cmd_str(untar_calls[0])

    async def test_exec_untar_uses_same_owner(self):
        """exec_untar 带 --same-owner（在 base64|tar 解压命令里）：保留属主跨恢复"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True,
            "phase": "Running",
            "pod_name": "engine-hermes-test1234-abcde",
        })
        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 0, b"")):
            await k8s_manager.exec_untar_data("550e8400e29b41d4a716446655440000", b"\x1f\x8bfake")
            calls = k8s_manager._ws_exec_sync.call_args_list

            def _cmd_str(c):
                cmd = c.args[1]
                return cmd[-1] if isinstance(cmd, list) else str(cmd)

            untar_calls = [c for c in calls if "base64 -d" in _cmd_str(c)]
            assert len(untar_calls) == 1
            assert "--same-owner" in _cmd_str(untar_calls[0])
            assert "xzf" in _cmd_str(untar_calls[0])

    async def test_exec_untar_data_pod_not_found(self):
        """Pod 不存在时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": False, "phase": "NotFound", "pod_name": None,
        })

        with pytest.raises(RuntimeError, match="Pod not found"):
            await k8s_manager.exec_untar_data("test-id", b"data")

    async def test_exec_untar_data_failure(self):
        """untar 命令失败时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True, "phase": "Running", "pod_name": "pod-test",
        })

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 1, b"untar: error")):
            with pytest.raises(RuntimeError, match="untar failed"):
                await k8s_manager.exec_untar_data("test-id", b"data")


class TestK8sExecHermesCommand:
    """exec_hermes_command 方法测试（使用 WSClient WebSocket exec）"""

    async def test_exec_hermes_command_success(self):
        """正常执行 hermes CLI 命令"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True,
            "phase": "Running",
            "pod_name": "engine-hermes-test1234-abcde",
        })

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"ok", 0, b"")):
            await k8s_manager.exec_hermes_command(
                "550e8400e29b41d4a716446655440000",
                ["hermes profile create test --clone --clone-from default"],
            )

            # 验证 console-script 被包装为 /bin/bash -c
            call_args = k8s_manager._ws_exec_sync.call_args
            command = call_args[0][1]  # second positional arg (pod_name is first)
            assert command[0] == "/bin/bash"
            assert command[1] == "-c"
            assert "hermes profile create" in command[2]

    async def test_exec_hermes_command_no_pod(self):
        """Pod 不存在时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": False, "phase": "NotFound", "pod_name": None,
        })

        with pytest.raises(RuntimeError, match="No pod found"):
            await k8s_manager.exec_hermes_command("test-id", ["echo hello"])

    async def test_exec_hermes_command_failure_logged(self):
        """hermes 命令失败时记录 warning 但不抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True, "phase": "Running", "pod_name": "pod-test",
        })

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 1, b"error msg")):
            # 不应抛异常，只记录 warning
            await k8s_manager.exec_hermes_command("test-id", ["hermes profile create test"])

    async def test_exec_hermes_command_multiple(self):
        """多条命令依次执行"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True, "phase": "Running", "pod_name": "pod-test",
        })

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"ok", 0, b"")):
            await k8s_manager.exec_hermes_command(
                "test-id",
                [
                    "hermes -p test gateway stop",
                    "hermes profile delete test --yes",
                ],
            )

            assert k8s_manager._ws_exec_sync.call_count == 2


class TestK8sExecWriteFile:
    """exec_write_file 方法测试（已改为使用 _ws_exec_sync）"""

    async def test_exec_write_file_success(self):
        """正常写入文件到 Pod"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True,
            "phase": "Running",
            "pod_name": "engine-hermes-test1234-abcde",
        })

        content = "model:\n  provider: auto\n"

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 0, b"")):
            await k8s_manager.exec_write_file(
                "550e8400e29b41d4a716446655440000",
                "/root/.hermes/config.yaml",
                content,
            )

            # heredoc 改造后文件内容在 cmd 字符串里（stdin_data=None）
            args, kwargs = k8s_manager._ws_exec_sync.call_args
            cmd = args[1][2]  # ["/bin/bash", "-c", cmd]
            assert content in cmd
            assert "cat > /root/.hermes/config.yaml" in cmd
            assert kwargs.get("stdin_data") is None
            assert kwargs.get("binary") is False
            assert kwargs.get("timeout") == 30

    async def test_exec_write_file_pod_not_found(self):
        """Pod 不存在时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": False, "phase": "NotFound", "pod_name": None,
        })

        with pytest.raises(RuntimeError, match="Pod not found"):
            await k8s_manager.exec_write_file("test-id", "/path/file", "content")

    async def test_exec_write_file_failure(self):
        """K8s exec 失败时应抛异常"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.get_pod_status = AsyncMock(return_value={
            "running": True, "phase": "Running", "pod_name": "pod-test",
        })

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 1, b"permission denied")):
            with pytest.raises(RuntimeError, match="write_file failed"):
                await k8s_manager.exec_write_file("test-id", "/path/file", "content")

    async def test_exec_write_file_in_pod_with_mode(self):
        """mode 非 None 时 cmd 追加 chmod（secrets.enc 写入即 0640，gateway 读不到）"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 0, b"")):
            await k8s_manager.exec_write_file_in_pod(
                "pod-x",
                "/opt/data/skills/abc/weather/secrets.enc",
                '{"api_key":"sk-x"}',
                mode=0o640,
            )
            args, _ = k8s_manager._ws_exec_sync.call_args
            cmd = args[1][2]  # ["/bin/bash", "-c", cmd]
            assert "cat > /opt/data/skills/abc/weather/secrets.enc" in cmd
            assert "chmod 640 /opt/data/skills/abc/weather/secrets.enc" in cmd
            # heredoc 结束符独占一行（bash 不接受行首 &&）
            assert "\n&& chmod" not in cmd

    async def test_exec_write_file_in_pod_no_mode(self):
        """mode 默认 None 时 cmd 不含 chmod（其他调用点行为不变）"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "_ws_exec_sync", return_value=(b"", 0, b"")):
            await k8s_manager.exec_write_file_in_pod("pod-x", "/p/config.yaml", "c")
            args, _ = k8s_manager._ws_exec_sync.call_args
            cmd = args[1][2]
            assert "chmod" not in cmd


class TestK8sPatchAgentEnvs:

    async def test_patch_envs_success(self):
        """patch_agent_envs 成功更新 Deployment env vars"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            mock_dep = MagicMock()
            mock_container = MagicMock()
            mock_container.env = []
            mock_dep.spec.template.spec.containers = [mock_container]
            mock_apps.read_namespaced_deployment.return_value = mock_dep

            await k8s_manager.patch_agent_envs(
                "550e8400e29b41d4a716446655440000",
                {"MODEL_PROVIDERS_JSON": '[{"type":"test"}]', "OPENROUTER_API_KEY": "sk-test"},
            )

            mock_apps.read_namespaced_deployment.assert_called_once()
            mock_apps.patch_namespaced_deployment.assert_called_once()

            # 验证调用 namespace 正确（使用关键字参数）
            _, kwargs = mock_apps.patch_namespaced_deployment.call_args
            assert kwargs.get("namespace") == "unionagents"

    async def test_patch_envs_empty(self):
        """空 env_overrides 不应报错"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            mock_dep = MagicMock()
            mock_container = MagicMock()
            mock_container.env = []
            mock_dep.spec.template.spec.containers = [mock_container]
            mock_apps.read_namespaced_deployment.return_value = mock_dep

            await k8s_manager.patch_agent_envs("test-id", {})
            mock_apps.patch_namespaced_deployment.assert_called_once()

    async def test_patch_envs_deployment_not_found(self):
        """Deployment 404 时不抛异常（日志警告）"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            err = ApiException()
            err.status = 404
            mock_apps.read_namespaced_deployment.side_effect = err

            await k8s_manager.patch_agent_envs("test-id", {"KEY": "val"})

    async def test_patch_envs_api_error(self):
        """非 404 的 API 异常应继续抛出"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            err = ApiException()
            err.status = 500
            mock_apps.read_namespaced_deployment.side_effect = err

            with pytest.raises(ApiException):
                await k8s_manager.patch_agent_envs("test-id", {"KEY": "val"})


class TestK8sRolloutRestart:

    async def test_rollout_restart_success(self):
        """rollout_restart 成功触发"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            await k8s_manager.rollout_restart("550e8400e29b41d4a716446655440000")

            mock_apps.patch_namespaced_deployment.assert_called_once()
            patch_call = mock_apps.patch_namespaced_deployment.call_args
            args, kwargs = patch_call
            name = kwargs.get("name", args[0] if args else "")
            assert name.startswith("engine-hermes-"), f"Expected engine-hermes- prefix, got: {name}"

            body = kwargs.get("body", {})
            annotations = body["spec"]["template"]["metadata"]["annotations"]
            assert "kubectl.kubernetes.io/restartedAt" in annotations

    async def test_rollout_restart_not_found(self):
        """Deployment 404 时不抛异常"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        with patch.object(k8s_manager, "apps_v1") as mock_apps:
            err = ApiException()
            err.status = 404
            mock_apps.patch_namespaced_deployment.side_effect = err

            await k8s_manager.rollout_restart("test-id")


class TestK8sPVC:
    """PVC 创建/删除/检查方法测试"""

    def test_pvc_name_all_scope(self):
        """ALL scope PVC 命名: engine-data-{short_id}"""
        from app.worker.k8s_manager import _pvc_name
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        name = _pvc_name(agent_id, "ALL", None)
        assert name == "engine-data-550e8400"

    def test_pvc_name_scoped(self):
        """Scoped PVC 命名: engine-data-{short_id}-{shash}"""
        from app.worker.k8s_manager import _pvc_name
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        name = _pvc_name(agent_id, "USER", "user-abc")
        assert name.startswith("engine-data-550e8400-")
        assert len(name) == len("engine-data-550e8400-") + 6

    def test_create_pvc_success(self):
        """正常创建 PVC"""
        from app.worker.k8s_manager import k8s_manager

        result = k8s_manager.create_pvc("engine-data-test", {"app": "engine-hermes"})
        assert result is True
        k8s_manager.core_v1.create_namespaced_persistent_volume_claim.assert_called_once()

    def test_create_pvc_already_exists(self):
        """PVC 已存在时返回 False"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err = ApiException()
        err.status = 409
        k8s_manager.core_v1.create_namespaced_persistent_volume_claim.side_effect = err

        result = k8s_manager.create_pvc("engine-data-test", {})
        assert result is False
        k8s_manager.core_v1.create_namespaced_persistent_volume_claim.side_effect = None

    def test_delete_pvc_success(self):
        """正常删除 PVC"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.delete_pvc("engine-data-test")
        k8s_manager.core_v1.delete_namespaced_persistent_volume_claim.assert_called_once_with(
            "engine-data-test", "unionagents",
        )

    def test_delete_pvc_not_found(self):
        """PVC 不存在时删除静默跳过"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err = ApiException()
        err.status = 404
        k8s_manager.core_v1.delete_namespaced_persistent_volume_claim.side_effect = err
        k8s_manager.delete_pvc("engine-data-test")  # 不应抛异常
        k8s_manager.core_v1.delete_namespaced_persistent_volume_claim.side_effect = None

    def test_pvc_exists_true(self):
        """PVC 存在返回 True"""
        from app.worker.k8s_manager import k8s_manager

        k8s_manager.core_v1.read_namespaced_persistent_volume_claim.return_value = MagicMock()
        assert k8s_manager.pvc_exists("engine-data-test") is True

    def test_pvc_exists_false(self):
        """PVC 不存在返回 False"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err = ApiException()
        err.status = 404
        k8s_manager.core_v1.read_namespaced_persistent_volume_claim.side_effect = err
        assert k8s_manager.pvc_exists("engine-data-test") is False
        k8s_manager.core_v1.read_namespaced_persistent_volume_claim.side_effect = None


class TestK8sCreateEnginePVC:
    """create_agent_engine 在 PVC 模式下的行为"""

    async def test_create_engine_with_pvc(self):
        """PVC 模式：创建 PVC + Deployment 使用 PVC Volume + mount_path=/opt/data"""
        from app.worker.k8s_manager import k8s_manager, _agent_short_id
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        expected_pvc = f"engine-data-{_agent_short_id(agent_id)}"

        with patch("pkg.common.config.settings.pvc_enabled", True):
            with patch.object(k8s_manager, "create_pvc") as mock_create_pvc:
                with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mock_create_dep:
                    with patch.object(k8s_manager.core_v1, "create_namespaced_service") as mock_create_svc:
                        mock_create_pvc.return_value = True

                        await k8s_manager.create_agent_engine(agent_id)

                        # PVC 被创建（在 Deployment 之前）
                        mock_create_pvc.assert_called_once()

                        # Deployment 使用 PVC Volume
                        # create_namespaced_deployment(namespace, body) 使用位置参数
                        dep = mock_create_dep.call_args[0][1]
                        volume = dep.spec.template.spec.volumes[0]
                        assert volume.persistent_volume_claim is not None
                        assert volume.persistent_volume_claim.claim_name == expected_pvc

                        # mount_path 为 /opt/data
                        mount = dep.spec.template.spec.containers[0].volume_mounts[0]
                        assert mount.mount_path == "/opt/data"

    async def test_create_engine_pvc_disabled(self):
        """V1 emptyDir 分支已移除：PVC 始终创建，mount_path=/opt/data（保留方法名兼容，断言 V2 行为）"""
        from app.worker.k8s_manager import k8s_manager
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch.object(k8s_manager, "create_pvc") as mock_create_pvc:
            with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mock_create_dep:
                with patch.object(k8s_manager.core_v1, "create_namespaced_service"):
                    await k8s_manager.create_agent_engine(agent_id)

                    # V2 强制 PVC：create_pvc 被调用
                    mock_create_pvc.assert_called_once()

                    # mount_path 为 /opt/data（V2），不再是 /root/.hermes
                    dep = mock_create_dep.call_args[0][1]
                    mount = dep.spec.template.spec.containers[0].volume_mounts[0]
                    assert mount.mount_path == "/opt/data"

    async def test_create_engine_group_code_label(self):
        """group_code 写入 Pod/Deployment/Service/PVC label `group.unionagents/group-code`"""
        from app.worker.k8s_manager import k8s_manager
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch.object(k8s_manager, "create_pvc") as mock_create_pvc:
            with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mock_create_dep:
                with patch.object(k8s_manager.core_v1, "create_namespaced_service") as mock_create_svc:
                    await k8s_manager.create_agent_engine(agent_id, group_code="yanfa")

                    # PVC labels 含 group-code
                    pvc_labels = mock_create_pvc.call_args[0][1]
                    assert pvc_labels["group.unionagents/group-code"] == "yanfa"

                    # Deployment metadata labels + pod template labels 都含 group-code
                    dep = mock_create_dep.call_args[0][1]
                    assert dep.metadata.labels["group.unionagents/group-code"] == "yanfa"
                    pod_labels = dep.spec.template.metadata.labels
                    assert pod_labels["group.unionagents/group-code"] == "yanfa"

                    # Service labels 含 group-code
                    svc = mock_create_svc.call_args[0][1]
                    assert svc.metadata.labels["group.unionagents/group-code"] == "yanfa"

    async def test_create_engine_no_group_code_no_label(self):
        """group_code 缺失时不加 group.unionagents/group-code label（向后兼容）"""
        from app.worker.k8s_manager import k8s_manager
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch.object(k8s_manager, "create_pvc") as mock_create_pvc:
            with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mock_create_dep:
                with patch.object(k8s_manager.core_v1, "create_namespaced_service"):
                    # 不传 group_code（None）
                    await k8s_manager.create_agent_engine(agent_id)

                    pvc_labels = mock_create_pvc.call_args[0][1]
                    assert "group.unionagents/group-code" not in pvc_labels
                    dep = mock_create_dep.call_args[0][1]
                    assert "group.unionagents/group-code" not in dep.metadata.labels

    async def test_create_engine_409_backfills_component_label(self):
        """409（Deployment 已存在）：钉住现有 selector（避免 422 immutable），patch body 仍带
        `unionagents.io/component=engine` template label，让 strategic-merge 回填老 Deployment。

        Regression: af808a9 前创建的老 engine Deployment selector/template labels 缺
        `unionagents.io/component=engine`；browser Pod NetworkPolicy 据此 label 放行
        engine→9222 CDP。create_agent_engine 409 之前整体 patch 会撞 selector immutable 422，
        label 永远回填不上 → CDP 被 NP 挡（Connection refused）。
        """
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException
        from unittest.mock import MagicMock, patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        err409 = ApiException(); err409.status = 409

        # 老 Deployment：selector 不含 component label（模拟 af808a9 前创建）
        existing = MagicMock()
        existing.spec.selector = MagicMock(
            match_labels={"app": "engine-hermes-550e8400", "agent-id": agent_id, "scope-hash": "f664d6"}
        )

        captured = {}

        def fake_patch(name, namespace, body):
            captured["body"] = body
            captured["name"] = name

        with patch.object(k8s_manager, "create_pvc"), \
                patch.object(k8s_manager.apps_v1, "create_namespaced_deployment", side_effect=err409), \
                patch.object(k8s_manager.apps_v1, "read_namespaced_deployment", return_value=existing) as mock_read, \
                patch.object(k8s_manager.apps_v1, "patch_namespaced_deployment", side_effect=fake_patch) as mock_patch, \
                patch.object(k8s_manager.core_v1, "create_namespaced_service"):
            await k8s_manager.create_agent_engine(agent_id)

        # 走 409 分支：读了现有 Deployment + patch 一次
        mock_read.assert_called_once()
        mock_patch.assert_called_once()
        body = captured["body"]
        # selector 钉成现有值（no-op，避免 422 immutable），不是新 pod_labels（含 component）
        assert body.spec.selector is existing.spec.selector
        # template labels 仍含 component（strategic-merge 合并进现有 Deployment）
        assert body.spec.template.metadata.labels["unionagents.io/component"] == "engine"


class TestK8sDeleteEnginePVC:
    """delete_agent_engine 在 PVC 模式下应同时删除 PVC"""

    async def test_delete_engine_with_pvc(self):
        """删除 Deployment + Service + PVC"""
        from app.worker.k8s_manager import k8s_manager, _agent_short_id
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        expected_pvc = f"engine-data-{_agent_short_id(agent_id)}"

        with patch.object(k8s_manager.apps_v1, "delete_namespaced_deployment") as mock_del_dep:
            with patch.object(k8s_manager.core_v1, "delete_namespaced_service") as mock_del_svc:
                with patch.object(k8s_manager, "delete_pvc") as mock_del_pvc:
                    await k8s_manager.delete_agent_engine(agent_id)

                    mock_del_dep.assert_called_once()
                    mock_del_svc.assert_called_once()
                    mock_del_pvc.assert_called_once_with(expected_pvc)

    async def test_delete_engine_pvc_disabled(self):
        """V1 分支已移除：pvc_reclaim_on_destroy=False 时不删 PVC（保留方法名兼容）"""
        from app.worker.k8s_manager import k8s_manager
        from unittest.mock import patch

        agent_id = "550e8400-e29b-41d4-a716-446655440000"

        with patch("pkg.common.config.settings.pvc_reclaim_on_destroy", False):
            with patch.object(k8s_manager.apps_v1, "delete_namespaced_deployment"):
                with patch.object(k8s_manager.core_v1, "delete_namespaced_service"):
                    with patch.object(k8s_manager, "delete_pvc") as mock_del_pvc:
                        await k8s_manager.delete_agent_engine(agent_id)
                        mock_del_pvc.assert_not_called()


class TestK8sGetPodsForInstance:
    """get_pods_for_instance：从 AgentDeployment.instance_id 派生 agent_id。

    Regression: task6 把 agent_deployments.agent_id 重命名为 instance_id；
    该方法曾误用 dep.agent_id → AttributeError。此处断言改用 dep.instance_id。
    """

    async def test_uses_instance_id_not_agent_id(self, mock_db_session):
        from app.worker.k8s_manager import k8s_manager
        from pkg.common.models import DeploymentStatus
        from types import SimpleNamespace

        inst_id = uuid.uuid4()
        dep = SimpleNamespace(
            id=uuid.uuid4(),
            instance_id=inst_id,            # 重命名后的列（旧名 agent_id 已不存在）
            scope_type="ALL",
            scope_target_id=None,
            pod_name="engine-hermes-abcd1234",
            status=DeploymentStatus.RUNNING,
            node_name="node-1",
        )
        result = MagicMock()
        result.scalars.return_value.all.return_value = [dep]
        mock_db_session.execute.return_value = result

        empty_pod_list = MagicMock()
        empty_pod_list.items = []
        with patch.object(k8s_manager, "core_v1") as mock_core:
            mock_core.list_namespaced_pod.return_value = empty_pod_list
            pods = await k8s_manager.get_pods_for_instance(str(uuid.uuid4()), mock_db_session)

        assert len(pods) == 1
        # agent_id 字段派生自 dep.instance_id（若回退到 dep.agent_id 会 AttributeError）
        assert pods[0]["agent_id"] == str(inst_id)
        assert pods[0]["deployment_id"] == str(dep.id)
        assert pods[0]["status"] == DeploymentStatus.RUNNING.value


class TestK8sNonBlocking:
    """长 IO 不阻塞 event loop + 总超时(防连接池死锁)。"""

    async def test_exec_tar_data_by_pod_total_timeout(self, monkeypatch):
        """exec_tar_data_by_pod 总超时:_ws_exec_sync 阻塞 > 超时 → asyncio.TimeoutError。

        防止 exec 卡死时连接被无限占用(连接池死锁根因之一)。
        """
        import asyncio
        import time

        from app.worker import k8s_manager as km

        monkeypatch.setattr(km, "_EXEC_TAR_TOTAL_TIMEOUT", 1)  # 测试用 1s 避免等 300s

        def _slow_ws(*a, **kw):
            # 在 to_thread 里阻塞,但 wait_for(1) 1s 即超时
            time.sleep(5)
            return (b"EXIT=0", 0, b"")

        monkeypatch.setattr(km.k8s_manager, "_ws_exec_sync", _slow_ws)

        with pytest.raises(asyncio.TimeoutError):
            await km.k8s_manager.exec_tar_data_by_pod("pod-x")

    async def test_wait_pod_ready_timeout_returns_false(self, monkeypatch):
        """wait_pod_ready 超时返回 False(经 to_thread 不阻塞 event loop)。"""
        import time
        from unittest.mock import AsyncMock

        from app.worker import k8s_manager as km

        # 直接 mock get_pod_status 返回非 Running(避免前测试 mock 污染 _get_pod_status_sync)
        monkeypatch.setattr(
            km.k8s_manager,
            "get_pod_status",
            AsyncMock(return_value={"running": False, "phase": "Pending"}),
        )

        start = time.monotonic()
        result = await km.k8s_manager.wait_pod_ready("agent-x", timeout=2)
        elapsed = time.monotonic() - start

        assert result is False
        assert 2 <= elapsed < 8  # ~2s 超时,不阻塞(CI 慢放宽上限)


class TestK8sBrowserPodNaming:
    """browser Pod 命名确定性（cdp_url 跨重建稳定）"""

    def test_browser_name_format(self):
        from app.worker.k8s_manager import _browser_name, _agent_short_id, _profile_hash
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        pn = "alice"
        assert _browser_name(agent_id, pn) == f"browser-{_agent_short_id(agent_id)}-{_profile_hash(pn)}"

    def test_browser_name_stable_across_calls(self):
        """同名 agent+profile → 同名 Pod（重建后 cdp_url 不变）"""
        from app.worker.k8s_manager import _browser_name
        a = "550e8400-e29b-41d4-a716-446655440000"
        assert _browser_name(a, "alice") == _browser_name(a, "alice")

    def test_browser_name_differs_per_profile(self):
        from app.worker.k8s_manager import _browser_name
        a = "550e8400-e29b-41d4-a716-446655440000"
        assert _browser_name(a, "alice") != _browser_name(a, "bob")


class TestK8sCreateBrowserPod:
    """create_browser_pod：建 Deployment(chrome+cdp-proxy)+Service+PVC+Secret+ConfigMap+NetworkPolicy"""

    async def test_create_browser_pod_builds_resources(self):
        from app.worker.k8s_manager import (
            k8s_manager, _browser_name, _browser_pvc_name,
            _browser_vnc_secret_name, _browser_network_policy_name,
        )
        from kubernetes.client.exceptions import ApiException
        from pkg.common.config import settings

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        profile_name = "alice"
        expected_name = _browser_name(agent_id, profile_name)

        # VNC Secret 不存在 → 404 → 走创建分支
        err404 = ApiException()
        err404.status = 404

        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", side_effect=err404):
            with patch.object(k8s_manager.core_v1, "create_namespaced_secret") as mk_secret:
                with patch.object(k8s_manager.core_v1, "create_namespaced_persistent_volume_claim") as mk_pvc:
                    with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mk_dep:
                        with patch.object(k8s_manager.core_v1, "create_namespaced_service") as mk_svc:
                            with patch.object(k8s_manager.net_v1, "create_namespaced_network_policy") as mk_netpol:
                                ret = await k8s_manager.create_browser_pod(agent_id, profile_name)

                                assert ret["name"] == expected_name
                                assert ret["vnc_pw"]  # VNC 明文密码总返回
                                mk_secret.assert_called_once()
                                mk_pvc.assert_called_once()
                                mk_dep.assert_called_once()
                                mk_svc.assert_called_once()
                                mk_netpol.assert_called_once()

    async def test_browser_deployment_has_chrome_and_cdp_proxy(self):
        """Deployment 含 chrome + cdp-proxy 两容器；cdp-proxy command 跑 relay，chrome 用 ACR 镜像"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException
        from pkg.common.config import settings

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        err404 = ApiException(); err404.status = 404

        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", side_effect=err404):
            with patch.object(k8s_manager.core_v1, "create_namespaced_secret"):
                with patch.object(k8s_manager.core_v1, "create_namespaced_persistent_volume_claim"):
                    with patch.object(k8s_manager.core_v1, "create_namespaced_config_map"):
                        with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mk_dep:
                            with patch.object(k8s_manager.core_v1, "create_namespaced_service"):
                                with patch.object(k8s_manager.net_v1, "create_namespaced_network_policy"):
                                    await k8s_manager.create_browser_pod(agent_id, "alice")
                                    dep = mk_dep.call_args[0][1]
                                    containers = dep.spec.template.spec.containers
                                    assert len(containers) == 2
                                    by_name = {c.name: c for c in containers}
                                    assert "chrome" in by_name
                                    assert "cdp-proxy" in by_name
                                    assert by_name["chrome"].image == settings.browser_sidecar_image
                                    # cdp-proxy 跑 CDP 感知代理（browser-v2 内置 cdp_proxy.py）
                                    assert by_name["cdp-proxy"].command == ["python3", "/opt/cdp_proxy.py"]

    async def test_browser_service_exposes_cdp_and_vnc_ports(self):
        """Service 暴露 2 端口（CDP 9222 + VNC 6901）"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err404 = ApiException(); err404.status = 404
        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", side_effect=err404):
            with patch.object(k8s_manager.core_v1, "create_namespaced_secret"):
                with patch.object(k8s_manager.core_v1, "create_namespaced_persistent_volume_claim"):
                    with patch.object(k8s_manager.core_v1, "create_namespaced_config_map"):
                        with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment"):
                            with patch.object(k8s_manager.core_v1, "create_namespaced_service") as mk_svc:
                                with patch.object(k8s_manager.net_v1, "create_namespaced_network_policy"):
                                    await k8s_manager.create_browser_pod("550e8400", "alice")
                                    svc = mk_svc.call_args[0][1]
                                    assert len(svc.spec.ports) == 2

    async def test_browser_pod_group_code_label(self):
        """group_code 写入 browser Pod/PVC/Service label"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err404 = ApiException(); err404.status = 404
        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", side_effect=err404):
            with patch.object(k8s_manager.core_v1, "create_namespaced_secret"):
                with patch.object(k8s_manager.core_v1, "create_namespaced_persistent_volume_claim") as mk_pvc:
                    with patch.object(k8s_manager.core_v1, "create_namespaced_config_map"):
                        with patch.object(k8s_manager.apps_v1, "create_namespaced_deployment") as mk_dep:
                            with patch.object(k8s_manager.core_v1, "create_namespaced_service") as mk_svc:
                                with patch.object(k8s_manager.net_v1, "create_namespaced_network_policy"):
                                    await k8s_manager.create_browser_pod("550e8400", "alice", group_code="yanfa")
                                    pvc_labels = mk_pvc.call_args[0][1].metadata.labels
                                    assert pvc_labels["group.unionagents/group-code"] == "yanfa"
                                    dep = mk_dep.call_args[0][1]
                                    assert dep.metadata.labels["group.unionagents/group-code"] == "yanfa"
                                    pod_labels = dep.spec.template.metadata.labels
                                    assert pod_labels["group.unionagents/group-code"] == "yanfa"


class TestK8sDeleteBrowserPod:
    """delete_browser_pod：删 Deployment+Service+PVC+Secret+ConfigMap+NetworkPolicy"""

    async def test_delete_browser_pod_removes_all(self):
        from app.worker.k8s_manager import k8s_manager

        with patch.object(k8s_manager.apps_v1, "delete_namespaced_deployment") as mk_dep:
            with patch.object(k8s_manager.core_v1, "delete_namespaced_service") as mk_svc:
                with patch.object(k8s_manager.core_v1, "delete_namespaced_persistent_volume_claim") as mk_pvc:
                    with patch.object(k8s_manager.core_v1, "delete_namespaced_secret") as mk_secret:
                        with patch.object(k8s_manager.net_v1, "delete_namespaced_network_policy") as mk_netpol:
                            await k8s_manager.delete_browser_pod("550e8400", "alice")
                            mk_dep.assert_called_once()
                            mk_svc.assert_called_once()
                            mk_pvc.assert_called_once()
                            mk_secret.assert_called_once()
                            mk_netpol.assert_called_once()

    async def test_delete_browser_pod_404_silent(self):
        """资源不存在（404）静默跳过，不抛异常"""
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err404 = ApiException(); err404.status = 404
        with patch.object(k8s_manager.apps_v1, "delete_namespaced_deployment", side_effect=err404):
            with patch.object(k8s_manager.core_v1, "delete_namespaced_service", side_effect=err404):
                with patch.object(k8s_manager.core_v1, "delete_namespaced_persistent_volume_claim", side_effect=err404):
                    with patch.object(k8s_manager.core_v1, "delete_namespaced_secret", side_effect=err404):
                        with patch.object(k8s_manager.core_v1, "delete_namespaced_config_map", side_effect=err404):
                            with patch.object(k8s_manager.net_v1, "delete_namespaced_network_policy", side_effect=err404):
                                await k8s_manager.delete_browser_pod("550e8400", "alice")  # 不抛

    async def test_delete_browser_pod_non404_does_not_abort_remaining(self):
        """首个删除抛非 404（如 500）→ 不中止，剩余 4 个资源仍被尝试删除（best-effort 全试）。

        回归：旧实现 `if e.status != 404: raise` 会在第一个非 404 错误时中止循环，剩余
        PVC/Secret/NetPol 永不删；DESTROY 时 DB 行已删无人重试 → 永久泄漏。
        """
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        err500 = ApiException(); err500.status = 500
        with patch.object(k8s_manager.apps_v1, "delete_namespaced_deployment", side_effect=err500):
            with patch.object(k8s_manager.core_v1, "delete_namespaced_service") as mk_svc:
                with patch.object(k8s_manager.core_v1, "delete_namespaced_persistent_volume_claim") as mk_pvc:
                    with patch.object(k8s_manager.core_v1, "delete_namespaced_secret") as mk_secret:
                        with patch.object(k8s_manager.net_v1, "delete_namespaced_network_policy") as mk_netpol:
                            await k8s_manager.delete_browser_pod("550e8400", "alice")  # 不抛
                            # 剩余 4 个仍被调用
                            mk_svc.assert_called_once()
                            mk_pvc.assert_called_once()
                            mk_secret.assert_called_once()
                            mk_netpol.assert_called_once()


class TestK8sBrowserPodScale:
    async def test_scale_browser_to_zero(self):
        from app.worker.k8s_manager import k8s_manager, _browser_name
        with patch.object(k8s_manager.apps_v1, "patch_namespaced_deployment_scale") as mk:
            await k8s_manager.scale_browser_to_zero("550e8400", "alice")
            args, kwargs = mk.call_args
            name = kwargs.get("name", args[0] if args else "")
            assert name == _browser_name("550e8400", "alice")
            assert kwargs["body"]["spec"]["replicas"] == 0

    async def test_resume_browser_pod_exists(self):
        from app.worker.k8s_manager import k8s_manager
        with patch.object(k8s_manager.apps_v1, "patch_namespaced_deployment_scale") as mk:
            ok = await k8s_manager.resume_browser_pod("550e8400", "alice")
            assert ok is True
            assert mk.call_args.kwargs["body"]["spec"]["replicas"] == 1

    async def test_resume_browser_pod_missing_returns_false(self):
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException
        err404 = ApiException(); err404.status = 404
        with patch.object(k8s_manager.apps_v1, "patch_namespaced_deployment_scale", side_effect=err404):
            ok = await k8s_manager.resume_browser_pod("550e8400", "alice")
            assert ok is False


class TestBrowserPodRfbAuth:
    """_create_browser_pod_sync：chrome 容器 env 必须关掉 RFB VncAuth（NoAuth）。

    根因：kasm 的 -rfbauth ~/.vnc/passwd 是镜像内置的空密码默认文件（vnc_startup.sh 只写
    ~/.kasmpasswd，从不重生成 ~/.vnc/passwd），noVNC 标准 VncAuth 送 VNC_PW 必被拒。设
    VNCOPTIONS=-SecurityTypes None 让 RFB 走 NoAuth；WS 升级层 Basic auth（~/.kasmpasswd）
    仍由 kasm 强制。详见 docs/features/browser-sandbox-vnc-debug-status.md。
    """

    def test_chrome_env_has_securitytypes_none(self):
        from app.worker import k8s_manager as k8s_mod
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        # conftest 把 V1EnvVar mock 成不保留参数的 MagicMock，无法断言 env 内容。
        # 换成记录型 fake：保留 name/value/value_from，可断言 chrome 容器 env。
        class FakeEnvVar:
            def __init__(self, name=None, value=None, value_from=None):
                self.name = name
                self.value = value
                self.value_from = value_from

        captured = {}

        def fake_create_dep(namespace, deployment):
            captured["deployment"] = deployment
            err = ApiException(); err.status = 409  # 已存在 → 走 patch 分支，不影响 spec 捕获
            raise err

        err409 = ApiException(); err409.status = 409
        with patch.object(k8s_mod, "V1EnvVar", FakeEnvVar), \
                patch.object(k8s_manager, "_create_browser_vnc_secret", return_value="test-vnc-pw"), \
                patch.object(k8s_manager, "_ensure_browser_network_policy"), \
                patch.object(k8s_manager.core_v1, "create_namespaced_pvc", side_effect=err409), \
                patch.object(k8s_manager.core_v1, "create_namespaced_service", side_effect=err409), \
                patch.object(k8s_manager.apps_v1, "create_namespaced_deployment", side_effect=fake_create_dep), \
                patch.object(k8s_manager.apps_v1, "patch_namespaced_deployment"):
            result = k8s_manager._create_browser_pod_sync(
                "1d515bfc-0a5a-4704-9b85-91960baef51b", "1d515bfc-cfd2a9-c246cea4"
            )

        dep = captured["deployment"]
        chrome = next(c for c in dep.spec.template.spec.containers if c.name == "chrome")
        env = {e.name: e.value for e in chrome.env}
        # RFB 层 NoAuth + 新连接踢旧（kasm 单会话，避免 "Server is already in use" 拒新）
        assert env.get("VNCOPTIONS") == "-SecurityTypes None -DisconnectClients 1"
        # VNC_PW 仍从 Secret 注入（value_from 非 None）：gateway WS Basic auth 需要
        vnc_pw_env = next(e for e in chrome.env if e.name == "VNC_PW")
        assert vnc_pw_env.value is None
        assert vnc_pw_env.value_from is not None
        # 明文密码仍返回供 manager 写 internal_port_map（gateway 经 DB 取用做 Basic auth）
        assert result["vnc_pw"] == "test-vnc-pw"


class TestBrowserVncSecretHeal:
    """_create_browser_vnc_secret：已存在但 VNC_PW 空/损坏 → create 撞 409 → patch 自愈。"""

    def test_corrupt_secret_healed_via_patch(self):
        """Secret 存在但 VNC_PW 为空 → create 409 → patch 新密码，返回新密码。

        回归：旧实现 create_namespaced_secret 未包 try，409 直接抛 → ensure_browser_pod_for_profile
        catch 返回 None，browser Pod 静默不建、无清理、无报错，只能手动删 Secret 恢复。
        """
        from app.worker.k8s_manager import k8s_manager
        from kubernetes.client.exceptions import ApiException

        # 已存在 Secret，但 VNC_PW 为空（损坏）
        existing = MagicMock()
        existing.data = {"VNC_PW": ""}
        err409 = ApiException(); err409.status = 409

        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", return_value=existing), \
                patch.object(k8s_manager.core_v1, "create_namespaced_secret", side_effect=err409) as mk_create, \
                patch.object(k8s_manager.core_v1, "patch_namespaced_secret") as mk_patch:
            pw = k8s_manager._create_browser_vnc_secret("browser-vnc-x", {"app": "browser"})

        assert pw  # 返回新明文密码
        mk_create.assert_called_once()
        mk_patch.assert_called_once()  # 409 后 patch 自愈
        # patch body 含 stringData.VNC_PW
        body = mk_patch.call_args.kwargs.get("body") or mk_patch.call_args.args[2]
        assert body["stringData"]["VNC_PW"] == pw

    def test_valid_existing_secret_returned_without_rewrite(self):
        """Secret 存在且 VNC_PW 有效 → 直接返回原密码，不 create/patch（重建 Pod 复用）。"""
        import base64
        from app.worker.k8s_manager import k8s_manager

        existing = MagicMock()
        existing.data = {"VNC_PW": base64.b64encode(b"original-pw").decode()}

        with patch.object(k8s_manager.core_v1, "read_namespaced_secret", return_value=existing), \
                patch.object(k8s_manager.core_v1, "create_namespaced_secret") as mk_create, \
                patch.object(k8s_manager.core_v1, "patch_namespaced_secret") as mk_patch:
            pw = k8s_manager._create_browser_vnc_secret("browser-vnc-x", {"app": "browser"})

        assert pw == "original-pw"
        mk_create.assert_not_called()
        mk_patch.assert_not_called()
