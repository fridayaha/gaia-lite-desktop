/**
 * main.js — Skill Engine 原型主进程
 *
 * Fastify HTTP 服务 + 子进程管理
 * - 每活跃 workspace:role 一个子进程
 * - HTTP → JSONL stdin 桥接
 * - 子进程 stdout JSONL → SSE 推送
 * - LLM 速率控制（Semaphore）
 */

import Fastify from "fastify";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WORKER_PATH = join(__dirname, "engine-worker.js");

// ── 配置 ───────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT || "8004");
const MAX_CONCURRENT_LLM = parseInt(process.env.MAX_CONCURRENT_LLM || "5");
const IDLE_TIMEOUT_MS = parseInt(process.env.IDLE_TIMEOUT_MS || "1800000"); // 30 min

// ── LLM 并发信号量 ─────────────────────────────────────────

class Semaphore {
  constructor(max) {
    this.max = max;
    this.current = 0;
    this.queue = [];
  }
  async acquire() {
    if (this.current < this.max) {
      this.current++;
      return;
    }
    return new Promise((resolve) => this.queue.push(resolve));
  }
  release() {
    this.current--;
    if (this.queue.length > 0) {
      this.current++;
      const next = this.queue.shift();
      next();
    }
  }
}

const llmSemaphore = new Semaphore(MAX_CONCURRENT_LLM);

// ── EngineInstance ──────────────────────────────────────────

class EngineInstance {
  constructor(workspaceId, role, process) {
    this.workspaceId = workspaceId;
    this.role = role;
    this.process = process;
    this.lastActivity = new Date();
    this.eventSubscribers = new Map(); // clientId → { send }
    this.pendingRequests = new Map(); // requestId → { resolve, reject, timer }
    this.ready = false;
    this.stopping = false;
  }

  get key() {
    return `${this.workspaceId}:${this.role}`;
  }
}

// ── EngineInstanceManager ───────────────────────────────────

class EngineInstanceManager {
  constructor() {
    this.instances = new Map(); // key → EngineInstance
    this.gcInterval = null;
  }

  startGc() {
    this.gcInterval = setInterval(() => this.idleGc(), 60000); // 每分钟检查
  }

  stopGc() {
    if (this.gcInterval) clearInterval(this.gcInterval);
  }

