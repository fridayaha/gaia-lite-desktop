from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_403_FORBIDDEN

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context


class Permission:
    asset__create = "asset:create"
    asset__read = "asset:read"
    asset__update = "asset:update"
    asset__delete_draft = "asset:delete_draft"
    asset__import = "asset:import"

    version__create = "version:create"
    version__edit = "version:edit"
    version__delete = "version:delete"

    scan__run = "scan:run"
    scan__read = "scan:read"

    review__submit = "review:submit"
    review__approve = "review:approve"
    review__reject = "review:reject"
    review__request_change = "review:request_change"

    lifecycle__publish = "lifecycle:publish"
    lifecycle__disable = "lifecycle:disable"
    lifecycle__archive = "lifecycle:archive"
    lifecycle__rollback = "lifecycle:rollback"

    relation__create = "relation:create"
    relation__delete = "relation:delete"

    export__download = "export:download"

    audit__read = "audit:read"
    admin__configure = "admin:configure"


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "platform_admin": frozenset({
        Permission.asset__create,
        Permission.asset__read,
        Permission.asset__update,
        Permission.asset__delete_draft,
        Permission.asset__import,
        Permission.version__create,
        Permission.version__edit,
        Permission.version__delete,
        Permission.scan__run,
        Permission.scan__read,
        Permission.review__submit,
        Permission.review__approve,
        Permission.review__reject,
        Permission.review__request_change,
        Permission.lifecycle__publish,
        Permission.lifecycle__disable,
        Permission.lifecycle__archive,
        Permission.lifecycle__rollback,
        Permission.relation__create,
        Permission.relation__delete,
        Permission.export__download,
        Permission.audit__read,
        Permission.admin__configure,
    }),
    "asset_owner": frozenset({
        Permission.asset__create,
        Permission.asset__read,
        Permission.asset__update,
        Permission.asset__import,
        Permission.version__create,
        Permission.version__edit,
        Permission.scan__run,
        Permission.scan__read,
        Permission.review__submit,
        Permission.relation__create,
        Permission.relation__delete,
        Permission.export__download,
    }),
    "contributor": frozenset({
        Permission.asset__create,
        Permission.asset__read,
        Permission.asset__import,
        Permission.version__create,
        Permission.version__edit,
        Permission.scan__run,
        Permission.scan__read,
        Permission.review__submit,
        Permission.relation__create,
        Permission.export__download,
    }),
    "security_reviewer": frozenset({
        Permission.asset__read,
        Permission.scan__read,
        Permission.scan__run,
        Permission.review__approve,
        Permission.review__reject,
        Permission.review__request_change,
        Permission.export__download,
    }),
    "business_approver": frozenset({
        Permission.asset__read,
        Permission.scan__read,
        Permission.review__approve,
        Permission.review__reject,
        Permission.review__request_change,
        Permission.export__download,
    }),
    "publisher": frozenset({
        Permission.asset__read,
        Permission.scan__read,
        Permission.lifecycle__publish,
        Permission.lifecycle__disable,
        Permission.lifecycle__rollback,
        Permission.export__download,
    }),
    "runtime_consumer": frozenset(),
    "auditor": frozenset({
        Permission.asset__read,
        Permission.scan__read,
        Permission.audit__read,
        Permission.export__download,
    }),
}


def normalize_role(raw: str) -> str:
    normalized = raw.strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return normalized


def get_permissions_for_roles(roles: list[str]) -> set[str]:
    perms: set[str] = set()
    for role in roles:
        normalized = normalize_role(role)
        if normalized in ROLE_PERMISSIONS:
            perms.update(ROLE_PERMISSIONS[normalized])
    return perms


def has_permission(ctx: AuthContext, permission: str) -> bool:
    if not ctx.roles:
        return False
    perms = get_permissions_for_roles(ctx.roles)
    return permission in perms


def require_permission(permission: str):
    required = permission

    def checker(request: Request) -> None:
        ctx = getattr(request.state, "auth_context", None)
        if ctx is None:
            ctx = get_auth_context()
        if has_permission(ctx, required):
            return
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="permission denied",
        )

    return Depends(checker)
