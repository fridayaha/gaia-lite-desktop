<template>
  <div class="ua-file-browser">
    <!-- 树 -->
    <div class="fb-tree">
      <div class="fb-tree-head">
        <span class="fb-tree-title">{{ title || "文件" }}</span>
        <div class="fb-tree-actions">
          <button v-if="editable" type="button" class="fb-icon-btn" title="新建文件" @click="onNewFile">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </button>
          <button v-if="editable" type="button" class="fb-icon-btn" title="上传文件" @click="onUploadClick">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          </button>
          <button type="button" class="fb-icon-btn" title="刷新" :disabled="loading" @click="refresh">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <input v-if="editable" ref="fileInputRef" type="file" class="fb-hidden-input" @change="onFileSelected" />
      </div>
      <div class="fb-tree-body">
        <div v-if="loading && !rootEntries.length" class="fb-empty">加载中…</div>
        <div v-else-if="!visibleNodes.length" class="fb-empty">工作区为空</div>
        <div v-else class="fb-tree-list">
          <div
            v-for="node in visibleNodes"
            :key="node.path"
            class="fb-node"
            :class="{ 'is-dir': node.isDir, 'is-file': !node.isDir, 'is-selected': selectedPath === node.path }"
            :style="{ paddingLeft: 6 + node.depth * 14 + 'px' }"
            :title="node.path"
            @click="onItemClick(node)"
          >
            <span class="fb-toggle" v-if="node.isDir">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline v-if="expandedDirs.has(node.path)" points="6 9 12 15 18 9"/><polyline v-else points="9 6 15 12 9 18"/></svg>
            </span>
            <span class="fb-toggle-placeholder" v-else></span>
            <span class="fb-node-icon" :class="{ 'is-dir-icon': node.isDir }">
              <LucideIcon :name="fileIconName(node.name, node.isDir)" :size="14" />
            </span>
            <span class="fb-node-label">{{ node.name }}</span>
            <span v-if="!node.isDir && changedFiles && changedFiles[node.path]" class="fb-change-dot" :class="changedFiles[node.path]" :title="changedFiles[node.path] === 'write' ? '新增' : '已修改'"></span>
            <span v-if="!node.isDir && node.size" class="fb-node-size">{{ formatSize(node.size) }}</span>
            <span v-if="node.isDir && loadingDirs.has(node.path)" class="fb-mini-loading">…</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 预览 / 编辑器 -->
    <div class="fb-preview">
      <template v-if="!selectedPath">
        <span class="fb-preview-empty">选择左侧文件查看内容</span>
      </template>
      <template v-else-if="previewLoading">
        <span class="fb-preview-empty">加载中…</span>
      </template>
      <template v-else-if="fileRead?.isImage && fileRead?.contentB64">
        <div class="fb-preview-bar"><span class="fb-path">{{ selectedPath }}</span></div>
        <div class="fb-preview-image-wrap">
          <img :src="`data:image/*;base64,${fileRead.contentB64}`" :alt="selectedPath" />
        </div>
      </template>
      <template v-else-if="isBinary">
        <div class="fb-preview-bar"><span class="fb-path">{{ selectedPath }}</span></div>
        <span class="fb-preview-empty">二进制文件不支持预览</span>
      </template>
      <template v-else>
        <div class="fb-preview-bar">
          <span class="fb-path">{{ selectedPath }}</span>
          <button
            v-if="editable"
            type="button"
            class="fb-save-btn"
            :disabled="!dirty || saving"
            @click="onSave"
          >{{ saving ? "保存中…" : "保存" }}</button>
        </div>
        <Codemirror
          v-model="editContent"
          :extensions="editorExtensions"
          :editable="editable"
          :style="{ height: 'calc(100% - 30px)' }"
          class="fb-editor"
        />
        <div v-if="fileRead?.truncated" class="fb-truncated">内容过大，已截断预览</div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from "vue";
import { Codemirror } from "vue-codemirror";
import { EditorView } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { json } from "@codemirror/lang-json";
import { oneDark } from "@codemirror/theme-one-dark";
import LucideIcon from "../icons/LucideIcon.vue";
import { fileIconName } from "../icons/lucide";
import type { FileApi, FileInfo, FileRead } from "../transport";

/**
 * FileBrowser — 共享文件浏览器（树 + CodeMirror 预览/编辑）。
 *
 * - editable=false（enduser）：CodeMirror 只读 + lang/oneDark 高亮，隐藏保存/新建/上传。
 * - editable=true（admin）：可编辑，dirty 跟踪 + 保存（fileApi.write）+ 新建/上传。
 *
 * 不依赖 Element Plus / vue-i18n / ~icons：原生 HTML 控件 + CSS 变量 + 内联 SVG，
 * 文案中文硬编码。fileApi 由宿主提供（normalize 各自后端形状）。
 */
const props = defineProps<{
  fileApi: FileApi;
  editable?: boolean;
  /** path → "write" | "edit"：工具改动过的文件，树节点标记。 */
  changedFiles?: Record<string, "write" | "edit">;
  title?: string;
}>();

const rootEntries = ref<FileInfo[]>([]);
const dirCache = reactive(new Map<string, FileInfo[]>());
const expandedDirs = reactive(new Set<string>());
const loading = ref(false);
const loadingDirs = reactive(new Set<string>());

