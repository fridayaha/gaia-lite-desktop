"""删用户时同步清理 profile 资源 + IM 绑定 — 真 DB 集成测试。

覆盖：
- delete_user 清理该用户所有 INDEPENDENT profile（DB 行删除 + k8s cleanup/port_map.remove 被调）
- delete_user 清理 im_user_bindings（解 RESTRICT）
- delete_user 无 profile 时正常删用户
- list_user_profiles / GET /api/manager/users/{id}/profiles 返回占用

遵循 CLAUDE.md：真 DB（unionagents_test）验证实际行删除，非 mock commit。
k8s exec 无法在测试中跑真集群，patch 为 no-op（teardown 的 DB 行删除才是断言重点）。
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock

from app.core.auth import get_current_user
from app.core.group_scope import get_current_group_ids
from app.main import app
from app.models import (
    AgentDefinition,
    AgentDeployment,
    AgentInstance,
    AgentProfile,
    DeploymentStatus,
    ImUserBinding,
    ResourcePool,
    User,
    UserGroup,
    user_group_members,
)
from app.services import litellm_client
from app.services.user_service import delete_user, list_user_profiles

from pkg.common.config import settings
from pkg.common.database import get_db

CLEANUP_TABLES = [
    "agent_profiles",
    "agent_deployments",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "im_user_bindings",
    "user_group_members",
    "user_groups",
    "users",
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
        for t in CLEANUP_TABLES:
            await session.execute(text(f"DELETE FROM {t}"))
        await session.commit()
        await session.close()
        await engine.dispose()


def _patch_k8s(monkeypatch):
    """teardown_profile 的 k8s 调用 patch 为 no-op，仅保留 DB 行为。"""
    import app.worker.profiles as profiles_mod

    monkeypatch.setattr(profiles_mod.k8s_manager, "exec_hermes_command", AsyncMock(return_value=""))
    monkeypatch.setattr(profiles_mod.k8s_manager, "update_nginx_config", AsyncMock())
    monkeypatch.setattr(profiles_mod, "_port_map_all", AsyncMock(return_value={}))


async def _seed_world(session, *, actor: User):
    """建 group/definition/instance/pool（actor 为 created_by），返回所需 id。"""
    group = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(group)
    await session.flush()
    group.litellm_team_id = str(group.id)

    definition = AgentDefinition(
        group_id=group.id,
        name=f"d_{uuid.uuid4().hex[:8]}",
        created_by=actor.id,
    )
    session.add(definition)
    await session.flush()

    instance = AgentInstance(
        group_id=group.id,
        name=f"i_{uuid.uuid4().hex[:8]}",
        definition_id=definition.id,
        created_by=actor.id,
    )
    session.add(instance)
    await session.flush()

    pool = ResourcePool(
        name=f"p_{uuid.uuid4().hex[:8]}",
        group_id=group.id,
        created_by=actor.id,
    )
    session.add(pool)
    await session.flush()

    deployment = AgentDeployment(
        instance_id=instance.id,
        group_id=group.id,
        resource_pool_id=pool.id,
        status=DeploymentStatus.RUNNING,
        scope_type="ALL",
        pod_name="engine-hermes-x",
    )
    session.add(deployment)
    await session.flush()
    await session.commit()
    return group, definition, instance, pool, deployment


async def _seed_profile(session, *, instance, pool, deployment, group, user, profile_name):
    prof = AgentProfile(
        instance_id=instance.id,
        resource_pool_id=pool.id,
        deployment_id=deployment.id,
        profile_name=profile_name,
        profile_type="INDEPENDENT",
        user_id=user.id,
        group_id=group.id,
        hermes_home=f"/opt/data/profiles/{profile_name}",
        internal_port=8644,
    )
    session.add(prof)
    await session.commit()
    return prof


def _make_user(username=None, real_name=None) -> User:
    return User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
        real_name=real_name,
    )


# ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_user_cleans_profiles_and_bindings(db, monkeypatch):
    """删用户：profile DB 行删 + k8s cleanup/port_map.remove 被调 + IM 绑定删 + 用户删。"""
    _patch_k8s(monkeypatch)
    import app.worker.profiles as profiles_mod

    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    target = _make_user(real_name="张三")
    db.add(target)
    await db.flush()
    await db.execute(user_group_members.insert().values(user_id=target.id, group_id=group.id))
    await db.commit()

    prof = await _seed_profile(
        db,
        instance=instance,
        pool=pool,
        deployment=deployment,
        group=group,
        user=target,
        profile_name=f"{str(instance.id).replace('-', '')[:8]}-abc123-{str(target.id).replace('-', '')[:8]}",
    )

    binding = ImUserBinding(
        user_id=target.id, channel_type="wecom", im_user_id="wecom_001", im_user_name="张三"
    )
    db.add(binding)
    await db.commit()

    deleted = await delete_user(db, target.id, actor_id=actor.id)
    assert deleted is True

    # profile DB 行已删
    remain = await db.execute(
        text("SELECT count(*) FROM agent_profiles WHERE id = :pid"), {"pid": str(prof.id)}
    )
    assert remain.scalar() == 0
    # IM 绑定已删（验证 RESTRICT 已解）
    remain_bind = await db.execute(
        text("SELECT count(*) FROM im_user_bindings WHERE user_id = :uid"), {"uid": str(target.id)}
    )
    assert remain_bind.scalar() == 0
    # 用户已删
    remain_user = await db.execute(
        text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": str(target.id)}
    )
    assert remain_user.scalar() == 0

    # k8s：cleanup + port_map remove 命令被调
    all_cmds = []
    for call in profiles_mod.k8s_manager.exec_hermes_command.call_args_list:
        all_cmds.extend(call.kwargs.get("commands", []))
    assert any("profile_isolation.py cleanup" in c for c in all_cmds), all_cmds
    assert any("port_map.py remove" in c for c in all_cmds), all_cmds


@pytest.mark.asyncio
async def test_teardown_stops_gateway_via_marker_not_kill(db, monkeypatch):
    """teardown 用 planned_stop_marker 停 gateway（绕过 aegis kill 拦截），不再用 kill $PID。

    验证 exec 命令结构：停 gateway 命令含 _ua_stop_gw.py + write_planned_stop_marker +
    setuid 降权（marker 文件 0600 须同 uid 持有），且不再含旧的 `kill "$PID"` / grep pid 模式。
    （脚本本身的端到端效果由云上真 Pod 验证，这里断言命令结构正确。）
    """
    _patch_k8s(monkeypatch)
    import app.worker.profiles as profiles_mod

    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    target = _make_user()
    db.add(target)
    await db.commit()

    prof = await _seed_profile(
        db,
        instance=instance,
        pool=pool,
        deployment=deployment,
        group=group,
        user=target,
        profile_name=f"{str(instance.id).replace('-', '')[:8]}-xyz789-{str(target.id).replace('-', '')[:8]}",
    )

    await delete_user(db, target.id, actor_id=actor.id)

    all_cmds = []
    for call in profiles_mod.k8s_manager.exec_hermes_command.call_args_list:
        all_cmds.extend(call.kwargs.get("commands", []))
    # 找到停 gateway 的那条命令（含 _ua_stop_gw.py）
    stop_cmd = next((c for c in all_cmds if "_ua_stop_gw.py" in c), None)
    assert stop_cmd is not None, f"停 gateway 命令缺失: {all_cmds}"
    # 走 marker 自退路径
    assert "write_planned_stop_marker" in stop_cmd
    assert "setuid" in stop_cmd  # 降权到 profile uid 写 marker（0600 同 uid 可读）
    assert "gateway_pids_for_uid" in stop_cmd  # 按 uid+cmdline 反查真 PID（不读 gateway.pid）
    # 不再用旧的 kill $PID / grep pid 模式
    assert "kill \"$PID\"" not in stop_cmd
    assert "grep -oE '[0-9]+'" not in stop_cmd
    # profile 名作为参数传入
    assert prof.profile_name in stop_cmd
    # 后续清理命令仍在
    assert any("profile_isolation.py cleanup" in c for c in all_cmds)
    assert any("port_map.py remove" in c for c in all_cmds)


@pytest.mark.asyncio
async def test_teardown_profile_deletes_browser_pod_and_preserves_others(db, monkeypatch):
    """teardown_profile：删本 profile 的 browser Pod + 清 port_map 记录，但保留其他 profile 的。

    回归：此前 internal_port_map 整体覆盖 `{"profiles": ...}` 把 browsers 键冲掉，下方删除块
    永远 False → browser Pod 永不删 + DB 记录丢失（SUSPEND/DESTROY 也回收不了）。
    """
    _patch_k8s(monkeypatch)
    import app.worker.profiles as profiles_mod
    from app.worker.profiles import teardown_profile

    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    target = _make_user()
    db.add(target)
    await db.flush()
    await db.execute(user_group_members.insert().values(user_id=target.id, group_id=group.id))
    await db.commit()

    pn = f"{str(instance.id).replace('-', '')[:8]}-brwsr01-{str(target.id).replace('-', '')[:8]}"
    prof = await _seed_profile(
        db, instance=instance, pool=pool, deployment=deployment, group=group,
        user=target, profile_name=pn,
    )

    # deployment 已带 browsers 记录：本 profile + 另一 profile（应保留）
    other_pn = "other-profile-hash-other"
    deployment.internal_port_map = {
        "profiles": {pn: 8644},
        "browsers": {
            pn: {"pod": "browser-x", "vnc_pw": "pw-x"},
            other_pn: {"pod": "browser-y", "vnc_pw": "pw-y"},
        },
    }
    db.add(deployment)
    await db.commit()

    del_mock = AsyncMock()
    monkeypatch.setattr(profiles_mod.k8s_manager, "delete_browser_pod", del_mock)

    await teardown_profile(db, prof)

    # browser Pod 被删（用 instance_id + profile_name）
    del_mock.assert_awaited_once_with(str(instance.id), pn)

    # 重新读 deployment：本 profile 的 browser 记录已清，另一 profile 的保留
    refreshed = await db.execute(
        text("SELECT internal_port_map FROM agent_deployments WHERE id = :did"),
        {"did": str(deployment.id)},
    )
    port_map = refreshed.scalar()
    browsers = (port_map or {}).get("browsers") or {}
    assert pn not in browsers, f"被删 profile 的 browser 记录应清掉: {browsers}"
    assert browsers.get(other_pn) == {"pod": "browser-y", "vnc_pw": "pw-y"}, browsers
    # profiles 段仍在（不被 browser 逻辑破坏）
    assert "profiles" in (port_map or {})


@pytest.mark.asyncio
async def test_delete_user_without_profiles(db, monkeypatch):
    """无 profile 用户：不调 k8s teardown，正常删用户 + 删 IM 绑定。"""
    _patch_k8s(monkeypatch)
    import app.worker.profiles as profiles_mod

    actor = _make_user()
    db.add(actor)
    await db.commit()

    target = _make_user()
    db.add(target)
    await db.flush()
    binding = ImUserBinding(
        user_id=target.id, channel_type="feishu", im_user_id="f_001"
    )
    db.add(binding)
    await db.commit()

    deleted = await delete_user(db, target.id, actor_id=actor.id)
    assert deleted is True

    remain_user = await db.execute(
        text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": str(target.id)}
    )
    assert remain_user.scalar() == 0
    remain_bind = await db.execute(
        text("SELECT count(*) FROM im_user_bindings WHERE user_id = :uid"), {"uid": str(target.id)}
    )
    assert remain_bind.scalar() == 0
    # 无 profile → 不应调 k8s exec
    profiles_mod.k8s_manager.exec_hermes_command.assert_not_called()


@pytest.mark.asyncio
async def test_list_user_profiles(db):
    """list_user_profiles 返回用户 profile 占用（含 instance name）。"""
    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    target = _make_user()
    db.add(target)
    await db.commit()
    await _seed_profile(
        db,
        instance=instance,
        pool=pool,
        deployment=deployment,
        group=group,
        user=target,
        profile_name="pn-list-test",
    )

    items, total = await list_user_profiles(db, target.id)
    assert total == 1
    assert items[0]["instance_name"] == instance.name
    assert items[0]["profile_name"] == "pn-list-test"
    assert items[0]["instance_id"] == str(instance.id)


@pytest.mark.asyncio
async def test_get_user_profiles_endpoint(db, monkeypatch):
    """GET /api/manager/users/{id}/profiles 返回 count + items。"""
    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    target = _make_user()
    db.add(target)
    await db.commit()
    await _seed_profile(
        db,
        instance=instance,
        pool=pool,
        deployment=deployment,
        group=group,
        user=target,
        profile_name="pn-api-test",
    )

    # 请求用独立 session：ASGITransport 的 BaseHTTPMiddleware 会把请求派发到新 task，
    # 与测试直用的 session 共享会触发 asyncpg MissingGreenlet。
    req_engine = create_async_engine(settings.test_database_url)
    req_factory = async_sessionmaker(req_engine, class_=AsyncSession, expire_on_commit=False)
    req_session = req_factory()

    async def _get_db():
        yield req_session

    # 鉴权：直接 monkeypatch is_platform_admin → True，避免读 user.roles（actor 绑在
    # fixture session，请求 task 内 lazy-load 触发 MissingGreenlet）；同时防其它测试
    # 泄漏 is_platform_admin=False 污染本测试。get_current_user 返回任意对象即可。
    from app.core import auth as _auth
    from types import SimpleNamespace

    monkeypatch.setattr(_auth, "is_platform_admin", lambda _u: True)
    admin = SimpleNamespace(id=actor.id, username="admin")

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_current_group_ids] = lambda: None
    orig = litellm_client.ensure_user
    litellm_client.ensure_user = AsyncMock(return_value={})

    try:
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        resp = await c.get(f"/api/manager/users/{target.id}/profiles")
        await c.aclose()
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["profile_name"] == "pn-api-test"
    finally:
        app.dependency_overrides.clear()
        litellm_client.ensure_user = orig
        await req_session.close()
        await req_engine.dispose()


@pytest.mark.asyncio
async def test_do_create_profile_deleted_user_returns_404(db, monkeypatch):
    """_do_create_profile 对不存在/已删 user_id → HTTPException 404。

    删用户后其 INDEPENDENT profile 已被 teardown 清理；gateway 再 ensure 重建时
    user_id 不在 users 表 → FK 违约 → manager 返回 404（让 gateway 硬拒绝不回退）。
    真 DB 验证 FK 约束 + 404 翻译。
    """
    import app.worker.profiles as profiles_mod
    from fastapi import HTTPException

    _patch_k8s(monkeypatch)
    actor = _make_user()
    db.add(actor)
    await db.flush()
    group, definition, instance, pool, deployment = await _seed_world(db, actor=actor)

    # 跳过 k8s/资源查询，直接给 deployment + port，让 insert 跑真 DB
    monkeypatch.setattr(
        profiles_mod, "_select_pod_by_load", AsyncMock(return_value=deployment)
    )
    monkeypatch.setattr(profiles_mod, "_port_map_alloc", AsyncMock(return_value=8644))
    monkeypatch.setattr(
        profiles_mod,
        "_load_resource_spec",
        AsyncMock(return_value={"max_profiles_per_pod": 20}),
    )

    req = profiles_mod.CreateProfileRequest(
        agent_id=str(instance.id),
        engine_instance_id=str(pool.id),
        user_id=str(uuid.uuid4()),  # 不存在的 user_id
        group_id=str(group.id),
        profile_type="INDEPENDENT",
        profile_name="pn-deleted-user",
    )
    with pytest.raises(HTTPException) as exc:
        await profiles_mod._do_create_profile(req, db)
    assert exc.value.status_code == 404
    assert "user not found" in exc.value.detail
    # 不应建出 profile 行
    remain = await db.execute(
        text("SELECT count(*) FROM agent_profiles WHERE profile_name = 'pn-deleted-user'")
    )
    assert remain.scalar() == 0
