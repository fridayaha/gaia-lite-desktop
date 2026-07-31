import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { buildApp } from "../../src/app.js";
import { FileStore } from "../../src/workspace/file-store.js";
import { encryptCredentialsDict } from "../../src/utils/crypto.js";
import type { WorkspaceStore } from "../../src/workspace/workspace-store.js";
import type { Workspace } from "../../src/types/workspace.js";

process.env.UA_CREDENTIAL_ENCRYPTION_KEY = "config-routes-test-key";

const MANIFEST = {
  name: "search-skill",
  version: "0.1.0",
  author: "skilldev",
  description: "搜索技能",
  engine: "hermes",
  type: "skill",
  config_params: [
    { name: "api_key", label: "API Key", type: "string", secret: true },
    { name: "mode", label: "Mode", type: "select", options: ["fast", "safe"] },
    { name: "timeout", label: "Timeout", type: "number" },
  ],
};

describe("config routes", () => {
  let baseDir: string;
  let app: Awaited<ReturnType<typeof buildApp>>;
  let getConfig: ReturnType<typeof vi.fn>;
  let saveConfig: ReturnType<typeof vi.fn>;
  let findBySkillName: ReturnType<typeof vi.fn>;
  let reload: ReturnType<typeof vi.fn>;
  const wid = "ws-cfg";

  beforeEach(async () => {
    baseDir = mkdtempSync(join(tmpdir(), "cfg-routes-"));
    mkdirSync(join(baseDir, wid), { recursive: true });
    writeFileSync(join(baseDir, wid, "manifest.json"), JSON.stringify(MANIFEST));

    getConfig = vi.fn().mockResolvedValue({ config: {}, credentialsEncrypted: null });
    saveConfig = vi.fn().mockResolvedValue(undefined);
    findBySkillName = vi.fn().mockResolvedValue(null);
    reload = vi.fn().mockResolvedValue(undefined);

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
      getConfig,
      saveConfig,
      findBySkillName,
    };

    app = await buildApp(
      { workspaceBaseDir: baseDir, logLevel: "warn" },
      {
        db: {} as any,
        sql: {} as any,
        fileStore: new FileStore(baseDir),
        workspaceStore: mockWorkspaceStore as unknown as WorkspaceStore,
        messageCache: { readMessages: vi.fn().mockResolvedValue([]) } as any,
        instanceManager: { reload } as any,
        hubClient: {} as any,
      },
    );
  });

  afterEach(async () => {
    await app.close();
    rmSync(baseDir, { recursive: true, force: true });
  });

  describe("GET /api/skill-engine/workspaces/:id/config", () => {
    it("returns empty config + empty configured when nothing stored", async () => {
      const res = await app.inject({ method: "GET", url: `/api/skill-engine/workspaces/${wid}/config` });
      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ configValues: {}, configured: [] });
    });

    it("returns configured key list (no plaintext) when secrets stored", async () => {
      getConfig.mockResolvedValue({
        config: { mode: "fast" },
        credentialsEncrypted: encryptCredentialsDict({ api_key: "sk-secret" }),
      });
      const res = await app.inject({ method: "GET", url: `/api/skill-engine/workspaces/${wid}/config` });
      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.configValues).toEqual({ mode: "fast" });
      expect(body.configured).toEqual(["api_key"]);
      // 不返密钥明文
      expect(JSON.stringify(body)).not.toContain("sk-secret");
    });
  });

  describe("PUT /api/skill-engine/workspaces/:id/config", () => {
    it("saves non-secret config (type-validated) + encrypted secret + caches skill_name", async () => {
      const res = await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { config: { mode: "fast", timeout: 30 }, credentials: { api_key: "sk-xxx" } },
      });
      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.ok).toBe(true);
      expect(body.configured).toEqual(["api_key"]);

      expect(saveConfig).toHaveBeenCalledTimes(1);
      const patch = saveConfig.mock.calls[0][1];
      expect(patch.config).toEqual({ mode: "fast", timeout: 30 });
      expect(patch.skillName).toBe("search-skill");
      expect(patch.credentialsEncrypted).toBeTruthy();
      // 非密钥变更触发 debug reload
      expect(reload).toHaveBeenCalledWith(`${wid}:debug`);
    });

    it("rejects secret value submitted via config (must go through credentials)", async () => {
      const res = await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { config: { api_key: "x" } },
      });
      expect(res.statusCode).toBe(400);
    });

    it("rejects credential key not declared secret:true", async () => {
      const res = await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { credentials: { mode: "x" } },
      });
      expect(res.statusCode).toBe(400);
    });

    it("rejects invalid select option", async () => {
      const res = await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { config: { mode: "turbo" } },
      });
      expect(res.statusCode).toBe(400);
    });

    it("merges with existing secrets (empty value = no change)", async () => {
      getConfig.mockResolvedValue({
        config: {},
        credentialsEncrypted: encryptCredentialsDict({ api_key: "existing", other: "keep" }),
      });
      await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { credentials: { api_key: "" } }, // 空 = 不改 api_key
      });
      const patch = saveConfig.mock.calls[0][1];
      // credentialsEncrypted 重加密了 existing+keep（api_key 空被跳过）
      expect(patch.credentialsEncrypted).toBeTruthy();
    });

    it("does not trigger reload when only secrets changed (no config)", async () => {
      await app.inject({
        method: "PUT",
        url: `/api/skill-engine/workspaces/${wid}/config`,
        payload: { credentials: { api_key: "sk-xxx" } },
      });
      expect(reload).not.toHaveBeenCalled();
    });
  });

  describe("GET /secret (runtime, in-Pod)", () => {
    it("returns plaintext value for configured secret", async () => {
      findBySkillName.mockResolvedValue({
        id: wid,
        credentialsEncrypted: encryptCredentialsDict({ api_key: "sk-runtime" }),
      });
      const res = await app.inject({
        method: "GET",
        url: "/secret?skill=search-skill&key=api_key",
      });
      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ value: "sk-runtime" });
    });

    it("404 when no secrets for skill", async () => {
      findBySkillName.mockResolvedValue(null);
      const res = await app.inject({ method: "GET", url: "/secret?skill=none&key=api_key" });
      expect(res.statusCode).toBe(404);
      expect(res.json().error).toContain("no secrets");
    });

    it("404 when key not configured", async () => {
      findBySkillName.mockResolvedValue({
        id: wid,
        credentialsEncrypted: encryptCredentialsDict({ other: "x" }),
      });
      const res = await app.inject({ method: "GET", url: "/secret?skill=search-skill&key=api_key" });
      expect(res.statusCode).toBe(404);
      expect(res.json().error).toContain("not configured");
    });

    it("400 when skill/key missing", async () => {
      const res = await app.inject({ method: "GET", url: "/secret?skill=" });
      expect(res.statusCode).toBe(400);
    });

    it("500 when decryption fails (key mismatch)", async () => {
      findBySkillName.mockResolvedValue({ id: wid, credentialsEncrypted: "not-valid-base64-token!!" });
      const res = await app.inject({ method: "GET", url: "/secret?skill=search-skill&key=api_key" });
      expect(res.statusCode).toBe(500);
      expect(res.json().error).toContain("decrypt failed");
    });
  });
});
