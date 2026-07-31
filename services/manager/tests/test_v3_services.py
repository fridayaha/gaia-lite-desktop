"""V3 service 层单元测试 — 用真 DB 断言写入字段值（非 mock commit）。

- 本地 postgres（settings.database_url），V3 表独立，每测试清理。
- litellm_client mock（避免外部 HTTP）；controller 未接入，instance_service 不调。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from pkg.common.config import settings
from app.models import User, ResourcePool, AgentDefinition, AgentVersion, AgentInstance, AgentStatus, DefinitionStatus, EngineType, AgentDeployment, DeploymentStatus
from app.schemas import (
    AgentDefinitionCreate, AgentDefinitionUpdate, PublishVersionRequest,
    AgentInstanceCreate, AgentInstanceUpdate,
    AgentInstanceChannelCreate, AgentInstanceChannelUpdate,
)
from app.services import litellm_client
from app.services import definition_service, instance_service


V3_TABLES = [
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
    """真 DB session + 隔离 test user；teardown 清理 V3 表与 user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"v3test_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

    # 先解除 current_version_id 循环 FK，再按依赖顺序清理
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in V3_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest.fixture
def mock_litellm(monkeypatch):
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
    """用户组（V3 隔离单元）：必填 code，litellm_team_id = str(id)。"""
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


# =========================================
# definition_service
# =========================================


async def test_create_and_get_definition(db, group):
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(name="客服助手", description="测试", group_id=group.id, persona_config={"system_prompt": "你好"}),
        user.id,
    )
    assert d.id is not None
    assert d.status == DefinitionStatus.DRAFT
    assert d.engine_type == EngineType.HERMES
    assert d.model_config == {}  # model_settings 默认 None → {}
    assert d.persona_config == {"system_prompt": "你好"}

    fetched = await definition_service.get_definition(session, d.id)
    assert fetched is not None
    assert fetched.name == "客服助手"


async def test_update_definition_writes_fields(db, group):
    session, user = db
    d = await definition_service.create_definition(
        session, AgentDefinitionCreate(name="助手", group_id=group.id), user.id
    )
    updated = await definition_service.update_definition(
        session, d.id,
        AgentDefinitionUpdate(name="改名", skill_config={"skills": ["a"]}), actor_id=user.id,
    )
    assert updated.name == "改名"
    assert updated.skill_config == {"skills": ["a"]}
    # DB 层断言
    row = (await session.execute(select(AgentDefinition).where(AgentDefinition.id == d.id))).scalar_one()
    assert row.name == "改名"
    assert row.skill_config == {"skills": ["a"]}


async def test_publish_definition_creates_version_snapshot(db, group):
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name="助手",
            group_id=group.id,
            persona_config={"system_prompt": "v1 人设"},
            model_settings={"litellm": {"model_group": "gpt-4o"}},
        ),
        user.id,
    )
    d, v = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="首次发布"), user.id
    )
    assert d.status == DefinitionStatus.PUBLISHED
    assert d.current_version_id == v.id
    assert d.published_at is not None
    assert v.version_no == "1.0.0"
    # 快照不可变：拷贝了发布时的配置
    assert v.persona_config == {"system_prompt": "v1 人设"}
    assert v.model_config == {"litellm": {"model_group": "gpt-4o"}}
    assert v.engine_type == EngineType.HERMES

    versions = await definition_service.list_versions(session, d.id)
    assert len(versions) == 1


async def test_publish_definition_version_increments(db, group):
    session, user = db
    d = await definition_service.create_definition(session, AgentDefinitionCreate(name="助手", group_id=group.id), user.id)
    await definition_service.publish_definition(session, d.id, PublishVersionRequest(), user.id)
    # 改草稿再发布
    await definition_service.update_definition(session, d.id, AgentDefinitionUpdate(description="v2"), actor_id=user.id)
    d2, v2 = await definition_service.publish_definition(session, d.id, PublishVersionRequest(change_log="v2"), user.id)
    assert v2.version_no == "1.0.1"
    versions = await definition_service.list_versions(session, d.id)
    assert len(versions) == 2
    assert d2.current_version_id == v2.id


async def test_delete_definition_rejected_when_instance_references(db, resource_pool, mock_litellm, group):
    session, user = db
    d, _ = await definition_service.publish_definition(
        session,
        (await definition_service.create_definition(session, AgentDefinitionCreate(name="助手", group_id=group.id), user.id)).id,
        PublishVersionRequest(),
        user.id,
    )
    await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
        user.id,
    )
    with pytest.raises(ValueError, match="引用"):
        await definition_service.delete_definition(session, d.id, actor_id=user.id)


