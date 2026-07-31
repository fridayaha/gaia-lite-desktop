from app.core.auth_context import AuthContext


def resolve_effective_operator(
    ctx: AuthContext, body_operator: str | None
) -> str:
    if ctx.actor_id and ctx.actor_id.strip():
        return ctx.actor_id
    if body_operator and body_operator.strip():
        return body_operator
    return "unknown"


def log_operator_mismatch(
    ctx: AuthContext,
    body_operator: str | None,
    action: str,
    item_id: str | None = None,
    version_id: str | None = None,
) -> None:
    if not ctx.actor_id or not ctx.actor_id.strip():
        return
    if not body_operator or not body_operator.strip():
        return
    if ctx.actor_id == body_operator:
        return

    from app.core.event_log import log_event

    log_event(
        "auth.operator_mismatch",
        action=action,
        body_operator=body_operator,
        item_id=item_id,
        version_id=version_id,
        result="observed",
    )


def resolve_and_log_operator(
    ctx: AuthContext,
    body_operator: str | None,
    action: str,
    item_id: str | None = None,
    version_id: str | None = None,
) -> str:
    log_operator_mismatch(ctx, body_operator, action,
                          item_id=item_id, version_id=version_id)
    return resolve_effective_operator(ctx, body_operator)


def resolve_effective_created_by(
    ctx: AuthContext, body_created_by: str | None = None,
) -> str:
    if ctx.actor_id and ctx.actor_id.strip():
        return ctx.actor_id
    if body_created_by and body_created_by.strip():
        return body_created_by
    return "unknown"
