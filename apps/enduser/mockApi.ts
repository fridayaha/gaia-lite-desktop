/**
 * Enduser 本地联调 mock —— A(manager) / B(gateway) 未就绪时按 01-接口契约.md 联调。
 *
 * 仅 dev 生效，VITE_USE_MOCK=true 开启（默认关闭）。开启方式：
 *   apps/enduser/.env.local 写入 VITE_USE_MOCK=true，或 VITE_USE_MOCK=true pnpm dev。
 * 覆盖：login + accessible 实例 + deploy/events(SSE) + gateway chat(SSE)/sessions/models/files。
 * A/B 就绪后删除本文件 + vite.config.ts 引用即可。
 */
import type { Connect, Plugin } from "vite";

type Handler = (req: Connect.IncomingMessage, res: any, match: RegExpMatchArray) => void | Promise<void>;
interface Route { method: string; pattern: RegExp; handler: Handler }

const now = () => new Date().toISOString();
const uid = (p: string) => `${p}-${Math.random().toString(36).slice(2, 10)}`;

const agents = [
  { id: "inst-seed-01", name: "通用助手", description: "通用问答 Agent", engine_type: "HERMES" as const },
  { id: "inst-seed-02", name: "代码助手", description: "代码审查与生成", engine_type: "HERMES" as const }
];

const sessions: Record<string, any> = {}; // id -> session

function readBody(req: Connect.IncomingMessage): Promise<any> {
  return new Promise(resolve => {
    let raw = "";
    req.on("data", c => (raw += c));
    req.on("end", () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch { resolve({}); } });
  });
}
function json(res: any, data: any, status = 200) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(data));
}

const routes: Route[] = [
  // ── manager: auth + accessible + deploy ──────────────────
  {
    method: "POST",
    pattern: /^\/api\/manager\/auth\/login$/,
    handler: async (_req, res) => json(res, { access_token: "mock-enduser-token", refresh_token: "mock-refresh", token_type: "bearer" })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/auth\/refresh$/,
    handler: async (_req, res) => json(res, { access_token: "mock-enduser-token", refresh_token: "mock-refresh", token_type: "bearer" })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/auth\/me$/,
    handler: async (_req, res) => json(res, { id: "u-1", username: "admin", email: "admin@ua.local" })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/accessible$/,
    handler: async (_req, res) => json(res, agents)
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deployment-status$/,
    handler: async (_req, res, m) =>
      json(res, { agent_id: m[1], status: "RUNNING", engine_url: `engine-hermes-${m[1].slice(0, 8)}.default.svc.cluster.local:8642`, last_active_at: now(), error_message: null })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deploy$/,
    handler: async (_req, res, m) => json(res, { agent_id: m[1], status: "DEPLOYING", engine_url: null, last_active_at: now(), error_message: null })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/models$/,
    handler: async (_req, res) =>
      json(res, { object: "list", data: [{ id: "openai/gpt-4o", object: "model", provider: "openai" }, { id: "anthropic/claude-sonnet-4.6", object: "model", provider: "anthropic" }] })
  },
  // deploy events SSE（EventSource，?token= 透传）
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deploy\/events$/,
    handler: async (req, res, m) => streamDeployEvents(req, res, m[1])
  },
  // ── gateway: sessions / models / files / chat ────────────
  // 注意：mock 中间件先于 vite proxy 执行，路径含 /api/gateway 前缀（未被 rewrite）
  {
    method: "GET",
    pattern: /^\/api\/gateway\/api\/sessions$/,
    handler: async (_req, res) => json(res, { sessions: Object.values(sessions), total: Object.keys(sessions).length })
  },
  {
    method: "POST",
    pattern: /^\/api\/gateway\/api\/sessions$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const s = { session_id: uid("sess"), id: uid("sess"), title: "新会话", messages: [], created_at: Date.now() / 1000, last_message_at: null, model: body.model || "gpt-4o" };
      sessions[s.session_id] = s;
      json(res, { session: s });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/gateway\/api\/sessions\/([^/]+)\/messages$/,
    handler: async (_req, res) => json(res, { items: [] })
  },
  {
    method: "DELETE",
    pattern: /^\/api\/gateway\/api\/sessions\/([^/]+)$/,
    handler: async (_req, res, m) => { delete sessions[m[1]]; json(res, { ok: true }); }
  },
  {
    method: "GET",
    pattern: /^\/api\/gateway\/v1\/models$/,
    handler: async (_req, res) =>
      json(res, { object: "list", data: [{ id: "openai/gpt-4o", object: "model" }, { id: "anthropic/claude-sonnet-4.6", object: "model" }] })
  },
  {
    method: "GET",
    pattern: /^\/api\/gateway\/v1\/files$/,
    handler: async (_req, res) => json(res, { items: [{ name: "README.md", path: "/workspace/README.md", size: 120, is_dir: false }] })
  },
  // chat completions SSE（OpenAI 兼容 chunk）
  {
    method: "POST",
    pattern: /^\/api\/gateway\/v1\/chat\/completions$/,
    handler: streamChatCompletions
  },
  // 审批响应：唤醒等待中的 chat SSE
  {
    method: "POST",
    pattern: /^\/api\/gateway\/v1\/chat\/approval$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const resolve = pendingApprovals.get(body.run_id);
      if (resolve) resolve(body.choice || "deny");
      json(res, { ok: true, resolved: !!resolve });
    }
  },
  // 兜底
  { method: "GET", pattern: /^\/api\/.*/, handler: async (_req, res) => json(res, { items: [] }) },
  { method: "POST", pattern: /^\/api\/.*/, handler: async (_req, res) => json(res, { ok: true }) }
];

