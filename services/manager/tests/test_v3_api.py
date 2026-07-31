"""V3 API 冒烟测试 — 真 DB + ASGITransport，覆盖 resource-pools / agent-definitions / agent-instances 全流程。"""

import uuid

import pytest_asyncio
from app.core.auth import get_current_user
from app.core.group_scope import get_current_group_ids
from app.main import app
from app.models import User, UserGroup, user_group_members
from app.services import litellm_client
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings
from pkg.common.database import get_db

V3_TABLES = [
    "agent_instance_channels",
    "agent_instance_api_keys",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "agent_deployments",
    "im_user_bindings",
    "user_group_members",
    "user_groups",
]


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"v3api_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    # 建用户组并把当前用户加为成员（V3 组隔离：用户只能操作/可见所属组资源）
    group = UserGroup(
        name=f"g_{uuid.uuid4().hex[:8]}",
        code=f"c{uuid.uuid4().hex[:8]}",
    )
    session.add(group)
    await session.flush()
    group.litellm_team_id = str(group.id)
    await session.execute(user_group_members.insert().values(user_id=user.id, group_id=group.id))
    await session.commit()
    # 预加载 roles（accessible 端点 is_platform_admin 访问 user.roles，避免 async lazy load）
    await session.refresh(user, ["roles"])
    await session.refresh(group)

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    # 用户所属组范围：[group.id]（组用户，非管理员；accessible 测试可临时改写）
    app.dependency_overrides[get_current_group_ids] = lambda: [group.id]

    # mock litellm_client（避免外部 HTTP）
    async def _noop(*a, **kw):
        return {}

    async def _gen(*a, **kw):
        return {"key": "sk-test", "token_id": "tid"}

    orig = (litellm_client.ensure_team, litellm_client.generate_key, litellm_client.delete_key)
    litellm_client.ensure_team = _noop
    litellm_client.generate_key = _gen
    litellm_client.delete_key = _noop

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c, user, group
    await c.aclose()

    app.dependency_overrides.clear()
    litellm_client.ensure_team, litellm_client.generate_key, litellm_client.delete_key = orig

    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in V3_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


# =========================================
# resource-pools
# =========================================


async def test_resource_pool_crud(client):
    c, _, group = client
    # create（组私有池）
    resp = await c.post(
        "/api/manager/resource-pools",
        json={"name": "标准池", "group_id": str(group.id), "max_sessions_per_pod": 30},
    )
    assert resp.status_code == 201
    pool = resp.json()
    assert pool["name"] == "标准池"
    assert pool["max_sessions_per_pod"] == 30
    pid = pool["id"]

    # get
    assert (await c.get(f"/api/manager/resource-pools/{pid}")).json()["name"] == "标准池"

    # list
    listing = (await c.get("/api/manager/resource-pools")).json()
    assert listing["total"] >= 1

    # update
    upd = await c.put(f"/api/manager/resource-pools/{pid}", json={"max_cpu": "4"})
    assert upd.json()["max_cpu"] == "4"

    # delete
    assert (await c.delete(f"/api/manager/resource-pools/{pid}")).status_code == 204
    assert (await c.get(f"/api/manager/resource-pools/{pid}")).status_code == 404


# =========================================
# agent-definitions
# =========================================


async def test_definition_publish_versions(client):
    c, _, group = client
    resp = await c.post(
        "/api/manager/agent-definitions",
        json={
            "name": "客服助手",
            "group_id": str(group.id),
            "persona_config": {"system_prompt": "你好"},
            "model_settings": {"litellm": {"model_group": "gpt-4o"}},
        },
    )
    assert resp.status_code == 201
    did = resp.json()["id"]
    assert resp.json()["status"] == "DRAFT"

    # publish v1
    v1 = (
        await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": "首次"})
    ).json()
    assert v1["version_no"] == "1.0.0"
    assert v1["model_config"] if "model_config" in v1 else True

    # definition 状态变 PUBLISHED
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["status"] == "PUBLISHED"
    assert d["current_version_id"] == v1["id"]
    # current_version_no 应填充（非 None），instance_count 初始 0
    assert d["current_version_no"] == "1.0.0"
    assert d["instance_count"] == 0

    # 改草稿再发布 v2
    await c.put(f"/api/manager/agent-definitions/{did}", json={"description": "v2 改"})
    v2 = (
        await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": "v2"})
    ).json()
    assert v2["version_no"] == "1.0.1"

    # update 后 current_version_no 跟进到 v2
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["current_version_no"] == "1.0.1"

    versions = (await c.get(f"/api/manager/agent-definitions/{did}/versions")).json()
    assert len(versions) == 2


async def test_definition_has_unpublished_changes(client):
    """has_unpublished_changes：从未发布=True；发布后无改动=False；改草稿=True；再发布=False。"""
    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "未发布测试",
                "group_id": str(group.id),
                "persona_config": {"system_prompt": "v1"},
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]

    # 1) 从未发布 → True
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["has_unpublished_changes"] is True

    # 2) 发布 v1 → 草稿与快照一致 → False
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["has_unpublished_changes"] is False

    # 3) 改 persona_config 草稿 → 偏离快照 → True
    await c.put(
        f"/api/manager/agent-definitions/{did}",
        json={"persona_config": {"system_prompt": "v2 改动"}},
    )
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["has_unpublished_changes"] is True

    # 4) 再发布 v2 → 一致 → False
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": "v2"})
    d = (await c.get(f"/api/manager/agent-definitions/{did}")).json()
    assert d["has_unpublished_changes"] is False


async def test_definition_list_instance_count(client):
    """列表页 instance_count 反映真实实例数（非恒 0）。"""
    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "d",
                "group_id": str(group.id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]
    # 建 2 个实例
    for i in range(2):
        (
            await c.post(
                "/api/manager/agent-instances",
                json={
                    "name": f"inst{i}",
                    "definition_id": did,
                    "resource_pool_id": pid,
                    "group_id": str(group.id),
                },
            )
        )

    listing = (await c.get("/api/manager/agent-definitions")).json()
    item = next(i for i in listing["items"] if i["id"] == did)
    assert item["instance_count"] == 2


async def test_definition_delete_conflict_when_instance_references(client):
    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions", json={"name": "d", "group_id": str(group.id)}
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]
    inst = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "inst",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
            },
        )
    ).json()
    assert inst["status"] == "DRAFT"

    # 有实例引用 → 删除定义应 409
    resp = await c.delete(f"/api/manager/agent-definitions/{did}")
    assert resp.status_code == 409


# =========================================
# agent-instances
# =========================================


