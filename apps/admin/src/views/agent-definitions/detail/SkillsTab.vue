<script setup lang="tsx">
import { ref, computed, onMounted, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { AgentSkill } from "@/api/manager/skills";
import {
  getSkillsApi,
  toggleSkillApi,
  installSkillApi,
  uninstallSkillApi,
  previewSkillApi,
  getMarketplaceSkillsApi,
  installMarketplaceSkillApi,
  getSkillCredentialStatusApi,
  saveSkillCredentialsApi,
  getSkillConfigApi,
  saveSkillConfigApi
} from "@/api/manager/skills";
import { message } from "@/utils/message";
import { addDialog, closeDialog } from "@/components/ReDialog";
import Apps2Line from "~icons/ri/apps-2-line";
import CodeLine from "~icons/ri/code-s-slash-line";
import BarChartLine from "~icons/ri/bar-chart-2-line";
import FileTextLine from "~icons/ri/file-text-line";
import SearchEyeLine from "~icons/ri/search-eye-line";
import GitBranchLine from "~icons/ri/git-branch-line";
import AddLine from "~icons/ri/add-line";
import Upload2Line from "~icons/ri/upload-2-line";
import Store2Line from "~icons/ri/store-2-line";
import SearchLine from "~icons/ri/search-line";

defineOptions({ name: "DefinitionSkillsTab" });

const props = defineProps<{
  definitionId: string;
}>();

const { t } = useI18n();

// ── 图标映射 ──
const iconMap: Record<string, any> = {
  "ri:code-s-slash-line": CodeLine,
  "ri:bar-chart-2-line": BarChartLine,
  "ri:file-text-line": FileTextLine,
  "ri:search-eye-line": SearchEyeLine,
  "ri:git-branch-line": GitBranchLine
};

function resolveIcon(skill: AgentSkill) {
  return iconMap[skill.icon] || Apps2Line;
}

// 内置技能描述常含换行（SKILL.md 多行），卡片展示压缩为单空格；
// 完整原文（含换行）通过 tooltip 展示，便于阅读。
function collapseDesc(desc: string | undefined): string {
  return (desc || "").replace(/\s+/g, " ").trim();
}

// ── 状态 ──
const loading = ref(false);
const skills = ref<AgentSkill[]>([]);
const searchText = ref("");
const installing = ref(false);

// ── 分页 ──
const pageSize = ref(8);
const currentPage = ref(1);

// ── 详情抽屉 ──
const drawerVisible = ref(false);
const selectedSkill = ref<AgentSkill | null>(null);
// secret 凭证：credDraft 存输入草稿（不回显明文），credStatus 存已配置参数名
const credDraft = ref<Record<string, string>>({});
const credStatus = ref<{ configured: string[] }>({ configured: [] });
const isCredConfigured = (name: string) => credStatus.value.configured.includes(name);
// 非 secret 配置：configDraft 存表单草稿（打开抽屉时从后端回填），保存时整体提交
const configDraft = ref<Record<string, any>>({});
const configSaving = ref(false);

// ── 计算属性 ──
const activeCategory = ref<"preset" | "custom">("preset");
const isPresetSkill = (s: AgentSkill) => s.source === "preset" || !!s.builtin;
const presetCount = computed(() => skills.value.filter(isPresetSkill).length);
const customCount = computed(() => skills.value.filter(s => !isPresetSkill(s)).length);
watch(activeCategory, () => { currentPage.value = 1; });
const filteredSkills = computed(() => {
  const byCat = skills.value.filter(s =>
    activeCategory.value === "preset" ? isPresetSkill(s) : !isPresetSkill(s)
  );
  if (!searchText.value.trim()) return byCat;
  const q = searchText.value.toLowerCase();
  return byCat.filter(
    s => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
  );
});

const pagedSkills = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredSkills.value.slice(start, start + pageSize.value);
});

const totalCount = computed(() => filteredSkills.value.length);

