// Permission governance API client (ADR-016/017 Phase 5).
//
// Wraps the backend /auth, /authz, /marking-* , /audit-logs routes.
// All calls go through the shared `request` helper (consistent error
// handling via ApiError).

import { request, ApiError } from './client';

// ── Types ──

export interface Principal {
  id: string;
  principal_type: 'USER' | 'GROUP' | 'SERVICE_USER';
  display_name: string;
  attributes: Record<string, string>;
  groups: string[];
  roles: string[];
  markings: string[];
  home_organization: string | null;
  is_anonymous: boolean;
}

export interface CheckAccessResult {
  principal_id: string;
  resource_type: string;
  resource_id: string;
  action: string;
  decision: 'ALLOW' | 'DENY';
  layer: string | null;
  reason: string;
  layers: Record<string, boolean>;
  missing: string[];
  provenance: string[];
}

export interface AccessRequest {
  id: string;
  requester_id: string;
  request_type: 'ROLE_ASSIGNMENT' | 'MARKING_GRANT';
  requested_item: string;
  scope_type: string | null;
  scope_id: string | null;
  justification: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED';
  reviewer_id: string | null;
  review_comment: string;
  reviewed_at: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AccessRequestCreate {
  request_type: 'ROLE_ASSIGNMENT' | 'MARKING_GRANT';
  requested_item: string;
  scope_type?: string | null;
  scope_id?: string | null;
  justification: string;
  expires_at?: string | null;
}

export interface AuditLog {
  id: string;
  timestamp: string;
  principal_id: string | null;
  resource_type: string;
  resource_id: string;
  action: string;
  result: 'ALLOW' | 'DENY';
  reason: string;
  layer: string | null;
  request_id: string | null;
}

export interface MarkingCategory {
  id: string;
  name: string;
  description: string;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

export interface Marking {
  id: string;
  category_id: string;
  name: string;
  display_name: string;
  description: string;
  is_system: boolean;
  source_organization_id: string | null;
  created_at: string;
  updated_at: string;
}

// ── API calls ──

export async function getMe(): Promise<Principal> {
  return request<Principal>('/auth/me');
}

export async function checkAccess(
  resourceType: string,
  resourceId: string,
  action: string,
): Promise<CheckAccessResult> {
  const params = new URLSearchParams({
    resource_type: resourceType,
    resource_id: resourceId,
    action,
  });
  return request<CheckAccessResult>(`/authz/check?${params}`);
}

export async function listAccessRequests(pendingOnly = false): Promise<AccessRequest[]> {
  return request<AccessRequest[]>(`/authz/access-requests?${pendingOnly ? 'pending_only=true' : ''}`);
}

export async function createAccessRequest(body: AccessRequestCreate): Promise<AccessRequest> {
  return request<AccessRequest>('/authz/access-requests', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function approveAccessRequest(id: string, reviewComment = ''): Promise<AccessRequest> {
  return request<AccessRequest>(`/authz/access-requests/${id}/approve`, {
    method: 'POST',
    body: JSON.stringify({ review_comment: reviewComment }),
  });
}

export async function rejectAccessRequest(id: string, reviewComment = ''): Promise<AccessRequest> {
  return request<AccessRequest>(`/authz/access-requests/${id}/reject`, {
    method: 'POST',
    body: JSON.stringify({ review_comment: reviewComment }),
  });
}

export async function listAuditLogs(params: {
  principal_id?: string;
  resource_type?: string;
  result?: string;
  layer?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<AuditLog[]> {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const qs = sp.toString();
  return request<AuditLog[]>(`/authz/audit-logs${qs ? `?${qs}` : ''}`);
}

export async function listMarkingCategories(): Promise<MarkingCategory[]> {
  return request<MarkingCategory[]>('/marking-categories');
}

export async function listMarkings(categoryId?: string): Promise<Marking[]> {
  const qs = categoryId ? `?category_id=${categoryId}` : '';
  return request<Marking[]>(`/markings${qs}`);
}

// ── Marking management (MARKING_ADMIN, design §7.4) ──

/** Create a marking category (MARKING_ADMIN only). */
export async function createMarkingCategory(
  name: string,
  description: string = '',
): Promise<MarkingCategory> {
  return request<MarkingCategory>('/marking-categories', {
    method: 'POST',
    body: JSON.stringify({ name, description }),
  });
}

/** Create a marking value under a category (MARKING_ADMIN only). */
export async function createMarking(
  body: {
    category_id: string;
    name: string;
    display_name?: string;
    description?: string;
  },
): Promise<Marking> {
  return request<Marking>('/markings', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** Grant a marking to a group (MARKING_ADMIN only). */
export async function grantMarking(
  markingId: string,
  groupId: string,
  expiresAt?: string | null,
): Promise<{ status: string }> {
  return request<{ status: string }>(`/markings/${markingId}/grants`, {
    method: 'POST',
    body: JSON.stringify({ group_id: groupId, expires_at: expiresAt ?? null }),
  });
}

/** Apply a marking to a resource (PROJECT_OWNER/EDITOR). */
export async function assignMarking(
  resourceType: string,
  resourceId: string,
  markingId: string,
): Promise<{ id: string }> {
  return request<{ id: string }>(`/resources/${resourceType}/${resourceId}/markings`, {
    method: 'POST',
    body: JSON.stringify({ marking_id: markingId }),
  });
}

/** Revoke a marking from a resource (PROJECT_OWNER/EDITOR). */
export async function revokeMarking(
  resourceType: string,
  resourceId: string,
  markingId: string,
): Promise<void> {
  await request<void>(`/resources/${resourceType}/${resourceId}/markings/${markingId}`, {
    method: 'DELETE',
  });
}

// ── Ship-the-decision: batch allowedActions (design §8.2) ──

/** Per-resource permission decision shipped from the backend. */
export interface ResourcePermission {
  /** Actions the current principal is allowed to perform on this resource. */
  allowedActions: string[];
  /** Denied actions → human-readable reason (for disabled-control tooltips). */
  disabledReasons: Record<string, string>;
}

/** Batch response: resource_id → permission decision. */
export type AllowedActionsMap = Record<string, ResourcePermission>;
/** Resolve allowedActions for N resources in one call (no N+1).
 *
 * This is the ship-the-decision channel: the frontend renders permission
 * state from these decisions rather than re-deriving rules or calling
 * /authz/check per resource. Call once per page load. */
export async function getAllowedActions(
  resourceType: string,
  resourceIds: string[],
): Promise<AllowedActionsMap> {
  if (resourceIds.length === 0) return {};
  const resp = await request<{
    resource_type: string;
    decisions: Record<string, { allowedActions: string[]; disabledReasons: Record<string, string> }>;
  }>('/authz/allowed-actions', {
    method: 'POST',
    body: JSON.stringify({ resource_type: resourceType, resource_ids: resourceIds }),
  });
  return resp.decisions;
}

// ── Role Assignment (design §7.3 — grant roles to Groups) ──

export type RoleScopeType = 'GLOBAL' | 'SPACE' | 'PROJECT';

export interface RoleAssignment {
  id: string;
  group_id: string;
  role_name: string;
  scope_type: RoleScopeType;
  scope_id: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RoleAssignmentCreate {
  group_id: string;
  role_name: string;
  scope_type: RoleScopeType;
  scope_id?: string | null;
  expires_at?: string | null;
}

/** Grant a role to a group at a scope (组授权铁律). */
export async function createRoleAssignment(
  body: RoleAssignmentCreate,
): Promise<RoleAssignment> {
  return request<RoleAssignment>('/authz/role-assignments', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** List role assignments, optionally filtered by scope or group. */
export async function listRoleAssignments(params: {
  scope_id?: string;
  group_id?: string;
} = {}): Promise<RoleAssignment[]> {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const qs = sp.toString();
  return request<RoleAssignment[]>(`/authz/role-assignments${qs ? `?${qs}` : ''}`);
}

/** Revoke a role assignment by id. */
export async function deleteRoleAssignment(id: string): Promise<void> {
  await request<void>(`/authz/role-assignments/${id}`, { method: 'DELETE' });
}

// ── Deployment info (progressive disclosure, design §8.1) ──

export interface DeploymentInfo {
  /** True when more than one Organization exists → expose three-tier mgmt. */
  is_multi_tenant: boolean;
}

/** Fetch deployment metadata (multi-tenant signal for the Settings panel). */
export async function getDeploymentInfo(): Promise<DeploymentInfo> {
  return request<DeploymentInfo>('/auth/deployment-info');
}

export { ApiError };

// ── Identity management (design §7.2 — User/Group/GroupMembership) ──

export interface User {
  id: string;
  email: string;
  subject: string;
  attributes: Record<string, string>;
  home_organization: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserCreate {
  email: string;
  subject: string;
  attributes?: Record<string, string>;
  home_organization?: string | null;
}

export interface Group {
  id: string;
  name: string;
  description: string;
  organization_id: string;
  parent_group_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface GroupCreate {
  name: string;
  organization_id: string;
  description?: string;
  parent_group_id?: string | null;
}

export async function listUsers(): Promise<User[]> {
  return request<User[]>('/identity/users');
}

export async function createUser(body: UserCreate): Promise<User> {
  return request<User>('/identity/users', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function listGroups(organizationId?: string): Promise<Group[]> {
  const params = organizationId ? `?organization_id=${organizationId}` : '';
  return request<Group[]>(`/identity/groups${params}`);
}

export async function createGroup(body: GroupCreate): Promise<Group> {
  return request<Group>('/identity/groups', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function listGroupMembers(groupId: string): Promise<User[]> {
  return request<User[]>(`/identity/groups/${groupId}/members`);
}

export async function addGroupMember(groupId: string, userId: string): Promise<void> {
  await request<void>(`/identity/groups/${groupId}/members`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function removeGroupMember(groupId: string, userId: string): Promise<void> {
  await request<void>(`/identity/groups/${groupId}/members/${userId}`, { method: 'DELETE' });
}

export async function listUserGroups(userId: string): Promise<Group[]> {
  return request<Group[]>(`/identity/users/${userId}/groups`);
}

// ── Container management (design §7.2 — Org/Space/Project) ──

export interface Organization {
  id: string;
  api_name: string;
  display_name: string;
  description: string;
  org_type: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Space {
  id: string;
  api_name: string;
  display_name: string;
  description: string;
  ontology_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  organization_ids: string[];
}

export interface Project {
  id: string;
  api_name: string;
  display_name: string;
  description: string;
  space_id: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface RoleInfo {
  id: string;
  name: string;
  scope_type: 'GLOBAL' | 'SPACE' | 'PROJECT';
  permissions: string[];
  description: string;
  is_builtin: boolean;
}

export async function listOrganizations(): Promise<Organization[]> {
  return request<Organization[]>('/containers/organizations');
}

export async function listSpaces(): Promise<Space[]> {
  return request<Space[]>('/containers/spaces');
}

export async function listProjects(spaceId?: string): Promise<Project[]> {
  const params = spaceId ? `?space_id=${spaceId}` : '';
  return request<Project[]>(`/containers/projects${params}`);
}

export async function listRoles(): Promise<RoleInfo[]> {
  return request<RoleInfo[]>('/containers/roles');
}
