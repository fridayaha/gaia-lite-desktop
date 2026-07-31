"""SQLAlchemy 2.0 ORM models for the permission governance system (ADR-016/017).

Four groups of tables, each with a single responsibility, linked by FK only
(no cross-group JOIN cascades — the AuthorizationService queries them in
separate steps and caches results):

  Group 1 — Three-tier containers (resource ownership skeleton):
      Organization → Space (1:1 Ontology) → Project

  Group 2 — Identity layer (who):
      Principal (polymorphic base) ← User / Group / ServiceUser
      Group ↔ User via GroupMembership

  Group 3 — Permission rules (what they can do) — Phase 1+:
      Role + RoleAssignment (RBAC)
      Marking + MarkingGrant + MarkingAssignment (MAC)
      RowSecurityPolicy + PropertyMaskingPolicy (ABAC)

  Group 4 — Governance evidence (after-the-fact) — Phase 4+:
      AuditLog (append-only) / AccessRequest (JIT)

This module implements **Phase 0**: Group 1 (containers) + Group 2 (identity)
+ resource-ownership columns on the existing models. Groups 3/4 are added in
later phases.

Key design decisions (see design doc §1):
- Space↔Ontology 1:1 is the hardest constraint: ``ontology_id`` is unique +
  ondelete=RESTRICT (Ontology is a core asset; deletion is irreversible).
- Organization is the MAC isolation boundary; it derives a system Marking
  (Phase 2) — all users/resources of an org auto-carry it.
- Project is the permission atomic unit (inheritance + cache unit), NOT a
  semantic unit — that's Ontology's job. The two are orthogonal.
- PrincipalModel is a polymorphic base so RoleAssignment/MarkingGrant can
  reference any principal type (User/Group/ServiceUser) uniformly.
- 100% of permissions are granted to Groups, never to individuals (组授权铁律).
- Ownership columns (space_id/project_id) are added nullable-first and
  backfilled to NOT NULL in a later revision (design §9.1).

Conventions follow the rest of the codebase:
- UUID v4 hex primary keys (``new_uuid``)
- VARCHAR for enums (validated at the pydantic layer)
- JSONB for flexible fields (``JSONBType`` — JSONB on PG, JSON on SQLite)
- ``utcnow()`` naive-UTC timestamps (see defaults.py for why naive)
- ``ON DELETE CASCADE`` unless noted (RESTRICT for Ontology, SET NULL for
  resource ownership — business assets are preserved on Project deletion)
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ontology.core.models.defaults import JSONBType, new_uuid, utcnow
from ontology.core.models.ontology import Base

# ═══════════════════════════════════════════════════════════════════
# Group 1: Three-tier containers (Organization / Space / Project)
# ═══════════════════════════════════════════════════════════════════


class OrganizationModel(Base):
    """Organization — subject-level MAC isolation boundary (ADR-016 D1).

    Answers "whose data is this" — tenant-level hard isolation. Each
    Organization derives a system-level built-in Marking (Phase 2): all
    users belonging to the org auto-hold it, all resources auto-carry it.
    This is the底层 implementation of subject isolation (对齐 Palantir:
    Organization is an access requirement, parallel to Marking).

    Single-tenant deployments create one ``org-default`` transparently
    (progressive disclosure); Organizations are fully isolated by default,
    and cross-org collaboration goes through a shared Space (both sides
    added to the Space's org whitelist).
    """

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # INTERNAL (internal employees' home org) | EXTERNAL (vendor/customer).
    # Affects default visibility (external orgs are fully hidden by default).
    org_type: Mapped[str] = mapped_column(String(20), default="INTERNAL", server_default=text("'INTERNAL'"))
    # ACTIVE | DISABLED. DISABLED invalidates all of the org's users' permissions
    # immediately (batch offboarding).
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    spaces: Mapped[list["SpaceModel"]] = relationship(
        secondary="space_organizations", back_populates="organizations"
    )


class SpaceModel(Base):
    """Space — business-domain container + Ontology lifecycle carrier (ADR-016 D1/D2).

    Answers "which business domain's data is this". The core constraint is
    ``ontology_id`` unique=True — **one Space maps to exactly one Ontology**
    (1:1 strong binding, Palantir's hardest constraint). Creating a Space
    auto-creates a same-named Ontology; deleting a Space makes the Ontology
    unrecoverable; an Ontology cannot be reused across Spaces.

    Why 1:1 not 1:N: 1:N lets the same business object be defined in multiple
    ontologies, breaking lineage and preventing JOINs. 1:1 forces "one
    business domain, one semantic model" — ontology bloat is handled by
    ObjectTypeGroup (semantic grouping), permission subdivision by
    Project + Marking, never by splitting the Ontology.

    Space binds an Organization whitelist (SpaceOrganization): only users
    from whitelisted orgs may access the Space. This is the only legal
    channel for cross-org collaboration.

    Palantir's Space also binds infrastructure (Spark/storage/encryption/
    billing) — Gaia has no Spark and storage is multi-engine unified, so
    that binding is intentionally dropped. Only "container + org whitelist
    + Ontology binding" semantics remain.
    """

    __tablename__ = "spaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # ⚠️ Space↔Ontology 1:1 strong binding: unique=True + ondelete=RESTRICT.
    # ondelete=RESTRICT forces explicit Ontology migration before Space
    # deletion (prevents irreversible误删 of a core asset). Space deletion
    # must first unbind/migrate the Ontology.
    ontology_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("ontologies.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    # ACTIVE | ARCHIVED.
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    organizations: Mapped[list["OrganizationModel"]] = relationship(
        secondary="space_organizations", back_populates="spaces"
    )
    projects: Mapped[list["ProjectModel"]] = relationship(
        back_populates="space", cascade="all, delete-orphan"
    )


class SpaceOrganizationModel(Base):
    """Space↔Organization whitelist association (cross-org collaboration channel).

    Only users whose home_organization (or guest orgs) is in a Space's
    whitelist may access that Space. This is the sole legal path for
    cross-org data sharing — there is no other bypass.
    """

    __tablename__ = "space_organizations"

    space_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_space_organizations_org", "organization_id"),
    )


class ProjectModel(Base):
    """Project — collaboration permission boundary (permission atomic unit).

    Answers "which collaboration unit can do what on this set of resources".
    Project is the **inheritance unit + cache unit** for permissions, NOT a
    semantic unit (that's Ontology's job). A Space may have multiple
    Projects (data team / ontology team / app team permission separation)
    sharing one Ontology.

    Resources (Dataset/SyncTask/Datasource/Credential) belong to a Project
    via their own table's ``project_id``. Definition-class resources
    (ObjectType/ActionType/...) belong to the Ontology in Phase 0 (option B
    simplification) with a nullable ``project_id`` reserved for future
    migration to option A (definitions can live in a Project).

    Project deletion SET NULLs resources' ``project_id`` (business assets are
    preserved, not cascaded — they become "unowned" and need reassignment).
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # api_name is unique within a Space (not globally), because Project
    # belongs to a Space.
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    space_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ACTIVE | ARCHIVED.
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    space: Mapped["SpaceModel"] = relationship(back_populates="projects")

    __table_args__ = (
        UniqueConstraint("space_id", "api_name", name="uq_projects_space_api_name"),
        {"comment": "协作权限边界（权限原子单位）"},
    )


