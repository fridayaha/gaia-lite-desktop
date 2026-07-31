<template>
  <div class="tool-card-row">
    <div class="agent-activity-status agent-activity-tool" :class="{ open: expanded }">
      <div
        class="agent-activity-tool-row"
        :class="{ clickable: hasDetail }"
        @click="hasDetail ? (expanded = !expanded) : undefined"
      >
        <span class="agent-activity-status-icon">
          <span v-if="status === 'running'" class="tool-card-running-dot"></span>
          <svg v-else-if="status === 'error'" class="tool-status-icon tool-status-error" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <svg v-else class="tool-status-icon tool-status-done" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        </span>
        <span class="tool-card-icon" v-html="toolIcon"></span>
        <span class="tool-card-name">{{ effectiveToolName }}</span>
        <span class="tool-card-preview" :title="summary">{{ truncatedPreview }}</span>
        <span class="tool-card-toggle" v-if="hasDetail" :class="{ open: expanded }">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
        </span>
      </div>

      <div class="tool-card-detail" v-if="expanded && hasDetail">
        <!-- bash：命令 + 输出 -->
        <template v-if="effectiveToolName === 'bash'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">命令</span>
            <pre class="tool-card-code cmd">{{ args?.command ?? "" }}</pre>
          </div>
          <div v-if="!isRunning" class="tool-card-kv">
            <span class="tool-card-kv-label">输出</span>
            <pre v-if="outputText" class="tool-card-code out">{{ outputText }}</pre>
            <span v-else class="tool-card-muted">无输出</span>
          </div>
        </template>

        <!-- write：路径 + 内容 -->
        <template v-else-if="effectiveToolName === 'write'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">路径</span>
            <code class="tool-card-path">{{ writePath }}</code>
          </div>
          <pre v-if="writeContent" class="tool-card-code out">{{ writeContent }}</pre>
        </template>

        <!-- edit：路径 + diff -->
        <template v-else-if="effectiveToolName === 'edit'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">路径</span>
            <code class="tool-card-path">{{ writePath }}</code>
          </div>
          <div v-if="editDiffLines.length" class="tool-card-kv">
            <span class="tool-card-kv-label">改动</span>
            <pre class="tool-card-code diff"><span
              v-for="(l, i) in editDiffLines"
              :key="i"
              class="tool-card-dl"
              :class="lineClass(l)"
            >{{ l || " " }}</span></pre>
          </div>
        </template>

        <!-- grep：pattern + path + 匹配 -->
        <template v-else-if="effectiveToolName === 'grep'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">匹配</span>
            <code class="tool-card-path">{{ grepMeta.pattern }}</code>
          </div>
          <div v-if="grepMeta.path" class="tool-card-kv">
            <span class="tool-card-kv-label">路径</span>
            <code class="tool-card-path">{{ grepMeta.path }}</code>
          </div>
          <div v-if="!isRunning" class="tool-card-kv">
            <span class="tool-card-kv-label">输出</span>
            <pre v-if="outputText" class="tool-card-code out">{{ outputText }}</pre>
            <span v-else class="tool-card-muted">无输出</span>
          </div>
        </template>

        <!-- read：路径 + 内容预览 -->
        <template v-else-if="effectiveToolName === 'read'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">路径</span>
            <code class="tool-card-path">{{ writePath }}</code>
          </div>
          <pre v-if="outputText" class="tool-card-code out">{{ outputText }}</pre>
        </template>

        <!-- ls：路径 + 列出项 -->
        <template v-else-if="effectiveToolName === 'ls'">
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">路径</span>
            <code class="tool-card-path">{{ writePath }}</code>
          </div>
          <div v-if="!isRunning" class="tool-card-kv">
            <span class="tool-card-kv-label">输出</span>
            <pre v-if="outputText" class="tool-card-code out">{{ outputText }}</pre>
            <span v-else class="tool-card-muted">无输出</span>
          </div>
        </template>

        <!-- clarify：需求澄清问卷（running=可填写表单，done/error=只读回显） -->
        <template v-else-if="effectiveToolName === 'clarify'">
          <div v-if="clarifyArgs.title" class="clarify-title">{{ clarifyArgs.title }}</div>
          <p v-if="clarifyArgs.intro" class="clarify-intro">{{ clarifyArgs.intro }}</p>

          <!-- 运行中：交互表单 -->
          <div v-if="isClarifyForm" class="clarify-form">
            <div
              v-for="q in clarifyQuestions"
              :key="q.id"
              class="clarify-q"
              :class="{ required: q.required && missingSet.has(q.id) }"
            >
              <div class="clarify-q-text">
                {{ q.text }}
                <span v-if="q.required" class="clarify-req-dot">*</span>
              </div>

              <!-- text -->
              <input
                v-if="q.type === 'text'"
                v-model="formText[q.id]"
                type="text"
                class="clarify-input"
                :placeholder="q.placeholder || ''"
                :disabled="submitting || submitted"
              />

              <!-- single -->
              <template v-else-if="q.type === 'single'">
                <div class="clarify-choices">
                  <label v-for="opt in q.options || []" :key="opt" class="clarify-choice">
                    <input
                      type="radio"
                      :name="'clq-' + q.id"
                      :value="opt"
                      v-model="formSingle[q.id]"
                      :disabled="submitting || submitted"
                    />
                    <span>{{ opt }}</span>
                  </label>
                  <label v-if="q.allowOther" class="clarify-choice">
                    <input
                      type="radio"
                      :name="'clq-' + q.id"
                      :value="OTHER"
                      v-model="formSingle[q.id]"
                      :disabled="submitting || submitted"
                    />
                    <span>其他</span>
                  </label>
                </div>
                <input
                  v-if="q.allowOther && formSingle[q.id] === OTHER"
                  v-model="otherText[q.id]"
                  type="text"
                  class="clarify-input clarify-other-input"
                  placeholder="请输入"
                  :disabled="submitting || submitted"
                />
              </template>

              <!-- multi -->
              <template v-else-if="q.type === 'multi'">
                <div class="clarify-choices">
                  <label v-for="opt in q.options || []" :key="opt" class="clarify-choice">
                    <input
                      type="checkbox"
                      :value="opt"
                      v-model="formMulti[q.id]"
                      :disabled="submitting || submitted"
                    />
                    <span>{{ opt }}</span>
                  </label>
                  <label v-if="q.allowOther" class="clarify-choice">
                    <input
                      type="checkbox"
                      :value="OTHER"
                      v-model="formMulti[q.id]"
                      :disabled="submitting || submitted"
                    />
                    <span>其他</span>
                  </label>
                </div>
                <input
                  v-if="q.allowOther && (Array.isArray(formMulti[q.id]) ? formMulti[q.id] : []).includes(OTHER)"
                  v-model="otherText[q.id]"
                  type="text"
                  class="clarify-input clarify-other-input"
                  placeholder="请输入"
                  :disabled="submitting || submitted"
                />
              </template>

              <!-- confirm -->
              <div v-else-if="q.type === 'confirm'" class="clarify-choices">
                <label class="clarify-choice">
                  <input
                    type="radio"
                    :name="'clq-' + q.id"
                    :value="true"
                    v-model="formConfirm[q.id]"
                    :disabled="submitting || submitted"
                  />
                  <span>是</span>
                </label>
                <label class="clarify-choice">
                  <input
                    type="radio"
                    :name="'clq-' + q.id"
                    :value="false"
                    v-model="formConfirm[q.id]"
                    :disabled="submitting || submitted"
                  />
                  <span>否</span>
                </label>
              </div>

              <div v-if="q.required && missingSet.has(q.id)" class="clarify-req-hint">
                此题为必填
              </div>
            </div>

            <div class="clarify-actions">
              <span v-if="submitted" class="clarify-submitted-hint">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                已提交，等待处理
              </span>
              <button
                v-else
                type="button"
                class="clarify-submit-btn"
                :disabled="submitting || submitted"
                @click="onSubmitClarify"
              >
                {{ submitting ? "提交中…" : "提交" }}
              </button>
            </div>
          </div>

          <!-- 已结束：只读回显 -->
          <div v-else class="clarify-readonly">
            <div v-for="q in clarifyQuestions" :key="q.id" class="clarify-q">
              <div class="clarify-q-text">{{ q.text }}</div>
              <div class="clarify-q-answer">{{ answerForDisplay(q) }}</div>
            </div>
            <div v-if="clarifyQuestions.length === 0" class="tool-card-muted">
              {{ JSON.stringify(rawResult ?? result, null, 2) }}
            </div>
          </div>
        </template>

        <!-- default / 未知工具：JSON -->
        <template v-else>
          <div class="tool-card-kv">
            <span class="tool-card-kv-label">参数</span>
            <pre class="tool-card-code out">{{ JSON.stringify(args ?? null, null, 2) }}</pre>
          </div>
          <div v-if="!isRunning" class="tool-card-kv">
            <span class="tool-card-kv-label">结果</span>
            <pre class="tool-card-code out">{{ jsonResult }}</pre>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watchEffect } from "vue";
