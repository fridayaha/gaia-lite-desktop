"""skill 凭证管理 API 测试 — 真 DB，验证加密落库 + 不回显明文 + 越权隔离 + merge。"""

import uuid

import pytest_asyncio
from app.core.auth import get_current_user
from app.core.crypto import decrypt_credentials_dict
from app.core.group_scope import get_current_group_ids
from app.main import app
from app.models import AgentDefinition, User, UserGroup, user_group_members
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings
from pkg.common.database import get_db

CRED_TABLES = ["skill_credentials", "agent_definitions", "user_group_members", "user_groups"]


def _skill_config(skill_name="weather-api"):
    return {
        "skills": [
            {
                "id": str(uuid.uuid4()),
                "name": skill_name,
                "enabled": True,
                "config_params": [
                    {
                        "name": "api_key",
                        "label": "API Key",
                        "type": "string",
                        "secret": True,
                        "description": "外部 API 密钥",
                    },
                    {
                        "name": "api_secret",
                        "label": "API Secret",
                        "type": "string",
                        "secret": True,
                        "description": "外部 API Secret",
                    },
                    {
                        "name": "endpoint",
                        "label": "Endpoint",
                        "type": "string",
                        "description": "非凭证参数",
                    },
                ],
                "config": {},
            }
        ],
        "order": [],
    }


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"cred_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    group = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(group)
    await session.flush()
    group.litellm_team_id = str(group.id)
    await session.execute(user_group_members.insert().values(user_id=user.id, group_id=group.id))

    group_b = UserGroup(name=f"gb_{uuid.uuid4().hex[:8]}", code=f"cb{uuid.uuid4().hex[:8]}")
    session.add(group_b)
    await session.flush()
    group_b.litellm_team_id = str(group_b.id)

    definition = AgentDefinition(
        group_id=group.id,
        name=f"d_{uuid.uuid4().hex[:8]}",
        engine_type="HERMES",
        skill_config=_skill_config(),
        created_by=user.id,
    )
    session.add(definition)
    await session.commit()

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_group_ids] = lambda: [group.id]

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    skill = definition.skill_config["skills"][0]
    yield c, user, group, group_b, definition, skill, session
    await c.aclose()

    app.dependency_overrides.clear()
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in CRED_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


def _cred_url(definition, skill):
    return f"/api/manager/agent-definitions/{definition.id}/skills/{skill['id']}/credentials"


async def _fetch_encrypted(session, definition_id):
    return (
        await session.execute(
            text("SELECT credentials_encrypted FROM skill_credentials WHERE definition_id = :d"),
            {"d": str(definition_id)},
        )
    ).scalar_one()


async def test_save_encrypts_in_db(client):
    c, user, group, group_b, definition, skill, session = client
    resp = await c.put(
        _cred_url(definition, skill),
        json={"credentials": {"api_key": "sk-plaintext-secret-123"}},
    )
    assert resp.status_code == 200, resp.text
    row = await _fetch_encrypted(session, definition.id)
    # 直接查 DB：加密列不含明文子串
    assert "sk-plaintext-secret-123" not in row
    # 可解密还原
    assert decrypt_credentials_dict(row) == {"api_key": "sk-plaintext-secret-123"}


async def test_get_status_no_plaintext(client):
    c, user, group, group_b, definition, skill, session = client
    await c.put(
        _cred_url(definition, skill),
        json={"credentials": {"api_key": "sk-plaintext-secret-123"}},
    )
    resp = await c.get(_cred_url(definition, skill))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] == ["api_key"]
    # 响应体不含明文
    assert "sk-plaintext-secret-123" not in resp.text


async def test_save_rejects_non_secret_param(client):
    c, user, group, group_b, definition, skill, session = client
    resp = await c.put(
        _cred_url(definition, skill),
        json={"credentials": {"endpoint": "https://api.example.com"}},  # 非 secret 参数
    )
    assert resp.status_code == 400


async def test_save_empty_value_skips(client):
    c, user, group, group_b, definition, skill, session = client
    await c.put(_cred_url(definition, skill), json={"credentials": {"api_key": "sk-first"}})
    # 再提交空值（不应覆盖已有）
    resp = await c.put(_cred_url(definition, skill), json={"credentials": {"api_key": ""}})
    assert resp.status_code == 200
    row = await _fetch_encrypted(session, definition.id)
    assert decrypt_credentials_dict(row) == {"api_key": "sk-first"}


async def test_save_merges_existing(client):
    c, user, group, group_b, definition, skill, session = client
    await c.put(_cred_url(definition, skill), json={"credentials": {"api_key": "sk-key"}})
    await c.put(_cred_url(definition, skill), json={"credentials": {"api_secret": "sk-secret"}})
    row = await _fetch_encrypted(session, definition.id)
    # 二次提交 merge，已有 key 保留
    assert decrypt_credentials_dict(row) == {"api_key": "sk-key", "api_secret": "sk-secret"}


async def test_save_skill_not_found_404(client):
    c, user, group, group_b, definition, skill, session = client
    resp = await c.put(
        f"/api/manager/agent-definitions/{definition.id}/skills/nonexistent/credentials",
        json={"credentials": {"api_key": "x"}},
    )
    assert resp.status_code == 404


async def test_cross_group_returns_404(client):
    c, user, group, group_b, definition, skill, session = client
    # 用 group_b 视角访问 group 的 definition → 跨组 404（_require_definition 组隔离）
    app.dependency_overrides[get_current_group_ids] = lambda: [group_b.id]
    resp = await c.put(
        _cred_url(definition, skill),
        json={"credentials": {"api_key": "sk-x"}},
    )
    assert resp.status_code == 404
