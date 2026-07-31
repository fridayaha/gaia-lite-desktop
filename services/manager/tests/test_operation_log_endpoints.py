"""OperationLog 写入集成测试 — 验证 P0 埋点真的写入了 operation_logs 表。

按 CLAUDE.md 反模式要求：DB 写入逻辑不能用 mock commit 绕过，必须验证实际字段值。
用真 DB（test_database_url）跑 service 函数，断言 OperationLog 行写入 + 字段正确。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    AgentDefinition,
    AgentInstance,
    AgentStatus,
    DefinitionStatus,
    OperationLog,
    ResourcePool,
    User,
)
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionUpdate,
    AgentInstanceCreate,
    PublishVersionRequest,
)
from app.services import definition_service, instance_service
from pkg.common.config import settings


V3_TABLES = [
    "operation_logs",
    "agent_instance_channels",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "agent_deployments",
    "user_group_members",
    "user_groups",
]


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 V3 表 + operation_logs + user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"optlog_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

    # 清理：operation_logs 必须先于 user（actor_id FK ON DELETE SET NULL，但 group_id CASCADE）
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in V3_TABLES:
        if t == "operation_logs":
            continue
        await session.execute(text(f"DELETE FROM {t}"))
    # 清理测试创建的 role / 额外 user（P1 埋点测试会建 r_<hex> 角色、u_<hex> 用户）
    await session.execute(text("DELETE FROM user_roles"))
    await session.execute(text("DELETE FROM roles WHERE name LIKE 'r\\_%' OR name LIKE 'role\\_%'"))
    await session.execute(text("DELETE FROM users WHERE email LIKE '%@example.com' AND id != :uid"),
                          {"uid": user.id})
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest.fixture
def mock_litellm(monkeypatch):
    from app.services import litellm_client

    async def _ensure_team(*a, **kw):
        return {}

    async def _generate_key(*a, **kw):
        return {"key": "sk-test-key", "token_id": "tid-123"}

    async def _delete_key(*a, **kw):
        return {}

    async def _list_keys(team_id=None):
        return []

    monkeypatch.setattr(litellm_client, "ensure_team", _ensure_team)
    monkeypatch.setattr(litellm_client, "generate_key", _generate_key)
    monkeypatch.setattr(litellm_client, "delete_key", _delete_key)
    monkeypatch.setattr(litellm_client, "list_keys", _list_keys)


@pytest_asyncio.fixture
async def resource_pool(db):
    session, user = db
    pool = ResourcePool(name="标准池", created_by=user.id)
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return pool


@pytest_asyncio.fixture
async def group(db):
    from app.models import UserGroup

    session, _ = db
    g = UserGroup(
        name=f"g_{uuid.uuid4().hex[:8]}",
        code=f"c{uuid.uuid4().hex[:8]}",
    )
    session.add(g)
    await session.flush()
    g.litellm_team_id = str(g.id)
    await session.commit()
    await session.refresh(g)
    return g


async def _published_definition(session, user, group, model_group="gpt-4o"):
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"助手-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            model_settings={"litellm": {"model_group": model_group}} if model_group else None,
        ),
        user.id,
    )
    d, v = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(), user.id
    )
    return d, v


# ── definition_service 埋点 ────────────────────────────────────


async def test_create_definition_writes_audit_log(db, group):
    """create_definition 应在 operation_logs 写一条 agent_definition.create。"""
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(name="助手", group_id=group.id),
        user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(OperationLog.target_id == d.id)
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.action == "agent_definition.create"
    assert log.target_type == "agent_definition"
    assert log.actor_id == user.id
    assert log.group_id == group.id
    assert log.status == "success"
    assert log.detail["name"] == "助手"


async def test_update_definition_writes_audit_log_with_changes(db, group):
    """update_definition 应写 agent_definition.update，detail 记录变更字段。"""
    session, user = db
    d = await definition_service.create_definition(
        session, AgentDefinitionCreate(name="原名", group_id=group.id), user.id
    )

    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(name="新名"),
        actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == d.id,
                OperationLog.action == "agent_definition.update",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == user.id
    assert log.group_id == group.id
    assert log.detail["name"] == "新名"


async def test_delete_definition_writes_audit_log(db, group):
    """delete_definition 应在删除前写 agent_definition.delete（target_id 仍可记录）。"""
    session, user = db
    d = await definition_service.create_definition(
        session, AgentDefinitionCreate(name="待删", group_id=group.id), user.id
    )
    target_id = d.id

    await definition_service.delete_definition(session, d.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "agent_definition.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == user.id
    assert log.group_id == group.id
    assert log.detail["name"] == "待删"


async def test_publish_definition_writes_audit_log(db, group):
    """publish_definition 应写 agent_definition.publish，detail 含 version_no。"""
    session, user = db
    d, v = await _published_definition(session, user, group)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == d.id,
                OperationLog.action == "agent_definition.publish",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.detail["version_no"] == "1.0.0"
    assert log.detail["version_id"] == str(v.id)


# ── instance_service 埋点 ────────────────────────────────────


async def test_create_instance_writes_audit_log(db, resource_pool, mock_litellm, group):
    """create_instance 应写 agent_instance.create。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == inst.id,
                OperationLog.action == "agent_instance.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == user.id
    assert log.group_id == group.id
    assert log.detail["name"] == "inst"


