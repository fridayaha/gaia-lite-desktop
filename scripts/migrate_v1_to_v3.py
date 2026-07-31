"""V1 → V3 数据迁移脚本。

将旧 V1/V2 单层 schema（agents / engine_instances / agent_channels / api_keys）
迁移到 V3 三层模型（agent_definitions + agent_versions + agent_instances /
resource_pools / agent_instance_channels），并把旧 ApiKey 映射为 per-instance
LiteLLM virtual key。

执行方式（dry-run 先行）:
  cd services/manager
  python ../../scripts/migrate_v1_to_v3.py --dry-run
  python ../../scripts/migrate_v1_to_v3.py            # 实际写入
  python ../../scripts/migrate_v1_to_v3.py --group-id <uuid>   # 指定默认组

映射规则（对齐 docs/merge/team-A-后端核心.md A8）:
  Agent           → AgentDefinition + AgentVersion + AgentInstance
                    （保留 agent.id 作为 definition.id 与 instance.definition_id；
                     instance.id 新生成；agent.engine_instance_id → resource_pool_id）
  EngineInstance  → ResourcePool（保留 id，使 agent.engine_instance_id 仍可解析）
  AgentChannel    → AgentInstanceChannel（agent_id → instance_id，V3 instance 按
                    definition_id 回查；scope_type/profile_type 透传）
  ApiKey          → LiteLLM virtual key（按 agent_id 找 instance，generate_key 写入
                    instance.litellm_config；api_keys 表不存在则跳过）

幂等：definition 已存在（id 命中）则跳过该 agent 整条链路；可安全重放。
组归属：V1 agent 若无 group_id 列或值为空，落到 --group-id 指定组（未指定则自动
建「V1迁移默认组」，含 code 与 litellm_team_id）。

⚠️ V1 schema 说明：Repo1 纯 V1 model 已不在本仓（Step 0 替换为 V3），源表列以
Repo2 V2 schema（agents/engine_instances/agent_channels）为准；若实际旧库列名有
出入，按本脚本 SQL 调整。api_keys 表为 V1-only，结构按 {id,key,agent_id,name} 推断。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

from app.models import (  # noqa: E402
    AgentDefinition,
    AgentInstance,
    AgentInstanceChannel,
    AgentStatus,
    AgentVersion,
    DefinitionStatus,
    EngineType,
    ResourcePool,
    UserGroup,
)
from app.services import litellm_client  # noqa: E402

from pkg.common.config import settings  # noqa: E402


async def _table_exists(db: AsyncSession, name: str) -> bool:
    r = await db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{name}"})
    return r.scalar() is not None


async def _column_exists(db: AsyncSession, table: str, col: str) -> bool:
    r = await db.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": col},
    )
    return r.first() is not None


async def _ensure_default_group(db: AsyncSession, group_id: str | None) -> str:
    """返回迁移默认组 id：--group-id 指定则校验存在；否则建「V1迁移默认组」。"""
    if group_id:
        r = await db.execute(text("SELECT id FROM user_groups WHERE id = :id"), {"id": group_id})
        if not r.first():
            raise SystemExit(f"--group-id {group_id} 在 user_groups 中不存在")
        return group_id
    r = await db.execute(text("SELECT id FROM user_groups WHERE name = 'V1迁移默认组'"))
    row = r.first()
    if row:
        return str(row[0])
    g = UserGroup(name="V1迁移默认组", code="v1migrated", litellm_team_id=None)
    db.add(g)
    await db.flush()
    g.litellm_team_id = str(g.id)  # 与 V3 一致：team_id = str(group.id)
    await db.commit()
    print(f"  建默认组 V1迁移默认组: {g.id}")
    return str(g.id)


async def _ensure_default_user(db: AsyncSession) -> str:
    """迁移默认 created_by：取 users 表首个用户（V1 旧库行无 created_by 时兜底）。

    无任何用户时报错（V3 created_by NOT NULL，需有真实 user）。
    """
    r = await db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1"))
    row = r.first()
    if not row:
        raise SystemExit("users 表无任何用户，无法迁移（V3 created_by NOT NULL）")
    return str(row[0])


# ── 迁移步骤 ──────────────────────────────────────────────


async def _migrate_engine_instances(
    db: AsyncSession, default_group_id: str, default_user_id: str, dry_run: bool
) -> dict[str, str]:
    """engine_instances → resource_pools。返回 {old_ei_id: pool_id}（保留 id）。"""
    if not await _table_exists(db, "engine_instances"):
        print("  engine_instances 表不存在，跳过")
        return {}
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, name, description, engine_type, "
                    "min_cpu, max_cpu, min_memory, max_memory, "
                    "min_replicas, max_replicas FROM engine_instances"
                )
            )
        )
        .mappings()
        .all()
    )
    mapping: dict[str, str] = {}
    for r in rows:
        old_id = str(r["id"])
        exists = (
            await db.execute(text("SELECT 1 FROM resource_pools WHERE id = :id"), {"id": old_id})
        ).first()
        if exists:
            mapping[old_id] = old_id
            continue
        pool = ResourcePool(
            id=uuid.UUID(old_id),
            name=r["name"],
            description=r.get("description") or "",
            min_cpu=r.get("min_cpu") or "100m",
            max_cpu=r.get("max_cpu") or "2",
            min_memory=r.get("min_memory") or "256Mi",
            max_memory=r.get("max_memory") or "2Gi",
            min_replicas=r.get("min_replicas") or 1,
            max_replicas=r.get("max_replicas") or 5,
            max_sessions_per_pod=20,
            group_id=None,  # V1 引擎实例为平台共享池
            created_by=uuid.UUID(default_user_id),
        )
        if not dry_run:
            db.add(pool)
            await db.commit()
        mapping[old_id] = old_id
        print(f"  EngineInstance {old_id[:8]} → ResourcePool")
    return mapping


async def _migrate_agents(
    db: AsyncSession,
    default_group_id: str,
    default_user_id: str,
    ei_map: dict[str, str],
    dry_run: bool,
) -> dict[str, str]:
    """agents → definition + version + instance。返回 {old_agent_id: instance_id}。"""
    has_group_col = await _column_exists(db, "agents", "group_id")
    group_sel = "group_id," if has_group_col else "NULL::uuid AS group_id,"
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT id, name, description, avatar_color, status, "
                    f"engine_type, engine_instance_id, model_config, skill_config, "
                    f"memory_config, {group_sel} created_by, created_at, published_at "
                    "FROM agents"
                )
            )
        )
        .mappings()
        .all()
    )
    agent_map: dict[str, str] = {}
    for r in rows:
        old_id = str(r["id"])
        # 幂等：definition 已存在则跳过
        exists = (
            await db.execute(text("SELECT 1 FROM agent_definitions WHERE id = :id"), {"id": old_id})
        ).first()
        if exists:
            # 回查已迁移出的 instance（按 definition_id）
            inst = (
                await db.execute(
                    text("SELECT id FROM agent_instances WHERE definition_id = :did LIMIT 1"),
                    {"did": old_id},
                )
            ).first()
            if inst:
                agent_map[old_id] = str(inst[0])
            continue

        group_id = str(r["group_id"]) if r.get("group_id") else default_group_id
        engine_type = r.get("engine_type") or "HERMES"
        try:
            engine_type = EngineType(engine_type)
        except ValueError:
            engine_type = EngineType.HERMES
        model_config = r.get("model_config") or {}
        if isinstance(model_config, str):
            model_config = json.loads(model_config) if model_config else {}
        # V1 model_config 可能含 litellm 段（per-agent key），迁入 instance.litellm_config
        litellm_cfg = (model_config or {}).get("litellm") or {}

        defn = AgentDefinition(
            id=uuid.UUID(old_id),
            group_id=uuid.UUID(group_id),
            name=r["name"],
            description=r.get("description") or "",
            avatar_color=r.get("avatar_color") or "#6366f1",
            engine_type=engine_type,
            status=DefinitionStatus.PUBLISHED,  # V1 已有 agent 视为已发布定义
            persona_config={},
            model_config=model_config,
            skill_config=r.get("skill_config") or {},
            memory_config=r.get("memory_config") or {},
            created_by=uuid.UUID(str(r["created_by"]))
            if r.get("created_by")
            else uuid.UUID(default_user_id),
            created_at=r.get("created_at") or datetime.now(UTC),
            published_at=r.get("published_at"),
        )
        version = AgentVersion(
            definition_id=uuid.UUID(old_id),
            group_id=uuid.UUID(group_id),
            version_no="1.0.0",
            persona_config={},
            model_config=model_config,
            skill_config=r.get("skill_config") or {},
            memory_config=r.get("memory_config") or {},
            engine_type=engine_type,
            change_log="migrated from V1",
            created_by=defn.created_by,
            created_at=defn.created_at,
        )
        if not dry_run:
            db.add(defn)
            await db.flush()
            db.add(version)
            await db.flush()
            defn.current_version_id = version.id
            await db.commit()

        ei_id = r.get("engine_instance_id")
        pool_id = ei_map.get(str(ei_id)) if ei_id else None
        inst = AgentInstance(
            group_id=uuid.UUID(group_id),
            name=r["name"],
            definition_id=uuid.UUID(old_id),
            version_id=version.id,
            resource_pool_id=uuid.UUID(pool_id) if pool_id else None,
            status=AgentStatus.PUBLISHED,
            litellm_config=litellm_cfg,
            created_by=defn.created_by,
            created_at=defn.created_at,
            published_at=r.get("published_at"),
        )
        if not dry_run:
            # resource_pool_id NOT NULL — 无 pool 时建一个归属组的私有池兜底
            if not pool_id:
                fallback = ResourcePool(
                    name=f"migrated-{old_id[:8]}",
                    group_id=uuid.UUID(group_id),
                    created_by=defn.created_by,
                )
                db.add(fallback)
                await db.flush()
                inst.resource_pool_id = fallback.id
            db.add(inst)
            await db.commit()
        agent_map[old_id] = str(inst.id)
        print(f"  Agent {old_id[:8]} → Definition+Version+Instance {str(inst.id)[:8]}")
    return agent_map


async def _migrate_channels(
    db: AsyncSession,
    agent_map: dict[str, str],
    default_group_id: str,
    dry_run: bool,
) -> int:
    """agent_channels → agent_instance_channels（agent_id → instance_id）。"""
    if not await _table_exists(db, "agent_channels"):
        print("  agent_channels 表不存在，跳过")
        return 0
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, agent_id, channel_type, scope_type, scope_target_id, profile_type, "
                    "config, enabled, callback_url FROM agent_channels"
                )
            )
        )
        .mappings()
        .all()
    )
    n = 0
    for r in rows:
        instance_id = agent_map.get(str(r["agent_id"]))
        if not instance_id:
            continue  # agent 未迁移（可能被幂等跳过），跳过其渠道
        exists = (
            await db.execute(
                text("SELECT 1 FROM agent_instance_channels WHERE id = :id"), {"id": str(r["id"])}
            )
        ).first()
        if exists:
            n += 1
            continue
        # 取 instance.group_id
        g = (
            await db.execute(
                text("SELECT group_id FROM agent_instances WHERE id = :id"), {"id": instance_id}
            )
        ).first()
        ch = AgentInstanceChannel(
            id=uuid.UUID(str(r["id"])),
            instance_id=uuid.UUID(instance_id),
            group_id=g[0] if g else uuid.UUID(default_group_id),
            channel_type=r["channel_type"],
            scope_type=r.get("scope_type") or "ALL",
            scope_target_id=r.get("scope_target_id"),
            profile_type=r.get("profile_type") or "INDEPENDENT",
            config=r.get("config") or {},
            enabled=r.get("enabled") if r.get("enabled") is not None else True,
            callback_url=r.get("callback_url"),
        )
        if not dry_run:
            db.add(ch)
            await db.commit()
        n += 1
        print(f"  AgentChannel {str(r['id'])[:8]} → InstanceChannel (instance {instance_id[:8]})")
    return n


async def _migrate_api_keys(
    db: AsyncSession,
    agent_map: dict[str, str],
    dry_run: bool,
) -> int:
    """api_keys → per-instance LiteLLM virtual key（写 instance.litellm_config）。

    api_keys 为 V1-only 表，结构按 {id, key, agent_id, name} 推断；不存在则跳过。
    需 LiteLLM 可达；不可达时记录跳过，不阻断迁移。
    """
    if not await _table_exists(db, "api_keys"):
        print("  api_keys 表不存在（V2+ 已无），跳过 ApiKey→LiteLLM 映射")
        return 0
    rows = (await db.execute(text("SELECT id, key, agent_id, name FROM api_keys"))).mappings().all()
    n = 0
    for r in rows:
        instance_id = agent_map.get(str(r["agent_id"]))
        if not instance_id:
            continue
        inst = (
            await db.execute(
                text("SELECT id, group_id FROM agent_instances WHERE id = :id"), {"id": instance_id}
            )
        ).first()
        if not inst:
            continue
        # team_id = group.litellm_team_id
        g = (
            await db.execute(
                text("SELECT litellm_team_id FROM user_groups WHERE id = :id"), {"id": str(inst[1])}
            )
        ).first()
        team_id = (g[0] if g else None) or str(inst[1])
        if dry_run:
            n += 1
            continue
        try:
            await litellm_client.ensure_team(team_id, "migrated")
            resp = await litellm_client.generate_key(
                team_id=team_id,
                models=[],
                metadata={
                    "instance_id": instance_id,
                    "group_id": str(inst[1]),
                    "migrated_from_api_key": str(r["id"]),
                },
                key_alias=f"instance:{instance_id[:8]}",
            )
            await db.execute(
                text(
                    "UPDATE agent_instances SET litellm_config = CAST(:cfg AS json) WHERE id = :id"
                ),
                {
                    "cfg": json.dumps(
                        {
                            "team_id": team_id,
                            "key_id": resp.get("token_id"),
                            "key": resp.get("key"),
                            "model_group": None,
                        }
                    ),
                    "id": instance_id,
                },
            )
            await db.commit()
            n += 1
            print(f"  ApiKey {str(r['id'])[:8]} → LiteLLM key (instance {instance_id[:8]})")
        except litellm_client.LitellmError as e:
            print(f"  ⚠️ ApiKey {str(r['id'])[:8]} LiteLLM 签发失败（跳过）: {e.message}")
    return n


async def _self_check(db: AsyncSession, agent_map: dict[str, str], ei_map: dict[str, str]) -> None:
    """数据一致性核对：源/目标计数对齐。"""
    print("\n[自检] 数据一致性核对")
    checks = [
        ("engine_instances", "resource_pools", len(ei_map)),
    ]
    for src, dst, expected in checks:
        if await _table_exists(db, src):
            s = (await db.execute(text(f"SELECT COUNT(*) FROM {src}"))).scalar()
            d = (await db.execute(text(f"SELECT COUNT(*) FROM {dst}"))).scalar()
            status = "✅" if d >= s else "⚠️"
            print(f"  {status} {src}={s} → {dst}={d}（迁移 {expected}）")
    if await _table_exists(db, "agents"):
        s = (await db.execute(text("SELECT COUNT(*) FROM agents"))).scalar()
        d = (await db.execute(text("SELECT COUNT(*) FROM agent_instances"))).scalar()
        status = "✅" if d >= s else "⚠️"
        print(f"  {status} agents={s} → agent_instances={d}（迁移 {len(agent_map)}）")
    if await _table_exists(db, "agent_channels"):
        s = (await db.execute(text("SELECT COUNT(*) FROM agent_channels"))).scalar()
        d = (await db.execute(text("SELECT COUNT(*) FROM agent_instance_channels"))).scalar()
        print(f"  ℹ️ agent_channels={s} → agent_instance_channels={d}")


async def migrate(dry_run: bool, group_id: str | None) -> None:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        print("=" * 60)
        print(f"UnionAgents V1 → V3 数据迁移{'（DRY-RUN）' if dry_run else ''}")
        print("=" * 60)

        if not await _table_exists(db, "agents"):
            print("\n源表 agents 不存在 — 无需迁移（库已是 V3 或空库）。")
            return

        default_user_id = await _ensure_default_user(db)
        default_group_id = await _ensure_default_group(db, group_id)

        print("\n[1/4] EngineInstance → ResourcePool")
        ei_map = await _migrate_engine_instances(db, default_group_id, default_user_id, dry_run)

        print("\n[2/4] Agent → Definition + Version + Instance")
        agent_map = await _migrate_agents(db, default_group_id, default_user_id, ei_map, dry_run)

        print("\n[3/4] AgentChannel → InstanceChannel")
        await _migrate_channels(db, agent_map, default_group_id, dry_run)

        print("\n[4/4] ApiKey → LiteLLM virtual key")
        await _migrate_api_keys(db, agent_map, dry_run)

        await _self_check(db, agent_map, ei_map)
        print("\n" + "=" * 60)
        print("迁移完成" + ("（dry-run，未写入）" if dry_run else ""))
        print("=" * 60)
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="V1 → V3 数据迁移")
    p.add_argument("--dry-run", action="store_true", help="只打印不写入")
    p.add_argument("--group-id", default=None, help="V1 无 group_id 的 agent 落到此组")
    args = p.parse_args()
    asyncio.run(migrate(args.dry_run, args.group_id))


if __name__ == "__main__":
    main()
