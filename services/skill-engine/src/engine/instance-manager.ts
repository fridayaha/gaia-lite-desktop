/**
 * EngineInstanceManager — manages engine worker subprocesses.
 *
 * Each active workspace:role pair gets a dedicated child process
 * running `engine-worker.ts`. Communication via stdin/stdout JSONL.
 *
 * Key responsibilities:
 * - Spawn / stop engine worker processes
 * - Bridge HTTP requests → JSONL commands → worker → responses
 * - Forward SDK events from worker to SSE subscribers
 * - Idle GC: stop workers after configurable inactivity timeout
 * - LLM concurrency semaphore
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { SkillEngineConfig } from "../config.js";
import type {
  EngineSessionKey,
  EngineInstance,
  EngineWorkerMessage,
  EngineCommandResponse,
  EngineCommandType,
  EngineSpawnOptions,
  EngineInstanceState,
  EventSubscriber,
  PersistedPart,
} from "../types/engine.js";
import { Semaphore } from "./semaphore.js";
import type { MessageCache } from "../redis/message-cache.js";
import type { LlmCredentialClient } from "../llm/credential-client.js";
import {
  InstanceNotFoundError,
  ConflictError,
  CommandTimeoutError,
} from "../utils/errors.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

export class EngineInstanceManager {
  private readonly instances = new Map<EngineSessionKey, EngineInstance>();
  private gcInterval: NodeJS.Timeout | null = null;
  private readonly llmSemaphore: Semaphore;
  /** Cached LLM env (LITELLM_API_KEY/BASE_URL) fetched from manager; null = not yet fetched. */
  private llmEnvCache: Record<string, string> | null = null;
  /** In-flight credential fetch promise (dedupes concurrent first-spawns). */
  private llmEnvPromise: Promise<Record<string, string>> | null = null;

  constructor(
    private readonly config: SkillEngineConfig,
    private readonly messageCache?: MessageCache,
    private readonly llmCredentialClient?: LlmCredentialClient,
  ) {
    this.llmSemaphore = new Semaphore(config.maxConcurrentLlm);
  }

  // ── Lifecycle ────────────────────────────────────────────────

  /** Start the idle GC timer. */
  startGc(): void {
    if (this.gcInterval) return;
    this.gcInterval = setInterval(
      () => void this.idleGc(),
      this.config.gcIntervalMs,
    );
  }

  /** Stop the idle GC timer. */
  stopGc(): void {
    if (this.gcInterval) {
      clearInterval(this.gcInterval);
      this.gcInterval = null;
    }
  }

  // ── Spawn / Stop ─────────────────────────────────────────────

  /**
   * Spawn an engine worker for the given workspace and role.
   * Idempotent: if an instance already exists and is ready, returns it.
   */
  async spawn(
    workspaceId: string,
    role: string,
    options: EngineSpawnOptions,
  ): Promise<EngineInstance> {
    const key: EngineSessionKey = `${workspaceId}:${role}`;

    const existing = this.instances.get(key);
    if (existing && existing.ready && !existing.stopping) {
      return existing;
    }
    if (existing && !existing.ready && !existing.stopping) {
      throw new ConflictError(`Instance ${key} is still starting`);
    }
    if (existing?.stopping) {
      throw new ConflictError(`Instance ${key} is shutting down`);
    }

    // Global concurrency cap (per-workspace 1 dev + 1 debug is already enforced
    // by the key; this bounds total worker subprocesses).
    if (this.instances.size >= this.config.maxInstances) {
      throw new ConflictError("max instances reached");
    }

    return this._spawnAndAttach(workspaceId, role, options);
  }

  /**
   * Create the child process, wire stdout/stderr/exit, and await ready.
   * Shared by spawn() and crash-recovery respawn. The new instance starts with
   * restartCount=0/lastRestartAt=0; _respawn overrides the carried budget.
   */
  private async _spawnAndAttach(
    workspaceId: string,
    role: string,
    options: EngineSpawnOptions,
  ): Promise<EngineInstance> {
    const key: EngineSessionKey = `${workspaceId}:${role}`;
    const workerPath = this._resolveWorkerPath();
    const { command, args } = this._buildSpawnArgs(workerPath, role, options);

    // Inject runtime-fetched LLM credentials (key not in pod env). Cached after
    // first fetch; failure returns {} (worker has no LLM) and isn't cached so
    // the next spawn retries.
    const llmEnv = await this._getLlmEnv();

    const child: ChildProcess = spawn(command, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        ...options.env,
        ...llmEnv,
        // debug 会话镜像 SKILL.md 时按非密钥 config 替换 ${config.param}，
        // worker 需 workspaceId 才能从 skill-engine HTTP 取 configValues。
        SKILL_WORKSPACE_ID: workspaceId,
      },
    });

    const instance: EngineInstance = {
      workspaceId,
      role,
      process: child,
      lastActivity: new Date(),
      eventSubscribers: new Map(),
      pendingRequests: new Map(),
      ready: false,
      stopping: false,
      currentTurn: null,
      spawnOptions: options,
      restartCount: 0,
      lastRestartAt: 0,
    };
    this.instances.set(key, instance);

    // Read stdout JSONL
    const rl = createInterface({ input: child.stdout! });
    rl.on("line", (line) => {
      if (!line.trim()) return;
      try {
        const msg = JSON.parse(line) as EngineWorkerMessage;
        this._handleWorkerMessage(instance, msg);
      } catch (err) {
        console.error(
          `[Worker ${key}] stdout parse error:`,
          (err as Error).message,
        );
      }
    });

    // Forward stderr
    child.stderr!.on("data", (data: Buffer) => {
      console.error(`[Worker ${key}] stderr:`, data.toString().trim());
    });

    // Single exit handler — covers both startup failure and runtime crash.
    child.on("exit", (code) => {
      this._onWorkerExit(instance, code);
    });

    // Wait for ready with event-driven detection (no polling)
    await new Promise<void>((resolve, reject) => {
      const startupTimeout = setTimeout(() => {
        reject(new Error("Worker startup timeout"));
        void this.stop(key);
      }, this.config.workerStartupTimeoutMs);

      instance._readyResolve = (): void => {
        clearTimeout(startupTimeout);
        resolve();
      };
      instance._readyReject = (err: Error): void => {
        clearTimeout(startupTimeout);
        reject(err);
      };
    });

    return instance;
  }

  /**
   * Handle worker process exit: reject pending requests, resolve startup wait on
   * failure, and schedule a respawn for unexpected crashes of ready workers.
   */
  private _onWorkerExit(instance: EngineInstance, code: number | null): void {
    const key: EngineSessionKey = `${instance.workspaceId}:${instance.role}`;
    console.log(`[Worker ${key}] exited with code ${code}`);

    // Reject all pending requests
    for (const [, pending] of instance.pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`Worker exited with code ${code}`));
    }
    instance.pendingRequests.clear();

    // Startup phase: reject the spawn/ready promise (no respawn — avoid crash
    // loop on bad config; the caller learns the worker wouldn't start).
    if (!instance.ready) {
      if (instance._readyReject) {
        instance._readyReject(
          new Error(`Worker exited before ready with code ${code}`),
        );
        instance._readyResolve = undefined;
        instance._readyReject = undefined;
      }
    }

    // Only the live instance manages lifecycle; ignore stale exit (e.g. a
    // respawn already replaced it, or stop() already cleaned up).
    if (this.instances.get(key) !== instance) return;
    this.instances.delete(key);

    if (instance.stopping) return; // intentional stop
    if (!instance.ready) return; // startup failure — no auto-respawn

    // Unexpected crash of a ready worker → schedule respawn.
    this._scheduleRespawn(instance);
  }

  /**
   * Schedule a respawn for a crashed worker, respecting the restart budget.
   * Budget resets after the worker ran stably beyond restartWindowMs.
   */
  private _scheduleRespawn(instance: EngineInstance): void {
    const now = Date.now();
    let count = instance.restartCount;
    if (
      instance.lastRestartAt > 0 &&
      now - instance.lastRestartAt > this.config.restartWindowMs
    ) {
      count = 0; // stable run beyond window → fresh budget
    }
    const key: EngineSessionKey = `${instance.workspaceId}:${instance.role}`;

    if (count >= this.config.maxRestarts) {
      console.error(
        `[Manager] ${key} giving up after ${count} restarts (manual restart required)`,
      );
      return;
    }

    const newCount = count + 1;
    // Carry over live SSE subscribers — the Fastify SSE responses are still
    // open (only the worker subprocess died), so transferring them to the new
    // instance lets connected clients keep receiving events without reconnect.
    const subscribers = instance.eventSubscribers;
    console.warn(
      `[Manager] ${key} scheduling respawn #${newCount} in ${this.config.restartDelayMs}ms (${subscribers.size} subscribers)`,
    );
    setTimeout(
      () =>
        void this._respawn(
          instance.workspaceId,
          instance.role,
          instance.spawnOptions,
          newCount,
          now,
          subscribers,
        ),
      this.config.restartDelayMs,
    );
  }

  /** Respawn a crashed worker, carrying the restart budget + SSE subscribers. */
  private async _respawn(
    workspaceId: string,
    role: string,
    options: EngineSpawnOptions,
    restartCount: number,
    lastRestartAt: number,
    subscribers: Map<string, EventSubscriber>,
  ): Promise<void> {
    try {
      const inst = await this._spawnAndAttach(workspaceId, role, options);
      inst.restartCount = restartCount;
      inst.lastRestartAt = lastRestartAt;
      // Transfer live SSE subscribers so connected clients seamlessly receive
      // the respawned worker's events on their existing connection.
      inst.eventSubscribers = subscribers;
      console.log(
        `[Manager] respawned ${workspaceId}:${role} (pid ${inst.process.pid}, ${subscribers.size} subscribers)`,
      );
    } catch (err) {
      // Startup failure during respawn — _onWorkerExit already declined to
      // auto-respawn startup failures, so just log.
      console.error(
        `[Manager] respawn of ${workspaceId}:${role} failed:`,
        (err as Error).message,
      );
    }
  }

  /**
   * Stop an engine instance. SIGTERM → wait → SIGKILL.
   */
  async stop(key: EngineSessionKey): Promise<void> {
    const instance = this.instances.get(key);
    if (!instance) return;

    instance.stopping = true;

    // Flush message cache to PostgreSQL before stopping
    if (this.messageCache) {
      try {
        await this.messageCache.flushToPostgres(
          instance.workspaceId,
          instance.role,
        );
      } catch (err) {
        console.error(
          `[Manager] Flush error for ${key}:`,
          (err as Error).message,
        );
      }
    }

    try {
      instance.process.kill("SIGTERM");
    } catch {
      // Process may have already exited
    }

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        try {
          instance.process.kill("SIGKILL");
        } catch {
          // Ignore
        }
        resolve();
      }, this.config.gracefulShutdownMs);

      instance.process.on("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });

    this.instances.delete(key);
  }

  /** Stop all active instances (for graceful shutdown). */
  async stopAll(): Promise<void> {
    const keys = [...this.instances.keys()];
    await Promise.all(keys.map((k) => this.stop(k)));
  }

  // ── Commands ─────────────────────────────────────────────────

  /** Send a generic command to an engine worker. */
  async sendCommand(
    key: EngineSessionKey,
    type: EngineCommandType,
    params: Record<string, unknown> = {},
  ): Promise<EngineCommandResponse> {
    const instance = this.instances.get(key);
    if (!instance || instance.stopping) {
      throw new InstanceNotFoundError(key);
    }

    const id = randomUUID();
    const command = { type, id, ...params };

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        instance.pendingRequests.delete(id);
        reject(new CommandTimeoutError(`Command ${type} timeout`));
      }, this.config.commandTimeoutMs);

      instance.pendingRequests.set(id, { resolve, reject, timer });
      instance.process.stdin!.write(JSON.stringify(command) + "\n");
      instance.lastActivity = new Date();
    });
  }

  /** Send a prompt (rate-limited by LLM semaphore). */
  async prompt(key: EngineSessionKey, message: string): Promise<EngineCommandResponse> {
    // Persist the user message before dispatching (best-effort, non-blocking).
    const instance = this.instances.get(key);
    if (instance && this.messageCache) {
      this._persistUserMessage(instance, message);
    }
    await this.llmSemaphore.acquire();
    try {
      return await this.sendCommand(key, "prompt", { message });
    } finally {
      this.llmSemaphore.release();
    }
  }

  /** Mid-turn steering. */
  async steer(key: EngineSessionKey, message: string): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "steer", { message });
  }

  /** Queue a follow-up message for after the current turn. */
  async followUp(key: EngineSessionKey, message: string): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "follow_up", { message });
  }

  /** Abort current generation. */
  async abort(key: EngineSessionKey): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "abort");
  }

  /** Get engine instance internal state. */
  async getState(key: EngineSessionKey): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "get_state");
  }

  /** Hot-reload skills/resources (re-reads SKILL.md) without a full restart. */
  async reload(key: EngineSessionKey): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "reload");
  }

  /**
   * Submit a clarify tool's user-supplied answers to resolve its blocked
   * `execute()` promise. `toolCallId` identifies the pending clarify call;
   * `answers` is the structured user input keyed by question id.
   */
  async submitToolResponse(
    key: EngineSessionKey,
    toolCallId: string,
    answers: Record<string, unknown>,
  ): Promise<EngineCommandResponse> {
    return this.sendCommand(key, "tool_response", { toolCallId, answers });
  }

  /**
   * Resolve the LLM env vars (LITELLM_API_KEY/BASE_URL) to inject into workers.
   * With an LlmCredentialClient: fetched once from manager and cached. Without
   * (or fetch failure): {} — workers then rely on whatever LITELLM_API_KEY is in
   * the pod env (fallback for local dev / unconfigured deployments).
   */
  private async _getLlmEnv(): Promise<Record<string, string>> {
    if (this.llmEnvCache !== null) return this.llmEnvCache;
    if (!this.llmCredentialClient) {
      this.llmEnvCache = {};
      return this.llmEnvCache;
    }
    if (this.llmEnvPromise) return this.llmEnvPromise;
    this.llmEnvPromise = (async (): Promise<Record<string, string>> => {
      const creds = await this.llmCredentialClient!.fetchCredentials();
      if (!creds) {
        // Don't cache failure — let the next spawn retry.
        this.llmEnvPromise = null;
        return {};
      }
      const env: Record<string, string> = { LITELLM_API_KEY: creds.apiKey };
      if (creds.baseUrl) env.LITELLM_BASE_URL = creds.baseUrl;
      this.llmEnvCache = env;
      this.llmEnvPromise = null;
      return env;
    })();
    return this.llmEnvPromise;
  }

  // ── Query ────────────────────────────────────────────────────

  /** Get an instance if it exists. */
  getInstance(key: EngineSessionKey): EngineInstance | undefined {
    return this.instances.get(key);
  }

  /** Check if an instance exists. */
  hasInstance(key: EngineSessionKey): boolean {
    return this.instances.has(key);
  }

  /** Get stats for all instances (admin endpoint). */
  getStats(): Record<
    string,
    {
      ready: boolean;
      lastActivity: string;
      subscribers: number;
      pendingRequests: number;
    }
  > {
    const stats: Record<string, { ready: boolean; lastActivity: string; subscribers: number; pendingRequests: number }> = {};
    for (const [key, instance] of this.instances) {
      stats[key] = {
        ready: instance.ready,
        lastActivity: instance.lastActivity.toISOString(),
        subscribers: instance.eventSubscribers.size,
        pendingRequests: instance.pendingRequests.size,
      };
    }
    return stats;
  }

  /** Get resource overview (admin endpoint). */
  getResources(): {
    activeInstances: number;
    llmSemaphore: { acquired: number; queued: number; max: number };
  } {
    return {
      activeInstances: this.instances.size,
      llmSemaphore: {
        acquired: this.llmSemaphore.acquired,
        queued: this.llmSemaphore.queued,
        max: this.llmSemaphore.maxSlots,
      },
    };
  }

  // ── Idle GC ──────────────────────────────────────────────────

  /** Stop instances that have been idle longer than the timeout. */
  async idleGc(): Promise<void> {
    const now = Date.now();
    for (const [key, instance] of this.instances) {
      if (
        !instance.stopping &&
        now - instance.lastActivity.getTime() > this.config.idleTimeoutMs
      ) {
        console.log(`[Manager] Idle GC: stopping ${key}`);
        await this.stop(key);
      }
    }
  }

  // ── Internal ─────────────────────────────────────────────────

  /** Resolve the path to the compiled engine-worker.js. */
  private _resolveWorkerPath(): string {
    // In production: dist/engine/engine-worker.js (compiled)
    // In dev with tsx: this file won't be reached (tsx handles it)
    return join(__dirname, "engine-worker.js");
  }

  /**
   * Build spawn command and args. Override in tests to use a different worker.
   * Default: `node <workerPath> --cwd ... --role ...`
   */
  protected _buildSpawnArgs(
    workerPath: string,
    role: string,
    options: EngineSpawnOptions,
  ): { command: string; args: string[] } {
    const args = [workerPath, "--cwd", options.cwd, "--role", role];
    if (options.tools) args.push("--tools", options.tools.join(","));
    if (options.excludeTools)
      args.push("--exclude", options.excludeTools.join(","));
    return { command: "node", args };
  }

  /**
   * Handle a message from an engine worker's stdout.
   * Dispatches to: ready signal, pending request resolution, or SSE broadcast.
   */
  private _handleWorkerMessage(
    instance: EngineInstance,
    msg: EngineWorkerMessage,
  ): void {
    // Ready status — resolve the spawn() promise
    if (msg.type === "status" && msg.status === "ready") {
      instance.ready = true;
      if (instance._readyResolve) {
        instance._readyResolve();
        instance._readyResolve = undefined;
        instance._readyReject = undefined;
      }
      return;
    }

    // Response — match to a pending request
    if (msg.type === "response" && msg.id) {
      const pending = instance.pendingRequests.get(msg.id);
      if (pending) {
        instance.pendingRequests.delete(msg.id);
        clearTimeout(pending.timer);
        if (msg.success) {
          pending.resolve(msg);
        } else {
          pending.reject(new Error(msg.error ?? "Unknown worker error"));
        }
      }
      return;
    }

    // Event — broadcast to SSE subscribers + persist to message cache
    if (msg.type === "event") {
      instance.lastActivity = new Date();
      const deadClients: string[] = [];
      for (const [clientId, { send }] of instance.eventSubscribers) {
        try {
          send(msg);
        } catch {
          deadClients.push(clientId);
        }
      }
      for (const id of deadClients) {
        instance.eventSubscribers.delete(id);
      }

      // Persist message events to Redis cache
      this._persistEvent(instance, msg);
      return;
    }

    // Heartbeat / other status — update activity, no action
    if (msg.type === "heartbeat" || msg.type === "status") {
      instance.lastActivity = new Date();
      return;
    }
  }

  /**
   * Persist a user message to the cache. Called from prompt() before dispatch.
   */
  private _persistUserMessage(instance: EngineInstance, message: string): void {
    if (!this.messageCache) return;
    this.messageCache
      .appendMessage({
        id: randomUUID(),
        workspaceId: instance.workspaceId,
        role: instance.role,
        sender: "user",
        content: message,
        toolCalls: null,
        createdAt: new Date().toISOString(),
      })
      .catch((err) => {
        console.error(`[Manager] User message cache write error:`, (err as Error).message);
      });
  }

  /**
   * Extract accumulated assistant text from a message_update / message_end
   * event. Real pi SDK shape: data.message.content[] of {type:"text",text}
   * blocks. Falls back to legacy text_delta/text for older emitters.
   */
  private _extractAssistantText(data: Record<string, unknown>): string {
    const msg = data.message as
      | { content?: Array<{ type?: string; text?: string }> }
      | undefined;
    const blocks = msg?.content;
    if (Array.isArray(blocks)) {
      return blocks
        .filter((b) => b && b.type === "text" && typeof b.text === "string")
        .map((b) => b.text as string)
        .join("");
    }
    const delta = data.text_delta;
    if (typeof delta === "string") return delta;
    const text = data.text;
    return typeof text === "string" ? text : "";
  }

  /**
   * Accumulate SDK events into a per-turn buffer and persist ONE assistant
   * message on turn_end. This avoids the old per-fragment storage (which
   * wrote a row per message_update with JSON.stringify(data) content and
   * never persisted tool calls). The persisted `tool_calls` column holds the
   * ordered PersistedPart[] so the frontend can reconstruct text/tool
   * interleaving from history.
   */
  private _persistEvent(
    instance: EngineInstance,
    msg: import("../types/engine.js").EngineEventMessage,
  ): void {
    if (!this.messageCache) return;

    const { eventType, data } = msg;

    // Ensure a turn buffer exists (turn_start creates a fresh one, but events
    // may arrive before it in edge cases).
    if (!instance.currentTurn) instance.currentTurn = { parts: [], textClosed: false };
    const turn = instance.currentTurn;

    switch (eventType) {
      case "turn_start":
        instance.currentTurn = { parts: [], textClosed: false };
        break;

      case "message_update": {
        const text = this._extractAssistantText(data);
        if (!text) break;
        const last = turn.parts[turn.parts.length - 1];
        if (last && last.kind === "text" && !turn.textClosed) {
          // Same message accumulating — replace.
          last.text = text;
        } else {
          // New message (after a tool, or after message_end) — new text part.
          turn.parts.push({ kind: "text", text });
          turn.textClosed = false;
        }
        break;
      }

      case "message_end":
        turn.textClosed = true;
        break;

      case "tool_execution_start": {
        const toolCallId = data.toolCallId as string | undefined;
        const toolName = data.toolName as string | undefined;
        if (!toolCallId || !toolName) break;
        // Idempotent: skip if already tracked (defends against replay).
        const exists = turn.parts.some(
          (p) => p.kind === "tool" && p.tool.id === toolCallId,
        );
        if (exists) break;
        turn.parts.push({
          kind: "tool",
          tool: {
            id: toolCallId,
            toolName,
            args: (data.args as Record<string, unknown>) ?? null,
            status: "running",
            startedAt: new Date().toISOString(),
          },
        });
        break;
      }

      case "tool_execution_end": {
        const toolCallId = data.toolCallId as string | undefined;
        if (!toolCallId) break;
        const part = turn.parts.find(
          (p): p is Extract<PersistedPart, { kind: "tool" }> =>
            p.kind === "tool" && p.tool.id === toolCallId,
        );
        if (part) {
          part.tool.result = data.result;
          part.tool.status = data.isError ? "error" : "done";
        }
        break;
      }

      case "turn_end": {
        // Flush the turn as a single assistant message.
        const parts: PersistedPart[] = turn.parts;
        const content = parts
          .filter((p): p is Extract<PersistedPart, { kind: "text" }> => p.kind === "text")
          .map((p) => p.text)
          .join("\n");
        instance.currentTurn = null;
        // Skip empty turns (no text and no tools).
        if (!content && parts.length === 0) break;
        this.messageCache
          .appendMessage({
            id: randomUUID(),
            workspaceId: instance.workspaceId,
            role: instance.role,
            sender: "assistant",
            content,
            toolCalls: parts,
            createdAt: new Date().toISOString(),
          })
          .catch((err) => {
            console.error(`[Manager] Turn cache write error:`, (err as Error).message);
          });
        break;
      }

      default:
        // Other events (agent_start/end, message_start, heartbeats, etc.) are
        // not persisted — they carry no user-visible turn content.
        break;
    }
  }
}

// Augment EngineInstance with internal ready-resolve/reject callbacks
declare module "../types/engine.js" {
  interface EngineInstance {
    /** Internal: resolve the spawn() promise when worker reports ready. */
    _readyResolve?: () => void;
    /** Internal: reject the spawn() promise when worker exits before ready. */
    _readyReject?: (err: Error) => void;
  }
}
