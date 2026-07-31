/**
 * UnionAgents admin 截图脚本 — 登录 ECS admin 门户后遍历所有页面截图。
 *
 * 用法：node scripts/capture-screenshots.mjs [输出目录]
 *
 * 登录方式：直接调 /api/manager/auth/login API 拿 token，再 set cookie + localStorage，
 * 绕过 UI 登录表单的图形验证码（ReImageVerify）。
 *
 * 关键点 1：SPA 守卫异步加载动态路由，第一次 goto 非首页路由会 fallback 到 /welcome。
 *   修复：goto /welcome 加载完动态路由后再开始遍历。
 *
 * 关键点 2：page.goto 触发全页刷新，会重置 SPA 状态，后续 goto 到新路由时
 *   URL 改变但组件不切换（仍渲染 welcome dashboard）。
 *   修复：首次 goto /welcome 加载完动态路由后，后续用 router.push（通过
 *   app.config.globalProperties.$router）做 SPA 内导航，避免全页刷新。
 *
 * 关键点 3：截图只截主内容区（.app-main），排除 navbar + 侧边栏。
 *
 * 输出：apps/docs/content/screenshots/<page>.png
 */
import { chromium, request } from "playwright";
import path from "path";
import fs from "fs";

const BASE = "http://190.92.230.115:30080";
const OUT_DIR = path.resolve(process.argv[2] || "./apps/docs/content/screenshots");
fs.mkdirSync(OUT_DIR, { recursive: true });

// 按侧边栏顺序的全量页面清单（name → path + 内容验证选择器）
const PAGES = [
  { name: "welcome",                 path: "/welcome",                              verify: ".welcome, .stat-card" },
  { name: "agent-definitions",        path: "/agent-definitions/index",              verify: ".el-card, .list-card" },
  { name: "agent-instances",          path: "/agent-instances/index",                verify: ".el-card, .list-card" },
  { name: "resource-pools",           path: "/resource-pools/index",                 verify: ".el-card, .list-card" },
  { name: "hub",                      path: "/hub/index",                            verify: ".el-card, .hub-card" },
  { name: "knowledge",                path: "/knowledge/index",                      verify: ".el-empty, .main" },
  { name: "litellm-models",           path: "/litellm/models",                       verify: ".el-table, .el-card" },
  { name: "litellm-keys",             path: "/litellm/keys",                         verify: ".el-table, .el-card" },
  { name: "monitoring-trace",         path: "/monitoring/trace",                     verify: ".el-table, .el-card, .filter" },
  { name: "monitoring-resources",     path: "/monitoring/resources",                 verify: ".el-card, canvas, .chart" },
  { name: "monitoring-service-health", path: "/monitoring/service-health",           verify: ".el-card, .el-table" },
  { name: "monitoring-usage",         path: "/monitoring/usage",                     verify: ".el-card, canvas, .chart" },
  { name: "monitoring-calls",         path: "/monitoring/calls",                     verify: ".el-card, .el-table" },
  { name: "monitoring-operation-log", path: "/monitoring/operation-log",             verify: ".el-table" },
  { name: "monitoring-log-search",    path: "/monitoring/log-search",                verify: ".el-table" },
  { name: "monitoring-alerts",        path: "/monitoring/alerts",                    verify: ".el-card, .el-table" },
  { name: "system-user",              path: "/system/user/index",                    verify: ".el-table" },
  { name: "system-role",              path: "/system/role/index",                    verify: ".el-table" },
  { name: "system-user-group",        path: "/system/user-group/index",              verify: ".el-table" },
  { name: "system-engine-config",     path: "/system/engine-config/index",           verify: ".el-form, .el-card, .el-radio-group" },
  { name: "system-security-config",   path: "/system/security-config/index",          verify: ".el-form, .el-card, .el-radio-group" },
  { name: "account-settings",         path: "/account-settings",                     verify: ".el-form, .el-card" },
  { name: "community-list",           path: "/community/list",                       verify: ".main, .el-card, .el-empty" },
  { name: "community-audit",          path: "/community/audit",                      verify: ".main, .el-table, .el-empty" },
  { name: "docs-site",                path: "/docs/",                                verify: ".VPHome, .VPNavBar, .content", external: true },
];

