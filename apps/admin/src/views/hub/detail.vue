<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  getHubItemApi,
  listHubVersionsApi,
  createHubVersionApi,
  submitHubReviewApi,
  approveHubVersionApi,
  rejectHubVersionApi,
  publishHubVersionApi,
  getHubScanReportApi,
  type HubItem,
  type HubItemVersion,
  type ScanReport,
} from "@/api/hub";
import { useSubscribe } from "./useSubscribe";

defineOptions({ name: "HubDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const itemId = route.params.id as string;

// ── 能力项详情（真实 API） ────────────────────────────────
const item = ref<HubItem | null>(null);
const itemLoading = ref(false);

// ── 版本/扫描 ────────────────────────────────────────────
const versions = ref<HubItemVersion[]>([]);
const scanReport = ref<ScanReport | null>(null);
const vLoading = ref(false);
const scanLoading = ref(false);
const showCreateVersion = ref(false);
const newVersion = ref({ version: "1.0.0", description: "" });

async function fetchItem() {
  itemLoading.value = true;
  try {
    item.value = await getHubItemApi(itemId);
  } catch {
    ElMessage.error(t("hub.msg.loadFailed"));
    router.push("/hub/index");
  } finally {
    itemLoading.value = false;
  }
}

async function fetchVersions() {
  vLoading.value = true;
  try {
    versions.value = (await listHubVersionsApi(itemId)) || [];
  } catch {
    versions.value = [];
  } finally {
    vLoading.value = false;
  }
}

async function fetchScan() {
  scanLoading.value = true;
  try {
    // 扫描报告按 version_id 查询：优先 current_version_id，否则取最新版本
    let vid = item.value?.current_version_id;
    if (!vid && versions.value.length) {
      vid = versions.value[0].id;
    }
    if (!vid) {
      scanReport.value = null;
      return;
    }
    scanReport.value = await getHubScanReportApi(vid);
  } catch {
    scanReport.value = null;
  } finally {
    scanLoading.value = false;
  }
}

async function createVersion() {
  try {
    await createHubVersionApi(itemId, {
      version: newVersion.value.version,
      description: newVersion.value.description
    });
    ElMessage.success(t("hub.msg.versionCreated"));
    showCreateVersion.value = false;
    await fetchVersions();
    await fetchScan();
  } catch {
    ElMessage.error(t("hub.msg.versionCreateFailed"));
  }
}

async function submitReview(vid: string) {
  await submitHubReviewApi(vid);
  ElMessage.success(t("hub.msg.submitted"));
  fetchVersions();
}

async function approve(vid: string) {
  await approveHubVersionApi(vid);
  ElMessage.success(t("hub.msg.approved"));
  fetchVersions();
}

async function reject(vid: string) {
  await rejectHubVersionApi(vid);
  ElMessage.success(t("hub.msg.rejected"));
  fetchVersions();
}

async function publish(vid: string) {
  await publishHubVersionApi(vid);
  ElMessage.success(t("hub.msg.published"));
  fetchVersions();
}

const statusColor: Record<string, string> = {
  draft: "info",
  pending_review: "warning",
  approved: "",
  published: "success",
  rejected: "danger",
  disabled: "danger",
  archived: "warning",
};
const typeColor: Record<string, string> = { agent: "success", skill: "warning", tool: "info", mcp: "danger" };
const riskColor: Record<string, string> = {
  low: "success",
  medium: "warning",
  high: "danger",
  blocking: "danger",
};

function statusLabel(s: string): string {
  return t(`hub.status.${s}`);
}

const formatDate = computed(() => {
  const d = item.value?.created_at;
  if (!d) return "—";
  return d;
});

// ── 订阅（共享 composable，仅 skill 可订阅） ──────────────
const {
  subscribeVisible, subscribeTarget, templates, templateLoading, selectedTemplateId, subscribing,
  subscribedMap, openSubscribe, doSubscribe, getSubscribedTemplateIds, isAlreadySubscribed,
} = useSubscribe();

const canSubscribe = computed(() => item.value?.type === "skill");
const isSubscribed = computed(() => !!subscribedMap.value[itemId]?.length);
const getSubscribedAgents = () => {
  const ids = getSubscribedTemplateIds(itemId);
  return ids.map(id => templates.value.find(t2 => t2.id === id)?.name || id).filter(Boolean);
};

onMounted(async () => {
  await fetchItem();
  await fetchVersions();
  await fetchScan();
});
</script>

<template>
  <div class="main">
    <el-page-header :content="item?.name || t('hub.detailTitle')" class="mb-4" @back="router.push('/hub/index')" />

    <!-- 基本信息 -->
    <el-card v-if="item" class="mb-4" shadow="never">
      <template #header>
        <div class="flex justify-between items-center">
          <span>{{ t("hub.section.basic") }}</span>
          <el-button v-if="canSubscribe" :type="isSubscribed ? 'success' : 'primary'" @click="item && openSubscribe(item)">
            {{ isSubscribed ? t("hub.subscribe.cancelSub") : t("hub.subscribe.short") }}
          </el-button>
        </div>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item :label="t('hub.col.name')">{{ item.name }}</el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.type')">
          <el-tag :type="(typeColor[item.type] || '') as any" size="small">{{ t(`hub.overview.${item.type}`) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.status')">
          <el-tag :type="(statusColor[item.status] || '') as any" size="small">
            {{ statusLabel(item.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.risk')">
          <el-tag :type="(riskColor[item.risk_level || ''] || 'info') as any" size="small">
            {{ item.risk_level ? t(`hub.risk.${item.risk_level}`) : "—" }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.description')" :span="2">
          {{ item.description || "—" }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.industry')">{{ item.industry || "—" }}</el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.scenario')">{{ item.scenario || "—" }}</el-descriptions-item>
        <!-- 新增字段 -->
        <el-descriptions-item label="标签" :span="2">
          <div class="flex flex-wrap gap-1" v-if="item.tags?.length">
            <el-tag v-for="tag in item.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
          </div>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="可发现">
          <el-tag size="small" :type="item.discoverable ? 'success' : 'info'" effect="plain">
            {{ item.discoverable ? "是" : "否" }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="精选">
          <el-tag size="small" :type="item.featured ? 'warning' : 'info'" effect="plain">
            {{ item.featured ? t("hub.featured") : t("hub.all") }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.creator')">{{ t(`hub.creator.${item.created_by}`) || item.created_by || "—" }}</el-descriptions-item>
        <el-descriptions-item :label="t('hub.col.source')">{{ t(`hub.sourceType.${item.source_type}`) || item.source_type || "—" }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDate }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 版本管理 + 审批工作流 -->
    <el-card v-if="item" class="mb-4" shadow="never">
      <template #header>
        <div class="flex justify-between items-center">
          <span>{{ t("hub.section.versions") }}</span>
          <el-button type="primary" size="small" @click="showCreateVersion = true">
            {{ t("hub.action.createVersion") }}
          </el-button>
        </div>
      </template>
      <el-empty v-if="versions.length === 0" description="暂无版本记录" :image-size="60" />
      <el-table v-else :data="versions" v-loading="vLoading" stripe size="small" border>
        <el-table-column prop="version" :label="t('hub.col.versionNo')" width="100" />
        <el-table-column prop="status" :label="t('hub.col.status')" width="110">
          <template #default="{ row }">
            <el-tag :type="(statusColor[row.status] || '') as any" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_level" :label="t('hub.col.risk')" width="90">
          <template #default="{ row }">
            {{ row.risk_level ? t(`hub.risk.${row.risk_level}`) : "—" }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" :label="t('hub.col.createdAt')" width="170" />
        <el-table-column :label="t('hub.col.operation')" width="300">
          <template #default="{ row }">
            <el-button v-if="row.status === 'draft'" link type="warning" size="small" @click="submitReview(row.id)">
              {{ t("hub.action.submitReview") }}
            </el-button>
            <template v-if="row.status === 'pending_review'">
              <el-button link type="success" size="small" @click="approve(row.id)">
                {{ t("hub.action.approve") }}
              </el-button>
              <el-button link type="danger" size="small" @click="reject(row.id)">
                {{ t("hub.action.reject") }}
              </el-button>
            </template>
            <el-button v-if="row.status === 'approved'" link type="primary" size="small" @click="publish(row.id)">
              {{ t("hub.action.publish") }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 安全扫描结果 (bisect) -->
    <el-card v-if="item" shadow="never">
      <template #header>
        <div class="flex justify-between items-center">
          <span>{{ t("hub.section.scan") }}</span>
          <el-tag v-if="scanReport" :type="(riskColor[scanReport.risk_level] || 'info') as any" size="small">
            {{ t(`hub.risk.${scanReport.risk_level}`) }} · {{ scanReport.finding_count }}
          </el-tag>
        </div>
      </template>
      <el-empty v-if="scanLoading" description="加载中..." :image-size="60" />
      <el-empty v-else-if="!scanReport || !scanReport.findings?.length" :description="t('hub.msg.noScanFindings')" :image-size="60" />
      <el-table v-else :data="scanReport.findings" stripe size="small" border>
        <el-table-column prop="severity" :label="t('hub.col.risk')" width="100">
          <template #default="{ row }">
            <el-tag :type="(riskColor[row.severity] || 'info') as any" size="small">
              {{ t(`hub.risk.${row.severity}`) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rule_id" :label="t('hub.col.ruleId')" width="180" />
        <el-table-column prop="message" :label="t('hub.col.message')" min-width="240" show-overflow-tooltip />
        <el-table-column prop="location" :label="t('hub.col.location')" width="180" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 创建版本弹窗 -->
    <el-dialog v-model="showCreateVersion" :title="t('hub.action.createVersion')" width="400px">
      <el-form :model="newVersion" label-width="80px">
        <el-form-item :label="t('hub.col.versionNo')">
          <el-input v-model="newVersion.version" placeholder="1.0.0" />
        </el-form-item>
        <el-form-item :label="t('hub.col.description')">
          <el-input v-model="newVersion.description" type="textarea" :placeholder="t('hub.col.description')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateVersion = false">{{ t("common.action.cancel") }}</el-button>
        <el-button type="primary" @click="createVersion">{{ t("common.action.confirm") }}</el-button>
      </template>
    </el-dialog>

    <!-- ── 订阅对话框 ── -->
    <el-dialog v-model="subscribeVisible" :title="t('hub.subscribe.title')" width="500px" destroy-on-close>
      <template v-if="subscribeTarget">
        <el-descriptions :column="1" border size="small" class="mb-4">
          <el-descriptions-item label="能力名称">{{ subscribeTarget.name }}</el-descriptions-item>
          <el-descriptions-item label="能力类型">
            <el-tag size="small" :type="(typeColor[subscribeTarget.type] || '') as any">{{ subscribeTarget.type }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <el-tag size="small" :type="(riskColor[subscribeTarget.risk_level || ''] || 'info') as any">{{ subscribeTarget.risk_level ? t(`hub.risk.${subscribeTarget.risk_level}`) : "—" }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-form label-width="90px" size="default">
          <el-form-item :label="t('hub.subscribe.targetAgent')">
            <el-select v-model="selectedTemplateId" :placeholder="t('hub.subscribe.targetAgentPlaceholder')" :loading="templateLoading" style="width: 100%">
              <el-option v-for="tpl in templates" :key="tpl.id" :label="`${tpl.name} (${tpl.status === 'PUBLISHED' ? t('hub.subscribe.tplPublished') : t('hub.subscribe.tplDraft')})`" :value="tpl.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="isSubscribed" :label="t('hub.subscribe.currentSub')">
            <div class="flex flex-wrap gap-1">
              <el-tag v-for="sa in getSubscribedAgents()" :key="sa" size="small" effect="dark" type="success">{{ sa }}</el-tag>
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
