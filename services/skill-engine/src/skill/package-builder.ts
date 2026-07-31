/**
 * package-builder.ts — 技能校验 + 打包。
 *
 * validateWorkspace：校验 SKILL.md frontmatter（name/description/version/author）
 *   与 manifest.json（合法 JSON，必含 name/version/author/description/engine、
 *   type==="skill"），返回 {valid, errors[]}。纯函数，便于单测。
 * buildPackageZip：把工作区文件（listFiles 已排除 .pi/node_modules/hidden）
 *   打成 zip Buffer，供下载与发布到 Hub 复用。
 */

import { readFileSync } from "node:fs";
import AdmZip from "adm-zip";
import type { FileStore } from "../workspace/file-store.js";

export interface ValidationError {
  field: string;
  message: string;
}

export interface ValidateResult {
  valid: boolean;
  errors: ValidationError[];
  /** 解析出的 manifest（合法时），供调用方取 name/version 派生包名。 */
  manifest?: Record<string, unknown>;
}

// SKILL.md frontmatter 只强制 name/description（触发必需，对齐 skill-creator 教学与
// Anthropic 技能约定）。version/author 由 manifest.json 承担（MANIFEST_REQUIRED），
// manager _parse_zip 也是 manifest 主、frontmatter 兜底，不要求 frontmatter 含 version/author。
const SKILL_FRONTMATTER_REQUIRED = ["name", "description"];
const MANIFEST_REQUIRED = [
  "name",
  "version",
  "author",
  "description",
  "engine",
  "type",
];
/** manifest.engine 接受的小写字符串枚举（canonical 形态）。 */
const ENGINE_ENUM = ["hermes", "openclaw"];
/** manifest.config_params[].type 接受的枚举。 */
const PARAM_TYPE_ENUM = ["string", "number", "boolean", "select"];

/** 解析 SKILL.md 顶部 YAML frontmatter（--- ... ---）。 */
function parseFrontmatter(raw: string): Record<string, string> {
  const m = raw.match(/^---\s*([\s\S]*?)\s*---/);
  if (!m) return {};
  const out: Record<string, string> = {};
  for (const line of m[1].split("\n")) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
    if (k) out[k] = v;
  }
  return out;
}

/**
 * 校验工作区的 SKILL.md + manifest.json。
 * 缺文件/格式错都记进 errors，不抛异常。
 */
export function validateWorkspace(
  fileStore: FileStore,
  workspaceId: string,
): ValidateResult {
  const errors: ValidationError[] = [];
  let manifest: Record<string, unknown> | undefined;

  // ── SKILL.md ──
  let skillContent = "";
  try {
    const res = fileStore.readFile(workspaceId, "SKILL.md");
    skillContent = typeof res.content === "string" ? res.content : "";
  } catch {
    errors.push({ field: "SKILL.md", message: "缺少 SKILL.md" });
  }
  if (skillContent) {
    const fm = parseFrontmatter(skillContent);
    if (Object.keys(fm).length === 0) {
      errors.push({ field: "SKILL.md", message: "缺少 YAML frontmatter（--- ... ---）" });
    } else {
      for (const k of SKILL_FRONTMATTER_REQUIRED) {
        if (!fm[k]) {
          errors.push({ field: `SKILL.md:${k}`, message: `frontmatter 缺少 ${k}` });
        }
      }
    }
  }

  // ── manifest.json ──
  let manifestRaw = "";
  try {
    const res = fileStore.readFile(workspaceId, "manifest.json");
    manifestRaw = typeof res.content === "string" ? res.content : "";
  } catch {
    errors.push({ field: "manifest.json", message: "缺少 manifest.json" });
  }
  if (manifestRaw) {
    try {
      const parsed = JSON.parse(manifestRaw) as Record<string, unknown>;
      manifest = parsed;
      for (const k of MANIFEST_REQUIRED) {
        if (!(k in parsed)) {
          errors.push({ field: `manifest.json:${k}`, message: `manifest 缺少 ${k}` });
        }
      }
      if (parsed.type && parsed.type !== "skill") {
        errors.push({
          field: "manifest.json:type",
          message: `manifest type 必须为 skill（当前 ${String(parsed.type)}）`,
        });
      }
      // engine 枚举校验（canonical 小写字符串）
      if (parsed.engine && !ENGINE_ENUM.includes(String(parsed.engine))) {
        errors.push({
          field: "manifest.json:engine",
          message: `manifest engine 必须为 ${ENGINE_ENUM.join(" / ")}（当前 ${String(parsed.engine)}）`,
        });
      }
      // config_params 结构校验
      validateConfigParams(parsed, errors);
    } catch (err) {
      errors.push({
        field: "manifest.json",
        message: `manifest.json 不是合法 JSON：${(err as Error).message}`,
      });
    }
  }

  return { valid: errors.length === 0, errors, manifest };
}

