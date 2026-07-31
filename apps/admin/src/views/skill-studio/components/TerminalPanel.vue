<script setup lang="ts">
/**
 * TerminalPanel — 底部 bash 工具输出聚合视图。
 *
 * 从当前 role 会话的消息 parts 里提取 bash 工具调用，按序展示命令 + 输出，
 * 作为命令执行的持久日志视图（聊天里的工具卡片是按 turn 交错的，这里聚焦
 * 命令流）。复用 @ua/chat 的 extractResultText 解析输出。
 */
import { computed, ref, watch, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import type { EngineRole } from "@/api/manager/skill-engine";
import type { useEngineSession, ToolCall } from "../useEngineSession";
import { extractResultText } from "@ua/chat";

defineOptions({ name: "TerminalPanel" });

const props = defineProps<{
  role: EngineRole;
  engine: ReturnType<typeof useEngineSession>;
}>();
const { t } = useI18n();

interface BashEntry {
  id: string;
  command: string;
  output: string;
  status: ToolCall["status"];
}

const session = computed(() => props.engine.sessions[props.role]);

const entries = computed<BashEntry[]>(() => {
  const out: BashEntry[] = [];
  for (const m of session.value.messages) {
    for (const p of m.parts) {
      if (p.kind !== "tool" || p.tool.toolName !== "bash") continue;
      out.push({
        id: p.tool.id,
        command: typeof p.tool.args?.command === "string" ? p.tool.args.command : "",
        output: p.tool.status === "running" ? "" : extractResultText(p.tool.result),
        status: p.tool.status,
      });
    }
  }
  return out;
});

const bodyEl = ref<HTMLElement | null>(null);
watch(
  () => entries.value.length,
  async () => {
    await nextTick();
    if (bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight;
  },
);
</script>

<template>
  <div class="terminal-panel">
    <div class="term-head">
      <span class="term-title">{{ t("hub.studio.detail.terminal") }}</span>
      <span class="term-count">{{ entries.length }}</span>
    </div>
    <div ref="bodyEl" class="term-body">
      <div v-if="entries.length === 0" class="term-empty">
        {{ t("hub.studio.detail.terminalEmpty") }}
      </div>
      <div v-for="e in entries" :key="e.id" class="term-entry">
        <div class="term-cmd">
          <span class="prompt">$</span>{{ e.command }}
          <span v-if="e.status === 'running'" class="running">●</span>
        </div>
        <pre v-if="e.output" class="term-out">{{ e.output }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.terminal-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #1e1e2e;
  color: #cdd6f4;
}
.term-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-bottom: 1px solid #313244;
  flex-shrink: 0;
}
.term-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #a6adc8;
}
.term-count {
  font-size: 10px;
  background: #313244;
  color: #cdd6f4;
  border-radius: 8px;
  padding: 0 6px;
}
.term-body {
  flex: 1;
  overflow: auto;
  padding: 6px 10px;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.term-empty {
  color: #6c7086;
  font-style: italic;
}
.term-entry {
  margin-bottom: 6px;
}
.term-cmd {
  color: #f9e2af;
  white-space: pre-wrap;
  word-break: break-word;
}
.prompt {
  color: #a6e3a1;
  margin-right: 4px;
}
.running {
  color: #f38ba8;
  margin-left: 6px;
  animation: ss-blink 1s steps(2, start) infinite;
}
@keyframes ss-blink {
  to {
    opacity: 0.3;
  }
}
.term-out {
  margin: 2px 0 0 12px;
  color: #cdd6f4;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