async function loginViaApi() {
  console.log("→ 登录 (API)...");
  const ctx = await request.newContext({ baseURL: BASE });
  const resp = await ctx.post("/api/manager/auth/login", {
    data: { username: "admin", password: "admin123" },
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok()) {
    throw new Error(`登录失败: ${resp.status()} ${await resp.text()}`);
  }
  const tokens = await resp.json();
  const meResp = await ctx.get("/api/manager/auth/me", {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  const me = meResp.ok() ? await meResp.json() : { username: "admin", roles: ["系统管理员"] };
  await ctx.dispose();
  console.log(`  ✅ 登录成功: ${me.username}, roles=${JSON.stringify(me.roles)}`);
  return { tokens, me };
}

async function seedAuthState(browser, { tokens, me }) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([
    {
      name: "authorized-token",
      value: JSON.stringify({
        accessToken: tokens.access_token,
        expires: Date.now() + 30 * 60 * 1000,
        refreshToken: tokens.refresh_token,
      }),
      domain: "190.92.230.115",
      path: "/",
    },
    { name: "multiple-tabs", value: "true", domain: "190.92.230.115", path: "/" },
  ]);
  const page = await ctx.newPage();
  await page.goto(`${BASE}/welcome`, { waitUntil: "domcontentloaded" });
  await page.evaluate(({ me, tokens }) => {
    localStorage.setItem("user-info", JSON.stringify({
      refreshToken: tokens.refresh_token,
      expires: Date.now() + 30 * 60 * 1000,
      avatar: "",
      username: me.username,
      nickname: me.nickname || me.username,
      roles: me.roles || [],
      permissions: [],
    }));
  }, { me, tokens });
  await page.goto(`${BASE}/welcome`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".el-menu, .el-aside, .sidebar-container, .app-sidebar", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(3000);
  return { ctx, page };
}

async function spaNavigate(page, toPath) {
  return await page.evaluate((p) => {
    const root = document.querySelector("#app")?.__vue_app__;
    if (!root) return false;
    const router = root.config.globalProperties.$router;
    if (!router) return false;
    router.push(p);
    return true;
  }, toPath);
}

async function capture(page, entry) {
  console.log(`→ 截图 ${entry.name} (${entry.path})`);

  if (entry.external) {
    const url = `${BASE}${entry.path}`;
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
    } catch (e) {
      console.log(`  ⚠ goto timeout: ${e.message.slice(0, 80)}`);
    }
    await page.waitForTimeout(2000);
    if (entry.verify) {
      await page.waitForSelector(entry.verify, { timeout: 10000 }).catch(() => {});
    }
    await page.waitForTimeout(1500);
    const out = path.join(OUT_DIR, `${entry.name}.png`);
    await page.screenshot({ path: out, fullPage: false });
    console.log(`  ✅ ${out}`);
    return true;
  }

  if (entry.path !== "/welcome") {
    const ok = await spaNavigate(page, entry.path);
    if (!ok) {
      console.log(`  ⚠ router.push 失败，回退到 goto`);
      await page.goto(`${BASE}${entry.path}`, { waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    }
  }
  await page.waitForTimeout(2000);
  if (entry.verify) {
    await page.waitForSelector(entry.verify, { timeout: 10000 }).catch(() => {});
  }
  await page.waitForTimeout(2500);
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});

  const out = path.join(OUT_DIR, `${entry.name}.png`);
  const main = page.locator(".app-main").first();
  if (await main.count() > 0) {
    await main.screenshot({ path: out });
  } else {
    await page.screenshot({ path: out, fullPage: false });
  }
  console.log(`  ✅ ${out}`);
  return true;
}

(async () => {
  const { tokens, me } = await loginViaApi();
  const browser = await chromium.launch();
  const { ctx, page } = await seedAuthState(browser, { tokens, me });

  let ok = 0, fail = 0;
  for (const entry of PAGES) {
    try {
      await capture(page, entry);
      ok++;
    } catch (e) {
      console.log(`  ❌ ${entry.name}: ${e.message.slice(0, 120)}`);
      fail++;
    }
  }

  await browser.close();
  console.log(`\n完成: ${ok} 成功, ${fail} 失败`);
})().catch(e => { console.error("❌", e); process.exit(1); });
