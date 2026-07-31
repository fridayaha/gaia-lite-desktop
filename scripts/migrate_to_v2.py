"""V2 数据迁移: 现有单用户架构 → 多租户 Profile 架构

执行方式:
  cd services/manager
  python -m scripts.migrate_to_v2

迁移内容:
  1. 为每个 Agent 创建默认 EngineInstance（如果尚未创建）
  2. 迁移 Agent.config → model_config
  3. 更新 AgentChannel 添加默认 scope 字段
  4. 更新 AgentDeployment 添加默认 scope 字段
"""

import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# 添加项目路径
sys.path.insert(0, ".")

from pkg.common.config import settings
from app.models import (
    Base, Agent, AgentChannel, AgentDeployment, AgentSession,
    EngineInstance, EngineType, AgentProfile,
)


async def migrate():
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as db:
        print("=" * 60)
        print("UnionAgents V2 数据迁移")
        print("=" * 60)

        # ── Step 1: 确保表存在 ──
        print("\n[1/6] 确保 V2 表结构...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("  完成")

        # ── Step 2: 创建默认 EngineInstance ──
        print("\n[2/6] 创建内置 EngineInstance...")
        result = await db.execute(
            select(EngineInstance).where(EngineInstance.name == "Hermes-标准版")
        )
        default_ei = result.scalar_one_or_none()

        if not default_ei:
            # 找到第一个 admin 用户
            from app.models import User
            admin_result = await db.execute(select(User).limit(1))
            admin = admin_result.scalar_one_or_none()
            admin_id = admin.id if admin else None

            if not admin_id:
                print("  警告: 没有找到 admin 用户，使用虚拟 ID")
                admin_id = UUID("00000000-0000-0000-0000-000000000001")

            default_ei = EngineInstance(
                name="Hermes-标准版",
                description="默认 Hermes 引擎实例（自动创建）",
                engine_type=EngineType.HERMES,
                is_builtin=True,
                created_by=admin_id,
            )
            db.add(default_ei)
            await db.commit()
            await db.refresh(default_ei)
            print(f"  创建默认 EngineInstance: {default_ei.id}")
        else:
            print(f"  已存在: {default_ei.id}")

        # ── Step 3: 迁移 Agent 表 ──
        print("\n[3/6] 迁移 agents 表...")
        result = await db.execute(select(Agent))
        agents = list(result.scalars().all())
        migrated = 0

        for agent in agents:
            changed = False

            # 迁移 config → model_config（保留兼容）
            if agent.model_config is None or agent.model_config == {}:
                old_config = {}
                try:
                    if isinstance(agent.config, str):
                        old_config = json.loads(agent.config) if agent.config else {}
                    elif agent.config:
                        old_config = agent.config
                except Exception:
                    pass

                # 构建 model_config
                model_config = {}
                if "model_providers" in old_config:
                    model_config["model_providers"] = old_config["model_providers"]
                if "system_prompt" in old_config:
                    model_config["system_prompt"] = old_config["system_prompt"]
                if "engine" in old_config:
                    engine_cfg = old_config["engine"]
                    if not model_config.get("model_providers") and "PROVIDER_NAME" in engine_cfg:
                        model_config["model_providers"] = [{
                            "type": engine_cfg.get("PROVIDER_NAME", "anthropic"),
                            "model_name": engine_cfg.get("MODEL_NAME", ""),
                        }]

                agent.model_config = model_config
                changed = True

            # 绑定到默认 EngineInstance
            if not agent.engine_instance_id:
                agent.engine_instance_id = default_ei.id
                changed = True

            if changed:
                migrated += 1

        await db.commit()
        print(f"  迁移 {migrated}/{len(agents)} 个 Agent")

        # ── Step 4: 迁移 agent_channels 表 ──
        print("\n[4/6] 迁移 agent_channels 表...")
        result = await db.execute(select(AgentChannel))
        channels = list(result.scalars().all())

        for ch in channels:
            if not ch.scope_type:
                await db.execute(
                    text("UPDATE agent_channels SET scope_type = 'ALL', profile_type = 'INDEPENDENT' WHERE id = :id"),
                    {"id": ch.id},
                )
        await db.commit()
        print(f"  更新 {len(channels)} 个 Channel")

        # ── Step 5: 迁移 agent_deployments 表 ──
        print("\n[5/6] 迁移 agent_deployments 表...")
        result = await db.execute(select(AgentDeployment))
        deps = list(result.scalars().all())

        for dep in deps:
            updates = {}
            if not dep.scope_type:
                updates["scope_type"] = "ALL"
            if not dep.engine_instance_id:
                updates["engine_instance_id"] = default_ei.id

            if updates:
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                await db.execute(
                    text(f"UPDATE agent_deployments SET {set_clause} WHERE id = :id"),
                    {"id": dep.id, **updates},
                )
        await db.commit()
        print(f"  更新 {len(deps)} 个 Deployment")

        # ── Step 6: 校验 ──
        print("\n[6/6] 校验迁移结果...")
        result = await db.execute(select(Agent).where(Agent.engine_instance_id.is_(None)))
        missing = result.scalars().all()
        if missing:
            print(f"  ⚠️  有 {len(missing)} 个 Agent 尚未绑定 EngineInstance")
        else:
            print("  ✅ 所有 Agent 已绑定 EngineInstance")

        result = await db.execute(
            text("SELECT COUNT(*) FROM engine_instances")
        )
        ei_count = result.scalar()
        print(f"  EngineInstance 数量: {ei_count}")

        result = await db.execute(
            text("SELECT COUNT(*) FROM agent_profiles")
        )
        ap_count = result.scalar()
        print(f"  AgentProfile 数量: {ap_count}")

        print("\n" + "=" * 60)
        print("迁移完成!")
        print("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
