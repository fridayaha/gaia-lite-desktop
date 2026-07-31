/**
 * config_params routes — skill-studio 开发期密钥/配置。
 *
 * 两类端点：
 *  ① 管理面（/api/skill-engine 前缀，经 manager 代理，extractAuthContext 校验）：
 *     GET/PUT /api/skill-engine/workspaces/:id/config —— 读/写 config_params 值。
 *     密钥（secret:true）加密存 workspaces.credentials_encrypted；非密钥明文存 workspaces.config。
 *  ② 运行时（裸 /secret，无前缀，无 auth——靠 Pod localhost 隔离，对齐生产 sidecar）：
 *     GET /secret?skill=&key= —— 技能脚本借 bash 调用，解密返回明文。
 *
 * 技能代码 `http://localhost:8004/secret?skill=<name>&key=<param>` 与生产 sidecar
 * 契约一致，skill-studio debug 和生产零改动可移植。
 */

import { FastifyInstance } from "fastify";

import { encryptCredentialsDict, decryptCredentialsDict } from "../utils/crypto.js";
import { ValidationError } from "../utils/errors.js";

type ConfigParam = {
  name: string;
  label?: string;
  type?: string;
  secret?: boolean;
  options?: string[];
};

/** 读取工作区 manifest.json 的 { name, config_params }。manifest 缺失返回 null。 */
function readManifest(
  fileStore: import("../workspace/file-store.js").FileStore,
  workspaceId: string,
): { name?: string; config_params?: ConfigParam[] } | null {
  try {
    const res = fileStore.readFile(workspaceId, "manifest.json");
    const raw = typeof res.content === "string" ? res.content : "";
    if (!raw) return null;
    return JSON.parse(raw) as { name?: string; config_params?: ConfigParam[] };
  } catch {
    return null;
  }
}

/** 非密钥 config 值类型校验（对齐生产 _validate_param_value 思路）。 */
function validateNonSecretValue(param: ConfigParam, value: unknown): unknown {
  switch (param.type) {
    case "number": {
      const n = Number(value);
      if (Number.isNaN(n)) throw new ValidationError(`${param.name} 须为数字`);
      return n;
    }
    case "boolean": {
      if (typeof value === "boolean") return value;
      if (value === "true") return true;
      if (value === "false") return false;
      throw new ValidationError(`${param.name} 须为 boolean`);
    }
    case "select": {
      const s = String(value);
      if (!param.options?.includes(s)) {
        throw new ValidationError(`${param.name} 须为选项之一: ${param.options?.join("/") ?? ""}`);
      }
      return s;
    }
    case "string":
    default: {
      const s = String(value);
      if (s.length > 2000) throw new ValidationError(`${param.name} 过长（>2000）`);
      return s;
    }
  }
}