# ═══════════════════════════════════════════════════════════════════
# Group 2: Identity layer (Principal / User / Group / ServiceUser)
# ═══════════════════════════════════════════════════════════════════


class PrincipalModel(Base):
    """Principal — polymorphic base for all authorizable subjects.

    The grant target of a permission may be a User, Group, or ServiceUser.
    Using a polymorphic base lets RoleAssignment/MarkingGrant reference
    ``principal_id`` uniformly without a per-type authorization table.

    The tradeoff vs. putting ``principal_type + principal_id`` directly on
    RoleAssignment: the base class gives subjects unified status/display_name
    management and lets future subject types (e.g. machine groups) be added
    without breaking the authorization tables.
    """

    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # USER | GROUP | SERVICE_USER. Drives which subtype table holds the
    # subject's details. Phase 1's AuthorizationService reads this to know
    # how to expand group membership / load attributes.
    principal_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    # ACTIVE | DISABLED. DISABLED principals are excluded from authorization
    # (Layer 1 identity check).
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserModel(Base):
    """User — natural person; ``attributes`` is the row-level security data source.

    ``attributes`` (JSONB) holds department/region/level etc. synced from
    OIDC claims. These are the evaluation inputs for RowSecurityPolicy
    expressions (e.g. ``principal.attributes['region'] == row['region']``).
    Attribute changes (转岗) automatically cascade to row-level permissions
    because every request re-resolves the principal (or cache-invalidates).

    ``subject`` is the OIDC IdP-side unique identifier (sub claim) — used to
    map a verified JWT to a Gaia User. We don't use email directly (email
    can change; sub cannot).

    ``home_organization`` is the user's primary org (unique, immutable per
    Palantir). Single-tenant deployments may leave it null; multi-tenant
    deployments use it to determine the default visibility scope.
    """

    __tablename__ = "users"

    # = principal_id (1:1 with PrincipalModel). Kept as a separate PK rather
    # than FK+PK to keep User queries independent of the principals table.
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # OIDC sub claim — the immutable IdP-side identifier.
    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Department / region / level etc. synced from OIDC claims. **This is the
    # key for row-level security** — RowSecurityPolicy expressions reference
    # these attributes for filtering. JSONB (not a relation table) because
    # attributes are flat key-value with simple lookup patterns and IdP
    # claim structure is variable.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, server_default=text("'{}'"))
    # Primary org (unique). Nullable in single-tenant deployments.
    home_organization: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GroupModel(Base):
    """Group — the sole permission carrier (组授权铁律).

    100% of permissions are granted to Groups, never to individuals. Users
    gain permissions by joining a Group. This is the foundation of
    governable/auditable/operable permissions: personnel changes (join/
    transfer/leave) only touch Group membership, never resource permissions.

    ``organization_id`` is required: a group belongs to exactly one org and
    cannot be reused across orgs (对齐 Palantir). This preserves org
    isolation integrity — a group cannot become a cross-org permission
   渗透 channel.

    ``parent_group_id`` supports nesting (child groups inherit parent
    permissions), but ≤ 2 levels is recommended. Deep nesting makes
    permission provenance opaque and risks accidental over-granting.
    """

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # Group belongs to exactly one org (cross-org group reuse forbidden).
    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nesting (child inherits parent permissions). Recommend ≤ 2 levels.
    parent_group_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    parent: Mapped["GroupModel | None"] = relationship(
        "GroupModel", remote_side="GroupModel.id", back_populates="children"
    )
    children: Mapped[list["GroupModel"]] = relationship(
        "GroupModel", back_populates="parent", cascade="all, delete-orphan"
    )

    # name is unique within an organization (not globally).
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_groups_org_name"),
    )


