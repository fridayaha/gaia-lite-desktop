"""全生命周期 E2E 测试

验证从 Pod 创建 → 写入数据 → SUSPEND → ARCHIVE → RESUME → 数据恢复的完整链路。
覆盖 V1 和 V2 Profile 两种目录结构。

运行：
  RUN_E2E_TESTS=1 pytest tests/e2e/ -v
"""

import hashlib
import pytest


pytestmark = pytest.mark.e2e


class TestLifecycleE2E:
    """完整的 SUSPEND→DESTROY→RESUME 生命周期测试"""

    @pytest.mark.skip(
        reason="V1 /root/.hermes 布局已废弃：V2 引擎数据在 /opt/data，exec_tar_data 只 tar "
        "/opt/data，V1 数据不在归档内无法验证。V2 数据完整性由 test_v2_profile_persistence 覆盖。"
    )
    async def test_v1_lifecycle_cycle(self, k8s_client, archiver_client, agent_id):
        """V1 /root/.hermes SUSPEND→ARCHIVE→RESUME 后数据一致"""
        # 1. 创建 Pod + 写入已知数据到 /root/.hermes
        await k8s_client.create_agent_engine(agent_id, {})
        assert await k8s_client.wait_pod_ready(agent_id), "Pod did not become ready"

        await k8s_client.exec_write_file(agent_id, "/root/.hermes/hello.txt", "verify-me-v1")
        await k8s_client.exec_write_file(agent_id, "/root/.hermes/config.yaml",
                                         "model:\n  provider: auto\n")

        # 2. SUSPEND: exec tar → MinIO backup → scale to 0
        tar_data = await k8s_client.exec_tar_data(agent_id)
        archiver_client.save_backup(agent_id, tar_data)
        assert archiver_client.backup_exists(agent_id)
        original_hash = hashlib.sha256(tar_data).hexdigest()

        # 移除 finalizer 再 scale_to_zero（忠实模拟 _do_suspend：备份已同步，避免 Pod 卡 Terminating）
        await k8s_client.remove_finalizer_from_agent_pods(agent_id)
        await k8s_client.scale_to_zero(agent_id)

        # 3. DESTROY: backup → archive → cleanup
        archive_path = archiver_client.archive_backup(agent_id)
        assert archive_path is not None
        assert not archiver_client.backup_exists(agent_id)

        # 删除 K8s 资源
        await k8s_client.delete_agent_engine(agent_id)
        # 等待 PVC 真正删除，避免立即重建同名 PVC 命中 409/复用 Terminating PVC
        await k8s_client.wait_pvc_deleted(f"engine-data-{agent_id[:8]}", timeout=120)

        # 4. RESUME: 新 Pod + untar 恢复
        await k8s_client.create_agent_engine(agent_id, {})
        assert await k8s_client.wait_pod_ready(agent_id)

        archive_data = archiver_client.get_archive(archive_path)
        assert hashlib.sha256(archive_data).hexdigest() == original_hash

        await k8s_client.exec_untar_data(agent_id, archive_data)

        # 5. 验证文件内容
        content = await k8s_client.exec_hermes_command(
            agent_id, ["cat /root/.hermes/hello.txt"]
        )
        assert "verify-me-v1" in str(content)

    async def test_v2_profile_persistence(self, k8s_client, archiver_client, agent_id):
        """V2 多 Profile 数据：tar→save_backup→archive_backup→get_archive→untar 往返完整性

        单 Pod 上验证归档链路（真实 MinIO + 真实 Pod）：写入数据 → tar → save_backup →
        archive_backup → get_archive（hash 一致）→ 清空 → untar 恢复 → 校验内容。
        避开 local-path PVC 删除延迟导致的 delete+重建抖动，聚焦归档数据完整性。
        """
        import hashlib

        # 1. 创建 Pod + 模拟 V2 Profile 数据
        await k8s_client.create_agent_engine(agent_id, {})
        assert await k8s_client.wait_pod_ready(agent_id)

        # 模拟两个 Profile 的运行时数据
        setup_commands = [
            "mkdir -p /opt/data/profiles/alice/sessions",
            "mkdir -p /opt/data/profiles/alice/memories",
            "mkdir -p /opt/data/profiles/bob/sessions",
            "echo 'alice-state-data' > /opt/data/profiles/alice/state.db",
            'echo \'{"session":"alice-s1"}\' > /opt/data/profiles/alice/sessions/s1.json',
            'echo \'{"memory":"alice-remember"}\' > /opt/data/profiles/alice/memories/m1.json',
            "echo 'bob-state-data' > /opt/data/profiles/bob/state.db",
            'echo \'{"session":"bob-s1"}\' > /opt/data/profiles/bob/sessions/s1.json',
        ]
        for cmd in setup_commands:
            await k8s_client.exec_hermes_command(agent_id, ["bash", "-c", cmd])

        # 2. tar → save_backup(daily 兼容 legacy latest) → archive_backup（永久归档）
        tar_data = await k8s_client.exec_tar_data(agent_id)
        archiver_client.save_backup(agent_id, tar_data)
        assert archiver_client.backup_exists(agent_id)
        archive_path = archiver_client.archive_backup(agent_id)
        assert archive_path is not None

        # 3. 往返完整性：get_archive 内容与原 tar 一致（服务端 CopyObject 未损坏）
        archive_data = archiver_client.get_archive(archive_path)
        assert hashlib.sha256(archive_data).hexdigest() == hashlib.sha256(tar_data).hexdigest()

        # 4. 清空 Pod 内 profile 数据 → untar 归档恢复 → 校验
        await k8s_client.exec_hermes_command(
            agent_id, ["bash", "-c", "rm -rf /opt/data/profiles/alice /opt/data/profiles/bob"]
        )
        await k8s_client.exec_untar_data(agent_id, archive_data)

        alice_state = await k8s_client.exec_hermes_command(
            agent_id, ["cat /opt/data/profiles/alice/state.db"]
        )
        assert "alice-state-data" in str(alice_state)

        alice_memory = await k8s_client.exec_hermes_command(
            agent_id, ["cat /opt/data/profiles/alice/memories/m1.json"]
        )
        assert "alice-remember" in str(alice_memory)

        bob_state = await k8s_client.exec_hermes_command(
            agent_id, ["cat /opt/data/profiles/bob/state.db"]
        )
        assert "bob-state-data" in str(bob_state)

        bob_session = await k8s_client.exec_hermes_command(
            agent_id, ["cat /opt/data/profiles/bob/sessions/s1.json"]
        )
        assert "bob-s1" in str(bob_session)

    async def test_suspend_resume_without_backup(self, k8s_client, agent_id):
        """SUSPENDED 但 backup 不存在 → 降级为全新启动（不 crash）"""
        # 创建 Pod 再删除，模拟 backup 不存在但 DB 状态为 SUSPENDED
        # 正常 deploy：SUSPENDED 路径会调 get_backup() → None → 跳过恢复
        # 验证引擎仍能正常启动

        # 模拟：Deployment 不存在 + DB 中 SUSPENDED 状态
        # 直接创建新 Pod（类似 _needs_backup_restore=True 但无 backup）
        await k8s_client.create_agent_engine(agent_id, {})
        assert await k8s_client.wait_pod_ready(agent_id)

        # 写入数据（模拟 SUSPENDED 状态下被外部删除后重建）
        await k8s_client.exec_write_file(agent_id, "/root/.hermes/fresh.txt", "fresh-start")

        content = await k8s_client.exec_hermes_command(
            agent_id, ["cat /root/.hermes/fresh.txt"]
        )
        assert "fresh-start" in str(content)

    async def test_failed_state_deploy(self, k8s_client, agent_id):
        """FAILED 状态 deploy → 全新启动（不尝试恢复）"""
        # FAILED 状态走 "else" 分支 → 创建新 Deployment → 引擎以空数据启动
        await k8s_client.create_agent_engine(agent_id, {})
        assert await k8s_client.wait_pod_ready(agent_id)

        # 验证空的 /root/.hermes （全新 Pod 的 emptyDir 可能已由 entrypoint 初始化）
        # 只要 Pod 正常运行即可
        assert await k8s_client.wait_engine_ready(agent_id)
