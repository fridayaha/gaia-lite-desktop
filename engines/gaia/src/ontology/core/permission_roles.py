"""Built-in role definitions for the permission governance system (ADR-016 D4).

Defines the nine built-in roles (design §1.3) with their scope types and
operation permission lists. Seeded by the Phase 1 bootstrap and referenced
by AuthorizationService for Layer 4 (Project RBAC) checks.

Role tiers + separation of duties:
  GLOBAL  — PLATFORM_ADMIN / AUDIT_ADMIN / MARKING_ADMIN
  SPACE   — SPACE_OWNER / SPACE_EDITOR / SPACE_VIEWER / SPACE_DISCOVERER
  PROJECT — OWNER / EDITOR / VIEWER / DISCOVERER (most common, granted to Group)

Separation of duties is a security baseline (design §0.1 principle 6):
  - MARKING_ADMIN manages data classification but NOT projects
  - PROJECT_OWNER manages collaboration but NOT classifications
  - PLATFORM_ADMIN has NO data access by default (manages permissions, not data)
  - AUDIT_ADMIN can ONLY read audit logs (no operation permissions)

Operations follow ``<resource>:<action>`` convention, e.g. ``ontology:edit``,
``object_type:view``, ``action:execute``, ``dataset:read``. The wildcard
``*`` means all operations on all resources (PLATFORM_ADMIN only, and even
then data access is separately gated by Marking/row-level).
"""

from __future__ import annotations

from typing import Any

# ── Operation constants (resource:action) ──
# Centralized so policy strings aren't scattered. AuthorizationService Layer 4
# checks ``action in role.permissions`` (or wildcard expansion).

# Ontology / definition management
OP_ONTOLOGY_VIEW = "ontology:view"
OP_ONTOLOGY_EDIT = "ontology:edit"
OP_ONTOLOGY_DELETE = "ontology:delete"
OP_OBJECT_TYPE_VIEW = "object_type:view"
OP_OBJECT_TYPE_EDIT = "object_type:edit"
OP_OBJECT_TYPE_DELETE = "object_type:delete"
OP_ACTION_TYPE_VIEW = "action_type:view"
OP_ACTION_TYPE_EDIT = "action_type:edit"
OP_ACTION_TYPE_EXECUTE = "action_type:execute"
OP_LINK_TYPE_VIEW = "link_type:view"
OP_LINK_TYPE_EDIT = "link_type:edit"

# Data resources
OP_DATASET_VIEW = "dataset:view"
OP_DATASET_EDIT = "dataset:edit"
OP_DATASET_DELETE = "dataset:delete"
OP_DATASOURCE_VIEW = "datasource:view"
OP_DATASOURCE_EDIT = "datasource:edit"
OP_DATASOURCE_DELETE = "datasource:delete"

# Object instances (read = query, write = action mutations)
OP_OBJECT_VIEW = "object:view"
OP_OBJECT_WRITE = "object:write"

# Container management
OP_SPACE_ADMIN = "space:admin"
OP_PROJECT_ADMIN = "project:admin"
OP_PROJECT_CREATE = "project:create"

# Governance
OP_MARKING_MANAGE = "marking:manage"
OP_MARKING_ASSIGN = "marking:assign"
OP_AUDIT_READ = "audit:read"
OP_USER_MANAGE = "user:manage"
OP_ROLE_MANAGE = "role:manage"

# Platform
OP_PLATFORM_ADMIN = "*"


# ── Built-in role definitions ──
# Each entry: (name, scope_type, permissions, description).
# scope_type determines where the role can be assigned:
#   GLOBAL  — scope_id = NULL (platform-wide)
#   SPACE   — scope_id = Space id (inherits to all Projects under the Space)
#   PROJECT — scope_id = Project id (most common collaboration boundary)

