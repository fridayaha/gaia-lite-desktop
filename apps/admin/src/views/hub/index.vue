<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  listHubItemsApi,
  createHubItemApi,
  createHubVersionApi,
  type HubItem,
  type HubItemType,
} from "@/api/hub";
import { ALL_TAGS, THIRD_PARTY_PLATFORMS } from "./mock";
import HubCard from "./components/HubCard.vue";
import { useSubscribe } from "./useSubscribe";
import Robot2Line from "~icons/ri/robot-2-line";
import MagicLine from "~icons/ri/magic-line";
import ToolsLine from "~icons/ri/tools-line";
import ServerLine from "~icons/ri/server-line";
import SearchLine from "~icons/ri/search-line";
import Download2Line from "~icons/ri/download-2-line";

defineOptions({ name: "HubIndex" });

const router = useRouter();
const { t } = useI18n();

// ── 类别图标映射 ──
const catIconMap: Record<string, any> = {
  agent: Robot2Line,
  skill: MagicLine,
  tool: ToolsLine,
  mcp: ServerLine,
};

// ── 列表数据（服务端分页 + 筛选） ──
const items = ref<HubItem[]>([]);
const total = ref(0);
const loading = ref(false);

// ── 总览统计（一次拉全量按 type 聚合） ──
const statsCounts = reactive<Record<string, number>>({ agent: 0, skill: 0, tool: 0, mcp: 0 });
const tagPool = ref<string[]>([...ALL_TAGS]);

const categoryStats = computed(() => [
  { type: "agent", label: t("hub.overview.agent"), count: statsCounts.agent, icon: Robot2Line, bgColor: "#41b6ff18", iconColor: "#41b6ff", subLabel: t("hub.overview.agent") + " · 通用+行业" },
  { type: "skill", label: t("hub.overview.skill"), count: statsCounts.skill, icon: MagicLine, bgColor: "#f59e0b18", iconColor: "#f59e0b", subLabel: "可组合原子能力" },
  { type: "tool", label: t("hub.overview.tool"), count: statsCounts.tool, icon: ToolsLine, bgColor: "#00a87018", iconColor: "#00a870", subLabel: "API 集成 & 数据处理" },
  { type: "mcp", label: t("hub.overview.mcp"), count: statsCounts.mcp, icon: ServerLine, bgColor: "#9b59b618", iconColor: "#9b59b6", subLabel: "标准化协议接入" },
]);

// ── 筛选 ──
const activeType = ref<HubItemType | "">("");
const showFeaturedOnly = ref(false);
const searchText = ref("");

// ── 分页 ──
const currentPage = ref(1);
const pageSize = ref(12);

function buildParams() {
  return {
    type: activeType.value || undefined,
    featured: showFeaturedOnly.value ? true : undefined,
    keyword: searchText.value.trim() || undefined,
    skip: (currentPage.value - 1) * pageSize.value,
    limit: pageSize.value,
  };
}

async function fetchList() {
  loading.value = true;
  try {
    const res = await listHubItemsApi(buildParams());
    items.value = res.items || [];
    total.value = res.total || 0;
  } catch {
    items.value = [];
    total.value = 0;
    ElMessage.error(t("hub.msg.loadFailed"));
  } finally {
    loading.value = false;
  }
}

async function fetchStats() {
  // 拉全量按 type 聚合统计 + 收集标签池
  try {
    const res = await listHubItemsApi({ limit: 100 });
    const all = res.items || [];
    statsCounts.agent = all.filter(i => i.type === "agent").length;
    statsCounts.skill = all.filter(i => i.type === "skill").length;
    statsCounts.tool = all.filter(i => i.type === "tool").length;
    statsCounts.mcp = all.filter(i => i.type === "mcp").length;
    const tagSet = new Set<string>(ALL_TAGS);
    all.forEach(i => (i.tags || []).forEach(tg => tagSet.add(tg)));
    tagPool.value = Array.from(tagSet);
  } catch {
    // 统计失败不阻塞列表
  }
}

function resetPage() { currentPage.value = 1; }
function onFilterChange() { resetPage(); fetchList(); }
function handleSizeChange(size: number) { pageSize.value = size; currentPage.value = 1; fetchList(); }
function handleCurrentChange(page: number) { currentPage.value = page; fetchList(); }