function streamDeployEvents(req: Connect.IncomingMessage, res: any, agentId: string) {
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  const send = (obj: any) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  const steps = [
    { step: "build", message: "构建镜像", percentage: 30 },
    { step: "pod", message: "创建 Pod", percentage: 60 },
    { step: "ready", message: "引擎就绪", percentage: 100 }
  ];
  let i = 0;
  const tick = () => {
    if (res.writableEnded) return;
    if (i >= steps.length) {
      send({ step: "done", message: "部署完成", percentage: 100, engine_url: `engine-hermes-${agentId.slice(0, 8)}.default.svc.cluster.local:8642` });
      res.write("data: [DONE]\n\n");
      res.end();
      return;
    }
    const s = steps[i++];
    send({ step: s.step, message: s.message, percentage: s.percentage });
    setTimeout(tick, 500);
  };
  setTimeout(tick, 200);
  req.on("close", () => {});
}

// ── 审批工作流 mock 状态 ─────────────────────────────────
// run_id -> 状态；approval 等待器
const runStates = new Map<string, "streaming" | "waiting_approval" | "done">();
const pendingApprovals = new Map<string, (choice: string) => void>();

function streamChatCompletions(req: Connect.IncomingMessage, res: any) {
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  const send = (obj: any) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  const reply = "你好！我是 mock 智能体，这是一条 SSE 流式回复，用于联调端到端聊天链路。";

  readBody(req).then((body: any) => {
    const resumeRunId = body?.resume_run_id;
    // 续接：若该 run 仍在等待审批，重新下发 approval.request；否则视为已结束
    if (resumeRunId) {
      const st = runStates.get(resumeRunId);
      if (st === "waiting_approval") {
        send({ type: "run.start", run_id: resumeRunId });
        send({
          type: "approval.request",
          run_id: resumeRunId,
          command: "rm -rf /workspace/cache",
          description: "智能体请求删除缓存目录（需人工确认）",
          choices: ["once", "session", "always", "deny"]
        });
      } else {
        send({ type: "run.start", run_id: resumeRunId });
        send({ choices: [{ delta: {}, finish_reason: "stop" }] });
        res.write("data: [DONE]\n\n");
        res.end();
      }
      return;
    }

    // 新 run
    const runId = uid("run");
    runStates.set(runId, "streaming");
    send({ type: "run.start", run_id: runId });

    const lastMsg = (body?.messages || []).slice(-1)[0]?.content || "";
    const needsApproval = /审批|删除|删除文件|危险/.test(String(lastMsg));

    const streamContent = () => {
      runStates.set(runId, "streaming");
      const chars = reply.split("");
      let i = 0;
      const tick = () => {
        if (res.writableEnded) return;
        if (i >= chars.length) {
          send({ choices: [{ delta: {}, finish_reason: "stop" }] });
          res.write("data: [DONE]\n\n");
          runStates.set(runId, "done");
          res.end();
          return;
        }
        send({ choices: [{ delta: { content: chars[i] } }] });
        i++;
        setTimeout(tick, 40);
      };
      setTimeout(tick, 100);
    };

    if (needsApproval) {
      runStates.set(runId, "waiting_approval");
      send({
        type: "approval.request",
        run_id: runId,
        command: "rm -rf /workspace/cache",
        description: "智能体请求删除缓存目录（需人工确认）",
        choices: ["once", "session", "always", "deny"]
      });
      // 等待 POST /v1/chat/approval（15s 超时自动 deny）
      const wait = new Promise<string>((resolve) => {
        pendingApprovals.set(runId, resolve);
        setTimeout(() => resolve("deny"), 60000);
      });
      wait.then((choice) => {
        pendingApprovals.delete(runId);
        if (res.writableEnded) return;
        send({ type: "approval.responded", run_id: runId, choice });
        if (choice === "deny") {
          send({ choices: [{ delta: { content: "已拒绝该操作。" }, finish_reason: "stop" }] });
          res.write("data: [DONE]\n\n");
          runStates.set(runId, "done");
          res.end();
        } else {
          streamContent();
        }
      });
    } else {
      streamContent();
    }
  });
  req.on("close", () => {});
}

export function mockApiPlugin(env: Record<string, string>): Plugin {
  const enabled = env.VITE_USE_MOCK === "true";
  return {
    name: "unionagents-enduser-mock",
    configureServer(server) {
      if (!enabled) return;
      server.middlewares.use((req, res, next) => {
        const url = (req.url || "").split("?")[0];
        const method = (req.method || "GET").toUpperCase();
        for (const r of routes) {
          if (r.method !== method) continue;
          const m = url.match(r.pattern);
          if (m) {
            Promise.resolve(r.handler(req, res, m)).catch(err => {
              if (!res.writableEnded) json(res, { detail: String(err) }, 500);
            });
            return;
          }
        }
        next();
      });
    }
  };
}
