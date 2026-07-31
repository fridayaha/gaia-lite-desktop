"""pydantic v2 schemas for the permission governance system (ADR-016/017).

Strictly separated from SQLAlchemy ORM models (``core/models/permission.py``).
These schemas define the API contract for creating, reading, and updating
permission entities, and carry the runtime Principal object through the
request lifecycle.

Conversion (mirrors the rest of the codebase):
    schema_obj = Organization.model_validate(orm_obj)   # ORM → pydantic
    orm_obj = OrganizationModel(**schema_obj.model_dump(exclude_unset=True))

Phase 0 scope: three-tier containers (Organization/Space/Project) + identity
(User/Group/ServiceUser/Principal) + the runtime Principal carried on
``request.state``. Role/Marking/Policy schemas land in Phase 1-3.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Literal enums (validated at the pydantic layer; ORM stores VARCHAR) ──

OrgType = Literal["INTERNAL", "EXTERNAL"]
ContainerStatus = Literal["ACTIVE", "DISABLED", "ARCHIVED"]
PrincipalType = Literal["USER", "GROUP", "SERVICE_USER"]
PrincipalStatus = Literal["ACTIVE", "DISABLED"]


# ═══════════════════════════════════════════════════════════════════
# Group 1: Three-tier containers
# ═══════════════════════════════════════════════════════════════════


class OrganizationBase(BaseModel):
    """Shared fields for Organization create/read/update."""

    model_config = ConfigDict(from_attributes=True)

    api_name: str = Field(..., description="Unique identifier, e.g. 'org-default' / 'org-vendor-xxx'")
    display_name: str
    description: str = ""
    org_type: OrgType = "INTERNAL"
    status: ContainerStatus = "ACTIVE"


class OrganizationCreate(OrganizationBase):
    """Request body for creating an Organization."""


class OrganizationUpdate(BaseModel):
    """Partial update for an Organization."""

    display_name: str | None = None
    description: str | None = None
    org_type: OrgType | None = None
    status: ContainerStatus | None = None


class Organization(OrganizationBase):
    """Organization response schema (ORM → API)."""

    id: str
    created_at: datetime
    updated_at: datetime


class SpaceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_name: str = Field(..., description="Unique identifier, e.g. 'finance-core' / 'supply-chain'")
    display_name: str
    description: str = ""
    status: ContainerStatus = "ACTIVE"


class SpaceCreate(SpaceBase):
    """Request body for creating a Space.

    Creating a Space atomically creates a same-named Ontology (1:1 binding)
    + a default Project + grants the creator the three-tier Owner roles
    (SpaceService.create_space, design §2.2). The caller does NOT pass
    ontology_id — it is derived.
    """

    # Optional: Organization whitelist to seed (creator's home org is added
    # automatically by SpaceService). Cross-org collaboration adds more later.
    organization_ids: list[str] = Field(default_factory=list)


class SpaceUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    status: ContainerStatus | None = None


class Space(SpaceBase):
    """Space response schema (ORM → API)."""

    id: str
    ontology_id: str
    created_at: datetime
    updated_at: datetime
    # Populated on read when requested (SpaceService joins the whitelist).
    organization_ids: list[str] = Field(default_factory=list)


class ProjectBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    api_name: str = Field(..., description="Unique within the parent Space")
    display_name: str
    description: str = ""
    status: ContainerStatus = "ACTIVE"


class ProjectCreate(ProjectBase):
    """Request body for creating a Project (nested under a Space)."""


class ProjectUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    status: ContainerStatus | None = None


class Project(ProjectBase):
    """Project response schema (ORM → API)."""

    id: str
    space_id: str
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════
# Group 2: Identity layer
# ═══════════════════════════════════════════════════════════════════


class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    subject: str = Field(..., description="OIDC sub claim (immutable IdP-side identifier)")
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Department/region/level synced from OIDC claims — row-level security data source",
    )


class UserCreate(UserBase):
    home_organization: str | None = None


class User(UserBase):
    id: str
    home_organization: str | None = None
    created_at: datetime
    updated_at: datetime


class GroupBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str = ""


class GroupCreate(GroupBase):
    organization_id: str
    parent_group_id: str | None = Field(None, description="Parent group for nesting (recommend ≤ 2 levels)")


class Group(GroupBase):
    id: str
    organization_id: str
    parent_group_id: str | None = None
    created_at: datetime
    updated_at: datetime


class GroupMembershipCreate(BaseModel):
    user_id: str


class ServiceUserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str = ""
    scopes: list[Any] = Field(default_factory=list, description="Accessible Project/ObjectType/API scope limit")


class ServiceUserCreate(ServiceUserBase):
    owner: str = Field(..., description="Responsible user id (key rotation / permission review)")


class ServiceUser(ServiceUserBase):
    id: str
    owner: str
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════
# Runtime Principal (carried on request.state.principal)
# ═══════════════════════════════════════════════════════════════════


class Principal(BaseModel):
    """The resolved principal for the current request.

    Built by PrincipalService.resolve_principal from the OIDC JWT (production,
    Better Auth + Authlib) or the ``X-User-Id`` header (dev fallback). This is
    the complete input to the AuthorizationService five-layer check (Phase 1+).

    Phase 0 carries only identity + attributes; groups/roles/markings are
    populated starting Phase 1 (RBAC) and Phase 2 (MAC). The fields are
    present now so ActionContext.principal is a stable shape across phases.

    ``anonymous`` marks an unauthenticated request (dev mode without
    X-User-Id). The AuthorizationService Layer 1 (identity check) will deny
    non-anonymous resources for anonymous principals (fail-closed).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field("anonymous", description="Principal id; 'anonymous' for unauthenticated dev requests")
    principal_type: PrincipalType = "USER"
    display_name: str = "anonymous"
    # Row-level security data source (department/region/level). Empty for
    # anonymous / before OIDC attribute sync.
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Populated Phase 1+ (RBAC): group ids the principal is a member of
    # (expanded, including nested parent groups).
    groups: list[str] = Field(default_factory=list)
    # Populated Phase 1+ (RBAC): effective role names (resolved from groups).
    roles: list[str] = Field(default_factory=list)
    # Populated Phase 2+ (MAC): marking ids the principal holds.
    markings: list[str] = Field(default_factory=list)
    # Home organization id (Layer 2 MAC isolation). None for anonymous /
    # single-tenant without org assignment.
    home_organization: str | None = None
    is_anonymous: bool = True

    @classmethod
    def anonymous_principal(cls) -> "Principal":
        """The unauthenticated principal (dev mode without X-User-Id).

        Layer 1 of the AuthorizationService denies non-public resources for
        this principal (fail-closed default-deny, design §0.1 principle 4).
        """
        return cls()

    @classmethod
    def from_user(
        cls,
        user: User,
        *,
        groups: list[str] | None = None,
        roles: list[str] | None = None,
        markings: list[str] | None = None,
    ) -> "Principal":
        """Build a Principal from a loaded User + resolved permission sets."""
        return cls(
            id=user.id,
            principal_type="USER",
            display_name=user.email,
            attributes=dict(user.attributes),
            groups=list(groups or []),
            roles=list(roles or []),
            markings=list(markings or []),
            home_organization=user.home_organization,
            is_anonymous=False,
        )