async def test_delete_definition_succeeds_when_no_reference(db, group):
    session, user = db
    d = await definition_service.create_definition(session, AgentDefinitionCreate(name="助手", group_id=group.id), user.id)
    assert await definition_service.delete_definition(session, d.id, actor_id=user.id) is True
    assert await definition_service.get_definition(session, d.id) is None


async def test_delete_published_definition_cascades_versions(db, mock_litellm, group):
    """已发布定义（含 version + current_version_id 循环 FK）删除：version 级联删除，不触发 CircularDependency。"""
    session, user = db
    d, v = await _published_definition(session, user, group)
    assert d.current_version_id == v.id  # 循环 FK 存在
    version_id = v.id
    assert await definition_service.delete_definition(session, d.id, actor_id=user.id) is True
    assert await definition_service.get_definition(session, d.id) is None
    # version 行应随定义级联删除
    remaining = (
        await session.execute(select(AgentVersion).where(AgentVersion.id == version_id))
    ).scalars().all()
    assert remaining == []


# =========================================
# instance_service
# =========================================


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


async def test_create_instance_binds_definition_version_pool(db, resource_pool, mock_litellm, group):
    session, user = db
    d, v = await _published_definition(session, user, group)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="客服-内部版", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
        user.id,
    )
    assert inst.definition_id == d.id
    assert inst.version_id == v.id  # 未传 version_id → 用 current_version_id
    assert inst.resource_pool_id == resource_pool.id
    assert inst.status == AgentStatus.DRAFT
    assert inst.group_id == group.id
    # per-instance litellm key 写入
    assert inst.litellm_config["key"] == "sk-test-key"
    assert inst.litellm_config["model_group"] == "gpt-4o"
    assert inst.litellm_config["team_id"] == str(group.id)

    # DB 层断言
    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.litellm_config["model_group"] == "gpt-4o"


async def test_create_instance_rejects_unpublished_definition(db, resource_pool, mock_litellm, group):
    session, user = db
    d = await definition_service.create_definition(session, AgentDefinitionCreate(name="助手", group_id=group.id), user.id)
    with pytest.raises(ValueError, match="尚未发布版本"):
        await instance_service.create_instance(
            session,
            AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
            user.id,
        )


async def test_publish_and_offline_instance(db, resource_pool, mock_litellm, group):
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    published = await instance_service.publish_instance(session, inst.id, actor_id=user.id)
    assert published.status == AgentStatus.PUBLISHED
    assert published.published_at is not None

    offline = await instance_service.offline_instance(session, inst.id, actor_id=user.id)
    assert offline.status == AgentStatus.OFFLINE


async def test_switch_version_reprovisions_key(db, resource_pool, mock_litellm, group):
    session, user = db
    d, v1 = await _published_definition(session, user, group, model_group="gpt-4o")
    # 发布第二版（model_group 变更）
    await definition_service.update_definition(
        session, d.id, AgentDefinitionUpdate(model_settings={"litellm": {"model_group": "claude-3.5"}}),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(session, d.id, PublishVersionRequest(change_log="v2"), user.id)

    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, version_id=v1.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    assert inst.litellm_config["model_group"] == "gpt-4o"

    switched = await instance_service.switch_version(session, inst.id, v2.id, actor_id=user.id)
    assert switched.version_id == v2.id
    assert switched.litellm_config["model_group"] == "claude-3.5"


async def test_create_dify_external_skips_resource_pool(db, mock_litellm, group):
    """Dify 外接模式（dify_config.base_url 存在）跳过资源池校验，inst.resource_pool_id 为 None。"""
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"dify-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            engine_type=EngineType.DIFY,
        ),
        user.id,
    )
    d, _ = await definition_service.publish_definition(session, d.id, PublishVersionRequest(), user.id)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="dify-external",
            definition_id=d.id,
            group_id=group.id,
            dify_config={"base_url": "http://dify.example.com", "app_api_key": "k", "app_type": "chat"},
        ),
        user.id,
    )
    assert inst.resource_pool_id is None
    assert inst.dify_config["base_url"] == "http://dify.example.com"

    # DB 层断言
    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.resource_pool_id is None


