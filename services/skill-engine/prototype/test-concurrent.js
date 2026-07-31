/**
 * test-concurrent.js — 并发压测 + 资源监控
 *
 * 验证项 #3（5-10 并发）、#4（资源占用）、#6（LLM 速率控制）
 *
 * 用法：
 *   node test-concurrent.js [--count N] [--prompt "消息"]
 *
 * 需要 Skill Engine 主进程运行中（node main.js）
 */

const BASE_URL = `http://localhost:${process.env.PORT || 8004}`;

async function request(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE_URL}${path}`, opts);
  return resp.json();
}

// ── 监控资源 ───────────────────────────────────────────────

async function getResources() {
  try {
    const resp = await fetch(`${BASE_URL}/api/skill-engine/admin/resources`);
    return await resp.json();
  } catch {
    return null;
  }
}

async function getStats() {
  try {
    const resp = await fetch(`${BASE_URL}/api/skill-engine/admin/stats`);
    return await resp.json();
  } catch {
    return null;
  }
}

// ── 单用户测试 ─────────────────────────────────────────────

async function runUser(userId, promptText) {
  const startMs = Date.now();
  const results = { userId, spawnMs: 0, promptMs: 0, error: null };

  try {
    // 1. 创建工作区
    const ws = await request("POST", "/api/skill-engine/workspaces", {
      name: `test-skill-${userId}`,
      description: `Test skill for user ${userId}`,
    });

    // 2. 启动 dev 会话
    const spawnStart = Date.now();
    const sess = await request("POST", `/api/skill-engine/workspaces/${ws.id}/sessions`, {
      role: "dev",
    });
    results.spawnMs = Date.now() - spawnStart;

    // 3. 发 prompt
    const promptStart = Date.now();
    const promptResult = await request(
      "POST",
      `/api/skill-engine/workspaces/${ws.id}/sessions/${sess.role}/prompt`,
      { message: promptText }
    );
    results.promptMs = Date.now() - promptStart;
    results.promptSuccess = promptResult.success;

    // 4. 清理
    await request("DELETE", `/api/skill-engine/workspaces/${ws.id}/sessions/${sess.role}`);
  } catch (err) {
    results.error = err.message;
  }

  results.totalMs = Date.now() - startMs;
  return results;
}

// ── 主流程 ─────────────────────────────────────────────────

async function main() {
  const count = parseInt(process.argv.find((a) => a.startsWith("--count="))?.split("=")[1] || "5");
  const promptText = process.argv.find((a) => a.startsWith("--prompt="))?.split("=")[1]
    || "请帮我创建一个简单的技能，功能是输出 hello world";

  console.log(`\n========================================`);
  console.log(`  并发压测: ${count} 用户`);
  console.log(`  Prompt: "${promptText}"`);
  console.log(`========================================\n`);

  // 记录压测前资源
  const beforeResources = await getResources();
  if (beforeResources) {
    console.log(`[Before] 内存: ${beforeResources.freeMemoryMB}MB free / ${beforeResources.totalMemoryMB}MB total`);
    console.log(`[Before] CPU: ${beforeResources.cpuCount} cores, load: ${beforeResources.loadAvg.map((l) => l.toFixed(2)).join(", ")}`);
  }

  // 并发启动
  console.log(`\n--- 并发启动 ${count} 个用户 ---`);
  const spawnStartAll = Date.now();

  const promises = [];
  for (let i = 0; i < count; i++) {
    promises.push(runUser(i + 1, promptText));
  }

  const results = await Promise.allSettled(promises);
  const totalMs = Date.now() - spawnStartAll;

  // 分析结果
  console.log(`\n--- 结果 (${totalMs}ms 总耗时) ---\n`);

  let successCount = 0;
  let errorCount = 0;
  let spawnTimes = [];
  let promptTimes = [];

  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    if (r.status === "fulfilled" && !r.value.error) {
      successCount++;
      spawnTimes.push(r.value.spawnMs);
      promptTimes.push(r.value.promptMs);
      console.log(`  User ${r.value.userId}: spawn=${r.value.spawnMs}ms, prompt=${r.value.promptMs}ms, total=${r.value.totalMs}ms`);
    } else {
      errorCount++;
      const err = r.status === "fulfilled" ? r.value.error : r.reason?.message;
      console.log(`  User ${i + 1}: ERROR - ${err}`);
    }
  }

  // 统计
  console.log(`\n--- 统计 ---`);
  console.log(`  成功: ${successCount}/${count}`);
  console.log(`  失败: ${errorCount}/${count}`);

  if (spawnTimes.length > 0) {
    spawnTimes.sort((a, b) => a - b);
    promptTimes.sort((a, b) => a - b);
    console.log(`  Spawn 时间: min=${spawnTimes[0]}ms, median=${spawnTimes[Math.floor(spawnTimes.length / 2)]}ms, max=${spawnTimes[spawnTimes.length - 1]}ms`);
    console.log(`  Prompt 时间: min=${promptTimes[0]}ms, median=${promptTimes[Math.floor(promptTimes.length / 2)]}ms, max=${promptTimes[promptTimes.length - 1]}ms`);
  }

  // 压测后资源
  const afterResources = await getResources();
  if (afterResources) {
    console.log(`\n[After] 内存: ${afterResources.freeMemoryMB}MB free / ${afterResources.totalMemoryMB}MB total`);
    if (beforeResources) {
      const memUsed = beforeResources.freeMemoryMB - afterResources.freeMemoryMB;
      console.log(`[After] 内存增量: ~${memUsed}MB`);
    }
  }

  const stats = await getStats();
  if (stats) {
    console.log(`[After] 活跃实例: ${Object.keys(stats).length}`);
  }

  // 判定
  console.log(`\n--- 判定 ---`);
  if (successCount === count) {
    console.log(`  ✅ 全部 ${count} 用户并发成功`);
  } else {
    console.log(`  ❌ ${errorCount} 个用户失败`);
  }

  if (spawnTimes.length > 0) {
    const maxSpawn = spawnTimes[spawnTimes.length - 1];
    if (maxSpawn < 5000) {
      console.log(`  ✅ 最大 spawn 时间 ${maxSpawn}ms < 5s`);
    } else {
      console.log(`  ⚠️ 最大 spawn 时间 ${maxSpawn}ms > 5s`);
    }
  }
}

main().catch(console.error);
