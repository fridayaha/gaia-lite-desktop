"""把 User 序列化为脱敏的「当前用户信息」结构化 dict。

供 manager 内部只读端点 ``GET /api/controller/profiles/{profile_name}/user-context``
返回给智能体（经 current-user-info 预置 skill 调用）。智能体处理任务需要当前用户
基本信息时主动 pull，每次调用查 DB 最新值——不注入 system prompt、不写引擎文件、
不进会话历史，避免旧信息与引擎耦合（见重构方案 1-hermes-user-md-2-federated-moon）。

字段自适应：遍历 User 表非敏感列自动带入，新增列无需改本模块。
"""

from __future__ import annotations

from app.models import User

# 敏感 / 无意义列不返回（PII 保护：不向智能体暴露凭据/登录态/审核状态）
_EXCLUDE_COLUMNS = frozenset(
    {
        "id",
        "hashed_password",
        "is_active",
        "email_verified",
        "phone_verified",
        "failed_login_count",
        "locked_until",
        "last_login_at",
        "last_login_ip",
        "last_login_user_agent",
        "avatar_url",
        "created_at",
        "updated_at",
    }
)

# 列名 → 中文标签；未命中用列名原文（保证新增列也有可读标签）
_LABEL_ZH: dict[str, str] = {
    "username": "用户名",
    "real_name": "真实姓名",
    "email": "邮箱",
    "phone": "手机号",
}


def _label_for(column_name: str) -> str:
    return _LABEL_ZH.get(column_name, column_name)


def serialize_user_context(user: User, business_binding=None) -> dict:
    """把用户基本信息序列化为脱敏 dict，供智能体只读查询。

    business_binding（可选）：BusinessUserBinding ORM 对象，带出业务系统身份
    （业务用户名/手机号/邮箱）。None 则跳过。

    只返回智能体处理任务所需的最小字段集，排除凭据、登录态、审核状态等敏感列。
    """
    fields: dict[str, str] = {}
    for col in User.__table__.columns:
        name = col.name
        if name in _EXCLUDE_COLUMNS:
            continue
        value = getattr(user, name, None)
        if value is None or value == "":
            continue
        fields[_label_for(name)] = str(value)

    roles = getattr(user, "roles", None) or []
    if roles:
        names = ", ".join(r.name for r in roles if r and r.name)
        if names:
            fields["角色"] = names

    groups = getattr(user, "groups", None) or []
    if groups:
        names = ", ".join(g.name for g in groups if g and g.name)
        if names:
            fields["用户组"] = names

    business: dict[str, str] = {}
    if business_binding is not None:
        if getattr(business_binding, "business_username", None):
            business["业务用户名"] = str(business_binding.business_username)
        if getattr(business_binding, "business_phone", None):
            business["业务手机号"] = str(business_binding.business_phone)
        if getattr(business_binding, "business_email", None):
            business["业务邮箱"] = str(business_binding.business_email)

    return {"fields": fields, "business": business}
