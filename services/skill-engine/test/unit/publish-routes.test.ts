import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { buildApp } from "../../src/app.js";
import { FileStore } from "../../src/workspace/file-store.js";
import type { WorkspaceStore } from "../../src/workspace/workspace-store.js";
import type { HubClient } from "../../src/hub/hub-client.js";
import type { Workspace } from "../../src/types/workspace.js";

const VALID_MANIFEST = {
  name: "chart-skill",
  version: "0.1.0",
  author: "skilldev",
  description: "图表绘制技能",
  engine: "hermes",
  type: "skill",
};
const VALID_SKILL = [
  "---",
  "name: chart-skill",
  "version: 0.1.0",
  "description: 图表绘制技能",
  "author: skilldev",
  "---",
  "# 图表绘制技能",
].join("\n");

describe("publish routes", () => {
  let baseDir: string;
  let app: Awaited<ReturnType<typeof buildApp>>;
  let setHubItemId: ReturnType<typeof vi.fn>;
  let hubImport: ReturnType<typeof vi.fn>;
  let hubScan: ReturnType<typeof vi.fn>;
  let hubGetItem: ReturnType<typeof vi.fn>;
  const wid = "ws-pub";

  beforeEach(async () => {
    baseDir = mkdtempSync(join(tmpdir(), "pub-routes-"));
    mkdirSync(join(baseDir, wid), { recursive: true });
    writeFileSync(join(baseDir, wid, "SKILL.md"), VALID_SKILL);
    writeFileSync(join(baseDir, wid, "manifest.json"), JSON.stringify(VALID_MANIFEST));

    setHubItemId = vi.fn().mockResolvedValue(undefined);
    hubImport = vi.fn().mockResolvedValue({
      itemId: "item-1",
      versionId: "ver-1",
      warnings: [],
    });
    hubScan = vi.fn().mockResolvedValue({
      riskLevel: "low",
      findings: [],
      summary: { total_findings: 0 },
    });
    hubGetItem = vi.fn().mockResolvedValue({ currentVersionId: "ver-1" });

    const ws: Workspace = {
      id: wid,
      userId: "u",
      groupId: "g",
      name: "test",
      description: "",
      localPath: join(baseDir, wid),
      status: "active",
      hubItemId: null,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    const mockWorkspaceStore = {
      get: vi.fn().mockResolvedValue(ws),
      setHubItemId,
    };

    app = await buildApp(
      { workspaceBaseDir: baseDir, logLevel: "warn" },
      {
        db: {} as any,
        sql: {} as any,
        fileStore: new FileStore(baseDir),
        workspaceStore: mockWorkspaceStore as unknown as WorkspaceStore,
        messageCache: { readMessages: vi.fn().mockResolvedValue([]) } as any,
        instanceManager: {} as any,
        hubClient: {
          importPackage: hubImport,
          scanVersion: hubScan,
          getItem: hubGetItem,
        } as unknown as HubClient,
      },
    );
  });

  afterEach(async () => {
    await app.close();
    rmSync(baseDir, { recursive: true, force: true });
  });

  it("validate returns valid for a compliant workspace", async () => {
    const res = await app.inject({
      method: "POST",
      url: `/api/skill-engine/workspaces/${wid}/validate`,
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.valid).toBe(true);
    expect(body.errors).toEqual([]);
  });

  it("build-package returns a zip download", async () => {
    const res = await app.inject({
      method: "GET",
      url: `/api/skill-engine/workspaces/${wid}/build-package`,
    });
    expect(res.statusCode).toBe(200);
    expect(res.headers["content-type"]).toBe("application/zip");
    expect(res.headers["content-disposition"]).toContain("chart-skill-0.1.0.zip");
    expect(res.body.length).toBeGreaterThan(0);
  });

  it("build-package 400s when manifest invalid", async () => {
    writeFileSync(join(baseDir, wid, "manifest.json"), "{ bad }");
    const res = await app.inject({
      method: "GET",
      url: `/api/skill-engine/workspaces/${wid}/build-package`,
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().errors).toBeDefined();
  });

  it("publish imports to hub, scans, and writes back hubItemId", async () => {
    const res = await app.inject({
      method: "POST",
      url: `/api/skill-engine/workspaces/${wid}/publish`,
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.itemId).toBe("item-1");
    expect(body.versionId).toBe("ver-1");
    expect(body.scan.riskLevel).toBe("low");
    expect(body.scan.findingsCount).toBe(0);
    // import got a zip buffer
    expect(hubImport).toHaveBeenCalledTimes(1);
    expect(Buffer.isBuffer(hubImport.mock.calls[0][0])).toBe(true);
    // scan called with the version id
    expect(hubScan).toHaveBeenCalledWith("ver-1", "skill-engine");
    // hubItemId written back
    expect(setHubItemId).toHaveBeenCalledWith(wid, "item-1");
  });

  it("publish 400s when validate fails", async () => {
    writeFileSync(join(baseDir, wid, "manifest.json"), "{ bad }");
    const res = await app.inject({
      method: "POST",
      url: `/api/skill-engine/workspaces/${wid}/publish`,
    });
    expect(res.statusCode).toBe(400);
    expect(hubImport).not.toHaveBeenCalled();
  });

  it("scan 400s when workspace not published (no hubItemId)", async () => {
    const res = await app.inject({
      method: "POST",
      url: `/api/skill-engine/workspaces/${wid}/scan`,
    });
    expect(res.statusCode).toBe(400);
  });

  it("scan re-scans the current version after publish", async () => {
    // simulate already-published workspace
    (app.workspaceStore.get as any) = vi.fn().mockResolvedValue({
      id: wid,
      hubItemId: "item-1",
      status: "active",
      localPath: join(baseDir, wid),
    }) as any;
    const res = await app.inject({
      method: "POST",
      url: `/api/skill-engine/workspaces/${wid}/scan`,
    });
    expect(res.statusCode).toBe(200);
    expect(hubGetItem).toHaveBeenCalledWith("item-1");
    expect(hubScan).toHaveBeenCalledWith("ver-1", "skill-engine");
    expect(res.json().riskLevel).toBe("low");
  });
});
