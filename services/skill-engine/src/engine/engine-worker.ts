/**
 * engine-worker.ts — Child process running a single engine SDK session.
 *
 * Communication protocol: stdin/stdout JSONL.
 * - Receives commands: { type: "prompt"|"steer"|"follow_up"|"abort"|"get_state"|"reload"|"tool_response", id, message?, toolCallId?, answers? }
 * - Sends responses:  { type: "response", id, command, success, data?, error? }
 * - Sends events:     { type: "event", eventType, data }  (from SDK subscribe)
 * - Sends status:     { type: "status", status: "initializing"|"ready"|"error"|"shutting_down" }
 * - Sends heartbeat:  { type: "heartbeat", ts }
 *
 * The SDK is imported dynamically so tests can mock it without loading
 * the real `@earendil-works/pi-coding-agent` package.
 *
 * CLI arguments:
 *   --cwd <path>          Working directory
 *   --role <dev|debug>    Instance role (default: dev)
 *   --tools <csv>         Allowed tool names
 *   --exclude <csv>       Excluded tool names
 *   --sdk-module <path>   SDK module override (for testing)
 */

import { parseArgs } from "node:util";
import { createInterface } from "node:readline";
import {
  writeFileSync,
  readFileSync,
  mkdirSync,
  cpSync,
  existsSync,
  readdirSync,
  lstatSync,
  symlinkSync,
  rmSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  createClarifyTool,
  resolveClarify,
  type PendingClarifyMap,
  type ClarifyAnswers,
} from "./clarify-tool.js";

// ── Parse CLI arguments ────────────────────────────────────────

const { values } = parseArgs({
  options: {
    cwd: { type: "string" },
    role: { type: "string", default: "dev" },
    tools: { type: "string" },
    exclude: { type: "string" },
    "sdk-module": { type: "string" },
  },
  strict: true,
});

const cwd = values.cwd ?? process.cwd();
const role = values.role;
const tools = values.tools ? values.tools.split(",") : undefined;
const excludeTools = values.exclude ? values.exclude.split(",") : undefined;
const sdkModule = values["sdk-module"] ?? "@earendil-works/pi-coding-agent";

// ── JSONL output helper ────────────────────────────────────────

