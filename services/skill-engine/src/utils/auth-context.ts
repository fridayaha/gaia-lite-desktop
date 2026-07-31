/**
 * Auth context extraction from X-* headers.
 *
 * The Manager proxy injects these headers after validating the admin JWT.
 * Skill-engine reads them for workspace ownership and authorization.
 */

export interface RequestContext {
  /** Actor ID (from X-Actor-Id, maps to workspace user_id) */
  actorId: string;
  /** Group ID (from X-Group-Id, maps to workspace group_id) */
  groupId: string;
  /** Roles (from X-Roles, comma-separated) */
  roles: string[];
  /** User name (from X-User-Name) */
  userName?: string;
  /** User email (from X-User-Email) */
  userEmail?: string;
}

/**
 * Extract auth context from Fastify request headers.
 * Returns default values for missing headers (for direct access without proxy).
 */
export function extractAuthContext(
  headers: Record<string, string | string[] | undefined>,
): RequestContext {
  const actorId = (headers["x-actor-id"] as string) ?? "anonymous";
  const groupId = (headers["x-group-id"] as string) ?? "default";
  const rolesRaw = (headers["x-roles"] as string) ?? "";
  const roles = rolesRaw
    .split(",")
    .map((r) => r.trim())
    .filter(Boolean);

  return {
    actorId,
    groupId,
    roles,
    userName: headers["x-user-name"] as string | undefined,
    userEmail: headers["x-user-email"] as string | undefined,
  };
}

/**
 * Check if the auth context has platform admin privileges.
 */
export function isPlatformAdmin(context: RequestContext): boolean {
  return context.roles.includes("platform_admin");
}