# ═══════════════════════════════════════════════════════════════════
# Authorization results (Phase 1+)
# ═══════════════════════════════════════════════════════════════════


class AccessResult(BaseModel):
    """Result of a single-resource access check (AuthorizationService.check_access).

    Carries the allow/deny decision, which layer decided, and a human-readable
    reason. ``layer`` records which of the five layers intercepted (for the
    Check Access debug panel + audit log, design §1.6 / §7.1).
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    layer: str | None = Field(None, description="Which layer decided: IDENTITY|ORG|SPACE|PROJECT|MARKING|ROW")
    reason: str = ""
    # Missing permissions for the Check Access panel (e.g. "requires Viewer role").
    missing: list[str] = Field(default_factory=list)

    @classmethod
    def allow(cls) -> "AccessResult":
        return cls(allowed=True)

    @classmethod
    def deny(cls, layer: str, reason: str, *, missing: list[str] | None = None) -> "AccessResult":
        return cls(allowed=False, layer=layer, reason=reason, missing=list(missing or []))


class QueryScope(BaseModel):
    """Result of evaluate_query_scope (AuthorizationService, Phase 3+).

    Phase 1: ``forbidden`` reflects the Layer 1-4 decision (anonymous or no
    Project access → forbidden=True → empty results). ``residual`` and
    ``masked_properties`` are populated in Phase 3 (Cedar TPE row-level +
    PropertyMaskingPolicy column-level).
    """

    model_config = ConfigDict(frozen=True)

    # True when the ObjectType is entirely off-limits (any of Layer 1-4 denied).
    # Callers return empty (不可见即安全) — no error, no existence leak.
    forbidden: bool = False
    # Cedar TPE residual (Phase 3): the post-partial-evaluation predicate,
    # already evaluated against the principal, leaving only resource-attribute
    # conditions. Translated to SQL WHERE by the SqlGlot injector.
    residual: str | None = None
    # Properties to mask (return null) for this principal (Phase 3).
    masked_properties: list[str] = Field(default_factory=list)
    # The Project scope that governs this ObjectType (option B fallback: when
    # the ObjectType's project_id is NULL, this is the Ontology's owning
    # Space's default Project).
    project_scope: str | None = None


# ═══════════════════════════════════════════════════════════════════
# Resource ownership chain (PIP data structure, internal)
# ═══════════════════════════════════════════════════════════════════


class ResourceOwnership:
    """The resolved ownership chain for a resource (PIP, design §0.4).

    Carries the Organization/Space/Project ids that Layers 2-4 need,
    pre-resolved so the five-layer check doesn't re-query per layer.
    ``PostgresMetaStore.resolve_resource_ownership`` builds this.

    This is a plain class (not pydantic) — it's an internal PIP data
    structure passed between the meta_store and AuthorizationService, never
    serialized to the API.
    """

    __slots__ = ("resource_type", "resource_id", "organization_ids", "space_id", "project_id")

    def __init__(
        self,
        *,
        resource_type: str,
        resource_id: str,
        organization_ids: list[str],
        space_id: str,
        project_id: str | None,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.organization_ids = organization_ids
        self.space_id = space_id
        self.project_id = project_id


# ═══════════════════════════════════════════════════════════════════
# Marking MAC (Phase 2)
# ═══════════════════════════════════════════════════════════════════


class MarkingCategoryBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str = ""


class MarkingCategoryCreate(MarkingCategoryBase):
    pass


class MarkingCategory(MarkingCategoryBase):
    id: str
    is_system: bool
    created_at: datetime
    updated_at: datetime


class MarkingBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    display_name: str = ""
    description: str = ""


class MarkingCreate(MarkingBase):
    category_id: str


class Marking(MarkingBase):
    id: str
    category_id: str
    is_system: bool
    source_organization_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MarkingGrantCreate(BaseModel):
    """Grant a marking to a group (MARKING_ADMIN only)."""

    group_id: str
    expires_at: datetime | None = None


class MarkingAssignmentCreate(BaseModel):
    """Apply a marking to a resource (PROJECT_OWNER/EDITOR only)."""

    marking_id: str


class MarkingAssignment(BaseModel):
    """A marking applied to a resource."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_type: str
    resource_id: str
    marking_id: str
    is_directly_applied: bool
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# Audit + Check Access + JIT (Phase 4)
# ═══════════════════════════════════════════════════════════════════


