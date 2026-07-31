<template>
  <div class="messages" id="messages" ref="messagesEl">
    <!-- Loading state: 历史消息加载中（避免先闪欢迎屏再切到消息） -->
    <div v-if="isLoadingMessages" class="messages-loading" id="messagesLoading">
      <div class="messages-loading-spinner" aria-label="加载中"></div>
      <p>正在加载历史消息…</p>
    </div>
    <!-- Empty state -->
    <div v-else-if="isEmpty" class="empty-state" id="emptyState">
      <div class="empty-logo">
        <slot name="logo">
          <!-- 默认通用 AI 图标（宿主未提供 logo slot 时） -->
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" aria-label="智能体">
            <rect x="29" y="10" width="6" height="44" rx="3" fill="#7C3AED" opacity="0.6"/>
            <circle cx="32" cy="12" r="5" fill="#7C3AED"/>
            <circle cx="32" cy="12" r="2" fill="#fff" opacity="0.6"/>
          </svg>
        </slot>
      </div>
      <h2>{{ greetingTitle }}</h2>
      <p>{{ greetingSubtitle }}</p>
      <div class="suggestion-grid">
        <button
          v-for="s in suggestionList"
          :key="s"
          class="suggestion"
          @click="$emit('send-suggestion', s)"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span>{{ s }}</span>
        </button>
      </div>
    </div>

    <!-- Messages container -->
    <div class="messages-inner" id="msgInner" ref="msgInnerRef">
      <!-- Load earlier messages -->
      <button v-if="messages.length > renderCount" class="load-earlier-btn" @click="renderCount += RENDER_WINDOW">
        加载更早消息（{{ messages.length - renderCount }} 条隐藏）
      </button>
      <template v-for="{ msg, idx } in renderedMessages" :key="idx">
        <!-- Date separator -->
        <div v-if="shouldShowDateSep(idx)" class="msg-date-sep">{{ dateSepLabel(msg._ts) }}</div>
        <!-- System message -->
        <div v-if="msg.role === 'system'" class="msg-row msg-system">
          <div class="msg-system-text">{{ msg.content }}</div>
        </div>

        <!-- User message -->
        <div v-else-if="msg.role === 'user'" class="msg-row" data-role="user" :data-msg-idx="idx">
          <!-- Attachments -->
          <div v-if="msg.attachments?.length" class="msg-files">
            <template v-for="(att, ai) in msg.attachments" :key="ai">
              <img v-if="attHasBlobUrl(att)"
                class="msg-media-img"
                :src="att.blobUrl"
                :alt="attName(att)"
                loading="lazy"
                @click="openLightbox(msg, Number(ai))" />
              <AuthenticatedImage v-else-if="attIsImage(att)"
                :path="attPath(att)"
                :alt="attName(att)"
                @click="openLightbox(msg, Number(ai))" />
              <div v-else class="msg-file-badge">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                {{ attName(att) }}
              </div>
            </template>
          </div>
          <!-- Edit mode -->
          <template v-if="editingMsgIdx === idx">
            <textarea
              ref="editTextareaRef"
              class="msg-edit-area"
              v-model="editingText"
              placeholder="编辑消息..."
              @keydown="onEditKeydown"
              @input="autoResizeEdit($event)"
            ></textarea>
            <div class="msg-edit-bar">
              <button class="msg-edit-send" @click="submitEdit">发送编辑</button>
              <button class="msg-edit-cancel" @click="cancelEdit">取消</button>
            </div>
          </template>
          <!-- Normal display -->
          <template v-else>
            <div class="msg-body user" v-html="renderMarkdown(stripAttachmentHint(msg.content))"></div>
            <div class="msg-foot">
              <span class="msg-time" v-if="msg._ts">{{ fmtTime(msg._ts) }}</span>
              <div class="msg-actions">
                <button class="msg-action-btn msg-copy-btn" :class="{ copied: copiedMsgIdx === idx }" :title="copiedMsgIdx === idx ? '已复制' : '复制'" @click="copyMessage(msg, idx)">
                  <svg v-if="copiedMsgIdx === idx" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 8.5 6.5 12 13 4.5"/></svg>
                  <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 12V3h9"/></svg>
                </button>
                <button v-if="idx === lastUserMsgIdx && !isStreaming" class="msg-action-btn" title="编辑" @click="startEdit(msg, idx)">
                  <LucideIcon name="pencil" :size="13" />
                </button>
              </div>
            </div>
          </template>
        </div>

        <!-- Assistant message -->
        <div v-else class="msg-row assistant-turn" data-role="assistant" :data-msg-idx="idx">
          <div class="msg-role assistant">
            <span class="role-icon assistant">A</span>
            <span>{{ agentName || '智能体' }}</span>
          </div>
          <div class="assistant-turn-blocks">
            <!-- Activity group（落定快照，默认折叠）在回复上方 — 对齐 Android：先过程后回复 -->
            <div v-if="msg._thinkingText || (msg._toolCalls || []).length > 0 || visibleEvents(msg._activityEvents).length > 0"
                 class="tool-call-group agent-activity-group"
                 :class="{ 'tool-call-group-collapsed': !settledExpanded(idx, msg) }">
              <button type="button" class="tool-call-group-summary"
                :aria-expanded="!!settledExpanded(idx, msg)"
                @click="toggleSettledActivity(idx)">
                <span class="tool-call-group-chevron">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6,4 10,8 6,12"/></svg>
                </span>
                <span class="tool-call-group-label">{{ settledActivitySummary(msg) }}</span>
              </button>
              <div class="tool-call-group-body">
                <ThinkingCard v-if="msg._thinkingText" :text="msg._thinkingText" status="done" :visible="true" />
                <div v-for="(ev, eidx) in visibleEvents(msg._activityEvents)" :key="`ae-${eidx}`"
                  class="agent-activity-status"
                  :class="`agent-activity-status-${ev.kind || 'info'} agent-activity-status-${ev.status || 'waiting'}`">
                  <span class="agent-activity-status-icon">
                    <svg v-if="ev.kind === 'model'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>
                    <svg v-else-if="ev.kind === 'run'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    <span v-else-if="ev.status === 'waiting' || ev.status === 'running'" class="tool-card-running-dot"></span>
                    <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  </span>
                  <span class="agent-activity-status-copy">
                    <span class="agent-activity-status-label">{{ ev.label }}</span>
                    <span class="agent-activity-status-detail" v-if="ev.detail">{{ ev.detail }}</span>
                  </span>
                  <span class="agent-activity-status-time">{{ ev.ts ? fmtTimeShort(ev.ts) : '' }}</span>
                </div>
                <ToolCard v-for="(tc, tidx) in (msg._toolCalls || [])" :key="`tc-${tidx}`" :name="tc.name" :status="tc.status" :result="tc.result" :preview="tc.preview" :status-label="tc.statusLabel" :tool-name="tc.toolName" :args="tc.args" :raw-result="tc.rawResult" :tool-call-id="tc.id" :clarify-submit="clarifySubmit" />
              </div>
            </div>
            <div class="assistant-segment">
              <!-- Error state -->
              <template v-if="msg.isError">
                <div class="msg-body msg-body-error">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                  <span>{{ friendlyError(msg.content) }}</span>
                </div>
                <details v-if="msg.provider_details" class="provider-error-details">
                  <summary>错误详情</summary>
                  <pre>{{ msg.provider_details }}</pre>
                </details>
                <div class="msg-foot msg-foot-error" v-if="msg._retryable">
                  <div class="msg-actions">
                    <button class="msg-action-btn msg-retry-btn" title="重试" @click="$emit('retry', msg)">
                      <LucideIcon name="refresh-cw" :size="13" /> 重试
                    </button>
                  </div>
                </div>
              </template>
              <!-- Normal content -->
              <template v-else>
                <div class="msg-body" v-html="renderMarkdown(msg.content)"></div>
                <!-- Usage / Status：耗时恒展示，tokens 仅在引擎回传 usage 时显示 -->
                <StatusCard :visible="!!msg._turnDuration || !!msg._turnUsage" :model="msg._model" :tokens="msg._turnUsage?.total_tokens" :duration="msg._turnDuration" />
              </template>
              <!-- Footer -->
              <div class="msg-foot" v-if="!msg.isError">
                <span class="msg-time" v-if="msg._ts">{{ fmtTime(msg._ts) }}</span>
                <div class="msg-actions">
                  <button class="msg-action-btn" title="跳转到提问" @click="jumpToQuestion(idx)">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
                  </button>
                  <button class="msg-action-btn msg-feedback-btn" :class="{ active: msg._feedback === 'up' }" title="赞" @click="$emit('feedback', msg, 'up')">
                    <LucideIcon name="thumbs-up" :size="14" />
                  </button>
                  <button class="msg-action-btn msg-feedback-btn" :class="{ active: msg._feedback === 'down' }" title="踩" @click="$emit('feedback', msg, 'down')">
                    <LucideIcon name="thumbs-down" :size="14" />
                  </button>
                  <button class="msg-action-btn msg-favorite-btn" :class="{ active: !!msg._favorite }" :title="msg._favorite ? '取消收藏' : '收藏'" @click="$emit('favorite', msg, !msg._favorite)">
                    <LucideIcon name="star" :size="14" />
                  </button>
                  <button class="msg-action-btn msg-copy-btn" :class="{ copied: copiedMsgIdx === idx }" :title="copiedMsgIdx === idx ? '已复制' : '复制'" @click="copyMessage(msg, idx)">
                    <svg v-if="copiedMsgIdx === idx" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 8.5 6.5 12 13 4.5"/></svg>
                    <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 12V3h9"/></svg>
                  </button>
                  <button v-if="idx === lastAssistantMsgIdx && !isStreaming" class="msg-action-btn" title="重新生成" @click="$emit('regenerate', msg)">
                    <LucideIcon name="refresh-cw" :size="13" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Live streaming turn -->
      <div v-if="isStreaming" class="msg-row assistant-turn" data-role="assistant">
        <div class="msg-role assistant">
          <span class="role-icon assistant">A</span>
          <span>{{ agentName || '智能体' }}</span>
        </div>
        <div class="assistant-turn-blocks">
          <!-- Activity feed (collapsible group) with nested thinking + tool cards -->
          <div v-if="(toolCalls || []).length > 0 || visibleEvents(activityEvents).length > 0 || !!thinkingText"
            class="tool-call-group agent-activity-group"
            :class="{ 'tool-call-group-collapsed': activityCollapsed && !hasLiveClarify }">
            <button type="button" class="tool-call-group-summary"
              :aria-expanded="!activityCollapsed || hasLiveClarify"
              @click="activityCollapsed = !activityCollapsed">
              <span class="tool-call-group-chevron">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6,4 10,8 6,12"/></svg>
              </span>
              <span class="tool-call-group-label">{{ activitySummary }}</span>
            </button>
            <!-- 折叠态"当前执行项"预览（对齐 Android CollapsedPreview）：正在跑的工具优先，
                 否则最近一条事件，不展开也能看到进行到哪一步 -->
            <div v-if="activityCollapsed && !hasLiveClarify && currentActivityPreview" class="agent-activity-preview"
              :class="{ 'agent-activity-preview-error': currentActivityPreview.error }">
              <span class="agent-activity-preview-icon">
                <span v-if="currentActivityPreview.waiting" class="tool-card-running-dot"></span>
                <svg v-else-if="currentActivityPreview.error" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              <span class="agent-activity-preview-label">{{ currentActivityPreview.label }}</span>
            </div>
            <div class="tool-call-group-body">
              <!-- 思考卡全程保留在组内（对齐 Android：回复开始流出后转 done 态折叠，可回看） -->
              <ThinkingCard v-if="!!thinkingText" :text="thinkingText" :status="(thinkingStatus as any)" :visible="true" />
              <div v-for="(ev, eidx) in visibleEvents(activityEvents)" :key="eidx"
                class="agent-activity-status"
                :class="`agent-activity-status-${ev.kind || 'info'} agent-activity-status-${ev.status || 'waiting'}`">
                <span class="agent-activity-status-icon">
                  <svg v-if="ev.kind === 'model'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>
                  <svg v-else-if="ev.kind === 'tool' && ev.status === 'done'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <svg v-else-if="ev.kind === 'tool'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                  <svg v-else-if="ev.kind === 'run'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                  <span v-else-if="ev.status === 'waiting' || ev.status === 'running'" class="tool-card-running-dot"></span>
                  <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                </span>
                <span class="agent-activity-status-copy">
                  <span class="agent-activity-status-label">{{ ev.label }}</span>
                  <span class="agent-activity-status-detail" v-if="ev.detail">{{ ev.detail }}</span>
                </span>
                <span class="agent-activity-status-time">{{ ev.ts ? fmtTimeShort(ev.ts) : '' }}</span>
              </div>
              <!-- Tool cards nested inside activity group (matching original structure) -->
              <ToolCard v-for="(tc, idx) in toolCalls" :key="idx" :name="tc.name" :status="tc.status" :result="tc.result" :preview="tc.preview" :status-label="tc.statusLabel" :tool-name="tc.toolName" :args="tc.args" :raw-result="tc.rawResult" :tool-call-id="tc.id" :clarify-submit="clarifySubmit" />
            </div>
          </div>

          <!-- Pending dots (no activity yet) -->
          <div v-if="(toolCalls || []).length === 0 && visibleEvents(activityEvents).length === 0 && !thinkingText" class="thinking">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </div>

          <!-- Assistant text body (incremental smd rendering) -->
          <div class="assistant-segment" v-show="hasStreamText">
            <div class="msg-body" :class="{ 'is-streaming': isStreaming, 'stream-fade': streamFadeEffect }" ref="streamBodyRef"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Scroll to bottom -->
    <button v-if="showScrollButton" @click="scrollToBottom" class="scroll-to-bottom-btn" aria-label="滚动到底部">
      <span aria-hidden="true">↓</span>
    </button>

    <!-- Image lightbox -->
    <ImageLightbox v-if="lightboxVisible" :images="lightboxImages" :index="lightboxIndex" @close="lightboxVisible = false" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, inject } from "vue";