// ── 开关切换 ──
async function handleToggle(skill: AgentSkill, val: boolean) {
  const state = val ? t("common.status.enabled") : t("common.status.disabled");
  try {
    await toggleSkillApi(props.definitionId, skill.id, val);
    skill.enabled = val;
    message(t("agent.skill.msg.toggled", { name: skill.name, state }), { type: "success" });
  } catch (e: any) {
    message(e?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
  }
}

// ── 详情抽屉 ──
async function openDrawer(skill: AgentSkill) {
  selectedSkill.value = skill;
  credDraft.value = {};
  credStatus.value = { configured: [] };
  configDraft.value = {};
  drawerVisible.value = true;
  // 拉取已配置的 secret 参数名（不回显明文）
  if (skill.configParams?.some(p => p.secret)) {
    try {
      const res = await getSkillCredentialStatusApi(props.definitionId, skill.id);
      credStatus.value = (res as any) || { configured: [] };
    } catch {
      credStatus.value = { configured: [] };
    }
  }
  // 拉取非 secret 配置回填（含未填参数的 default 兜底）
  if (skill.configParams?.some(p => !p.secret)) {
    try {
      const res = await getSkillConfigApi(props.definitionId, skill.id);
      configDraft.value = { ...((res as any)?.values || {}) };
    } catch {
      configDraft.value = {};
    }
  }
}

async function saveSkillConfig() {
  if (!selectedSkill.value) return;
  const skill = selectedSkill.value;
  const secretParams = skill.configParams?.filter(p => p.secret) || [];
  const nonSecretParams = skill.configParams?.filter(p => !p.secret) || [];

  // 非 secret 配置：收集已填值整体提交（空值不传，部分更新语义，对齐 secret 空值不修改）
  const config: Record<string, any> = {};
  for (const p of nonSecretParams) {
    const v = configDraft.value[p.name];
    if (v !== undefined && v !== null && v !== "") config[p.name] = v;
  }
  // secret 凭证：仅提交非空值（空值表示不修改）
  const creds: Record<string, string> = {};
  for (const p of secretParams) {
    const v = credDraft.value[p.name];
    if (v) creds[p.name] = v;
  }

  configSaving.value = true;
  try {
    const tasks: Promise<unknown>[] = [];
    if (nonSecretParams.length) tasks.push(saveSkillConfigApi(props.definitionId, skill.id, config));
    if (secretParams.length && Object.keys(creds).length)
      tasks.push(saveSkillCredentialsApi(props.definitionId, skill.id, creds));
    if (tasks.length === 0) {
      drawerVisible.value = false;
      return;
    }
    await Promise.all(tasks);
    // 保存成功后刷新 secret 配置状态
    if (secretParams.length) {
      try {
        const res = await getSkillCredentialStatusApi(props.definitionId, skill.id);
        credStatus.value = (res as any) || { configured: [] };
      } catch {
        /* ignore */
      }
    }
    credDraft.value = {};
    message(t("agent.skill.msg.configSaved", { name: skill.name }), { type: "success" });
    drawerVisible.value = false;
  } catch (e: any) {
    message(e?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
  } finally {
    configSaving.value = false;
  }
}

async function handleUninstall() {
  if (!selectedSkill.value) return;
  const skill = selectedSkill.value;
  if (skill.builtin) return; // 内置技能不可卸载
  try {
    await uninstallSkillApi(props.definitionId, skill.id);
    skills.value = skills.value.filter(s => s.id !== skill.id);
    message(t("agent.skill.msg.uninstalled", { name: skill.name }), { type: "success" });
    drawerVisible.value = false;
  } catch (e: any) {
    message(e?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
  }
}

// ── 本地上传安装 ──
const fileInputRef = ref<HTMLInputElement>();

function triggerFilePick() {
  if (installing.value) return;
  fileInputRef.value?.click();
}

async function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input?.files?.[0];
  input.value = "";
  if (!file) return;
  if (!file.name.endsWith(".zip")) {
    message(t("agent.skill.msg.zipOnly"), { type: "warning" });
    return;
  }
  message(t("agent.skill.msg.parsing"), { type: "info" });
  try {
    const res = await previewSkillApi(props.definitionId, file);
    showInstallPreview(res.manifest, file);
  } catch (err: any) {
    message(err?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
  }
}

function showInstallPreview(preview: AgentSkill, file: File) {
  addDialog({
    title: t("agent.skill.dialogTitle.preview"),
    width: "520px",
    contentRenderer: () => (
      <div>
        <div class="flex items-center gap-3 mb-4 p-4 bg-[var(--el-fill-color-light)] rounded-lg">
          <div class="flex items-center justify-center w-12 h-12 rounded-xl bg-[var(--el-color-primary-light-9)] text-[var(--el-color-primary)] text-2xl">
            <CodeLine style="font-size:24px;color:var(--el-color-primary)" />
          </div>
          <div>
            <h3 class="text-base font-semibold m-0">{preview.name}</h3>
            <p class="text-sm text-[var(--el-text-color-secondary)] m-0 mt-1">{preview.description}</p>
            <div class="flex gap-3 mt-1 text-xs text-[var(--el-text-color-placeholder)]">
              <span>v{preview.version}</span>
              <span>{t("agent.skill.preview.author", { author: preview.author })}</span>
            </div>
          </div>
        </div>
        {preview.configParams?.length > 0 && (
          <div class="mb-4">
            <p class="text-sm font-medium mb-2">{t("agent.skill.preview.params")}</p>
            <div class="flex flex-wrap gap-2">
              {preview.configParams.map((p: any) => (
                <el-tag size="small" effect="plain">{p.label} ({p.type})</el-tag>
              ))}
            </div>
          </div>
        )}
        <div class="flex items-center gap-2 mb-4 p-3 bg-[rgba(0,168,112,0.08)] rounded-lg text-sm text-[#00a870]">
          <span>{t("agent.skill.preview.safe")}</span>
        </div>
      </div>
    ),
    footerButtons: [
      { label: t("common.action.cancel"), text: true, bg: true },
      {
        label: t("agent.skill.preview.confirmInstall"),
        type: "primary",
        loading: installing.value,
        btnClick: async ({ dialog: d }: any) => {
          installing.value = true;
          try {
            await installSkillApi(props.definitionId, file);
            closeDialog(d.options, d.index);
            message(t("agent.skill.msg.installOk", { name: preview.name }), { type: "success" });
            // 定义层无实例，安装 fan-out 由后端处理；前端刷新列表即可
            await fetchData();
          } catch (err: any) {
            message(err?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
          } finally {
            installing.value = false;
          }
        }
      }
    ]
  });
}

// ── 市场安装 ──
function openMarketplace() {
  const marketSkills = ref<AgentSkill[]>([]);
  const marketLoading = ref(true);

  addDialog({
    title: t("agent.skill.dialogTitle.market"),
    width: "700px",
    contentRenderer: () => {
      getMarketplaceSkillsApi().then(res => {
        marketSkills.value = (res as any).items || [];
      }).catch(() => {
        marketSkills.value = [];
      }).finally(() => {
        marketLoading.value = false;
      });
      return (
        <div v-loading={marketLoading.value}>
          {marketSkills.value.length === 0 && !marketLoading.value && (
            <el-empty description={t("agent.skill.marketEmpty")} />
          )}
          <el-row gutter={16}>
            {marketSkills.value.map(skill => (
              <el-col span={8} key={skill.id}>
                <el-card shadow="never" class="mb-3 marketplace-card">
                  <div class="flex items-start gap-3">
                    <div class="marketplace-icon">
                      <CodeLine style="font-size:20px;color:#386bf5" />
                    </div>
                    <div class="flex-1 min-w-0">
                      <h4 class="text-sm font-semibold m-0">{skill.name}</h4>
                      <p class="text-xs text-[var(--el-text-color-secondary)] m-0 mt-1 line-clamp-2">{skill.description}</p>
                      <div class="flex items-center gap-2 mt-2 text-xs text-[var(--el-text-color-placeholder)]">
                        <span>v{skill.version}</span>
                        <span>·</span>
                        <span>{t("agent.skill.installCount", { count: skill.usageCount })}</span>
                      </div>
                    </div>
                    {skills.value.some(s => s.name === skill.name) ? (
                      <el-tag size="small" type="info">{t("agent.skill.installed")}</el-tag>
                    ) : (
                      <el-button
                        size="small"
                        type="primary"
                        plain
                        loading={installing.value}
                        onClick={async () => {
                          try {
                            installing.value = true;
                            await installMarketplaceSkillApi(props.definitionId, skill.id);
                            message(t("agent.skill.msg.installOk", { name: skill.name }), { type: "success" });
                            await fetchData();
                          } catch (err: any) {
                            message(err?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
                          } finally {
                            installing.value = false;
                          }
                        }}
                      >
                        {t("common.action.install")}
                      </el-button>
                    )}
                  </div>
                </el-card>
              </el-col>
            ))}
          </el-row>
        </div>
      );
    }
  });
}

// ── 分页 ──
function handleSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
}

function handleCurrentChange(page: number) {
  currentPage.value = page;
}

// ── 数据加载 ──
async function fetchData() {
  loading.value = true;
  try {
    const res = await getSkillsApi(props.definitionId);
    skills.value = (res as any).items || res || [];
  } catch {
    skills.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(fetchData);
</script>

<template>
  <div class="skills-tab">
    <!-- 工具栏 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <el-radio-group v-model="activeCategory" size="default">
        <el-radio-button value="preset">{{ t("agent.skill.preset") }} ({{ presetCount }})</el-radio-button>
        <el-radio-button value="custom">{{ t("agent.skill.custom") }} ({{ customCount }})</el-radio-button>
      </el-radio-group>
      <div class="flex items-center gap-3 flex-wrap">
        <el-input
          v-model="searchText"
          style="width: 260px"
          :placeholder="t('agent.skill.searchPlaceholder')"
          clearable
        >
          <template #suffix>
            <el-icon class="el-input__icon">
              <SearchLine v-show="searchText.length === 0" />
            </el-icon>
          </template>
        </el-input>
      </div>
    </div>
    <input
      ref="fileInputRef"
      type="file"
      accept=".zip"
      class="hidden-input"
      @change="onFileSelected"
    />

    <!-- 技能卡片网格 -->
    <div v-loading="loading">
      <el-empty
        v-if="filteredSkills.length === 0 && !loading"
        :description="t('agent.skill.empty')"
      />
      <el-row :gutter="16">
        <el-col
          v-for="skill in pagedSkills"
          :key="skill.id"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
          class="mb-4"
        >
          <el-card
            shadow="never"
            :class="['skill-card', { 'skill-disabled': !skill.enabled }]"
            @click="openDrawer(skill)"
          >
            <div class="skill-card-body">
              <div class="skill-icon-wrapper">
                <component :is="resolveIcon(skill)" style="font-size:24px" :color="skill.enabled ? '#386bf5' : '#909399'" />
              </div>
              <div class="skill-info">
                <h4 class="skill-name">
                  <span class="skill-name-text" :title="skill.name">{{ skill.name }}</span>
                  <el-tag v-if="skill.source === 'preset'" size="small" type="warning" effect="dark" class="skill-builtin-tag">{{ t("agent.skill.preset") }}</el-tag>
                  <el-tag v-else-if="skill.builtin" size="small" type="primary" effect="light" class="skill-builtin-tag">{{ t("agent.skill.builtin") }}</el-tag>
                  <el-tag v-else-if="!skill.installed" size="small" type="warning" effect="plain" class="skill-builtin-tag">{{ t("agent.skill.notSynced") }}</el-tag>
                  <el-tag v-else size="small" type="success" effect="light" class="skill-builtin-tag">{{ t("agent.skill.custom") }}</el-tag>
                </h4>
                <el-tooltip
                  :content="skill.description"
                  placement="top"
                  :disabled="!skill.description"
                  popper-class="skill-desc-tooltip"
                  :show-after="300"
                >
                  <p class="skill-desc">{{ collapseDesc(skill.description) }}</p>
                </el-tooltip>
                <div class="skill-meta">
                  <span class="skill-version">v{{ skill.version }}</span>
                  <span class="skill-meta-sep">·</span>
                  <span class="skill-author" :title="skill.author">{{ skill.author }}</span>
                </div>
              </div>
              <div class="skill-toggle" @click.stop>
                <el-switch
                  :modelValue="skill.enabled"
                  @change="(val: boolean) => handleToggle(skill, val)"
                  size="default"
                />
              </div>
            </div>
          </el-card>
        </el-col>

        <!-- 安装新技能卡片（仅自定义 tab 显示） -->
        <el-col
          v-if="activeCategory === 'custom'"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
          class="mb-4"
        >
          <div style="width: 100%">
          <el-dropdown
            trigger="click"
            placement="bottom"
            class="install-dropdown"
            @command="(cmd: string) => cmd === 'upload' ? triggerFilePick() : openMarketplace()"
          >
            <el-card
              shadow="never"
              class="skill-add-card"
            >
              <div class="skill-add-body">
                <AddLine style="font-size:36px;color:var(--el-text-color-placeholder)" />
              </div>
            </el-card>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="upload">
                  <el-icon class="mr-2"><Upload2Line /></el-icon>
                  {{ t("agent.skill.install.local") }}
                </el-dropdown-item>
                <el-dropdown-item command="marketplace">
                  <el-icon class="mr-2"><Store2Line /></el-icon>
                  {{ t("agent.skill.install.market") }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 分页 -->
    <div class="pagination-wrapper">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="totalCount"
        :page-sizes="[4, 8, 12]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        size="small"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>

    <!-- 技能详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="selectedSkill?.name || t('agent.skill.dialogTitle.detail')"
      size="420px"
    >
      <template v-if="selectedSkill">
        <div class="drawer-header">
          <div class="drawer-icon">
            <component :is="resolveIcon(selectedSkill)" style="font-size:32px;color:#386bf5" />
          </div>
          <div>
            <h3 class="drawer-name">{{ selectedSkill.name }}</h3>
            <p class="drawer-desc">{{ collapseDesc(selectedSkill.description) }}</p>
            <div class="drawer-tags">
              <el-tag size="small" effect="plain">v{{ selectedSkill.version }}</el-tag>
              <el-tag size="small" effect="plain">{{ selectedSkill.author }}</el-tag>
              <el-tag
                size="small"
                :color="selectedSkill.enabled ? '#00a87018' : '#90939918'"
                :style="{
                  color: selectedSkill.enabled ? '#00a870' : '#909399',
                  border: `1px solid ${selectedSkill.enabled ? '#00a87040' : '#90939940'}`
                }"
              >
                {{ selectedSkill.enabled ? t("common.status.enabled") : t("common.status.disabled") }}
              </el-tag>
            </div>
          </div>
        </div>

        <!-- 使用统计 -->
        <el-card shadow="never" class="drawer-stat">
          <div class="stat-row">
            <span class="stat-label">{{ t("agent.skill.drawer.usage") }}</span>
            <span class="stat-value">{{ selectedSkill.usageCount }} {{ t("agent.skill.drawer.usageUnit") }}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">{{ t("agent.skill.drawer.engine") }}</span>
            <span class="stat-value">{{ selectedSkill.engine?.join("、") || t("agent.skill.drawer.engineAll") }}</span>
          </div>
        </el-card>

        <!-- 配置参数 -->
        <div v-if="selectedSkill.configParams?.length" class="drawer-config">
          <h4 class="drawer-section-title">{{ t("agent.skill.drawer.configTitle") }}</h4>
          <el-form label-position="top" size="small">
            <el-form-item
              v-for="param in selectedSkill.configParams"
              :key="param.name"
              :label="param.label"
            >
              <el-input
                v-if="param.type === 'string' && !param.secret"
                v-model="configDraft[param.name]"
                :placeholder="param.description"
              />
              <div v-else-if="param.type === 'string' && param.secret" class="flex items-center gap-2 w-full">
                <el-input
                  v-model="credDraft[param.name]"
                  type="password"
                  show-password
                  :placeholder="isCredConfigured(param.name) ? t('agent.skill.drawer.credConfigured') : (param.description || t('agent.skill.drawer.credPlaceholder'))"
                />
                <el-tag v-if="isCredConfigured(param.name)" size="small" type="success">{{ t("agent.skill.drawer.credConfigured") }}</el-tag>
              </div>
              <el-input-number
                v-else-if="param.type === 'number'"
                v-model="configDraft[param.name]"
                :min="0"
                style="width: 100%"
              />
              <el-switch
                v-else-if="param.type === 'boolean'"
                v-model="configDraft[param.name]"
              />
              <el-select
                v-else-if="param.type === 'select'"
                v-model="configDraft[param.name]"
                style="width: 100%"
              >
                <el-option
                  v-for="opt in param.options"
                  :key="opt"
                  :label="opt"
                  :value="opt"
                />
              </el-select>
            </el-form-item>
          </el-form>
        </div>
      </template>

      <template #footer>
        <el-button @click="drawerVisible = false">{{ t("common.action.cancel") }}</el-button>
        <el-button v-if="!selectedSkill?.builtin" type="danger" plain @click="handleUninstall">{{ t("common.action.uninstall") }}</el-button>
        <el-button type="primary" :loading="configSaving" @click="saveSkillConfig">{{ t("common.action.save") }}</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.skills-tab {
  margin-bottom: 20px;
}

.hidden-input {
  display: none;
}

.skill-card {
  border-radius: 8px;
  transition: all 0.2s;
  cursor: pointer;
  min-height: 128px;
}

.skill-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.skill-disabled {
  opacity: 0.65;
}

.skill-card-body {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.skill-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
  flex-shrink: 0;
}

.skill-info {
  flex: 1;
  min-width: 0;
}

.skill-name {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

.skill-name-text {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-builtin-tag {
  flex-shrink: 0;
  font-weight: 600;
}

.skill-desc {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 33.6px;
  white-space: normal;
  word-break: break-word;
}

.skill-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  min-height: 18px;
  flex-wrap: nowrap;
  overflow: hidden;
}

.skill-version,
.skill-meta-sep {
  flex-shrink: 0;
}

.skill-author {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-meta-sep {
  color: var(--el-border-color);
}

.skill-toggle {
  flex-shrink: 0;
  margin-top: 8px;
}

/* 安装卡片 - 高度与正常卡片一致 */
.skill-add-card {
  height: 100%;
  border: 1px dashed var(--el-border-color);
  background: transparent;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
}

.skill-add-card:hover {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.skill-add-body {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: 24px 0;
}

.install-dropdown {
  width: 100% !important;
  display: block !important;
}

/* 市场卡片 */
.marketplace-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--el-color-primary-light-9);
  flex-shrink: 0;
}

/* 分页 */
.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* 抽屉样式 */
.drawer-header {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.drawer-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 14px;
  background: var(--el-color-primary-light-9);
  flex-shrink: 0;
}

.drawer-name {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 600;
}

.drawer-desc {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.drawer-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.drawer-stat {
  margin-bottom: 16px;
  border-radius: 8px;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
}

.stat-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 13px;
  font-weight: 500;
}

.drawer-section-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
}

.drawer-config {
  margin-bottom: 16px;
}
</style>

<!-- 非 scoped：tooltip popper 渲染在 body 下，需全局样式 -->
<style>
.skill-desc-tooltip {
  max-width: 380px;
  white-space: pre-wrap;
  line-height: 1.5;
}
</style>
