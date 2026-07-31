/**
 * 0.8.102 短信服务商配置 multi-provider 验证脚本 — 在 ECS 上端到端测试
 *
 * 验证项：
 * 1. 短信卡显示 el-table list（不再是 el-form singleton），空状态文案正确
 * 2. 新建 dialog 选 provider 时字段联动（aliyun/huawei → region；tencent → sdk_app_id，无 region）
 * 3. 创建 3 个 provider 配置（每个一行）
 * 4. 测试连接按钮：3 个 provider 都会调用对应 SDK（假凭据必然探活失败，但应返回错误而非崩溃）
 * 5. 设为发码渠道：仅一行 is_active=true，激活其他行时上一行自动 deactivate
 * 6. 删除一行
 * 7. 截图最终状态
 *
 * 用法：node scripts/verify-0.8.102-sms-multi-config.mjs
 */
import pkg from "/Users/terry/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.js";
const { chromium, request } = pkg;
import path from "path";
import fs from "fs";

const BASE = "http://190.92.230.115:30080";
const OUT_DIR = path.resolve("./tmp/verify-0.8.102");
fs.mkdirSync(OUT_DIR, { recursive: true });

const log = (step, msg) => console.log(`[${step}] ${msg}`);
const shot = async (page, name) => {
  const out = path.join(OUT_DIR, `${name}.png`);
  const main = page.locator(".app-main").first();
  if (await main.count() > 0) {
    await main.screenshot({ path: out });
  } else {
    await page.screenshot({ path: out, fullPage: false });
  }
  log("shot", `-> ${out}`);
};

// 假凭据（用于测试探活失败路径，不提交到代码仓库）
const FAKE = {
  aliyun: {
    sign_name: "知行测试",
    template_code: "SMS_12345678",
    access_key_id: "LTAI5fakeAkId001",
    access_key_secret: "fakeAkSecret002notreal0000",
    region: "cn-hangzhou",
    daily_limit: 1000,
    interval_seconds: 60,
  },
  tencent: {
    sign_name: "知行测试",
    template_code: "1234567",
    access_key_id: "AKIDfakeAkId001NotReal",
    access_key_secret: "fakeAkSecret002notreal0000",
    sdk_app_id: "1400001234",
    daily_limit: 1000,
    interval_seconds: 60,
  },
  huawei: {
    sign_name: "知行测试",
    template_code: "1234567890123",
    access_key_id: "fakeAkId001NotReal",
    access_key_secret: "fakeAkSecret002notreal0000",
    region: "cn-north-4",
    daily_limit: 1000,
    interval_seconds: 60,
  },
};

