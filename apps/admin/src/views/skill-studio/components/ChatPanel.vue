<script setup lang="ts">
/**
 * ChatPanel — Skill Studio 中栏单会话面板（dev/debug 各一）。
 *
 * Phase 3c：改用 @ua/chat 共享 <ChatMessages> + <ChatComposer>，经 useSkillEngineChat
 * 适配器把 skill-engine 的 parts[] 展平成平行模型。工具卡走共享 ToolCard 丰富形态
 * （bash/write/edit diff/grep/read/clarify）。clarify 表单提交透传 submitClarifyApi。
 */
import { computed, provide, toRef } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { ChatMessages, ChatComposer, chatContextKey } from "@ua/chat";
import {
  submitClarifyApi,
  readFileAsImageApi,
  type EngineRole,
} from "@/api/manager/skill-engine";
import type { useEngineSession } from "../useEngineSession";
import { useSkillEngineChat } from "../useSkillEngineChat";
import SkillStudioLogo from "./SkillStudioLogo.vue";

defineOptions({ name: "ChatPanel" });

const props = defineProps<{
  role: EngineRole;
  engine: ReturnType<typeof useEngineSession>;
  workspaceId: string;
}>();
const { t } = useI18n();

const chat = useSkillEngineChat(props.engine, props.role, toRef(props, "workspaceId"));

// 注入共享包聊天上下文：imageResolver 把工作区图片解析成 base64，让技能产出的
// 图表（output/charts/*.png 等）能在对话区里内联渲染。fileDownloader/fileLister
// 暂不需要（Skill Studio 有自己的文件树）。
provide(chatContextKey, {
  imageResolver: (path: string) => readFileAsImageApi(props.workspaceId, path),
});

const agentName = computed(() => t(`hub.studio.detail.greeting.${props.role}.title`));
const greetingSubtitle = computed(
  () => t(`hub.studio.detail.greeting.${props.role}.desc`) || "",
);
const suggestions = computed(() =>
  ["ex1", "ex2", "ex3"].map((k) => t(`hub.studio.detail.greeting.${props.role}.${k}`)),
);
const composerDisabled = computed(
  () => chat.isStreaming.value || chat.status.value === "connecting"
);
const currentModel = computed(() => {
  const m = chat.model.value;
  return m ? `${m.provider}/${m.modelId}` : "—";
});

function onSend(text: string, _files: File[]) {
  const trimmed = text.trim();
  if (!trimmed || chat.isStreaming.value) return;
  void chat.send(trimmed);
}

function onSuggestion(text: string) {
  if (chat.isStreaming.value) return;
  void chat.send(text);
}

function onStop() {
  chat.abort();
}

/**
 * clarify 工具提交：把用户填写的答案递交给阻塞中的引擎会话。
 * 返回 true 表示成功递交（卡进入「已提交」态，等 tool_execution_end 到达后转只读）。
 */
async function onClarifySubmit(
  toolCallId: string,
  answers: Record<string, unknown>
): Promise<boolean> {
  try {
    const res = await submitClarifyApi(props.workspaceId, props.role, toolCallId, answers);
    if (res.ok) return true;
    ElMessage.error(res.error || t("hub.studio.msg.loadFailed"));
    return false;
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
    return false;
  }
}
</script>

<template>
  <div class="chat-panel">
    <div class="messages-shell">
      <ChatMessages
        :messages="chat.messages.value"
        :is-streaming="chat.isStreaming.value"
        :streaming-content="chat.streamingContent.value"
        :is-empty="chat.isEmpty.value"
        :tool-calls="chat.toolCalls.value"
        :agent-name="agentName"
        :greeting-subtitle="greetingSubtitle"
        :suggestions="suggestions"
        :stream-fade-effect="true"
        :clarify-submit="onClarifySubmit"
        @send-suggestion="onSuggestion"
      >
        <template #logo><SkillStudioLogo /></template>
      </ChatMessages>
    </div>
    <ChatComposer
      :disabled="composerDisabled"
      :models="[]"
      :current-model="currentModel"
      :hide-attach="true"
      :hide-model="true"
      @send="onSend"
      @stop="onStop"
    />
  </div>
</template>

<style scoped>
/* 主题对齐：把 @ua/chat 的 CSS 变量覆盖到 skill-studio 紫色系，使共享组件融入 admin。 */
.chat-panel {
  --accent: var(--ss-accent, #6d5efc);
  --accent-hover: var(--ss-accent, #6d5efc);
  --accent-bg: var(--ss-accent-soft, rgba(109, 94, 252, 0.1));
  --accent-bg-strong: rgba(109, 94, 252, 0.15);
  --accent-text: var(--ss-accent, #6d5efc);
  --text: var(--ss-ink, #1f2430);
  --muted: var(--el-text-color-secondary, #6b7280);
  --border: var(--ss-line, #e5e7eb);
  --border-subtle: var(--ss-line, #e5e7eb);
  --surface: var(--el-bg-color, #fff);
  --surface-subtle: var(--ss-recess, #f6f7f9);
  --surface-subtle-hover: rgba(109, 94, 252, 0.06);
  --code-bg: var(--ss-recess, #f6f7f9);
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
/* composer 前后留空隙（上下不贴消息/边缘，左右与消息对齐）；同时撑满中栏宽度
   （.composer-box 默认 max-width + margin:0 auto 在 flex column 下会缩成内容宽）。 */
.chat-panel :deep(.messages-shell) {
  padding: 0 12px;
}
.chat-panel :deep(.composer-box) {
  max-width: 100%;
  margin: 8px 12px 12px;
}
/* .messages-shell 包裹 ChatMessages（不含 composer），position:relative 让共享组件
   内的 .scroll-to-bottom-btn 浮在消息区底部（composer 上方），不与发送按钮重叠。
   对齐 enduser ChatPage 的 .messages-shell 结构。 */
.messages-shell {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.chat-panel :deep(.messages) {
  flex: 1;
  min-height: 0;
}
</style>