async def test_create_dify_managed_still_requires_pool(db, mock_litellm, group):
    """Dify MANAGED 模式（dify_config 空/无 base_url）仍需资源池，不传则报"资源池必填"。"""
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"dify-managed-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            engine_type=EngineType.DIFY,
        ),
        user.id,
    )
    d, _ = await definition_service.publish_definition(session, d.id, PublishVersionRequest(), user.id)

    with pytest.raises(ValueError, match="资源池必填"):
        await instance_service.create_instance(
            session,
            AgentInstanceCreate(
                name="dify-managed",
                definition_id=d.id,
                group_id=group.id,
            ),
            user.id,
        )


async def test_publish_dify_external_creates_deployment(db, mock_litellm, group):
    """Dify 外接模式 publish_instance 应内联建 AgentDeployment（RUNNING + engine_url=base_url）"""
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"dify-ext-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            engine_type=EngineType.DIFY,
        ),
        user.id,
    )
    d, _ = await definition_service.publish_definition(session, d.id, PublishVersionRequest(), user.id)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="dify-ext-pub",
            definition_id=d.id,
            group_id=group.id,
            dify_config={"base_url": "http://dify.example.com", "app_api_key": "k", "app_type": "chat"},
        ),
        user.id,
    )

    # publish 前无 deployment 记录
    pre = (await session.execute(select(AgentDeployment).where(AgentDeployment.instance_id == inst.id))).scalar_one_or_none()
    assert pre is None

    published = await instance_service.publish_instance(session, inst.id, actor_id=user.id)
    assert published.status == AgentStatus.PUBLISHED

    # publish 后应有一行 RUNNING deployment，engine_url 指向 base_url，pod_name 为空
    dep = (await session.execute(select(AgentDeployment).where(AgentDeployment.instance_id == inst.id))).scalar_one()
    assert dep.status == DeploymentStatus.RUNNING
    assert dep.engine_url == "http://dify.example.com"
    assert dep.pod_name is None
    assert dep.scope_type == "ALL"
    assert dep.scope_target_id is None
    assert dep.resource_pool_id is None
    assert dep.deployed_at is not None


async def test_publish_dify_external_idempotent_updates_deployment(db, mock_litellm, group):
    """Dify 外接模式重复 publish 应复用既有 deployment 行（id 不变），刷新 engine_url/last_active_at"""
    session, user = db
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"dify-ext2-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            engine_type=EngineType.DIFY,
        ),
        user.id,
    )
    d, _ = await definition_service.publish_definition(session, d.id, PublishVersionRequest(), user.id)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="dify-ext-pub2",
            definition_id=d.id,
            group_id=group.id,
            dify_config={"base_url": "http://dify2.example.com", "app_api_key": "k", "app_type": "chat"},
        ),
        user.id,
    )

    await instance_service.publish_instance(session, inst.id, actor_id=user.id)
    dep1 = (await session.execute(select(AgentDeployment).where(AgentDeployment.instance_id == inst.id))).scalar_one()
    first_id = dep1.id
    first_active = dep1.last_active_at

    # 改 base_url 后再 publish（模拟 OFFLINE→PUBLISHED 二次上线）
    inst.dify_config = {"base_url": "http://dify3.example.com", "app_api_key": "k", "app_type": "chat"}
    session.add(inst)
    await session.commit()
    await instance_service.publish_instance(session, inst.id, actor_id=user.id)

    dep2 = (await session.execute(select(AgentDeployment).where(AgentDeployment.instance_id == inst.id))).scalar_one()
    assert dep2.id == first_id  # 复用同一行
    assert dep2.engine_url == "http://dify3.example.com"
    assert dep2.status == DeploymentStatus.RUNNING
    assert dep2.last_active_at is not None
    if first_active is not None:
        assert dep2.last_active_at >= first_active


async def test_publish_non_dify_does_not_create_deployment(db, resource_pool, mock_litellm, group):
    """非 Dify 引擎 publish 不应建 deployment 记录（Hermes 仍走 worker deploy 流程）"""
    session, user = db
    d, _ = await _published_definition(session, user, group)  # HERMES 默认
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="hermes-pub", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
        user.id,
    )
    await instance_service.publish_instance(session, inst.id, actor_id=user.id)
    dep = (await session.execute(select(AgentDeployment).where(AgentDeployment.instance_id == inst.id))).scalar_one_or_none()
    assert dep is None  # 没 deploy 过，不应有 dep 记录


