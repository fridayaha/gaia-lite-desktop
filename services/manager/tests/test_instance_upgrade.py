"""instance_service.upgrade_version 单测 — 真 DB 断言写入 + controller_client spy 验证调用。

upgrade_version = switch_version(DB+key) + 按版本 diff 增量热推送。
- litellm_client mock（避免外部 HTTP）
- controller_client spy（无 k3s，验证调用而非真实推送）
- archiver.get_skill_zip mock（无 MinIO）
"""

import uuid

import pytest
import pytest_asyncio
from app.models import (
    AgentInstance,
    ResourcePool,
    User,
    UserGroup,
)
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionUpdate,
    AgentInstanceCreate,
    PublishVersionRequest,
)
from app.services import definition_service, instance_service, litellm_client
from app.services import instance_service as isvc
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

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
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"upg_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

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
    session, _ = db
    g = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(g)
    await session.flush()
    g.litellm_team_id = str(g.id)
    await session.commit()
    await session.refresh(g)
    return g


@pytest.fixture
def controller_spy(monkeypatch):
    """spy controller_client 调用（不真实推送），status 可配。返回 (spies, set_status)。"""
    from unittest.mock import AsyncMock

    state = {"status": "RUNNING"}
    spies = {
        "get_agent_status": AsyncMock(side_effect=lambda aid: {"status": state["status"]}),
        # 默认 engine_deployed=False → on_pod=None → 回退「只装 added」原行为；
        # 测试可覆盖 return_value 为 {"engine_deployed": True, "items": [{"name": ...}]}
        # 来验证「补缺失」分支。
        "list_engine_skills": AsyncMock(return_value={"engine_deployed": False, "items": []}),
        "sync_persona": AsyncMock(return_value={"status": "synced"}),
        "sync_skills_config": AsyncMock(return_value={"status": "synced"}),
        "install_skill": AsyncMock(return_value={"status": "installed"}),
        "uninstall_skill": AsyncMock(return_value={"status": "uninstalled"}),
        "apply_agent_config": AsyncMock(return_value={"status": "applied"}),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(isvc.controller_client, name, spy)

    def set_status(s):
        state["status"] = s

    return spies, set_status


@pytest.fixture
def mock_archiver(monkeypatch):
    """archiver.get_skill_zip 返回占位 bytes（任何 skill 名都命中）。"""
    from unittest.mock import MagicMock

    m = MagicMock()
    m.get_skill_zip = MagicMock(return_value=b"fake-zip-bytes")
    m.list_skill_zips = MagicMock(return_value=[])
    monkeypatch.setattr(isvc, "archiver", m)
    return m


async def _published_definition(
    session, user, group, *, model_group="gpt-4o", persona=None, skills=None
):
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"助手-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            model_settings={"litellm": {"model_group": model_group}} if model_group else None,
            persona_config=persona,
            skill_config=skills,
        ),
        user.id,
    )
    d, v = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(), user.id
    )
    return d, v


# =========================================


async def test_upgrade_persona_only_hot(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """仅人设变更 → 纯热路径：sync_persona 调用，apply_agent_config 不调用，restarted=False。"""
    session, user = db
    spies, _ = controller_spy
    d, v1 = await _published_definition(
        session,
        user,
        group,
        persona={"system_prompt": "v1 人设"},
        skills={"skills": [], "order": []},
    )
    # 发布 v2：只改人设
    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(persona_config={"system_prompt": "v2 新人设"}),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is True
    assert result["restarted"] is False
    assert "persona" in result["changed"]
    assert "model" not in result["changed"]

    spies["sync_persona"].assert_awaited_once_with(str(inst.id))
    spies["apply_agent_config"].assert_not_awaited()

    # DB 层断言：version_id 实际落库为 v2
    row = (
        await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))
    ).scalar_one()
    assert row.version_id == v2.id


async def test_upgrade_model_group_changed_triggers_restart(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver, monkeypatch
):
    """model_group 变更 → key 重生成 + apply_agent_config 调用 + restarted=True。"""
    session, user = db
    spies, _ = controller_spy
    d, v1 = await _published_definition(session, user, group, model_group="gpt-4o")

    # 第二次 generate_key 返回不同 token_id，证明 key 重生成
    call_count = {"n": 0}

    async def _gen(**kw):
        call_count["n"] += 1
        return {"key": f"sk-key-{call_count['n']}", "token_id": f"tid-{call_count['n']}"}

    monkeypatch.setattr(litellm_client, "generate_key", _gen)

    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(model_settings={"litellm": {"model_group": "claude-3.5"}}),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    assert inst.litellm_config["model_group"] == "gpt-4o"

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is True
    assert result["restarted"] is True
    assert "model" in result["changed"]

    spies["apply_agent_config"].assert_awaited_once_with(str(inst.id))

    # DB 层：model_group 已更新为 claude-3.5，key_id 变化
    row = (
        await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))
    ).scalar_one()
    assert row.version_id == v2.id
    assert row.litellm_config["model_group"] == "claude-3.5"
    assert row.litellm_config["key_id"] == "tid-2"


