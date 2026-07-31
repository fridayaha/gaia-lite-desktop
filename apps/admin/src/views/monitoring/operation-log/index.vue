<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, reactive, computed } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  getOperationLogsApi,
  type OperationLogItem
} from "@/api/manager/observability";
import { getUsersApi, type UserResponse } from "@/api/manager/users";

const { t, te } = useI18n();
const loading = ref(false);
const items = ref<OperationLogItem[]>([]);
const total = ref(0);
const users = ref<UserResponse[]>([]);

const filters = reactive({
  actor_id: "",
  action: "",
  target_type: "",
  status: "",
  keyword: "",
  timeRange: null as [string, string] | null
});

const pageSize = ref(10);
const currentPage = ref(1);

const detailVisible = ref(false);
const detailItem = ref<OperationLogItem | null>(null);

const targetTypeOptions = [
  { value: "user", label: () => tr("operationLog.targetType.user", "user") },
  { value: "agent_instance", label: () => tr("operationLog.targetType.agent_instance", "agent_instance") },
  { value: "agent_definition", label: () => tr("operationLog.targetType.agent_definition", "agent_definition") },
  { value: "user_group", label: () => tr("operationLog.targetType.user_group", "user_group") },
  { value: "role", label: () => tr("operationLog.targetType.role", "role") },
  { value: "resource_pool", label: () => tr("operationLog.targetType.resource_pool", "resource_pool") },
  { value: "engine_config", label: () => tr("operationLog.targetType.engine_config", "engine_config") },
  { value: "agent_channel", label: () => tr("operationLog.targetType.agent_channel", "agent_channel") },
  { value: "agent_skill", label: () => tr("operationLog.targetType.agent_skill", "agent_skill") },
  { value: "litellm_model", label: () => tr("operationLog.targetType.litellm_model", "litellm_model") },
  { value: "litellm_key", label: () => tr("operationLog.targetType.litellm_key", "litellm_key") },
  { value: "litellm_team", label: () => tr("operationLog.targetType.litellm_team", "litellm_team") }
];

// 全量 action 列表（从 services/manager/app/ log_operation 调用点扫描）
// value 是原值供查询用，label 用翻译后的"领域 · 动作"格式展示
const ALL_ACTIONS = [
  "auth.login", "auth.refresh",
  "user.create", "user.update", "user.delete",
  "user_group.create", "user_group.update", "user_group.delete",
  "role.create", "role.update", "role.delete",
  "agent_definition.create", "agent_definition.update", "agent_definition.delete", "agent_definition.publish",
  "agent_instance.create", "agent_instance.update", "agent_instance.delete", "agent_instance.clone",
  "agent_instance.deploy", "agent_instance.suspend", "agent_instance.resume", "agent_instance.restart",
  "agent_instance.destroy", "agent_instance.offline", "agent_instance.publish",
  "agent_instance.reprovision_key", "agent_instance.switch_version", "agent_instance.upgrade",
  "agent_channel.create", "agent_channel.update", "agent_channel.delete",
  "agent_skill.install", "agent_skill.uninstall", "agent_skill.toggle", "agent_skill.reorder", "agent_skill.credentials_save",
  "resource_pool.create", "resource_pool.update", "resource_pool.delete", "resource_pool.clone",
  "engine_config.upsert", "engine_config.delete",
  "litellm_model.create", "litellm_model.update", "litellm_model.delete",
  "litellm_team.sync",
  "litellm_key.create", "litellm_key.update", "litellm_key.delete", "litellm_key.block", "litellm_key.unblock"
];

const actionOptions = ALL_ACTIONS.map(action => ({
  value: action,
  label: () => formatAction(action).label
}));

// 二级级联选项：第一级是 domain（鉴权/用户/智能体/...），第二级是该 domain 下的 verb
const actionCascadeOptions = computed(() => {
  const groups: Record<string, string[]> = {};
  for (const action of ALL_ACTIONS) {
    const [domain] = action.split(".");
    if (!groups[domain]) groups[domain] = [];
    groups[domain].push(action);
  }
  return Object.entries(groups).map(([domain, actions]) => ({
    value: domain,
    label: tr(`operationLog.actionDomain.${domain}`, domain),
    children: actions.map(action => {
      const verb = action.split(".")[1];
      return {
        value: action,
        label: tr(`operationLog.actionVerb.${verb}`, verb)
      };
    })
  }));
});

// cascader v-model 是 [domain, action] 数组，选中后取 action 部分传给后端
const actionCascade = ref<string[]>([]);

function onActionCascadeChange(val: string[] | null) {
  if (val && val.length === 2) {
    filters.action = val[1];
  } else {
    filters.action = "";
  }
  onSearch();
}