async def test_update_instance_change_group_id(db, resource_pool, mock_litellm):
    """改实例 group_id 到新组（定义也搬到新组）→ inst.group_id 更新 + _provision_litellm force=True 重生成 key。"""
    from app.models import UserGroup
    session, user = db
    g_a = UserGroup(name=f"g_a_{uuid.uuid4().hex[:6]}", code=f"ca{uuid.uuid4().hex[:8]}")
    g_b = UserGroup(name=f"g_b_{uuid.uuid4().hex[:6]}", code=f"cb{uuid.uuid4().hex[:8]}")
    session.add_all([g_a, g_b])
    await session.flush()
    g_a.litellm_team_id = str(g_a.id)
    g_b.litellm_team_id = str(g_b.id)
    await session.commit()
    await session.refresh(g_a)
    await session.refresh(g_b)

    d, _ = await _published_definition(session, user, g_a)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=g_a.id),
        user.id,
    )
    assert inst.group_id == g_a.id
    assert inst.litellm_config["team_id"] == str(g_a.id)

    # 把定义也搬到 g_b（update_definition 不支持改 group_id，直接 DB 层改）
    d.group_id = g_b.id
    await session.commit()

    # 改实例 group_id 到 g_b → 触发 _provision_litellm(force=True)，team_id 变为 g_b
    updated = await instance_service.update_instance(
        session, inst.id, AgentInstanceUpdate(group_id=g_b.id), actor_id=user.id,
    )
    assert updated.group_id == g_b.id
    assert updated.litellm_config["team_id"] == str(g_b.id)
    assert updated.litellm_config["key"] == "sk-test-key"  # mock 返回的 key


async def test_update_instance_group_id_mismatch_definition(db, resource_pool, mock_litellm):
    """改实例 group_id 到新组但定义还在旧组 → 报"当前绑定的智能体模版不属于该用户组"。"""
    from app.models import UserGroup
    session, user = db
    g_a = UserGroup(name=f"g_a_{uuid.uuid4().hex[:6]}", code=f"ca{uuid.uuid4().hex[:8]}")
    g_b = UserGroup(name=f"g_b_{uuid.uuid4().hex[:6]}", code=f"cb{uuid.uuid4().hex[:8]}")
    session.add_all([g_a, g_b])
    await session.flush()
    g_a.litellm_team_id = str(g_a.id)
    g_b.litellm_team_id = str(g_b.id)
    await session.commit()
    await session.refresh(g_a)
    await session.refresh(g_b)

    d, _ = await _published_definition(session, user, g_a)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=g_a.id),
        user.id,
    )

    # 定义还在 g_a，改实例 group_id 到 g_b → 报错
    with pytest.raises(ValueError, match="不属于该用户组"):
        await instance_service.update_instance(
        session, inst.id, AgentInstanceUpdate(group_id=g_b.id), actor_id=user.id,
    )


async def test_reprovision_instance_key_regenerates_and_persists(db, resource_pool, mock_litellm, group, monkeypatch):
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    assert inst.litellm_config["key"] == "sk-test-key"
    old_key = inst.litellm_config["key"]

    # 覆盖 generate_key 返回新 key，证明 reprovision 重新生成（而非复用旧值）
    async def _new_gen(**kw):
        return {"key": "sk-test-key-2", "token_id": "tid-456"}

    monkeypatch.setattr(litellm_client, "generate_key", _new_gen)

    reprovisioned = await instance_service.reprovision_instance_key(session, inst.id, actor_id=user.id)
    assert reprovisioned is not None
    assert reprovisioned.litellm_config["key"] == "sk-test-key-2"
    assert reprovisioned.litellm_config["key"] != old_key
    assert reprovisioned.litellm_config["model_group"] == "gpt-4o"  # 版本/access 未变

    # DB 层断言（不只断言内存对象）
    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.litellm_config["key"] == "sk-test-key-2"
    assert row.litellm_config["model_group"] == "gpt-4o"


async def test_reprovision_instance_key_noop_without_litellm_config(db, resource_pool, mock_litellm, group):
    session, user = db
    d, _ = await _published_definition(session, user, group, model_group=None)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    # 版本未配 litellm.model_group → create 时未写 litellm_config
    assert not inst.litellm_config

    # reprovision 不应抛错，且保持未配置（_provision_litellm 内部 return）
    reprovisioned = await instance_service.reprovision_instance_key(session, inst.id, actor_id=user.id)
    assert reprovisioned is not None
    assert not reprovisioned.litellm_config