/**
 * 校验 manifest.config_params 结构（存在时）。
 * - 必须为数组
 * - 每项必含 name（非空字符串）/ label / type；type ∈ PARAM_TYPE_ENUM
 * - secret 若存在必须为 boolean
 * - select 类型必须有非空 options 数组
 * - name 不允许重复
 * 错误记入 errors，不抛异常。
 */
function validateConfigParams(
  manifest: Record<string, unknown>,
  errors: ValidationError[],
): void {
  if (!("config_params" in manifest)) return;
  const params = manifest.config_params;
  if (!Array.isArray(params)) {
    errors.push({
      field: "manifest.json:config_params",
      message: "config_params 必须为数组",
    });
    return;
  }
  const seen = new Set<string>();
  params.forEach((p, i) => {
    if (typeof p !== "object" || p === null) {
      errors.push({
        field: `manifest.json:config_params[${i}]`,
        message: `config_params[${i}] 必须为对象`,
      });
      return;
    }
    const item = p as Record<string, unknown>;
    const loc = `manifest.json:config_params[${i}]`;
    if (typeof item.name !== "string" || !item.name.trim()) {
      errors.push({ field: `${loc}.name`, message: `config_params[${i}] 缺少 name` });
    } else if (seen.has(item.name)) {
      errors.push({
        field: `${loc}.name`,
        message: `config_params name 重复：${item.name}`,
      });
    } else {
      seen.add(item.name);
    }
    if (typeof item.label !== "string" || !item.label.trim()) {
      errors.push({ field: `${loc}.label`, message: `config_params[${i}] 缺少 label` });
    }
    if (typeof item.type !== "string" || !PARAM_TYPE_ENUM.includes(item.type)) {
      errors.push({
        field: `${loc}.type`,
        message: `config_params[${i}] type 必须为 ${PARAM_TYPE_ENUM.join(" / ")}（当前 ${String(item.type)}）`,
      });
    }
    if ("secret" in item && typeof item.secret !== "boolean") {
      errors.push({
        field: `${loc}.secret`,
        message: `config_params[${i}] secret 必须为 boolean`,
      });
    }
    if (item.type === "select") {
      if (!Array.isArray(item.options) || item.options.length === 0) {
        errors.push({
          field: `${loc}.options`,
          message: `config_params[${i}] type=select 必须提供非空 options 数组`,
        });
      }
    }
  });
}

/**
 * 把工作区打成 zip Buffer。仅包含 listFiles 返回的文件（已排除 .pi、
 * node_modules、hidden），目录条目跳过。复用 fileStore.safeResolve 安全解析路径。
 */
export function buildPackageZip(
  fileStore: FileStore,
  workspaceId: string,
): Buffer {
  const zip = new AdmZip();
  const entries = fileStore.listFiles(workspaceId);
  for (const e of entries) {
    if (e.isDir) continue;
    const abs = fileStore.safeResolve(workspaceId, e.path);
    let bytes: Buffer;
    try {
      bytes = readFileSync(abs);
    } catch {
      continue; // 单文件读失败不阻断整包
    }
    zip.addFile(e.path, bytes);
  }
  return zip.toBuffer();
}
