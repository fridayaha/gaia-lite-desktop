"""reconcile_skills 全链对账 + 自愈单测。

4 链对账（DB skill_config ↔ COS zip ↔ Pod 文件 ↔ secrets.enc）+ drift 自愈：
  - Pod 缺文件、COS 有 → _fanout_skill_to_pods
  - Pod 缺 secrets.enc、有 SkillCredential → write_skill_secrets
  - DB 有、COS 无 → 仅上报（不可自愈）
真 DB（AgentDefinition + SkillCredential）断言查询；k8s exec / archiver / _fanout /
write_skill_secrets 全 mock。
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from app.core.crypto import encrypt_credentials_dict
from app.models import AgentDefinition, SkillCredential, User, UserGroup, user_group_members
from app.worker.config_skills import reconcile_skills
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


def _probe(*, dir_=True, secret=True, name=SKILL_NAME) -> str:
    """造 _probe_pod_skills_shell 的 JSON 输出（单 skill）。"""
    return json.dumps([{"name": name, "dir": dir_, "secret": secret}])


class TestReconcileSkills:
    @pytest_asyncio.fixture
    async def mocks(self, db):
        session, definition, cred = db
        exec_mock = AsyncMock()
        fanout_mock = AsyncMock()
        write_mock = AsyncMock()
        archiver_mock = MagicMock()
        archiver_mock.list_skill_zips.return_value = [SKILL_NAME]
        archiver_mock.get_skill_zip.return_value = b"zip"
        skill_config = {"skills": [{"name": SKILL_NAME}], "order": []}
        stack = [
            patch(
                "app.worker.config_skills._load_agent_configs",
                new=AsyncMock(return_value=(None, skill_config, str(definition.id))),
            ),
            patch(
                "app.worker.config_skills._iter_agent_target_pods",
                new=AsyncMock(return_value=[{"pod_name": "p1", "homes": ["/h1"]}]),
            ),
            patch("app.worker.config_skills.k8s_manager.exec_command_in_pod", new=exec_mock),
            patch("app.worker.config_skills._fanout_skill_to_pods", new=fanout_mock),
            patch("app.worker.config_skills.write_skill_secrets", new=write_mock),
            patch("app.worker.config_skills.archiver", new=archiver_mock),
        ]
        for s in stack:
            s.__enter__()
        yield {
            "exec": exec_mock,
            "fanout": fanout_mock,
            "write": write_mock,
            "archiver": archiver_mock,
            "session": session,
            "definition": definition,
            "cred": cred,
        }
        for s in reversed(stack):
            s.__exit__(None, None, None)

    async def test_all_consistent_noop(self, mocks):
        """全一致：DB 有、COS 有、Pod 文件+secrets 都在 → 不 fan-out 不写，drift=False。"""
        mocks["exec"].return_value = _probe(dir_=True, secret=True)
        result = await reconcile_skills("agent-1", mocks["session"])
        assert result["drift"] is False
        assert result["healed"] is False
        mocks["fanout"].assert_not_awaited()
        mocks["write"].assert_not_awaited()

    async def test_missing_files_refanout(self, mocks):
        """Pod 缺文件、COS 有 zip → _fanout_skill_to_pods（含 _replay_secrets），healed=True。"""
        mocks["exec"].return_value = _probe(dir_=False, secret=False)
        result = await reconcile_skills("agent-1", mocks["session"])
        assert result["drift"] is True
        assert result["healed"] is True
        mocks["fanout"].assert_awaited_once()
        # 缺文件时不应走 write_secrets 分支（dir_ok=False）
        mocks["write"].assert_not_awaited()

    async def test_missing_secrets_rewrite(self, mocks):
        """Pod 有文件、缺 secrets.enc、有 SkillCredential → write_skill_secrets，healed=True。"""
        mocks["exec"].return_value = _probe(dir_=True, secret=False)
        result = await reconcile_skills("agent-1", mocks["session"])
        assert result["drift"] is True
        assert result["healed"] is True
        mocks["write"].assert_awaited_once()
        req = mocks["write"].call_args.args[1]
        assert req.skill_name == SKILL_NAME
        assert req.credentials_encrypted == mocks["cred"].credentials_encrypted
        mocks["fanout"].assert_not_awaited()

    async def test_no_pods_graceful(self, mocks):
        """无 pod（SUSPEND）→ pods_scanned=0，不 fan-out 不写。"""
        with patch(
            "app.worker.config_skills._iter_agent_target_pods",
            new=AsyncMock(return_value=[{"pod_name": None, "homes": []}]),
        ):
            result = await reconcile_skills("agent-1", mocks["session"])
        assert result["pods_scanned"] == 0
        assert result["drift"] is False
        mocks["fanout"].assert_not_awaited()

    async def test_cos_missing_db_present_flags_drift(self, mocks):
        """DB 有 skill、COS 无 zip → drift=True，不可自愈（healed=False）。"""
        mocks["archiver"].list_skill_zips.return_value = []
        mocks["exec"].return_value = _probe(dir_=False, secret=False)
        result = await reconcile_skills("agent-1", mocks["session"])
        assert result["drift"] is True
        assert result["healed"] is False
        mocks["fanout"].assert_not_awaited()

    async def test_idempotent_repeat(self, mocks):
        """二次调用：首次 drift 自愈，二次全一致不再 fan-out。"""
        mocks["exec"].side_effect = [_probe(dir_=False), _probe(dir_=True, secret=True)]
        await reconcile_skills("agent-1", mocks["session"])
        await reconcile_skills("agent-1", mocks["session"])
        assert mocks["fanout"].await_count == 1