import * as smd from "streaming-markdown";
import ThinkingCard from "./ThinkingCard.vue";
import ToolCard from "./ToolCard.vue";
import StatusCard from "./StatusCard.vue";
import AuthenticatedImage from "./AuthenticatedImage.vue";
import ImageLightbox from "./ImageLightbox.vue";
import LucideIcon from "../icons/LucideIcon.vue";
import { renderMarkdown, highlightCode } from "../markdown";
import { copyTextToClipboard } from "../clipboard";
import { enhanceRendered } from "../renderEnhancements";
import { stripAttachmentHint } from "../attachment";
import { normalizeWorkspacePath } from "../workspacePath";
import { chatContextKey } from "../chatContext";

const props = defineProps<{
  messages: any[];
  isStreaming: boolean;
  streamingContent: string;
  isEmpty: boolean;
  isLoadingMessages?: boolean;
  toolCalls?: any[];
  thinkingText?: string;
  thinkingStatus?: string;
  activityEvents?: any[];
  agentName?: string;
  streamFadeEffect?: boolean;
  currentSessionId?: string;
  /** 空态标题（默认「有什么可以帮你的？」）。 */
  greetingTitle?: string;
  /** 空态副标题（默认通用语）。 */
  greetingSubtitle?: string;
  /** 空态建议问句列表（默认 3 条通用问句）。 */
  suggestions?: string[];
  /** clarify 工具提交回调（透传给 ToolCard）：返回 true=已递交。 */
  clarifySubmit?: (toolCallId: string, answers: Record<string, unknown>) => Promise<boolean>;
}>();

