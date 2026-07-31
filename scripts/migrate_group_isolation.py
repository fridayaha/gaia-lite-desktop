"""用户组隔离改造 migration —— UserGroup 升级为最小隔离单元。

执行方式:
  cd services/manager
  PYTHONPATH=.:../../pkg python ../../scripts/migrate_group_isolation.py

幂等：每步检查列/约束/表是否已存在，已存在则跳过。本地 DB + 云 DB 均需执行。

内容:
  1. user_groups 加 code / litellm_team_id 列，回填（拼音/英文 + 唯一；litellm_team_id=str(id)），加 NOT NULL + UNIQUE
  2. 确保「默认组」(code=default) 存在，作为存量数据兜底归属
  3. 资源表加 group_id 列（agent_definitions/versions/instances/deployments/channels NOT NULL；resource_pools 可空；agent_profiles 已有列）
  4. 回填 group_id：按 created_by 反查用户所属组 → 兜底默认组；运行时表按 instance.group_id
  5. 联合唯一 (group_id, name) for agent_definitions / agent_instances
  6. 删 access_scope 列 + agent_instance_user_access / agent_instance_group_access 表
"""

import asyncio
import re

from pypinyin import lazy_pinyin, Style
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from pkg.common.config import settings


def _generate_code(name: str) -> str:
    raw = "".join(lazy_pinyin(name, style=Style.NORMAL))
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", raw).lower().strip("-")
    return raw or "group"


async def col_exists(db: AsyncSession, table: str, col: str) -> bool:
    r = await db.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": col},
    )
    return r.scalar() is not None


async def constraint_exists(db: AsyncSession, name: str) -> bool:
    r = await db.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}
    )
    return r.scalar() is not None


async def table_exists(db: AsyncSession, name: str) -> bool:
    r = await db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{name}"})
    return r.scalar() is not None


async def _step1_user_groups(db: AsyncSession) -> None:
    print("[1] user_groups: 加 code / litellm_team_id")
    if not await col_exists(db, "user_groups", "code"):
        await db.execute(text("ALTER TABLE user_groups ADD COLUMN code VARCHAR(64)"))
    if not await col_exists(db, "user_groups", "litellm_team_id"):
        await db.execute(text("ALTER TABLE user_groups ADD COLUMN litellm_team_id VARCHAR(128)"))

    # 回填 code（拼音/英文 + 全局唯一后缀）
    rows = (await db.execute(text("SELECT id, name FROM user_groups WHERE code IS NULL"))).mappings().all()
    for r in rows:
        base = _generate_code(r["name"])
        code = base
        n = 2
        while True:
            cnt = (
                await db.execute(
                    text("SELECT count(*) FROM user_groups WHERE code = :c"), {"c": code}
                )
            ).scalar() or 0
            if cnt == 0:
                break
            code = f"{base}-{n}"
            n += 1
        await db.execute(text("UPDATE user_groups SET code = :c WHERE id = :id"), {"c": code, "id": r["id"]})

    # 回填 litellm_team_id = str(id)
    await db.execute(text("UPDATE user_groups SET litellm_team_id = id::text WHERE litellm_team_id IS NULL"))

    # NOT NULL + UNIQUE
    if not await constraint_exists(db, "uq_user_groups_code"):
        await db.execute(text("ALTER TABLE user_groups ALTER COLUMN code SET NOT NULL"))
        await db.execute(text("ALTER TABLE user_groups ADD CONSTRAINT uq_user_groups_code UNIQUE (code)"))
    print("    done")


async def _ensure_default_group(db: AsyncSession) -> str:
    """确保默认组存在，返回其 id。"""
    row = (
        (await db.execute(text("SELECT id FROM user_groups WHERE code = 'default'"))).mappings().first()
    )
    if row:
        return str(row["id"])
    r = await db.execute(
        text(
            "INSERT INTO user_groups (id, name, code, description, litellm_team_id, created_at, updated_at) "
            "VALUES (gen_random_uuid(), '默认组', 'default', '存量数据兜底归属组', 'default', now(), now()) RETURNING id"
        )
    )
    return str(r.scalar())


async def _step3_add_group_id_columns(db: AsyncSession) -> None:
    print("[3] 资源表加 group_id 列")
    tables_not_null = [
        "agent_definitions",
        "agent_versions",
        "agent_instances",
        "agent_deployments",
        "agent_instance_channels",
    ]
    for t in tables_not_null:
        if not await col_exists(db, t, "group_id"):
            await db.execute(text(f"ALTER TABLE {t} ADD COLUMN group_id UUID"))
    # agent_profiles 已有 group_id（nullable），无需加列
    # resource_pools group_id 可空
    if not await col_exists(db, "resource_pools", "group_id"):
        await db.execute(text("ALTER TABLE resource_pools ADD COLUMN group_id UUID"))
    print("    done")


