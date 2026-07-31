import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";

import { WorkspaceStore } from "../../src/workspace/workspace-store.js";
import { WorkspaceNotFoundError } from "../../src/utils/errors.js";

// 预制技能目录（源码树），工作区创建时 seed 到 .pi/skills/
const PREINSTALLED = resolve(__dirname, "../../preinstalled-skills");

// ── Mock Drizzle DB ───────────────────────────────────────────

/**
 * A minimal in-memory mock that implements the Drizzle query builder
 * interface used by WorkspaceStore. Only covers insert/select/update.
 */
function createMockDb() {
  const rows = new Map<string, any>();

  return {
    insert: (table: any) => ({
      values: (vals: any) => {
        rows.set(vals.id, { ...vals, createdAt: new Date(), updatedAt: new Date() });
        return { onConflictDoNothing: () => {}, returning: () => {} };
      },
    }),
    select: () => ({
      from: (table: any) => ({
        where: (condition: any) => ({
          limit: (n: number) => {
            // Parse condition to extract field/value
            // Simple implementation: filter rows by status + optional userId
            const results = [...rows.values()].filter(
              (r: any) => r.status === "active",
            );
            return results.slice(0, n);
          },
        }),
      }),
    }),
    update: (table: any) => ({
      set: (vals: any) => ({
        where: (condition: any) => {
          // Find and update the row
          for (const [id, row] of rows) {
            if (row.status !== "deleted") {
              Object.assign(row, vals);
            }
          }
          return { returning: () => {} };
        },
      }),
    }),
    _rows: rows,
  };
}

describe("WorkspaceStore", () => {
  let baseDir: string;
  let store: WorkspaceStore;
  let mockDb: ReturnType<typeof createMockDb>;

  beforeEach(() => {
    baseDir = mkdtempSync(join(tmpdir(), "skill-engine-test-"));
    mockDb = createMockDb();
    store = new WorkspaceStore(baseDir, mockDb as any, PREINSTALLED);
  });

  afterEach(() => {
    rmSync(baseDir, { recursive: true, force: true });
  });

  describe("create", () => {
    it("creates a workspace with default skill skeleton", async () => {
      const ws = await store.create({
        name: "my-skill",
        userId: "user-1",
        groupId: "group-1",
      });

      expect(ws.id).toBeTruthy();
      expect(ws.name).toBe("my-skill");
      expect(ws.description).toBe("");
      expect(ws.status).toBe("active");
      expect(ws.userId).toBe("user-1");
      expect(ws.groupId).toBe("group-1");
    });

    it("creates a workspace with description", async () => {
      const ws = await store.create({
        name: "test",
        description: "A test skill",
        userId: "user-1",
        groupId: "group-1",
      });
      expect(ws.description).toBe("A test skill");
    });

    it("seeds the preinstalled skill-creator into .pi/skills/", async () => {
      const ws = await store.create({
        name: "test",
        userId: "user-1",
        groupId: "group-1",
      });
      const skillPath = join(
        ws.localPath,
        ".pi",
        "skills",
        "skill-creator",
        "SKILL.md",
      );
      expect(existsSync(skillPath)).toBe(true);
      const content = readFileSync(skillPath, "utf-8");
      expect(content).toContain("skill-creator");
      // multi-file skill: references + scripts also seeded
      expect(existsSync(join(ws.localPath, ".pi", "skills", "skill-creator", "references", "schemas.md"))).toBe(true);
      expect(existsSync(join(ws.localPath, ".pi", "skills", "skill-creator", "scripts", "quick_validate.py"))).toBe(true);
    });

    it("inserts row into DB", async () => {
      await store.create({
        name: "test",
        userId: "user-1",
        groupId: "group-1",
      });
      expect(mockDb._rows.size).toBe(1);
    });
  });

  describe("get", () => {
    it("returns workspace by id", async () => {
      const created = await store.create({
        name: "test",
        userId: "user-1",
        groupId: "group-1",
      });
      const got = await store.get(created.id);
      expect(got.name).toBe("test");
    });

    it("throws NotFoundError for missing workspace", async () => {
      await expect(store.get("nonexistent")).rejects.toThrow(
        WorkspaceNotFoundError,
      );
    });
  });

  describe("delete", () => {
    it("soft-deletes a workspace", async () => {
      const ws = await store.create({
        name: "to-delete",
        userId: "user-1",
        groupId: "group-1",
      });
      await store.delete(ws.id);
      // The row should still exist but be soft-deleted
      const row = mockDb._rows.get(ws.id);
      expect(row?.status).toBe("deleted");
    });
  });

  describe("exists", () => {
    it("returns true for existing workspace", async () => {
      const ws = await store.create({
        name: "test",
        userId: "user-1",
        groupId: "group-1",
      });
      expect(await store.exists(ws.id)).toBe(true);
    });

    it("returns false for missing workspace", async () => {
      expect(await store.exists("nonexistent")).toBe(false);
    });
  });

  describe("getDir", () => {
    it("returns the absolute path for a workspace id", () => {
      const dir = store.getDir("abc-123");
      expect(dir).toBe(join(baseDir, "abc-123"));
    });
  });
});
