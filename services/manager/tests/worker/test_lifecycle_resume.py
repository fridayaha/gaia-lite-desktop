"""resume_agent 触发全链 reconcile 单测 + background loop 注册单测。

resume 后等引擎就绪 + 调 reconcile_skills（兜底 entrypoint curl 失败 + SUSPEND 期间装的技能）。
best-effort：reconcile 失败不阻断 resume。外部 Dify 实例跳过。
用 SimpleNamespace 造假 dep + mock k8s_manager（无需真 DB / k8s）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import DeploymentStatus
from app.worker import background as bg
from app.worker.lifecycle import resume_agent


def _dep(*, engine_url=None):
    return SimpleNamespace(
        instance_id="agent-1",
        status=DeploymentStatus.SUSPENDED,
        scope_type="ALL",
        scope_target_id=None,
        engine_url=engine_url,  # None → 非外部 Dify
    )


def _mock_db(dep):
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=dep))
    )
    return db


class TestResumeReconcile:
    async def test_resume_calls_wait_engine_ready_and_reconcile(self):
        dep = _dep()
        db = _mock_db(dep)
        with (
            patch("app.worker.lifecycle.k8s_manager") as mk,
            patch("app.worker.lifecycle.resume_browser_pods_for_deployment", new=AsyncMock()),
            patch("app.worker.lifecycle.reconcile_skills", new=AsyncMock()) as rec,
        ):
            mk.resume = AsyncMock(return_value=True)
            mk.wait_engine_ready = AsyncMock(return_value=True)
            result = await resume_agent("agent-1", db)
        assert result == {"status": "running"}
        mk.resume.assert_awaited_once()
        mk.wait_engine_ready.assert_awaited_once()
        rec.assert_awaited_once_with("agent-1", db)

    async def test_resume_reconcile_best_effort(self):
        """reconcile_skills 抛异常 → resume 仍返回 running（best-effort 不阻断）。"""
        dep = _dep()
        db = _mock_db(dep)
        with (
            patch("app.worker.lifecycle.k8s_manager") as mk,
            patch("app.worker.lifecycle.resume_browser_pods_for_deployment", new=AsyncMock()),
            patch(
                "app.worker.lifecycle.reconcile_skills",
                new=AsyncMock(side_effect=RuntimeError("reconcile fail")),
            ) as rec,
        ):
            mk.resume = AsyncMock(return_value=True)
            mk.wait_engine_ready = AsyncMock(return_value=True)
            result = await resume_agent("agent-1", db)
        assert result == {"status": "running"}
        rec.assert_awaited_once()

    async def test_resume_external_dify_skips_reconcile(self):
        """外部 Dify 实例（engine_url 非 cluster DNS）→ 不调 reconcile（无 Pod）。"""
        dep = _dep(engine_url="https://external.dify.app")
        db = _mock_db(dep)
        with (
            patch("app.worker.lifecycle.k8s_manager"),
            patch("app.worker.lifecycle.resume_browser_pods_for_deployment", new=AsyncMock()),
            patch("app.worker.lifecycle.reconcile_skills", new=AsyncMock()) as rec,
        ):
            result = await resume_agent("agent-1", db)
        assert result == {"status": "running"}
        rec.assert_not_awaited()


class TestBackgroundLoopRegistration:
    async def test_skill_reconcile_loop_registered(self):
        """start_background 注册 9 个循环（含新增 _skill_reconcile_loop）。"""
        with (
            patch.object(bg.recycle_scheduler, "start"),
            patch.object(bg.recycle_scheduler, "stop", new=AsyncMock()),
            patch.object(bg.metric_sampler, "start"),
            patch.object(bg.metric_sampler, "stop", new=AsyncMock()),
        ):
            await bg.start_background()
            try:
                assert len(bg._bg_tasks) == 9
                # 新增的 _skill_reconcile_loop 在注册列表里
                coro_names = [t.get_coro().cr_code.co_name for t in bg._bg_tasks]
                assert "_skill_reconcile_loop" in coro_names
            finally:
                await bg.stop_background()
        assert bg._bg_tasks == []
