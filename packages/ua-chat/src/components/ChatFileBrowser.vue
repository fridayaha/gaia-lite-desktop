<template>
  <div class="workspace-panel-files">
    <div v-if="loading && !rootEntries.length" class="ws-empty">加载中…</div>
    <div v-else-if="!visibleNodes.length" class="ws-empty">工作区为空</div>
    <div v-else class="ws-tree">
      <div
        v-for="node in visibleNodes"
        :key="node.path"
        class="file-item"
        :class="{ 'is-dir': node.is_dir, 'is-file': !node.is_dir, 'is-selected': selectedPath === node.path }"
        :style="{ paddingLeft: 6 + node.depth * 14 + 'px' }"
        :title="node.path"
        @click="onItemClick(node)"
      >
        <span class="file-tree-toggle" v-if="node.is_dir">
          <LucideIcon :name="expandedDirs.has(node.path) ? 'chevron-down' : 'chevron-right'" :size="12" :stroke-width="2.5" />
        </span>
        <span class="file-tree-toggle-placeholder" v-else></span>
        <span class="file-item-icon" :class="{ 'is-dir-icon': node.is_dir }">
          <LucideIcon :name="fileIconName(node.name, node.is_dir)" :size="14" />
        </span>
        <span class="file-item-name">{{ node.name }}</span>
        <span v-if="!node.is_dir && node.size" class="file-item-size">{{ formatSize(node.size) }}</span>
        <span v-if="node.is_dir && loadingDirs.has(node.path)" class="ws-mini-loading">…</span>
        <span v-if="node.is_dir && expandedDirs.has(node.path) && (dirCache.get(node.path) || []).length === 0 && !loadingDirs.has(node.path)" class="ws-empty-inline">空</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch, inject } from "vue";
import LucideIcon from "../icons/LucideIcon.vue";
import { fileIconName } from "../icons/lucide";
import { chatContextKey } from "../chatContext";

const props = defineProps<{
  selectedPath?: string;
}>();

const emit = defineEmits<{
  select: [path: string];
}>();

const ctx = inject(chatContextKey, {});

const EXPANDED_KEY = "ua-ws-expanded-dirs";

const rootEntries = ref<any[]>([]);
const dirCache = reactive(new Map<string, any[]>());
const expandedDirs = reactive(new Set<string>());
const loading = ref(false);
const loadingDirs = reactive(new Set<string>());

function loadExpanded(): string[] {
  try { return JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]") } catch { return [] }
}
function saveExpanded() {
  localStorage.setItem(EXPANDED_KEY, JSON.stringify([...expandedDirs]));
}

const visibleNodes = computed(() => {
  const out: any[] = [];
  const walk = (entries: any[], depth: number, parentPath: string) => {
    const sorted = [...entries].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return String(a.name).localeCompare(String(b.name));
    });
    for (const e of sorted) {
      const path = parentPath === "." ? e.name : `${parentPath}/${e.name}`;
      const nodePath = e.path || path;
      out.push({ ...e, path: nodePath, depth });
      if (e.is_dir && expandedDirs.has(nodePath)) {
        const children = dirCache.get(nodePath) || [];
        walk(children, depth + 1, nodePath);
      }
    }
  };
  walk(rootEntries.value, 0, ".");
  return out;
});

async function loadDir(path: string): Promise<any[]> {
  if (!ctx.fileLister) return [];
  const data = await ctx.fileLister(path || ".");
  return data.entries || [];
}

async function onItemClick(node: any) {
  if (node.is_dir) {
    await toggleExpand(node);
  } else {
    emit("select", node.path);
  }
}

async function toggleExpand(node: any) {
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
      const children = await loadDir(path);
      dirCache.set(path, children);
    } catch {
      dirCache.set(path, []);
      expandedDirs.delete(path);
      saveExpanded();
    } finally {
      loadingDirs.delete(path);
    }
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

onMounted(init);
</script>
