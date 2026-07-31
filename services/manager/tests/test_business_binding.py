"""Manager Business Binding API 单元测试（1:1）。

覆盖：GET（空/有）、PUT upsert（新建/更新/校验）、DELETE（成功/404）。
业务绑定信息不再 fan-out 写 USER.md（智能体经 current-user-info skill 实时 pull）。
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from .helpers import FakeObj, make_mock_result

USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BINDING_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def make_business_row(
    binding_id=BINDING_ID,
    user_id=USER_ID,
    business_username="biz_user",
    business_phone="13800000000",
    business_email="biz@example.com",
):
    return FakeObj(
        id=binding_id,
        user_id=user_id,
        business_username=business_username,
        business_phone=business_phone,
        business_email=business_email,
        created_at=datetime(2026, 7, 15, 10, 0, 0),
    )


@pytest.fixture
def stub_side_effects(monkeypatch):
    """patch log_operation，避免审计干扰 db mock。"""
    monkeypatch.setattr("app.api.business_bindings.log_operation", AsyncMock())


# ═══════════════════════════════════════════════════
# GET
# ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_empty(client, mock_db_session, stub_side_effects):
    mock_db_session.execute.return_value = make_mock_result(None)
    resp = await client.get(f"/api/manager/users/{USER_ID}/business-bindings")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_get_with_data(client, mock_db_session, stub_side_effects):
    row = make_business_row()
    mock_db_session.execute.return_value = make_mock_result(row)
    resp = await client.get(f"/api/manager/users/{USER_ID}/business-bindings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_username"] == "biz_user"
    assert data["business_phone"] == "13800000000"
    assert data["business_email"] == "biz@example.com"


# ═══════════════════════════════════════════════════
# PUT upsert
# ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_put_create(client, mock_db_session, stub_side_effects):
    """无现有绑定 → 新建（add 被调用）。"""

    async def mock_refresh(b):
        b.id = BINDING_ID
        b.created_at = datetime(2026, 7, 15, 10, 0, 0)

    mock_db_session.execute.return_value = make_mock_result(None)  # get_binding None
    mock_db_session.commit = AsyncMock()
    mock_db_session.refresh = AsyncMock(side_effect=mock_refresh)
    mock_db_session.add = MagicMock()

    resp = await client.put(
        f"/api/manager/users/{USER_ID}/business-bindings",
        json={
            "business_username": "biz_user",
            "business_phone": "13800000000",
            "business_email": "biz@example.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["business_username"] == "biz_user"
    mock_db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_put_update(client, mock_db_session, stub_side_effects):
    """已有绑定 → 更新（不 add）。"""
    existing = make_business_row(business_username="old_name")
    mock_db_session.execute.return_value = make_mock_result(existing)
    mock_db_session.commit = AsyncMock()
    mock_db_session.refresh = AsyncMock()
    mock_db_session.add = MagicMock()

    resp = await client.put(
        f"/api/manager/users/{USER_ID}/business-bindings",
        json={"business_username": "new_name"},
    )
    assert resp.status_code == 200
    assert resp.json()["business_username"] == "new_name"
    mock_db_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_put_missing_username(client, mock_db_session, stub_side_effects):
    """缺 business_username → 422。"""
    resp = await client.put(
        f"/api/manager/users/{USER_ID}/business-bindings",
        json={"business_phone": "13800000000"},
    )
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════
# DELETE
# ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_success(client, mock_db_session, stub_side_effects):
    row = make_business_row()
    mock_db_session.execute.return_value = make_mock_result(row)
    mock_db_session.commit = AsyncMock()
    mock_db_session.delete = AsyncMock()

    resp = await client.delete(f"/api/manager/users/{USER_ID}/business-bindings")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_not_found(client, mock_db_session, stub_side_effects):
    mock_db_session.execute.return_value = make_mock_result(None)
    resp = await client.delete(f"/api/manager/users/{USER_ID}/business-bindings")
    assert resp.status_code == 404