const emit = defineEmits<{
  "send-suggestion": [text: string];
  "retry": [msg: any];
  "feedback": [msg: any, rating: "up" | "down"];
  "favorite": [msg: any, favored: boolean];
  "edit-message": [msg: any, newText: string];
  "regenerate": [msg: any];
}>();

const ctx = inject(chatContextKey, {});

// ── 空态文案默认值（宿主未传时回落到通用文案，保持 enduser 既有行为）──
const DEFAULT_GREETING_TITLE = "有什么可以帮你的？";
const DEFAULT_GREETING_SUBTITLE = "你可以提问、执行命令、浏览文件或管理定时任务。";
const DEFAULT_SUGGESTIONS = [
  "这个工作区有哪些文件？",
  "帮我规划一个小项目",
  "你能做什么？",
];
const greetingTitle = computed(() => props.greetingTitle || DEFAULT_GREETING_TITLE);
const greetingSubtitle = computed(
  () => props.greetingSubtitle ?? DEFAULT_GREETING_SUBTITLE,
);
const suggestionList = computed(() =>
  props.suggestions && props.suggestions.length ? props.suggestions : DEFAULT_SUGGESTIONS,
);

const messagesEl = ref<HTMLElement | null>(null);
const msgInnerRef = ref<HTMLElement | null>(null);
const streamBodyRef = ref<HTMLElement | null>(null);
const showScrollButton = ref(false);
const hasStreamText = ref(false);
const activityCollapsed = ref(true);
const settledActivityExpanded = ref<Record<number, boolean>>({});
const editingMsgIdx = ref(-1);
const editingText = ref("");
const editTextareaRef = ref<HTMLTextAreaElement | null>(null);

