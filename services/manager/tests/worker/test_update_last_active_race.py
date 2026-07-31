"""_update_last_active race 修复单测。

覆盖 suspend/destroy 并发场景下,后台 _update_last_active 循环不应把
SUSPENDED 覆盖成 FAILED：terminating 的 Pod 是 scale_to_zero 杀的,属于预期,
不应据此标 FAILED（与 get_agent_status 保护逻辑一致）。

节点 124.243.186.4 的 adf57637 实例曾命中此 race：suspend 已 scale_to_zero
杀 Pod,本循环拿着 RUNNING 快照把状态写成 FAILED,前端显示异常。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.worker.scheduler import _update_last_active

from pkg.common.models import DeploymentStatus


def _make_dep(status=DeploymentStatus.RUNNING, instance_id="inst-race-1"):
    """构造 AgentDeployment mock;engine_url=None → 非 external Dify(Pod 模式)。"""
    dep = MagicMock()
    dep.status = status
    dep.instance_id = instance_id
    dep.last_active_at = None
    dep.error_message = None
    dep.engine_url = None
    return dep


def _deps_result(deps):
    """构造 select(...).where(status==RUNNING).scalars().all() 的 execute 返回。"""
    return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=deps))))


async def _run_with_db(mock_db):
    """patch pkg.common.database.get_db（_update_last_active 内部局部 import）yield mock_db。"""

    async def _fake_get_db():
        yield mock_db

    with patch("pkg.common.database.get_db", _fake_get_db):
        await _update_last_active()


# ── race 修复：terminating 的 Pod 不标 FAILED ──


@pytest.mark.asyncio
async def test_running_terminating_pod_not_marked_failed():
    """RUNNING + terminating pod（phase=Failed, scale_to_zero 杀的）→ 保持 RUNNING,不标 FAILED。

    bug 复现：suspend 已 scale_to_zero 杀 Pod（deletion_timestamp 已设 → terminating=True）,
    本循环不应据此把状态覆盖成 FAILED。
    """
    dep = _make_dep(status=DeploymentStatus.RUNNING)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_deps_result([dep]))
    db.commit = AsyncMock()

    with patch("app.worker.scheduler.k8s_manager") as mk:
        mk.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": True,
                "phase": "Failed",
                "reason": None,
                "pod_name": "pod-1",
            }
        )
        await _run_with_db(db)

    assert dep.status == DeploymentStatus.RUNNING
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_running_non_terminating_failed_pod_marked_failed():
    """RUNNING + 非 terminating Failed pod（真实崩溃,非 suspend 杀的）→ 标 FAILED。

    正常路径不能被破坏：Pod 真的挂了且没在 terminating,仍应标 FAILED 交运维。
    """
    dep = _make_dep(status=DeploymentStatus.RUNNING)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_deps_result([dep]))
    db.commit = AsyncMock()

    with patch("app.worker.scheduler.k8s_manager") as mk:
        mk.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": False,
                "phase": "Failed",
                "reason": "Error",
                "pod_name": "pod-1",
            }
        )
        await _run_with_db(db)

    assert dep.status == DeploymentStatus.FAILED
    assert dep.error_message is not None
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_not_found_with_backup_set_suspended():
    """RUNNING + NotFound + backup_exists → SUSPENDED（正常 suspend 后 Pod 已删,数据在 MinIO）。"""
    dep = _make_dep(status=DeploymentStatus.RUNNING)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_deps_result([dep]))
    db.commit = AsyncMock()

    with (
        patch("app.worker.scheduler.k8s_manager") as mk,
        patch("app.worker.scheduler._load_group_code", AsyncMock(return_value="default")),
        patch("app.worker.scheduler.archiver") as ma,
    ):
        mk.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "phase": "NotFound",
                "reason": None,
            }
        )
        ma.backup_exists = MagicMock(return_value=True)
        await _run_with_db(db)

    assert dep.status == DeploymentStatus.SUSPENDED
    assert dep.error_message is None
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_not_found_without_backup_marks_failed():
    """RUNNING + NotFound + 无备份 → FAILED（外部误删，数据丢失）。

    与 test_not_found_with_backup_set_suspended 对偶：sweep 路径 has_backup=False
    时 reconcile_status 走 NotFound-FAILED 分支，写外部误删 error_message。
    """
    dep = _make_dep(status=DeploymentStatus.RUNNING)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_deps_result([dep]))
    db.commit = AsyncMock()

    with (
        patch("app.worker.scheduler.k8s_manager") as mk,
        patch("app.worker.scheduler._load_group_code", AsyncMock(return_value="default")),
        patch("app.worker.scheduler.archiver") as ma,
    ):
        mk.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "phase": "NotFound",
                "reason": None,
            }
        )
        ma.backup_exists = MagicMock(return_value=False)
        await _run_with_db(db)

    assert dep.status == DeploymentStatus.FAILED
    assert dep.error_message is not None
    assert "removed externally" in dep.error_message
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_suspended_dep_not_overwritten_even_non_terminating():
    """防御：即使 dep.status=SUSPENDED 且 Pod 非 terminating,也不覆盖成 FAILED。

    保护逻辑 `dep.status not in (FAILED, SUSPENDED, ARCHIVED)` 的兜底覆盖
    （_update_last_active 只查 RUNNING,此用例模拟 race 中 status 已被并发
    suspend 改成 SUSPENDED 的极端情况,验证守卫仍生效）。
    """
    dep = _make_dep(status=DeploymentStatus.SUSPENDED)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_deps_result([dep]))
    db.commit = AsyncMock()

    with patch("app.worker.scheduler.k8s_manager") as mk:
        mk.get_pod_status = AsyncMock(
            return_value={
                "running": False,
                "terminating": False,
                "phase": "Failed",
                "reason": None,
                "pod_name": "pod-1",
            }
        )
        await _run_with_db(db)

    assert dep.status == DeploymentStatus.SUSPENDED
    db.commit.assert_not_called()
