"""归档数据安全加固单测（F1~F8 + C5a/C5b）。

覆盖：
  F1 _do_suspend 备份失败不置 SUSPENDED / 连续失败置 FAILED
  F2 _do_destroy 无归档拒删 PVC
  F5 ARCHIVED 恢复失败置 FAILED
  F6 exec_tar_data 退出码非 0 raise
  F3 配置联动守卫（skip_backup + reclaim 拒启动）
  F4 save_daily 上传 size 校验
  C5a _reconcile_finalizers 销毁前备份编排 + 超时兜底
  C5b _check_and_daily_backup 触发守卫

DB 写入逻辑用 mock_db_session + 断言 dep 字段值（与现有 test_do_suspend_passes_group_code
一致），不止断言 commit。
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker import router as router_mod
from app.worker import lifecycle_service, scheduler as scheduler_mod
from app.worker.router import _do_destroy, _do_suspend
from app.worker.scheduler import _check_and_daily_backup, _reconcile_finalizers
from pkg.common.config import Settings
from pkg.common.models import DeploymentStatus


# ── helpers ────────────────────────────────────────────────


def _mapping_row(mapping):
    m = MagicMock()
    m.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mapping)))
    return m


def _dep_row(dep):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=dep))


def _make_dep(status=DeploymentStatus.RUNNING, **attrs):
    dep = MagicMock()
    dep.status = status
    dep.instance_id = attrs.get("instance_id", "inst-safety-1")
    dep.backup_at = attrs.get("backup_at", None)
    dep.archive_path = attrs.get("archive_path", None)
    dep.error_message = attrs.get("error_message", None)
    dep.archived_at = None
    dep.internal_port_map = attrs.get("internal_port_map", {"profiles": {}, "next_port": 8644})
    # engine_url=None → _is_external_dify_deployment 返回 False（Pod 模式，走正常 suspend/destroy）
    dep.engine_url = None
    return dep


@pytest.fixture(autouse=True)
def _reset_suspend_fail_count():
    """每个测试前清空 SUSPEND 失败计数全局，避免互相影响。"""
    lifecycle_service._suspend_fail_count.clear()
    scheduler_mod._daily_backup_last_run_date = None
    yield
    lifecycle_service._suspend_fail_count.clear()


# ═══════════════════════════════════════════════════════════
# F1: _do_suspend 备份失败处理
# ═══════════════════════════════════════════════════════════


class TestSuspendBackupFailure:
    AGENT_ID = "inst-suspend-fail"

    def _setup(self, mock_db_session, mock_k8s, dep):
        # execute 序列：_acquire_agent_lock → dify 预检查(select dep) → _load_group_code →（FAILED 分支）select dep
        mock_db_session.execute.side_effect = [
            MagicMock(),  # pg_advisory_xact_lock
            _dep_row(dep),  # dify 预检查（engine_url=None → 非外部 Dify，继续）
            _mapping_row({"group_code": "yanfa"}),
            _dep_row(dep),  # 仅 FAILED 分支会用到
        ]
        mock_k8s.pvc_exists.return_value = False
        mock_k8s.exec_tar_data = AsyncMock(side_effect=RuntimeError("tar boom"))

    async def test_suspend_backup_failure_keeps_running(self, mock_db_session, mock_k8s):
        """备份失败（attempt 1）→ 不 scale_to_zero、不置 SUSPENDED、不写 backup_at，re-raise 待重试"""
        dep = _make_dep(status=DeploymentStatus.RUNNING, instance_id=self.AGENT_ID)
        self._setup(mock_db_session, mock_k8s, dep)

        with patch("app.worker.lifecycle_service.settings.pvc_skip_backup_on_suspend", False):
            with patch("app.worker.lifecycle_service.archiver.save_daily") as mock_save:
                with pytest.raises(RuntimeError, match="tar boom"):
                    await _do_suspend(self.AGENT_ID, mock_db_session)
                mock_save.assert_not_called()  # tar 失败前未到 save

        # 状态保持 RUNNING，未休眠
        assert dep.status == DeploymentStatus.RUNNING
        assert dep.backup_at is None
        mock_k8s.scale_to_zero.assert_not_called()
        mock_k8s.remove_finalizer_from_agent_pods.assert_not_called()
        # 失败计数已记
        assert lifecycle_service._suspend_fail_count.get(self.AGENT_ID) == 1

    async def test_suspend_backup_persistent_failure_marks_failed(self, mock_db_session, mock_k8s):
        """连续失败达上限 → 置 FAILED + error_message，不再重试"""
        dep = _make_dep(status=DeploymentStatus.RUNNING, instance_id=self.AGENT_ID)
        self._setup(mock_db_session, mock_k8s, dep)
        # 预置已失败 2 次，本次为第 3 次
        lifecycle_service._suspend_fail_count[self.AGENT_ID] = 2

        with patch("app.worker.lifecycle_service.settings.pvc_skip_backup_on_suspend", False):
            with patch("app.worker.lifecycle_service.archiver.save_daily"):
                await _do_suspend(self.AGENT_ID, mock_db_session)

        assert dep.status == DeploymentStatus.FAILED
        assert dep.error_message and "tar boom" in dep.error_message
        mock_k8s.scale_to_zero.assert_not_called()
        mock_db_session.commit.assert_awaited()
        # FAILED 后清零计数（避免再次被后台循环重试）
        assert self.AGENT_ID not in lifecycle_service._suspend_fail_count


# ═══════════════════════════════════════════════════════════
# F2: _do_destroy 无归档拒删 PVC
# ═══════════════════════════════════════════════════════════


class TestDestroyRefuseWithoutArchive:
    AGENT_ID = "inst-destroy-refuse"

    def _setup(self, mock_db_session, mock_k8s, dep):
        # execute 序列：_acquire_agent_lock → dify 预检查(select dep) → _load_group_code
        mock_db_session.execute.side_effect = [
            MagicMock(),  # pg_advisory_xact_lock
            _dep_row(dep),  # dify 预检查（engine_url=None → 非外部 Dify，继续）
            _mapping_row({"group_code": "yanfa"}),
        ]
        mock_k8s.delete_agent_engine = AsyncMock()

    async def test_destroy_refuses_when_no_archive_and_reclaim(
        self, mock_db_session, mock_k8s, mock_archiver
    ):
        """reclaim=True 且无归档 → raise，不删 PVC，保持 SUSPENDED"""
        dep = _make_dep(status=DeploymentStatus.SUSPENDED, instance_id=self.AGENT_ID)
        self._setup(mock_db_session, mock_k8s, dep)
        mock_archiver.backup_exists.return_value = False  # 无备份

        with patch("app.worker.lifecycle_service.settings.pvc_reclaim_on_destroy", True):
            with patch("app.worker.lifecycle_service.archiver.delete_engine_config"):
                with pytest.raises(RuntimeError, match="no valid archive"):
                    await _do_destroy(self.AGENT_ID, mock_db_session)

        mock_k8s.delete_agent_engine.assert_not_called()  # PVC/Deployment 未删
        assert dep.status == DeploymentStatus.SUSPENDED  # 状态未变
        mock_archiver.archive_backup.assert_not_called()

    async def test_destroy_proceeds_when_reclaim_false_no_archive(
        self, mock_db_session, mock_k8s, mock_archiver
    ):
        """reclaim=False（PVC 保留）时无归档也可放行：删 Deployment 但不丢 PVC 上数据"""
        dep = _make_dep(status=DeploymentStatus.SUSPENDED, instance_id=self.AGENT_ID)
        mock_k8s.delete_agent_engine = AsyncMock()
        mock_archiver.backup_exists.return_value = False
        # execute 序列：lock → dify 预检查(select dep) → group_code → select(dep) → DELETE agent_profiles
        mock_db_session.execute.side_effect = [
            MagicMock(),  # pg_advisory_xact_lock
            _dep_row(dep),  # dify 预检查
            _mapping_row({"group_code": "yanfa"}),
            _dep_row(dep),
            MagicMock(),  # DELETE agent_profiles
        ]

        with patch("app.worker.lifecycle_service.settings.pvc_reclaim_on_destroy", False):
            with patch("app.worker.lifecycle_service.archiver.delete_engine_config"):
                await _do_destroy(self.AGENT_ID, mock_db_session)

        mock_k8s.delete_agent_engine.assert_called_once()  # 删 Deployment/Service
        assert dep.status == DeploymentStatus.ARCHIVED


# ═══════════════════════════════════════════════════════════
# F5: ARCHIVED 恢复失败置 FAILED
# ═══════════════════════════════════════════════════════════


class TestArchivedRestoreFailure:
    AGENT_ID = "inst-archived-restore"

    def _setup(self, mock_db_session, mock_k8s, dep):
        config_data = {"model_providers": [{"type": "openrouter", "api_key": "sk-or-test"}]}
        inst_row = MagicMock()
        inst_row.mappings = MagicMock(
            return_value=MagicMock(first=MagicMock(return_value={"model_config": json_dumps(config_data)}))
        )
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
        ]
        mock_k8s.create_agent_engine = AsyncMock(return_value="engine-hermes-x")
        mock_k8s.wait_pod_ready = AsyncMock(return_value=True)
        mock_k8s.wait_engine_ready = AsyncMock(return_value=True)
        mock_k8s.get_pod_status = AsyncMock(
            return_value={"running": True, "phase": "Running", "pod_name": "pod-x", "node_name": "n1"}
        )
        mock_k8s.get_service_url = AsyncMock(return_value="http://engine-x:8642")

    async def test_archived_restore_download_failure_marks_failed(
        self, mock_k8s, mock_archiver, mock_db_session
    ):
        """ARCHIVED 恢复时归档下载失败 → 置 FAILED，不留空数据 Pod 服务"""
        from app.worker.router import _deploy_body

        dep = _make_dep(status=DeploymentStatus.DEPLOYING, instance_id=self.AGENT_ID)
        dep.archive_path = "s3://unionagents-archives/groups/yanfa/archives/x/ts.tar.gz"
        dep.node_name = None
        dep.deployed_at = None
        self._setup(mock_db_session, mock_k8s, dep)
        mock_archiver.get_archive.side_effect = RuntimeError("obs down")

        await _deploy_body(mock_db_session, self.AGENT_ID, "ALL", None, prev_status=DeploymentStatus.ARCHIVED)

        assert dep.status == DeploymentStatus.FAILED
        assert dep.error_message and "archive download failed" in dep.error_message
        mock_k8s.scale_to_zero.assert_called_once()  # 缩容空数据 Pod


def json_dumps(x):
    import json as _j

    return _j.dumps(x)


# ═══════════════════════════════════════════════════════════
# F6: exec_tar_data 退出码严格
# ═══════════════════════════════════════════════════════════


class TestTarExitCodeStrict:
    async def test_tar_fatal_exit_raises(self):
        """tar EXIT=2（致命）→ raise，不返回残缺 tar 当备份"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(
            k8s_manager, "_ws_exec_sync", return_value=(b"EXIT=2\n", 0, b"")
        ):
            with pytest.raises(RuntimeError, match="tar backup fatal"):
                await k8s_manager.exec_tar_data_by_pod("pod-x", agent_id_tag="inst-x")

    async def test_tar_no_exit_marker_raises(self):
        """exec 异常无 EXIT 标记 → raise"""
        from app.worker.k8s_manager import k8s_manager

        with patch.object(
            k8s_manager, "_ws_exec_sync", return_value=(b"some garbage\n", 0, b"")
        ):
            with pytest.raises(RuntimeError, match="no EXIT marker"):
                await k8s_manager.exec_tar_data_by_pod("pod-x", agent_id_tag="inst-x")

    async def test_tar_warning_exit_proceeds(self):
        """tar EXIT=1（非致命警告，如 file changed）→ 不 raise，返回 tar 流"""
        from app.worker.k8s_manager import k8s_manager

        # 3 次 _ws_exec_sync 调用：tar(EXIT=1) / cat(返回 tar bytes) / rm(忽略)
        with patch.object(
            k8s_manager,
            "_ws_exec_sync",
            side_effect=[(b"EXIT=1\n", 0, b""), (b"tar-bytes", 0, b""), (b"", 0, b"")],
        ):
            data = await k8s_manager.exec_tar_data_by_pod("pod-x", agent_id_tag="inst-x")
            assert data == b"tar-bytes"