const selectedPath = ref("");
const fileRead = ref<FileRead | null>(null);
const editContent = ref("");
const originalContent = ref("");
const previewLoading = ref(false);
const saving = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);

const EXPANDED_KEY = "ua-ws-expanded-dirs";

const dirty = computed(() => editContent.value !== originalContent.value);
const isBinary = computed(() => !!fileRead.value?.isBinary && !fileRead.value?.isImage);

const editorExtensions = computed(() => {
  const ext = selectedPath.value.split(".").pop()?.toLowerCase() ?? "";
  const base = [oneDark];
  if (ext === "md" || ext === "markdown") base.push(markdown());
  else if (ext === "json") base.push(json());
  // vue-codemirror 的 :editable prop 不够（cm-content 仍 contentEditable=true 可被
  // execCommand 改写）；用 EditorView.editable.of(false) 真正锁只读。
  if (!props.editable) base.push(EditorView.editable.of(false));
  return base;
});

function loadExpanded(): string[] {
  try {
    return JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]");
  } catch {
    return [];
  }
}
function saveExpanded() {
  localStorage.setItem(EXPANDED_KEY, JSON.stringify([...expandedDirs]));
}

const visibleNodes = computed(() => {
  const out: Array<FileInfo & { depth: number }> = [];
  const walk = (entries: FileInfo[], depth: number, parentPath: string) => {
    const sorted = [...entries].sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return String(a.name).localeCompare(String(b.name));
    });
    for (const e of sorted) {
      out.push({ ...e, depth });
      if (e.isDir && expandedDirs.has(e.path)) {
        walk(dirCache.get(e.path) || [], depth + 1, e.path);
      }
    }
  };
  walk(rootEntries.value, 0, ".");
  return out;
});

async function loadDir(path: string): Promise<FileInfo[]> {
  return await props.fileApi.list(path || ".");
}

async function onItemClick(node: FileInfo) {
  if (node.isDir) {
    await toggleExpand(node);
  } else {
    await selectFile(node.path);
  }
}

async function toggleExpand(node: FileInfo) {
  const path = node.path;
  if (expandedDirs.has(path)) {
    expandedDirs.delete(path);
    saveExpanded();
    return;
  }
  expandedDirs.add(path);
  saveExpanded();
  if (!dirCache.has(path)) {
    loadingDirs.add(path);
    try {
      dirCache.set(path, await loadDir(path));
    } catch {
      dirCache.set(path, []);
      expandedDirs.delete(path);
      saveExpanded();
    } finally {
      loadingDirs.delete(path);
    }
  }
}

async function selectFile(path: string) {
  if (props.editable && dirty.value && !(await confirmDiscard())) return;
  selectedPath.value = path;
  previewLoading.value = true;
  try {
    const res = await props.fileApi.read(path);
    fileRead.value = res;
    if (res.isBinary && !res.isImage) {
      editContent.value = "";
      originalContent.value = "";
    } else {
      editContent.value = res.content || "";
      originalContent.value = editContent.value;
    }
  } catch {
    fileRead.value = null;
  } finally {
    previewLoading.value = false;
  }
}

function confirmDiscard(): Promise<boolean> {
  if (typeof window === "undefined") return Promise.resolve(true);
  return Promise.resolve(window.confirm("当前文件有未保存的改动，切换将丢失，是否继续？"));
}

async function onSave() {
  if (!dirty.value || !selectedPath.value || !props.fileApi.write) return;
  saving.value = true;
  try {
    await props.fileApi.write(selectedPath.value, editContent.value);
    originalContent.value = editContent.value;
  } catch {
    // 保存失败：保持 dirty 态，宿主可自行提示
  } finally {
    saving.value = false;
  }
}

async function onNewFile() {
  const path = (typeof window !== "undefined" ? window.prompt("输入文件路径", "path/to/file.md") : "") || "";
  const trimmed = path.trim();
  if (!trimmed || !props.fileApi.write) return;
  try {
    await props.fileApi.write(trimmed, "");
    await refresh();
    await selectFile(trimmed);
  } catch {
    /* ignore */
  }
}

function onUploadClick() {
  fileInputRef.value?.click();
}

async function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !props.fileApi.write) return;
  const text = await file.text();
  const path = file.name;
  try {
    await props.fileApi.write(path, text);
    await refresh();
    await selectFile(path);
  } catch {
    /* ignore */
  } finally {
    input.value = "";
  }
}

function formatSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

async function init() {
  loading.value = true;
  try {
    rootEntries.value = await loadDir(".");
    const persisted = loadExpanded().sort((a, b) => a.split("/").length - b.split("/").length);
    for (const p of persisted) {
      const parent = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : ".";
      if (parent !== "." && !expandedDirs.has(parent)) continue;
      if (expandedDirs.has(p)) continue;
      expandedDirs.add(p);
      if (!dirCache.has(p)) {
        try {
          dirCache.set(p, await loadDir(p));
        } catch {
          dirCache.set(p, []);
        }
      }
    }
  } catch {
    rootEntries.value = [];
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  dirCache.clear();
  expandedDirs.clear();
  saveExpanded();
  await init();
}

defineExpose({ refresh, selectFile });
onMounted(init);
</script>