export async function configRoutes(app: FastifyInstance): Promise<void> {
  // ── ① 管理面：GET config（密钥不返明文，仅返已配置 key 列表）──
  app.get<{
    Params: { id: string };
  }>("/api/skill-engine/workspaces/:id/config", async (request) => {
    const { id } = request.params;
    // 确认 workspace 存在（404 via store）
    await app.workspaceStore.get(id);

    const { config: configValues, credentialsEncrypted } = await app.workspaceStore.getConfig(id);
    let configured: string[] = [];
    try {
      configured = Object.keys(decryptCredentialsDict(credentialsEncrypted ?? ""));
    } catch {
      // 解密失败（key 漂移）：当作未配置
      configured = [];
    }
    return { configValues, configured };
  });

  // ── ① 管理面：PUT config（保存密钥 + 非密钥）──
  app.put<{
    Params: { id: string };
    Body: { config?: Record<string, unknown>; credentials?: Record<string, string> };
  }>("/api/skill-engine/workspaces/:id/config", async (request) => {
    const { id } = request.params;
    const { config: configInput = {}, credentials: credInput = {} } = request.body ?? {};

    // 确认 workspace 存在
    await app.workspaceStore.get(id);

    // 读 manifest 拿 config_params 声明 + skill name
    const manifest = readManifest(app.fileStore, id);
    const params = manifest?.config_params ?? [];
    const secretNames = new Set(params.filter((p) => p.secret === true).map((p) => p.name));
    const nonSecretParams = new Map(params.filter((p) => p.secret !== true).map((p) => [p.name, p]));
    const skillName = manifest?.name ?? "";

    // 校验：credentials 的 key 必须声明 secret:true；config 的 key 必须非 secret
    for (const k of Object.keys(credInput)) {
      if (!secretNames.has(k)) {
        throw new ValidationError(`密钥 ${k} 未在 manifest 声明为 secret:true`);
      }
    }
    for (const k of Object.keys(configInput)) {
      if (secretNames.has(k)) {
        throw new ValidationError(`${k} 是密钥，请走 credentials`);
      }
      if (!nonSecretParams.has(k)) {
        throw new ValidationError(`配置项 ${k} 未在 manifest 声明`);
      }
    }

    // 非密钥：类型校验 + 整列覆盖
    const configNorm: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(configInput)) {
      const param = nonSecretParams.get(k)!;
      configNorm[k] = validateNonSecretValue(param, v);
    }

    // 密钥：空值跳过（"不改"语义），与已有合并后加密整列覆盖
    const { credentialsEncrypted: existingEnc } = await app.workspaceStore.getConfig(id);
    let existingCreds: Record<string, string> = {};
    try {
      existingCreds = decryptCredentialsDict(existingEnc ?? "");
    } catch {
      existingCreds = {};
    }
    for (const [k, v] of Object.entries(credInput)) {
      if (!v) continue; // 空值 = 不改
      existingCreds[k] = v;
    }
    const credentialsEncrypted = Object.keys(existingCreds).length
      ? encryptCredentialsDict(existingCreds)
      : null;

    const configChanged = Object.keys(configNorm).length > 0;

    // 写回 workspaces 行（config / credentials_encrypted / skill_name 缓存）
    await app.workspaceStore.saveConfig(id, {
      ...(configChanged ? { config: configNorm } : {}),
      ...(credentialsEncrypted ? { credentialsEncrypted } : {}),
      ...(skillName ? { skillName } : {}),
    });

    // 非密钥 config 变更 → 触发 debug 会话 reload（重新镜像 + 替换 SKILL.md 让新值生效）
    if (configChanged) {
      try {
        await app.instanceManager.reload(`${id}:debug`);
      } catch {
        // debug 未运行：下次 spawn 时 syncUserSkill 自然带上新值，忽略
      }
    }

    return {
      ok: true,
      configured: Object.keys(existingCreds),
      configValues: configNorm,
    };
  });

  // ── ② 运行时：GET /secret?skill=&key=（裸路径，不进 manager 代理，仅 Pod 内可达）──
  // 对齐生产 sidecar 契约：技能脚本 http://localhost:8004/secret?skill=<name>&key=<param>
  app.get<{
    Querystring: { skill?: string; key?: string };
  }>("/secret", async (request, reply) => {
    const skill = (request.query.skill ?? "").trim();
    const key = (request.query.key ?? "").trim();
    if (!skill || !key) {
      throw new ValidationError("skill and key are required");
    }

    // 按 skill_name 查 workspaces（多行取最近 updated，对齐生产 sidecar first-match）
    const row = await app.workspaceStore.findBySkillName(skill);
    if (!row || !row.credentialsEncrypted) {
      reply.status(404);
      return { error: `no secrets for skill ${skill}` };
    }

    let creds: Record<string, string>;
    try {
      creds = decryptCredentialsDict(row.credentialsEncrypted);
    } catch (err) {
      reply.status(500);
      return { error: `decrypt failed: ${(err as Error).message}` };
    }

    if (!(key in creds)) {
      reply.status(404);
      return { error: `secret ${key} not configured for skill ${skill}` };
    }

    return { value: creds[key] };
  });
}