// ── Image lightbox ──
const lightboxImages = ref<{ src: string; alt: string }[]>([]);
const lightboxIndex = ref(0);
const lightboxVisible = ref(false);

function openLightbox(msg: any, clickedIdx: number) {
  const imgs: { src: string; alt: string }[] = [];
  let imgPos = 0;
  for (const att of (msg.attachments || [])) {
    const name = typeof att === "string" ? att : att.name;
    const isImg = typeof att === "string" ? isImageExt(name) : att.is_image;
    if (isImg) {
      const src = (typeof att === "object" && att.blobUrl) ? att.blobUrl : "";
      if (src) {
        if (imgs.length === imgPos && imgPos === clickedIdx) {
          // current
        }
        imgs.push({ src, alt: name });
      }
      imgPos++;
    }
  }
  if (imgs.length > 0) {
    let imgCount = 0;
    let targetIdx = 0;
    for (let i = 0; i <= clickedIdx && i < (msg.attachments || []).length; i++) {
      const att = msg.attachments[i];
      const name = typeof att === "string" ? att : att.name;
      const isImg = typeof att === "string" ? isImageExt(name) : att.is_image;
      if (isImg) {
        if (i === clickedIdx) { targetIdx = imgCount; break }
        imgCount++;
      }
    }
    lightboxImages.value = imgs;
    lightboxIndex.value = targetIdx;
    lightboxVisible.value = true;
  }
}

