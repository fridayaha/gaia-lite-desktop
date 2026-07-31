from __future__ import annotations

DEFAULT_ORGANIZATION_ID = "default"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_VISIBILITY_SCOPE = "workspace"

VALID_VISIBILITY_SCOPES = frozenset({"private", "workspace", "organization", "public"})


def resolve_tenant_ids(
    org: str | None,
    ws: str | None,
) -> tuple[str, str]:
    return (
        org or DEFAULT_ORGANIZATION_ID,
        ws or DEFAULT_WORKSPACE_ID,
    )


def resolve_tenant_from_context(ctx) -> tuple[str, str]:
    return resolve_tenant_ids(
        getattr(ctx, "organization_id", None),
        getattr(ctx, "workspace_id", None),
    )


def normalize_visibility_scope(value: str | None) -> str:
    if value and value in VALID_VISIBILITY_SCOPES:
        return value
    return DEFAULT_VISIBILITY_SCOPE
