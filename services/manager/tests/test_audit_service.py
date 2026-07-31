"""audit_service.log_operation 单测 — 用 mock session 验证 OperationLog 字段映射。

不依赖真 DB，验证 log_operation 调用时 db.add 收到的 OperationLog 实例字段正确，
以及不调用 commit（事务边界由调用方控制）。
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.audit_service import log_operation


@pytest.mark.asyncio
async def test_log_operation_writes_all_fields(mock_db_session):
    """log_operation 应构造 OperationLog 实例（含全部字段）并 add 到 session，不 commit。"""
    actor_id = uuid.uuid4()
    target_id = uuid.uuid4()
    group_id = uuid.uuid4()

    await log_operation(
        mock_db_session,
        actor_id=actor_id,
        action="agent_instance.create",
        target_type="agent_instance",
        target_id=target_id,
        status="success",
        detail={"name": "客服助手"},
        group_id=group_id,
    )

    # db.add 被调用一次，参数是 OperationLog 实例
    assert mock_db_session.add.call_count == 1
    op_log = mock_db_session.add.call_args[0][0]
    assert op_log.__class__.__name__ == "OperationLog"
    assert op_log.actor_id == actor_id
    assert op_log.action == "agent_instance.create"
    assert op_log.target_type == "agent_instance"
    assert op_log.target_id == target_id
    assert op_log.status == "success"
    assert op_log.detail == {"name": "客服助手"}
    assert op_log.group_id == group_id
    # 不应调用 commit（事务边界由调用方控制）
    mock_db_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_operation_defaults(mock_db_session):
    """status 默认 'success'，detail 默认 {}，target_id/group_id 可为 None。"""
    actor_id = uuid.uuid4()

    await log_operation(
        mock_db_session,
        actor_id=actor_id,
        action="auth.login",
        target_type="user",
        target_id=actor_id,
    )

    op_log = mock_db_session.add.call_args[0][0]
    assert op_log.status == "success"
    assert op_log.detail == {}
    assert op_log.group_id is None


@pytest.mark.asyncio
async def test_log_operation_failure_status(mock_db_session):
    """status='failure' 时 detail 应包含错误信息（由调用方填充）。"""
    actor_id = uuid.uuid4()

    await log_operation(
        mock_db_session,
        actor_id=actor_id,
        action="agent_instance.deploy",
        target_type="agent_instance",
        target_id=uuid.uuid4(),
        status="failure",
        detail={"error": "controller unreachable", "name": "inst-1"},
    )

    op_log = mock_db_session.add.call_args[0][0]
    assert op_log.status == "failure"
    assert op_log.detail["error"] == "controller unreachable"
    assert op_log.detail["name"] == "inst-1"


@pytest.mark.asyncio
async def test_log_operation_accepts_str_ids(mock_db_session):
    """target_id/group_id 接受 str（API 层从 path param 拿到的 UUID 字符串）。"""
    actor_id = uuid.uuid4()
    target_id_str = str(uuid.uuid4())
    group_id_str = str(uuid.uuid4())

    await log_operation(
        mock_db_session,
        actor_id=actor_id,
        action="agent_instance.update",
        target_type="agent_instance",
        target_id=target_id_str,
        group_id=group_id_str,
    )

    op_log = mock_db_session.add.call_args[0][0]
    assert op_log.target_id == target_id_str
    assert op_log.group_id == group_id_str