const IMAGE_EXTS_MSG = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"];
function isImageExt(name: string): boolean {
  const e = name.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  return IMAGE_EXTS_MSG.includes(e);
}
function attHasBlobUrl(att: any): boolean {
  return typeof att === "object" && !!att.blobUrl;
}
function attIsImage(att: any): boolean {
  if (typeof att === "string") return isImageExt(att);
  return !!att.is_image;
}
function attName(att: any): string {
  return typeof att === "string" ? att : (att.name || att.path || "");
}
function attPath(att: any): string {
  return typeof att === "string" ? att : (att.path || att.name || "");
}

// ── Render windowing ──
const RENDER_WINDOW = 50;
const renderCount = ref(RENDER_WINDOW);
let _prevMsgLen = 0;

const renderedMessages = computed(() => {
  const msgs = props.messages || [];
  const start = Math.max(0, msgs.length - renderCount.value);
  return msgs.slice(start).map((msg, idx) => ({ msg, idx: start + idx }));
});

const lastUserMsgIdx = computed(() => {
  const msgs = props.messages || [];
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "user") return i;
  }
  return -1;
});

const lastAssistantMsgIdx = computed(() => {
  const msgs = props.messages || [];
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "assistant" && !msgs[i].isError) return i;
  }
  return -1;
});

// 工具生命周期只由 ToolCard 表达（对齐 Android，feed 里不出 "Tool finished" 行）；
// 过滤历史快照里残留的 kind=tool 事件
function visibleEvents(events: any[] | undefined): any[] {
  return (events || []).filter((e: any) => e.kind !== 'tool');
}

// 组合式摘要（对齐 Android IntermediateProcess）：已思考/思考中 · N 个工具 · M 条过程；
// 当前正在执行什么由折叠态的 currentActivityPreview 预览行承担
const activitySummary = computed(() => {
  const parts: string[] = [];
  if (props.thinkingText) parts.push(props.thinkingStatus === 'thinking' ? '思考中' : '已思考');
  const toolCount = (props.toolCalls || []).length;
  if (toolCount > 0) parts.push(`${toolCount} 个工具`);
  const eventCount = visibleEvents(props.activityEvents).length;
  if (eventCount > 0) parts.push(`${eventCount} 条过程`);
  return parts.join(' · ') || '活动';
});

// 折叠态"当前执行项"（对齐 Android CollapsedPreview）：正在跑的工具优先，否则最近一条事件
const currentActivityPreview = computed(() => {
  const runningTool = (props.toolCalls || []).find((t: any) => t.status === 'running');
  if (runningTool) {
    const pv = (runningTool.preview || '').trim().replace(/\s+/g, ' ');
    return { waiting: true, error: false, label: runningTool.name + (pv ? ` · ${pv}` : '') };
  }
  const events = visibleEvents(props.activityEvents);
  const latest = events.length > 0 ? events[events.length - 1] : null;
  if (latest) {
    const detail = (latest.detail || '').trim().replace(/\s+/g, ' ');
    return {
      waiting: latest.status === 'waiting' || latest.status === 'running',
      error: latest.status === 'error',
      label: latest.label + (detail ? ` · ${detail}` : ''),
    };
  }
  if (props.thinkingText && props.thinkingStatus === 'thinking') {
    return { waiting: true, error: false, label: '思考中' };
  }
  return null;
});

