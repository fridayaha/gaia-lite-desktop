"""V1 → V3 迁移脚本测试（real DB）。

建 V1/V2 源表（agents/engine_instances/agent_channels/api_keys）+ 样本数据，
跑 migrate_v1_to_v3.migrate，验证 V3 写入字段、id 保留、幂等，并覆盖
ApiKey → LiteLLM virtual key 路径（mock litellm_client）。
"""

import json
import uuid

import pytest
import pytest_asyncio
from app.services import litellm_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

# 迁移脚本以 services/manager 为 cwd 运行（sys.path.insert '.'），测试直接 import
from scripts import migrate_v1_to_v3 as mig

V1_DDL = [
    """CREATE TABLE IF NOT EXISTS engine_instances (
        id UUID PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        description TEXT DEFAULT '',
        engine_type VARCHAR(16) NOT NULL,
        min_cpu VARCHAR(16) DEFAULT '100m',
        max_cpu VARCHAR(16) DEFAULT '2',
        min_memory VARCHAR(16) DEFAULT '256Mi',
        max_memory VARCHAR(16) DEFAULT '2Gi',
        min_replicas INT DEFAULT 1,
        max_replicas INT DEFAULT 5
    )""",
    """CREATE TABLE IF NOT EXISTS agents (
        id UUID PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        description TEXT DEFAULT '',
        avatar_color VARCHAR(7) DEFAULT '#6366f1',
        status VARCHAR(16) DEFAULT 'PUBLISHED',
        engine_type VARCHAR(16) DEFAULT 'HERMES',
        engine_instance_id UUID,
        model_config JSON DEFAULT '{}',
        skill_config JSON DEFAULT '{}',
        memory_config JSON DEFAULT '{}',
        created_by UUID,
        created_at TIMESTAMPTZ DEFAULT now(),
        published_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS agent_channels (
        id UUID PRIMARY KEY,
        agent_id UUID NOT NULL,
        channel_type VARCHAR(32) NOT NULL,
        scope_type VARCHAR(16) DEFAULT 'ALL',
        scope_target_id UUID,
        profile_type VARCHAR(16) DEFAULT 'INDEPENDENT',
        config JSON DEFAULT '{}',
        enabled BOOLEAN DEFAULT TRUE,
        callback_url VARCHAR(512)
    )""",
    """CREATE TABLE IF NOT EXISTS api_keys (
        id UUID PRIMARY KEY,
        key VARCHAR(256) NOT NULL,
        agent_id UUID NOT NULL,
        name VARCHAR(128)
    )""",
]