// 搜索防抖
let searchTimer: ReturnType<typeof setTimeout> | null = null;
function onSearchInput() {
  resetPage();
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(fetchList, 350);
}

// ── 导入 ──
const importVisible = ref(false);
const importType = ref<HubItemType>("skill");
const importTab = ref("custom");
const importTypeLabel = computed(() => ({
  agent: t("hub.overview.agent"), skill: t("hub.overview.skill"), tool: t("hub.overview.tool"), mcp: t("hub.overview.mcp"),
}[importType.value] || ""));

const importForm = reactive({
  name: "", description: "", manifest: "", industry: "", scenario: "", tags: [] as string[],
});
const thirdPartyForm = reactive({ platform: "", identifier: "", syncMode: "once" });

function openImport(type: HubItemType) {
  importType.value = type;
  importTab.value = "custom";
  importForm.name = ""; importForm.description = ""; importForm.manifest = "";
  importForm.industry = ""; importForm.scenario = ""; importForm.tags = [];
  thirdPartyForm.platform = ""; thirdPartyForm.identifier = ""; thirdPartyForm.syncMode = "once";
  importVisible.value = true;
}

async function doImportCustom() {
  if (!importForm.name.trim()) {
    ElMessage.warning(t("hub.import.nameRequired"));
    return;
  }
  try {
    const item = await createHubItemApi({
      name: importForm.name.trim(),
      type: importType.value,
      description: importForm.description || undefined,
      industry: importForm.industry || undefined,
      scenario: importForm.scenario || undefined,
    });
    // 创建首版本（若有 manifest 填入 config_json）
    let configJson: Record<string, any> | undefined;
    if (importForm.manifest.trim()) {
      try { configJson = JSON.parse(importForm.manifest); } catch { configJson = { raw: importForm.manifest }; }
    }
    await createHubVersionApi(item.id, {
      version: "1.0.0",
      description: importForm.description || undefined,
      config_json: configJson || {},
    });
    importVisible.value = false;
    ElMessage.success(t("hub.import.successMsg", { name: item.name }));
    fetchList();
    fetchStats();
  } catch {
    ElMessage.error(t("hub.import.failed"));
  }
}

// ── 订阅（列表页用共享 composable） ──
const {
  subscribeVisible, subscribeTarget, templates, templateLoading, selectedTemplateId, subscribing,
  subscribedMap, openSubscribe, doSubscribe, getSubscribedTemplateIds, isAlreadySubscribed,
} = useSubscribe();

function getSubscribedAgents(itemId: string): string[] {
  const ids = getSubscribedTemplateIds(itemId);
  return ids.map(id => templates.value.find(t2 => t2.id === id)?.name || id).filter(Boolean);
}

// ── 跳转详情 ──
function goToDetail(item: HubItem) {
  router.push(`/hub/detail/${item.id}`);
}

onMounted(() => {
  fetchList();
  fetchStats();
});
</script>