function toggleSettledActivity(idx: number) {
  settledActivityExpanded.value = { ...settledActivityExpanded.value, [idx]: !settledExpanded(idx, renderedMessages.value[idx]?.msg) };
}

/** clarify 工具含交互表单/只读回显，默认展开（其余工具组默认折叠）。 */
function msgHasClarify(msg: any): boolean {
  return (msg?._toolCalls || []).some((tc: any) => tc?.toolName === "clarify");
}
function settledExpanded(idx: number, msg: any): boolean {
  const v = settledActivityExpanded.value[idx];
  if (v !== undefined) return v;
  return msgHasClarify(msg);
}
/** 流式回合里有 running 的 clarify 时，活动组强制展开（表单需可见可填写）。 */
const hasLiveClarify = computed(() =>
  (props.toolCalls || []).some((tc: any) => tc?.toolName === "clarify" && tc?.status === "running")
);

function settledActivitySummary(msg: any): string {
  const parts: string[] = [];
  if (msg._thinkingText) parts.push('已思考');
  const toolCount = (msg._toolCalls || []).length;
  if (toolCount > 0) parts.push(`${toolCount} 个工具`);
  const eventCount = visibleEvents(msg._activityEvents).length;
  if (eventCount > 0) parts.push(`${eventCount} 条过程`);
  return parts.join(' · ') || '活动';
}

// ── Incremental smd streaming parser ──
let _smdParser: any = null;
let _smdWrittenText = "";

function initStreamParser() {
  if (!streamBodyRef.value) return;
  streamBodyRef.value.innerHTML = "";
  const renderer = smd.default_renderer(streamBodyRef.value);
  _smdParser = smd.parser(renderer);
  _smdWrittenText = "";
}

function writeStreamDelta(fullText: string) {
  if (!_smdParser) {
    initStreamParser();
    if (!_smdParser) return;
  }
  if (_smdWrittenText && !fullText.startsWith(_smdWrittenText)) {
    initStreamParser();
  }
  const delta = fullText.slice(_smdWrittenText.length);
  if (delta) {
    smd.parser_write(_smdParser, delta);
    _smdWrittenText = fullText;
  }
}

function endStreamParser() {
  if (!_smdParser) return;
  smd.parser_end(_smdParser);
  if (streamBodyRef.value) {
    streamBodyRef.value.querySelectorAll("pre").forEach((pre) => {
      if (pre.parentElement?.classList.contains("code-block")) return;
      const code = pre.querySelector("code");
      const lang = (code?.className.match(/language-([\w+-]+)/) || [])[1] || "";
      const wrap = document.createElement("div");
      wrap.className = "code-block";
      const header = document.createElement("div");
      header.className = "code-block-header";
      const langSpan = document.createElement("span");
      langSpan.className = "code-lang";
      langSpan.textContent = lang;
      const copyBtn = document.createElement("button");
      copyBtn.className = "code-copy-btn";
      copyBtn.type = "button";
      copyBtn.textContent = "复制";
      header.appendChild(langSpan);
      header.appendChild(copyBtn);
      wrap.appendChild(header);
      pre.parentNode!.insertBefore(wrap, pre);
      wrap.appendChild(pre);
    });
    highlightCode(streamBodyRef.value);
    enhanceRendered(streamBodyRef.value, ctx.imageResolver);
  }
  _smdParser = null;
  _smdWrittenText = "";
}

watch(() => props.streamingContent, (val) => {
  if (val) {
    hasStreamText.value = true;
    writeStreamDelta(val);
  }
});

watch(() => props.isStreaming, (s) => {
  if (!s) {
    endStreamParser();
    hasStreamText.value = false;
  } else {
    // 新一轮回复开始：强制回到底部并恢复跟随——用户上一轮上翻浏览过历史，
    // unpin 状态不能带到这一轮，否则本轮流式全程不自动下滚
    _scrollPinned = true;
    _messageUserUnpinned = false;
    showScrollButton.value = false;
    nextTick(() => scrollIfPinned());
  }
});

watch(() => (props.messages || []).length, async (newLen) => {
  if (newLen < _prevMsgLen) {
    renderCount.value = RENDER_WINDOW;
  }
  _prevMsgLen = newLen;
  await nextTick();
  highlightCode(msgInnerRef.value);
  enhanceRendered(msgInnerRef.value, ctx.imageResolver);
});

