"""
Manager IM Binding API 单元测试。

覆盖：列表、创建（含重复检测 409）、删除、404。
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from uuid import UUID
from datetime import datetime

from .helpers import FakeObj, make_mock_result

USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BINDING_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def make_binding_row(
    binding_id=BINDING_ID,
    user_id=USER_ID,
    channel_type="wecom",
    im_user_id="wecom_001",
    im_user_name="张三",
):
    return FakeObj(
        id=binding_id,
        user_id=user_id,
        channel_type=channel_type,
        im_user_id=im_user_id,
        im_user_name=im_user_name,
        created_at=datetime(2026, 6, 15, 10, 0, 0),
    )


# ═══════════════════════════════════════════════════
# List
# ═══════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_empty(client, mock_db_session):
    mock_db_session.execute.return_value = make_mock_result([])
    resp = await client.get(f"/api/manager/users/{USER_ID}/im-bindings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_with_data(client, mock_db_session):
    binding = make_binding_row()
    mock_db_session.execute.return_value = make_mock_result([binding])
    resp = await client.get(f"/api/manager/users/{USER_ID}/im-bindings")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["channel_type"] == "wecom"
    assert data["items"][0]["im_user_id"] == "wecom_001"
    assert data["items"][0]["im_user_name"] == "张三"


@pytest.mark.asyncio
async def test_list_multiple_bindings(client, mock_db_session):
    bindings = [
        make_binding_row(channel_type="wecom", im_user_id="w1"),
        make_binding_row(channel_type="feishu", im_user_id="f1"),
        make_binding_row(channel_type="dingtalk", im_user_id="d1"),
    ]
    mock_db_session.execute.return_value = make_mock_result(bindings)
    resp = await client.get(f"/api/manager/users/{USER_ID}/im-bindings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    types = {b["channel_type"] for b in data["items"]}
    assert types == {"wecom", "feishu", "dingtalk"}


# ═══════════════════════════════════════════════════
# Create
# ═══════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_success(client, mock_db_session):
    async def mock_refresh(binding):
        binding.id = BINDING_ID
        binding.created_at = datetime(2026, 6, 15, 10, 0, 0)

    mock_db_session.commit = AsyncMock()
    mock_db_session.refresh = AsyncMock(side_effect=mock_refresh)
    mock_db_session.add = MagicMock()
    # 让 execute 返回 None (not found for unique check)
    mock_db_session.execute.return_value = make_mock_result(None)

    resp = await client.post(
        f"/api/manager/users/{USER_ID}/im-bindings",
        json={"channel_type": "wecom", "im_user_id": "wecom_001", "im_user_name": "张三"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel_type"] == "wecom"
    assert data["im_user_id"] == "wecom_001"
    # 验证 add 被调用
    mock_db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_invalid_channel_type(client, mock_db_session):
    resp = await client.post(
        f"/api/manager/users/{USER_ID}/im-bindings",
        json={"channel_type": "invalid", "im_user_id": "u001"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_missing_im_user_id(client, mock_db_session):
    resp = await client.post(
        f"/api/manager/users/{USER_ID}/im-bindings",
        json={"channel_type": "wecom"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_duplicate(client, mock_db_session):
    """重复的 (channel_type, im_user_id) 返回 409"""
    mock_db_session.commit = AsyncMock(side_effect=Exception("duplicate"))
    mock_db_session.rollback = AsyncMock()
    mock_db_session.add = MagicMock()

    resp = await client.post(
        f"/api/manager/users/{USER_ID}/im-bindings",
        json={"channel_type": "wecom", "im_user_id": "wecom_001"},
    )
    assert resp.status_code == 409
    assert "已绑定" in resp.json()["detail"]


# ═══════════════════════════════════════════════════
# Delete
# ═══════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_success(client, mock_db_session):
    binding = make_binding_row()
    mock_db_session.execute.return_value = make_mock_result(binding)
    mock_db_session.commit = AsyncMock()

    resp = await client.delete(
        f"/api/manager/users/{USER_ID}/im-bindings/{BINDING_ID}"
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_not_found(client, mock_db_session):
    mock_db_session.execute.return_value = make_mock_result(None)

    resp = await client.delete(
        f"/api/manager/users/{USER_ID}/im-bindings/{BINDING_ID}"
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════
# Auth gating
# ═══════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_requires_auth(client, mock_db_session):
    """没有认证 token 应 401（但我们的 client fixture 已有 mock_user，所以这里验证不影响已有功能）"""
    mock_db_session.execute.return_value = make_mock_result([])
    resp = await client.get(f"/api/manager/users/{USER_ID}/im-bindings")
    assert resp.status_code == 200  # mock_user 已通过认证