  async spawn(workspaceId, role, options = {}) {
    const key = `${workspaceId}:${role}`;
    if (this.instances.has(key)) {
      return this.instances.get(key);
    }

    const args = [
      WORKER_PATH,
      "--cwd", options.cwd || `/tmp/skill-engine-workspaces/${workspaceId}`,
      "--role", role,
    ];
    if (options.tools) args.push("--tools", options.tools.join(","));
    if (options.excludeTools) args.push("--exclude", options.excludeTools.join(","));

    console.log(`[Manager] Spawning worker: ${key}`);
    const startMs = Date.now();

    const child = spawn("node", args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...options.env },
    });

    const instance = new EngineInstance(workspaceId, role, child);
    this.instances.set(key, instance);

    // 读取 stdout JSONL
    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      if (!line.trim()) return;
      try {
        const msg = JSON.parse(line);
        this._handleWorkerMessage(instance, msg);
      } catch (err) {
        console.error(`[Worker ${key}] stdout parse error:`, err.message);
      }
    });

    // 读取 stderr
    child.stderr.on("data", (data) => {
      console.error(`[Worker ${key}] stderr:`, data.toString().trim());
    });

    // 子进程退出
    child.on("exit", (code) => {
      console.log(`[Worker ${key}] exited with code ${code}`);
      this.instances.delete(key);
      // 拒绝所有 pending requests
      for (const [id, { reject, timer }] of instance.pendingRequests) {
        clearTimeout(timer);
        reject(new Error(`Worker exited with code ${code}`));
      }
    });

    // 等待 ready
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Worker startup timeout (30s)"));
        this.stop(key);
      }, 30000);

      const checkReady = setInterval(() => {
        if (instance.ready) {
          clearTimeout(timeout);
          clearInterval(checkReady);
          const elapsed = Date.now() - startMs;
          console.log(`[Manager] Worker ${key} ready in ${elapsed}ms`);
          resolve();
        }
      }, 100);
    });

    return instance;
  }

  async sendCommand(key, type, params = {}) {
    const instance = this.instances.get(key);
    if (!instance || instance.stopping) {
      throw new Error(`No active instance for ${key}`);
    }

    const id = randomUUID();
    const command = { type, id, ...params };

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        instance.pendingRequests.delete(id);
        reject(new Error(`Command ${type} timeout (120s)`));
      }, 120000);

      instance.pendingRequests.set(id, { resolve, reject, timer });
      instance.process.stdin.write(JSON.stringify(command) + "\n");
      instance.lastActivity = new Date();
    });
  }

  async prompt(key, message) {
    await llmSemaphore.acquire();
    try {
      return await this.sendCommand(key, "prompt", { message });
    } finally {
      llmSemaphore.release();
    }
  }

  async abort(key) {
    return this.sendCommand(key, "abort");
  }

  async getState(key) {
    return this.sendCommand(key, "get_state");
  }

  async stop(key) {
    const instance = this.instances.get(key);
    if (!instance) return;
    instance.stopping = true;
    try {
      instance.process.kill("SIGTERM");
    } catch {}
    // 给 5s 优雅退出
    await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        try { instance.process.kill("SIGKILL"); } catch {}
        resolve();
      }, 5000);
      instance.process.on("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
    this.instances.delete(key);
  }

  async idleGc() {
    const now = Date.now();
    for (const [key, instance] of this.instances) {
      if (now - instance.lastActivity.getTime() > IDLE_TIMEOUT_MS) {
        console.log(`[Manager] Idle GC: stopping ${key}`);
        await this.stop(key);
      }
    }
  }

  _handleWorkerMessage(instance, msg) {
    // ready 状态
    if (msg.type === "status" && msg.status === "ready") {
      instance.ready = true;
      return;
    }

    // 响应（匹配 pending request）
    if (msg.type === "response" && msg.id) {
      const pending = instance.pendingRequests.get(msg.id);
      if (pending) {
        instance.pendingRequests.delete(msg.id);
        clearTimeout(pending.timer);
        if (msg.success) {
          pending.resolve(msg);
        } else {
          pending.reject(new Error(msg.error || "Unknown error"));
        }
      }
      return;
    }

    // 事件（分发给 SSE 订阅者）
    if (msg.type === "event") {
      instance.lastActivity = new Date();
      const deadClients = [];
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
      return;
    }

    // 心跳 / 状态 / 错误 → 日志
    if (msg.type === "heartbeat" || msg.type === "status") {
      return;
    }
    console.log(`[Worker ${instance.key}] unknown message type:`, msg.type);
  }

  getStats() {
    const stats = {};
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
}

// ── Fastify 服务 ───────────────────────────────────────────

const app = Fastify({ logger: false });
const manager = new EngineInstanceManager();

// 创建工作区目录（简化版，用本地文件系统）
function getWorkspaceDir(workspaceId) {
  return `/tmp/skill-engine-workspaces/${workspaceId}`;
}

// 确保工作区目录存在
import { mkdirSync, writeFileSync, existsSync } from "node:fs";

function ensureWorkspace(workspaceId, skillName = "my-skill") {
  const dir = getWorkspaceDir(workspaceId);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
    mkdirSync(join(dir, ".pi", "skills"), { recursive: true });
    mkdirSync(join(dir, ".pi", "skills", "skill-dev-assistant"), { recursive: true });
    // 写入默认开发指引 skill
    writeFileSync(join(dir, ".pi", "skills", "skill-dev-assistant", "SKILL.md"), [
      "---",
      "name: skill-dev-assistant",
      "description: 技能开发助手",
      "---",
      "",
      "你是一个技能开发助手。帮助用户创建和修改智能体技能文件。",
      "技能文件规范：",
      "- SKILL.md: YAML frontmatter(name/description/version/author) + Markdown正文",
      "- manifest.json: {name, version, author, description, engine:'hermes', config_params:[]}",
    ].join("\n"));
  }
  return dir;
}

// ── Routes ─────────────────────────────────────────────────

// 创建工作区
app.post("/api/skill-engine/workspaces", async (req) => {
  const { name = "my-skill", description = "" } = req.body || {};
  const workspaceId = randomUUID();
  const cwd = ensureWorkspace(workspaceId, name);
  return { id: workspaceId, name, description, cwd, status: "active" };
});