// ── Smart scroll (sticky-unpin model) ──
let _scrollPinned = true;
let _messageUserUnpinned = false;
let _nearBottomCount = 0;
let _programmaticScroll = false;

function scrollIfPinned() {
  if (!_scrollPinned || _messageUserUnpinned) return;
  const el = messagesEl.value;
  if (!el) return;
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  _programmaticScroll = true;
  el.scrollTop = el.scrollHeight;
  setTimeout(() => { el.scrollTop = el.scrollHeight }, 16);
  if (dist > 200) setTimeout(() => { el.scrollTop = el.scrollHeight }, 80);
  setTimeout(() => { _programmaticScroll = false }, 200);
}

// Auto scroll on new messages, streaming content, or 中间过程（思考/工具/事件）增长
watch([
  () => (props.messages || []).length,
  () => props.streamingContent,
  () => (props.activityEvents || []).length,
  () => (props.toolCalls || []).length,
  () => props.thinkingText,
], async () => {
  await nextTick();
  scrollIfPinned();
});

let _lastScrollTop = 0;
function checkScroll() {
  const el = messagesEl.value;
  if (!el) return;
  if (_programmaticScroll) { _lastScrollTop = el.scrollTop; return; }
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  // 只有"向上滚动"才视为用户主动取消跟随。程序性回底的 scroll 事件若延迟送达
  // （流式期间帧压力大时可能晚于 _programmaticScroll 窗口），方向恒为向下，
  // 不会被误判成用户上翻而永久停掉自动滚动。
  const goingUp = el.scrollTop < _lastScrollTop - 1;
  _lastScrollTop = el.scrollTop;
  if (dist > 80) {
    if (goingUp && !_messageUserUnpinned) {
      _messageUserUnpinned = true;
      _scrollPinned = false;
    }
    showScrollButton.value = true;
    _nearBottomCount = 0;
  } else {
    _nearBottomCount++;
    if (_nearBottomCount >= 2 || dist <= 8) {
      _scrollPinned = true;
      _messageUserUnpinned = false;
      showScrollButton.value = false;
    }
  }
}

function scrollToBottom() {
  _scrollPinned = true;
  _messageUserUnpinned = false;
  _nearBottomCount = 0;
  showScrollButton.value = false;
  const el = messagesEl.value;
  if (el) {
    _programmaticScroll = true;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setTimeout(() => { _programmaticScroll = false }, 300);
  }
}

function onWheel(e: WheelEvent) {
  if (e.deltaY < 0 && !_messageUserUnpinned) {
    _messageUserUnpinned = true;
    _scrollPinned = false;
  }
}

let _lastTouchY = 0;
function onTouchMove(e: TouchEvent) {
  const t = e.touches[0];
  if (!t) return;
  if (_lastTouchY === 0) { _lastTouchY = t.clientY; return }
  const dy = t.clientY - _lastTouchY;
  _lastTouchY = t.clientY;
  if (dy > 0 && !_messageUserUnpinned) {
    _messageUserUnpinned = true;
    _scrollPinned = false;
  }
}
function onTouchEnd() {
  _lastTouchY = 0;
}

// ── Date separators ──
function isDifferentDay(ts1?: number, ts2?: number): boolean {
  if (!ts1 || !ts2) return false;
  const d1 = new Date(ts1 * 1000);
  const d2 = new Date(ts2 * 1000);
  return d1.toDateString() !== d2.toDateString();
}