class GroupMembershipModel(Base):
    """User↔Group membership relation.

    Personnel changes (join/transfer/leave) only touch this table — resource
    permissions stay untouched (组授权铁律).
    """

    __tablename__ = "group_memberships"

    group_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_group_memberships_user", "user_id"),
    )


class ServiceUserModel(Base):
    """ServiceUser — non-human principal for Agent/API/pipeline integration.

    Key design is ``scopes`` — limits the accessible Project/ObjectType/API,
    so even a leaked key can only operate within its bounded scope. Zero
    default permissions on creation (must be manually granted, 对齐 Palantir).

    One ServiceUser per integration scenario (for audit + revocation).
    ``owner`` is required so key rotation / permission review has an owner.
    """

    __tablename__ = "service_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # Scope limit: accessible Project/ObjectType/API list. Even with a leaked
    # key, scope bounds the blast radius.
    scopes: Mapped[list[Any]] = mapped_column(JSONBType, default=list, server_default=text("'[]'"))
    # Responsible person — required. Owns key rotation / permission review.
    owner: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ═══════════════════════════════════════════════════════════════════
# Group 3: Permission rules — RBAC (Phase 1)
# Marking/Policy (MAC/ABAC) land in Phase 2-3; AuditLog in Phase 4.
# ═══════════════════════════════════════════════════════════════════