async def test_instance_lifecycle(client):
    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "d",
                "group_id": str(group.id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]

    # create instance
    inst = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "客服-内部版",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
            },
        )
    ).json()
    assert inst["status"] == "DRAFT"
    assert inst["litellm_config"]["model_group"] == "gpt-4o"
    assert inst["engine_type"] == "HERMES"
    iid = inst["id"]

    # 上线
    pub = (await c.post(f"/api/manager/agent-instances/{iid}/publish")).json()
    assert pub["status"] == "PUBLISHED"

    # 停用
    off = (await c.post(f"/api/manager/agent-instances/{iid}/offline")).json()
    assert off["status"] == "OFFLINE"

    # 再上线
    assert (await c.post(f"/api/manager/agent-instances/{iid}/publish")).json()[
        "status"
    ] == "PUBLISHED"


async def test_instance_create_rejects_unpublished(client):
    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions", json={"name": "d", "group_id": str(group.id)}
        )
    ).json()["id"]
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]
    resp = await c.post(
        "/api/manager/agent-instances",
        json={
            "name": "inst",
            "definition_id": did,
            "resource_pool_id": pid,
            "group_id": str(group.id),
        },
    )
    assert resp.status_code == 400
    assert "尚未发布版本" in resp.json()["detail"]


# =========================================
# agent-instances /accessible（终端门户可见性）
# =========================================


async def _published_def_pool(c, group_id):
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "d",
                "group_id": str(group_id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group_id)})
    ).json()["id"]
    return did, pid


async def _make_instance(c, did, pid, group_id, name):
    body = {"name": name, "definition_id": did, "resource_pool_id": pid, "group_id": str(group_id)}
    iid = (await c.post("/api/manager/agent-instances", json=body)).json()["id"]
    await c.post(f"/api/manager/agent-instances/{iid}/publish")
    return iid


async def test_accessible_filters_by_group_and_status(client):
    """组隔离 + 状态过滤：仅本组 PUBLISHED 实例可见，他组实例与未上线实例不可见。"""
    c, _, group = client
    did, pid = await _published_def_pool(c, group.id)

    # 本组已上线实例 → 可见
    iid_published = await _make_instance(c, did, pid, group.id, "本组已上线")
    # 本组已上线 + 浏览器沙箱启用实例 → 可见，browser_sandbox_enabled=true
    iid_sandbox = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "沙箱实例",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
                "runtime_config": {"browser_sandbox": {"enabled": True}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-instances/{iid_sandbox}/publish")
    # 未上线（DRAFT）实例 → 不可见
    iid_draft = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "草稿态",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
            },
        )
    ).json()["id"]

    resp = await c.get("/api/manager/agent-instances/accessible")
    assert resp.status_code == 200
    items = resp.json()
    ids = {i["id"] for i in items}
    assert iid_published in ids
    assert iid_sandbox in ids
    assert iid_draft not in ids  # 非 PUBLISHED 不可见
    # 响应字段对齐 portal AccessibleAgent（含 browser_sandbox_enabled 派生布尔）
    sample = next(i for i in items if i["id"] == iid_published)
    assert set(sample.keys()) == {
        "id", "name", "description", "engine_type", "browser_sandbox_enabled",
    }
    assert sample["engine_type"] == "HERMES"
    assert sample["browser_sandbox_enabled"] is False
    # 沙箱启用实例 → browser_sandbox_enabled=true（终端门户据此展示云桌面入口）
    sandbox_item = next(i for i in items if i["id"] == iid_sandbox)
    assert sandbox_item["browser_sandbox_enabled"] is True


async def test_accessible_user_group_membership(client, monkeypatch):
    """组隔离：用户只可见所属组的实例，他组实例不可见；加入他组后可见。"""
    from app.models import (
        AgentDefinition as ADModel,
    )
    from app.models import (
        AgentInstance as AIModel,
    )
    from app.models import (
        AgentStatus as AIStatus,
    )
    from sqlalchemy import select as _select

    c, user, group = client

    # 建第二个用户组（当前用户不在其中），并直接落库一个归属该组的 PUBLISHED 实例。
    # （fixture 用户的 group_ids=[group.id]，无权通过 API 写他组资源，故直接插库。）
    other_group = UserGroup(
        name=f"g_{uuid.uuid4().hex[:8]}",
        code=f"c{uuid.uuid4().hex[:8]}",
    )
    # 复用 client fixture 的 session（通过 get_db override 同一 session）
    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()
    session.add(other_group)
    await session.flush()
    other_group.litellm_team_id = str(other_group.id)
    await session.commit()
    await session.refresh(other_group)
    other_group_id = other_group.id

    did, pid = await _published_def_pool(c, group.id)
    # 取定义当前版本，造一个归属他组的 PUBLISHED 实例
    d_row = (
        await session.execute(_select(ADModel).where(ADModel.id == uuid.UUID(did)))
    ).scalar_one()
    other_inst = AIModel(
        name="他组实例",
        definition_id=uuid.UUID(did),
        version_id=d_row.current_version_id,
        resource_pool_id=uuid.UUID(pid),
        group_id=other_group_id,
        created_by=user.id,
        status=AIStatus.PUBLISHED,
    )
    session.add(other_inst)
    await session.commit()
    await session.refresh(other_inst)
    other_iid = str(other_inst.id)

    # 当前用户所属组=[group.id] → 他组实例不可见
    resp = await c.get("/api/manager/agent-instances/accessible")
    ids = {i["id"] for i in resp.json()}
    assert other_iid not in ids

    # 把用户加入他组 → accessible 查 user_group_members 命中他组 → 可见
    await session.execute(
        user_group_members.insert().values(user_id=user.id, group_id=other_group_id)
    )
    await session.commit()
    resp = await c.get("/api/manager/agent-instances/accessible")
    ids = {i["id"] for i in resp.json()}
    assert other_iid in ids


async def test_three_layer_cross_group_404_and_admin_bypass(client):
    """A3 验收：三层 CRUD 跨组访问 404 + 平台管理员旁路可见。

    覆盖 definition / resource-pool / instance 三层 GET 端点的组隔离：
      - 组用户访问他组资源 → 404（不暴露存在性）
      - 平台管理员（group_ids=None）→ 跨组可见 200
    """
    c, _, group = client
    did, pid = await _published_def_pool(c, group.id)
    iid = await _make_instance(c, did, pid, group.id, "本组实例")

    paths = [
        f"/api/manager/agent-definitions/{did}",
        f"/api/manager/resource-pools/{pid}",
        f"/api/manager/agent-instances/{iid}",
    ]

    # 1) 组用户范围切到一个他组 → 三层 GET 均 404
    #    （路由 Depends(get_current_group_ids) 经 app.dependency_overrides 覆盖）
    foreign = uuid.uuid4()
    app.dependency_overrides[get_current_group_ids] = lambda: [foreign]
    for p in paths:
        resp = await c.get(p)
        assert resp.status_code == 404, f"{p} 跨组应 404，实际 {resp.status_code}"

    # 2) 平台管理员（group_ids=None）旁路 → 三层 GET 均 200
    app.dependency_overrides[get_current_group_ids] = lambda: None
    for p in paths:
        resp = await c.get(p)
        assert resp.status_code == 200, f"{p} 管理员旁路应 200，实际 {resp.status_code}"

    # 还原 fixture 默认（组用户 [group.id]），避免影响后续测试
    app.dependency_overrides[get_current_group_ids] = lambda: [group.id]