import {
  extractResultText,
  extractEditDiff,
  toolSummary,
  diffLineKind,
  editsToDiffLines,
  type DiffLineKind,
} from "../toolCallRender";

/**
 * ToolCard — 单次工具调用的可折叠卡片（共享版）。
 *
 * 双形态：
 *  - 简单形态（enduser）：仅 name + status + result(string) + preview，`<pre>` 显示。
 *  - 丰富形态（admin skill-engine）：传 toolName/args/rawResult，按工具分形态渲染
 *    （bash 命令+输出、write 路径+内容、edit 路径+diff 着色、grep 匹配、read 路径、
 *    ls 列出、clarify 问卷表单、default JSON）。
 *
 * 不依赖 Element Plus / vue-i18n / ~icons：clarify 表单用原生 HTML 控件 + CSS 变量，
 * 图标内联 SVG，文案中文硬编码（与共享包其它组件一致）。
 */
const props = defineProps<{
  name: string;
  status: "running" | "success" | "error";
  /** 简单形态：结果文本（enduser）。 */
  result?: string;
  statusLabel?: string;
  preview?: string;
  /** 丰富形态：工具名（默认取 name）。 */
  toolName?: string;
  args?: Record<string, unknown> | null;
  /** 丰富形态：结构化 AgentToolResult（含 content/details，用于 diff/bash 输出提取）。 */
  rawResult?: unknown;
  /** clarify 提交所需：工具调用 id。 */
  toolCallId?: string;
  /** clarify 提交回调：返回 true=已递交（卡进入「已提交」态）。 */
  clarifySubmit?: (toolCallId: string, answers: Record<string, unknown>) => Promise<boolean>;
}>();