class RoleModel(Base):
    """Role — a set of operations (ADR-016 D4, Phase 1).

    对齐 Palantir: "Roles are sets of operations". A role is not an abstract
    concept but a packaged set of atomic operations. Granting a role grants
    a set of operations + sub-resource inheritance. Phase 1 stores the
    operation list in ``permissions`` JSONB (no separate Operation table).

    Role tiers + separation of duties (design §1.3):
      GLOBAL  — PLATFORM_ADMIN / AUDIT_ADMIN / MARKING_ADMIN
      SPACE   — SPACE_OWNER / SPACE_EDITOR / SPACE_VIEWER / SPACE_DISCOVERER
      PROJECT — OWNER / EDITOR / VIEWER / DISCOVERER (most common, granted to Group)

    Separation of duties is a security baseline: MARKING_ADMIN manages data
    classification but not projects; PROJECT_OWNER manages collaboration but
    not classifications; PLATFORM_ADMIN has no data access by default.
    """

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # OWNER | EDITOR | VIEWER | DISCOVERER | SPACE_OWNER | SPACE_EDITOR |
    # SPACE_VIEWER | SPACE_DISCOVERER | PLATFORM_ADMIN | AUDIT_ADMIN | MARKING_ADMIN
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # GLOBAL | SPACE | PROJECT
    permissions: Mapped[list[Any]] = mapped_column(JSONBType, default=list, server_default=text("'[]'"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # Built-in roles are seeded by the bootstrap and cannot be deleted
    # (system-managed). Custom roles (Phase 2+) set this to False.
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RoleAssignmentModel(Base):
    """RoleAssignment — grants a Role to a Principal at a scope (Phase 1).

    组授权铁律: ``principal_id`` is typically a Group id (permissions are
    granted to Groups, never individuals). Users gain roles via Group
    membership.

    ``scope_type + scope_id``: the role's scope. SPACE-level roles inherit to
    all Projects under the Space; PROJECT-level roles apply only within that
    Project. GLOBAL roles (PLATFORM_ADMIN etc.) have scope_id = NULL.

    ``expires_at``: JIT temporary-permission expiry (auto-revoked by a
    background sweep, Phase 4). NULL = permanent.

    Option B fallback (design §0.5): when a definition-class resource's
    ``project_id`` is NULL, the AuthorizationService Layer 4 falls back to
    the Ontology's owning Space's default Project role. This keeps callers
    unaware of the option A/B distinction.
    """

    __tablename__ = "role_assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # Typically a Group id (组授权铁律). Users inherit via GroupMembership.
    principal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # GLOBAL | SPACE | PROJECT
    # Space id (SPACE scope) / Project id (PROJECT scope) / NULL (GLOBAL scope).
    scope_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # JIT temporary permission expiry. NULL = permanent.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # A principal gets a role at most once per (role, scope) — prevents
        # duplicate grants that would confuse the effective-role resolution.
        UniqueConstraint("principal_id", "role_id", "scope_type", "scope_id",
                         name="uq_role_assignments_principal_role_scope"),
    )


# ═══════════════════════════════════════════════════════════════════
# Group 3: Permission rules — MAC / Marking (Phase 2)
# ═══════════════════════════════════════════════════════════════════


class MarkingCategoryModel(Base):
    """Marking category — classification axis (data sensitivity / type / partition).

    Categories group markings: e.g. "DataSensitivity" (机密/秘密/公开),
    "DataType" (PII/PHI/PCI), "BusinessPartition" (华东/华南). A resource
    carries markings from (potentially multiple) categories, and a principal
    must hold ALL of them (合取 AND) to access the resource (design §1.4).

    ``is_system=True`` marks categories auto-derived from an Organization
    (the org's system marking — subject isolation, MAC). System categories
    cannot be deleted by users.
    """

    __tablename__ = "marking_categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # True for the Organization-derived system category (subject isolation).
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MarkingModel(Base):
    """Marking — a classification value (机密 / PII / 华东) (Phase 2).

    A marking belongs to a category. Resources carry markings via
    MarkingAssignment; principals hold markings via MarkingGrant (granted to
    Groups, 组授权铁律). Layer 5 (MAC) checks the resource's markings are a
    subset of the principal's markings (合取 AND).

    Organization↔Marking linkage (design §1.4): creating an Organization
    auto-derives a system marking (``is_system=True``, ``source_organization_id``
    pointing to the org). The org's users auto-hold it, the org's resources
    auto-carry it. System markings cannot be manually removed.
    """

    __tablename__ = "markings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    category_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("marking_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # True for Organization-derived system markings (subject isolation).
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # The org this system marking was derived from (NULL for user-created).
    source_organization_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # A marking name is unique within its category.
        UniqueConstraint("category_id", "name", name="uq_markings_category_name"),
    )


