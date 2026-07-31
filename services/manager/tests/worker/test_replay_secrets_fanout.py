"""skill secrets.enc 同步单测。

验证 _fanout_skill_to_pods 装 skill（rm -rf 擦除）后回写 secrets.enc，以及
_replay_skill_secrets helper / reconcile 端点的行为——修"install/rebind 后 Pod 有
skill 文件但缺 secrets.enc → sidecar 404 → skill auth_fail"。

真 DB（AgentDefinition + SkillCredential）断言查询条件；k8s exec / write_skill_secrets
全 mock（避免 k8s exec）。
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from app.core.crypto import encrypt_credentials_dict
from app.models import AgentDefinition, SkillCredential, User, UserGroup, user_group_members
from app.worker.config_skills import (
    _fanout_skill_to_pods,
    _replay_skill_secrets,
    reconcile_skill_secrets,
    replay_persona_and_skills,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

SKILL_NAME = "customer-profile-update"


@pytest_asyncio.fixture
async def db():
    """真 DB：User + UserGroup + AgentDefinition + SkillCredential。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    group = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add_all([user, group])
    await session.flush()
    group.litellm_team_id = str(group.id)
    await session.execute(user_group_members.insert().values(user_id=user.id, group_id=group.id))

    definition = AgentDefinition(
        group_id=group.id,
        name=f"d_{uuid.uuid4().hex[:8]}",
        engine_type="HERMES",
        skill_config={"skills": [], "order": []},
        created_by=user.id,
    )
    session.add(definition)
    await session.flush()

    cred = SkillCredential(
        definition_id=definition.id,
        skill_name=SKILL_NAME,
        scope_type="ALL",
        credentials_encrypted=encrypt_credentials_dict({"api_key": "sk-test-secret"}),
        created_by=user.id,
    )
    session.add(cred)
    await session.commit()

    yield session, definition, cred

    await session.execute(
        text("DELETE FROM skill_credentials WHERE definition_id = :d"), {"d": str(definition.id)}
    )
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    await session.execute(
        text("DELETE FROM agent_definitions WHERE id = :d"), {"d": str(definition.id)}
    )
    await session.execute(
        text("DELETE FROM user_group_members WHERE user_id = :u"), {"u": str(user.id)}
    )
    await session.delete(user)
    await session.delete(group)
    await session.commit()
    await session.close()
    await engine.dispose()


# ── _replay_skill_secrets helper（核心新增逻辑）──────────────────────────


class TestReplaySkillSecrets:
    async def test_writes_when_credential_exists(self, db):
        """有 SkillCredential → 调 write_skill_secrets fan-out secrets.enc。"""
        session, definition, cred = db
        with patch("app.worker.config_skills.write_skill_secrets", new=AsyncMock()) as mock_write:
            await _replay_skill_secrets("agent-1", SKILL_NAME, str(definition.id), session)
            mock_write.assert_awaited_once()
            req = mock_write.call_args.args[1]
            assert req.skill_name == SKILL_NAME
            assert req.credentials_encrypted == cred.credentials_encrypted

    async def test_skip_when_no_credential(self, db):
        """无 SkillCredential → 不调 write_skill_secrets。"""
        session, definition, cred = db
        await session.delete(cred)
        await session.commit()
        with patch("app.worker.config_skills.write_skill_secrets", new=AsyncMock()) as mock_write:
            await _replay_skill_secrets("agent-1", SKILL_NAME, str(definition.id), session)
            mock_write.assert_not_awaited()

    async def test_failure_does_not_raise(self, db):
        """write_skill_secrets 抛异常 → _replay_skill_secrets 不抛（best-effort）。"""
        session, definition, cred = db
        with patch("app.worker.config_skills.write_skill_secrets", new=AsyncMock()) as mock_write:
            mock_write.side_effect = RuntimeError("k8s exec down")
            # 不应抛
            await _replay_skill_secrets("agent-1", SKILL_NAME, str(definition.id), session)
            mock_write.assert_awaited_once()


# ── _fanout_skill_to_pods tar 后回写 secrets（修 rm -rf 擦除缺口）──────────