async function loginViaApi() {
  log("login", "→ POST /api/manager/auth/login (admin/admin123)");
  const ctx = await request.newContext({ baseURL: BASE });
  const resp = await ctx.post("/api/manager/auth/login", {
    data: { username: "admin", password: "admin123" },
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok()) throw new Error(`登录失败: ${resp.status()} ${await resp.text()}`);
  const tokens = await resp.json();
  await ctx.dispose();
  log("login", `✅ access_token: ${tokens.access_token.slice(0, 30)}...`);
  return tokens;
}

async function seedAuthState(browser, tokens) {
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
  await page.evaluate((t) => {
    localStorage.setItem("user-info", JSON.stringify({
      refreshToken: t.refresh_token,
      expires: Date.now() + 30 * 60 * 1000,
      avatar: "",
      username: "admin",
      nickname: "admin",
      roles: ["系统管理员", "平台管理员"],
      permissions: [],
    }));
  }, tokens);
  await page.goto(`${BASE}/welcome`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-main, .el-menu", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);
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

(async () => {
  const tokens = await loginViaApi();
  const browser = await chromium.launch();
  const { ctx, page } = await seedAuthState(browser, tokens);

  let pass = 0, fail = 0;
  const expect = (cond, label) => {
    if (cond) { pass++; log("PASS", label); }
    else { fail++; log("FAIL", label); }
  };

  try {
    // ===== Step 1: 跳转安全配置页 → 短信卡显示 el-table list =====
    log("step1", "→ spaNavigate /system/security-config/index");
    await spaNavigate(page, "/system/security-config/index");
    await page.waitForTimeout(2500);
    await page.waitForSelector(".el-table", { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await shot(page, "01-security-config-page");

    const smsCard = page.locator(".el-card").filter({ hasText: "短信服务商配置" }).first();
    expect(await smsCard.count() > 0, "短信卡标题「短信服务商配置」存在");

    const smsTable = smsCard.locator(".el-table").first();
    expect(await smsTable.count() > 0, "短信卡渲染 el-table（multi-config list 模式）");

    // 空状态文案（如果 DB 已有数据则可能不显示空状态，不强制断言）
    const emptyText = await smsCard.locator(".el-table__empty-text").first().textContent().catch(() => "");
    if (emptyText) {
      expect(emptyText.includes("暂无短信服务商配置"), `空状态文案「暂无短信服务商配置」(actual: ${emptyText?.trim()})`);
    }

    // ===== Step 2: 新建 dialog 选 provider 时字段联动 =====
    log("step2", "→ 点击「新建」按钮打开 dialog");
    const newBtn = smsCard.locator("button").filter({ hasText: "新建" }).first();
    await newBtn.click();
    await page.waitForTimeout(500);
    const dialog = page.locator(".el-dialog").filter({ hasText: "新建短信服务商" }).first();
    await dialog.waitFor({ state: "visible", timeout: 5000 });
    expect(await dialog.isVisible(), "新建 dialog 可见");

    // 选 aliyun → 应出现 region 字段
    const aliyunRadio = dialog.locator(".el-radio").filter({ hasText: "阿里云" }).first();
    await aliyunRadio.click();
    await page.waitForTimeout(300);
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "AccessKey ID" }).count() > 0,
       "选阿里云 → 出现 AccessKey ID 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "AccessKey Secret" }).count() > 0,
       "选阿里云 → 出现 AccessKey Secret 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "地域" }).count() > 0,
       "选阿里云 → 出现 地域 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "签名名称" }).count() > 0,
       "选阿里云 → 出现 签名名称 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "模板 CODE" }).count() > 0,
       "选阿里云 → 出现 模板 CODE 字段");

    // 切到 tencent → 应出现 SDK AppID 字段，地域 字段消失
    const tencentRadio = dialog.locator(".el-radio").filter({ hasText: "腾讯云" }).first();
    await tencentRadio.click();
    await page.waitForTimeout(300);
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "SDK AppID" }).count() > 0,
       "切到腾讯云 → 出现 SDK AppID 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "地域" }).count() === 0,
       "切到腾讯云 → 不显示 地域 字段（tencent 不用 region）");

    // 切到 huawei → 应出现 地域 字段，SDK AppID 字段消失
    const huaweiRadio = dialog.locator(".el-radio").filter({ hasText: "华为云" }).first();
    await huaweiRadio.click();
    await page.waitForTimeout(300);
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "地域" }).count() > 0,
       "切到华为云 → 重新出现 地域 字段");
    expect(await dialog.locator(".el-form-item__label").filter({ hasText: "SDK AppID" }).count() === 0,
       "切到华为云 → 不显示 SDK AppID 字段");
    await shot(page, "02-dialog-huawei-selected");

    // 关闭 dialog，准备通过 API 创建（更稳定）
    await page.locator(".el-dialog").first().locator("button").filter({ hasText: "取消" }).first().click().catch(() => {});
    await page.waitForTimeout(500);
    await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(500);

    // ===== Step 3: 通过 API 创建 3 个 provider 配置 =====
    log("step3", "→ 通过 API 创建 3 个 provider 配置");
    const apiCtx = await request.newContext({ baseURL: BASE });
    const authHeaders = { Authorization: `Bearer ${tokens.access_token}`, "Content-Type": "application/json" };

    // 先清理可能存在的旧数据（避免上次失败遗留）
    const oldList = await (await apiCtx.get("/api/manager/sms-configs", { headers: authHeaders })).json();
    if (Array.isArray(oldList) && oldList.length > 0) {
      log("cleanup", `  清理旧数据 ${oldList.length} 行`);
      for (const c of oldList) {
        await apiCtx.delete(`/api/manager/sms-configs/${c.id}`, { headers: authHeaders });
      }
    }

    const createdIds = [];
    for (const [provider, payload] of Object.entries(FAKE)) {
      const resp = await apiCtx.post("/api/manager/sms-configs", {
        data: { provider, enabled: true, ...payload },
        headers: authHeaders,
      });
      if (!resp.ok()) {
        log("api", `❌ 创建 ${provider} 失败: ${resp.status()} ${await resp.text()}`);
        fail++;
        continue;
      }
      const cfg = await resp.json();
      createdIds.push({ provider, id: cfg.id, cfg });
      log("api", `  ✅ 创建 ${provider}: id=${cfg.id}`);
    }
    expect(createdIds.length === 3, `3 个 provider 都创建成功 (actual: ${createdIds.length})`);

    // reload page → table 应显示 3 行
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await spaNavigate(page, "/system/security-config/index");
    await page.waitForTimeout(2500);
    await page.waitForSelector(".el-table", { timeout: 10000 }).catch(() => {});

    const smsCard2 = page.locator(".el-card").filter({ hasText: "短信服务商配置" }).first();
    const tableRows = smsCard2.locator(".el-table__body-wrapper tr");
    const rowCount = await tableRows.count();
    expect(rowCount === 3, `表格显示 3 行配置 (actual: ${rowCount})`);
    await shot(page, "03-list-3-providers");

    // 验证 3 个 provider 都出现
    const providerTags = await smsCard2.locator(".el-table .el-tag").allTextContents();
    const providerLabels = providerTags.map(s => s.trim()).filter(s => s.length > 0);
    expect(providerLabels.some(t => t.includes("阿里云")), "表格中有 阿里云 tag");
    expect(providerLabels.some(t => t.includes("腾讯云")), "表格中有 腾讯云 tag");
    expect(providerLabels.some(t => t.includes("华为云")), "表格中有 华为云 tag");

    // ===== Step 4: 测试连接按钮 — 假凭据必然探活失败，但应返回错误而非崩溃 =====
    log("step4", "→ 测试连接按钮：假凭据应返回探活失败而非崩溃");
    for (const { provider, id } of createdIds) {
      const testResp = await apiCtx.post(`/api/manager/sms-configs/${id}/test`, { headers: authHeaders });
      const testResult = await testResp.json();
      log("test", `  ${provider}: ok=${testResult.ok}, error=${testResult.error || "null"}`);
      expect(testResp.ok() && testResult.ok === false, `${provider} 测试连接返回 ok=false（探活失败，预期行为）`);
      expect(!!testResult.error, `${provider} 测试连接返回错误信息（非空）`);
    }

    // ===== Step 5: 设为发码渠道 — 激活唯一性 =====
    log("step5", "→ POST /api/manager/sms-configs/{id}/activate 激活 aliyun 行");
    const aliyunRow = createdIds.find(r => r.provider === "aliyun");
    const actResp = await apiCtx.post(`/api/manager/sms-configs/${aliyunRow.id}/activate`, { headers: authHeaders });
    expect(actResp.ok(), `activate aliyun 返回 200 (status=${actResp.status()})`);
    const actData = await actResp.json();
    expect(actData.is_active === true, "aliyun 行 is_active=true");

    // 列表查询 → 应只有 aliyun 行 is_active=true
    const listResp = await apiCtx.get("/api/manager/sms-configs", { headers: authHeaders });
    const list = await listResp.json();
    const activeRows = list.filter(c => c.is_active);
    expect(activeRows.length === 1, `全局仅一行 active (actual: ${activeRows.length})`);
    expect(activeRows[0].provider === "aliyun", `active 行是 aliyun (actual: ${activeRows[0].provider})`);

    // 再激活 tencent 行 → aliyun 应 deactivate
    const tencentRow = createdIds.find(r => r.provider === "tencent");
    const act2Resp = await apiCtx.post(`/api/manager/sms-configs/${tencentRow.id}/activate`, { headers: authHeaders });
    expect(act2Resp.ok(), `activate tencent 返回 200`);
    const list2 = await (await apiCtx.get("/api/manager/sms-configs", { headers: authHeaders })).json();
    const active2 = list2.filter(c => c.is_active);
    expect(active2.length === 1, `激活 tencent 后仍仅一行 active (actual: ${active2.length})`);
    expect(active2[0].provider === "tencent", `切换后 active 行是 tencent (actual: ${active2[0].provider})`);
    log("step5", `  ✅ 切换 active: aliyun → tencent，aliyun is_active=${list2.find(c => c.provider === "aliyun").is_active}`);

    // reload 页面 → 表格首行应是 tencent（active），且只有 tencent 有「已启用」tag
    await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
    await page.waitForTimeout(2000);
    await spaNavigate(page, "/system/security-config/index");
    await page.waitForTimeout(2500);
    await shot(page, "05-tencent-active");

    // ===== Step 6: 删除一行 =====
    log("step6", "→ DELETE 短信配置（删除 huawei 行）");
    const huaweiRow = createdIds.find(r => r.provider === "huawei");
    const delResp = await apiCtx.delete(`/api/manager/sms-configs/${huaweiRow.id}`, { headers: authHeaders });
    expect(delResp.status() === 204, `删除 huawei 返回 204 (status=${delResp.status()})`);

    const list3 = await (await apiCtx.get("/api/manager/sms-configs", { headers: authHeaders })).json();
    expect(list3.length === 2, `删除后剩 2 行 (actual: ${list3.length})`);

    await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
    await page.waitForTimeout(2000);
    await spaNavigate(page, "/system/security-config/index");
    await page.waitForTimeout(2500);
    await shot(page, "06-after-delete");

    // 清理：删除剩余 2 行，让 DB 回到初始状态
    log("cleanup", "→ 清理：删除剩余 2 行测试配置");
    for (const c of list3) {
      await apiCtx.delete(`/api/manager/sms-configs/${c.id}`, { headers: authHeaders });
    }
    const list4 = await (await apiCtx.get("/api/manager/sms-configs", { headers: authHeaders })).json();
    log("cleanup", `  清理后剩余 ${list4.length} 行`);
    expect(list4.length === 0, `清理后剩 0 行 (actual: ${list4.length})`);

    await apiCtx.dispose();
  } catch (e) {
    console.error("❌", e);
    fail++;
  } finally {
    await browser.close();
    console.log(`\n========== 验证结果: ${pass} 通过, ${fail} 失败 ==========`);
    process.exit(fail > 0 ? 1 : 0);
  }
})();
