"""Database migration: add node_name column to agent_deployments.

用于引擎启动性能优化 — 记录 Pod 上次运行的节点名，
SUSPEND→RESUME 时通过 preferredDuringScheduling 优先调度回原节点，
利用已有镜像缓存避免重新拉取。

Run: python3 scripts/migrate.py
"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pkg.common.config import settings


async def migrate():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        # 添加 node_name 列（用于节点亲和性优化）
        await conn.execute(text(
            "ALTER TABLE agent_deployments ADD COLUMN IF NOT EXISTS node_name VARCHAR(256)"
        ))
        print("✓ Migration complete: agent_deployments.node_name added")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
