"""classify_drift 纯函数表驱动测试（漂移纠正单一真相源）。

覆盖 running/NotFound/unhealthy × dep 状态 × has_backup × terminating 全组合，
锁定 race 保护：SUSPENDED/ARCHIVED 的 Failed pod 不覆盖、terminating 不标 FAILED。
"""

from types import SimpleNamespace

from app.worker.lifecycle_service import classify_drift
from pkg.common.models import DeploymentStatus as S


def _dep(status):
    return SimpleNamespace(status=status, instance_id="i" * 32)


def _pod(running=False, phase="Running", terminating=False, reason=None):
    return {"running": running, "phase": phase, "terminating": terminating, "reason": reason}


# ── running ──────────────────────────────────────────────


def test_running_recovers_non_running():
    """pod running + dep 非 RUNNING → 恢复 RUNNING（任何陈旧态）。"""
    for st in (S.PENDING, S.FAILED, S.SUSPENDED, S.ARCHIVED, S.DEPLOYING):
        assert classify_drift(_dep(st), _pod(running=True)) == S.RUNNING


def test_running_already_running_no_change():
    """pod running + dep 已 RUNNING → None（调用方刷 last_active，不动状态）。"""
    assert classify_drift(_dep(S.RUNNING), _pod(running=True)) is None


# ── NotFound ─────────────────────────────────────────────


def test_notfound_non_running_no_change():
    """pod NotFound + dep 非 RUNNING → None（FAILED/ARCHIVED 确无 pod，保持）。"""
    for st in (S.FAILED, S.SUSPENDED, S.ARCHIVED, S.PENDING, S.DEPLOYING):
        assert classify_drift(_dep(st), _pod(phase="NotFound")) is None


def test_notfound_running_optimistic_suspended():
    """pod NotFound + dep RUNNING + has_backup=None → SUSPENDED（read 路径乐观，不查备份）。"""
    assert classify_drift(_dep(S.RUNNING), _pod(phase="NotFound"), has_backup=None) == S.SUSPENDED


def test_notfound_running_with_backup_suspended():
    assert classify_drift(_dep(S.RUNNING), _pod(phase="NotFound"), has_backup=True) == S.SUSPENDED


def test_notfound_running_no_backup_failed():
    """pod NotFound + dep RUNNING + 无备份 → FAILED（外部误删，sweep 路径精确判）。"""
    assert classify_drift(_dep(S.RUNNING), _pod(phase="NotFound"), has_backup=False) == S.FAILED


# ── unhealthy（pod 存在但非运行）──────────────────────────


def test_unhealthy_terminating_no_change():
    """terminating pod（suspend/destroy 杀的窗口）不标 FAILED，即使 dep 是 RUNNING。"""
    for st in (S.RUNNING, S.PENDING, S.DEPLOYING):
        assert classify_drift(_dep(st), _pod(phase="Failed", terminating=True)) is None


def test_unhealthy_suspended_archived_failed_not_overwritten():
    """SUSPENDED/ARCHIVED/FAILED 的 Failed pod（scale_to_zero 杀的）不覆盖。"""
    for st in (S.SUSPENDED, S.ARCHIVED, S.FAILED):
        assert classify_drift(_dep(st), _pod(phase="Failed", terminating=False)) is None


def test_unhealthy_running_marks_failed():
    """pod 不健康（非 terminating）+ dep RUNNING → FAILED（race：terminating 已单独测）。"""
    assert classify_drift(_dep(S.RUNNING), _pod(phase="CrashLoopBackOff", terminating=False)) == S.FAILED


def test_unhealthy_pending_dep_marks_failed():
    assert classify_drift(_dep(S.PENDING), _pod(phase="Pending", terminating=False)) == S.FAILED