async def test_reprovision_instance_key_returns_none_when_missing(db, resource_pool, mock_litellm):
    session, user = db
    missing = await instance_service.reprovision_instance_key(session, uuid.uuid4(), actor_id=user.id)
    assert missing is None


# =========================================
# runtime_config（per-instance 运行时开关，如浏览器沙箱）
# =========================================


async def test_create_instance_persists_runtime_config(db, resource_pool, mock_litellm, group):
    """创建实例带 runtime_config → 落库（DB 层断言，非 mock commit）。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="browser-inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
            runtime_config={"browser_sandbox": {"enabled": True}},
        ),
        user.id,
    )
    assert inst.runtime_config == {"browser_sandbox": {"enabled": True}}

    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.runtime_config == {"browser_sandbox": {"enabled": True}}


async def test_create_instance_runtime_config_defaults_empty(db, resource_pool, mock_litellm, group):
    """不传 runtime_config → 落库为空 dict（非 NULL）。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
        user.id,
    )
    assert inst.runtime_config == {}

    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.runtime_config == {}


async def test_update_instance_runtime_config(db, resource_pool, mock_litellm, group):
    """更新 runtime_config → 落库新值。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
            runtime_config={"browser_sandbox": {"enabled": False}},
        ),
        user.id,
    )

    updated = await instance_service.update_instance(
        session,
        inst.id,
        AgentInstanceUpdate(runtime_config={"browser_sandbox": {"enabled": True}}),
        actor_id=user.id,
    )
    assert updated.runtime_config == {"browser_sandbox": {"enabled": True}}

    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.runtime_config == {"browser_sandbox": {"enabled": True}}


async def test_update_instance_runtime_config_unset_keeps_value(db, resource_pool, mock_litellm, group):
    """update 不传 runtime_config（None）→ 不改原值。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
            runtime_config={"browser_sandbox": {"enabled": True}},
        ),
        user.id,
    )

    await instance_service.update_instance(
        session, inst.id, AgentInstanceUpdate(description="改描述"), actor_id=user.id,
    )
    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))).scalar_one()
    assert row.runtime_config == {"browser_sandbox": {"enabled": True}}


async def test_clone_instance_copies_runtime_config(db, resource_pool, mock_litellm, group):
    """克隆实例 → runtime_config 一并拷贝。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
            runtime_config={"browser_sandbox": {"enabled": True}},
        ),
        user.id,
    )

    clone = await instance_service.clone_instance(session, inst.id, user.id)
    assert clone is not None
    assert clone.runtime_config == {"browser_sandbox": {"enabled": True}}

    row = (await session.execute(select(AgentInstance).where(AgentInstance.id == clone.id))).scalar_one()
    assert row.runtime_config == {"browser_sandbox": {"enabled": True}}


async def test_create_instance_compensates_key_on_commit_failure(
    db, resource_pool, mock_litellm, group, monkeypatch
):
    """commit 失败时，_commit_or_compensate 应回滚并删除已建的 LiteLLM key，避免孤儿。

    回归 _provision_litellm 在 commit 前就 generate_key 的事务泄漏：commit 失败后
    DB 回滚但 LiteLLM key 已建 → 指向不存在实例的「无所属智能体」垃圾 key。
    """
    session, user = db
    d, _ = await _published_definition(session, user, group)

    async def _gen(**kw):
        return {"key": "sk-orphan-on-rollback", "token_id": "tid-x"}

    deleted = []

    async def _del(token):
        deleted.append(token)

    monkeypatch.setattr(litellm_client, "generate_key", _gen)
    monkeypatch.setattr(litellm_client, "delete_key", _del)

    # 让 create_instance 的那次 commit 失败（仅一次；后续 db fixture teardown 的 commit 走真实路径）
    real_commit = session.commit
    calls = {"n": 0}

    async def _fail_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated commit failure")
        await real_commit()

    monkeypatch.setattr(session, "commit", _fail_once)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await instance_service.create_instance(
            session,
            AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id),
            user.id,
        )

    # commit 失败 → 补偿删除已建 key，不留孤儿
    assert "sk-orphan-on-rollback" in deleted


async def test_switch_version_rejects_foreign_version(db, resource_pool, mock_litellm, group):
    session, user = db
    d1, _ = await _published_definition(session, user, group)
    d2, v2 = await _published_definition(session, user, group)  # 另一个定义
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d1.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    with pytest.raises(ValueError, match="不属于"):
        await instance_service.switch_version(session, inst.id, v2.id, actor_id=user.id)


async def test_clone_instance_independent_key(db, resource_pool, mock_litellm, group):
    session, user = db
    d, _ = await _published_definition(session, user, group)
    src = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    clone = await instance_service.clone_instance(session, src.id, user.id)
    assert clone.id != src.id
    assert clone.definition_id == src.definition_id
    assert clone.resource_pool_id == src.resource_pool_id
    assert clone.litellm_config["key"] == "sk-test-key"  # 独立生成


async def test_delete_instance_revokes_key(db, resource_pool, mock_litellm, group, monkeypatch):
    """delete_instance 按 metadata.instance_id 批量吊销 key：当前 key + 历史孤儿都删，
    别的实例的 key 不动。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    cur_key = inst.litellm_config["key"]

    # 模拟 LiteLLM 里有 3 个 key：当前 key、历史孤儿（同 instance_id，不在 config 里）、别的实例的 key
    async def _list_keys(team_id=None):
        return [
            {"token": cur_key, "metadata": {"instance_id": str(inst.id)}},
            {"token": "sk-orphan-xxx", "metadata": {"instance_id": str(inst.id)}},
            {"token": "sk-other-inst", "metadata": {"instance_id": str(uuid.uuid4())}},
        ]

    deleted = []
    async def _delete_key(token):
        deleted.append(token)

    monkeypatch.setattr(litellm_client, "list_keys", _list_keys)
    monkeypatch.setattr(litellm_client, "delete_key", _delete_key)

    ok = await instance_service.delete_instance(session, inst.id, actor_id=user.id)
    assert ok is True
    assert await instance_service.get_instance(session, inst.id) is None
    # 当前 key 和孤儿都删；别的实例的 key 不动
    assert cur_key in deleted
    assert "sk-orphan-xxx" in deleted
    assert "sk-other-inst" not in deleted


