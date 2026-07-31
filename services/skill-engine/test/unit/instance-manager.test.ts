import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ConflictError } from "../../src/utils/errors.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { EngineInstanceManager } from "../../src/engine/instance-manager.js";
import { readConfig } from "../../src/config.js";
import type { EngineSpawnOptions } from "../../src/types/engine.js";
import type { MessageCache } from "../../src/redis/message-cache.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MOCK_WORKER_PATH = resolve(__dirname, "../fixtures/mock-worker.ts");

/**
 * Testable subclass that runs the TypeScript mock worker via tsx.
 */
class TestableInstanceManager extends EngineInstanceManager {
  protected _buildSpawnArgs(
    _workerPath: string,
    role: string,
    options: EngineSpawnOptions,
  ): { command: string; args: string[] } {
    const args = [MOCK_WORKER_PATH, "--cwd", options.cwd, "--role", role];
    if (options.tools) args.push("--tools", options.tools.join(","));
    if (options.excludeTools)
      args.push("--exclude", options.excludeTools.join(","));
    return { command: "npx", args: ["tsx", ...args] };
  }
}

describe("EngineInstanceManager", () => {
  let baseDir: string;
  let manager: TestableInstanceManager;

  beforeEach(() => {
    baseDir = mkdtempSync(join(tmpdir(), "skill-engine-im-test-"));
    const config = readConfig({
      workspaceBaseDir: baseDir,
      workerStartupTimeoutMs: 10000,
      commandTimeoutMs: 15000,
      maxConcurrentLlm: 3,
    });
    manager = new TestableInstanceManager(config);
  });

  afterEach(async () => {
    await manager.stopAll();
    manager.stopGc();
    rmSync(baseDir, { recursive: true, force: true });
  });

  describe("spawn", () => {
    it("spawns a worker and detects ready", async () => {
      const instance = await manager.spawn("ws1", "dev", {
        cwd: baseDir,
      });
      expect(instance).toBeTruthy();
      expect(instance.ready).toBe(true);
      expect(instance.workspaceId).toBe("ws1");
      expect(instance.role).toBe("dev");
    });

    it("returns existing instance if already running", async () => {
      const first = await manager.spawn("ws1", "dev", { cwd: baseDir });
      const second = await manager.spawn("ws1", "dev", { cwd: baseDir });
      expect(first).toBe(second);
    });

    it("supports dev and debug roles simultaneously", async () => {
      const dev = await manager.spawn("ws1", "dev", { cwd: baseDir });
      const debug = await manager.spawn("ws1", "debug", { cwd: baseDir });
      expect(dev).not.toBe(debug);
      expect(dev.role).toBe("dev");
      expect(debug.role).toBe("debug");
    });
  });

  describe("prompt", () => {
    it("sends prompt and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.prompt("ws1:dev", "hello");
      expect(response.success).toBe(true);
      expect(response.command).toBe("prompt");
    });
  });

  describe("followUp", () => {
    it("sends follow-up and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.followUp("ws1:dev", "next message");
      expect(response.success).toBe(true);
      expect(response.command).toBe("follow_up");
    });
  });

  describe("steer", () => {
    it("sends steer and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.steer("ws1:dev", "change direction");
      expect(response.success).toBe(true);
      expect(response.command).toBe("steer");
    });
  });

  describe("abort", () => {
    it("sends abort and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.abort("ws1:dev");
      expect(response.success).toBe(true);
    });
  });

  describe("reload", () => {
    it("sends reload and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.reload("ws1:dev");
      expect(response.success).toBe(true);
      expect(response.command).toBe("reload");
    });
  });

  describe("submitToolResponse", () => {
    it("sends tool_response with toolCallId + answers and receives response", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const answers = { scene: "查新闻", engine: "DuckDuckGo" };
      const response = await manager.submitToolResponse(
        "ws1:dev",
        "tc-clarify-1",
        answers,
      );
      expect(response.success).toBe(true);
      expect(response.command).toBe("tool_response");
    });
  });

  describe("getState", () => {
    it("returns instance state", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const response = await manager.getState("ws1:dev");
      expect(response.success).toBe(true);
      expect(response.data).toBeDefined();
      expect(response.data!.isStreaming).toBe(false);
      expect(response.data!.model).toEqual({
        provider: "mock",
        modelId: "mock-v1",
      });
    });
  });

  describe("stop", () => {
    it("stops a running instance", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      await manager.stop("ws1:dev");
      expect(manager.hasInstance("ws1:dev")).toBe(false);
    });

    it("is no-op for non-existent instance", async () => {
      await expect(manager.stop("missing:dev")).resolves.toBeUndefined();
    });
  });

  describe("getInstance / hasInstance", () => {
    it("returns undefined for non-existent instance", () => {
      expect(manager.getInstance("missing:dev")).toBeUndefined();
      expect(manager.hasInstance("missing:dev")).toBe(false);
    });
  });

  describe("getStats", () => {
    it("returns stats for running instances", async () => {
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      const stats = manager.getStats();
      expect(stats["ws1:dev"]).toBeDefined();
      expect(stats["ws1:dev"].ready).toBe(true);
    });
  });

  describe("persistence", () => {    // Builds a fake MessageCache that records every appendMessage input so the
    // test can assert exactly what gets persisted (field values, not just that
    // commit was called).
    function makeMockCache() {
      const calls: Array<Record<string, unknown>> = [];
      const cache = {
        appendMessage: vi.fn(async (input: Record<string, unknown>) => {
          calls.push(input);
        }),
        flushToPostgres: vi.fn(async () => 0),
        readMessages: vi.fn(async () => []),
        getRecentMessages: vi.fn(async () => []),
        _calls: calls,
      };
      return cache as unknown as MessageCache & { _calls: typeof calls };
    }

    const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms));

    it("persists one user + one assistant turn row (not per-fragment)", async () => {
      const cache = makeMockCache();
      const mgr = new TestableInstanceManager(
        readConfig({
          workspaceBaseDir: baseDir,
          workerStartupTimeoutMs: 10000,
          commandTimeoutMs: 15000,
          maxConcurrentLlm: 3,
        }),
        cache,
      );
      await mgr.spawn("ws1", "dev", { cwd: baseDir });
      await mgr.prompt("ws1:dev", "hello");
      await tick();

      // Exactly two rows: the user prompt + the flushed assistant turn.
      // (Old per-fragment code would persist message_update + turn_end and no
      // user row — this count + the user-row assertion catches a regression.)
      expect(cache.appendMessage).toHaveBeenCalledTimes(2);

      const userRow = cache._calls[0];
      expect(userRow.sender).toBe("user");
      expect(userRow.content).toBe("hello");
      expect(userRow.toolCalls).toBeNull();

      const asstRow = cache._calls[1];
      expect(asstRow.sender).toBe("assistant");
      // Two message_updates carried accumulated text "Echo: hello" then
      // "Echo: hello done" — the text part is replaced, not duplicated.
      expect(asstRow.content).toBe("Echo: hello done");

      // tool_calls column holds the ordered PersistedPart[]: [text, tool].
      const parts = asstRow.toolCalls as Array<Record<string, unknown>>;
      expect(Array.isArray(parts)).toBe(true);
      expect(parts).toHaveLength(2);
      expect(parts[0]).toEqual({ kind: "text", text: "Echo: hello done" });
      expect(parts[1].kind).toBe("tool");
      const tool = parts[1].tool as Record<string, unknown>;
      expect(tool.toolName).toBe("ls");
      expect(tool.status).toBe("done");
      expect(tool.args).toEqual({ path: "." });
      expect(tool.result).toEqual({
        content: [{ type: "text", text: "file.txt" }],
      });

      await mgr.stopAll();
    });

    it("does not persist when no messageCache is configured", async () => {
      // manager (no cache) constructed in beforeEach.
      await manager.spawn("ws1", "dev", { cwd: baseDir });
      await manager.prompt("ws1:dev", "hello");
      await tick();
      // No throw, no crash — persistence is best-effort.
      expect(manager.hasInstance("ws1:dev")).toBe(true);
    });
  });

  describe("reliability", () => {
    const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms));

    it("respawns a crashed worker and gives up after maxRestarts", async () => {
      const mgr = new TestableInstanceManager(
        readConfig({
          workspaceBaseDir: baseDir,
          workerStartupTimeoutMs: 10000,
          commandTimeoutMs: 15000,
          maxConcurrentLlm: 3,
          maxRestarts: 2,
          restartWindowMs: 60000,
          restartDelayMs: 30,
        }),
      );
      const inst = await mgr.spawn("ws1", "dev", { cwd: baseDir });
      const origPid = inst.process.pid;
      expect(origPid).toBeTruthy();

      // 2 kills → 2 respawns (within budget maxRestarts=2). After each kill,
      // wait for the respawn delay + tsx startup, then re-fetch the new instance.
      let current = mgr.getInstance("ws1:dev");
      for (let i = 0; i < 2; i++) {
        process.kill(current!.process.pid!, "SIGKILL");
        await tick(600);
        current = mgr.getInstance("ws1:dev");
        expect(current).toBeTruthy(); // respawned
      }
      expect(current!.process.pid).not.toBe(origPid);

      // 3rd kill → exceeds budget → instance stays dead.
      process.kill(current!.process.pid!, "SIGKILL");
      await tick(600);
      expect(mgr.hasInstance("ws1:dev")).toBe(false);

      await mgr.stopAll();
    });

    it("enforces a global maxInstances cap", async () => {
      const mgr = new TestableInstanceManager(
        readConfig({
          workspaceBaseDir: baseDir,
          workerStartupTimeoutMs: 10000,
          commandTimeoutMs: 15000,
          maxConcurrentLlm: 3,
          maxInstances: 1,
        }),
      );
      await mgr.spawn("ws1", "dev", { cwd: baseDir });
      await expect(mgr.spawn("ws2", "dev", { cwd: baseDir })).rejects.toThrow(
        ConflictError,
      );
      await mgr.stopAll();
    });

    it("idle GC stops instances inactive beyond the timeout", async () => {
      const mgr = new TestableInstanceManager(
        readConfig({
          workspaceBaseDir: baseDir,
          workerStartupTimeoutMs: 10000,
          commandTimeoutMs: 15000,
          maxConcurrentLlm: 3,
          idleTimeoutMs: 1800000,
        }),
      );
      await mgr.spawn("ws1", "dev", { cwd: baseDir });
      const inst = mgr.getInstance("ws1:dev")!;
      // Simulate long inactivity.
      inst.lastActivity = new Date(Date.now() - 2000000);
      await mgr.idleGc();
      expect(mgr.hasInstance("ws1:dev")).toBe(false);
      await mgr.stopAll();
    });

    it("fetches LLM credentials once (cached) and injects into spawns", async () => {
      const fetchCreds = vi.fn().mockResolvedValue({
        apiKey: "sk-from-manager",
        baseUrl: "http://litellm:4000/v1",
      });
      const mgr = new TestableInstanceManager(
        readConfig({
          workspaceBaseDir: baseDir,
          workerStartupTimeoutMs: 10000,
          commandTimeoutMs: 15000,
          maxConcurrentLlm: 3,
        }),
        undefined,
        { fetchCredentials: fetchCreds } as any,
      );
      await mgr.spawn("ws1", "dev", { cwd: baseDir });
      await mgr.spawn("ws2", "dev", { cwd: baseDir }); // different key, second spawn
      // Credential fetch is lazy + cached → called exactly once across two spawns.
      expect(fetchCreds).toHaveBeenCalledTimes(1);
      await mgr.stopAll();
    });
  });
});
