"""engine_rollout_service 单测 — 真 DB 断言 EngineRollout/Item 写入（非 mock commit）。

覆盖：
- list_candidates：排除 ARCHIVED + DIFY 外部实例
- create_rollout(dry_run=True)：只返回预览，不落库
- create_rollout(dry_run=False) + run_rollout：分批处理，状态分类正确
  （RUNNING→READY / SUSPENDED→PATCHED / 镜像已是目标→SKIPPED），summary 正确

k8s_manager 的 K8s 调用必须 mock（无集群），但断言走真 DB（CLAUDE.md 反模式：禁止
mock commit 不断言写入）。
"""

import uuid

import pytest
import pytest_asyncio
from app.models import (
    AgentDefinition,
    AgentDeployment,
    AgentInstance,
    DeploymentStatus,
    EngineRollout,
    EngineRolloutItem,
    EngineType,
    RolloutItemStatus,
    RolloutStatus,
    User,
    UserGroup,
)
from app.services import engine_rollout_service as svc
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

TARGET_IMAGE = "registry.example.com/engine-hermes-v2:0.9.0"
OLD_IMAGE = "registry.example.com/engine-hermes-v2:0.8.16"

ROLLOUT_TABLES = ["engine_rollout_items", "engine_rollouts"]
V3_TABLES = [
    "agent_deployments",
    "agent_instances",
    "agent_definitions",
    "user_group_members",
    "user_groups",
]


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    # run_rollout / _patch_under_lock 用 svc.async_session 开独立会话；
    # 测试需指向测试库（否则走 dev unionagents 库，新表不存在）。
    monkeypatch.setattr(svc, "async_session", factory)

    user = User(
        username=f"ro_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

    for t in ROLLOUT_TABLES + V3_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def group(db):
    session, _ = db
    g = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(g)
    await session.commit()
    await session.refresh(g)
    return g


async def _make_engine(
    db,
    group,
    *,
    engine_type=EngineType.HERMES,
    status=DeploymentStatus.RUNNING,
    engine_url=None,
    prev_image=OLD_IMAGE,
):
    """建一组 definition→instance→deployment，返回 (dep, agent_id)。"""
    session, user = db
    d = AgentDefinition(
        name=f"d_{uuid.uuid4().hex[:6]}",
        group_id=group.id,
        engine_type=engine_type,
        created_by=user.id,
    )
    session.add(d)
    await session.flush()
    inst = AgentInstance(
        name=f"i_{uuid.uuid4().hex[:6]}",
        group_id=group.id,
        definition_id=d.id,
        created_by=user.id,
    )
    session.add(inst)
    await session.flush()
    dep = AgentDeployment(
        instance_id=inst.id,
        group_id=group.id,
        status=status,
        scope_type="ALL",
        engine_url=engine_url,
    )
    session.add(dep)
    await session.commit()
    await session.refresh(dep)
    return dep, str(dep.instance_id), prev_image


@pytest.fixture
def mock_k8s(monkeypatch):
    """mock k8s_manager 的 K8s 调用。按 deployment_name 返回预设 prev_image。

    返回 (mgr, prev_map, patch_calls, wait_calls) 供断言。
    """
    prev_map: dict[str, str] = {}
    patch_calls: list[tuple] = []
    wait_calls: list[tuple] = []

    def _read(name):
        return prev_map.get(name)

    def _patch(name, target, force_repull=False):
        patch_calls.append((name, target, force_repull))
        return prev_map.get(name)

    def _wait(name, target, timeout=300):
        # 同步：wait_deployment_ready 现为同步方法，service 用 asyncio.to_thread 调用
        wait_calls.append((name, target))
        return True

    mgr = svc.k8s_manager
    monkeypatch.setattr(mgr, "read_engine_image", _read)
    monkeypatch.setattr(mgr, "patch_engine_image", _patch)
    monkeypatch.setattr(mgr, "wait_deployment_ready", _wait)
    return mgr, prev_map, patch_calls, wait_calls


@pytest.fixture
def no_auto_task(monkeypatch):
    """禁止 create_rollout 自动起后台任务，测试显式 await run_rollout。"""
    monkeypatch.setattr(svc, "_autolaunch", False)


# ── list_candidates：排除 ARCHIVED + DIFY 外部 ──────────────────


@pytest.mark.asyncio
async def test_list_candidates_excludes_archived_and_external_dify(db, group, mock_k8s):
    _, prev_map, _, _ = mock_k8s
    dep_run, aid_run, _ = await _make_engine(db, group, status=DeploymentStatus.RUNNING)
    await _make_engine(db, group, status=DeploymentStatus.ARCHIVED)
    await _make_engine(
        db, group, engine_type=EngineType.DIFY, engine_url="https://external.example.com"
    )
    from app.worker.k8s_manager import _engine_name

    prev_map[_engine_name(aid_run, "ALL", None)] = OLD_IMAGE

    session, _ = db
    cands = await svc.list_candidates(session, engine_type=None)
    agent_ids = [aid for _, aid, _ in cands]
    assert aid_run in agent_ids
    assert len(cands) == 1  # ARCHIVED + DIFY 外部都被排除


# ── dry_run：只预览不落库 ─────────────────────────────────


@pytest.mark.asyncio
async def test_create_rollout_requires_engine_type(db, group, mock_k8s):
    """engine_type=None 必须拒绝：不同类型镜像不同，无法用单一 target_image 跨类型滚动。"""
    session, _ = db
    with pytest.raises(ValueError):
        await svc.create_rollout(
            session, engine_type=None, target_image=TARGET_IMAGE, dry_run=True
        )


@pytest.mark.asyncio
async def test_create_rollout_dry_run_no_db_rows(db, group, mock_k8s):
    _, prev_map, _, _ = mock_k8s
    dep_run, aid_run, _ = await _make_engine(db, group, status=DeploymentStatus.RUNNING)
    dep_sus, aid_sus, _ = await _make_engine(db, group, status=DeploymentStatus.SUSPENDED)
    from app.worker.k8s_manager import _engine_name

    prev_map[_engine_name(aid_run, "ALL", None)] = OLD_IMAGE
    prev_map[_engine_name(aid_sus, "ALL", None)] = OLD_IMAGE

    session, _ = db
    result = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, dry_run=True
    )
    assert result["dry_run"] is True
    assert result["target_image"] == TARGET_IMAGE
    assert result["running"] == 1
    assert result["suspended"] == 1
    assert result["total"] == 2

    # 不落库
    rows = (await session.execute(select(EngineRollout))).scalars().all()
    assert rows == []


