"""非 secret 配置管理 API 测试 — 真 DB，验证类型校验 + 落库 + 回填 + secret 隔离。

覆盖：
- save_skill_config：类型校验各分支 / 拒 secret / 拒未声明 / 部分 update / 真 DB 写入
- get_skill_config：回填已存值 + default 兜底 + secret 不回显
- _refanout_skill：mock get_skill_zip 返回 None 短路 fan-out（避免连 MinIO / k8s）
"""

import uuid

import pytest_asyncio
from app.core.auth import get_current_user
from app.core.group_scope import get_current_group_ids
from app.main import app
from app.models import AgentDefinition, User, UserGroup, user_group_members
from app.worker.minio_archiver import archiver
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings
from pkg.common.database import get_db

CFG_TABLES = ["skill_credentials", "agent_definitions", "user_group_members", "user_groups"]


def _skill_config(skill_name="credential-checker"):
    """覆盖 string/number/boolean/select 四类型 + 1 个 secret + default 兜底。"""
    return {
        "skills": [
            {
                "id": str(uuid.uuid4()),
                "name": skill_name,
                "enabled": True,
                "config_params": [
                    {"name": "api_key", "label": "API Key", "type": "string", "secret": True},
                    {
                        "name": "echo_endpoint",
                        "label": "Endpoint",
                        "type": "string",
                        "default": "https://httpbin.org/anything",
                    },
                    {"name": "timeout_seconds", "label": "Timeout", "type": "number", "default": 30},
                    {"name": "verbose", "label": "Verbose", "type": "boolean", "default": False},
                    {
                        "name": "http_method",
                        "label": "Method",
                        "type": "select",
                        "options": ["GET", "POST"],
                        "default": "POST",
                    },
                ],
                "config": {},
            }
        ],
        "order": [],
    }


@pytest_asyncio.fixture
async def client(monkeypatch):
    # refanout 会调 archiver.get_skill_zip + controller_client.install_skill；
    # mock get_skill_zip 返回 None 短路 fan-out，避免连 MinIO / 调 k8s
    monkeypatch.setattr(archiver, "get_skill_zip", lambda *a, **k: None)

    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"cfg_{uuid.uuid4().hex[:8]}",
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
    yield c, definition, skill, session
    await c.aclose()

    app.dependency_overrides.clear()
    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in CFG_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


def _cfg_url(definition, skill):
    return f"/api/manager/agent-definitions/{definition.id}/skills/{skill['id']}/config"


async def _fetch_skill_config(session, definition_id):
    """裸 SQL 读 skill_config JSON，绕过 ORM identity map，确认真落库。"""
    return (
        await session.execute(
            text("SELECT skill_config FROM agent_definitions WHERE id = :d"),
            {"d": str(definition_id)},
        )
    ).scalar_one()


async def test_save_persists_config_to_db(client):
    c, definition, skill, session = client
    resp = await c.put(
        _cfg_url(definition, skill),
        json={
            "config": {
                "echo_endpoint": "https://x.example.com",
                "timeout_seconds": 60,
                "verbose": True,
                "http_method": "GET",
            }
        },
    )
    assert resp.status_code == 200, resp.text
    sc = await _fetch_skill_config(session, definition.id)
    cfg = sc["skills"][0]["config"]
    assert cfg["echo_endpoint"] == "https://x.example.com"
    assert cfg["timeout_seconds"] == 60
    assert cfg["verbose"] is True
    assert cfg["http_method"] == "GET"
    assert "api_key" not in cfg  # secret 不进 config


async def test_save_rejects_secret_key(client):
    c, definition, skill, session = client
    resp = await c.put(_cfg_url(definition, skill), json={"config": {"api_key": "sk-x"}})
    assert resp.status_code == 400


async def test_save_rejects_undeclared_key(client):
    c, definition, skill, session = client
    resp = await c.put(
        _cfg_url(definition, skill), json={"config": {"unknown_param": "x"}}
    )
    assert resp.status_code == 400


async def test_save_validates_select_options(client):
    c, definition, skill, session = client
    resp = await c.put(
        _cfg_url(definition, skill), json={"config": {"http_method": "PUT"}}  # 非 options
    )
    assert resp.status_code == 400


async def test_save_validates_number_type(client):
    c, definition, skill, session = client
    resp = await c.put(
        _cfg_url(definition, skill),
        json={"config": {"timeout_seconds": "not-a-number"}},
    )
    assert resp.status_code == 400


async def test_save_accepts_numeric_string_for_number(client):
    c, definition, skill, session = client
    resp = await c.put(
        _cfg_url(definition, skill), json={"config": {"timeout_seconds": "45"}}
    )
    assert resp.status_code == 200, resp.text
    sc = await _fetch_skill_config(session, definition.id)
    assert sc["skills"][0]["config"]["timeout_seconds"] == 45  # 归一化为 int


async def test_save_partial_update_keeps_other_keys(client):
    c, definition, skill, session = client
    await c.put(_cfg_url(definition, skill), json={"config": {"echo_endpoint": "https://a"}})
    # 部分更新：只提交一个 key，另一个已存的保留
    await c.put(_cfg_url(definition, skill), json={"config": {"timeout_seconds": 90}})
    sc = await _fetch_skill_config(session, definition.id)
    cfg = sc["skills"][0]["config"]
    assert cfg["echo_endpoint"] == "https://a"  # 保留
    assert cfg["timeout_seconds"] == 90


async def test_get_config_returns_values_with_default(client):
    c, definition, skill, session = client
    resp = await c.get(_cfg_url(definition, skill))
    assert resp.status_code == 200
    values = resp.json()["values"]
    # 未填参数用 default 兜底
    assert values["echo_endpoint"] == "https://httpbin.org/anything"
    assert values["timeout_seconds"] == 30
    assert values["verbose"] is False
    assert values["http_method"] == "POST"
    assert "api_key" not in values  # secret 不回显


async def test_get_config_after_save_returns_stored_values(client):
    c, definition, skill, session = client
    await c.put(_cfg_url(definition, skill), json={"config": {"echo_endpoint": "https://saved"}})
    resp = await c.get(_cfg_url(definition, skill))
    assert resp.json()["values"]["echo_endpoint"] == "https://saved"


async def test_save_skill_not_found_404(client):
    c, definition, skill, session = client
    resp = await c.put(
        f"/api/manager/agent-definitions/{definition.id}/skills/nonexistent/config",
        json={"config": {"echo_endpoint": "x"}},
    )
    assert resp.status_code == 404