async def test_three_layer_cross_group_write_forbidden(client):
    """A3 验收：跨组写操作被拒（assert_group_writable → 403/400）。

    组用户尝试在他组下创建定义/实例 → 403；资源池 he组私有池同理。
    """
    c, _, group = client
    foreign = uuid.uuid4()
    app.dependency_overrides[get_current_group_ids] = lambda: [foreign]
    try:
        # 跨组建定义 → 403（target_group_id=group.id 不在 [foreign]）
        resp = await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "跨组定义",
                "group_id": str(group.id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
        assert resp.status_code == 403

        # 跨组建私有资源池 → 403
        resp = await c.post(
            "/api/manager/resource-pools",
            json={
                "name": "跨组池",
                "group_id": str(group.id),
            },
        )
        assert resp.status_code == 403

        # 平台共享池（group_id=None）非管理员建 → 403
        resp = await c.post("/api/manager/resource-pools", json={"name": "共享池"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides[get_current_group_ids] = lambda: [group.id]


# =========================================
# agent-instances 运行时生命周期（代理调 controller）
# =========================================


async def test_instance_runtime_endpoints_proxy_to_controller(client, monkeypatch):
    from app.api import agent_instances as ai

    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "d",
                "group_id": str(group.id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]
    iid = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "inst",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
            },
        )
    ).json()["id"]

    calls = []

    async def _fake_deploy(instance_id, scope_type="ALL", scope_target_id=None):
        calls.append(("deploy", instance_id))
        return {"status": "running", "engine_url": "http://x"}

    async def _fake_suspend(instance_id):
        calls.append(("suspend", instance_id))
        return {"status": "suspended"}

    async def _fake_resume(instance_id):
        calls.append(("resume", instance_id))
        return {"status": "running"}

    async def _fake_restart(instance_id):
        calls.append(("restart", instance_id))
        return {"status": "restarting"}

    async def _fake_destroy(instance_id):
        calls.append(("destroy", instance_id))
        return {"status": "archived"}

    monkeypatch.setattr(ai.controller_client, "deploy_instance", _fake_deploy)
    monkeypatch.setattr(ai.controller_client, "suspend_instance", _fake_suspend)
    monkeypatch.setattr(ai.controller_client, "resume_instance", _fake_resume)
    monkeypatch.setattr(ai.controller_client, "restart_instance", _fake_restart)
    monkeypatch.setattr(ai.controller_client, "destroy_instance", _fake_destroy)

    assert (await c.post(f"/api/manager/agent-instances/{iid}/deploy")).status_code == 200
    assert (await c.post(f"/api/manager/agent-instances/{iid}/suspend")).status_code == 200
    assert (await c.post(f"/api/manager/agent-instances/{iid}/resume")).status_code == 200
    assert (await c.post(f"/api/manager/agent-instances/{iid}/restart")).status_code == 200
    assert (await c.post(f"/api/manager/agent-instances/{iid}/destroy")).status_code == 200

    # 每个运行时操作都以 instance_id 调过 controller_client 一次
    assert {op for op, _ in calls} == {"deploy", "suspend", "resume", "restart", "destroy"}
    assert all(cid == iid for _, cid in calls)


async def test_instance_runtime_404_when_instance_missing(client):
    c, _, _ = client
    missing = uuid.uuid4()
    for op in ("deploy", "suspend", "resume", "restart", "destroy"):
        resp = await c.post(f"/api/manager/agent-instances/{missing}/{op}")
        assert resp.status_code == 404, op


async def test_instance_runtime_controller_error_propagates(client, monkeypatch):
    from app.api import agent_instances as ai

    c, _, group = client
    did = (
        await c.post(
            "/api/manager/agent-definitions", json={"name": "d", "group_id": str(group.id)}
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]
    iid = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "inst",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group.id),
            },
        )
    ).json()["id"]

    async def _raise(instance_id, *a, **kw):
        raise ai.controller_client.ControllerError("controller 不可达", 502)

    monkeypatch.setattr(ai.controller_client, "resume_instance", _raise)

    resp = await c.post(f"/api/manager/agent-instances/{iid}/resume")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "controller 不可达"


# =========================================
# agent-instances 子资源（channels / deployment-status / pods / metrics / overview）
# =========================================


async def _published_def_with_instance(c, group_id):
    did = (
        await c.post(
            "/api/manager/agent-definitions",
            json={
                "name": "d",
                "group_id": str(group_id),
                "model_settings": {"litellm": {"model_group": "gpt-4o"}},
            },
        )
    ).json()["id"]
    await c.post(f"/api/manager/agent-definitions/{did}/publish", json={"change_log": ""})
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group_id)})
    ).json()["id"]
    iid = (
        await c.post(
            "/api/manager/agent-instances",
            json={
                "name": "inst",
                "definition_id": did,
                "resource_pool_id": pid,
                "group_id": str(group_id),
            },
        )
    ).json()["id"]
    return did, pid, iid


