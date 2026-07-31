<script setup lang="ts">
import { ref, computed, onMounted, h } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import dayjs from "dayjs";
import {
  getDefinitionApi,
  deleteDefinitionApi,
  publishDefinitionApi,
  type AgentDefinitionResponse
} from "@/api/manager/agentDefinitions";
import { addDialog, closeDialog } from "@/components/ReDialog";
import { message } from "@/utils/message";
import { ElMessageBox } from "element-plus";
import editForm from "../form.vue";
import VersionTab from "./VersionTab.vue";
import SkillsTab from "./SkillsTab.vue";
import PersonaTab from "./PersonaTab.vue";
import HermesLogo from "../components/icons/HermesLogo.vue";
import OpenClawLogo from "../components/icons/OpenClawLogo.vue";
import DifyLogo from "../components/icons/DifyLogo.vue";
import { IconifyIconOffline } from "@/components/ReIcon";
import More2Fill from "~icons/ri/more-2-fill";

defineOptions({ name: "AgentDefinitionDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const definitionId = computed(() => route.params.id as string);
const activeTab = ref("persona");
const loading = ref(true);
const definition = ref<AgentDefinitionResponse | null>(null);
const actionLoading = ref(false);
const formRef = ref();

/** 模型组（取 model_settings.litellm.model_group） */
const modelGroup = computed(() => {
  const ms = definition.value?.model_settings as any;
  if (!ms) return "";
  const obj = typeof ms === "string" ? (() => { try { return JSON.parse(ms); } catch { return {}; } })() : ms;
  return obj?.litellm?.model_group || obj?.model || "";
});

/** 状态映射 */
const statusConfig = computed<Record<string, { label: string; color: string }>>(() => ({
  DRAFT: { label: t("common.status.draft"), color: "#f59e0b" },
  PUBLISHED: { label: t("common.status.published"), color: "#00a870" }
}));

const avatarBg = computed(() => {
  if (!definition.value) return "#909399";
  return definition.value.avatar_color || "#909399";
});

async function fetchDefinition() {
  loading.value = true;
  try {
    definition.value = await getDefinitionApi(definitionId.value);
  } catch {
    message(t("agent.msg.loadFailed"), { type: "error" });
    router.back();
  } finally {
    loading.value = false;
  }
}

// ── 编辑 ──
async function openEditDialog() {
  if (!definition.value) return;
  const d = definition.value;
  const modelSettings = d.model_settings || {};
  const personaConfig = d.persona_config || {};
  const modelGroup: string = modelSettings?.litellm?.model_group ?? "";
  const systemPrompt: string =
    personaConfig?.system_prompt ?? modelSettings?.system_prompt ?? "";

  /** 按当前 step 刷新底部按钮：上一步 disabled / 下一步 label */
  const refreshFooter = (options: any, step: number, lastStep: number) => {
    const btns = options?.footerButtons;
    if (btns && btns.length >= 3) {
      btns[1].disabled = step === 0;
      btns[2].label = step < lastStep ? t("common.action.next") : t("common.action.ok");
    }
  };

  const LAST_STEP = 1;

  addDialog({
    title: t("agent.editTitle"),
    props: {
      formInline: {
        title: "edit",
        id: d.id,
        name: d.name,
        description: d.description,
        avatar_color: d.avatar_color ?? "#386bf5",
        engine_type: d.engine_type ?? "HERMES",
        persona_config: personaConfig,
        model_settings: modelSettings,
        skill_config: d.skill_config ?? {},
        memory_config: d.memory_config ?? {},
        modelGroup,
        system_prompt: systemPrompt
      }
    },
    width: "55%",
    draggable: true,
    fullscreenIcon: true,
    closeOnClickModal: false,
    contentRenderer: () => h(editForm, { ref: formRef, formInline: null }),
    footerButtons: [
      {
        label: t("common.action.cancel"),
        text: true,
        bg: true,
        btnClick: ({ dialog: { options, index } }) => {
          closeDialog(options, index);
        }
      },
      {
        label: t("common.action.prev"),
        text: true,
        bg: true,
        disabled: true,
        btnClick: ({ dialog: { options } }) => {
          const form = formRef.value;
          if (!form) return;
          form.prevStep();
          refreshFooter(options, form.getCurrentStep(), LAST_STEP);
        }
      },
      {
        label: t("common.action.next"),
        type: "primary",
        text: true,
        bg: true,
        btnClick: ({ dialog: { options, index } }) => {
          if (options?.beforeSure) {
            options.beforeSure(
              () => closeDialog(options, index),
              { options, index, closeLoading: () => {} }
            );
          }
        }
      }
    ],
    beforeSure: async (done, { options }) => {
      const form = formRef.value;
      if (!form) return;
      const submitted = await form.submitStep();
      if (submitted) {
        done();
        await fetchDefinition();
        return;
      }
      refreshFooter(options, form.getCurrentStep(), LAST_STEP);
    }
  });
}

// ── 发布版本 ──
async function handlePublishVersion() {
  if (!definition.value) return;
  try {
    const { value } = await ElMessageBox.prompt(
      t("definition.version.changeLog"),
      t("definition.publishVersion"),
      {
        confirmButtonText: t("common.action.publish"),
        cancelButtonText: t("common.action.cancel"),
        inputType: "textarea",
        inputPlaceholder: t("definition.version.changeLog")
      }
    );
    actionLoading.value = true;
    await publishDefinitionApi(definition.value.id, { change_log: value || "" });
    message(t("definition.msg.published"), { type: "success" });
    await fetchDefinition();
  } catch (err: any) {
    if (err === "cancel" || err?.toString?.().includes("cancel")) return;
    message(err?.response?.data?.detail || t("agent.msg.publishFailed"), { type: "error" });
  } finally {
    actionLoading.value = false;
  }
}

// ── 删除 ──
async function handleDelete() {
  if (!definition.value) return;
  try {
    await ElMessageBox.confirm(
      t("common.action.confirmDelete") + `「${definition.value.name}」?`,
      t("common.action.delete"),
      {
        confirmButtonText: t("common.action.confirm"),
        cancelButtonText: t("common.action.cancel"),
        type: "warning"
      }
    );
  } catch {
    return;
  }
  actionLoading.value = true;
  try {
    await deleteDefinitionApi(definition.value.id);
    message(t("definition.msg.deleted", { name: definition.value.name }), { type: "success" });
    router.push("/agent-definitions/index");
  } catch (e: any) {
    message(e?.response?.data?.detail || t("agent.msg.deleteFailed"), { type: "error" });
  } finally {
    actionLoading.value = false;
  }
}

onMounted(fetchDefinition);
</script>

<template>
  <div v-loading="loading" class="main">
    <div class="agent-detail">
    <!-- Header Card -->
    <el-card shadow="never" class="detail-header">
      <el-row :gutter="24" align="middle">
        <el-col :xs="24" :sm="16">
          <div class="header-main">
            <!-- Avatar -->
            <div
              class="header-avatar"
              :style="{ background: avatarBg + '18', color: avatarBg }"
            >
              <HermesLogo v-if="definition?.engine_type === 'HERMES'" class="avatar-icon" />
              <OpenClawLogo v-else-if="definition?.engine_type === 'OPENCLAW'" class="avatar-icon" />
              <DifyLogo v-else-if="definition?.engine_type === 'DIFY'" class="avatar-icon" />
              <span v-else class="avatar-text">{{ definition?.name?.charAt(0).toUpperCase() }}</span>
            </div>
            <div class="header-info">
              <div class="header-name-row">
                <h2 class="header-name">{{ definition?.name }}</h2>
                <el-tag
                  v-if="definition"
                  :color="statusConfig[definition.status]?.color"
                  effect="dark"
                  size="small"
                  class="status-tag"
                >
                  {{ statusConfig[definition.status]?.label }}
                </el-tag>
                <el-tag
                  v-if="definition?.current_version_no"
                  type="info"
                  effect="plain"
                  size="small"
                >
                  v{{ definition.current_version_no }}
                </el-tag>
                <el-tag
                  v-if="definition?.has_unpublished_changes"
                  color="#f59e0b"
                  effect="dark"
                  size="small"
                  class="status-tag"
                >
                  {{ t("definition.unpublishedChanges") }}
                </el-tag>
              </div>
              <p class="header-desc">{{ definition?.description || t("agent.noDescription") }}</p>
            </div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8" class="header-actions">
          <el-button type="primary" plain @click="openEditDialog">
            {{ t("common.action.edit") }}
          </el-button>
          <el-button
            type="success"
            :loading="actionLoading"
            @click="handlePublishVersion"
          >
            {{ t("definition.publishVersion") }}
          </el-button>
          <el-dropdown trigger="click" @command="cmd => cmd === 'delete' && handleDelete()">
            <IconifyIconOffline :icon="More2Fill" class="three-dot-btn" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item divided command="delete">
                  <span class="text-red-500">{{ t("common.action.delete") }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </el-col>
      </el-row>
      <!-- 配置汇总标签行 -->
      <div v-if="definition" class="header-tags">
        <div class="tag-item">
          <span class="tag-label">{{ t("agent.detail.label.engineType") }}</span>
          <span class="tag-value engine-type-value">
            <HermesLogo v-if="definition.engine_type === 'HERMES'" class="engine-type-logo" />
            <OpenClawLogo v-else-if="definition.engine_type === 'OPENCLAW'" class="engine-type-logo" />
            <DifyLogo v-else-if="definition.engine_type === 'DIFY'" class="engine-type-logo" />
            {{ definition.engine_type || "—" }}
          </span>
        </div>
        <div class="tag-item">
          <span class="tag-label">{{ t("agent.detail.label.modelGroup") }}</span>
          <span class="tag-value">{{ modelGroup || "—" }}</span>
        </div>
        <div class="tag-item">
          <span class="tag-label">{{ t("agent.config.label.creator") }}</span>
          <span class="tag-value">{{ definition.creator_name }}</span>
        </div>
        <div class="tag-item">
          <span class="tag-label">{{ t("agent.config.label.updatedAt") }}</span>
          <span class="tag-value">
            {{ definition.updated_at ? dayjs(definition.updated_at).format("MM-DD HH:mm") : "—" }}
          </span>
        </div>
      </div>
    </el-card>

    <!-- Tab 面板 -->
    <el-card shadow="never" class="detail-tabs mt-4">
      <el-tabs v-model="activeTab" class="detail-tabs-inner">
        <el-tab-pane :label="t('agent.detail.tabs.persona')" name="persona">
          <PersonaTab v-if="definition" :definition="definition" @updated="fetchDefinition" />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.skills')" name="skills">
          <SkillsTab v-if="definition" :definition-id="definition.id" />
        </el-tab-pane>
        <el-tab-pane :label="t('definition.version.title')" name="versions">
          <VersionTab
            v-if="definition"
            :definition-id="definition.id"
            :current-version-id="definition.current_version_id"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>
    </div>
  </div>
</template>

<style scoped>
.agent-detail {
  max-width: 1400px;
  margin: 0 auto;
}

.detail-header {
  border-radius: 8px;
}

.header-main {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.header-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  flex-shrink: 0;
}

.avatar-icon {
  width: 28px;
  height: 28px;
}

.avatar-text {
  font-size: 22px;
  font-weight: 600;
}

.header-info {
  flex: 1;
  min-width: 0;
}

.header-name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.header-name {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}

.header-desc {
  margin: 4px 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.status-tag {
  border: 0;
  height: 22px;
  line-height: 22px;
}

.header-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.three-dot-btn {
  font-size: 22px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: color 0.2s, background 0.2s;
  flex-shrink: 0;
}

.three-dot-btn:hover {
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.header-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 16px 24px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-light);
}

.tag-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}

.tag-label {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tag-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.engine-type-value {
  display: flex;
  align-items: center;
  gap: 4px;
}

.engine-type-logo {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.detail-tabs {
  border-radius: 8px;
}

.detail-tabs-inner {
  min-height: 400px;
}

:deep(.el-tabs__header) {
  margin-bottom: 20px;
}
</style>
