<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import { message } from "@/utils/message";
import {
  getPoolPodsApi,
  getPoolPodLogsApi,
  getPoolPodLogSourcesApi,
  type PoolPod,
  type PodLogProfile,
  type PoolPodLogSources
} from "@/api/manager/resourcePools";

defineOptions({ name: "ResourcePoolLogsTab" });

const props = defineProps<{
  poolId: string;
  initialPodName?: string;
}>();

const { t } = useI18n();
const selectedPod = ref("");
const logs = ref("");
const loading = ref(false);
const autoRefresh = ref(true);
const podOptions = ref<PoolPod[]>([]);

// 日志来源：engine=容器 stdout；gateway=<profile> 网关日志
const source = ref<"engine" | "gateway">("gateway");
const profile = ref<string>("");
const profileOptions = ref<PodLogProfile[]>([]);
const tailLines = ref<number>(500);

// Profile 标签：真实姓名(用户名)，无用户信息时回退 profile_name
function profileLabel(p: PodLogProfile): string {
  if (p.real_name && p.username) return `${p.real_name}(${p.username})`;
  return p.username || p.real_name || p.profile_name;
}

// Pod 选择器标签：附带智能体名称
function podLabel(pod: PoolPod): string {
  return pod.agent_name ? `${pod.name}（${pod.agent_name}）` : pod.name;
}

const logContainerRef = ref<HTMLElement | null>(null);
let refreshTimer: ReturnType<typeof setInterval> | null = null;

const tailLineOptions = [200, 500, 1000, 2000];

async function fetchPods() {
  try {
    const res = await getPoolPodsApi(props.poolId);
    podOptions.value = res.items;
    if (props.initialPodName && res.items.some(p => p.name === props.initialPodName)) {
      selectedPod.value = props.initialPodName;
    } else if (!selectedPod.value && res.items.length > 0) {
      selectedPod.value = res.items[0].name;
    }
  } catch {
    message(t("engine.log.msg.fetchPodsFailed"), { type: "error" });
  }
}

async function fetchSources() {
  if (!selectedPod.value) {
    profileOptions.value = [];
    return;
  }
  try {
    const res: PoolPodLogSources = await getPoolPodLogSourcesApi(props.poolId, selectedPod.value);
    profileOptions.value = res.profiles || [];
    // 默认选第一个 profile 网关日志（比 nginx stdout 更有用）；无则回落 engine
    if (profileOptions.value.length > 0) {
      const names = profileOptions.value.map(p => p.profile_name);
      if (!profile.value || !names.includes(profile.value)) {
        profile.value = profileOptions.value[0].profile_name;
      }
      source.value = "gateway";
    } else {
      source.value = "engine";
      profile.value = "";
    }
  } catch {
    profileOptions.value = [];
    source.value = "engine";
  }
}

async function fetchLogs() {
  if (!selectedPod.value) return;
  loading.value = true;
  try {
    const res = await getPoolPodLogsApi(props.poolId, selectedPod.value, {
      tailLines: tailLines.value,
      source: source.value,
      profile: source.value === "gateway" ? profile.value : undefined
    });
    logs.value = res.logs || "";
    await nextTick();
    if (logContainerRef.value) {
      logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight;
    }
  } catch {
    message(t("engine.log.msg.fetchLogsFailed"), { type: "error" });
  } finally {
    loading.value = false;
  }
}

function onPodChange() {
  profile.value = "";
  fetchSources().then(fetchLogs);
}

watch(selectedPod, onPodChange);
watch([source, profile, tailLines], fetchLogs);

watch(autoRefresh, val => {
  if (val) {
    fetchLogs();
    refreshTimer = setInterval(fetchLogs, 5000);
  } else if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
});

onMounted(fetchPods);
</script>

<template>
  <div class="logs-tab">
    <!-- 控制栏 -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <span class="text-sm text-gray-500">{{ t("engine.log.selectPod") }}</span>
      <el-select
        v-model="selectedPod"
        :placeholder="t('engine.log.selectPlaceholder')"
        style="width: 340px"
        clearable
      >
        <el-option
          v-for="pod in podOptions"
          :key="pod.name"
          :label="podLabel(pod)"
          :value="pod.name"
        />
      </el-select>

      <span class="text-sm text-gray-500">{{ t("engine.log.source") }}</span>
      <el-select v-model="source" style="width: 140px">
        <el-option :label="t('engine.log.sourceEngine')" value="engine" />
        <el-option
          :label="t('engine.log.sourceGateway')"
          value="gateway"
          :disabled="profileOptions.length === 0"
        />
      </el-select>

      <el-select
        v-if="source === 'gateway'"
        v-model="profile"
        style="width: 240px"
        :placeholder="t('engine.log.selectProfile')"
      >
        <el-option
          v-for="p in profileOptions"
          :key="p.profile_name"
          :label="profileLabel(p)"
          :value="p.profile_name"
        />
      </el-select>

      <span class="text-sm text-gray-500">{{ t("engine.log.tailLines") }}</span>
      <el-select v-model="tailLines" style="width: 100px">
        <el-option
          v-for="n in tailLineOptions"
          :key="n"
          :label="String(n)"
          :value="n"
        />
      </el-select>

      <el-switch v-model="autoRefresh" :active-text="t('engine.log.autoRefresh')" />
      <el-button :loading="loading" @click="fetchLogs">
        {{ t("engine.log.refresh") }}
      </el-button>
    </div>

    <!-- 日志内容 -->
    <div v-if="!selectedPod" class="text-center py-16 text-gray-400">
      <p>{{ t("engine.log.empty") }}</p>
    </div>
    <div v-else-if="loading && !logs" class="text-center py-16 text-gray-400">
      <p>{{ t("engine.loading") }}</p>
    </div>
    <div v-else ref="logContainerRef" class="log-container">
      <pre class="log-content">{{ logs || t("engine.log.empty") }}</pre>
    </div>
  </div>
</template>

<style scoped>
.logs-tab {
  margin-bottom: 20px;
}

.log-container {
  background: #1e1e2e;
  border-radius: 8px;
  overflow: auto;
  max-height: 560px;
}

.log-content {
  margin: 0;
  padding: 16px;
  font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #cdd6f4;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