async def test_instance_channels_crud(client):
    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)

    # create
    resp = await c.post(
        f"/api/manager/agent-instances/{iid}/channels",
        json={
            "channel_type": "feishu",
            "config": {"app_id": "x", "app_secret": "s"},
        },
    )
    assert resp.status_code == 201
    ch = resp.json()
    assert ch["instance_id"] == iid
    assert ch["channel_type"] == "feishu"
    cid = ch["id"]

    # list
    listing = (await c.get(f"/api/manager/agent-instances/{iid}/channels")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == cid

    # update（敏感字段空值保留）
    upd = await c.put(
        f"/api/manager/agent-instances/{iid}/channels/{cid}",
        json={
            "config": {"app_id": "y", "app_secret": ""},
        },
    )
    assert upd.status_code == 200
    # response 脱敏：app_secret 被掩码为空串
    assert upd.json()["config"]["app_id"] == "y"

    # delete
    assert (await c.delete(f"/api/manager/agent-instances/{iid}/channels/{cid}")).status_code == 204
    assert (await c.get(f"/api/manager/agent-instances/{iid}/channels")).json()["total"] == 0


async def test_instance_subresources_proxy_to_controller(client, monkeypatch):
    from app.api import agent_instances as ai

    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)

    async def _status(instance_id):
        return {
            "agent_id": instance_id,
            "status": "RUNNING",
            "engine_url": "http://x",
            "last_active_at": None,
            "error_message": None,
        }

    async def _pods(pool_id):
        return {
            "items": [
                {
                    "name": "pod-1",
                    "node": "n",
                    "status": "Running",
                    "cpu": "100m",
                    "memory": "256Mi",
                    "restarts": 0,
                    "age": "1h",
                    "agent_id": iid,
                }
            ]
        }

    async def _pod_metrics(pool_id):
        return {"pod-1": {"cpu": "100m", "memory": "256Mi"}}

    async def _logs(pool_id, pod_name, tail_lines=200):
        return {"pod_name": pod_name, "logs": "line1\nline2\n"}

    monkeypatch.setattr(ai.controller_client, "get_agent_status", _status)
    monkeypatch.setattr(ai.controller_client, "list_instance_pods", _pods)
    monkeypatch.setattr(ai.controller_client, "list_instance_pod_metrics", _pod_metrics)
    monkeypatch.setattr(ai.controller_client, "get_pod_logs", _logs)

    # deployment-status
    st = (await c.get(f"/api/manager/agent-instances/{iid}/deployment-status")).json()
    assert st["status"] == "RUNNING"

    # pods
    pods = (await c.get(f"/api/manager/agent-instances/{iid}/pods")).json()
    assert pods["summary"]["running"] == 1
    assert pods["items"][0]["name"] == "pod-1"

    # logs
    logs = (await c.get(f"/api/manager/agent-instances/{iid}/pods/pod-1/logs")).json()
    assert "line1" in logs["logs"]

    # metrics / overview（LiteLLM key 在 instance.litellm_config；mock spend_logs 返回空）
    from app.services import litellm_client

    async def _empty_logs(*a, **kw):
        return {"data": []}

    monkeypatch.setattr(litellm_client, "spend_logs", _empty_logs)
    m = (await c.get(f"/api/manager/agent-instances/{iid}/metrics")).json()
    assert "cpu" in m and "requests" in m
    ov = (await c.get(f"/api/manager/agent-instances/{iid}/overview")).json()
    assert ov["conversationCount"] == 0


async def test_instance_pods_filtered_by_instance(client, monkeypatch):
    """实例详情 Pod 列表只返回本实例的 Pod，过滤掉同池其他实例的 Pod。"""
    from app.api import agent_instances as ai

    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    foreign_id = str(uuid.uuid4())

    async def _pods(pool_id):
        # 同池两个实例的 Pod：本实例 + 另一实例
        return {
            "items": [
                {
                    "name": "engine-hermes-mine",
                    "node": "n",
                    "status": "Running",
                    "cpu": "100m",
                    "memory": "256Mi",
                    "restarts": 0,
                    "age": "1h",
                    "agent_id": iid,
                },
                {
                    "name": "engine-hermes-foreign",
                    "node": "n",
                    "status": "Running",
                    "cpu": "200m",
                    "memory": "512Mi",
                    "restarts": 1,
                    "age": "2h",
                    "agent_id": foreign_id,
                },
            ]
        }

    async def _pod_metrics(pool_id):
        return {}

    monkeypatch.setattr(ai.controller_client, "list_instance_pods", _pods)
    monkeypatch.setattr(ai.controller_client, "list_instance_pod_metrics", _pod_metrics)

    pods = (await c.get(f"/api/manager/agent-instances/{iid}/pods")).json()
    names = [p["name"] for p in pods["items"]]
    assert names == ["engine-hermes-mine"]
    assert pods["summary"]["running"] == 1


# =========================================
# agent-definitions 技能（definition 维度 + controller fan-out mock）
# =========================================


def _skill_zip(name="greeter") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md", f"---\nname: {name}\ndescription: hi\nversion: 1.0.0\n---\nbody"
        )
    return buf.getvalue()