V1_TABLES = ["api_keys", "agent_channels", "agents", "engine_instances"]
V3_TABLES = [
    "agent_instance_channels",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "user_group_members",
    "user_groups",
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    # V3 表已由 manager app 启动建好；这里补建 V1 源表（先 DROP 保证干净，避免上次失败残留）
    for t in V1_TABLES:
        await session.execute(text(f"DROP TABLE IF EXISTS {t}"))
    for ddl in V1_DDL:
        await session.execute(text(ddl))
    # 插入测试 user（迁移脚本要求 users 表至少有一个 user 作为 created_by）
    await session.execute(
        text(
            "INSERT INTO users (id, username, hashed_password) "
            "VALUES (:id, 'v1test-user', 'x') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": uuid.UUID("00000000-0000-0000-0000-000000000001")},
    )
    await session.commit()
    yield session
    # 清理：V1 源表 + 本测试产生的 V3 行 + 默认组（按表 targeted，避免无 name 列报错）
    await session.execute(
        text("UPDATE agent_definitions SET current_version_id = NULL WHERE name LIKE 'v1test%'")
    )
    await session.execute(
        text(
            "DELETE FROM agent_instance_channels WHERE instance_id IN "
            "(SELECT id FROM agent_instances WHERE name LIKE 'v1test%')"
        )
    )
    await session.execute(text("DELETE FROM agent_instances WHERE name LIKE 'v1test%'"))
    await session.execute(
        text(
            "DELETE FROM agent_versions WHERE definition_id IN "
            "(SELECT id FROM agent_definitions WHERE name LIKE 'v1test%')"
        )
    )
    await session.execute(text("DELETE FROM agent_definitions WHERE name LIKE 'v1test%'"))
    await session.execute(
        text("DELETE FROM resource_pools WHERE name LIKE 'v1test%' OR name LIKE 'migrated-%'")
    )
    await session.execute(text("DELETE FROM user_groups WHERE name = 'V1迁移默认组'"))
    for t in V1_TABLES:
        await session.execute(text(f"DROP TABLE IF EXISTS {t}"))
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest.fixture
def mock_litellm(monkeypatch):
    async def _ensure_team(*a, **kw):
        return {}

    async def _generate_key(*, team_id, models=None, metadata=None, key_alias=None, **kw):
        return {"key": "sk-migrated", "token_id": "tid-migrated"}

    monkeypatch.setattr(litellm_client, "ensure_team", _ensure_team)
    monkeypatch.setattr(litellm_client, "generate_key", _generate_key)
    # 迁移脚本内部用 settings.database_url 创建 engine，测试时需指向测试库
    monkeypatch.setattr(settings, "database_url", settings.test_database_url)


async def test_migrate_v1_to_v3_mapping_and_idempotent(db, mock_litellm, monkeypatch):
    """A8 验收：V1→V3 映射正确（id 保留/configs/version/instance/channel/key）+ 幂等。"""
    # 让迁移脚本复用本测试的 engine（settings.test_database_url 即测试库）
    ei_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    ch_id = uuid.uuid4()
    key_id = uuid.uuid4()

    await db.execute(
        text(
            "INSERT INTO engine_instances "
            "(id, name, engine_type, min_cpu, max_cpu, min_memory, max_memory) "
            "VALUES (:id, 'v1test-pool', 'HERMES', '200m', '4', '512Mi', '4Gi')"
        ),
        {"id": ei_id},
    )
    model_cfg = {"litellm": {"model_group": "gpt-4o", "key": "sk-old"}, "system_prompt": "hi"}
    await db.execute(
        text(
            "INSERT INTO agents "
            "(id, name, description, engine_type, engine_instance_id, model_config, status) "
            "VALUES (:id, 'v1test-agent', 'd', 'HERMES', :eid, CAST(:cfg AS json), 'PUBLISHED')"
        ),
        {"id": agent_id, "eid": ei_id, "cfg": json.dumps(model_cfg)},
    )
    await db.execute(
        text(
            "INSERT INTO agent_channels (id, agent_id, channel_type, scope_type, config) "
            "VALUES (:id, :aid, 'feishu', 'ALL', CAST('{}' AS json))"
        ),
        {"id": ch_id, "aid": agent_id},
    )
    await db.execute(
        text(
            "INSERT INTO api_keys (id, key, agent_id, name) VALUES (:id, 'sk-old-key', :aid, 'old')"
        ),
        {"id": key_id, "aid": agent_id},
    )
    await db.commit()

    # 第 1 次迁移
    await mig.migrate(dry_run=False, group_id=None)

    # 核对 ResourcePool（id 保留）
    pool = (
        await db.execute(
            text("SELECT id, name, min_cpu, max_cpu FROM resource_pools WHERE id = :id"),
            {"id": ei_id},
        )
    ).first()
    assert pool is not None
    assert pool[1] == "v1test-pool"
    assert pool[2] == "200m" and pool[3] == "4"

    # 核对 Definition（id 保留 + configs 拷贝）
    defn = (
        await db.execute(
            text(
                "SELECT id, name, status, model_config, current_version_id "
                "FROM agent_definitions WHERE id = :id"
            ),
            {"id": agent_id},
        )
    ).first()
    assert defn is not None
    assert defn[1] == "v1test-agent"
    assert defn[2] == "PUBLISHED"
    assert defn[3]["litellm"]["model_group"] == "gpt-4o"
    assert defn[4] is not None  # current_version_id 已置

    # 核对 Version
    ver = (
        await db.execute(
            text(
                "SELECT version_no, definition_id, model_config "
                "FROM agent_versions WHERE definition_id = :id"
            ),
            {"id": agent_id},
        )
    ).first()
    assert ver[0] == "1.0.0"
    assert ver[1] == agent_id
    assert ver[2]["litellm"]["model_group"] == "gpt-4o"

    # 核对 Instance（resource_pool_id=ei_id，definition_id=agent_id，
    # litellm_config 已被 ApiKey 覆盖）
    inst = (
        await db.execute(
            text(
                "SELECT definition_id, resource_pool_id, status, litellm_config "
                "FROM agent_instances WHERE definition_id = :id"
            ),
            {"id": agent_id},
        )
    ).first()
    assert inst is not None
    assert inst[0] == agent_id
    assert inst[1] == ei_id
    assert inst[2] == "PUBLISHED"
    assert inst[3]["key"] == "sk-migrated"  # ApiKey→LiteLLM 覆盖了 V1 model_config.litellm.key

    # 核对 Channel（instance_id 回查到 instance）
    ch = (
        await db.execute(
            text(
                "SELECT instance_id, channel_type, scope_type "
                "FROM agent_instance_channels WHERE id = :id"
            ),
            {"id": ch_id},
        )
    ).first()
    assert ch is not None
    assert ch[1] == "feishu"
    assert ch[2] == "ALL"

    # 幂等：第 2 次迁移不新增行
    before_def = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_definitions WHERE id = :id"), {"id": agent_id}
        )
    ).scalar()
    before_inst = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_instances WHERE definition_id = :id"), {"id": agent_id}
        )
    ).scalar()
    before_ver = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_versions WHERE definition_id = :id"), {"id": agent_id}
        )
    ).scalar()
    await mig.migrate(dry_run=False, group_id=None)
    after_def = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_definitions WHERE id = :id"), {"id": agent_id}
        )
    ).scalar()
    after_inst = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_instances WHERE definition_id = :id"), {"id": agent_id}
        )
    ).scalar()
    after_ver = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_versions WHERE definition_id = :id"), {"id": agent_id}
        )
    ).scalar()
    assert (before_def, before_inst, before_ver) == (after_def, after_inst, after_ver) == (1, 1, 1)


async def test_migrate_dry_run_does_not_write(db, mock_litellm):
    """dry-run 不写入 V3 表。"""
    agent_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO agents (id, name, engine_type, model_config) "
            "VALUES (:id, 'v1test-dry', 'HERMES', CAST('{}' AS json))"
        ),
        {"id": agent_id},
    )
    await db.commit()
    await mig.migrate(dry_run=True, group_id=None)
    n = (
        await db.execute(
            text("SELECT COUNT(*) FROM agent_definitions WHERE id = :id"), {"id": agent_id}
        )
    ).scalar()
    assert n == 0


async def test_migrate_skips_when_no_v1_tables(db, mock_litellm):
    """库已是 V3（无 agents 表）时直接跳过，不报错。"""
    await db.execute(text("DROP TABLE IF EXISTS api_keys"))
    await db.execute(text("DROP TABLE IF EXISTS agent_channels"))
    await db.execute(text("DROP TABLE IF EXISTS agents"))
    await db.execute(text("DROP TABLE IF EXISTS engine_instances"))
    await db.commit()
    # 不应抛异常
    await mig.migrate(dry_run=False, group_id=None)