class AuditLog(BaseModel):
    """An append-only audit log entry (design §1.6)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: datetime
    principal_id: str | None
    resource_type: str
    resource_id: str
    action: str
    result: Literal["ALLOW", "DENY"]
    reason: str = ""
    layer: str | None = None
    request_id: str | None = None


class CheckAccessResult(BaseModel):
    """Result of GET /authz/check — the explainability API (design §7.1).

    Returns the five-layer decision + per-layer status + the permission
    provenance (which Group → which Role → user) + missing permissions.
    Powers the Check Access debug panel + Agent self-probing + audit trace.
    """

    principal_id: str
    resource_type: str
    resource_id: str
    action: str
    decision: Literal["ALLOW", "DENY"]
    layer: str | None = Field(None, description="Which layer decided")
    reason: str = ""
    # Per-layer status (for the stepper visualization).
    layers: dict[str, bool] = Field(default_factory=dict)
    # Missing permissions (for the "request access" CTA).
    missing: list[str] = Field(default_factory=list)
    # Permission provenance: which groups/roles granted access (when ALLOW).
    provenance: list[str] = Field(default_factory=list)


class AccessRequestBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_type: Literal["ROLE_ASSIGNMENT", "MARKING_GRANT"]
    requested_item: str = Field(..., description="Role name or marking name")
    scope_type: str | None = None
    scope_id: str | None = None
    justification: str
    expires_at: datetime | None = Field(None, description="Requested duration (grant expiry on approval)")


class AccessRequestCreate(AccessRequestBase):
    pass


class AccessRequest(AccessRequestBase):
    id: str
    requester_id: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
    reviewer_id: str | None = None
    review_comment: str = ""
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AccessRequestReview(BaseModel):
    """Approve or reject an access request."""

    review_comment: str = ""


# ═══════════════════════════════════════════════════════════════════
# Role Assignment (design §7.3 — grant roles to Groups, not individuals)
# ═══════════════════════════════════════════════════════════════════


RoleScopeType = Literal["GLOBAL", "SPACE", "PROJECT"]


class RoleAssignmentCreate(BaseModel):
    """Grant a role to a Group at a scope (组授权铁律: Group, not User).

    ``role_name`` resolves to a builtin role (VIEWER/EDITOR/OWNER/...). The
    scope determines where the role applies: SPACE-level inherits to all
    Projects under the Space; PROJECT-level applies only within that Project.
    """

    group_id: str
    role_name: str = Field(..., description="Builtin role name: VIEWER/EDITOR/OWNER/DISCOVERER/SPACE_*/PLATFORM_ADMIN")
    scope_type: RoleScopeType
    scope_id: str | None = Field(None, description="Space/Project id; NULL for GLOBAL")
    expires_at: datetime | None = Field(None, description="JIT temporary grant expiry; NULL = permanent")


class RoleAssignmentResponse(BaseModel):
    """A granted role assignment (ORM → API)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str = Field(..., description="The Principal (Group) the role is granted to")
    role_name: str
    scope_type: RoleScopeType
    scope_id: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime



