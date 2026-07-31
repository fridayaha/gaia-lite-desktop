"""Worker 后台任务组 —— 挂 manager lifespan，故障隔离。

并入 manager 后，原 controller lifespan 启动的后台循环统一在此管理：
  - recycle_scheduler（每 5min 检测空闲 → SUSPEND；每 1h 检测超期 → DESTROY）
  - metric_sampler（每 60s 采样 Pod CPU/内存，7d 保留）
  - 内联循环：
      _check_and_suspend / _check_and_destroy / _update_last_active
      _reconcile_finalizers（销毁前数据备份 finalizer，~10s 轮询）
      _check_and_daily_backup（每日 RUNNING 引擎全量备份）
      _check_and_daily_cleanup（清理 30 天前 daily 备份）

故障隔离：每个循环单轮异常自吞（logger 记录后继续），单个循环挂掉不拖垮主 API。
recycle_scheduler / metric_sampler 各自 start()/stop() 管理内部 task。
"""

import asyncio
import logging

from pkg.common.config import settings

from . import scheduler
from .lifecycle_service import destroy as _do_destroy, suspend as _do_suspend
from .metric_sampler import metric_sampler
from .recycle_scheduler import recycle_scheduler

logger = logging.getLogger(__name__)

# manager lifespan 持有的内联循环 task（recycle/metric_sampler 自管内部 task）
_bg_tasks: list[asyncio.Task] = []


async def _suspend_loop():
    """每 5 分钟检查 RUNNING 状态引擎，空闲超 idle_suspend_minutes 则存档+休眠。"""
    while True:
        try:
            await asyncio.sleep(300)
            await scheduler._check_and_suspend()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker suspend_loop error: %s", e)


async def _cleanup_loop():
    """每小时检查 SUSPENDED 状态引擎，超 idle_destroy_hours 则清理 K8s 资源。"""
    while True:
        try:
            await asyncio.sleep(3600)
            await scheduler._check_and_destroy()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker cleanup_loop error: %s", e)


async def _update_active_loop():
    """每 60 秒更新 RUNNING 部署的 last_active_at 并巡检 profile 一致性。"""
    while True:
        try:
            await asyncio.sleep(60)
            await scheduler._update_last_active()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker update_active_loop error: %s", e)


async def _finalizer_reconcile_loop():
    """~10s 轮询：为 Terminating 的引擎 Pod 销毁前备份数据再放行（外部销毁感知）。"""
    interval = settings.finalizer_reconcile_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            await scheduler._reconcile_finalizers()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker finalizer_reconcile_loop error: %s", e)


async def _daily_backup_loop():
    """每小时检查：到 daily_backup_hour 则对所有 RUNNING 引擎做当日全量备份。"""
    while True:
        try:
            await asyncio.sleep(3600)
            await scheduler._check_and_daily_backup()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker daily_backup_loop error: %s", e)


async def _daily_cleanup_loop():
    """每小时清理 daily_backup_retain_days 天前的 daily 备份。"""
    while True:
        try:
            await asyncio.sleep(3600)
            await scheduler._check_and_daily_cleanup()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker daily_cleanup_loop error: %s", e)


async def _metrics_refresh_loop():
    """每 60 秒刷新 Prometheus 自定义 gauge（agent_count / deployment_count / dify_health）。"""
    from app.metrics import probe_dify_health, refresh_metrics

    from pkg.common.database import async_session

    while True:
        try:
            await asyncio.sleep(60)
            async with async_session() as db:
                await refresh_metrics(db)
                await probe_dify_health(db)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker metrics_refresh_loop error: %s", e)


async def _alert_check_loop():
    """每 120 秒轮询 5 大类告警规则触发 + 发通知。

    告警规则从 alert_rules 表读（tracing/resource/service_health/usage/call_analysis 5 大类），
    去重走 alert_events 表（同 rule+trace 1h 内不重复）。
    Langfuse/Prometheus/LiteLLM 未配置/不可达时 evaluator 静默返回空列表，不抛异常。
    周期 60s→120s 节流：5 个 evaluator 每次都跑会增加外部依赖调用，120s 平衡实时性与负载。
    """
    from app.services.alert_service import check_and_notify

    from pkg.common.database import async_session

    while True:
        try:
            await asyncio.sleep(120)
            async with async_session() as db:
                n = await check_and_notify(db)
                if n:
                    logger.info("[alert] notified %s events", n)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker alert_check_loop error: %s", e)


async def _skill_reconcile_loop():
    """每 30min 全链技能一致性对账 + 自愈（reconcile_skills）。

    事件驱动（entrypoint pod 启动 / resume）之外的兜底：长跑 Pod 不重启也能自愈 drift。
    30min 周期控规模负载（1000 Pod ≈ 33 pod/min，与已弃的 5min 备份轮询同约束）。
    每.Pod 单次 exec 批量探活，drift 时才重放。per-agent 异常自吞。
    """
    from app.models import AgentDeployment, DeploymentStatus
    from app.worker.config_skills import reconcile_skills
    from pkg.common.database import async_session
    from sqlalchemy import select

    while True:
        try:
            await asyncio.sleep(1800)
            async with async_session() as db:
                deps = (
                    (
                        await db.execute(
                            select(AgentDeployment).where(
                                AgentDeployment.status == DeploymentStatus.RUNNING
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for dep in deps:
                    try:
                        await reconcile_skills(str(dep.instance_id), db)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "reconcile %s failed", str(dep.instance_id)[:8], exc_info=True
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.error("worker skill_reconcile_loop error: %s", e)


async def start_background() -> None:
    """manager lifespan 启动时调用：注册回调 + 启调度器 + 起内联循环。"""
    # recycle_scheduler 要求先 set_handlers 再 start
    recycle_scheduler.set_handlers(
        suspend_handler=_do_suspend,
        destroy_handler=_do_destroy,
    )
    recycle_scheduler.start()
    metric_sampler.start()
    _bg_tasks.extend(
        [
            asyncio.create_task(_suspend_loop()),
            asyncio.create_task(_cleanup_loop()),
            asyncio.create_task(_update_active_loop()),
            asyncio.create_task(_finalizer_reconcile_loop()),
            asyncio.create_task(_daily_backup_loop()),
            asyncio.create_task(_daily_cleanup_loop()),
            asyncio.create_task(_metrics_refresh_loop()),
            asyncio.create_task(_alert_check_loop()),
            asyncio.create_task(_skill_reconcile_loop()),
        ]
    )
    logger.info(
        "worker background started: recycle_scheduler + metric_sampler + 9 loops"
    )


async def stop_background() -> None:
    """manager lifespan 关闭时调用：取消内联循环 + 停调度器。"""
    for t in _bg_tasks:
        t.cancel()
    for t in _bg_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("worker background task exit error: %s", e)
    _bg_tasks.clear()
    await recycle_scheduler.stop()
    await metric_sampler.stop()
    logger.info("worker background stopped.")