function dateSepLabel(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "今天";
  if (d.toDateString() === yesterday.toDateString()) return "昨天";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function shouldShowDateSep(idx: number): boolean {
  if (idx === 0) return true;
  const prev = props.messages[idx - 1];
  const curr = props.messages[idx];
  return isDifferentDay(prev?._ts, curr?._ts);
}

// Copy message（HTTP 环境 navigator.clipboard 不存在，走降级实现）
const copiedMsgIdx = ref(-1);
let _copiedTimer: ReturnType<typeof setTimeout> | null = null;
async function copyMessage(msg: any, idx: number) {
  const text = msg.role === "user" ? stripAttachmentHint(msg.content) : msg.content;
  const ok = await copyTextToClipboard(text);
  if (!ok) return;
  copiedMsgIdx.value = idx;
  if (_copiedTimer) clearTimeout(_copiedTimer);
  _copiedTimer = setTimeout(() => { copiedMsgIdx.value = -1 }, 1500);
}

function startEdit(msg: any, idx: number) {
  editingMsgIdx.value = idx;
  editingText.value = stripAttachmentHint(msg.content);
  nextTick(() => {
    const ta = editTextareaRef.value;
    if (ta instanceof HTMLTextAreaElement) {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 300) + "px";
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  });
}
function cancelEdit() {
  editingMsgIdx.value = -1;
  editingText.value = "";
}
function submitEdit() {
  const text = editingText.value.trim();
  const idx = editingMsgIdx.value;
  cancelEdit();
  if (text && idx >= 0) {
    emit("edit-message", props.messages[idx], text);
  }
}
function onEditKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitEdit();
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelEdit();
  }
}
function autoResizeEdit(e: Event) {
  const ta = e.target as HTMLTextAreaElement;
  if (!ta) return;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 300) + "px";
}
function jumpToQuestion(msgIdx: number) {
  for (let i = msgIdx - 1; i >= 0; i--) {
    if (props.messages[i].role === "user") {
      const el = messagesEl.value?.querySelector(`[data-msg-idx="${i}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
  }
}

function friendlyError(msg: string): string {
  if (!msg) return '请求失败';
  const m = msg.toLowerCase();
  if (m.includes('failed to fetch') || m.includes('networkerror') || m.includes('network error')) return '连接断开，请检查网络后重试';
  if (m.includes('abort') || m.includes('cancel')) return '请求已取消';
  if (m.includes('timeout') || m.includes('timed out')) return '请求超时，请重试';
  if (m.includes('500')) return '服务器错误，请稍后重试';
  if (m.includes('401') || m.includes('unauthorized')) return '认证失败，请重新登录';
  if (m.includes('429') || m.includes('rate limit')) return '请求频率限制，请稍后重试';
  if (m.includes('404')) return '资源不存在';
  return msg;
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}
function fmtTimeShort(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
}

async function onCodeCopyClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement)?.closest('.code-copy-btn') as HTMLButtonElement | null;
  if (!btn) return;
  const block = btn.closest('.code-block');
  const code = block?.querySelector('pre')?.textContent || '';
  const ok = await copyTextToClipboard(code);
  if (ok) {
    btn.textContent = '已复制';
    btn.classList.add('copied');
  } else {
    btn.textContent = '复制失败';
  }
  setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied') }, 1500);
}

function onAgentImgClick(e: MouseEvent) {
  const img = (e.target as HTMLElement)?.closest?.("img.msg-media-img") as HTMLImageElement | null;
  if (!img || !img.src) return;
  if (!img.src.startsWith("data:") && !img.src.startsWith("blob:")) return;
  lightboxImages.value = [{ src: img.src, alt: img.alt || "" }];
  lightboxIndex.value = 0;
  lightboxVisible.value = true;
}

async function onFileLinkClick(e: MouseEvent) {
  const a = (e.target as HTMLElement)?.closest?.("a") as HTMLAnchorElement | null;
  if (!a) return;
  const rawHref = a.getAttribute("href") || "";
  if (!rawHref) return;
  if (/^(https?:|data:|blob:|mailto:|#|\/\/)/i.test(rawHref)) return;
  if (!ctx.fileDownloader) return;
  e.preventDefault();
  const origText = a.textContent;
  a.textContent = "下载中…";
  try {
    let href = rawHref;
    try { href = decodeURIComponent(rawHref) } catch { /* 含非法 % 序列时保持原值 */ }
    await ctx.fileDownloader(normalizeWorkspacePath(href));
  } catch (err) {
    console.error("下载文件失败:", err);
    a.textContent = "下载失败，点击重试";
    setTimeout(() => { a.textContent = origText }, 2000);
    return;
  }
  a.textContent = origText;
}

onMounted(() => {
  messagesEl.value?.addEventListener("scroll", checkScroll);
  messagesEl.value?.addEventListener("click", onCodeCopyClick);
  messagesEl.value?.addEventListener("click", onAgentImgClick);
  messagesEl.value?.addEventListener("click", onFileLinkClick);
  messagesEl.value?.addEventListener("wheel", onWheel, { passive: true });
  messagesEl.value?.addEventListener("touchmove", onTouchMove, { passive: true });
  messagesEl.value?.addEventListener("touchend", onTouchEnd, { passive: true });
  messagesEl.value?.addEventListener("touchstart", onTouchEnd, { passive: true });
  nextTick(() => {
    highlightCode(msgInnerRef.value);
    enhanceRendered(msgInnerRef.value, ctx.imageResolver);
  });
});
</script>