function send(obj: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

// ── User skill sync (debug role) ───────────────────────────────

/**
 * For the debug role, mirror the workspace root SKILL.md into
 * `.pi/skills/user-skill/SKILL.md` so pi's resource loader picks it up as an
 * invocable skill. Called on init (before session creation) and on reload so
 * edits to the root SKILL.md take effect without a full instance restart.
 *
 * 镜像前按非密钥 config 替换 `${config.param}`（对齐生产 fanout 替换）——configValues
 * 从 skill-engine 自身 HTTP 取（worker 与 skill-engine 同 Pod，localhost:8004）。
 * 密钥不替换（运行时经 /secret 取，见 config-routes）。
 */
async function syncUserSkill(): Promise<void> {
  if (role !== "debug") return;
  const src = join(cwd, "SKILL.md");
  let content = "";
  try {
    content = readFileSync(src, "utf-8");
  } catch {
    // No SKILL.md at workspace root — nothing to sync.
    return;
  }

  // 拉非密钥 config 值（best-effort：失败则不替换，原样镜像）
  const configValues = await fetchConfigValues();
  if (configValues && Object.keys(configValues).length) {
    content = substituteSkillMd(content, configValues);
  }

  try {
    const destDir = join(cwd, ".pi", "skills", "user-skill");
    mkdirSync(destDir, { recursive: true });
    writeFileSync(join(destDir, "SKILL.md"), content, "utf-8");
    // 把工作区根的 scripts/、references/、assets/、output/ 等用软链接挂进技能目录，
    // 让 .pi/skills/user-skill/ 自包含（对齐生产技能目录结构）。否则 debug agent
    // 加载技能后在该目录找不到 scripts/，误报「脚本不存在」——实际 scripts/ 在工作区根
    // （bash cwd）。SKILL.md 是替换后的副本，不软链。
    syncSkillSideFiles(cwd, destDir);
  } catch (err) {
    // Best-effort; reload will still proceed.
    send({ type: "error", error: `skill sync failed: ${(err as Error).message}` });
  }
}

/**
 * 把工作区根目录下的子项（scripts/、references/、assets/、output/、manifest.json 等）
 * 以相对软链接形式挂到 .pi/skills/user-skill/ 下，使技能目录自包含。
 * 跳过 .pi（避免递归）和 SKILL.md（用替换后的副本）。每次 sync 先清旧链接再重建，
 * 处理用户增删文件的情况。
 */
function syncSkillSideFiles(workspaceRoot: string, skillDir: string): void {
  let entries: string[] = [];
  try {
    entries = readdirSync(workspaceRoot);
  } catch {
    return;
  }
  for (const name of entries) {
    if (name === ".pi" || name === "SKILL.md") continue;
    const src = join(workspaceRoot, name);
    const dest = join(skillDir, name);
    try {
      // 清旧（软链接 / 残留文件 / 残留目录）
      if (existsSync(dest) || isSymlink(dest)) {
        rmSync(dest, { force: true, recursive: true });
      }
      // 相对链接：skillDir = <root>/.pi/skills/user-skill → 上 3 级到 root
      symlinkSync(`../../../${name}`, dest, lstatSync(src).isDirectory() ? "dir" : "file");
    } catch {
      // 单项失败不阻断（可能并发写）；下次 sync 重试
    }
  }
}

function isSymlink(p: string): boolean {
  try {
    return lstatSync(p).isSymbolicLink();
  } catch {
    return false;
  }
}

/**
 * 从 skill-engine HTTP 取工作区非密钥 config 值（worker 同 Pod，localhost:8004）。
 * 返回 null 表示取不到（fetch 失败 / 无 workspaceId）——调用方原样镜像不替换。
 */
async function fetchConfigValues(): Promise<Record<string, unknown> | null> {
  const wsId = process.env.SKILL_WORKSPACE_ID;
  if (!wsId) return null;
  try {
    const res = await fetch(
      `http://localhost:8004/api/skill-engine/workspaces/${wsId}/config`,
      { signal: AbortSignal.timeout(3000) },
    );
    if (!res.ok) return null;
    const body = (await res.json()) as { configValues?: Record<string, unknown> };
    return body.configValues ?? {};
  } catch {
    return null;
  }
}

/**
 * 替换 SKILL.md body 里的 `${config.param}` 为非密钥 config 值（镜像生产
 * _substitute_skill_md_body）。仅替换 body（跳过 frontmatter），bool → "True"/"False"
 * （Python 风格，因 ${config.x} 常出现在 execute_code 的 python 块里）。未知 token 保留。
 */
function substituteSkillMd(
  content: string,
  configValues: Record<string, unknown>,
): string {
  // 分离 frontmatter（首段 --- 之间）不替换，只替换 body
  const parts = content.split(/^---\s*$/m, 3);
  if (parts.length < 3) {
    return replaceConfigTokens(content, configValues);
  }
  // parts[0]=前导, parts[1]=frontmatter, parts[2]=body
  return `${parts[0]}---\n${parts[1]}---\n${replaceConfigTokens(parts[2], configValues)}`;
}

function replaceConfigTokens(
  text: string,
  configValues: Record<string, unknown>,
): string {
  return text.replace(/\$\{config\.([a-zA-Z_]\w*)\s*\}/g, (match, name: string) => {
    if (!(name in configValues)) return match; // 未知 token 保留
    const v = configValues[name];
    if (typeof v === "boolean") return v ? "True" : "False"; // Python 风格
    return String(v);
  });
}

/**
 * For the dev role, re-sync the preinstalled skill-creator from the image into
 * `.pi/skills/skill-creator/` on every spawn/reload. This ensures the latest
 * preset guidance (e.g. language directive, clarify usage) takes effect for
 * existing workspaces without recreating them. Best-effort: if the bundled
 * preset is absent (local non-container run), skip silently.
 */
function syncPreinstalledSkill(): void {
  if (role !== "dev") return;
  const srcDir = process.env.PREINSTALLED_SKILLS_DIR ?? "/app/preinstalled-skills";
  const src = join(srcDir, "skill-creator");
  const dest = join(cwd, ".pi", "skills", "skill-creator");
  try {
    if (!existsSync(src)) return;
    cpSync(src, dest, { recursive: true });
  } catch (err) {
    send({
      type: "error",
      error: `preset skill sync failed: ${(err as Error).message}`,
    });
  }
}

/**
 * Read the role-specific persona + scope guardrail bundled with the
 * preinstalled skill-creator: dev → APPEND_SYSTEM.md，debug → APPEND_SYSTEM_DEBUG.md.
 * Returns "" if the file is absent (e.g. local non-container run without the
 * bundled preset). Injected in-memory as an appended system prompt (see
 * initSession) — dev 限定只做技能开发、debug 限定只通过 user-skill 行动。
 */
function readRolePersona(roleParam: string): string {
  const srcDir = process.env.PREINSTALLED_SKILLS_DIR ?? "/app/preinstalled-skills";
  const file = roleParam === "debug" ? "APPEND_SYSTEM_DEBUG.md" : "APPEND_SYSTEM.md";
  const src = join(srcDir, "skill-creator", file);
  try {
    if (!existsSync(src)) return "";
    return readFileSync(src, "utf-8");
  } catch {
    return "";
  }
}

// ── SDK session ────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let session: any = null;

// ── clarify 工具的 pending 通道 ─────────────────────────────────
// toolCallId → 阻塞中的 clarify execute() 的 resolver。用户提交答案后
// tool_response 命令经 resolveClarify() 在此查表并 resolve。
const pendingClarify: PendingClarifyMap = new Map();

async function initSession(): Promise<void> {
  send({ type: "status", status: "initializing", role, cwd });

  try {
    // Dynamic import — allows mocking via --sdk-module
    const sdk = await import(sdkModule);
    const sessionManager = sdk.SessionManager.inMemory(cwd);

    // ── LLM provider/model wiring (Phase 5) ──────────────────────
    // When LITELLM_API_KEY is set, configure a custom OpenAI-compatible
    // provider pointing at LiteLLM via a runtime-generated models.json.
    // The key lives only in the ephemeral models.json (written from env),
    // never in auth.json. Falls back to the SDK default runtime otherwise.
    const litellmKey = process.env.LITELLM_API_KEY;
    const litellmBase = process.env.LITELLM_BASE_URL;
    const modelName = process.env.SKILL_ENGINE_MODEL || "deepseek-chat";
    const providerId = process.env.SKILL_ENGINE_PROVIDER || "litellm";

    let createOptions: Record<string, unknown> = {
      cwd,
      sessionManager,
      tools,
      excludeTools,
      // clarify：需求澄清/二次确认的结构化问卷工具（human-in-the-loop，
      // execute 阻塞等用户提交）。是否对 agent 可用由 tools allowlist 控制
      //（devBootstrap 含 "clarify"，debugBootstrap 不含）。
      customTools: [createClarifyTool(pendingClarify)],
    };

    if (litellmKey && litellmBase) {
      // models.json with a custom OpenAI-compatible provider pointing at
      // LiteLLM. The API key is interpolated from env at runtime ("$VAR"),
      // so the secret never lands in auth.json or the image. compat disables
      // developer-role/reasoning-effort (LiteLLM-backed chat models may not
      // support them, per pi's models.md guidance for OpenAI-compatible servers).
      const modelsConfig = {
        providers: {
          [providerId]: {
            name: "LiteLLM",
            baseUrl: litellmBase,
            api: "openai-completions",
            apiKey: "$LITELLM_API_KEY",
            compat: {
              supportsDeveloperRole: false,
              supportsReasoningEffort: false,
            },
            models: [{ id: modelName, name: modelName }],
          },
        },
      };
      // Write to a per-process temp file (not the workspace, not auth.json).
      const modelsPath = join(tmpdir(), `pi-models-${process.pid}.json`);
      writeFileSync(modelsPath, JSON.stringify(modelsConfig), "utf-8");

      const modelRuntime = await sdk.ModelRuntime.create({ modelsPath });
      const model = modelRuntime.getModel(providerId, modelName);
      if (!model) {
        throw new Error(
          `Model ${providerId}/${modelName} not found in runtime catalog`,
        );
      }
      createOptions = { ...createOptions, model, modelRuntime };
    }

    // Debug role: mirror root SKILL.md into .pi/skills/ so pi loads it.
    await syncUserSkill();
    // Dev role: refresh the preinstalled skill-creator so existing workspaces
    // pick up the latest preset guidance.
    syncPreinstalledSkill();

    // Inject the role-specific persona + scope guardrail as an appended system
    // prompt (dev: 只做技能开发；debug: 只通过 user-skill 行动). Passed in-memory
    // via a custom DefaultResourceLoader (appendSystemPrompt) rather than a
    // shared `.pi/APPEND_SYSTEM.md` file — the workspace cwd is shared between
    // dev and debug sessions, so a file-based approach would race (each session
    // could pick up the wrong role's persona). In-memory injection is
    // per-session and race-free.
    const personaText = readRolePersona(role);
    if (personaText && sdk.DefaultResourceLoader) {
      try {
        const loader = new sdk.DefaultResourceLoader({
          cwd,
          agentDir: sdk.getAgentDir(),
          appendSystemPrompt: [personaText],
        });
        await loader.reload();
        createOptions.resourceLoader = loader;
      } catch (err) {
        // 非致命：降级为默认 loader（无人设追加），会话仍可继续。
        send({
          type: "error",
          error: `persona loader init failed: ${(err as Error).message}`,
        });
      }
    }

    const result = await sdk.createAgentSession(createOptions);

    session = result.session;

    // Subscribe to all SDK events and forward to stdout
    session.subscribe((event: { type: string; [key: string]: unknown }) => {
      send({ type: "event", eventType: event.type, data: event });
    });

    send({ type: "status", status: "ready", role });
  } catch (err) {
    const error = err as Error;
    send({
      type: "status",
      status: "error",
      error: error.message,
    });
    process.exit(1);
  }
}

// ── Command handling ───────────────────────────────────────────

async function handleCommand(cmd: {
  type: string;
  id?: string;
  message?: string;
  toolCallId?: string;
  answers?: Record<string, unknown>;
}): Promise<void> {
  const { type, id } = cmd;

  try {
    switch (type) {
      case "prompt": {
        await session.prompt(cmd.message);
        send({
          type: "response",
          id,
          command: "prompt",
          success: true,
        });
        break;
      }
      case "steer": {
        await session.steer(cmd.message);
        send({
          type: "response",
          id,
          command: "steer",
          success: true,
        });
        break;
      }
      case "follow_up": {
        await session.followUp(cmd.message);
        send({
          type: "response",
          id,
          command: "follow_up",
          success: true,
        });
        break;
      }
      case "abort": {
        await session.abort();
        send({
          type: "response",
          id,
          command: "abort",
          success: true,
        });
        break;
      }
      case "reload": {
        // Re-mirror root SKILL.md into .pi/skills/ (picks up edits) then ask
        // the SDK session to reload resources — re-reads skills without a
        // full subprocess restart.
        try {
          await syncUserSkill();
          syncPreinstalledSkill();
          await session.reload?.();
          send({ type: "response", id, command: "reload", success: true });
        } catch (err) {
          send({
            type: "response",
            id,
            command: "reload",
            success: false,
            error: (err as Error).message,
          });
        }
        break;
      }
      case "tool_response": {
        // 用户通过 UI 表单提交了 clarify 答案 → 解析阻塞中的 execute()。
        const { toolCallId, answers } = cmd;
        if (!toolCallId) {
          send({
            type: "response",
            id,
            command: "tool_response",
            success: false,
            error: "missing toolCallId",
          });
          break;
        }
        const resolved = resolveClarify(
          pendingClarify,
          toolCallId,
          (answers ?? {}) as ClarifyAnswers,
        );
        send({
          type: "response",
          id,
          command: "tool_response",
          success: resolved,
          error: resolved ? undefined : "no pending clarify for this toolCallId",
        });
        break;
      }
      case "get_state": {
        const state = session.agent?.state;
        send({
          type: "response",
          id,
          command: "get_state",
          success: true,
          data: {
            isStreaming: state?.isStreaming ?? false,
            model: state?.model
              ? { provider: state.model.provider, modelId: state.model.modelId }
              : null,
            messageCount: state?.messages?.length ?? 0,
          },
        });
        break;
      }
      default:
        send({
          type: "response",
          id,
          command: type,
          success: false,
          error: `Unknown command: ${type}`,
        });
    }
  } catch (err) {
    const error = err as Error;
    send({
      type: "response",
      id,
      command: type,
      success: false,
      error: error.message,
    });
  }
}

// ── Heartbeat (every 30s) ──────────────────────────────────────

const heartbeat = setInterval(() => {
  send({ type: "heartbeat", ts: Date.now() });
}, 30_000);

// ── Shutdown handler ───────────────────────────────────────────

async function shutdown(): Promise<void> {
  send({ type: "status", status: "shutting_down" });
  clearInterval(heartbeat);
  if (session) {
    try {
      await session.dispose();
    } catch {
      // Best-effort cleanup
    }
  }
  process.exit(0);
}

// ── stdin reader ───────────────────────────────────────────────

const rl = createInterface({ input: process.stdin });

rl.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    const cmd = JSON.parse(line);
    await handleCommand(cmd);
  } catch (err) {
    const error = err as Error;
    send({ type: "error", error: `Parse error: ${error.message}` });
  }
});

rl.on("close", async () => {
  await shutdown();
});

// ── Signal handling ────────────────────────────────────────────

process.on("SIGTERM", async () => {
  await shutdown();
});

process.on("SIGINT", async () => {
  await shutdown();
});

// ── Start ──────────────────────────────────────────────────────

initSession();