BUILTIN_ROLES: list[dict[str, Any]] = [
    {
        "name": "PLATFORM_ADMIN",
        "scope_type": "GLOBAL",
        "permissions": [
            OP_PLATFORM_ADMIN,  # platform management — but NO data access by default
            OP_USER_MANAGE,
            OP_ROLE_MANAGE,
            OP_SPACE_ADMIN,
            OP_PROJECT_CREATE,
        ],
        "description": (
            "Platform management. Manages users/roles/spaces but has NO data "
            "access by default (separation of duties)."
        ),
    },
    {
        "name": "AUDIT_ADMIN",
        "scope_type": "GLOBAL",
        "permissions": [OP_AUDIT_READ],
        "description": (
            "Read-only audit access. Can read audit logs but has NO operation "
            "permissions (separation of duties)."
        ),
    },
    {
        "name": "MARKING_ADMIN",
        "scope_type": "GLOBAL",
        "permissions": [OP_MARKING_MANAGE, OP_MARKING_ASSIGN],
        "description": (
            "Marking definition and grant management. Manages data classification "
            "but NOT projects (separation of duties)."
        ),
    },
    {
        "name": "SPACE_OWNER",
        "scope_type": "SPACE",
        "permissions": [
            OP_SPACE_ADMIN,
            OP_PROJECT_ADMIN,
            OP_PROJECT_CREATE,
            OP_ONTOLOGY_VIEW,
            OP_ONTOLOGY_EDIT,
            OP_OBJECT_TYPE_VIEW,
            OP_OBJECT_TYPE_EDIT,
            OP_ACTION_TYPE_VIEW,
            OP_ACTION_TYPE_EDIT,
            OP_LINK_TYPE_VIEW,
            OP_LINK_TYPE_EDIT,
            OP_DATASET_VIEW,
            OP_DATASET_EDIT,
            OP_DATASET_DELETE,
            OP_DATASOURCE_VIEW,
            OP_DATASOURCE_EDIT,
            OP_OBJECT_VIEW,
            OP_OBJECT_WRITE,
            OP_USER_MANAGE,
        ],
        "description": "Space-level owner. Inherits to all Projects under the Space.",
    },
    {
        "name": "SPACE_EDITOR",
        "scope_type": "SPACE",
        "permissions": [
            OP_ONTOLOGY_VIEW,
            OP_ONTOLOGY_EDIT,
            OP_OBJECT_TYPE_VIEW,
            OP_OBJECT_TYPE_EDIT,
            OP_ACTION_TYPE_VIEW,
            OP_ACTION_TYPE_EDIT,
            OP_LINK_TYPE_VIEW,
            OP_LINK_TYPE_EDIT,
            OP_DATASET_VIEW,
            OP_DATASET_EDIT,
            OP_DATASOURCE_VIEW,
            OP_OBJECT_VIEW,
            OP_OBJECT_WRITE,
        ],
        "description": "Space-level editor. Inherits to all Projects under the Space.",
    },
    {
        "name": "SPACE_VIEWER",
        "scope_type": "SPACE",
        "permissions": [
            OP_ONTOLOGY_VIEW,
            OP_OBJECT_TYPE_VIEW,
            OP_ACTION_TYPE_VIEW,
            OP_LINK_TYPE_VIEW,
            OP_DATASET_VIEW,
            OP_DATASOURCE_VIEW,
            OP_OBJECT_VIEW,
        ],
        "description": "Space-level viewer. Inherits to all Projects under the Space.",
    },
    {
        "name": "SPACE_DISCOVERER",
        "scope_type": "SPACE",
        "permissions": [OP_ONTOLOGY_VIEW, OP_OBJECT_TYPE_VIEW],
        "description": "Space-level discoverer. Can see resource names but not data.",
    },
    {
        "name": "OWNER",
        "scope_type": "PROJECT",
        "permissions": [
            OP_PROJECT_ADMIN,
            OP_ONTOLOGY_VIEW,
            OP_ONTOLOGY_EDIT,
            OP_OBJECT_TYPE_VIEW,
            OP_OBJECT_TYPE_EDIT,
            OP_ACTION_TYPE_VIEW,
            OP_ACTION_TYPE_EDIT,
            OP_LINK_TYPE_VIEW,
            OP_LINK_TYPE_EDIT,
            OP_DATASET_VIEW,
            OP_DATASET_EDIT,
            OP_DATASET_DELETE,
            OP_DATASOURCE_VIEW,
            OP_DATASOURCE_EDIT,
            OP_OBJECT_VIEW,
            OP_OBJECT_WRITE,
            OP_USER_MANAGE,
        ],
        "description": "Project owner. Full collaboration permissions within the Project (most common).",
    },
    {
        "name": "EDITOR",
        "scope_type": "PROJECT",
        "permissions": [
            OP_ONTOLOGY_VIEW,
            OP_ONTOLOGY_EDIT,
            OP_OBJECT_TYPE_VIEW,
            OP_OBJECT_TYPE_EDIT,
            OP_ACTION_TYPE_VIEW,
            OP_ACTION_TYPE_EDIT,
            OP_ACTION_TYPE_EXECUTE,
            OP_LINK_TYPE_VIEW,
            OP_LINK_TYPE_EDIT,
            OP_DATASET_VIEW,
            OP_DATASET_EDIT,
            OP_DATASOURCE_VIEW,
            OP_OBJECT_VIEW,
            OP_OBJECT_WRITE,
        ],
        "description": "Project editor. Can edit definitions and execute actions within the Project.",
    },
    {
        "name": "VIEWER",
        "scope_type": "PROJECT",
        "permissions": [
            OP_ONTOLOGY_VIEW,
            OP_OBJECT_TYPE_VIEW,
            OP_ACTION_TYPE_VIEW,
            OP_LINK_TYPE_VIEW,
            OP_DATASET_VIEW,
            OP_DATASOURCE_VIEW,
            OP_OBJECT_VIEW,
        ],
        "description": "Project viewer. Read-only access within the Project.",
    },
    {
        "name": "DISCOVERER",
        "scope_type": "PROJECT",
        "permissions": [OP_ONTOLOGY_VIEW, OP_OBJECT_TYPE_VIEW],
        "description": "Project discoverer. Can see resource names but not data (不可见即安全).",
    },
]


def get_builtin_role_names() -> set[str]:
    """Return the set of built-in role names (for bootstrap idempotency)."""
    return {r["name"] for r in BUILTIN_ROLES}


def is_builtin_role(name: str) -> bool:
    return name in get_builtin_role_names()
