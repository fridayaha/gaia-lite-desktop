"""GET /api/controller/profiles/{profile_name}/user-context 只读端点测试。

智能体经 current-user-info 预置 skill 调本端点 pull 当前用户最新信息。
覆盖：鉴权（X-Internal-Token）、profile 未找到/user 未找到 404、成功返回脱敏 dict。
serialize_user_context 的字段行为由 test_user_info_renderer.py 覆盖，本测试只验端点编排。
"""

from uuid import uuid4

import pytest
from app.models import BusinessUserBinding, Role, User, UserGroup

from pkg.common.config import settings
from tests.helpers import FakeObj, make_mock_result

PROFILE_NAME = "prof-ctx-test"


def _make_user() -> User:
    u = User(
        username="alice",
        real_name="张三",
        email="alice@example.com",
        phone="13800000000",
        hashed_password="secret-hash",
        is_active=True,
    )
    u.roles = [Role(name="销售")]
    u.groups = [UserGroup(name="门店A组", code="a")]
    return u


def _make_binding() -> BusinessUserBinding:
    return BusinessUserBinding(
        business_username="LiuWei",
        business_phone="13900000000",
        business_email=None,
    )


def _set_executes(session, profile, user, binding):
    """端点按顺序执行 3 次 select：profile → user → binding。"""
    session.execute.side_effect = [
        make_mock_result(profile),
        make_mock_result(user),
        make_mock_result(binding),
    ]


@pytest.mark.asyncio
async def test_success_returns_user_context(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "secret")
    uid = uuid4()
    profile = FakeObj(profile_name=PROFILE_NAME, user_id=uid)
    _set_executes(mock_db_session, profile, _make_user(), _make_binding())

    resp = await client.get(
        f"/api/controller/profiles/{PROFILE_NAME}/user-context",
        headers={"X-Internal-Token": "secret"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fields"]["用户名"] == "alice"
    assert data["fields"]["真实姓名"] == "张三"
    assert data["fields"]["角色"] == "销售"
    assert data["business"]["业务用户名"] == "LiuWei"
    assert data["business"]["业务手机号"] == "13900000000"
    # 敏感字段不返回
    assert "hashed_password" not in " ".join(data["fields"].values())


@pytest.mark.asyncio
async def test_profile_not_found_returns_404(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "")  # 未配置 token → 放行鉴权
    mock_db_session.execute.return_value = make_mock_result(None)

    resp = await client.get(f"/api/controller/profiles/{PROFILE_NAME}/user-context")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_without_user_id_returns_404(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "")
    profile = FakeObj(profile_name=PROFILE_NAME, user_id=None)
    mock_db_session.execute.return_value = make_mock_result(profile)

    resp = await client.get(f"/api/controller/profiles/{PROFILE_NAME}/user-context")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_not_found_returns_404(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "")
    uid = uuid4()
    profile = FakeObj(profile_name=PROFILE_NAME, user_id=uid)
    mock_db_session.execute.side_effect = [
        make_mock_result(profile),
        make_mock_result(None),
    ]

    resp = await client.get(f"/api/controller/profiles/{PROFILE_NAME}/user-context")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_missing_token_returns_401_when_configured(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "secret")
    mock_db_session.execute.return_value = make_mock_result(None)

    resp = await client.get(f"/api/controller/profiles/{PROFILE_NAME}/user-context")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_returns_401(client, mock_db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "secret")
    mock_db_session.execute.return_value = make_mock_result(None)

    resp = await client.get(
        f"/api/controller/profiles/{PROFILE_NAME}/user-context",
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_token_configured_allows_request(client, mock_db_session, monkeypatch):
    """未配置 internal_token（本地 dev）→ 不校验，靠网络隔离。"""
    monkeypatch.setattr(settings, "internal_token", "")
    uid = uuid4()
    profile = FakeObj(profile_name=PROFILE_NAME, user_id=uid)
    _set_executes(mock_db_session, profile, _make_user(), None)

    resp = await client.get(f"/api/controller/profiles/{PROFILE_NAME}/user-context")
    assert resp.status_code == 200
    assert resp.json()["fields"]["用户名"] == "alice"
    assert resp.json()["business"] == {}