class MarkingGrantModel(Base):
    """MarkingGrant — grants a marking to a Group (Phase 2, 组授权铁律).

    Separation of duties (design §2.4): MarkingGrant is created by
    MARKING_ADMIN (manages data classification), NOT by PROJECT_OWNER.
    This prevents a project owner from loosening classification data access
    (MAC vs DAC separation). Users gain markings via their Group membership.

    ``expires_at``: JIT temporary marking grant expiry (Phase 4). NULL = permanent.
    """

    __tablename__ = "marking_grants"

    group_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    marking_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("markings.id", ondelete="CASCADE"), primary_key=True
    )
    # JIT temporary grant expiry. NULL = permanent.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarkingAssignmentModel(Base):
    """MarkingAssignment — a marking applied to a resource (Phase 2).

    Polymorphic reference: ``resource_type + resource_id`` can point to an
    ObjectType / Property / Ontology / Dataset. Layer 5 collects all
    markings on a resource and checks the principal holds every one (合取 AND).

    Separation of duties (design §2.4): MarkingAssignment is created by
    PROJECT_OWNER/EDITOR (applies an EXISTING marking to a resource), NOT by
    MARKING_ADMIN (who can only define/grant markings, not apply them). This
    split prevents either role from single-handedly loosening data access.

    ``is_directly_applied``: aligns with Palantir — distinguishes direct
    marking vs inherited (Phase 2: all direct; blood-lineage propagation is
    Phase 6). One-off manual marking for Phase 2.
    """

    __tablename__ = "marking_assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # OBJECT_TYPE | PROPERTY | ONTOLOGY | DATASET | DATA_SOURCE | SYNC_TASK
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    marking_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("markings.id", ondelete="CASCADE"), nullable=False
    )
    is_directly_applied: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        # A marking is applied at most once per (resource, marking).
        UniqueConstraint("resource_type", "resource_id", "marking_id",
                         name="uq_marking_assignments_resource_marking"),
    )


# ═══════════════════════════════════════════════════════════════════
# Group 3: Permission rules — ABAC row/column (Phase 3)
# ═══════════════════════════════════════════════════════════════════


class RowSecurityPolicyModel(Base):
    """RowSecurityPolicy — ObjectType-level row filtering (Phase 3, ABAC).

    The ``expression`` is a Cedar policy condition referencing
    ``principal.attributes`` (and/or ``resource`` attributes). At query time
    the AuthorizationService runs Cedar ``is_authorized_partial`` with the
    principal known but the resource unknown, producing a **residual** that
    describes which resource-attribute conditions decide visibility. The
    residual is translated to a SQL WHERE predicate and injected via SqlGlot
    AST into the query (design §4, ADR-017 D4).

    Example expression::

        principal.attributes.region == resource.region

    This is ABAC (attribute-driven), not RBAC — one policy covers all rows of
    the ObjectType, avoiding role explosion. Combined with
    PropertyMaskingPolicy (column) = cell-level (对齐 Palantir Object +
    Property Security Policy).
    """

    __tablename__ = "row_security_policies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    object_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("object_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Cedar policy condition expression (references principal.attributes / resource).
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    # Provenance: "manual" (human-authored) or "llm" (LLM-assisted, ADR-017 D6).
    # When "llm", generation_meta records {prompt, model, ts, reviewer} for audit.
    generated_by: Mapped[str] = mapped_column(String(20), default="manual", server_default=text("'manual'"))
    generation_meta: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # One active policy per ObjectType (the expression is a single
        # conjunctive condition; complex rules compose with &&).
        UniqueConstraint("object_type_id", name="uq_row_security_policies_object_type"),
    )