async def test_delete_instance_swallows_litellm_error(db, resource_pool, mock_litellm, group, monkeypatch):
    """delete_keys_by_instance 抛 LitellmError（LiteLLM 不可用）时 best-effort 放行，实例仍删。"""
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )

    async def _boom(instance_id):
        raise litellm_client.LitellmError("litellm down", 503)

    monkeypatch.setattr(litellm_client, "delete_keys_by_instance", _boom)

    ok = await instance_service.delete_instance(session, inst.id, actor_id=user.id)
    assert ok is True
    assert await instance_service.get_instance(session, inst.id) is None


async def test_delete_published_instance_cascades_channels(db, resource_pool, mock_litellm, group):
    """已上线实例（publish 创建 http 渠道）删除：渠道级联删除，不触发 instance_id NOT NULL。"""
    from app.models import AgentInstanceChannel

    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    await instance_service.publish_instance(session, inst.id, actor_id=user.id)  # 上线 → ensure_http_channel 建渠道
    inst_id = inst.id
    chans = (
        await session.execute(
            select(AgentInstanceChannel).where(AgentInstanceChannel.instance_id == inst_id)
        )
    ).scalars().all()
    assert len(chans) >= 1  # 渠道已存在

    assert await instance_service.delete_instance(session, inst_id, actor_id=user.id) is True
    assert await instance_service.get_instance(session, inst_id) is None
    chans_after = (
        await session.execute(
            select(AgentInstanceChannel).where(AgentInstanceChannel.instance_id == inst_id)
        )
    ).scalars().all()
    assert chans_after == []  # 渠道级联删除


async def test_deployment_fk_references_instance(db, resource_pool, mock_litellm, group):
    """新建实例的 agent_deployments 行 FK 指向 agent_instances（B3 列重命名 unblock 验证）。

    冒烟发现：列重命名前 agent_deployments.agent_id FK→老 agents 表，新建实例（agents 表无行）
    deploy 时 ForeignKeyViolation。重命名后 instance_id FK→agent_instances，新建实例可落 deployment 行。
    """
    from app.models import AgentDeployment, DeploymentStatus
    from datetime import datetime, timezone

    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    # 直接写入 agent_deployments（模拟 controller deploy 落库），验证 FK→agent_instances 成立
    dep = AgentDeployment(
        instance_id=inst.id,
        group_id=inst.group_id,
        resource_pool_id=resource_pool.id,
        status=DeploymentStatus.RUNNING,
        scope_type="ALL",
        pod_name=f"engine-hermes-{str(inst.id)[:8]}",
        engine_url=f"http://engine-hermes-{str(inst.id)[:8]}.unionagents.svc.cluster.local:8642",
        deployed_at=datetime.now(timezone.utc),
    )
    session.add(dep)
    await session.commit()
    await session.refresh(dep)

    # 回查：按 instance_id 查到该 deployment，字段值正确
    found = (
        await session.execute(
            select(AgentDeployment).where(AgentDeployment.instance_id == inst.id)
        )
    ).scalar_one()
    assert found.resource_pool_id == resource_pool.id
    assert found.status == DeploymentStatus.RUNNING
    assert found.pod_name.startswith("engine-hermes-")