# ── create_rollout + run_rollout：状态分类 + 真 DB 写入 ─────────


@pytest.mark.asyncio
async def test_run_rollout_classifies_by_status(db, group, mock_k8s, no_auto_task):
    _, prev_map, patch_calls, wait_calls = mock_k8s
    # RUNNING（旧镜像）→ READY
    _, aid_run, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=OLD_IMAGE
    )
    # SUSPENDED → PATCHED（只 patch 不等 ready）
    _, aid_sus, _ = await _make_engine(
        db, group, status=DeploymentStatus.SUSPENDED, prev_image=OLD_IMAGE
    )
    # RUNNING 但镜像已是目标 → SKIPPED
    _, aid_skip, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=TARGET_IMAGE
    )
    from app.worker.k8s_manager import _engine_name

    name_run = _engine_name(aid_run, "ALL", None)
    name_sus = _engine_name(aid_sus, "ALL", None)
    name_skip = _engine_name(aid_skip, "ALL", None)
    prev_map[name_run] = OLD_IMAGE
    prev_map[name_sus] = OLD_IMAGE
    prev_map[name_skip] = TARGET_IMAGE

    session, _ = db
    created = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, batch_size=5
    )
    rollout_id = created["rollout_id"]
    assert created["total"] == 3

    # 显式跑（no_auto_task 已禁止自动起）
    await svc.run_rollout(rollout_id)

    # 真 DB 断言
    rollout = (
        await session.execute(
            select(EngineRollout).where(EngineRollout.id == uuid.UUID(rollout_id))
        )
    ).scalar_one()
    assert rollout.status == RolloutStatus.FINISHED
    assert rollout.summary == {"total": 3, "ready": 1, "patched": 1, "failed": 0, "skipped": 1}

    items = (
        (
            await session.execute(
                select(EngineRolloutItem).where(EngineRolloutItem.rollout_id == rollout.id)
            )
        )
        .scalars()
        .all()
    )
    by_name = {it.deployment_name: it for it in items}
    assert by_name[name_run].status == RolloutItemStatus.READY
    assert by_name[name_sus].status == RolloutItemStatus.PATCHED
    assert by_name[name_skip].status == RolloutItemStatus.SKIPPED

    # patch 调用：RUNNING + SUSPENDED 被 patch；SKIPPED 不 patch
    patched_names = {c[0] for c in patch_calls}
    assert name_run in patched_names
    assert name_sus in patched_names
    assert name_skip not in patched_names
    # wait 只对 RUNNING（含 skip？skip 在 patch 前已 return，不 wait）
    waited_names = {c[0] for c in wait_calls}
    assert name_run in waited_names
    assert name_sus not in waited_names  # SUSPENDED 不等 ready
    assert name_skip not in waited_names