# ═══════════════════════════════════════════════════════════
# F3: 配置联动守卫
# ═══════════════════════════════════════════════════════════


class TestConfigGuard:
    def test_skip_backup_with_reclaim_rejected(self):
        """skip_backup=True + reclaim=True → 拒绝构造（拒绝启动）"""
        with pytest.raises(ValueError, match="配置冲突"):
            Settings(pvc_skip_backup_on_suspend=True, pvc_reclaim_on_destroy=True)

    def test_skip_backup_with_retain_allowed(self):
        """skip_backup=True + reclaim=False → 允许（PVC 保留，DR-only）"""
        s = Settings(pvc_skip_backup_on_suspend=True, pvc_reclaim_on_destroy=False)
        assert s.pvc_skip_backup_on_suspend is True
        assert s.pvc_reclaim_on_destroy is False

    def test_default_config_allowed(self):
        """默认配置（skip=False, reclaim=True）→ 允许"""
        s = Settings()
        assert s.pvc_reclaim_on_destroy is True


# ═══════════════════════════════════════════════════════════
# F4: save_daily 上传 size 校验
# ═══════════════════════════════════════════════════════════


class TestSaveDailyIntegrity:
    def test_size_mismatch_raises(self):
        """上传后 stat size 与写入不符 → raise（防截断静默成功）"""
        from app.worker.minio_archiver import archiver

        old_client = archiver.client
        archiver.client = MagicMock()
        archiver._bucket_ensured = True
        try:
            stat = MagicMock()
            stat.size = 0  # 与 len(b"tar-data!") != 0
            archiver.client.stat_object.return_value = stat
            with pytest.raises(RuntimeError, match="upload size mismatch"):
                archiver.save_daily("inst-x", b"tar-data!", group_code="yanfa", date_str="20260629")
        finally:
            archiver.client = old_client

    def test_size_match_ok(self):
        """上传后 stat size 一致 → 不 raise"""
        from app.worker.minio_archiver import archiver

        old_client = archiver.client
        archiver.client = MagicMock()
        archiver._bucket_ensured = True
        try:
            data = b"tar-data!"
            stat = MagicMock()
            stat.size = len(data)
            archiver.client.stat_object.return_value = stat
            key = archiver.save_daily("inst-x", data, group_code="yanfa", date_str="20260629")
            assert key.endswith("daily-20260629.tar.gz")
        finally:
            archiver.client = old_client