// 启动引擎会话
app.post("/api/skill-engine/workspaces/:wid/sessions", async (req) => {
  const { wid } = req.params;
  const { role = "dev" } = req.body || {};
  const key = `${wid}:${role}`;

  ensureWorkspace(wid);

  const options = {
    cwd: getWorkspaceDir(wid),
    tools: role === "dev" ? ["read", "write", "edit", "bash", "grep", "find", "ls"] : ["read", "grep", "find", "ls"],
    excludeTools: role === "debug" ? ["write", "edit", "bash"] : undefined,
  };

  const instance = await manager.spawn(wid, role, options);
  return { sessionId: key, role, status: instance.ready ? "ready" : "starting" };
});

// 发送 prompt
app.post("/api/skill-engine/workspaces/:wid/sessions/:sid/prompt", async (req) => {
  const { wid, sid } = req.params;
  const { message } = req.body || {};
  if (!message) throw new Error("message is required");

  const key = `${wid}:${sid}`;
  const result = await manager.prompt(key, message);
  return result;
});

// 中断
app.post("/api/skill-engine/workspaces/:wid/sessions/:sid/abort", async (req) => {
  const { wid, sid } = req.params;
  const key = `${wid}:${sid}`;
  return await manager.abort(key);
});

// 获取状态
app.get("/api/skill-engine/workspaces/:wid/sessions/:sid/state", async (req) => {
  const { wid, sid } = req.params;
  const key = `${wid}:${sid}`;
  return await manager.getState(key);
});

// SSE 事件流
app.get("/api/skill-engine/workspaces/:wid/sessions/:sid/events", async (req) => {
  const { wid, sid } = req.params;
  const key = `${wid}:${sid}`;
  const instance = manager.instances.get(key);
  if (!instance) throw new Error(`No active instance for ${key}`);

  const clientId = randomUUID();

  return new Promise((resolve) => {
    let closed = false;

    const send = (msg) => {
      if (closed) return;
      // SSE 格式
      const data = JSON.stringify(msg);
      // 这里我们用 raw response 直接写 SSE
    };

    // 使用 Fastify 的 raw response 写 SSE
    const res = req.raw.res;
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    const sseSend = (msg) => {
      if (closed) return;
      try {
        res.write(`data: ${JSON.stringify(msg)}\n\n`);
      } catch {
        closed = true;
        instance.eventSubscribers.delete(clientId);
      }
    };

    instance.eventSubscribers.set(clientId, { send: sseSend });

    req.raw.on("close", () => {
      closed = true;
      instance.eventSubscribers.delete(clientId);
    });

    // 发初始连接确认
    res.write(`data: ${JSON.stringify({ type: "connected", sessionId: key })}\n\n`);

    // 不 resolve，保持连接
  });
});

// 停止会话
app.delete("/api/skill-engine/workspaces/:wid/sessions/:sid", async (req) => {
  const { wid, sid } = req.params;
  const key = `${wid}:${sid}`;
  await manager.stop(key);
  return { ok: true };
});

// 管理端点：查看所有实例状态
app.get("/api/skill-engine/admin/stats", async () => {
  return manager.getStats();
});

// 管理端点：资源监控
app.get("/api/skill-engine/admin/resources", async () => {
  const { default: si } = await import("node:os");
  return {
    totalMemoryMB: Math.round(si.totalmem() / 1024 / 1024),
    freeMemoryMB: Math.round(si.freemem() / 1024 / 1024),
    cpuCount: si.cpus().length,
    loadAvg: si.loadavg(),
    activeInstances: manager.instances.size,
    llmSemaphore: {
      current: llmSemaphore.current,
      max: llmSemaphore.max,
      queued: llmSemaphore.queue.length,
    },
  };
});

// ── 启动 ───────────────────────────────────────────────────

try {
  await app.listen({ port: PORT, host: "0.0.0.0" });
  manager.startGc();
  console.log(`[Skill Engine Prototype] Listening on http://0.0.0.0:${PORT}`);
  console.log(`[Skill Engine Prototype] MAX_CONCURRENT_LLM=${MAX_CONCURRENT_LLM}, IDLE_TIMEOUT=${IDLE_TIMEOUT_MS}ms`);
} catch (err) {
  console.error("Failed to start:", err);
  process.exit(1);
}

// 优雅关闭
process.on("SIGTERM", async () => {
  console.log("[Skill Engine Prototype] Shutting down...");
  manager.stopGc();
  for (const key of manager.instances.keys()) {
    await manager.stop(key);
  }
  await app.close();
  process.exit(0);
});

export { manager, llmSemaphore };