@pytest.mark.asyncio
async def test_run_rollout_wait_timeout_marks_failed(db, group, mock_k8s, no_auto_task, monkeypatch):
    """wait_deployment_ready 返回 False → item FAILED，rollout 仍 FINISHED（best-effort）。"""
    _, prev_map, _, wait_calls = mock_k8s
    dep_run, aid_run, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=OLD_IMAGE
    )
    from app.worker.k8s_manager import _engine_name

    name_run = _engine_name(aid_run, "ALL", None)
    prev_map[name_run] = OLD_IMAGE

    # 覆盖 wait 返回 False（同步：wait_deployment_ready 经 asyncio.to_thread 调用）
    def _wait_fail(name, target, timeout=300):
        return False

    monkeypatch.setattr(svc.k8s_manager, "wait_deployment_ready", _wait_fail)

    session, _ = db
    created = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, batch_size=5
    )
    await svc.run_rollout(created["rollout_id"])

    rollout = (await session.execute(select(EngineRollout))).scalar_one()
    assert rollout.summary["failed"] == 1
    assert rollout.summary["ready"] == 0
    # 全失败但 total>0 → FAILED
    assert rollout.status == RolloutStatus.FAILED
    item = (await session.execute(select(EngineRolloutItem))).scalar_one()
    assert item.status == RolloutItemStatus.FAILED
    assert item.error  # 有错误说明


@pytest.mark.asyncio
async def test_get_rollout_and_list_rollouts(db, group, mock_k8s, no_auto_task):
    _, prev_map, _, _ = mock_k8s
    _, aid_run, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=OLD_IMAGE
    )
    from app.worker.k8s_manager import _engine_name

    prev_map[_engine_name(aid_run, "ALL", None)] = OLD_IMAGE
    session, _ = db
    created = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, batch_size=5
    )
    await svc.run_rollout(created["rollout_id"])

    got = await svc.get_rollout(session, uuid.UUID(created["rollout_id"]))
    assert got["status"] == RolloutStatus.FINISHED.value
    assert got["target_image"] == TARGET_IMAGE
    assert len(got["items"]) == 1
    assert got["items"][0]["status"] == RolloutItemStatus.READY.value

    listed = await svc.list_rollouts(session)
    assert len(listed) == 1
    assert listed[0]["rollout_id"] == created["rollout_id"]


@pytest.mark.asyncio
async def test_run_rollout_summary_shape_stable(db, group, mock_k8s, no_auto_task):
    """summary 持久化结构在 create→run 之间不漂移：恒为 5-key 规范结构。"""
    _, prev_map, _, _ = mock_k8s
    _, aid_run, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=OLD_IMAGE
    )
    from app.worker.k8s_manager import _engine_name

    prev_map[_engine_name(aid_run, "ALL", None)] = OLD_IMAGE
    session, _ = db
    created = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, batch_size=5
    )

    # create 后即落库的 summary 已是规范 5-key 结构
    before = (await session.execute(select(EngineRollout))).scalar_one()
    assert set(before.summary.keys()) == set(svc.SUMMARY_KEYS)

    await svc.run_rollout(created["rollout_id"])
    # run_rollout 用独立会话提交，测试会话 identity map 缓存了 before 实例，需失效后重查
    session.expire_all()

    after = (await session.execute(select(EngineRollout))).scalar_one()
    assert set(after.summary.keys()) == set(svc.SUMMARY_KEYS)
    assert after.summary == {"total": 1, "ready": 1, "patched": 0, "failed": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_interrupt_stale_rollouts(db, group, mock_k8s, no_auto_task):
    """进程重启后残留 RUNNING 的 rollout 应被启动清理标为 FAILED + interrupted。"""
    _, prev_map, _, _ = mock_k8s
    _, aid_run, _ = await _make_engine(
        db, group, status=DeploymentStatus.RUNNING, prev_image=OLD_IMAGE
    )
    from app.worker.k8s_manager import _engine_name

    prev_map[_engine_name(aid_run, "ALL", None)] = OLD_IMAGE
    session, _ = db
    created = await svc.create_rollout(
        session, engine_type="HERMES", target_image=TARGET_IMAGE, batch_size=5
    )
    # 不跑 run_rollout，模拟进程在 RUNNING 中途重启
    rollout = (await session.execute(select(EngineRollout))).scalar_one()
    assert rollout.status == RolloutStatus.RUNNING

    n = await svc.interrupt_stale_rollouts(session)
    assert n == 1

    await session.refresh(rollout)
    assert rollout.status == RolloutStatus.FAILED
    assert rollout.finished_at is not None
    assert rollout.summary.get("interrupted") is True