async def test_delete_deployed_instance_cascades_deployment(db, resource_pool, mock_litellm, group):
    """删已 deploy 的实例：agent_deployments 行由 DB ondelete=CASCADE 级联清理，
    不触发 ORM 置 instance_id=NULL（NOT NULL）。"""
    from app.models import AgentDeployment, DeploymentStatus
    from datetime import datetime, timezone

    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session, AgentInstanceCreate(name="inst", definition_id=d.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    session.add(AgentDeployment(
        instance_id=inst.id,
        group_id=inst.group_id,
        resource_pool_id=resource_pool.id,
        status=DeploymentStatus.ARCHIVED,
        scope_type="ALL",
        pod_name=f"engine-hermes-{str(inst.id)[:8]}",
        deployed_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    assert await instance_service.delete_instance(session, inst.id, actor_id=user.id) is True
    assert await instance_service.get_instance(session, inst.id) is None
    # deployment 行应随实例级联删除
    deps = (
        await session.execute(
            select(AgentDeployment).where(AgentDeployment.instance_id == inst.id)
        )
    ).scalars().all()
    assert deps == []


async def test_user_group_scope_derives_team(db, resource_pool, mock_litellm, group):
    """实例 team_id 恒等于其所属组的 litellm_team_id（= str(group.id)）。"""
    session, user = db

    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst", definition_id=d.id, resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    assert inst.litellm_config["team_id"] == str(group.id)


# =========================================
# channel_service（V3：AgentInstanceChannel + instance_id）
# =========================================


async def _instance_for_channel(db, resource_pool, mock_litellm, group):
    session, user = db
    d, _ = await _published_definition(session, user, group)
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst", definition_id=d.id, resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    return inst


async def test_create_channel_writes_instance_id_and_derives_scope(db, resource_pool, mock_litellm, group):
    from app.services import channel_service
    from app.models import AgentInstanceChannel
    session, user = db

    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    ch = await channel_service.create_channel(
        session, inst.id,
        AgentInstanceChannelCreate(channel_type="feishu", config={"app_id": "x", "app_secret": "s"}),
        actor_id=user.id,
    )
    # DB 层断言：写 instance_id；默认 INDEPENDENT（用户级独占 profile，scope=USER/None）
    row = (await session.execute(
        select(AgentInstanceChannel).where(AgentInstanceChannel.id == ch.id)
    )).scalar_one()
    assert row.instance_id == inst.id
    assert row.scope_type == "USER"
    assert row.scope_target_id is None
    assert row.profile_type == "INDEPENDENT"
    assert row.group_id == group.id
    assert row.config["app_id"] == "x"
    assert row.callback_url.endswith(f"/feishu/{inst.id}/callback")


async def test_create_channel_forces_independent_even_if_shared_passed(db, resource_pool, mock_litellm, group):
    """SHARED 已下线：传入 profile_type=SHARED 也强制 INDEPENDENT/USER。"""
    from app.services import channel_service
    from app.models import AgentInstanceChannel
    session, user = db

    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    ch = await channel_service.create_channel(
        session, inst.id,
        AgentInstanceChannelCreate(
            channel_type="feishu",
            config={"app_id": "x", "app_secret": "s"},
            profile_type="SHARED",
        ),
        actor_id=user.id,
    )
    row = (await session.execute(
        select(AgentInstanceChannel).where(AgentInstanceChannel.id == ch.id)
    )).scalar_one()
    assert row.scope_type == "USER"
    assert row.scope_target_id is None
    assert row.profile_type == "INDEPENDENT"


async def test_list_channels_by_instance(db, resource_pool, mock_litellm, group):
    from app.services import channel_service
    session, user = db
    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    await channel_service.create_channel(
        session, inst.id, AgentInstanceChannelCreate(channel_type="wecom", config={"corp_id": "c"}),
        actor_id=user.id,
    )
    channels, total = await channel_service.list_channels(session, inst.id)
    assert total == 1
    assert channels[0].channel_type == "wecom"


async def test_ensure_http_channel_idempotent(db, resource_pool, mock_litellm, group):
    from app.services import channel_service
    from app.models import AgentInstanceChannel
    session, user = db
    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    c1 = await channel_service.ensure_http_channel(session, inst)
    c2 = await channel_service.ensure_http_channel(session, inst)
    assert c1.id == c2.id  # 幂等
    rows = (await session.execute(
        select(AgentInstanceChannel).where(
            AgentInstanceChannel.instance_id == inst.id,
            AgentInstanceChannel.channel_type == "http",
        )
    )).scalars().all()
    assert len(rows) == 1
    # 默认 INDEPENDENT（与 create_channel 一致，避免 web/IM 跨渠道产生两个 profile）
    row = rows[0]
    assert row.profile_type == "INDEPENDENT"
    assert row.scope_type == "USER"
    assert row.scope_target_id is None


async def test_update_channel_merges_sensitive(db, resource_pool, mock_litellm, group):
    """敏感字段空值表示保持不变，避免编辑单字段清空凭据。"""
    from app.services import channel_service
    from app.models import AgentInstanceChannel
    session, user = db
    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    ch = await channel_service.create_channel(
        session, inst.id,
        AgentInstanceChannelCreate(channel_type="feishu", config={"app_id": "a", "app_secret": "secret"}),
        actor_id=user.id,
    )
    await channel_service.update_channel(
        session, ch.id,
        AgentInstanceChannelUpdate(config={"app_id": "b", "app_secret": ""}),  # 空值→保留
        actor_id=user.id,
    )
    row = (await session.execute(
        select(AgentInstanceChannel).where(AgentInstanceChannel.id == ch.id)
    )).scalar_one()
    assert row.config["app_id"] == "b"
    assert row.config["app_secret"] == "secret"  # 未被空值清空


async def test_delete_channel(db, resource_pool, mock_litellm, group):
    from app.services import channel_service
    from app.models import AgentInstanceChannel
    session, user = db
    inst = await _instance_for_channel(db, resource_pool, mock_litellm, group)
    ch = await channel_service.create_channel(
        session, inst.id, AgentInstanceChannelCreate(channel_type="wecom", config={}),
        actor_id=user.id,
    )
    ok = await channel_service.delete_channel(session, ch.id, actor_id=user.id)
    assert ok is True
    remaining = (await session.execute(
        select(AgentInstanceChannel).where(AgentInstanceChannel.id == ch.id)
    )).scalar_one_or_none()
    assert remaining is None


# =========================================
# accessible 跨组隔离 + 平台管理员旁路
# =========================================


async def test_list_accessible_admin_bypasses_group(db, resource_pool, mock_litellm, group):
    """平台管理员 accessible 跨组见全部 PUBLISHED；组用户仅见所属组。"""
    from app.models import UserGroup, user_group_members
    session, user = db
    other = UserGroup(name=f"g_{uuid.uuid4().hex[:6]}", code=f"c{uuid.uuid4().hex[:6]}")
    session.add(other)
    await session.flush()
    other.litellm_team_id = str(other.id)
    await session.commit()
    await session.refresh(other)

    # user 加 group（成为 group 成员，但不在 other 组）
    await session.execute(
        user_group_members.insert().values(user_id=user.id, group_id=group.id)
    )
    await session.commit()

    # 两组各建一个 PUBLISHED 实例
    d1, _ = await _published_definition(session, user, group)
    inst1 = await instance_service.create_instance(
        session, AgentInstanceCreate(name="i1", definition_id=d1.id, resource_pool_id=resource_pool.id, group_id=group.id), user.id
    )
    await instance_service.publish_instance(session, inst1.id, actor_id=user.id)
    d2, _ = await _published_definition(session, user, other)
    inst2 = await instance_service.create_instance(
        session, AgentInstanceCreate(name="i2", definition_id=d2.id, resource_pool_id=resource_pool.id, group_id=other.id), user.id
    )
    await instance_service.publish_instance(session, inst2.id, actor_id=user.id)

    # 组用户（user 属 group，不在 other）：只见 i1
    got = await instance_service.list_accessible_instances(session, user.id, is_admin=False)
    assert {g.name for g in got} == {"i1"}
    # 平台管理员：跨组见全部 PUBLISHED
    got_admin = await instance_service.list_accessible_instances(session, user.id, is_admin=True)
    assert {g.name for g in got_admin} == {"i1", "i2"}