# ═══════════════════════════════════════════════════════════════════
# Ship-the-decision envelope (design §8.2)
# ═══════════════════════════════════════════════════════════════════


class AllowedActionsRequest(BaseModel):
    """Batch request: resolve allowedActions for N resources at once.

    The frontend calls ``POST /authz/allowed-actions`` once per page load
    (not per resource) to get every resource's permission decisions in a
    single round-trip. This is the ship-the-decision channel — the frontend
    renders state from these decisions rather than re-deriving rules or
    calling ``/authz/check`` per resource.
    """

    resource_type: str
    resource_ids: list[str]


class AllowedActionsResponse(BaseModel):
    """Per-resource allowedActions + disabledReasons (ship-the-decision)."""

    resource_type: str
    # Keyed by resource_id → {allowedActions, disabledReasons}.
    decisions: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Row/Column Security Policies (Phase 3, ABAC) + LLM-assisted generation
# ═══════════════════════════════════════════════════════════════════


class RowSecurityPolicyBase(BaseModel):
    """Base fields for a row-level security policy (Cedar condition)."""

    object_type_id: str
    # Cedar policy condition expression referencing principal.attributes / resource.
    # Example: ``principal.attributes.region == resource.region``
    expression: str
    description: str = ""


class RowSecurityPolicyCreate(RowSecurityPolicyBase):
    """Create a row security policy.

    ``generated_by`` tracks whether the expression was authored by a human
    ("manual") or generated by the LLM policy assistant ("llm"). When
    ``generated_by="llm"``, ``generation_meta`` records the prompt, model,
    and reviewer for provenance/audit (ADR-017 D6, Phase 7).
    """

    generated_by: str = "manual"
    generation_meta: dict[str, Any] = Field(default_factory=dict)


