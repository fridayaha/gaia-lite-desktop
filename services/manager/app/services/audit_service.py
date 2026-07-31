"""审计日志 service — admin 后台写操作埋点 helper。

设计：手动在 service 层 commit 前调用 log_operation，与业务变更同事务 commit
保证一致性（创建智能体失败时日志也回滚）。不在这里 commit，由调用方控制事务边界。

用法：
    async with db.begin():
        db.add(instance)
        await db.flush()
        await log_operation(db, actor_id=user.id, action="agent_instance.create",
                           target_type="agent_instance", target_id=instance.id,
                           group_id=instance.group_id,
                           detail={"name": instance.name})

action 命名约定：<target_type>.<verb>，如 agent_instance.create / agent_instance.deploy /
agent_definition.publish / auth.login。target_type 用蛇形单数（agent_instance），与 model
名一致方便 grep。
"""

import contextvars
from typing import Any
from uuid import UUID

from app.models import OperationLog
from sqlalchemy.ext.asyncio import AsyncSession

# 请求级 operator_ip 跨度传播 — 由 access_log_middleware set，log_operation 自动读取。
# 不在调用点显式传 IP，避免 71 个 log_operation 调用点都改。
_operator_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "operator_ip", default=None
)
# 请求级 operator_user_agent 跨度传播 — 由 middleware 从 User-Agent 头提取后 set。
_operator_ua_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "operator_user_agent", default=None
)


def set_operator_ip(ip: str | None) -> None:
    _operator_ip_var.set(ip)


def get_operator_ip() -> str | None:
    return _operator_ip_var.get()


def set_operator_user_agent(ua: str | None) -> None:
    _operator_ua_var.set(ua)


def get_operator_user_agent() -> str | None:
    return _operator_ua_var.get()


async def log_operation(
    db: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: UUID | str | None = None,
    status: str = "success",
    detail: dict[str, Any] | None = None,
    group_id: UUID | str | None = None,
) -> None:
    """写入一条操作日志。不 commit，由调用方控制事务边界。

    参数：
        actor_id: 当前操作用户 ID（API 层从 get_current_user 拿 user.id 传入）；
                  None 表示匿名操作（如用户名不存在的登录尝试），DB 列已 nullable
        action: 操作动作，命名 <target_type>.<verb>（如 agent_instance.create）
        target_type: 操作对象类型（如 agent_instance / agent_definition / user）
        target_id: 操作对象 ID（UUID 或 str，None 表示无具体对象如 auth.login）
        status: "success" 或 "failure"
        detail: 请求体快照/响应摘要/错误信息（dict，存 JSON 列）
        group_id: 所属组（组级资源必传，平台级操作为 None）
    """
    db.add(OperationLog(
        actor_id=actor_id,
        group_id=group_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        status=status,
        detail=detail or {},
        operator_ip=get_operator_ip(),
        operator_user_agent=get_operator_user_agent(),
    ))