async def test_upgrade_skills_diff(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """新版本新增技能 b → install_skill('b') 调用；sync_skills_config 调用。"""
    session, user = db
    spies, _ = controller_spy
    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}], "order": ["a"]}
    )
    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(
            skill_config={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
        ),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is True
    assert "skills" in result["changed"]

    # install_skill 被调用，skill_name='b'（新增的）
    installed_names = [c.args[1] for c in spies["install_skill"].await_args_list]
    assert "b" in installed_names
    spies["uninstall_skill"].assert_not_awaited()
    spies["sync_skills_config"].assert_awaited_with(str(inst.id))

    # DB 层断言
    row = (
        await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))
    ).scalar_one()
    assert row.version_id == v2.id


async def test_upgrade_skills_removed_uninstalls(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """新版本移除技能 b → uninstall_skill('b') 调用。"""
    session, user = db
    spies, _ = controller_spy
    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
    )
    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(skill_config={"skills": [{"name": "a"}], "order": ["a"]}),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    uninstalled_names = [c.args[1] for c in spies["uninstall_skill"].await_args_list]
    assert "b" in uninstalled_names
    spies["install_skill"].assert_not_awaited()


async def test_upgrade_not_running_skips_push(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """实例未 RUNNING → 仅 DB 更新，applied=False，无 sync 调用。"""
    session, user = db
    spies, set_status = controller_spy
    set_status("SUSPENDED")

    d, v1 = await _published_definition(session, user, group, persona={"system_prompt": "v1"})
    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(persona_config={"system_prompt": "v2"}),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is False
    assert result["reason"] == "not_running"
    spies["sync_persona"].assert_not_awaited()
    spies["sync_skills_config"].assert_not_awaited()
    spies["apply_agent_config"].assert_not_awaited()

    # DB 层：版本仍已切换（待 resume 时生效）
    row = (
        await session.execute(select(AgentInstance).where(AgentInstance.id == inst.id))
    ).scalar_one()
    assert row.version_id == v2.id


async def test_upgrade_wrong_definition_version_raises(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """目标版本属于别的 definition → ValueError。"""
    session, user = db
    d1, v1 = await _published_definition(session, user, group)
    d2, v2 = await _published_definition(session, user, group)

    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d1.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    with pytest.raises(ValueError, match="不属于该实例的定义"):
        await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)


# ── 补缺失技能：新旧版本技能集相同/部分相同，但 Pod 上缺失 → 升级补装 ──


async def test_upgrade_skills_missing_reinstalls(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """新旧版本技能集相同，Pod 缺 b → 补装 b；Pod 已有的 a 不重装（保留 secrets.enc）。"""
    session, user = db
    spies, _ = controller_spy
    spies["list_engine_skills"].return_value = {"engine_deployed": True, "items": [{"name": "a"}]}

    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is True

    installed_names = [c.args[1] for c in spies["install_skill"].await_args_list]
    assert "b" in installed_names
    assert "a" not in installed_names
    spies["uninstall_skill"].assert_not_awaited()


async def test_upgrade_skills_missing_skips_when_zip_absent(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """Pod 缺 b 但 COS 无 b 的 zip → skip install（不抛错，best-effort）。"""
    session, user = db
    spies, _ = controller_spy
    spies["list_engine_skills"].return_value = {"engine_deployed": True, "items": [{"name": "a"}]}
    mock_archiver.get_skill_zip.return_value = None

    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert result["applied"] is True
    spies["install_skill"].assert_not_awaited()


async def test_upgrade_no_reinstall_when_on_pod_complete(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """新旧版本技能集相同且 Pod 已有全部技能 → 不装不卸。"""
    session, user = db
    spies, _ = controller_spy
    spies["list_engine_skills"].return_value = {
        "engine_deployed": True,
        "items": [{"name": "a"}, {"name": "b"}],
    }

    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    spies["install_skill"].assert_not_awaited()
    spies["uninstall_skill"].assert_not_awaited()


async def test_upgrade_list_engine_skills_error_fallback(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """list_engine_skills 抛 ControllerError → 回退原行为：只装 added，不补缺失。"""
    from app.worker.errors import ControllerError

    session, user = db
    spies, _ = controller_spy
    spies["list_engine_skills"].side_effect = ControllerError("scan failed", 500)

    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}], "order": ["a"]}
    )
    await definition_service.update_definition(
        session,
        d.id,
        AgentDefinitionUpdate(
            skill_config={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
        ),
        actor_id=user.id,
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    installed_names = [c.args[1] for c in spies["install_skill"].await_args_list]
    assert installed_names == ["b"]


async def test_upgrade_skills_missing_marks_changed(
    db, resource_pool, mock_litellm, group, controller_spy, mock_archiver
):
    """补缺失技能时 changed 含 'skills'（即便版本 diff 为空）。"""
    session, user = db
    spies, _ = controller_spy
    spies["list_engine_skills"].return_value = {"engine_deployed": True, "items": [{"name": "a"}]}

    d, v1 = await _published_definition(
        session, user, group, skills={"skills": [{"name": "a"}, {"name": "b"}], "order": ["a", "b"]}
    )
    _, v2 = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(change_log="v2"), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name="inst",
            definition_id=d.id,
            version_id=v1.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )

    result = await instance_service.upgrade_version(session, inst.id, v2.id, actor_id=user.id)
    assert "skills" in result["changed"]