class PropertyMaskingPolicyModel(Base):
    """PropertyMaskingPolicy — column-level masking (Phase 3, ABAC).

    The ``expression`` is a Cedar condition. When it evaluates to false for
    the current principal, the property is returned as null (masked). Combined
    with RowSecurityPolicy (row) = cell-level visibility.

    Example expression::

        "PII" in principal.markings

    (The principal must hold the PII marking to see this column.)
    """

    __tablename__ = "property_masking_policies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    property_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Cedar condition: when false, the property is masked (returned as null).
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        # One active masking policy per property.
        UniqueConstraint("property_id", name="uq_property_masking_policies_property"),
    )


# ═══════════════════════════════════════════════════════════════════
# Group 4: Governance evidence — audit + JIT (Phase 4)
# ═══════════════════════════════════════════════════════════════════


class AuditLogModel(Base):
    """AuditLog — append-only permission decision log (Phase 4, design §1.6).

    Every AuthorizationService.check_access decision is recorded here: who
    asked for what, on which resource, the result (ALLOW/DENY), and which
    layer decided (IDENTITY/ORG/SPACE/PROJECT/MARKING/ROW). The ``layer``
    field powers the Check Access debug panel + audit analysis (which layer
    denies most → configuration issue to fix).

    Immutability (design §1.6):
      - The application exposes ONLY ``append()`` (no UPDATE/DELETE method).
      - DB role permissions restrict the table to INSERT + SELECT (Phase 5+).
      - Append-only pattern (only new rows, never modify history).

    A SHA-256 hash chain (``content_hash`` linking to ``previous_hash``) is a
    Phase 6 enhancement for SOC 2 tamper-evidence; Phase 4 keeps it simple.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    principal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # ALLOW | DENY
    result: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    # Human-readable reason (which layer intercepted, what was missing).
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # IDENTITY | ORG | SPACE | PROJECT | MARKING | ROW | ALLOW
    layer: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Trace/request correlation (links to X-Trace-ID for end-to-end tracing).
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class AccessRequestModel(Base):
    """AccessRequest — JIT permission self-service request (Phase 4, design §7.1).

    A user requests temporary elevated access (a role or marking) they don't
    currently hold. The request flows PENDING → APPROVED/REJECTED/EXPIRED.
    When approved, a time-limited RoleAssignment/MarkingGrant is created
    (``expires_at``), auto-revoked on expiry.

    This is the JIT (Just-in-Time) permission pattern — temporary needs go
    through self-service + approval + auto-revocation, rather than
    pre-granting standing high privileges. Reduces zombie permissions and
    the blast radius of a compromised account.
    """

    __tablename__ = "access_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    # The principal requesting access.
    requester_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # What's being requested: ROLE_ASSIGNMENT or MARKING_GRANT.
    request_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # For ROLE_ASSIGNMENT: the role name. For MARKING_GRANT: the marking name.
    requested_item: Mapped[str] = mapped_column(String(100), nullable=False)
    # Scope for role requests (scope_type + scope_id); NULL for marking grants.
    scope_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Justification (required — the "why" for the audit trail).
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    # PENDING | APPROVED | REJECTED | EXPIRED
    status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default=text("'PENDING'"), index=True)
    # Who approved/rejected (NULL while PENDING).
    reviewer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Requested duration (the grant expires at this time when approved).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
