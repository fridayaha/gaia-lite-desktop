<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, toRef } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import ArrowLeftLine from "~icons/ri/arrow-left-line";
import RestartLine from "~icons/ri/restart-line";
import RefreshLine from "~icons/ri/refresh-line";
import DownloadLine from "~icons/ri/download-2-line";
import RocketLine from "~icons/ri/rocket-line";
import DeleteBinLine from "~icons/ri/delete-bin-line";
import EditLine from "~icons/ri/edit-line";
import {
  getWorkspaceApi,
  reloadApi,
  validateApi,
  downloadPackageApi,
  listFilesApi,
  readFileApi,
  writeFileApi,
  clearSessionMessagesApi,
  type Workspace,
} from "@/api/manager/skill-engine";
import { useEngineSession } from "./useEngineSession";
import { isFileMutationTool, FileBrowser, type FileApi } from "@ua/chat";
import SkillConfigPanel from "./components/SkillConfigPanel.vue";
import ChatPanel from "./components/ChatPanel.vue";
import PublishDialog from "./components/PublishDialog.vue";

defineOptions({ name: "SkillStudioDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const workspaceId = computed(() => route.params.id as string);
const workspace = ref<Workspace | null>(null);
const activeRole = ref<"dev" | "debug">("dev");

const engine = useEngineSession(toRef(workspaceId));
const fileBrowserRef = ref<InstanceType<typeof FileBrowser> | null>(null);
const configOpen = ref(false);
const clearing = ref(false);

// FileApi 适配器：把 skill-engine 的扁平全量文件列表归一成 FileBrowser 期望的
// 「按目录列直接子项」模型（listFilesApi 一次返回全量扁平 paths，按 path 前缀过滤）。
const fileApi = computed<FileApi>(() => {
  const id = workspaceId.value;
  return {
    async list(path: string) {
      const all = (await listFilesApi(id)).files || [];
      const parent = path === "." ? "" : path + "/";
      return all
        .filter((e) => {
          if (!e.path.startsWith(parent)) return false;
          const rest = e.path.slice(parent.length);
          return rest.length > 0 && !rest.includes("/");
        })
        .map((e) => ({
          name: e.path.split("/").pop() || e.path,
          path: e.path,
          isDir: e.isDir,
          size: e.size,
        }));
    },
    async read(path: string) {
      const r = await readFileApi(id, path);
      return { path: r.path, content: r.content, isBinary: r.isBinary, size: r.size };
    },
    async write(path: string, content: string) {
      await writeFileApi(id, path, content);
    },
    async download() {
      /* skill-engine 无单文件下载端点，no-op */
    },
  };
});

// 本会话工具改动过的文件（相对路径 → write/edit），用于 FilePanel 树节点标记。
const changedFiles = ref<Record<string, "write" | "edit">>({});

// 记录每个 role 是否已 connect（Tab 首次切入才连接，切走不断开）
const connectedRoles = ref<Record<"dev" | "debug", boolean>>({
  dev: false,
  debug: false,
});

async function ensureConnected(role: "dev" | "debug") {
  if (connectedRoles.value[role]) return;
  connectedRoles.value[role] = true;
  await engine.connect(role);
}

async function loadWorkspace() {
  try {
    workspace.value = await getWorkspaceApi(workspaceId.value);
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  }
}

// 真重启：disconnect（停 worker）→ ensureConnected（重新 spawn，重读工作区文件）。
// 旧实现直接调 connect，而 connect 对已连接会话 no-op，等于没重启。
async function restartSession(role: "dev" | "debug") {
  await engine.disconnect(role);
  connectedRoles.value[role] = false;
  await ensureConnected(role);
}

async function onRestart() {
  await restartSession(activeRole.value);
}

// 清除当前会话的全部消息（dev/debug 独立）：后端删 messages 表 + Redis 缓存，
// 再重启会话清引擎内存并重新 backfill（空）。二次确认（不可恢复）。
async function onClearSession() {
  const role = activeRole.value;
  try {
    await ElMessageBox.confirm(
      `将清除「${t(`hub.studio.detail.${role}`)}」会话的全部对话记录，且不可恢复，是否继续？`,
      "清除会话",
      { confirmButtonText: "清除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return; // cancelled
  }
  clearing.value = true;
  try {
    await clearSessionMessagesApi(workspaceId.value, role);
    // 重启会话：清引擎内存中的 messages + 重新 backfill（此时为空）
    await restartSession(role);
    ElMessage.success("会话已清除");
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  } finally {
    clearing.value = false;
  }
}

// 热重载技能（仅 debug）：重新读取工作区 SKILL.md 注入 .pi/skills/ 并让引擎
// reload 资源，不重建子进程。编辑 SKILL.md 后用它快速生效。
const reloading = ref(false);
async function onReloadSkill() {
  reloading.value = true;
  try {
    await reloadApi(workspaceId.value, activeRole.value);
    ElMessage.success(t("hub.studio.detail.reloaded"));
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  } finally {
    reloading.value = false;
  }
}

// ── 验证 / 打包 / 发布 ──
const publishOpen = ref(false);
const publishVersion = ref("");
const packaging = ref(false);

async function onPackage() {
  packaging.value = true;
  try {
    // 文件名用 manifest 的 name-version（对齐后端 ${name}-${version}.zip），区分不同技能
    let filename = "skill.zip";
    try {
      const r = await readFileApi(workspaceId.value, "manifest.json");
      const m = JSON.parse(r.content);
      if (m.name && m.version) filename = `${m.name}-${m.version}.zip`;
    } catch {
      /* manifest 读取失败则回退默认名 */
    }
    const blob = await downloadPackageApi(workspaceId.value);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  } finally {
    packaging.value = false;
  }
}

async function onPublishClick() {
  // 先校验：无效则提示并阻止发布
  try {
    const res = await validateApi(workspaceId.value);
    if (!res.valid) {
      ElMessage.warning(`${t("hub.studio.detail.validateFailed")}: ${res.errors.map((e) => e.field).join(", ")}`);
      return;
    }
    publishVersion.value = String(res.manifest?.version ?? "");
    publishOpen.value = true;
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  }
}

function onPublished() {
  // 发布成功后刷新工作区（拿回 hubItemId 用于「打开能力中心」）
  void loadWorkspace();
}

// 配置保存后：SKILL.md 是 debug 会话的技能定义，改动需重载 debug 才生效。
// debug 已连接则重启；未连接则不主动拉起（下次首次连接会读新文件）。
// 同时刷新文件树（manifest/SKILL.md 可能新增或变更）。
async function onConfigSaved(payload: { skillChanged: boolean }) {
  void fileBrowserRef.value?.refresh();
  if (payload.skillChanged && connectedRoles.value.debug) {
    await restartSession("debug");
  }
}

// 工具改动文件后联动刷新右侧文件树 + 标记改动文件。
// 聚合 dev/debug 两个会话里已完成的 write/edit 工具：数量增长时刷新文件树，
// 同时收集 path→类型 供 FilePanel 树节点标 🟢新增/🟡修改。
const lastFileMutCount = ref(0);

function relativizeToolPath(absPath: unknown): string {
  if (typeof absPath !== "string") return "";
  const base = workspace.value?.localPath;
  if (base && absPath.startsWith(base)) {
    const rel = absPath.slice(base.length).replace(/^\/+/, "");
    return rel;
  }
  // Fallback: last path segment.
  const seg = absPath.split("/").filter(Boolean).pop();
  return seg ?? absPath;
}

watch(
  () => [engine.sessions.dev.messages, engine.sessions.debug.messages],
  () => {
    const next: Record<string, "write" | "edit"> = {};
    let count = 0;
    for (const role of ["dev", "debug"] as const) {
      for (const m of engine.sessions[role].messages) {
        for (const p of m.parts) {
          if (p.kind !== "tool" || !isFileMutationTool(p.tool.toolName)) continue;
          if (p.tool.status === "running") continue;
          count++;
          const rel = relativizeToolPath(p.tool.args?.path);
          if (rel) next[rel] = p.tool.toolName as "write" | "edit";
        }
      }
    }
    changedFiles.value = next;
    if (count > lastFileMutCount.value) {
      void fileBrowserRef.value?.refresh();
    }
    lastFileMutCount.value = count;
  },
  { deep: true },
);

onMounted(async () => {
  await loadWorkspace();
  await ensureConnected("dev");
});
onBeforeUnmount(async () => {
  await Promise.all([engine.disconnect("dev"), engine.disconnect("debug")]);
});
</script>

<template>
  <div class="skill-detail">
    <!-- 顶部操作栏 -->
    <header class="topbar">
      <div class="left">
        <el-button text @click="router.push('/skill-studio/index')">
          <ArrowLeftLine width="16" height="16" class="mr-1" />
        </el-button>
        <span class="ws-name">{{ workspace?.name || "—" }}</span>
        <el-tag size="small" effect="plain" class="status-tag">
          {{ engine.sessions[activeRole].status }}
        </el-tag>
        <el-button size="small" @click="configOpen = true">
          <EditLine width="14" height="14" class="mr-1" />
          编辑配置
        </el-button>
        <el-button size="small" :loading="packaging" @click="onPackage">
          <DownloadLine width="14" height="14" class="mr-1" />
          {{ t("hub.studio.detail.package") }}
        </el-button>
        <el-button size="small" type="primary" plain @click="onPublishClick">
          <RocketLine width="14" height="14" class="mr-1" />
          {{ t("hub.studio.detail.publish") }}
        </el-button>
      </div>
      <div class="right">
        <el-button
          v-if="activeRole === 'debug'"
          size="small"
          :loading="reloading"
          :disabled="!connectedRoles[activeRole]"
          @click="onReloadSkill"
        >
          <RefreshLine width="14" height="14" class="mr-1" />
          {{ t("hub.studio.detail.reloadSkill") }}
        </el-button>
        <el-button
          size="small"
          :loading="clearing"
          :disabled="!connectedRoles[activeRole]"
          @click="onClearSession"
        >
          <DeleteBinLine width="14" height="14" class="mr-1" />
          清除会话
        </el-button>
        <el-button size="small" type="primary" plain :disabled="!connectedRoles[activeRole]" @click="onRestart">
          <RestartLine width="14" height="14" class="mr-1" />
          {{ t("hub.studio.detail.restartInstance") }}
        </el-button>
      </div>
    </header>

    <!-- 工作台：聊天（中）+ 文件（右） -->
    <div class="workbench">
      <section class="col col-center">
        <el-tabs v-model="activeRole" class="role-tabs" @tab-change="(r: any) => ensureConnected(r)">
          <el-tab-pane :label="t('hub.studio.detail.dev')" name="dev">
            <ChatPanel role="dev" :engine="engine" :workspace-id="workspaceId" />
          </el-tab-pane>
          <el-tab-pane :label="t('hub.studio.detail.debug')" name="debug">
            <ChatPanel role="debug" :engine="engine" :workspace-id="workspaceId" />
          </el-tab-pane>
        </el-tabs>
      </section>

      <aside class="col col-right">
        <div class="file-panel-wrap">
          <FileBrowser
            ref="fileBrowserRef"
            :file-api="fileApi"
            :editable="true"
            :changed-files="changedFiles"
            title="文件"
          />
        </div>
      </aside>
    </div>

    <!-- 技能配置（元数据）表单弹窗 -->
    <SkillConfigPanel v-model="configOpen" :workspace-id="workspaceId" @saved="onConfigSaved" />

    <!-- 发布弹窗 -->
    <PublishDialog
      v-model="publishOpen"
      :workspace-id="workspaceId"
      :manifest-version="publishVersion"
      @published="onPublished"
    />
  </div>
</template>

<style scoped>
.skill-detail {
  --ss-ink: #1f2430;
  --ss-line: #e5e7eb;
  --ss-accent: #6d5efc;
  --ss-accent-soft: rgba(109, 94, 252, 0.1);
  --ss-recess: #f6f7f9;
  display: flex;
  flex-direction: column;
  /* Fixed viewport height: the admin app-main wraps pages in an el-scrollbar
     whose view is content-sized, so height:100% resolves to auto and the page
     grows with chat content. Pin to the visible scroll area (100vh minus the
     ~85px admin chrome: header + tags-view) so the chat scrolls internally. */
  height: calc(100vh - 85px);
  min-height: 0;
  background: var(--ss-recess);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--ss-line);
  flex-shrink: 0;
}
.topbar .left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ws-name {
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-size: 14px;
  font-weight: 600;
  color: var(--ss-ink);
}
.status-tag {
  font-size: 11px;
}

.workbench {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 320px;
  grid-template-rows: minmax(0, 1fr);
  gap: 1px;
  background: var(--ss-line);
  min-height: 0;
}
.col {
  background: var(--el-bg-color);
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.col-right {
  background: var(--el-bg-color);
}
/* FileBrowser 主题对齐：把 @ua/chat CSS 变量覆盖到 skill-studio 紫色系 */
.file-panel-wrap {
  --accent: var(--ss-accent, #6d5efc);
  --accent-hover: var(--ss-accent, #6d5efc);
  --accent-bg: var(--ss-accent-soft, rgba(109, 94, 252, 0.1));
  --text: var(--ss-ink, #1f2430);
  --muted: var(--el-text-color-secondary, #6b7280);
  --border: var(--ss-line, #e5e7eb);
  --border-subtle: var(--ss-line, #e5e7eb);
  --surface: var(--el-bg-color, #fff);
  --hover-bg: rgba(109, 94, 252, 0.06);
  --code-bg: var(--ss-recess, #f6f7f9);
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* center chat column uses recessed backdrop */
.col-center {
  background: var(--ss-recess);
}
.role-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.role-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 14px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--ss-line);
}
.role-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
}
.role-tabs :deep(.el-tab-pane) {
  height: 100%;
}

@media (max-width: 1100px) {
  .workbench {
    grid-template-columns: 1fr;
    grid-auto-rows: auto;
  }
}
</style>
