"""_install_skill_bytes 顺序单测：COS 先存后 fan-out（COS 为重放真相源）。

修旧"fan-out 先于 save_skill_zip + COS 失败静默吞错"的坑：
  - COS 失败 → raise 503，session 回滚，不 fan-out
  - fan-out 失败 → DB+COS 已落库（reconcile 可重放），不回滚
真 DB（AgentDefinition）断言持久化；archiver / controller_client / _definition_instance_ids mock。
"""

import io
import uuid
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from app.api.agent_skills import _install_skill_bytes
from app.models import AgentDefinition, User, UserGroup, user_group_members
from app.worker.errors import ControllerError
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings


def _skill_zip(name="greeter") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: hi\nversion: 1.0.0\n---\nbody",
        )
    return buf.getvalue()


@pytest_asyncio.fixture
async def db():
    """真 DB：User + UserGroup + AgentDefinition。"""
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
    await session.commit()

    yield session, definition, user

    # teardown 用全新 session 跑 raw SQL（测试 session 可能因 _install_skill_bytes 中途抛
    # 异常而持有 dirty 对象 / 未提交事务，直接 ORM delete 会触发 StaleDataError）。
    did, uid, gid = str(definition.id), str(user.id), str(group.id)
    await session.close()
    await engine.dispose()

    cleanup_engine = create_async_engine(settings.test_database_url)
    cleanup_factory = async_sessionmaker(cleanup_engine, class_=AsyncSession)
    async with cleanup_factory() as s:
        await s.execute(text("DELETE FROM operation_logs WHERE target_id = :d"), {"d": did})
        await s.execute(
            text("UPDATE agent_definitions SET current_version_id = NULL WHERE id = :d"),
            {"d": did},
        )
        await s.execute(text("DELETE FROM agent_definitions WHERE id = :d"), {"d": did})
        await s.execute(
            text("DELETE FROM user_group_members WHERE user_id = :u"), {"u": uid}
        )
        await s.execute(text("DELETE FROM user_groups WHERE id = :g"), {"g": gid})
        await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        await s.commit()
    await cleanup_engine.dispose()


class TestInstallOrdering:
    async def test_install_raises_on_cos_save_failure(self, db):
        """COS save 失败 → 503，不 fan-out（save 先于 fan-out 的直接证据）。"""
        session, definition, user = db
        archiver = MagicMock()
        install_mock = AsyncMock()
        with (
            patch("app.api.agent_skills.archiver", new=archiver),
            patch("app.api.agent_skills.controller_client.install_skill", new=install_mock),
            patch(
                "app.api.agent_skills._definition_instance_ids",
                new=AsyncMock(return_value=["iid-1"]),
            ),
        ):
            archiver.save_skill_zip.side_effect = RuntimeError("COS down")
            with pytest.raises(HTTPException) as exc:
                await _install_skill_bytes(
                    session, definition.id, definition, _skill_zip(), user, "local"
                )
            assert exc.value.status_code == 503
            install_mock.assert_not_awaited()  # save 失败 → fan-out 未执行

    async def test_install_cos_save_before_fanout(self, db):
        """成功路径：save_skill_zip 在 controller_client.install_skill 之前调用。"""
        session, definition, user = db
        order: list[str] = []
        archiver = MagicMock()
        archiver.save_skill_zip.side_effect = lambda *a, **k: order.append("save")
        install_mock = AsyncMock(side_effect=lambda *a, **k: order.append("install"))
        with (
            patch("app.api.agent_skills.archiver", new=archiver),
            patch("app.api.agent_skills.controller_client.install_skill", new=install_mock),
            patch(
                "app.api.agent_skills._definition_instance_ids",
                new=AsyncMock(return_value=["iid-1"]),
            ),
        ):
            await _install_skill_bytes(
                session, definition.id, definition, _skill_zip(), user, "local"
            )
        assert order == ["save", "install"]

    async def test_install_fanout_failure_keeps_cos_and_db(self, db):
        """fan-out 失败 → HTTPException，但 DB+COS 已落库（commit 在 fan-out 前）。"""
        session, definition, user = db
        archiver = MagicMock()
        install_mock = AsyncMock(side_effect=ControllerError("fanout fail", 500))
        with (
            patch("app.api.agent_skills.archiver", new=archiver),
            patch("app.api.agent_skills.controller_client.install_skill", new=install_mock),
            patch(
                "app.api.agent_skills._definition_instance_ids",
                new=AsyncMock(return_value=["iid-1"]),
            ),
        ):
            with pytest.raises(HTTPException):
                await _install_skill_bytes(
                    session, definition.id, definition, _skill_zip(), user, "local"
                )
            archiver.save_skill_zip.assert_called_once()  # COS 已存
            install_mock.assert_awaited_once()  # fan-out 已尝试
        # DB 已提交 → skill_config 持久化（fan-out 失败不回滚 DB，reconcile 可重放）
        await session.refresh(definition)
        assert any(
            s.get("name") == "greeter" for s in definition.skill_config.get("skills", [])
        )