const statusOptions = [
  { value: "success", label: () => tr("operationLog.status.success", "success") },
  { value: "failure", label: () => tr("operationLog.status.failure", "failure") }
];

function tr(key: string, fallback: string): string {
  return te(key) ? t(key) : fallback;
}

function formatAction(action: string): { label: string; raw: string } {
  if (!action) return { label: "", raw: "" };
  const parts = action.split(".");
  if (parts.length === 2) {
    const dKey = `operationLog.actionDomain.${parts[0]}`;
    const vKey = `operationLog.actionVerb.${parts[1]}`;
    const d = te(dKey) ? t(dKey) : parts[0];
    const v = te(vKey) ? t(vKey) : parts[1];
    return { label: `${d} · ${v}`, raw: action };
  }
  return { label: action, raw: action };
}

function formatTargetType(type: string): { label: string; raw: string } {
  if (!type) return { label: "", raw: "" };
  const key = `operationLog.targetType.${type}`;
  return { label: te(key) ? t(key) : type, raw: type };
}

function formatStatus(status: string): { label: string; raw: string } {
  if (!status) return { label: "", raw: "" };
  const key = `operationLog.status.${status}`;
  return { label: te(key) ? t(key) : status, raw: status };
}

function targetDisplay(item: OperationLogItem): { label: string; tooltip: string } {
  if (!item.target_id) return { label: "—", tooltip: "" };
  const prefix = targetPrefix(item.target_type);
  if (item.target_name) {
    return { label: `${prefix}${item.target_name}`, tooltip: item.target_id };
  }
  // 业务对象已删除（如 resource_pool.delete），target_name 解析不到，
  // 也加前缀让用户知道目标类型，如 "资源池: 7f4f2df2…"
  return { label: `${prefix}${item.target_id.slice(0, 8)}…`, tooltip: item.target_id };
}

function targetPrefix(type: string): string {
  if (!type) return "";
  const key = `operationLog.targetPrefix.${type}`;
  return te(key) ? `${t(key)}: ` : `${type}: `;
}

const shortcuts = [
  {
    text: "今天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return [start, end];
    }
  },
  {
    text: "近 7 天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(end.getTime() - 3600 * 1000 * 24 * 7);
      return [start, end];
    }
  },
  {
    text: "近 30 天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(end.getTime() - 3600 * 1000 * 24 * 30);
      return [start, end];
    }
  }
];

async function loadUsers() {
  try {
    const resp = await getUsersApi({ page: 1, page_size: 200 });
    users.value = resp.items || [];
  } catch (e: any) {
    // 加载用户列表失败不影响主功能
  }
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, any> = {
      pageSize: pageSize.value,
      currentPage: currentPage.value
    };
    if (filters.actor_id) params.actor_id = filters.actor_id;
    if (filters.action) params.action = filters.action;
    if (filters.target_type) params.target_type = filters.target_type;
    if (filters.status) params.status = filters.status;
    if (filters.keyword) params.keyword = filters.keyword;
    if (filters.timeRange && filters.timeRange.length === 2) {
      params.time_from = filters.timeRange[0];
      params.time_to = filters.timeRange[1];
    }
    const resp = await getOperationLogsApi(params);
    items.value = resp.data.list;
    total.value = resp.data.total;
  } catch (e: any) {
    ElMessage.error(e?.message || "查询失败");
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  filters.actor_id = "";
  filters.action = "";
  filters.target_type = "";
  filters.status = "";
  filters.keyword = "";
  filters.timeRange = null;
  currentPage.value = 1;
  load();
}

function onSearch() {
  currentPage.value = 1;
  load();
}

function onPageChange(page: number) {
  currentPage.value = page;
  load();
}

function onSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
  load();
}

function showDetail(row: OperationLogItem) {
  detailItem.value = row;
  detailVisible.value = true;
}

function formatDetail(d: Record<string, any> | null): string {
  if (!d) return "";
  try {
    return JSON.stringify(d, null, 2);
  } catch {
    return String(d);
  }
}

function actorLabel(row: OperationLogItem): string {
  if (row.actor_name) {
    return row.actor_real_name ? `${row.actor_real_name} (${row.actor_name})` : row.actor_name;
  }
  return t("operationLog.targetDeleted");
}

function formatTime(ts: string | null): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return ts;
  }
}

onMounted(() => {
  loadUsers();
  load();
});
</script>

