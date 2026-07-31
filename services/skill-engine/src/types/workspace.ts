/**
 * Workspace type definitions.
 *
 * Phase 1B: metadata stored in PostgreSQL, files on PVC filesystem.
 */

/** Persistent workspace record (from DB + filesystem). */
export interface Workspace {
  id: string; // UUID v4
  userId: string;
  groupId: string;
  name: string;
  description: string;
  localPath: string; // Absolute path on PVC filesystem
  status: "active" | "deleted";
  hubItemId: string | null;
  createdAt: string; // ISO 8601
  updatedAt: string; // ISO 8601
}

/** POST /workspaces request body. */
export interface WorkspaceCreateRequest {
  name: string;
  description?: string;
  userId: string; // From X-Actor-Id header
  groupId: string; // From X-Group-Id header
}

/** POST /workspaces response. */
export interface WorkspaceCreateResponse {
  id: string;
  name: string;
  description: string;
  localPath: string;
  status: "active";
}

/** GET /workspaces response. */
export interface WorkspaceListResponse {
  workspaces: Workspace[];
}

/** GET /workspaces/:id response. */
export interface WorkspaceDetailResponse extends WorkspaceCreateResponse {
  userId: string;
  groupId: string;
  hubItemId: string | null;
  createdAt: string;
  updatedAt: string;
}
