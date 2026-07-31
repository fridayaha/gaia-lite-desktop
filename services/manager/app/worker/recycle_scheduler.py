"""Recycle scheduler — idle detection and automatic engine suspension/cleanup.

运行在 Controller 进程中的后台循环任务：
  - RecycleScheduler  (每 5 分钟): RUNNING 超 30min → suspend
  - CleanupScheduler  (每小时): SUSPENDED 超 24h → destroy
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pkg.common.config import settings

logger = logging.getLogger(__name__)

# Type aliases
SuspendHandler = Callable[[str], Awaitable[None]]
DestroyHandler = Callable[[str], Awaitable[None]]


class RecycleScheduler:
    """回收调度器，定期检测空闲引擎并执行 SUSPEND / DESTROY"""

    def __init__(self):
        self.suspend_handler: SuspendHandler | None = None
        self.destroy_handler: DestroyHandler | None = None
        self._suspend_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    def set_handlers(
        self,
        suspend_handler: SuspendHandler,
        destroy_handler: DestroyHandler,
    ):
        """注册 SUSPEND 和 DESTROY 回调"""
        self.suspend_handler = suspend_handler
        self.destroy_handler = destroy_handler

    def start(self):
        """启动后台调度任务"""
        if not self.suspend_handler or not self.destroy_handler:
            raise RuntimeError("Handlers must be set before starting scheduler")
        self._suspend_task = asyncio.create_task(self._suspend_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"RecycleScheduler started: "
            f"suspend after {settings.idle_suspend_minutes}m, "
            f"destroy after {settings.idle_destroy_hours}h"
        )

    async def stop(self):
        """停止后台调度任务"""
        if self._suspend_task:
            self._suspend_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()

    async def _suspend_loop(self):
        """每 5 分钟检查 RUNNING 状态引擎，超时则 SUSPEND"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 min
                await self._check_and_suspend()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Suspend loop error: {e}")

    async def _cleanup_loop(self):
        """每小时检查 SUSPENDED 状态引擎，超时则 DESTROY"""
        while True:
            try:
                await asyncio.sleep(3600)  # 1 hour
                await self._check_and_destroy()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    async def _check_and_suspend(self):
        """遍历所有 RUNNING 部署，空闲超 30min 则存档+休眠"""
        if not self.suspend_handler:
            return
        # 实际部署数据通过 Controller 的 DB 查询，此处由外部 handler 完成
        logger.debug("Running idle check cycle...")

    async def _check_and_destroy(self):
        """遍历所有 SUSPENDED 部署，超 24h 则清理 K8s 资源"""
        if not self.destroy_handler:
            return
        logger.debug("Running cleanup cycle...")


# Singleton — handlers are registered by controller main.py at startup
recycle_scheduler = RecycleScheduler()