class TestFanoutRewritesSecrets:
    """_fanout 的 rm -rf 会擦掉 secrets.enc；验证 tar 后调 _replay_skill_secrets 回写。"""

    @pytest_asyncio.fixture
    async def fanout_patches(self, db):
        """mock _fanout 的 k8s exec 等依赖，返回 _replay_skill_secrets mock 供断言。"""
        session, definition, _cred = db
        stack = [
            patch(
                "app.worker.config_skills._load_agent_configs",
                new=AsyncMock(return_value=(None, None, str(definition.id))),
            ),
            patch(
                "app.worker.config_skills._iter_agent_target_pods",
                new=AsyncMock(return_value=[{"pod_name": "p1", "homes": ["/h1"]}]),
            ),
            patch("app.worker.config_skills._ensure_shared_skill_dir", new=AsyncMock()),
            patch("app.worker.config_skills.k8s_manager.exec_command_in_pod", new=AsyncMock()),
            patch("app.worker.config_skills.k8s_manager.exec_untar_to_in_pod", new=AsyncMock()),
            patch("app.worker.config_skills._zip_to_tar_strip_top", return_value=b"tar-bytes"),
            patch("app.worker.config_skills._regen_homes_config", new=AsyncMock()),
            patch("app.worker.config_skills._replay_skill_secrets", new=AsyncMock()),
            # 新 base（skill-config 变量替换）_fanout 调 _load_definition_skill_record，需 mock
            patch(
                "app.worker.config_skills._load_definition_skill_record",
                new=AsyncMock(return_value={}),
            ),
        ]
        ctxs = [s.__enter__() for s in stack]
        # ctxs[7] = _replay_skill_secrets mock
        yield ctxs[7], stack
        for s in reversed(stack):
            s.__exit__(None, None, None)

    async def test_fanout_calls_replay_secrets_after_tar(self, fanout_patches):
        """tar 完成（rm -rf 已擦除）后必须调 _replay_skill_secrets 回写。"""
        mock_replay, _ = fanout_patches
        written = await _fanout_skill_to_pods("agent-1", SKILL_NAME, b"zip-bytes", None)
        assert written == 1  # 1 个 pod 写入成功
        mock_replay.assert_awaited_once()
        assert mock_replay.call_args.args[0] == "agent-1"
        assert mock_replay.call_args.args[1] == SKILL_NAME

    async def test_fanout_replay_secrets_best_effort(self, fanout_patches):
        """_replay_skill_secrets 抛异常不影响 _fanout 返回（best-effort）。"""
        mock_replay, _ = fanout_patches
        mock_replay.side_effect = RuntimeError("secret write failed")
        # 不应抛
        written = await _fanout_skill_to_pods("agent-1", SKILL_NAME, b"zip-bytes", None)
        assert written == 1


# ── reconcile_skill_secrets 端点（pod 启动兜底，现已委托全链 reconcile_skills）──


class TestReconcileSkillSecretsEndpoint:
    async def test_delegates_to_reconcile_skills(self, db):
        """reconcile_skill_secrets 端点现已委托给全链 reconcile_skills（保留旧 URL，无需引擎镜像 bump）。"""
        session, definition, _cred = db
        with (
            patch(
                "app.worker.config_skills._load_agent_configs",
                new=AsyncMock(return_value=(None, None, str(definition.id))),
            ),
            patch(
                "app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=[])
            ),
            patch("app.worker.config_skills.archiver") as archiver,
        ):
            archiver.list_skill_zips.return_value = []
            result = await reconcile_skill_secrets("agent-1", session)
            assert result["agent_id"] == "agent-1"
            assert result["pods_scanned"] == 0
            assert result["drift"] is False

    async def test_no_definition_id(self, db):
        """无 definition_id → ok=False。"""
        session, _, _ = db
        with patch(
            "app.worker.config_skills._load_agent_configs", new=AsyncMock(return_value=None)
        ):
            result = await reconcile_skill_secrets("agent-1", session)
            assert result["ok"] is False


