import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";

import { buildApp } from "../../src/app.js";
import type { WorkspaceStore } from "../../src/workspace/workspace-store.js";
import type { FileStore } from "../../src/workspace/file-store.js";
import type { EngineInstanceManager } from "../../src/engine/instance-manager.js";
import type { MessageCache } from "../../src/redis/message-cache.js";
import type { CachedMessage } from "../../src/redis/message-cache.js";
import type { Workspace } from "../../src/types/workspace.js";
import { WorkspaceNotFoundError } from "../../src/utils/errors.js";

// ── In-memory mock WorkspaceStore ──────────────────────────────

class MockWorkspaceStore {
  private workspaces = new Map<string, Workspace>();
  private baseDir: string;

  constructor(baseDir: string) {
    this.baseDir = baseDir;
  }

  async create(req: {
    name: string;
    description?: string;
    userId: string;
    groupId: string;
  }): Promise<Workspace> {
    const id = randomUUID();
    const ws: Workspace = {
      id,
      userId: req.userId,
      groupId: req.groupId,
      name: req.name,
      description: req.description ?? "",
      localPath: join(this.baseDir, id),
      status: "active",
      hubItemId: null,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    this.workspaces.set(id, ws);
    return ws;
  }

  async list(userId?: string): Promise<Workspace[]> {
    let result = [...this.workspaces.values()].filter(
      (w) => w.status === "active",
    );
    if (userId) {
      result = result.filter((w) => w.userId === userId);
    }
    return result;
  }

  async get(id: string): Promise<Workspace> {
    const ws = this.workspaces.get(id);
    if (!ws || ws.status === "deleted") {
      throw new WorkspaceNotFoundError(id);
    }
    return ws;
  }

  async delete(id: string): Promise<void> {
    const ws = this.workspaces.get(id);
    if (!ws) throw new WorkspaceNotFoundError(id);
    ws.status = "deleted";
  }

  async exists(id: string): Promise<boolean> {
    const ws = this.workspaces.get(id);
    return !!ws && ws.status === "active";
  }

  getDir(id: string): string {
    return join(this.baseDir, id);
  }
}

// ── In-memory mock MessageCache ────────────────────────────────
// Stores messages newest-first (mirroring real getRecentMessages) so the
// readMessages wrapper's reversal to oldest-first is exercised.

class MockMessageCache {
  private store = new Map<string, CachedMessage[]>();

  private key(workspaceId: string, role: string): string {
    return `${workspaceId}:${role}`;
  }

  /** Seed newest-first history for a workspace:role. */
  seed(workspaceId: string, role: string, msgs: CachedMessage[]): void {
    this.store.set(this.key(workspaceId, role), [...msgs].reverse());
  }

  async getRecentMessages(
    workspaceId: string,
    role: string,
    _limit = 50,
  ): Promise<CachedMessage[]> {
    return this.store.get(this.key(workspaceId, role)) ?? [];
  }

