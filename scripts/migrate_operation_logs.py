"""operation_logs 表 migration —— admin 后台写操作审计日志。

执行方式:
  cd services/manager
  PYTHONPATH=.:../../pkg python ../../scripts/migrate_operation_logs.py

幂等：表已存在则跳过。本地 DB + 云 DB 均需执行。

内容:
  1. CREATE TABLE operation_logs（id / group_id / actor_id / action / target_type /
     target_id / status / detail / created_at）
  2. CREATE INDEX ix_oplog_group_created / ix_oplog_actor_created / ix_oplog_target
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings


async def table_exists(db: AsyncSession, table: str) -> bool:
    r = await db.execute(
        text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        ),
        {"t": table},
    )
    return r.scalar() is not None


async def index_exists(db: AsyncSession, name: str) -> bool:
    r = await db.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    )
    return r.scalar() is not None


async def migrate() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    async with session_factory() as db:
        print("=" * 60)
        print("UnionAgents operation_logs 表 migration")
        print("=" * 60)

        if await table_exists(db, "operation_logs"):
            print("[1] operation_logs 表已存在，跳过 CREATE TABLE")
            # 已部署的 v0.8.47 表 actor_id 为 NOT NULL，与 ON DELETE SET NULL 冲突
            # （用户被删时 SET NULL 被 NOT NULL 拒绝 → NotNullViolation）。
            # 这里幂等 DROP NOT NULL；空 actor 表示"用户已删除但日志保留"。
            print("[1.1] 确保 actor_id 可空（已部署 v0.8.47 NOT NULL → NULL）...")
            await db.execute(text(
                "ALTER TABLE operation_logs ALTER COLUMN actor_id DROP NOT NULL"
            ))
            print("    done")
        else:
            print("[1] CREATE TABLE operation_logs ...")
            await db.execute(text("""
                CREATE TABLE operation_logs (
                    id UUID PRIMARY KEY,
                    group_id UUID REFERENCES user_groups(id) ON DELETE CASCADE,
                    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
                    action VARCHAR(128) NOT NULL,
                    target_type VARCHAR(64) NOT NULL,
                    target_id UUID,
                    status VARCHAR(16) NOT NULL DEFAULT 'success',
                    detail JSON,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            print("    done")

        for idx_sql, idx_name in (
            ("CREATE INDEX ix_oplog_group_created ON operation_logs (group_id, created_at)", "ix_oplog_group_created"),
            ("CREATE INDEX ix_oplog_actor_created ON operation_logs (actor_id, created_at)", "ix_oplog_actor_created"),
            ("CREATE INDEX ix_oplog_target ON operation_logs (target_type, target_id)", "ix_oplog_target"),
            ("CREATE INDEX ix_oplog_created_at ON operation_logs (created_at)", "ix_oplog_created_at"),
        ):
            if await index_exists(db, idx_name):
                print(f"[2] {idx_name} 已存在，跳过")
            else:
                print(f"[2] CREATE {idx_name} ...")
                await db.execute(text(idx_sql))
                print("    done")

        await db.commit()
        print("=" * 60)
        print("完成。")
        print("=" * 60)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