<template>
  <div class="main">
    <DocsLink to="hub.html" />
    <!-- ① 总览统计卡 -->
    <el-row :gutter="12" class="mb-4">
      <el-col :xs="12" :sm="6" v-for="cat in categoryStats" :key="cat.type" class="mb-2">
        <el-card shadow="never" class="stat-card">
          <div class="stat-header">
            <span class="stat-label">{{ cat.label }}</span>
            <div class="stat-icon" :style="{ background: cat.bgColor, color: cat.iconColor }">
              <component :is="cat.icon" width="16" height="16" />
            </div>
          </div>
          <span class="stat-number">{{ cat.count }}</span>
          <p class="stat-change">{{ cat.subLabel }}</p>
          <div class="stat-actions">
            <el-button size="small" type="primary" plain @click="openImport(cat.type as HubItemType)">
              <Download2Line width="14" height="14" class="mr-1" />
              {{ t("hub.import.title", { type: cat.label }) }}
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ② 类别 + 精选 + 标签 + 搜索 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-radio-group v-model="activeType" size="default" @change="onFilterChange">
          <el-radio-button value="">
            {{ t("hub.all") }}
          </el-radio-button>
          <el-radio-button value="agent">{{ t("hub.overview.agent") }}</el-radio-button>
          <el-radio-button value="skill">{{ t("hub.overview.skill") }}</el-radio-button>
          <el-radio-button value="tool">{{ t("hub.overview.tool") }}</el-radio-button>
          <el-radio-button value="mcp">{{ t("hub.overview.mcp") }}</el-radio-button>
        </el-radio-group>
        <el-switch v-model="showFeaturedOnly" :active-text="t('hub.featured')" @change="onFilterChange" />
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-input v-model="searchText" :placeholder="t('hub.filter.searchPlaceholder')" clearable style="width: 260px" @input="onSearchInput" @clear="onFilterChange">
          <template #suffix>
            <el-icon v-show="searchText.length === 0"><SearchLine /></el-icon>
          </template>
        </el-input>
      </div>
    </div>

    <!-- ③ 卡片网格 -->
    <el-empty v-if="loading" description="加载中..." :image-size="80" class="mt-4" />
    <el-row v-else :gutter="12">
      <el-col v-for="item in items" :key="item.id" :xs="24" :sm="12" :md="8" :lg="6" class="mb-3">
        <HubCard
          :item="item"
          :subscribed="!!subscribedMap[item.id]?.length"
          :subscribed-agents="getSubscribedAgents(item.id)"
          @click="goToDetail(item)"
          @subscribe="openSubscribe(item)"
        />
      </el-col>
    </el-row>

    <!-- 空态 -->
    <el-empty v-if="!loading && items.length === 0" :description="t('hub.msg.loadFailed')" :image-size="80" class="mt-4" />

    <!-- ④ 分页条 -->
    <el-pagination
      v-if="total > 0"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[12, 24, 36, 48]"
      layout="total, sizes, prev, pager, next, jumper"
      background
      style="display: flex; justify-content: flex-end; clear: both; margin-top: 16px;"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />

    <!-- ── 导入对话框 ── -->
    <el-dialog v-model="importVisible" :title="t('hub.import.title', { type: importTypeLabel })" width="620px" destroy-on-close>
      <el-tabs v-model="importTab">
        <!-- 自定义导入 -->
        <el-tab-pane :label="t('hub.import.custom')" name="custom">
          <el-form :model="importForm" label-width="80px" size="default">
            <el-form-item :label="t('hub.import.name')">
              <el-input v-model="importForm.name" :placeholder="t('hub.import.namePlaceholder')" />
            </el-form-item>
            <el-form-item label="类型">
              <el-tag :type="(typeColor[importType] || '') as any" effect="light">{{ typeLabel(importType) }}</el-tag>
              <span class="text-xs text-[var(--el-text-color-secondary)] ml-2">{{ t("hub.import.typeAuto") }}</span>
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="importForm.description" type="textarea" :rows="2" :placeholder="t('hub.import.descriptionPlaceholder')" />
            </el-form-item>
            <el-form-item :label="t('hub.import.manifest')">
              <el-input v-model="importForm.manifest" type="textarea" :rows="4" :placeholder="t('hub.import.manifestPlaceholder')" />
            </el-form-item>
            <el-form-item label="行业">
              <el-input v-model="importForm.industry" :placeholder="t('hub.import.industryPlaceholder')" />
            </el-form-item>
            <el-form-item label="场景">
              <el-input v-model="importForm.scenario" :placeholder="t('hub.import.scenarioPlaceholder')" />
            </el-form-item>
            <el-form-item label="标签">
              <el-select v-model="importForm.tags" multiple filterable allow-create default-first-option :placeholder="t('hub.import.tagsPlaceholder')" style="width: 100%">
                <el-option v-for="tag in tagPool" :key="tag" :label="tag" :value="tag" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <!-- 第三方导入 -->
        <el-tab-pane :label="t('hub.import.thirdparty')" name="thirdparty">
          <el-form :model="thirdPartyForm" label-width="100px" size="default">
            <el-form-item :label="t('hub.import.platform')">
              <el-select v-model="thirdPartyForm.platform" :placeholder="t('hub.import.platform')" style="width: 100%">
                <el-option v-for="p in THIRD_PARTY_PLATFORMS" :key="p.value" :label="p.label" :value="p.value" />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('hub.import.identifier')">
              <el-input v-model="thirdPartyForm.identifier" :placeholder="t('hub.import.identifierPlaceholder')" />
            </el-form-item>
            <el-form-item :label="t('hub.import.syncMode')">
              <el-radio-group v-model="thirdPartyForm.syncMode">
                <el-radio value="once">{{ t("hub.import.syncOnce") }}</el-radio>
                <el-radio value="subscribe">{{ t("hub.import.syncSubscribe") }}</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-form>
          <el-alert :title="t('hub.import.comingSoon')" type="info" :closable="false" show-icon class="mt-3">
            <template #default>{{ t("hub.import.comingSoonMsg") }}</template>
          </el-alert>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="importVisible = false">{{ t("common.action.cancel") }}</el-button>
        <el-button v-if="importTab === 'custom'" type="primary" :loading="false" @click="doImportCustom">{{ t("hub.import.doImport") }}</el-button>
        <el-button v-if="importTab === 'thirdparty'" type="primary" disabled>{{ t("hub.import.comingSoon") }}</el-button>
      </template>
    </el-dialog>

    <!-- ── 订阅对话框 ── -->
    <el-dialog v-model="subscribeVisible" :title="t('hub.subscribe.title')" width="500px" destroy-on-close>
      <template v-if="subscribeTarget">
        <el-descriptions :column="1" border size="small" class="mb-4">
          <el-descriptions-item label="能力名称">{{ subscribeTarget.name }}</el-descriptions-item>
          <el-descriptions-item label="能力类型">
            <el-tag size="small" :type="(typeColor[subscribeTarget.type] || '') as any">{{ typeLabel(subscribeTarget.type) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <el-tag size="small" :type="(riskColor[subscribeTarget.risk_level || ''] || '') as any">{{ riskLabel(subscribeTarget.risk_level) }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-form label-width="90px" size="default">
          <el-form-item :label="t('hub.subscribe.targetAgent')">
            <el-select v-model="selectedTemplateId" :placeholder="t('hub.subscribe.targetAgentPlaceholder')" :loading="templateLoading" style="width: 100%">
              <el-option v-for="tpl in templates" :key="tpl.id" :label="`${tpl.name} (${tpl.status === 'PUBLISHED' ? t('hub.subscribe.tplPublished') : t('hub.subscribe.tplDraft')})`" :value="tpl.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="subscribeTarget && getSubscribedTemplateIds(subscribeTarget.id).length" :label="t('hub.subscribe.currentSub')">
            <div class="flex flex-wrap gap-1">
              <el-tag v-for="sa in getSubscribedAgents(subscribeTarget.id)" :key="sa" size="small" effect="dark" type="success">{{ sa }}</el-tag>
            </div>
          </el-form-item>
        </el-form>
        <el-alert type="info" :closable="false" show-icon class="mt-2" :title="t('hub.subscribe.installNote')" />
      </template>
      <template #footer>
        <el-button @click="subscribeVisible = false">{{ t("common.action.cancel") }}</el-button>
        <el-button type="primary" :loading="subscribing" :disabled="!selectedTemplateId || isAlreadySubscribed()" @click="doSubscribe">{{ t("hub.subscribe.confirm") }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script lang="ts">
// 非响应式辅助数据（不会被 reactive 包裹，用于模板）
const typeColor: Record<string, string> = { agent: "success", skill: "warning", tool: "info", mcp: "danger" };
const typeLabel = (t: string) => ({ agent: "Agent", skill: "Skill", tool: "Tool", mcp: "MCP" }[t] || t);
const riskColor: Record<string, string> = { low: "success", medium: "warning", high: "danger", blocking: "danger" };
const riskLabel = (r?: string) => ({ low: "低", medium: "中", high: "高", blocking: "阻断" }[r || ""] || r || "—");
</script>

<style scoped>
.stat-card { border-radius: 8px; }
.stat-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.stat-label { font-size: 13px; color: var(--el-text-color-secondary); }
.stat-icon { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 8px; }
.stat-number { font-size: 1.4em; font-weight: 600; }
.stat-change { margin: 4px 0 0; font-size: 12px; font-weight: 500; color: #909399; }
.stat-actions { display: flex; gap: 8px; margin-top: 12px; }
</style>