async def test_publish_offline_write_audit_logs(db, resource_pool, mock_litellm, group):
    """publish + offline 各写一条 log。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    await instance_service.publish_instance(session, inst.id, actor_id=user.id)
    await instance_service.offline_instance(session, inst.id, actor_id=user.id)

    actions = [
        r.action
        for r in (
            await session.execute(
                select(OperationLog).where(OperationLog.target_id == inst.id)
            )
        ).scalars().all()
    ]
    assert "agent_instance.create" in actions
    assert "agent_instance.publish" in actions
    assert "agent_instance.offline" in actions


async def test_delete_instance_writes_audit_log(db, resource_pool, mock_litellm, group):
    """delete_instance 应在删除前写 agent_instance.delete。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="待删",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    target_id = inst.id

    await instance_service.delete_instance(session, inst.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "agent_instance.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == "待删"


# ── P1 埋点：resource_pools / users / user_groups / roles / channels ────────


async def test_create_resource_pool_writes_audit_log(db):
    """create_resource_pool 应写 resource_pool.create。"""
    from app.schemas import ResourcePoolCreate
    from app.services import resource_pool_service

    session, user = db
    pool = await resource_pool_service.create_resource_pool(
        session,
        ResourcePoolCreate(name="池1", min_cpu="1", max_cpu="2", min_memory="1Gi", max_memory="2Gi"),
        user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == pool.id,
                OperationLog.action == "resource_pool.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == user.id
    assert log.target_type == "resource_pool"
    assert log.detail["name"] == "池1"


async def test_update_resource_pool_writes_audit_log(db):
    from app.schemas import ResourcePoolCreate, ResourcePoolUpdate
    from app.services import resource_pool_service

    session, user = db
    pool = await resource_pool_service.create_resource_pool(
        session,
        ResourcePoolCreate(name="原名", min_cpu="1", max_cpu="2", min_memory="1Gi", max_memory="2Gi"),
        user.id,
    )
    await resource_pool_service.update_resource_pool(
        session, pool.id, ResourcePoolUpdate(name="新名"), actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == pool.id,
                OperationLog.action == "resource_pool.update",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == "新名"
    assert "name" in logs[0].detail["fields"]


async def test_delete_resource_pool_writes_audit_log(db):
    from app.schemas import ResourcePoolCreate
    from app.services import resource_pool_service

    session, user = db
    pool = await resource_pool_service.create_resource_pool(
        session,
        ResourcePoolCreate(name="待删", min_cpu="1", max_cpu="2", min_memory="1Gi", max_memory="2Gi"),
        user.id,
    )
    target_id = pool.id
    await resource_pool_service.delete_resource_pool(session, pool.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "resource_pool.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == "待删"


async def test_create_user_writes_audit_log(db):
    from app.schemas import UserCreate
    from app.services import user_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    created = await user_service.create_user(
        session,
        UserCreate(username=f"newuser_{suffix}", password="Tr0ub4dor&3-Strong-99!", email=f"new_{suffix}@example.com", real_name="新用户"),
        actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == created.id,
                OperationLog.action == "user.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == user.id
    assert log.detail["username"] == f"newuser_{suffix}"


async def test_update_user_writes_audit_log(db):
    from app.schemas import UserCreate, UserUpdate
    from app.services import user_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    target = await user_service.create_user(
        session,
        UserCreate(username=f"u_update_{suffix}", password="Tr0ub4dor&3-Strong-99!", email=f"uu_{suffix}@example.com"),
        actor_id=user.id,
    )
    await user_service.update_user(
        session, target.id, UserUpdate(real_name="改过名"), actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target.id,
                OperationLog.action == "user.update",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert "real_name" in logs[0].detail["fields"]


async def test_delete_user_writes_audit_log(db):
    from app.schemas import UserCreate
    from app.services import user_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    target = await user_service.create_user(
        session,
        UserCreate(username=f"u_del_{suffix}", password="Tr0ub4dor&3-Strong-99!", email=f"udel_{suffix}@example.com"),
        actor_id=user.id,
    )
    target_id = target.id
    await user_service.delete_user(session, target.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "user.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["username"] == f"u_del_{suffix}"


async def test_create_user_group_writes_audit_log(db):
    from app.schemas import UserGroupCreate
    from app.services import user_group_service

    session, user = db
    g = await user_group_service.create_group(
        session,
        UserGroupCreate(name="测试组"),
        actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == g.id,
                OperationLog.action == "user_group.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.detail["name"] == "测试组"
    assert log.detail["code"] == g.code


async def test_update_user_group_writes_audit_log(db):
    from app.schemas import UserGroupCreate, UserGroupUpdate
    from app.services import user_group_service

    session, user = db
    g = await user_group_service.create_group(
        session, UserGroupCreate(name="原名"), actor_id=user.id,
    )
    await user_group_service.update_group(
        session, g.id, UserGroupUpdate(name="新名"), actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == g.id,
                OperationLog.action == "user_group.update",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == "新名"


async def test_delete_user_group_writes_audit_log(db):
    from app.schemas import UserGroupCreate
    from app.services import user_group_service

    session, user = db
    g = await user_group_service.create_group(
        session, UserGroupCreate(name="待删组"), actor_id=user.id,
    )
    target_id = g.id
    await user_group_service.delete_group(session, g.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "user_group.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == "待删组"


async def test_create_role_writes_audit_log(db):
    from app.schemas import RoleCreate
    from app.services import role_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    role = await role_service.create_role(
        session, RoleCreate(name=f"r_create_{suffix}"), actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == role.id,
                OperationLog.action == "role.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == f"r_create_{suffix}"


async def test_update_role_writes_audit_log(db):
    from app.schemas import RoleCreate, RoleUpdate
    from app.services import role_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    role = await role_service.create_role(
        session, RoleCreate(name=f"r_orig_{suffix}"), actor_id=user.id,
    )
    new_name = f"r_new_{suffix}"
    await role_service.update_role(
        session, role.id, RoleUpdate(name=new_name), actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == role.id,
                OperationLog.action == "role.update",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == new_name


async def test_delete_role_writes_audit_log(db):
    from app.schemas import RoleCreate
    from app.services import role_service

    session, user = db
    suffix = uuid.uuid4().hex[:6]
    role = await role_service.create_role(
        session, RoleCreate(name=f"r_del_{suffix}"), actor_id=user.id,
    )
    target_id = role.id
    await role_service.delete_role(session, role.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "role.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["name"] == f"r_del_{suffix}"


async def test_create_channel_writes_audit_log(db, resource_pool, mock_litellm, group):
    """channel_service.create_channel 应写 agent_channel.create。"""
    from app.schemas import AgentInstanceChannelCreate, AgentInstanceCreate
    from app.services import channel_service, instance_service

    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    ch = await channel_service.create_channel(
        session, inst.id,
        AgentInstanceChannelCreate(channel_type="feishu", config={"app_id": "x"}),
        actor_id=user.id,
    )

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == ch.id,
                OperationLog.action == "agent_channel.create",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.target_type == "agent_channel"
    assert log.group_id == group.id
    assert log.detail["channel_type"] == "feishu"


async def test_delete_channel_writes_audit_log(db, resource_pool, mock_litellm, group):
    from app.schemas import AgentInstanceChannelCreate, AgentInstanceCreate
    from app.services import channel_service, instance_service

    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    ch = await channel_service.create_channel(
        session, inst.id,
        AgentInstanceChannelCreate(channel_type="wecom", config={}),
        actor_id=user.id,
    )
    target_id = ch.id
    await channel_service.delete_channel(session, ch.id, actor_id=user.id)

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.target_id == target_id,
                OperationLog.action == "agent_channel.delete",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].detail["channel_type"] == "wecom"