async def test_skills_install_writes_definition_config(client, monkeypatch):
    from app.api import agent_skills as sk

    c, _, group = client
    did, pid, iid = await _published_def_with_instance(c, group.id)
    # 上线实例使其成为 fan-out 目标
    await c.post(f"/api/manager/agent-instances/{iid}/publish")

    calls = []

    async def _install(instance_id, skill_name, zip_b64):
        calls.append((instance_id, skill_name))
        return {"status": "ok"}

    monkeypatch.setattr(sk.controller_client, "install_skill", _install)

    # 上传 zip 安装
    resp = await c.post(
        f"/api/manager/agent-definitions/{did}/skills/install",
        files={"file": ("greeter.zip", _skill_zip("greeter"), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "greeter"
    assert body["installed"] is True

    # fan-out 到实例
    assert calls and calls[0][0] == iid and calls[0][1] == "greeter"

    # DB 层断言：definition.skill_config 写入（list 未部署时读 config）
    async def _scan_not_deployed(instance_id):
        raise sk.controller_client.ControllerError("not deployed", 404)

    monkeypatch.setattr(sk.controller_client, "list_engine_skills", _scan_not_deployed)
    items = (await c.get(f"/api/manager/agent-definitions/{did}/skills")).json()["items"]
    assert any(i["name"] == "greeter" and i["installed"] for i in items)


async def test_skills_install_from_hub_writes_definition_config(client, monkeypatch):
    """从 hub 订阅技能：mock httpx 拉包 + controller fan-out，断言 DB 写入 source=hub + hub_ref。"""
    from app.api import agent_skills as sk

    c, _, group = client
    did, pid, iid = await _published_def_with_instance(c, group.id)
    await c.post(f"/api/manager/agent-instances/{iid}/publish")

    fanout = []

    async def _install(instance_id, skill_name, zip_b64):
        fanout.append((instance_id, skill_name))
        return {"status": "ok"}

    monkeypatch.setattr(sk.controller_client, "install_skill", _install)

    # mock httpx.AsyncClient：hub 拉包返回 greeter.zip
    hub_item_id = "00000000-0000-0000-0000-000000000001"
    hub_version_id = "00000000-0000-0000-0000-000000000002"
    zip_bytes = _skill_zip("greeter")

    class _FakeResp:
        status_code = 200
        content = zip_bytes
        text = ""

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            assert "/api/hub/exports/items/" in url
            assert hub_item_id in url and hub_version_id in url
            return _FakeResp()

    monkeypatch.setattr(sk.httpx, "AsyncClient", _FakeClient)

    resp = await c.post(
        f"/api/manager/agent-definitions/{did}/skills/install-from-hub",
        json={"hub_item_id": hub_item_id, "version_id": hub_version_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "greeter"
    assert body["source"] == "hub"

    # fan-out 命中实例
    assert fanout and fanout[0][0] == iid and fanout[0][1] == "greeter"

    # DB 层断言：definition.skill_config 写入 source=hub + hub_item_id/version_id
    from app.models import AgentDefinition as ADModel
    from sqlalchemy import select as _select
    import uuid as _uuid

    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()
    d_row = (
        await session.execute(_select(ADModel).where(ADModel.id == _uuid.UUID(did)))
    ).scalar_one()
    sc = d_row.skill_config
    if isinstance(sc, str):
        import json as _json
        sc = _json.loads(sc)
    rec = next(s for s in sc["skills"] if s.get("name") == "greeter")
    assert rec["source"] == "hub"
    assert rec["hub_item_id"] == hub_item_id
    assert rec["hub_version_id"] == hub_version_id


async def test_skills_install_from_hub_404(client, monkeypatch):
    """hub 拉包 404 时端点返回 404。"""
    from app.api import agent_skills as sk

    c, _, group = client
    did, _, _ = await _published_def_with_instance(c, group.id)

    class _FakeResp:
        status_code = 404
        content = b""
        text = "not found"

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _FakeResp()

    monkeypatch.setattr(sk.httpx, "AsyncClient", _FakeClient)

    resp = await c.post(
        f"/api/manager/agent-definitions/{did}/skills/install-from-hub",
        json={
            "hub_item_id": "00000000-0000-0000-0000-000000000001",
            "version_id": "00000000-0000-0000-0000-000000000002",
        },
    )
    assert resp.status_code == 404


async def test_skills_list_not_deployed(client, monkeypatch):
    from app.api import agent_skills as sk

    c, _, group = client
    did, _, _ = await _published_def_with_instance(c, group.id)

    async def _scan(instance_id):
        raise sk.controller_client.ControllerError("not deployed", 404)

    monkeypatch.setattr(sk.controller_client, "list_engine_skills", _scan)

    resp = await c.get(f"/api/manager/agent-definitions/{did}/skills")
    assert resp.status_code == 200
    assert resp.json()["engineDeployed"] is False


async def test_skills_toggle_order_uninstall(client, monkeypatch):
    from app.api import agent_skills as sk

    c, _, group = client
    did, pid, iid = await _published_def_with_instance(c, group.id)
    await c.post(f"/api/manager/agent-instances/{iid}/publish")

    async def _noop(*a, **kw):
        return {"status": "ok"}

    async def _scan_not_deployed(instance_id):
        raise sk.controller_client.ControllerError("not deployed", 404)

    monkeypatch.setattr(sk.controller_client, "install_skill", _noop)
    monkeypatch.setattr(sk.controller_client, "sync_skills_config", _noop)
    monkeypatch.setattr(sk.controller_client, "uninstall_skill", _noop)
    monkeypatch.setattr(sk.controller_client, "list_engine_skills", _scan_not_deployed)

    sid = (
        await c.post(
            f"/api/manager/agent-definitions/{did}/skills/install",
            files={"file": ("s.zip", _skill_zip("alpha"), "application/zip")},
        )
    ).json()["id"]

    # 开关
    tog = await c.put(f"/api/manager/agent-definitions/{did}/skills/{sid}", json={"enabled": False})
    assert tog.status_code == 200
    assert tog.json()["enabled"] is False

    # 排序
    assert (
        await c.put(f"/api/manager/agent-definitions/{did}/skills/order", json={"skill_ids": [sid]})
    ).status_code == 200

    # 卸载
    assert (await c.delete(f"/api/manager/agent-definitions/{did}/skills/{sid}")).status_code == 200
    items = (await c.get(f"/api/manager/agent-definitions/{did}/skills")).json()["items"]
    assert all(i["name"] != "alpha" for i in items)


async def test_uninstall_deletes_skill_credentials(client, monkeypatch):
    """uninstall 技能时级联删 SkillCredential，避免残留旧 credentials_encrypted 导致重装后 500。"""
    from app.api import agent_skills as sk
    from app.core.crypto import encrypt_credentials_dict
    from app.models import SkillCredential
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    c, user, group = client
    did, _pid, iid = await _published_def_with_instance(c, group.id)
    await c.post(f"/api/manager/agent-instances/{iid}/publish")

    async def _noop(*a, **kw):
        return {"status": "ok"}

    async def _scan_not_deployed(instance_id):
        raise sk.controller_client.ControllerError("not deployed", 404)

    monkeypatch.setattr(sk.controller_client, "install_skill", _noop)
    monkeypatch.setattr(sk.controller_client, "sync_skills_config", _noop)
    monkeypatch.setattr(sk.controller_client, "uninstall_skill", _noop)
    monkeypatch.setattr(sk.controller_client, "list_engine_skills", _scan_not_deployed)
    monkeypatch.setattr(sk.archiver, "delete_skill_zip", lambda *a, **kw: None)

    sid = (
        await c.post(
            f"/api/manager/agent-definitions/{did}/skills/install",
            files={"file": ("s.zip", _skill_zip("alpha"), "application/zip")},
        )
    ).json()["id"]

    # 直接插入 SkillCredential（模拟曾配过 api_key；alpha 无 secret param，走 API 会被拒）
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            s.add(
                SkillCredential(
                    definition_id=uuid.UUID(did),
                    skill_name="alpha",
                    scope_type="ALL",
                    scope_target_id=None,
                    credentials_encrypted=encrypt_credentials_dict({"api_key": "secret-val"}),
                    created_by=user.id,
                )
            )
            await s.commit()
            assert (
                len(
                    (
                        await s.execute(
                            select(SkillCredential).where(SkillCredential.skill_name == "alpha")
                        )
                    )
                    .scalars()
                    .all()
                )
                == 1
            )

        # uninstall
        assert (
            await c.delete(f"/api/manager/agent-definitions/{did}/skills/{sid}")
        ).status_code == 200

        # 验证 SkillCredential 被删（不残留 → 重装后 save_skill_credentials 不会 decrypt 旧值 500）
        async with factory() as s:
            rows = (
                (
                    await s.execute(
                        select(SkillCredential).where(SkillCredential.skill_name == "alpha")
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 0
    finally:
        await engine.dispose()


# =========================================
# resource-pools 子资源（metrics / pods / logs）
# =========================================


async def test_pool_subresources_proxy(client, monkeypatch):
    from app.api import resource_pools as rp

    c, _, group = client
    pid = (
        await c.post("/api/manager/resource-pools", json={"name": "p", "group_id": str(group.id)})
    ).json()["id"]

    async def _pods(pool_id):
        return {
            "items": [
                {
                    "name": "p1",
                    "status": "Running",
                    "cpu": "50m",
                    "memory": "128Mi",
                    "restarts": 0,
                    "age": "1h",
                    "agent_id": "x",
                }
            ]
        }

    async def _pod_metrics(pool_id):
        return {}

    async def _logs(pool_id, pod_name, tail_lines=200):
        return {"pod_name": pod_name, "logs": "hi"}

    monkeypatch.setattr(rp.controller_client, "list_instance_pods", _pods)
    monkeypatch.setattr(rp.controller_client, "list_instance_pod_metrics", _pod_metrics)
    monkeypatch.setattr(rp.controller_client, "get_pod_logs", _logs)
    from app.services import litellm_client

    monkeypatch.setattr(litellm_client, "spend_logs", _noop_spend)

    pods = (await c.get(f"/api/manager/resource-pools/{pid}/pods")).json()
    assert pods["summary"]["running"] == 1

    logs = (await c.get(f"/api/manager/resource-pools/{pid}/pods/p1/logs")).json()
    assert logs["logs"] == "hi"

    m = (await c.get(f"/api/manager/resource-pools/{pid}/metrics")).json()
    assert "cpu" in m


async def _noop_spend(*a, **kw):
    return {"data": []}


# =========================================
# LiteLLM 用量 / 成本（A4 验收）
# =========================================


async def test_litellm_spend_endpoints_group_scope_and_cny(client, monkeypatch):
    """A4 验收：/litellm/spend 明细，USD→CNY 转换，组管理员仅见本组。

    - /spend 明细：spend 字段 USD→CNY（× spend_usd_to_cny）
    - /spend?group_id=他组 → 403（组隔离）
    - /spend?group_id=不存在 → 404

    注：/spend/summary / /spend/by-model / /spend/trend 已合并到
    /api/manager/observability/usage（by_group 维度），见 test_observability.py。
    """
    from pkg.common.config import settings

    c, _, group = client
    rate = settings.spend_usd_to_cny

    async def _spend_logs(*, start_date=None, end_date=None, team_id=None, api_key=None, limit=100):
        # 2.0 USD → 2.0 * rate CNY
        return {"data": [{"spend": 2.0, "model": "gpt-4o"}], "total_spend": 2.0}

    monkeypatch.setattr(litellm_client, "spend_logs", _spend_logs)

    # 1) /spend 明细：本组 → 200，spend 已转 CNY
    resp = await c.get(f"/api/manager/litellm/spend?group_id={group.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_spend"] == round(2.0 * rate, 4)
    assert body["data"][0]["spend"] == round(2.0 * rate, 4)

    # 2) /spend 他组（存在但非成员）→ 403；不存在的组 → 404
    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()
    other_group = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(other_group)
    await session.flush()
    other_group.litellm_team_id = str(other_group.id)
    await session.commit()
    await session.refresh(other_group)

    resp = await c.get(f"/api/manager/litellm/spend?group_id={other_group.id}")
    assert resp.status_code == 403
    resp = await c.get(f"/api/manager/litellm/spend?group_id={uuid.uuid4()}")
    assert resp.status_code == 404


# =========================================
# IM 绑定跨组限制（A5 验收）
# =========================================


async def test_im_binding_cross_group_scope(client):
    """A5 验收：IM 绑定跨组限制正确。

    - 组用户可管自己 / 同组成员的 IM 绑定
    - 组用户管他组成员 → 403
    - 平台管理员旁路 → 任意 user_id 可管
    """
    from app.models import User, UserGroup

    c, user, group = client
    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()

    # 同组成员（与 fixture user 同属 group）
    same_group_user = User(
        username=f"same_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    # 他组成员（属另一个组）
    other_group = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    other_user = User(
        username=f"other_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add_all([same_group_user, other_group, other_user])
    await session.flush()
    other_group.litellm_team_id = str(other_group.id)
    await session.execute(
        user_group_members.insert().values(user_id=same_group_user.id, group_id=group.id)
    )
    await session.execute(
        user_group_members.insert().values(user_id=other_user.id, group_id=other_group.id)
    )
    await session.commit()

    binding = {"channel_type": "wecom", "im_user_id": "wecom_self", "im_user_name": "self"}

    # 1) 管自己 → 201
    resp = await c.post(f"/api/manager/users/{user.id}/im-bindings", json=binding)
    assert resp.status_code == 201, resp.text

    # 2) 管同组成员 → 201
    binding2 = {"channel_type": "feishu", "im_user_id": "feishu_same", "im_user_name": "same"}
    resp = await c.post(f"/api/manager/users/{same_group_user.id}/im-bindings", json=binding2)
    assert resp.status_code == 201, resp.text

    # 3) 管他组成员 → 403
    binding3 = {"channel_type": "dingtalk", "im_user_id": "dt_other", "im_user_name": "other"}
    resp = await c.post(f"/api/manager/users/{other_user.id}/im-bindings", json=binding3)
    assert resp.status_code == 403

    # 4) 平台管理员旁路 → 他组成员也可管
    from app.api import im_bindings as im_api
    from app.core.auth import is_platform_admin as _real_is_admin

    im_api.is_platform_admin = lambda u: True
    app.dependency_overrides[get_current_group_ids] = lambda: None
    try:
        resp = await c.post(f"/api/manager/users/{other_user.id}/im-bindings", json=binding3)
        assert resp.status_code == 201, resp.text
    finally:
        im_api.is_platform_admin = _real_is_admin
        app.dependency_overrides[get_current_group_ids] = lambda: [group.id]
        # 先清这些用户的 im 绑定（FK），再删额外用户与他组
        for uid in (same_group_user.id, other_user.id):
            await session.execute(
                text("DELETE FROM im_user_bindings WHERE user_id = :uid"), {"uid": uid}
            )
        await session.delete(same_group_user)
        await session.delete(other_user)
        await session.delete(other_group)
        await session.commit()


# =========================================
# Dashboard + metrics 采样（A6 验收）
# =========================================


async def test_dashboard_and_metrics_range_aggregation(client, monkeypatch):
    """A6 验收：/dashboard/* 各端点返回；metrics 按 1h/6h/24h/7d 聚合，非法 range 422。"""
    from app.worker import client as controller_client

    c, _, group = client

    # mock controller_client（避免 K8s）+ litellm spend（避免外部 HTTP）
    async def _pods(_id):
        return {"items": [{"name": "p1", "cpu": "500m", "memory": "256Mi"}], "total": 1}

    async def _pod_metrics(_id):
        return {}

    async def _spend_logs(*a, **kw):
        return {"data": []}

    async def _spend_teams():
        return {"total_spend_per_team": []}

    async def _spend_models(*a, **kw):
        return []

    monkeypatch.setattr(controller_client, "list_instance_pods", _pods)
    monkeypatch.setattr(controller_client, "list_instance_pod_metrics", _pod_metrics)
    monkeypatch.setattr(litellm_client, "spend_logs", _spend_logs)
    monkeypatch.setattr(litellm_client, "spend_teams", _spend_teams)
    monkeypatch.setattr(litellm_client, "spend_models", _spend_models)

    did, pid = await _published_def_pool(c, group.id)
    iid = await _make_instance(c, did, pid, group.id, "A6 实例")

    # 1) metrics range 聚合：1h/6h/24h/7d 均 200，结构含 cpu/memory/resourceRequest/podCount
    for r in ("1h", "6h", "24h", "7d"):
        resp = await c.get(f"/api/manager/resource-pools/{pid}/metrics?range={r}")
        assert resp.status_code == 200, f"range={r}: {resp.text}"
        body = resp.json()
        assert {"cpu", "memory", "resourceRequest", "podCount"} <= set(body.keys())
        assert body["podCount"] == 1

    # 非法 range → 422（Query pattern 校验）
    assert (await c.get(f"/api/manager/resource-pools/{pid}/metrics?range=bad")).status_code == 422

    # 实例 metrics：cpu/memory/requests/tokens
    resp = await c.get(f"/api/manager/agent-instances/{iid}/metrics?range=24h")
    assert resp.status_code == 200
    assert {"cpu", "memory", "requests", "tokens", "resourceRequest", "attribution"} <= set(
        resp.json().keys()
    )

    # 2) /dashboard/* 各端点均 200
    for ep in [
        "/dashboard/activities",
        "/dashboard/group",
        "/dashboard/health",
        "/dashboard/resources",
        "/dashboard/instance-status",
        "/dashboard/billing",
        "/dashboard/top-agents",
    ]:
        resp = await c.get(f"/api/manager{ep}")
        assert resp.status_code == 200, f"{ep}: {resp.status_code} {resp.text[:200]}"


# =========================================
# 资源池 Pod / 日志来源 可读性富化（agent_name + profile 用户信息）
# =========================================


async def test_pool_pods_carry_agent_name(client, monkeypatch):
    """Pod 列表补 agent_name：controller 只返回 agent_id，API 应映射成智能体名称。"""
    from app.api import resource_pools as rp

    c, _, group = client
    _, pid, iid = await _published_def_with_instance(c, group.id)
    inst_name = (await c.get(f"/api/manager/agent-instances/{iid}")).json()["name"]

    async def _pods(pool_id):
        return {
            "items": [
                {
                    "name": "engine-hermes-x",
                    "node": "n",
                    "status": "Running",
                    "cpu": "100m",
                    "memory": "256Mi",
                    "restarts": 0,
                    "age": "1h",
                    "agent_id": iid,
                },
                {
                    "name": "engine-hermes-y",
                    "node": "n",
                    "status": "Running",
                    "cpu": "50m",
                    "memory": "128Mi",
                    "restarts": 0,
                    "age": "1h",
                    "agent_id": str(uuid.uuid4()),  # 不存在的实例 → agent_name 为空
                },
            ]
        }

    async def _pod_metrics(pool_id):
        return {}

    monkeypatch.setattr(rp.controller_client, "list_instance_pods", _pods)
    monkeypatch.setattr(rp.controller_client, "list_instance_pod_metrics", _pod_metrics)

    pods = (await c.get(f"/api/manager/resource-pools/{pid}/pods")).json()
    by_name = {p["name"]: p for p in pods["items"]}
    assert by_name["engine-hermes-x"]["agent_name"] == inst_name
    assert by_name["engine-hermes-x"]["agent_id"] == iid
    assert by_name["engine-hermes-y"]["agent_name"] == ""


async def _seed_profile(session, *, instance_id, pool_id, group_id, profile_name, user_id):
    """直接写入 AgentProfile（含 AgentDeployment），用于 sources 富化测试。"""
    from app.models import AgentDeployment, AgentProfile, DeploymentStatus

    dep = AgentDeployment(
        instance_id=uuid.UUID(instance_id),
        group_id=group_id,
        resource_pool_id=uuid.UUID(pool_id),
        status=DeploymentStatus.RUNNING,
        scope_type="ALL",
        pod_name="engine-hermes-x",
    )
    session.add(dep)
    await session.flush()
    prof = AgentProfile(
        instance_id=uuid.UUID(instance_id),
        resource_pool_id=uuid.UUID(pool_id),
        deployment_id=dep.id,
        profile_name=profile_name,
        profile_type="USER",
        user_id=user_id,
        group_id=group_id,
        hermes_home=f"/data/{profile_name}",
    )
    session.add(prof)
    await session.commit()
    return prof


async def test_pool_pod_log_sources_enriched_with_user(client, monkeypatch):
    """日志来源 profiles 富化为 {profile_name, username, real_name}。"""
    from app.api import resource_pools as rp
    from app.models import User

    c, user, group = client
    _, pid, iid = await _published_def_with_instance(c, group.id)

    # 另建一个带真实姓名的终端用户，挂 profile
    end_user = User(
        username=f"eu_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
        real_name="张三",
    )
    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()
    session.add(end_user)
    await session.commit()
    await session.refresh(end_user)

    pn_user = "a1b2c3d4-1a2b3c-ef9f8f7d"
    pn_shared = "shared-1a2b3c-00000000"  # user_id=None
    await _seed_profile(
        session,
        instance_id=iid,
        pool_id=pid,
        group_id=group.id,
        profile_name=pn_user,
        user_id=end_user.id,
    )
    await _seed_profile(
        session,
        instance_id=iid,
        pool_id=pid,
        group_id=group.id,
        profile_name=pn_shared,
        user_id=None,
    )

    async def _sources(pool_id, pod_name):
        return {"engine": True, "profiles": [pn_user, pn_shared]}

    monkeypatch.setattr(rp.controller_client, "list_pod_log_sources", _sources)

    body = (await c.get(f"/api/manager/resource-pools/{pid}/pods/p1/logs/sources")).json()
    assert body["engine"] is True
    by_pn = {p["profile_name"]: p for p in body["profiles"]}
    assert by_pn[pn_user]["username"] == end_user.username
    assert by_pn[pn_user]["real_name"] == "张三"
    # user_id 为空的共享 profile → username/real_name 为 None
    assert by_pn[pn_shared]["username"] is None
    assert by_pn[pn_shared]["real_name"] is None

    # 清理：删除 seed 的用户（profile 随 instance 级联清理）
    await session.delete(end_user)
    await session.commit()


async def test_instance_pod_log_sources_enriched_with_user(client, monkeypatch):
    """实例维度日志来源同样富化用户信息。"""
    from app.api import agent_instances as ai
    from app.models import User

    c, _, group = client
    _, pid, iid = await _published_def_with_instance(c, group.id)

    end_user = User(
        username=f"eu_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
        real_name="李四",
    )
    db_override = app.dependency_overrides[get_db]
    session = await db_override().__anext__()
    session.add(end_user)
    await session.commit()
    await session.refresh(end_user)

    pn = "b2c3d4e5-2b3c4d-abcd1234"
    await _seed_profile(
        session,
        instance_id=iid,
        pool_id=pid,
        group_id=group.id,
        profile_name=pn,
        user_id=end_user.id,
    )

    async def _sources(pool_id, pod_name):
        return {"engine": True, "profiles": [pn]}

    monkeypatch.setattr(ai.controller_client, "list_pod_log_sources", _sources)

    body = (await c.get(f"/api/manager/agent-instances/{iid}/pods/p1/logs/sources")).json()
    by_pn = {p["profile_name"]: p for p in body["profiles"]}
    assert by_pn[pn]["username"] == end_user.username
    assert by_pn[pn]["real_name"] == "李四"

    await session.delete(end_user)
    await session.commit()


# =========================================
# LiteLLM Key 列表「所属智能体」名称富化
# =========================================


async def test_list_keys_enriched_with_agent_name(client, monkeypatch):
    """per-instance key 的 metadata.instance_id → agent_name；非 per-instance key 留空。"""
    from app.services import litellm_client

    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    inst_name = (await c.get(f"/api/manager/agent-instances/{iid}")).json()["name"]

    async def _list_keys(team_id=None):
        return [
            {
                "token": "k-instance",
                "key_alias": f"instance:{iid[:8]}",
                "metadata": {"instance_id": iid, "group_id": str(group.id)},
                "spend": "1.0",
            },
            {
                "token": "k-manual",
                "key_alias": "manual-key",
                "metadata": {"group_id": str(group.id)},
                "spend": "0",
            },
        ]

    monkeypatch.setattr(litellm_client, "list_keys", _list_keys)

    body = (await c.get("/api/manager/litellm/keys")).json()
    by_token = {k["token"]: k for k in body["items"]}
    assert by_token["k-instance"]["agent_name"] == inst_name
    assert by_token["k-instance"]["agent_id"] == iid
    # 手动创建的 key 无 instance_id → agent_name 留空
    assert by_token["k-manual"]["agent_name"] == ""
    assert by_token["k-manual"]["agent_id"] == ""


# =========================================
# agent_instance API Keys（OpenAI 兼容）
# =========================================


async def test_instance_api_keys_crud(client):
    """CRUD 全流程：create 返回明文 key + prefix；list 只返回 prefix；delete 204。"""
    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)

    # create
    resp = await c.post(
        f"/api/manager/agent-instances/{iid}/api-keys",
        json={"name": "prod"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "prod"
    assert body["key"].startswith("sk-")
    assert body["key_prefix"] == body["key"][:14]
    assert body["key_prefix"].startswith("sk-")
    kid = body["id"]
    plaintext = body["key"]

    # list（不含明文 key）
    listing = (await c.get(f"/api/manager/agent-instances/{iid}/api-keys")).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == kid
    assert item["name"] == "prod"
    assert item["key_prefix"] == body["key_prefix"]
    assert "key" not in item  # 列表绝不返回明文

    # delete
    assert (await c.delete(f"/api/manager/agent-instances/{iid}/api-keys/{kid}")).status_code == 204
    assert (await c.get(f"/api/manager/agent-instances/{iid}/api-keys")).json()["total"] == 0


async def test_instance_api_keys_max_10_limit(client):
    """每实例最多 10 个 Key，第 11 个 400。"""
    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    for i in range(10):
        r = await c.post(
            f"/api/manager/agent-instances/{iid}/api-keys",
            json={"name": f"k{i}"},
        )
        assert r.status_code == 201, f"第 {i} 个应成功: {r.text}"
    r = await c.post(
        f"/api/manager/agent-instances/{iid}/api-keys",
        json={"name": "k10"},
    )
    assert r.status_code == 400
    assert "10" in r.json()["detail"]


async def test_instance_api_keys_unique_name_per_instance(client):
    """同实例下 name 唯一（uq_instance_apikey_name），重名 400。"""
    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    await c.post(f"/api/manager/agent-instances/{iid}/api-keys", json={"name": "dup"})
    r = await c.post(f"/api/manager/agent-instances/{iid}/api-keys", json={"name": "dup"})
    assert r.status_code in (400, 409)  # UniqueViolation → 422/400/409，看 FastAPI 兜底


async def test_instance_api_keys_hash_differs_from_plaintext(client):
    """DB 存的 key_hash 不等于明文 key（HMAC-SHA256 不可逆）。"""
    from app.models import AgentApiKey
    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    r = await c.post(
        f"/api/manager/agent-instances/{iid}/api-keys",
        json={"name": "verify-test"},
    )
    plaintext = r.json()["key"]
    kid = r.json()["id"]

    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        result = await db.execute(sa_select(AgentApiKey).where(AgentApiKey.id == kid))
        key = result.scalar_one()
        assert key.key_hash != plaintext  # 不存明文
        assert len(key.key_hash) == 64  # SHA256 hex
        assert key.key_prefix == plaintext[:14]
    await engine.dispose()


async def test_instance_api_keys_delete_cascade_on_instance_delete(client):
    """删实例时 DB ondelete=CASCADE 自动清理 api_keys。"""
    from app.models import AgentApiKey
    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)
    r = await c.post(
        f"/api/manager/agent-instances/{iid}/api-keys",
        json={"name": "cascade-test"},
    )
    kid = r.json()["id"]

    # 删实例
    assert (await c.delete(f"/api/manager/agent-instances/{iid}")).status_code == 204

    # DB 里 api_key 应被 CASCADE 清理
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        result = await db.execute(sa_select(AgentApiKey).where(AgentApiKey.id == kid))
        assert result.scalar_one_or_none() is None
    await engine.dispose()


async def test_instance_api_keys_group_isolation(client):
    """非所属组的用户不能 list/create/delete keys。"""
    c, _, group = client
    _, _, iid = await _published_def_with_instance(c, group.id)

    # 模拟另一组用户：override get_current_group_ids 为空列表（无组权限）
    from app.core.group_scope import get_current_group_ids
    original = app.dependency_overrides.get(get_current_group_ids)
    app.dependency_overrides[get_current_group_ids] = lambda: []

    try:
        # list → 空列表（_require_instance 抛 404 因无组权限）
        r = await c.get(f"/api/manager/agent-instances/{iid}/api-keys")
        assert r.status_code == 404
        # create → 404
        r = await c.post(
            f"/api/manager/agent-instances/{iid}/api-keys",
            json={"name": "x"},
        )
        assert r.status_code == 404
    finally:
        if original is not None:
            app.dependency_overrides[get_current_group_ids] = original
        else:
            app.dependency_overrides.pop(get_current_group_ids, None)
