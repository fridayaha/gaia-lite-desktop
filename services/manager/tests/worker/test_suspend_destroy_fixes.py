"""suspend/destroy/finalizer/deploy 修复单测。

覆盖：
- get_agent_status: SUSPENDED/terminating 不标 FAILED；DEPLOYING + Ready 恢复 RUNNING
- _reconcile_finalizers: 容器 terminated 跳过 backup + 移除 finalizer
- _do_destroy: 先 remove_finalizer 再 delete_agent_engine
- is_pod_container_running / is_pod_ready
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ENGINE_URL = "http://engine-hermes-x.unionagents.svc.cluster.local:8642"

# ── get_agent_status: SUSPENDED/terminating 不标 FAILED ──


@pytest.mark.asyncio
async def test_suspended_failed_pod_not_marked_failed():
    """SUSPENDED + Failed pod → 不标 FAILED（保持 SUSPENDED）。"""
    from app.worker.router import get_agent_status

    from pkg.common.models import DeploymentStatus

    dep = SimpleNamespace(
        instance_id="a" * 32,
        status=DeploymentStatus.SUSPENDED,
        engine_url="http://engine-hermes-x.unionagents.svc.cluster.local:8642",
        last_active_at=None,
        error_message=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep)))
    db.commit = AsyncMock()

    with patch("app.worker.lifecycle.k8s_manager") as mock_k8s:
        mock_k8s.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": False,
                "phase": "Failed",
                "reason": None,
                "pod_name": "pod-1",
                "pod_ip": None,
                "start_time": None,
                "node_name": None,
            }
        )
        resp = await get_agent_status("a" * 32, db)

    assert resp.status == DeploymentStatus.SUSPENDED


@pytest.mark.asyncio
async def test_running_terminating_pod_not_marked_failed():
    """RUNNING + terminating pod → 不标 FAILED（跳过）。"""
    from app.worker.router import get_agent_status

    from pkg.common.models import DeploymentStatus

    dep = SimpleNamespace(
        instance_id="a" * 32,
        status=DeploymentStatus.RUNNING,
        engine_url="http://engine-hermes-x.unionagents.svc.cluster.local:8642",
        last_active_at=None,
        error_message=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep)))
    db.commit = AsyncMock()

    with patch("app.worker.lifecycle.k8s_manager") as mock_k8s:
        mock_k8s.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": True,
                "phase": "Failed",
                "reason": None,
                "pod_name": "pod-1",
                "pod_ip": None,
                "start_time": None,
                "node_name": None,
            }
        )
        resp = await get_agent_status("a" * 32, db)

    assert resp.status == DeploymentStatus.RUNNING


# ── get_agent_status: DEPLOYING + Ready 恢复 RUNNING ──


@pytest.mark.asyncio
async def test_deploying_pod_ready_recovers_running():
    """DEPLOYING + pod Ready → 恢复 RUNNING。"""
    from app.worker.router import get_agent_status

    from pkg.common.models import DeploymentStatus

    dep = SimpleNamespace(
        instance_id="a" * 32,
        status=DeploymentStatus.DEPLOYING,
        engine_url="http://engine-hermes-x.unionagents.svc.cluster.local:8642",
        last_active_at=None,
        error_message=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep)))
    db.commit = AsyncMock()

    with patch("app.worker.lifecycle.k8s_manager") as mock_k8s:
        mock_k8s.get_pod_status = AsyncMock(
            return_value={
                "running": True,
                "terminating": False,
                "phase": "Running",
                "reason": None,
                "pod_name": "pod-1",
                "pod_ip": None,
                "start_time": None,
                "node_name": None,
            }
        )
        mock_k8s.is_pod_ready = AsyncMock(return_value=True)
        resp = await get_agent_status("a" * 32, db)

    assert resp.status == DeploymentStatus.RUNNING


@pytest.mark.asyncio
async def test_deploying_pod_not_ready_stays_deploying():
    """DEPLOYING + pod not Ready → 保持 DEPLOYING。"""
    from app.worker.router import get_agent_status

    from pkg.common.models import DeploymentStatus

    dep = SimpleNamespace(
        instance_id="a" * 32,
        status=DeploymentStatus.DEPLOYING,
        engine_url="http://engine-hermes-x.unionagents.svc.cluster.local:8642",
        last_active_at=None,
        error_message=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep)))
    db.commit = AsyncMock()

    with patch("app.worker.lifecycle.k8s_manager") as mock_k8s:
        mock_k8s.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": False,
                "phase": "Pending",
                "reason": None,
                "pod_name": "pod-1",
                "pod_ip": None,
                "start_time": None,
                "node_name": None,
            }
        )
        mock_k8s.is_pod_ready = AsyncMock(return_value=False)
        resp = await get_agent_status("a" * 32, db)

    assert resp.status == DeploymentStatus.DEPLOYING


# ── _reconcile_finalizers: 容器 terminated 跳过 backup ──


@pytest.mark.asyncio
async def test_reconcile_container_dead_skips_backup():
    """容器 terminated → 跳过 backup，直接 remove finalizer。"""
    from app.worker import scheduler as _r

    pods = [
        {
            "agent_id": "a" * 32,
            "pod_name": "pod-1",
            "terminating_since": datetime.now(UTC),
        }
    ]

    with (
        patch.object(_r.k8s_manager, "list_terminating_engine_pods", AsyncMock(return_value=pods)),
        patch.object(_r.k8s_manager, "is_pod_container_running", AsyncMock(return_value=False)),
        patch.object(_r.k8s_manager, "remove_finalizer", AsyncMock()) as mock_remove,
        patch.object(_r, "_backup_pod_on_destroy", AsyncMock()) as mock_backup,
    ):
        await _r._reconcile_finalizers()

    mock_backup.assert_not_called()
    mock_remove.assert_called_once_with("pod-1")


@pytest.mark.asyncio
async def test_reconcile_container_alive_backups_then_removes():
    """容器活着 → backup 成功 → remove finalizer。"""
    from app.worker import scheduler as _r

    pods = [
        {
            "agent_id": "a" * 32,
            "pod_name": "pod-1",
            "terminating_since": datetime.now(UTC),
        }
    ]

    with (
        patch.object(_r.k8s_manager, "list_terminating_engine_pods", AsyncMock(return_value=pods)),
        patch.object(_r.k8s_manager, "is_pod_container_running", AsyncMock(return_value=True)),
        patch.object(_r.k8s_manager, "remove_finalizer", AsyncMock()) as mock_remove,
        patch.object(_r, "_backup_pod_on_destroy", AsyncMock()) as mock_backup,
    ):
        await _r._reconcile_finalizers()

    mock_backup.assert_called_once()
    mock_remove.assert_called_once_with("pod-1")


# ── _do_destroy: remove_finalizer before delete ──


@pytest.mark.asyncio
async def test_destroy_removes_finalizer_before_delete():
    """_do_destroy: remove_finalizer 在 delete_agent_engine 之前调用。"""
    from app.worker import lifecycle_service as _r

    from pkg.common.models import DeploymentStatus

    dep = SimpleNamespace(
        instance_id="a" * 32,
        status=DeploymentStatus.SUSPENDED,
        engine_url=None,
        archived_at=None,
        archive_path=None,
        internal_port_map={"profiles": {}, "next_port": 8644},
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep)))
    db.commit = AsyncMock()

    call_order = []

    with patch("app.worker.lifecycle_service.k8s_manager") as mock_k8s:
        mock_k8s.remove_finalizer_from_agent_pods = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("remove_finalizer")
        )
        mock_k8s.delete_agent_engine = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("delete")
        )
        with patch("app.worker.lifecycle_service.archiver") as mock_archiver:
            mock_archiver.backup_exists = MagicMock(return_value=True)
            mock_archiver.archive_backup = MagicMock(return_value="groups/default/path")
            mock_archiver.delete_engine_config = AsyncMock()
            with (
                patch("app.worker.lifecycle_service._load_group_code", AsyncMock(return_value="default")),
                patch("app.worker.lifecycle_service._acquire_agent_lock", AsyncMock()),
                patch(
                    "app.worker.lifecycle_service._is_external_dify_deployment", MagicMock(return_value=False)
                ),
            ):
                await _r.destroy("a" * 32, db)

    assert "remove_finalizer" in call_order
    assert "delete" in call_order
    assert call_order.index("remove_finalizer") < call_order.index("delete")


# ── is_pod_container_running / is_pod_ready ──


async def test_is_pod_container_running_true():
    """容器 running → True。"""
    from app.worker.k8s_manager import k8s_manager

    pod = MagicMock()
    cs = MagicMock()
    cs.name = "engine"
    cs.state = MagicMock(running=MagicMock(), terminated=None)
    pod.status.container_statuses = [cs]

    with patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod):
        result = await k8s_manager.is_pod_container_running("pod-1")

    assert result is True


async def test_is_pod_container_running_false_terminated():
    """容器 terminated → False。"""
    from app.worker.k8s_manager import k8s_manager

    pod = MagicMock()
    cs = MagicMock()
    cs.name = "engine"
    cs.state = MagicMock(running=None, terminated=MagicMock())
    pod.status.container_statuses = [cs]

    with patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod):
        result = await k8s_manager.is_pod_container_running("pod-1")

    assert result is False


async def test_is_pod_ready_true():
    """Ready=True → True。"""
    from app.worker.k8s_manager import k8s_manager

    pod = MagicMock()
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True"
    pod.status.conditions = [cond]

    with patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod):
        result = await k8s_manager.is_pod_ready("pod-1")

    assert result is True


async def test_is_pod_ready_false():
    """Ready=False → False。"""
    from app.worker.k8s_manager import k8s_manager

    pod = MagicMock()
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "False"
    pod.status.conditions = [cond]

    with patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod):
        result = await k8s_manager.is_pod_ready("pod-1")

    assert result is False


# ── remove_finalizer: patch body 用 null 不用空 list ──


@pytest.mark.asyncio
async def test_remove_finalizer_uses_null_not_empty_list():
    """remove_finalizer 用 null 删除 finalizers 字段，不用空 list。

    patch_namespaced_pod 默认 strategic merge patch，空 finalizers list 不移除
    （suspend/destroy 后 Pod 卡 Terminating）；null 删字段才放行。
    """
    from app.worker.k8s_manager import DATA_BACKUP_FINALIZER, k8s_manager

    pod = MagicMock()
    pod.metadata.finalizers = [DATA_BACKUP_FINALIZER]
    with (
        patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod),
        patch.object(k8s_manager.core_v1, "patch_namespaced_pod") as mock_patch,
    ):
        await k8s_manager.remove_finalizer("pod-1")

    body = mock_patch.call_args.args[2]
    assert body == {"metadata": {"finalizers": None}}, (
        "应用 null 删除 finalizers 字段，不是空 list（strategic merge 空 list 不移除）"
    )


@pytest.mark.asyncio
async def test_remove_finalizer_keeps_other_finalizers():
    """有多个 finalizer 时，移除 data-backup 保留其他（非空 list replace）。"""
    from app.worker.k8s_manager import DATA_BACKUP_FINALIZER, k8s_manager

    pod = MagicMock()
    pod.metadata.finalizers = [DATA_BACKUP_FINALIZER, "other.example/finalizer"]
    with (
        patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod),
        patch.object(k8s_manager.core_v1, "patch_namespaced_pod") as mock_patch,
    ):
        await k8s_manager.remove_finalizer("pod-1")

    body = mock_patch.call_args.args[2]
    assert body == {"metadata": {"finalizers": ["other.example/finalizer"]}}


@pytest.mark.asyncio
async def test_remove_finalizer_no_finalizer_skips_patch():
    """Pod 没有 data-backup finalizer 时不 patch。"""
    from app.worker.k8s_manager import k8s_manager

    pod = MagicMock()
    pod.metadata.finalizers = []
    with (
        patch.object(k8s_manager.core_v1, "read_namespaced_pod", return_value=pod),
        patch.object(k8s_manager.core_v1, "patch_namespaced_pod") as mock_patch,
    ):
        await k8s_manager.remove_finalizer("pod-1")

    mock_patch.assert_not_called()
