/**
 * engine-worker.js — 子进程内运行单个引擎 SDK 会话
 *
 * 通信协议：stdin/stdout JSONL
 * - 收到命令：{ type: "prompt"|"abort"|"get_state"|"steer", id?, message?, ... }
 * - 输出响应：{ type: "response", id?, command, success, data? }
 * - 输出事件：{ type: "event", eventType, data }  (SDK AgentSessionEvent)
 * - 输出心跳：{ type: "heartbeat", ts }
 *
 * 启动参数（命令行）：
 *   --cwd <path>        工作目录
 *   --role <dev|debug>  实例角色
 *   --tools <csv>       允许的工具名列表
 *   --exclude <csv>     排除的工具名列表
 */

import { createAgentSession, SessionManager } from "@earendil-works/pi-coding-agent";
import { parseArgs } from "node:util";
import { createInterface } from "node:readline";

// ── 解析启动参数 ────────────────────────────────────────────

const { values } = parseArgs({
  options: {
    cwd: { type: "string" },
    role: { type: "string", default: "dev" },
    tools: { type: "string" },
    exclude: { type: "string" },
  },
  strict: true,
});

const cwd = values.cwd || process.cwd();
const role = values.role;
const tools = values.tools ? values.tools.split(",") : undefined;
const excludeTools = values.exclude ? values.exclude.split(",") : undefined;

// ── JSONL 输出工具 ──────────────────────────────────────────

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

// ── 创建 SDK 会话 ───────────────────────────────────────────

let session = null;

async function initSession() {
  send({ type: "status", status: "initializing", role, cwd });

  try {
    // 使用 in-memory session manager 避免文件系统副作用
    const sessionManager = SessionManager.inMemory(cwd);

    const result = await createAgentSession({
      cwd,
      sessionManager,
      tools,
      excludeTools,
    });

    session = result.session;

    // 订阅所有 SDK 事件，转发到 stdout
    session.subscribe((event) => {
      send({ type: "event", eventType: event.type, data: event });
    });

    send({ type: "status", status: "ready", role });
  } catch (err) {
    send({ type: "status", status: "error", error: err.message, stack: err.stack });
    process.exit(1);
  }
}

// ── 命令处理 ────────────────────────────────────────────────

async function handleCommand(cmd) {
  const { type, id } = cmd;

  try {
    switch (type) {
      case "prompt": {
        // 异步：prompt 返回后事件通过 subscribe 流式推送
        await session.prompt(cmd.message);
        send({ type: "response", id, command: "prompt", success: true });
        break;
      }
      case "steer": {
        await session.steer(cmd.message);
        send({ type: "response", id, command: "steer", success: true });
        break;
      }
      case "abort": {
        await session.abort();
        send({ type: "response", id, command: "abort", success: true });
        break;
      }
      case "get_state": {
        // 读取 Agent 内部状态
        const state = session.agent?.state;
        send({
          type: "response",
          id,
          command: "get_state",
          success: true,
          data: {
            isStreaming: state?.isStreaming ?? false,
            model: state?.model ? { provider: state.model.provider, modelId: state.model.modelId } : null,
            messageCount: state?.messages?.length ?? 0,
          },
        });
        break;
      }
      default:
        send({ type: "response", id, command: type, success: false, error: `Unknown command: ${type}` });
    }
  } catch (err) {
    send({ type: "response", id, command: type, success: false, error: err.message });
  }
}

// ── 心跳（每 30s） ─────────────────────────────────────────

const heartbeat = setInterval(() => {
  send({ type: "heartbeat", ts: Date.now() });
}, 30000);

// ── stdin 读取 ─────────────────────────────────────────────

const rl = createInterface({ input: process.stdin });

rl.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    const cmd = JSON.parse(line);
    await handleCommand(cmd);
  } catch (err) {
    send({ type: "error", error: `Parse error: ${err.message}` });
  }
});

rl.on("close", async () => {
  clearInterval(heartbeat);
  if (session) {
    try { await session.dispose(); } catch {}
  }
  process.exit(0);
});

// ── 信号处理 ────────────────────────────────────────────────

process.on("SIGTERM", async () => {
  send({ type: "status", status: "shutting_down" });
  clearInterval(heartbeat);
  if (session) {
    try { await session.dispose(); } catch {}
  }
  process.exit(0);
});

// ── 启动 ────────────────────────────────────────────────────

initSession();