async def _step4_backfill_group_id(db: AsyncSession, default_group_id: str) -> None:
    print("[4] 回填 group_id")
    # agent_definitions: 按 created_by 反查用户首个组，兜底默认组
    await db.execute(
        text(
            "UPDATE agent_definitions d SET group_id = COALESCE("
            "  (SELECT ugm.group_id FROM user_group_members ugm "
            "   WHERE ugm.user_id = d.created_by ORDER BY ugm.group_id LIMIT 1), "
            "  :default_gid) WHERE group_id IS NULL"
        ),
        {"default_gid": default_group_id},
    )
    # agent_versions: 跟随 definition
    await db.execute(
        text(
            "UPDATE agent_versions v SET group_id = "
            "(SELECT group_id FROM agent_definitions d WHERE d.id = v.definition_id) "
            "WHERE group_id IS NULL"
        )
    )
    # agent_instances: 优先按 definition.group_id，其次 created_by
    await db.execute(
        text(
            "UPDATE agent_instances i SET group_id = COALESCE("
            "  (SELECT group_id FROM agent_definitions d WHERE d.id = i.definition_id), "
            "  (SELECT ugm.group_id FROM user_group_members ugm WHERE ugm.user_id = i.created_by ORDER BY ugm.group_id LIMIT 1), "
            "  :default_gid) WHERE group_id IS NULL"
        ),
        {"default_gid": default_group_id},
    )
    # agent_deployments: 跟随 instance
    await db.execute(
        text(
            "UPDATE agent_deployments dep SET group_id = "
            "(SELECT group_id FROM agent_instances i WHERE i.id = dep.instance_id) "
            "WHERE group_id IS NULL"
        )
    )
    # agent_profiles: 已有 group_id，空的按 instance
    await db.execute(
        text(
            "UPDATE agent_profiles p SET group_id = "
            "(SELECT group_id FROM agent_instances i WHERE i.id = p.instance_id) "
            "WHERE group_id IS NULL"
        )
    )
    # agent_instance_channels: 跟随 instance
    await db.execute(
        text(
            "UPDATE agent_instance_channels c SET group_id = "
            "(SELECT group_id FROM agent_instances i WHERE i.id = c.instance_id) "
            "WHERE group_id IS NULL"
        )
    )
    # resource_pools: 存量全部为平台共享池（group_id NULL），不动
    print("    done")


async def _step5_constraints(db: AsyncSession) -> None:
    print("[5] NOT NULL + FK + 联合唯一")
    not_null_tables = [
        "agent_definitions",
        "agent_versions",
        "agent_instances",
        "agent_deployments",
        "agent_instance_channels",
        "agent_profiles",
    ]
    for t in not_null_tables:
        await db.execute(text(f"ALTER TABLE {t} ALTER COLUMN group_id SET NOT NULL"))

    fk_map = {
        "agent_definitions": "fk_definitions_group",
        "agent_versions": "fk_versions_group",
        "agent_instances": "fk_instances_group",
        "agent_deployments": "fk_deployments_group",
        "agent_instance_channels": "fk_channels_group",
        "agent_profiles": "fk_profiles_group",
        "resource_pools": "fk_resource_pools_group",
    }
    for t, fk_name in fk_map.items():
        if not await constraint_exists(db, fk_name):
            await db.execute(
                text(
                    f"ALTER TABLE {t} ADD CONSTRAINT {fk_name} "
                    f"FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE"
                )
            )

    for t, uq in [
        ("agent_definitions", "uq_definition_group_name"),
        ("agent_instances", "uq_instance_group_name"),
    ]:
        # 旧的全局 name unique 约束（若存在）先删，避免与组内唯一冲突
        await db.execute(text(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_name_key"))
        if not await constraint_exists(db, uq):
            await db.execute(
                text(f"ALTER TABLE {t} ADD CONSTRAINT {uq} UNIQUE (group_id, name)")
            )
    print("    done")


async def _step6_drop_access(db: AsyncSession) -> None:
    print("[6] 删 access_scope 列 + access 关联表")
    if await col_exists(db, "agent_instances", "access_scope"):
        await db.execute(text("ALTER TABLE agent_instances DROP COLUMN access_scope"))
    for t in ("agent_instance_user_access", "agent_instance_group_access"):
        if await table_exists(db, t):
            await db.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))
    print("    done")


async def migrate() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    async with session_factory() as db:
        print("=" * 60)
        print("UnionAgents 用户组隔离改造 migration")
        print("=" * 60)
        await _step1_user_groups(db)
        default_group_id = await _ensure_default_group(db)
        print(f"[2] 默认组 id = {default_group_id}")
        await _step3_add_group_id_columns(db)
        await _step4_backfill_group_id(db, default_group_id)
        await _step5_constraints(db)
        await _step6_drop_access(db)
        await db.commit()
        print("=" * 60)
        print("完成。请在 manager 启动后用 create_all 校验新表结构一致。")
        print("=" * 60)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