  async readMessages(
    workspaceId: string,
    role: string,
    limit = 100,
  ): Promise<CachedMessage[]> {
    const recent = await this.getRecentMessages(workspaceId, role, limit);
    return recent.slice().reverse();
  }
}

// ── Tests ──────────────────────────────────────────────────────

describe("Routes", () => {
  let baseDir: string;
  let app: Awaited<ReturnType<typeof buildApp>>;
  let mockWorkspaceStore: MockWorkspaceStore;
  let mockMessageCache: MockMessageCache;

  beforeEach(async () => {
    baseDir = mkdtempSync(join(tmpdir(), "skill-engine-route-test-"));
    mockWorkspaceStore = new MockWorkspaceStore(baseDir);
    mockMessageCache = new MockMessageCache();

    // Build app with mock decorators (no real DB needed)
    app = await buildApp(
      { workspaceBaseDir: baseDir, logLevel: "warn" },
      {
        db: {} as any,
        sql: {} as any,
        workspaceStore:
          mockWorkspaceStore as unknown as WorkspaceStore,
        messageCache:
          mockMessageCache as unknown as MessageCache,
      },
    );
  });

  afterEach(async () => {
    await app.instanceManager.stopAll();
    await app.close();
    rmSync(baseDir, { recursive: true, force: true });
  });

  // ── Workspace routes ──────────────────────────────────────────

  describe("POST /api/skill-engine/workspaces", () => {
    it("creates a workspace with X-* headers", async () => {
      const response = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "my-skill", description: "A test skill" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-456",
        },
      });
      expect(response.statusCode).toBe(201);
      const body = response.json();
      expect(body.name).toBe("my-skill");
      expect(body.description).toBe("A test skill");
      expect(body.status).toBe("active");
      expect(body.id).toBeTruthy();
    });
  });

  describe("GET /api/skill-engine/workspaces", () => {
    it("returns empty list initially", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces",
        headers: { "x-actor-id": "user-123" },
      });
      expect(response.statusCode).toBe(200);
      expect(response.json().workspaces).toEqual([]);
    });

    it("returns workspaces filtered by user", async () => {
      // Create workspace for user-123
      await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "skill-a" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-1",
        },
      });

      // user-456 should see empty list
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces",
        headers: { "x-actor-id": "user-456" },
      });
      expect(response.json().workspaces).toHaveLength(0);

      // user-123 should see their workspace
      const response2 = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces",
        headers: { "x-actor-id": "user-123" },
      });
      expect(response2.json().workspaces).toHaveLength(1);
    });

    it("platform_admin sees all workspaces", async () => {
      await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "skill-a" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-1",
        },
      });

      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces",
        headers: {
          "x-actor-id": "admin-1",
          "x-roles": "platform_admin",
        },
      });
      expect(response.json().workspaces).toHaveLength(1);
    });
  });

  describe("GET /api/skill-engine/workspaces/:id", () => {
    it("returns workspace detail", async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "test" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-1",
        },
      });
      const { id } = createRes.json();

      const response = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${id}`,
      });
      expect(response.statusCode).toBe(200);
      expect(response.json().name).toBe("test");
    });

    it("returns 404 for missing workspace", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces/nonexistent",
      });
      expect(response.statusCode).toBe(404);
    });
  });

  describe("DELETE /api/skill-engine/workspaces/:id", () => {
    it("deletes a workspace", async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "to-delete" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-1",
        },
      });
      const { id } = createRes.json();

      const response = await app.inject({
        method: "DELETE",
        url: `/api/skill-engine/workspaces/${id}`,
      });
      expect(response.statusCode).toBe(200);
      expect(response.json().ok).toBe(true);
    });
  });

  // ── File routes ────────────────────────────────────────────────

  describe("File read/write", () => {
    let workspaceId: string;

    beforeEach(async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "file-test" },
        headers: {
          "x-actor-id": "user-123",
          "x-group-id": "group-1",
        },
      });
      workspaceId = createRes.json().id;
    });

    it("writes and reads a file", async () => {
      const writeRes = await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${workspaceId}/files/test.txt`,
        payload: { content: "Hello" },
      });
      expect(writeRes.statusCode).toBe(200);
      expect(writeRes.json().ok).toBe(true);

      const readRes = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/files/test.txt`,
      });
      expect(readRes.statusCode).toBe(200);
      expect(readRes.json().content).toBe("Hello");
    });

    it("returns 404 for missing file", async () => {
      const readRes = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/files/missing.txt`,
      });
      expect(readRes.statusCode).toBe(404);
    });
  });

  describe("GET /api/skill-engine/workspaces/:id/files (file tree)", () => {
    let workspaceId: string;
    let wsDir: string;

    beforeEach(async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "tree-test" },
        headers: { "x-actor-id": "user-123", "x-group-id": "group-1" },
      });
      workspaceId = createRes.json().id;
      wsDir = join(baseDir, workspaceId);
      mkdirSync(wsDir, { recursive: true });

      // User-visible files (write directly to the real FS path the FileStore serves)
      writeFileSync(join(wsDir, "SKILL.md"), "# skill", "utf-8");
      mkdirSync(join(wsDir, "scripts"), { recursive: true });
      writeFileSync(join(wsDir, "scripts", "a.py"), "print('hi')", "utf-8");
      // Engine internals — must be excluded
      mkdirSync(join(wsDir, ".pi"), { recursive: true });
      writeFileSync(join(wsDir, ".pi", "internal.txt"), "x", "utf-8");
      writeFileSync(join(wsDir, ".DS_Store"), "x", "utf-8");
    });

    it("returns the file tree excluding internals", async () => {
      const res = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/files`,
      });
      expect(res.statusCode).toBe(200);
      const paths = res.json().files.map((f: { path: string }) => f.path).sort();
      expect(paths).toContain("SKILL.md");
      expect(paths).toContain("scripts");
      expect(paths).toContain("scripts/a.py");
      expect(paths).not.toContain(".pi");
      expect(paths).not.toContain(".pi/internal.txt");
      expect(paths).not.toContain(".DS_Store");
    });

    it("returns entry field values (isDir/size/modifiedAt)", async () => {
      const res = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/files`,
      });
      const files = res.json().files as Array<{
        path: string;
        size: number;
        isDir: boolean;
        modifiedAt: string;
      }>;
      const dir = files.find((f) => f.path === "scripts");
      expect(dir).toBeDefined();
      expect(dir!.isDir).toBe(true);
      expect(dir!.size).toBe(0);
      expect(typeof dir!.modifiedAt).toBe("string");
      const file = files.find((f) => f.path === "SKILL.md");
      expect(file!.isDir).toBe(false);
      expect(file!.size).toBe(7);
    });

    it("returns 404 for missing workspace", async () => {
      const res = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces/nonexistent/files",
      });
      expect(res.statusCode).toBe(404);
    });
  });

  describe("GET .../sessions/:sid/messages (history)", () => {
    let workspaceId: string;

    beforeEach(async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "msg-test" },
        headers: { "x-actor-id": "user-123", "x-group-id": "group-1" },
      });
      workspaceId = createRes.json().id;
    });

    it("returns conversation history in oldest-first order with field values", async () => {
      // Seed newest-first (mirrors real getRecentMessages storage order).
      const now = Date.now();
      const oldest: CachedMessage = {
        id: "m1",
        workspaceId,
        role: "dev",
        seq: 1,
        sender: "assistant",
        content: "first chunk",
        toolCalls: null,
        createdAt: new Date(now).toISOString(),
      };
      const newer: CachedMessage = {
        id: "m2",
        workspaceId,
        role: "dev",
        seq: 2,
        sender: "assistant",
        content: "second chunk",
        toolCalls: null,
        createdAt: new Date(now + 1000).toISOString(),
      };
      mockMessageCache.seed(workspaceId, "dev", [oldest, newer]);

      const res = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/sessions/dev/messages`,
      });
      expect(res.statusCode).toBe(200);
      const msgs = res.json().messages as CachedMessage[];
      // Oldest-first: m1 then m2
      expect(msgs.map((m) => m.id)).toEqual(["m1", "m2"]);
      // Assert actual field values (not just call invocation)
      expect(msgs[0].content).toBe("first chunk");
      expect(msgs[0].seq).toBe(1);
      expect(msgs[1].content).toBe("second chunk");
      expect(msgs[1].seq).toBe(2);
    });

    it("returns empty list when no history", async () => {
      const res = await app.inject({
        method: "GET",
        url: `/api/skill-engine/workspaces/${workspaceId}/sessions/dev/messages`,
      });
      expect(res.statusCode).toBe(200);
      expect(res.json().messages).toEqual([]);
    });

    it("returns 404 for missing workspace", async () => {
      const res = await app.inject({
        method: "GET",
        url: "/api/skill-engine/workspaces/nonexistent/sessions/dev/messages",
      });
      expect(res.statusCode).toBe(404);
    });
  });

  // ── Clarify response route ─────────────────────────────────────

  describe("POST .../sessions/:sid/tools/:toolCallId/response", () => {
    let workspaceId: string;

    beforeEach(async () => {
      const createRes = await app.inject({
        method: "POST",
        url: "/api/skill-engine/workspaces",
        payload: { name: "clarify-test" },
        headers: { "x-actor-id": "user-123", "x-group-id": "group-1" },
      });
      workspaceId = createRes.json().id;
    });

    it("forwards answers to instanceManager.submitToolResponse and returns ok", async () => {
      const spy = vi
        .spyOn(app.instanceManager, "submitToolResponse")
        .mockResolvedValue({
          type: "response",
          id: "fake",
          command: "tool_response",
          success: true,
        });

      const res = await app.inject({
        method: "POST",
        url: `/api/skill-engine/workspaces/${workspaceId}/sessions/dev/tools/tc-1/response`,
        payload: { answers: { scene: "查新闻", engine: "DuckDuckGo" } },
      });
      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ ok: true, error: undefined });
      // Composite key built from the real workspaceId + role, toolCallId from
      // the path, answers from the body.
      const [key, toolCallId, answers] = spy.mock.calls[0];
      expect(key).toBe(`${workspaceId}:dev`);
      expect(toolCallId).toBe("tc-1");
      expect(answers).toEqual({ scene: "查新闻", engine: "DuckDuckGo" });
      spy.mockRestore();
    });

    it("surfaces a failed resolve (no pending clarify) as ok:false", async () => {
      vi.spyOn(app.instanceManager, "submitToolResponse").mockResolvedValue({
        type: "response",
        id: "fake",
        command: "tool_response",
        success: false,
        error: "no pending clarify for this toolCallId",
      });

      const res = await app.inject({
        method: "POST",
        url: `/api/skill-engine/workspaces/${workspaceId}/sessions/dev/tools/tc-stale/response`,
        payload: { answers: {} },
      });
      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.ok).toBe(false);
      expect(body.error).toContain("no pending");
    });

    it("accepts an empty body (answers defaults to {})", async () => {
      const spy = vi
        .spyOn(app.instanceManager, "submitToolResponse")
        .mockResolvedValue({
          type: "response",
          id: "fake",
          command: "tool_response",
          success: true,
        });
      const res = await app.inject({
        method: "POST",
        url: `/api/skill-engine/workspaces/${workspaceId}/sessions/dev/tools/tc-2/response`,
      });
      expect(res.statusCode).toBe(200);
      expect(spy.mock.calls[0][2]).toEqual({});
      spy.mockRestore();
    });
  });

  // ── Admin routes ───────────────────────────────────────────────

  describe("GET /api/skill-engine/admin/stats", () => {
    it("returns stats for platform_admin", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/admin/stats",
        headers: { "x-roles": "platform_admin" },
      });
      expect(response.statusCode).toBe(200);
    });

    it("returns 403 for non-admin", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/admin/stats",
        headers: { "x-roles": "contributor" },
      });
      expect(response.statusCode).toBe(403);
    });
  });

  describe("GET /api/skill-engine/admin/resources", () => {
    it("returns resource info for platform_admin", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/admin/resources",
        headers: { "x-roles": "platform_admin" },
      });
      expect(response.statusCode).toBe(200);
      const body = response.json();
      expect(body.totalMemoryMB).toBeGreaterThan(0);
      expect(body.activeInstances).toBe(0);
    });

    it("returns 403 for non-admin", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/api/skill-engine/admin/resources",
        headers: { "x-roles": "contributor" },
      });
      expect(response.statusCode).toBe(403);
    });
  });
});