class RowSecurityPolicy(RowSecurityPolicyBase):
    """A row security policy (ORM → API)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str = "ACTIVE"
    generated_by: str = "manual"
    generation_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ── LLM-assisted policy generation (ADR-017 D6, Phase 7) ──────────────


class PolicyGenerationRequest(BaseModel):
    """Request: generate a Cedar row-security policy from natural language.

    The caller provides the NL requirement (e.g. "sales reps can only see
    customers in their own region"), the target ObjectType id, and sample
    principal/resource attribute values for the floor/ceiling dry-run
    preview (ADR-017 D6, AutoCedar verifier-guided paradigm).
    """

    object_type_id: str
    natural_language: str
    # Sample principal for dry-run preview (Floor: should be allowed).
    sample_principal_id: str
    sample_principal_attributes: dict[str, Any] = Field(default_factory=dict)
    sample_principal_markings: list[str] = Field(default_factory=list)
    # Sample resource attribute sets for floor/ceiling preview.
    # floor_resources: rows the principal SHOULD see (decision must be Allow).
    # ceiling_resources: rows the principal should NOT see (decision must be Deny).
    floor_resources: list[dict[str, Any]] = Field(default_factory=list)
    ceiling_resources: list[dict[str, Any]] = Field(default_factory=list)


class PolicyPreviewResult(BaseModel):
    """Dry-run preview for one sample resource (floor or ceiling check)."""

    resource_attributes: dict[str, Any]
    expected: str  # "allow" or "deny"
    actual: str  # Cedar decision: "Allow" / "Deny" / "NoDecision"
    passed: bool


class PolicyGenerationResult(BaseModel):
    """Result of LLM-assisted policy generation.

    The ``expression`` is a Cedar condition validated by cedarpy
    ``validate_policies`` (syntax + type check against the ObjectType's
    schema). The ``previews`` show floor/ceiling dry-run results so the
    user can confirm semantic correctness before HITL approval (the
    expression is NOT auto-saved — the user must explicitly POST
    RowSecurityPolicyCreate after review).
    """

    expression: str
    explanation: str
    confidence: float = 0.0
    # Validation status from cedarpy validate_policies.
    validation_passed: bool
    validation_errors: list[str] = Field(default_factory=list)
    # Floor/ceiling dry-run preview (empty if no samples provided).
    previews: list[PolicyPreviewResult] = Field(default_factory=list)
    # The Cedar schema used for validation (for transparency/debugging).
    schema_used: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Option B→A migration (ADR-016 D3, resource ownership evolution)
# ═══════════════════════════════════════════════════════════════════


class MigrationImpactEntry(BaseModel):
    """One group's permission change when migrating an ObjectType to a new Project.

    ``status`` is "gain" (group gains access), "lose" (group loses access),
    or "unchanged" (group has the same role in both Projects).
    """

    group_id: str
    group_name: str
    current_role: str | None = None
    target_role: str | None = None
    status: str  # gain | lose | unchanged


class MigrationImpact(BaseModel):
    """Impact analysis for migrating an ObjectType to a different Project.

    Compares role assignments on the current Project (option B fallback or
    current option A Project) vs the target Project. Lets the admin preview
    which groups gain/lose access before committing the migration
    (ADR-016 D3, Palantir migration guide: "once migrated, cannot revert").
    """

    object_type_id: str
    object_type_api_name: str
    current_project_id: str
    current_project_name: str
    target_project_id: str
    target_project_name: str
    entries: list[MigrationImpactEntry] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)  # {gain: N, lose: N, unchanged: N}
