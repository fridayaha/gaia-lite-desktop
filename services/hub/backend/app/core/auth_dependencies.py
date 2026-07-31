from fastapi import Query, Request

from app.core.auth_context import AuthContext


def _parse_list(val: str | None) -> list[str]:
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


def get_runtime_auth_context(
    request: Request,
    agent_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    roles: str | None = Query(default=None),
) -> AuthContext:
    ctx = request.state.auth_context

    if not ctx.actor_id and actor_id:
        ctx.actor_id = actor_id
        ctx.is_authenticated = True
    if not ctx.actor_type and actor_type:
        ctx.actor_type = actor_type
    if not ctx.agent_id and agent_id:
        ctx.agent_id = agent_id
    if not ctx.workspace_id and workspace_id:
        ctx.workspace_id = workspace_id
    if not ctx.organization_id and organization_id:
        ctx.organization_id = organization_id
    if not ctx.scopes and scopes:
        ctx.scopes = _parse_list(scopes)
    if not ctx.roles and roles:
        ctx.roles = _parse_list(roles)

    return ctx
