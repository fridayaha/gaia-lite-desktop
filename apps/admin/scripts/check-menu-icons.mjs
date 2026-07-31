/**
 * 校验路由模块中 `icon: "ri:xxx"` / `"ep:xxx"` / `"bi:xxx"` 等菜单图标名
 * 是否在对应 @iconify-json/<set> 图标集中真实存在。
 *
 * 背景：commit 51783bc 修过一次「ri:gateway-line 不存在导致菜单图标空白」。
 * 此脚本在 CI / 本地构建前运行，防止无效图标名再次混入（运行时 useRenderIcon
 * 对无效名静默渲染空白，不会报错）。
 *
 * 用法：node scripts/check-menu-icons.mjs
 * 退出码：0 全部有效，1 存在无效图标名。
 */
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(import.meta.dirname, "..");
const ROUTER_DIR = path.join(ROOT, "src", "router", "modules");

// 支持 prefix → @iconify-json 包名映射；新增图标集在此补充
const SUPPORTED_PREFIXES = ["ri", "ep", "bi", "lucide"];

// 缓存每个图标集的有效名集合（icons + aliases）
const setCache = new Map();
function getIconNames(prefix) {
  if (setCache.has(prefix)) return setCache.get(prefix);
  const pkg = `@iconify-json/${prefix}/icons.json`;
  let data;
  try {
    data = require(pkg);
  } catch {
    // 图标集未安装：跳过该 prefix（不误报）
    setCache.set(prefix, null);
    return null;
  }
  const names = new Set([
    ...Object.keys(data.icons || {}),
    ...Object.keys(data.aliases || {}),
  ]);
  setCache.set(prefix, names);
  return names;
}

// 匹配 "prefix:name" 形式的图标字符串（prefix 限白名单，避免误匹配普通字符串）
const ICON_STRING_RE = new RegExp(
  `["'](${SUPPORTED_PREFIXES.join("|")}):([a-z0-9-]+)["']`,
  "g"
);

async function* walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) yield* walk(full);
    else if (/\.(ts|tsx|vue)$/.test(e.name)) yield full;
  }
}

async function main() {
  const files = [];
  if (existsSync(ROUTER_DIR)) {
    for await (const f of walk(ROUTER_DIR)) files.push(f);
  }
  // 同时扫描 views 下以 "prefix:name" 字符串形式用作图标的用法（兜底）
  const viewsDir = path.join(ROOT, "src", "views");
  if (existsSync(viewsDir)) {
    for await (const f of walk(viewsDir)) files.push(f);
  }

  const invalid = [];
  let checked = 0;
  for (const file of files) {
    const src = await readFile(file, "utf8");
    let m;
    ICON_STRING_RE.lastIndex = 0;
    while ((m = ICON_STRING_RE.exec(src)) !== null) {
      const prefix = m[1];
      const name = m[2];
      const names = getIconNames(prefix);
      if (names === null) continue; // 图标集未安装，跳过
      checked++;
      if (!names.has(name)) {
        const rel = path.relative(ROOT, file);
        invalid.push(`${rel}: "${prefix}:${name}" 不存在于 @iconify-json/${prefix}`);
      }
    }
  }

  if (invalid.length) {
    console.error(`❌ 发现 ${invalid.length} 个无效菜单图标名（共校验 ${checked} 处）：`);
    for (const line of invalid) console.error("  - " + line);
    console.error("\n请改用对应图标集中存在的图标名。参考 https://remixicon.com / https://icon-sets.iconify.design");
    process.exit(1);
  }
  console.log(`✅ 菜单图标名校验通过（共 ${checked} 处）。`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