const effectiveToolName = computed(() => props.toolName || props.name);
const isRunning = computed(() => props.status === "running");

const RICH_TOOLS = new Set(["bash", "write", "edit", "grep", "read", "ls", "clarify"]);
const isRich = computed(() => RICH_TOOLS.has(effectiveToolName.value));

const summary = computed(() =>
  isRich.value ? toolSummary(effectiveToolName.value, props.args ?? null) : (props.preview || "")
);

const truncatedPreview = computed(() => {
  const p = summary.value || "";
  if (p.length <= 120) return p;
  const cut = p.slice(0, 120);
  const lastBreak = Math.max(cut.lastIndexOf(" "), cut.lastIndexOf("\n"), cut.lastIndexOf(";"));
  return (lastBreak > 40 ? cut.slice(0, lastBreak) : cut) + "…";
});

// ── 各形态派生数据 ──
const outputText = computed(() => extractResultText(props.rawResult ?? props.result));

const editDiffLines = computed<string[]>(() => {
  const diff = extractEditDiff(props.rawResult);
  if (diff) return diff.split("\n");
  const edits = props.args?.edits;
  return editsToDiffLines(edits);
});

const writeContent = computed(() => {
  const c = props.args?.content;
  return typeof c === "string" ? c : "";
});
const writePath = computed(() => {
  const p = props.args?.path;
  return typeof p === "string" ? p : "";
});
const grepMeta = computed(() => ({
  pattern: typeof props.args?.pattern === "string" ? props.args.pattern : "",
  path: typeof props.args?.path === "string" ? props.args.path : "",
}));

const jsonResult = computed(() =>
  JSON.stringify(props.rawResult ?? props.result ?? null, null, 2)
);

// 有无展开内容：clarify 恒展开（表单），其余需要有 args/result 文本
const hasDetail = computed(() => {
  if (effectiveToolName.value === "clarify") return true;
  if (isRich.value) {
    return !!props.args || !!outputText.value || !!editDiffLines.value.length || !!writeContent.value;
  }
  return !!(props.result && props.result.length > 0);
});

// clarify 卡默认展开（交互表单，不是可折叠日志）
const expanded = ref(effectiveToolName.value === "clarify" ? true : false);

const toolIcon = computed(() => {
  const n = effectiveToolName.value;
  if (n === "bash") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
  if (n === "write" || n === "edit") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22h6a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v10"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10.4 19.4 14 16l-4-1 .4 4.4z"/><path d="m14 16 1.5-1.5a2.12 2.12 0 0 1 3 3L17 19"/></svg>';
  if (n === "read") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
  if (n === "ls") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  if (n === "grep") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
  if (n === "clarify") return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  // default wrench
  return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>';
});

function lineClass(line: string): DiffLineKind {
  return diffLineKind(line);
}

