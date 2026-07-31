from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass
class AuthContext:
    actor_id: str | None = None
    actor_type: str | None = None
    display_name: str | None = None
    email: str | None = None
    agent_id: str | None = None
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    organization_id: str | None = None
    workspace_id: str | None = None
    groups: list[str] = field(default_factory=list)
    service_name: str | None = None
    raw: dict = field(default_factory=dict)
    is_authenticated: bool = False
    auth_mode: str = "none"

    @classmethod
    def from_headers(
        cls,
        headers: Mapping[str, str],
        auth_mode: str = "dev",
    ) -> "AuthContext":
        def _list(val: str | None) -> list[str]:
            if not val:
                return []
            return [s.strip() for s in val.split(",") if s.strip()]

        def _normalize_role(role: str) -> str:
            return role.strip().lower().replace("-", "_").replace(" ", "_")

        if auth_mode == "none":
            return cls(auth_mode="none")

        actor_id = headers.get("X-Actor-ID")
        raw_roles = _list(headers.get("X-Roles"))
        roles = [_normalize_role(r) for r in raw_roles if r]
        scopes = _list(headers.get("X-Scopes"))
        groups = _list(headers.get("X-Groups"))

        return cls(
            actor_id=actor_id,
            actor_type=headers.get("X-Actor-Type"),
            display_name=headers.get("X-User-Name"),
            email=headers.get("X-User-Email"),
            agent_id=headers.get("X-Agent-ID"),
            roles=roles,
            scopes=scopes,
            groups=groups,
            organization_id=headers.get("X-Organization-ID"),
            workspace_id=headers.get("X-Workspace-ID"),
            service_name=headers.get("X-Service-Name"),
            is_authenticated=actor_id is not None,
            auth_mode=auth_mode,
        )