# ── 原子换入 fan-out（修 rm -rf 先删后 tar 失败留空目录的坑）──────────────


class TestFanoutAtomicSwap:
    """_fanout_skill_to_pods 先解压到 {dest}.new.{uuid} 再原子 mv；失败只清理 temp，旧版保留。"""

    @pytest_asyncio.fixture
    async def patches(self, db):
        session, definition, _cred = db
        exec_mock = AsyncMock()
        untar_mock = AsyncMock()
        stack = [
            patch(
                "app.worker.config_skills._load_agent_configs",
                new=AsyncMock(return_value=(None, None, str(definition.id))),
            ),
            patch(
                "app.worker.config_skills._iter_agent_target_pods",
                new=AsyncMock(return_value=[{"pod_name": "p1", "homes": ["/h1"]}]),
            ),
            patch("app.worker.config_skills._ensure_shared_skill_dir", new=AsyncMock()),
            patch("app.worker.config_skills.k8s_manager.exec_command_in_pod", new=exec_mock),
            patch("app.worker.config_skills.k8s_manager.exec_untar_to_in_pod", new=untar_mock),
            patch("app.worker.config_skills._zip_to_tar_strip_top", return_value=b"tar-bytes"),
            patch("app.worker.config_skills._regen_homes_config", new=AsyncMock()),
            patch("app.worker.config_skills._replay_skill_secrets", new=AsyncMock()),
            patch(
                "app.worker.config_skills._load_definition_skill_record",
                new=AsyncMock(return_value={}),
            ),
        ]
        for s in stack:
            s.__enter__()
        yield {"exec": exec_mock, "untar": untar_mock}
        for s in reversed(stack):
            s.__exit__(None, None, None)

    async def test_atomic_swap_success(self, patches):
        """成功：exec_command_in_pod 收到 rm -rf {dest} && mv {dest_new} {dest}。"""
        written = await _fanout_skill_to_pods("agent-1", SKILL_NAME, b"zip-bytes", None)
        assert written == 1
        cmds = [c.args[1][0] for c in patches["exec"].call_args_list]
        assert any("mv " in c and ".new." in c for c in cmds), cmds

    async def test_atomic_swap_preserves_old_on_failure(self, patches):
        """untar 失败：清理 {dest_new}，不执行 mv，旧 {dest} 保留，written=0。"""
        patches["untar"].side_effect = RuntimeError("tar failed")
        written = await _fanout_skill_to_pods("agent-1", SKILL_NAME, b"zip-bytes", None)
        assert written == 0
        cmds = [c.args[1][0] for c in patches["exec"].call_args_list]
        # 清理 temp
        assert any(".new." in c and "rm -rf" in c for c in cmds), cmds
        # 不应执行原子换入（无 mv）
        assert not any("mv " in c for c in cmds), cmds


# ── replay_persona_and_skills 回归（仍 fan-out skill 文件）───────────────


class TestReplayFansOut:
    async def test_replay_calls_fanout(self, db):
        """replay 仍对每个已装 skill 调 _fanout_skill_to_pods（secrets 由 _fanout 内回写）。"""
        session, definition, _cred = db
        with (
            patch("app.worker.config_skills.sync_persona", new=AsyncMock()),
            patch("app.worker.config_skills._fanout_skill_to_pods", new=AsyncMock()) as mock_fanout,
            patch("app.worker.config_skills.archiver") as archiver,
        ):
            archiver.list_skill_zips.return_value = [SKILL_NAME]
            archiver.get_skill_zip.return_value = b"zip-bytes"
            await replay_persona_and_skills(
                "agent-1", {"definition_id": str(definition.id)}, session
            )
            mock_fanout.assert_awaited_once()

    async def test_replay_no_definition_id_skips(self, db):
        """inst_cfg 无 definition_id → 直接 return，不 fan-out。"""
        session, _, _ = db
        with patch(
            "app.worker.config_skills._fanout_skill_to_pods", new=AsyncMock()
        ) as mock_fanout:
            await replay_persona_and_skills("agent-1", {}, session)
            mock_fanout.assert_not_awaited()