// ── clarify 问卷 ──
type ClarifyQType = "text" | "single" | "multi" | "confirm";
interface ClarifyQuestion {
  id: string;
  text: string;
  type: ClarifyQType;
  options?: string[];
  placeholder?: string;
  required?: boolean;
  allowOther?: boolean;
}
interface ClarifyArgs {
  title?: string;
  intro?: string;
  questions?: ClarifyQuestion[];
}
type ClarifyAnswers = Record<string, string | string[] | boolean>;

const OTHER = "__other__";

const clarifyArgs = computed<ClarifyArgs>(() => {
  const a = props.args as unknown;
  return a && typeof a === "object" ? (a as ClarifyArgs) : {};
});
const clarifyQuestions = computed<ClarifyQuestion[]>(() => clarifyArgs.value.questions ?? []);
const clarifyAnswers = computed<ClarifyAnswers>(() => {
  const r = props.rawResult as { details?: { answers?: ClarifyAnswers } } | null | undefined;
  return r?.details?.answers ?? {};
});

const formText = reactive<Record<string, string>>({});
const formSingle = reactive<Record<string, string>>({});
const formMulti = reactive<Record<string, string[]>>({});
const formConfirm = reactive<Record<string, boolean>>({});
const otherText = reactive<Record<string, string>>({});

// 初始化表单状态：multi 题的 v-model 必须是数组（否则 Vue 把 checkbox 退化成布尔，
// (formMulti[id] || []).includes(...) 会因 truthy 布尔调 .includes 抛错）。
// single/text/confirm 也给默认值，避免 undefined。
watchEffect(() => {
  for (const q of clarifyQuestions.value) {
    if (q.type === "multi" && !Array.isArray(formMulti[q.id])) formMulti[q.id] = [];
    else if (q.type === "single" && formSingle[q.id] === undefined) formSingle[q.id] = "";
    else if (q.type === "text" && formText[q.id] === undefined) formText[q.id] = "";
    else if (q.type === "confirm" && formConfirm[q.id] === undefined) formConfirm[q.id] = false;
  }
});

const submitting = ref(false);
const submitted = ref(false);
const isClarifyForm = computed(
  () => effectiveToolName.value === "clarify" && props.status === "running"
);

function answerForDisplay(q: ClarifyQuestion): string {
  const v = clarifyAnswers.value[q.id];
  if (v === undefined || v === null || v === "") return "未回答";
  if (Array.isArray(v)) return v.length ? v.join("、") : "未回答";
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}

function collectAnswers(): ClarifyAnswers {
  const out: ClarifyAnswers = {};
  for (const q of clarifyQuestions.value) {
    if (q.type === "text") {
      out[q.id] = formText[q.id] ?? "";
    } else if (q.type === "single") {
      const sel = formSingle[q.id];
      out[q.id] = sel === OTHER ? (otherText[q.id] ?? "") : (sel ?? "");
    } else if (q.type === "multi") {
      const sel = formMulti[q.id] ?? [];
      const picked = sel.filter((s) => s !== OTHER);
      if (sel.includes(OTHER) && (otherText[q.id] ?? "").trim()) {
        picked.push(otherText[q.id].trim());
      }
      out[q.id] = picked;
    } else if (q.type === "confirm") {
      out[q.id] = formConfirm[q.id] ?? false;
    }
  }
  return out;
}

function missingRequired(): ClarifyQuestion[] {
  return clarifyQuestions.value.filter((q) => {
    if (!q.required) return false;
    if (q.type === "text") return !(formText[q.id] ?? "").trim();
    if (q.type === "single") {
      const sel = formSingle[q.id];
      if (!sel) return true;
      return sel === OTHER && !(otherText[q.id] ?? "").trim();
    }
    if (q.type === "multi") {
      const sel = formMulti[q.id] ?? [];
      const picked = sel.filter((s) => s !== OTHER);
      if (sel.includes(OTHER) && (otherText[q.id] ?? "").trim()) picked.push("x");
      return picked.length === 0;
    }
    if (q.type === "confirm") return formConfirm[q.id] === undefined;
    return false;
  });
}

const missingSet = ref<Set<string>>(new Set());

async function onSubmitClarify() {
  if (submitting.value || submitted.value) return;
  if (!props.clarifySubmit || !props.toolCallId) return;
  const missing = missingRequired();
  if (missing.length) {
    missingSet.value = new Set(missing.map((q) => q.id));
    return;
  }
  missingSet.value = new Set();
  submitting.value = true;
  try {
    const ok = await props.clarifySubmit(props.toolCallId, collectAnswers());
    submitting.value = false;
    if (ok) submitted.value = true;
  } catch {
    submitting.value = false;
  }
}
</script>
