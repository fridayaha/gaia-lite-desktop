"""V3 schema 收尾：列重命名 + DROP 老 V2 表。

执行方式:
  cd services/manager
  python ../../scripts/migrate_to_v3_data.py

历史：本脚本原用于 V2→V3 一次性数据迁移（老 Agent/EngineInstance → 三层模型）。
该迁移已在 dev/云 DB 完成，老 model 与读取逻辑随 B3 任务8/9 下线。本脚本现仅做
两件幂等 schema 收尾工作：

  1. _rename_v3_columns（B3 任务6）：agent_deployments/agent_profiles/resource_metric_samples
     列重命名 agent_id→instance_id、engine_instance_id→resource_pool_id + FK 改指
     agent_instances/resource_pools。
  2. _drop_legacy_tables（B3 任务9）：DROP 老 V2 表
     agent_channels / agent_user_access / agent_group_access / agents /
     engine_instances / agent_sessions（IF EXISTS，幂等）。

保留：agent_deployments / agent_profiles / resource_metric_samples（已改列名，V3 在用）。

注意：DROP 老表后，V2→V3 数据迁移不再可重放；dev 数据恢复改用 V3 API/UI 重建
（测试清 V3 表后不再有「从老表恢复」路径）。
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from pkg.common.config import settings


async def _rename_v3_columns(db: AsyncSession):
    """B3 任务6：agent_deployments/agent_profiles/resource_metric_samples 列重命名
    agent_id→instance_id、engine_instance_id→resource_pool_id，FK 改指 agent_instances/resource_pools。

    幂等：每张表检查新列名是否已存在，已存在则跳过整张表。
    """

    async def col_exists(table: str, col: str) -> bool:
        r = await db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": col},
        )
        return r.scalar() is not None

    # ── agent_deployments ──
    if not await col_exists("agent_deployments", "instance_id"):
        print("[B3] agent_deployments: agent_id→instance_id, engine_instance_id→resource_pool_id, FK 改指 ...")
        await db.execute(text("ALTER TABLE agent_deployments DROP CONSTRAINT IF EXISTS uq_agent_deployment_scope"))
        await db.execute(text("ALTER TABLE agent_deployments DROP CONSTRAINT IF EXISTS agent_deployments_agent_id_fkey"))
        await db.execute(text("ALTER TABLE agent_deployments DROP CONSTRAINT IF EXISTS agent_deployments_engine_instance_id_fkey"))
        await db.execute(text("ALTER TABLE agent_deployments RENAME COLUMN agent_id TO instance_id"))
        await db.execute(text("ALTER TABLE agent_deployments RENAME COLUMN engine_instance_id TO resource_pool_id"))
        await db.execute(text(
            "ALTER TABLE agent_deployments ADD CONSTRAINT agent_deployments_instance_id_fkey "
            "FOREIGN KEY (instance_id) REFERENCES agent_instances(id) ON DELETE CASCADE"
        ))
        await db.execute(text(
            "ALTER TABLE agent_deployments ADD CONSTRAINT agent_deployments_resource_pool_id_fkey "
            "FOREIGN KEY (resource_pool_id) REFERENCES resource_pools(id)"
        ))
        await db.execute(text(
            "ALTER TABLE agent_deployments ADD CONSTRAINT uq_agent_deployment_scope "
            "UNIQUE (instance_id, scope_type, scope_target_id)"
        ))
        print("  done")
    else:
        print("[B3] agent_deployments 已是 V3 列名，跳过")

    # ── agent_profiles ──
    if not await col_exists("agent_profiles", "instance_id"):
        print("[B3] agent_profiles: 列重命名 + FK 改指 ...")
        await db.execute(text("ALTER TABLE agent_profiles DROP CONSTRAINT IF EXISTS uq_user_profile_per_instance"))
        await db.execute(text("ALTER TABLE agent_profiles DROP CONSTRAINT IF EXISTS agent_profiles_agent_id_fkey"))
        await db.execute(text("ALTER TABLE agent_profiles DROP CONSTRAINT IF EXISTS agent_profiles_engine_instance_id_fkey"))
        # deployment_id FK（→agent_deployments）保留不动
        await db.execute(text("ALTER TABLE agent_profiles RENAME COLUMN agent_id TO instance_id"))
        await db.execute(text("ALTER TABLE agent_profiles RENAME COLUMN engine_instance_id TO resource_pool_id"))
        await db.execute(text(
            "ALTER TABLE agent_profiles ADD CONSTRAINT agent_profiles_instance_id_fkey "
            "FOREIGN KEY (instance_id) REFERENCES agent_instances(id) ON DELETE CASCADE"
        ))
        await db.execute(text(
            "ALTER TABLE agent_profiles ADD CONSTRAINT agent_profiles_resource_pool_id_fkey "
            "FOREIGN KEY (resource_pool_id) REFERENCES resource_pools(id)"
        ))
        await db.execute(text(
            "ALTER TABLE agent_profiles ADD CONSTRAINT uq_user_profile_per_instance "
            "UNIQUE (instance_id, resource_pool_id, user_id)"
        ))
        print("  done")
    else:
        print("[B3] agent_profiles 已是 V3 列名，跳过")

    # ── resource_metric_samples（无 FK，只改列名 + 索引）──
    if not await col_exists("resource_metric_samples", "instance_id"):
        print("[B3] resource_metric_samples: 列重命名 + 索引重建 ...")
        await db.execute(text("DROP INDEX IF EXISTS ix_rms_agent_ts"))
        await db.execute(text("DROP INDEX IF EXISTS ix_rms_instance_ts"))
        await db.execute(text("ALTER TABLE resource_metric_samples RENAME COLUMN agent_id TO instance_id"))
        await db.execute(text("ALTER TABLE resource_metric_samples RENAME COLUMN engine_instance_id TO resource_pool_id"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_rms_instance_ts ON resource_metric_samples (resource_pool_id, ts)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_rms_agent_ts ON resource_metric_samples (instance_id, ts)"))
        print("  done")
    else:
        print("[B3] resource_metric_samples 已是 V3 列名，跳过")

    await db.commit()


# B3 任务9：DROP 老 V2 表（V3 三层模型已取代，老表无活跃代码依赖）
LEGACY_TABLES = [
    "agent_channels",        # → agent_instance_channels
    "agent_user_access",     # → agent_instance_user_access
    "agent_group_access",    # → agent_instance_group_access
    "agent_sessions",        # 聊天会话由引擎自管，不入 manager DB
    "agents",                # → agent_definitions + agent_instances
    "engine_instances",      # → resource_pools
]


async def _drop_legacy_tables(db: AsyncSession):
    """B3 任务9：幂等 DROP 老 V2 表。已不存在则跳过。"""
    for t in LEGACY_TABLES:
        existed = (
            await db.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{t}"}
            )
        ).scalar()
        if not existed:
            print(f"[B3] {t}: 不存在，跳过")
            continue
        await db.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))
        print(f"[B3] {t}: DROP")
    await db.commit()


async def migrate():
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as db:
        print("=" * 60)
        print("UnionAgents V3 schema 收尾（列重命名 + DROP 老 V2 表）")
        print("=" * 60)

        print("\n[1] 列重命名 ...")
        await _rename_v3_columns(db)

        print("\n[2] DROP 老 V2 表 ...")
        await _drop_legacy_tables(db)

        print("\n" + "=" * 60)
        print("完成。保留 agent_deployments/agent_profiles/resource_metric_samples（V3 在用）。")
        print("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