# ═══════════════════════════════════════════════════════════
# C5a: _reconcile_finalizers 编排
# ═══════════════════════════════════════════════════════════


class TestReconcileFinalizers:
    async def test_backup_then_remove_finalizer(self, mock_k8s):
        """Terminating Pod → 备份成功 → 移除 finalizer 放行"""
        mock_k8s.list_terminating_engine_pods = AsyncMock(
            return_value=[
                {"agent_id": "inst-a", "pod_name": "pod-a", "terminating_since": None}
            ]
        )
        mock_k8s.exec_tar_data_by_pod = AsyncMock(return_value=b"tar")
        with patch("app.worker.scheduler._backup_pod_on_destroy", new=AsyncMock()):
            with patch("app.worker.scheduler.archiver"):
                await _reconcile_finalizers()
        mock_k8s.remove_finalizer.assert_called_once_with("pod-a")

    async def test_backup_failure_not_timed_out_keeps_finalizer(self, mock_k8s):
        """备份失败且未超时 → 不移除 finalizer（待下轮重试）"""
        mock_k8s.list_terminating_engine_pods = AsyncMock(
            return_value=[
                {
                    "agent_id": "inst-a",
                    "pod_name": "pod-a",
                    "terminating_since": datetime.now(UTC),  # 刚开始 terminating
                }
            ]
        )
        with patch(
            "app.worker.scheduler._backup_pod_on_destroy",
            new=AsyncMock(side_effect=RuntimeError("exec fail")),
        ):
            with patch("app.worker.scheduler.archiver"):
                await _reconcile_finalizers()
        mock_k8s.remove_finalizer.assert_not_called()

    async def test_backup_failure_timed_out_force_remove(self, mock_k8s):
        """备份失败且超时 → 强制移除 finalizer 放行 + 不卡死 Pod"""
        mock_k8s.list_terminating_engine_pods = AsyncMock(
            return_value=[
                {
                    "agent_id": "inst-a",
                    "pod_name": "pod-a",
                    # 远早于 timeout（默认 5min）
                    "terminating_since": datetime.now(UTC) - timedelta(minutes=30),
                }
            ]
        )
        with patch(
            "app.worker.scheduler._backup_pod_on_destroy",
            new=AsyncMock(side_effect=RuntimeError("exec fail")),
        ):
            with patch("app.worker.scheduler.archiver"):
                await _reconcile_finalizers()
        mock_k8s.remove_finalizer.assert_called_once_with("pod-a")


# ═══════════════════════════════════════════════════════════
# C5b: _check_and_daily_backup 触发守卫
# ═══════════════════════════════════════════════════════════


class TestDailyBackupGuard:
    async def test_hour_mismatch_skips(self):
        """非触发小时 → 不备份"""
        now_hour = datetime.now(UTC).hour
        other_hour = (now_hour + 1) % 24
        with patch("app.worker.scheduler.settings.daily_backup_hour", other_hour):
            with patch("app.worker.scheduler.archiver.save_daily") as mock_save:
                with patch("app.worker.scheduler.k8s_manager.exec_tar_data") as mock_tar:
                    await _check_and_daily_backup()
        mock_save.assert_not_called()
        mock_tar.assert_not_called()

    async def test_already_run_today_skips(self):
        """今日已执行 → 跳过（即便小时匹配）"""
        now_hour = datetime.now(UTC).hour
        today = datetime.now(UTC).strftime("%Y%m%d")
        scheduler_mod._daily_backup_last_run_date = today
        with patch("app.worker.scheduler.settings.daily_backup_hour", now_hour):
            with patch("app.worker.scheduler.archiver.save_daily") as mock_save:
                await _check_and_daily_backup()
        mock_save.assert_not_called()