<template>
  <div class="main">
    <DocsLink to="monitoring.html#operation-log" />
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="filters.actor_id"
          filterable
          clearable
          placeholder="操作人"
          style="width: 200px"
          @change="onSearch"
        >
          <el-option
            v-for="u in users"
            :key="u.id"
            :label="u.real_name ? `${u.real_name} (${u.username})` : u.username"
            :value="u.id"
          />
        </el-select>
        <el-cascader
          v-model="actionCascade"
          :options="actionCascadeOptions"
          :props="{ expandTrigger: 'hover' }"
          filterable
          clearable
          placeholder="动作"
          style="width: 220px"
          @change="onActionCascadeChange"
        >
          <template #default="{ data }">
            <span>{{ data.label }}</span>
          </template>
        </el-cascader>
        <el-select
          v-model="filters.target_type"
          filterable
          clearable
          placeholder="目标类型"
          style="width: 160px"
          @change="onSearch"
        >
          <el-option v-for="opt in targetTypeOptions" :key="opt.value" :label="opt.label()" :value="opt.value" />
        </el-select>
        <el-select
          v-model="filters.status"
          filterable
          clearable
          placeholder="状态"
          style="width: 120px"
          @change="onSearch"
        >
          <el-option v-for="opt in statusOptions" :key="opt.value" :label="opt.label()" :value="opt.value" />
        </el-select>
        <el-date-picker
          v-model="filters.timeRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          :shortcuts="shortcuts"
          style="width: 380px"
          @change="onSearch"
        />
        <el-input
          v-model="filters.keyword"
          placeholder="detail 关键字"
          clearable
          style="width: 200px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-button @click="resetFilters">重置</el-button>
        <el-button type="primary" @click="onSearch">刷新</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" stripe style="width: 100%">
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作人" width="180">
        <template #default="{ row }">
          <span v-if="row.actor_name">{{ actorLabel(row) }}</span>
          <el-tag v-else type="info" size="small">{{ t("operationLog.targetDeleted") }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作IP" width="140">
        <template #default="{ row }">
          <span v-if="row.operator_ip" class="text-sm">{{ row.operator_ip }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="用户代理" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.operator_user_agent" class="text-sm font-mono">{{ row.operator_user_agent }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="动作" min-width="200">
        <template #default="{ row }">
          <el-tooltip :content="formatAction(row.action).raw" placement="top" :disabled="!row.action">
            <span>{{ formatAction(row.action).label }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="目标类型" width="140">
        <template #default="{ row }">
          <el-tooltip :content="formatTargetType(row.target_type).raw" placement="top" :disabled="!row.target_type">
            <span>{{ formatTargetType(row.target_type).label }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="目标" min-width="200">
        <template #default="{ row }">
          <el-tooltip :content="targetDisplay(row).tooltip" placement="top" :disabled="!targetDisplay(row).tooltip">
            <span v-if="row.target_name" class="text-sm">{{ targetDisplay(row).label }}</span>
            <span v-else-if="row.target_id" class="text-sm">{{ targetDisplay(row).label }}</span>
            <span v-else>—</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
            {{ formatStatus(row.status).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="showDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="mt-4 flex justify-end">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>

    <el-drawer v-model="detailVisible" title="操作日志详情" size="50%">
      <template v-if="detailItem">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="ID">{{ detailItem.id }}</el-descriptions-item>
          <el-descriptions-item label="时间">{{ formatTime(detailItem.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="操作人">{{ actorLabel(detailItem) }}</el-descriptions-item>
          <el-descriptions-item label="操作IP">{{ detailItem.operator_ip || "—" }}</el-descriptions-item>
          <el-descriptions-item label="用户代理">
            <span v-if="detailItem.operator_user_agent" class="font-mono text-xs break-all">{{ detailItem.operator_user_agent }}</span>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="动作">
            {{ formatAction(detailItem.action).label }}
            <span class="ml-2 text-xs text-gray-400 font-mono">{{ formatAction(detailItem.action).raw }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="目标类型">
            {{ formatTargetType(detailItem.target_type).label }}
            <span class="ml-2 text-xs text-gray-400 font-mono">{{ formatTargetType(detailItem.target_type).raw }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="目标">
            <div v-if="detailItem.target_name" class="flex items-center gap-2">
              <span>{{ targetPrefix(detailItem.target_type) }}{{ detailItem.target_name }}</span>
              <span class="text-xs text-gray-400 font-mono">{{ detailItem.target_id }}</span>
            </div>
            <div v-else-if="detailItem.target_id" class="flex items-center gap-2">
              <span>{{ targetPrefix(detailItem.target_type) }}{{ detailItem.target_id.slice(0, 8) }}…</span>
              <span class="text-xs text-gray-400 font-mono">{{ detailItem.target_id }}</span>
            </div>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="detailItem.status === 'success' ? 'success' : 'danger'" size="small">
              {{ formatStatus(detailItem.status).label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="用户组 ID">{{ detailItem.group_id || "—" }}</el-descriptions-item>
        </el-descriptions>
        <div class="mt-4 text-sm text-gray-500">Detail</div>
        <pre class="mt-2 p-3 bg-gray-50 rounded text-xs overflow-auto max-h-96">{{ formatDetail(detailItem.detail) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>
