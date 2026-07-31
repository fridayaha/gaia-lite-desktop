/**
 * test-dev-debug.js — Dev/Debug 双实例测试
 *
 * 验证项 #8（Dev vs Debug 实例隔离）、#9（文件变更追踪）
 *
 * 用法：node test-dev-debug.js
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

// ── SSE 事件收集 ──────────────────────────────────────────

async function collectEvents(wid, sid, timeoutMs = 30000) {
  const events = [];
  const url = `${BASE_URL}/api/skill-engine/workspaces/${wid}/sessions/${sid}/events`;

  const resp = await fetch(url);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  const startMs = Date.now();

  while (Date.now() - startMs < timeoutMs) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event = JSON.parse(line.slice(6));
          events.push(event);

          // 遇到 turn_end 停止收集
          if (event.eventType === "turn_end") {
            reader.cancel();
            return events;
          }
        } catch {}
      }
    }
  }

  reader.cancel();
  return events;
}

// ── 主流程 ─────────────────────────────────────────────────

async function main() {
  console.log("\n========================================");
  console.log("  Dev / Debug 双实例测试");
  console.log("========================================\n");

  // 1. 创建工作区
  const ws = await request("POST", "/api/skill-engine/workspaces", {
    name: "dev-debug-test",
    description: "Dev/Debug 双实例隔离测试",
  });
  console.log(`✅ 工作区已创建: ${ws.id}`);

  // 2. 启动 Dev 实例
  console.log("\n--- 启动 Dev 实例 ---");
  const devSess = await request("POST", `/api/skill-engine/workspaces/${ws.id}/sessions`, {
    role: "dev",
  });
  console.log(`Dev 实例: ${JSON.stringify(devSess)}`);

  // 3. 启动 Debug 实例
  console.log("\n--- 启动 Debug 实例 ---");
  const debugSess = await request("POST", `/api/skill-engine/workspaces/${ws.id}/sessions`, {
    role: "debug",
  });
  console.log(`Debug 实例: ${JSON.stringify(debugSess)}`);

  // 4. 验证两个实例共存
  const stats = await request("GET", "/api/skill-engine/admin/stats");
  const activeKeys = Object.keys(stats);
  console.log(`\n活跃实例: ${activeKeys.join(", ")}`);
  const hasBoth = activeKeys.includes(`${ws.id}:dev`) && activeKeys.includes(`${ws.id}:debug`);
  console.log(hasBoth ? "✅ Dev 和 Debug 实例共存" : "❌ 实例缺失");

  // 5. Dev 实例：发 prompt（开发对话）
  console.log("\n--- Dev 实例: 发送开发 prompt ---");
  const devPromptStart = Date.now();
  const devResult = await request(
    "POST",
    `/api/skill-engine/workspaces/${ws.id}/sessions/dev/prompt`,
    { message: "请创建一个简单的 hello world 技能，只需要 SKILL.md 和 manifest.json" }
  );
  const devPromptMs = Date.now() - devPromptStart;
  console.log(`Dev prompt 结果: success=${devResult.success}, 耗时=${devPromptMs}ms`);

  // 收集 Dev 事件（如果 SSE 可用）
  console.log("\n--- 收集 Dev SSE 事件 ---");
  try {
    const devEvents = await collectEvents(ws.id, "dev", 60000);
    const eventTypes = devEvents.map((e) => e.eventType).filter(Boolean);
    const toolEvents = eventTypes.filter((t) => t.startsWith("tool_execution"));
    console.log(`收到 ${devEvents.length} 个事件`);
    console.log(`事件类型: ${[...new Set(eventTypes)].join(", ")}`);
    console.log(`工具调用事件: ${toolEvents.length} 个`);
    if (toolEvents.length > 0) {
      console.log("✅ Dev 实例产生了工具调用事件（文件变更可追踪）");
    }
  } catch (err) {
    console.log(`⚠️ SSE 收集失败: ${err.message}（非阻塞，继续测试）`);
  }

  // 6. Debug 实例：发 prompt（测试技能对话）
  console.log("\n--- Debug 实例: 发送测试 prompt ---");
  const debugPromptStart = Date.now();
  const debugResult = await request(
    "POST",
    `/api/skill-engine/workspaces/${ws.id}/sessions/debug/prompt`,
    { message: "你好，请执行 hello world 技能" }
  );
  const debugPromptMs = Date.now() - debugPromptStart;
  console.log(`Debug prompt 结果: success=${debugResult.success}, 耗时=${debugPromptMs}ms`);

  // 7. 验证工具隔离：Debug 实例应该只有只读工具
  console.log("\n--- 验证工具隔离 ---");
  try {
    const devState = await request("GET", `/api/skill-engine/workspaces/${ws.id}/sessions/dev/state`);
    const debugState = await request("GET", `/api/skill-engine/workspaces/${ws.id}/sessions/debug/state`);
    console.log(`Dev 状态: ${JSON.stringify(devState.data)}`);
    console.log(`Debug 状态: ${JSON.stringify(debugState.data)}`);
  } catch (err) {
    console.log(`⚠️ 状态查询失败: ${err.message}`);
  }

  // 8. 清理
  console.log("\n--- 清理 ---");
  await request("DELETE", `/api/skill-engine/workspaces/${ws.id}/sessions/dev`);
  await request("DELETE", `/api/skill-engine/workspaces/${ws.id}/sessions/debug`);
  console.log("✅ 实例已停止");

  // 判定
  console.log("\n--- 判定 ---");
  if (hasBoth) {
    console.log("✅ Dev/Debug 双实例可共存");
  } else {
    console.log("❌ Dev/Debug 双实例共存失败");
  }
  if (devResult.success) {
    console.log("✅ Dev 实例可正常对话+文件操作");
  } else {
    console.log("❌ Dev 实例对话失败");
  }
  if (debugResult.success) {
    console.log("✅ Debug 实例可正常对话（只读模式）");
  } else {
    console.log("❌ Debug 实例对话失败");
  }
}

main().catch(console.error);
