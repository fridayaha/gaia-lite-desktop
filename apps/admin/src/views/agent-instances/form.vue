<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { makeFormRules } from "./utils/rule";
import { FormProps } from "./utils/types";
import HermesLogo from "./components/icons/HermesLogo.vue";
import OpenClawLogo from "./components/icons/OpenClawLogo.vue";
import DifyLogo from "./components/icons/DifyLogo.vue";
import QuestionLine from "~icons/ri/question-line";
import { message } from "@/utils/message";
import {
  createInstanceApi,
  updateInstanceApi,
  publishInstanceApi
} from "@/api/manager/agentInstances";
import { getVersionsApi } from "@/api/manager/agentDefinitions";
import {
  getEngineConfigApi,
  listDifyAppsApi,
  selectDifyAppApi,
  verifyDifyServiceApi
} from "@/api/manager/engineConfigs";

const { t } = useI18n();

const emit = defineEmits<{
  (e: "step-config-change"): void;
}>();

const props = withDefaults(defineProps<FormProps>(), {
  formInline: () => ({
    title: "create",
    name: "",
    description: "",
    definition_id: "",
    version_id: "",
    resource_pool_id: "",
    group_id: "",
    allDefinitions: [],
    allVersions: [],
    allResourcePools: [],
    allGroups: []
  })
});

const ruleFormRef = ref();
const newFormInline = ref({ ...props.formInline });
const activeStep = ref(0);
const isEdit = computed(() => newFormInline.value.title === "edit");

const groupCount = computed(() => (newFormInline.value.allGroups || []).length);
/** 单组用户自动归属其唯一组，不显示目标组选择；多组(或 0)显示。编辑模式可改组（连带定义/资源池需重选）。 */
const showGroupSelect = computed(() => groupCount.value !== 1);
const formRules = makeFormRules(() => groupCount.value, () => isDifyExternal.value);

const versionLoading = ref(false);

const selectedDefinition = computed(() => {
  const id = newFormInline.value.definition_id;
  if (!id) return null;
  return (newFormInline.value.allDefinitions || []).find(d => d.id === id) || null;
});

const selectedPool = computed(() => {
  const id = newFormInline.value.resource_pool_id;
  if (!id) return null;
  return (newFormInline.value.allResourcePools || []).find(p => p.id === id) || null;
});

/** 当前目标组可用的定义（实例必须与定义同组） */
const visibleDefinitions = computed(() => {
  const gid = newFormInline.value.group_id;
  const all = newFormInline.value.allDefinitions || [];
  if (!gid) return all;
  return all.filter(d => d.group_id === gid);
});

/** 当前目标组可用的资源池（平台共享池 + 本组私有池） */
const visiblePools = computed(() => {
  const gid = newFormInline.value.group_id;
  const all = newFormInline.value.allResourcePools || [];
  if (!gid) return all;
  return all.filter(p => !p.group_id || p.group_id === gid);
});

/** Dify 引擎实例需展示第 3 步「Dify 应用」 */
const isDifyEngine = computed(() => selectedDefinition.value?.engine_type === "DIFY");
/** 非多组用户的目标组选择是否隐藏 — 单组自动填，多组要选 */
const maxStep = computed(() => (isDifyEngine.value ? 2 : 1));

/** maxStep 或 activeStep 变化时通知父组件刷新底部按钮：
 *  - 创建时选 Dify 模版后 maxStep 1→2，step 1 按钮要从"确定"变"下一步"
 *  - 进入 step 2（Dify 应用配置）后 activeStep 2==maxStep 2，按钮要从"下一步"变"确定" */
watch([maxStep, activeStep], () => {
  emit("step-config-change");
});

// ── Dify 配置 refs ────────────────────────────────────
const difyEngineConfig = ref<any>(null);
const difyApps = ref<any[]>([]);
const loadingDifyApps = ref(false);
const verifyingApiKey = ref(false);
const verifiedAppName = ref("");
const difyAppId = ref("");
const difyAppName = ref("");
const difyAppType = ref<"chat" | "agent" | "workflow" | "">("");
const difyBaseUrl = ref("");
const difyApiKey = ref("");
const difySource = ref<"console" | "manual" | "">("");

// 运行时开关：浏览器沙箱（VNC 接管）启用
const browserSandboxEnabled = ref(false);

const difyManaged = computed(() => difyEngineConfig.value?.mode === "MANAGED");
const difyExternalWithAdmin = computed(
  () => difyEngineConfig.value?.mode === "EXTERNAL" && difyEngineConfig.value?.admin_password_configured
);
const difyExternalManual = computed(
  () =>
    difyEngineConfig.value?.mode === "EXTERNAL" &&
    !difyEngineConfig.value?.admin_password_configured
);
/** Dify 外接模式（含管理员下拉选 + 手填）— 不需要资源池；MANAGED 模式仍走 K8s 部署需要资源池 */
const isDifyExternal = computed(
  () => isDifyEngine.value && !difyManaged.value
);

/** 单组用户：自动填 group_id */
watch(
  () => newFormInline.value.allGroups,
  groups => {
    if (groups && groups.length === 1 && !newFormInline.value.group_id) {
      newFormInline.value.group_id = groups[0].id;
    }
  },
  { immediate: true }
);

async function loadVersions(definitionId: string) {
  if (!definitionId) {
    newFormInline.value.allVersions = [];
    return;
  }
  versionLoading.value = true;
  try {
    const res = await getVersionsApi(definitionId);
    newFormInline.value.allVersions = res || [];
  } catch (e: any) {
    message(
      e?.response?.data?.detail || t("instance.msg.loadVersionsFailed"),
      { type: "warning" }
    );
    newFormInline.value.allVersions = [];
  } finally {
    versionLoading.value = false;
  }
}

function pickDefaultVersionId(): string {
  const versions = newFormInline.value.allVersions || [];
  if (versions.length === 0) return "";
  const curId = selectedDefinition.value?.current_version_id;
  if (curId && versions.some(v => v.id === curId)) return curId;
  return versions[0]?.id ?? "";
}

async function onDefinitionChange(definitionId: string) {
  newFormInline.value.version_id = "";
  newFormInline.value.allVersions = [];
  await loadVersions(definitionId);
  if (!newFormInline.value.version_id) {
    newFormInline.value.version_id = pickDefaultVersionId();
  }
  // 切换定义时重置 Dify 配置（避免脏数据）
  resetDifyConfig();
  if (isDifyEngine.value) {
    await loadDifyEngineConfig();
  }
}

/** 目标组切换：清空定义/版本/资源池（定义需同组） */
function onGroupChange() {
  newFormInline.value.definition_id = "";
  newFormInline.value.version_id = "";
  newFormInline.value.allVersions = [];
  newFormInline.value.resource_pool_id = "";
  resetDifyConfig();
}

function resetDifyConfig() {
  difyAppId.value = "";
  difyAppName.value = "";
  difyAppType.value = "";
  difyBaseUrl.value = "";
  difyApiKey.value = "";
  difySource.value = "";
  verifiedAppName.value = "";
  difyApps.value = [];
}

// ── Dify 引擎配置 / 应用列表 ────────────────────────────────────

async function loadDifyEngineConfig() {
  try {
    const cfg = await getEngineConfigApi("DIFY");
    difyEngineConfig.value = cfg;
    if (cfg && cfg.mode === "EXTERNAL" && cfg.admin_password_configured) {
      await loadDifyApps(cfg.id);
    }
  } catch {
    difyEngineConfig.value = null;
  }
}

async function loadDifyApps(configId?: string) {
  const cid = configId || difyEngineConfig.value?.id;
  if (!cid) return;
  loadingDifyApps.value = true;
  try {
    const list = await listDifyAppsApi(cid);
    difyApps.value = list || [];
  } catch (e: any) {
    message(e?.response?.data?.detail || t("agent.form.msg.difyAppsLoadFailed"), {
      type: "error"
    });
    difyApps.value = [];
  } finally {
    loadingDifyApps.value = false;
  }
}

async function onDifyAppSelect(appId: string) {
  const cid = difyEngineConfig.value?.id;
  if (!cid || !appId) return;
  try {
    const result = await selectDifyAppApi(cid, appId);
    difyAppId.value = result.app_id;
    difyAppName.value = result.app_name;
    difyAppType.value = result.app_type;
    difyApiKey.value = result.app_api_key;
    difyBaseUrl.value = result.base_url;
    difySource.value = "console";
    verifiedAppName.value = "";
  } catch (e: any) {
    message(e?.response?.data?.detail || t("agent.form.msg.difyAppSelectFailed"), {
      type: "error"
    });
  }
}

async function verifyDifyApiKey() {
  if (!difyBaseUrl.value || !difyApiKey.value) {
    message(t("agent.form.msg.difyVerifyNeedBoth"), { type: "warning" });
    return;
  }
  verifyingApiKey.value = true;
  try {
    const result = await verifyDifyServiceApi(difyBaseUrl.value.trim(), difyApiKey.value);
    if (!result.app_type) {
      message(t("agent.form.msg.difyVerifyModeUnsupported"), { type: "warning" });
      return;
    }
    difyAppName.value = result.name;
    difyAppType.value = result.app_type;
    difySource.value = "manual";
    verifiedAppName.value = result.name;
    message(t("agent.form.msg.difyVerifySuccess", { name: result.name }), {
      type: "success"
    });
  } catch (e: any) {
    message(e?.response?.data?.detail || t("agent.form.msg.difyVerifyFailed"), {
      type: "error"
    });
  } finally {
    verifyingApiKey.value = false;
  }
}

onMounted(() => {
  if (isEdit.value && newFormInline.value.definition_id) {
    loadVersions(newFormInline.value.definition_id);
    if (isDifyEngine.value) {
      loadDifyEngineConfig().then(() => {
        // 编辑模式回填 dify_config
        const cfg = (newFormInline.value as any).dify_config;
        if (cfg) {
          difyBaseUrl.value = cfg.base_url || "";
          difyApiKey.value = cfg.app_api_key || "";
          difyAppType.value = cfg.app_type || "chat";
          difyAppId.value = cfg.app_id || "";
          difyAppName.value = cfg.app_name || "";
          difySource.value = cfg.source || "";
        }
      });
    }
    // 回填运行时开关
    browserSandboxEnabled.value = !!(
      (newFormInline.value as any).runtime_config?.browser_sandbox?.enabled
    );
  } else if (isEdit.value) {
    // 编辑模式但 definition_id 为空（不应该发生，兜底）
  }
});

watch(
  () => props.formInline,
  val => {
    newFormInline.value = { ...val };
    activeStep.value = 0;
    // 单组用户自动填 group_id：此 watch 的 {...val} 会覆盖 allGroups 自动填充 watch
    // 设入的 group_id（val.group_id 为 ""），且 allGroups 数组引用不变不会触发后者，
    // 故在此处兜底再填一次，避免单组用户创建时 group_id 为空被后端 422。
    if (
      !isEdit.value &&
      newFormInline.value.allGroups?.length === 1 &&
      !newFormInline.value.group_id
    ) {
      newFormInline.value.group_id = newFormInline.value.allGroups[0].id;
    }
    if (isEdit.value && newFormInline.value.definition_id) {
      loadVersions(newFormInline.value.definition_id);
      if (isDifyEngine.value) {
        loadDifyEngineConfig().then(() => {
          const cfg = (newFormInline.value as any).dify_config;
          if (cfg) {
            difyBaseUrl.value = cfg.base_url || "";
            difyApiKey.value = cfg.app_api_key || "";
            difyAppType.value = cfg.app_type || "chat";
            difyAppId.value = cfg.app_id || "";
            difyAppName.value = cfg.app_name || "";
            difySource.value = cfg.source || "";
          }
        });
      }
      browserSandboxEnabled.value = !!(
        (newFormInline.value as any).runtime_config?.browser_sandbox?.enabled
      );
    }
  },
  { deep: true, immediate: true }
);

function getRef() {
  return ruleFormRef.value;
}

function getCurrentStep() {
  return activeStep.value;
}

function getMaxStep() {
  return maxStep.value;
}

function prevStep() {
  if (activeStep.value > 0) activeStep.value--;
}

/** 校验当前步，前进或提交 */
async function submitStep(): Promise<boolean> {
  const valid = await ruleFormRef.value.validate().catch(() => false);
  if (!valid) return false;

  // 非最后一步 → 前进
  if (activeStep.value < maxStep.value) {
    activeStep.value++;
    return false;
  }

  // 最后一步 → 提交
  try {
    const payload: any = {
      name: newFormInline.value.name,
      description: newFormInline.value.description,
      definition_id: newFormInline.value.definition_id,
      version_id: newFormInline.value.version_id || undefined,
      resource_pool_id: isDifyExternal.value ? null : newFormInline.value.resource_pool_id,
      group_id: newFormInline.value.group_id
    };

    // Dify 引擎实例：附加 dify_config
    if (isDifyEngine.value) {
      if (difyManaged.value) {
        // MANAGED 模式：dify_config 留空（worker 走 K8s 部署 Dify Pod）
        payload.dify_config = {};
      } else if (difyExternalWithAdmin.value) {
        // EXTERNAL + 配了管理员账号：走选应用下拉
        payload.dify_config = {
          base_url: difyBaseUrl.value || "",
          app_api_key: difyApiKey.value || "",
          app_type: difyAppType.value || "chat",
          app_id: difyAppId.value || "",
          app_name: difyAppName.value || "",
          source: "console"
        };
      } else {
        // EXTERNAL + 未配管理员账号：手填
        payload.dify_config = {
          base_url: (difyBaseUrl.value || "").trim(),
          app_api_key: difyApiKey.value || "",
          app_type: difyAppType.value || "chat",
          app_id: "",
          app_name: difyAppName.value || "",
          source: "manual"
        };
      }
    }

    // 运行时开关：浏览器沙箱（VNC 接管）启用与否
    payload.runtime_config = {
      browser_sandbox: { enabled: !!browserSandboxEnabled.value }
    };

    let createdId: string | undefined;
    if (!isEdit.value) {
      const created = await createInstanceApi(payload);
      createdId = created?.id;
    } else {
      await updateInstanceApi(newFormInline.value.id!, payload);
    }

    // Dify 外接实例：创建后自动上线（无 Pod 部署，publish 只是注册外部 URL 到 routing 表）
    if (!isEdit.value && isDifyExternal.value && createdId) {
      try {
        await publishInstanceApi(createdId);
        message(t("instance.msg.createOkAndOnline"), { type: "success" });
      } catch (err: any) {
        message(
          err?.response?.data?.detail || t("instance.msg.createOkButOnlineFailed"),
          { type: "warning" }
        );
      }
    } else {
      message(
        t(isEdit.value ? "instance.msg.editOk" : "instance.msg.createOk"),
        { type: "success" }
      );
    }
    return true;
  } catch (err: any) {
    message(
      err?.response?.data?.detail || t("common.msg.operationFailed"),
      { type: "error" }
    );
    return false;
  }
}

defineExpose({ getRef, getCurrentStep, getMaxStep, submitStep, prevStep });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="formRules"
    label-width="100px"
    label-position="top"
  >
    <!-- Steps indicator -->
    <el-steps :active="activeStep" align-center class="mb-6">
      <el-step :title="t('instance.form.step.basic')" />
      <el-step :title="t('instance.form.step.pool')" />
      <el-step v-if="isDifyEngine" :title="t('instance.form.step.dify')" />
    </el-steps>

    <!-- ======== Step 0: 目标组(多组时) + 实例名 + 描述 ======== -->
    <div v-if="activeStep === 0" class="form-section">
      <el-form-item
        v-if="showGroupSelect"
        :label="t('instance.form.field.selectGroup')"
        prop="group_id"
      >
        <el-select
          v-model="newFormInline.group_id"
          :placeholder="t('instance.form.field.selectGroupPlaceholder')"
          filterable
          style="width: 100%"
          @change="onGroupChange"
        >
          <el-option
            v-for="g in newFormInline.allGroups"
            :key="g.id"
            :label="g.name"
            :value="g.id"
          />
        </el-select>
      </el-form-item>

      <el-row :gutter="24">
        <el-col :span="12">
          <el-form-item :label="t('instance.form.field.name')" prop="name">
            <el-input
              v-model="newFormInline.name"
              clearable
              :placeholder="t('instance.form.field.namePlaceholder')"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item :label="t('instance.form.field.description')" prop="description">
            <el-input
              v-model="newFormInline.description"
              clearable
              :placeholder="t('instance.form.field.descriptionPlaceholder')"
            />
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <!-- ======== Step 1: 选定义 + 版本 + 资源池（按目标组过滤）======== -->
    <div v-if="activeStep === 1" class="form-section">
      <el-form-item :label="t('instance.form.field.definition')" prop="definition_id">
        <el-select
          v-model="newFormInline.definition_id"
          :placeholder="t('instance.form.field.definitionPlaceholder')"
          filterable
          style="width: 100%"
          @change="onDefinitionChange"
        >
          <el-option
            v-for="d in visibleDefinitions"
            :key="d.id"
            :label="d.name"
            :value="d.id"
          >
            <span class="def-option">
              <HermesLogo v-if="d.engine_type === 'HERMES'" class="def-option-logo" />
              <OpenClawLogo v-else-if="d.engine_type === 'OPENCLAW'" class="def-option-logo" />
              <DifyLogo v-else-if="d.engine_type === 'DIFY'" class="def-option-logo" />
              <span>{{ d.name }}</span>
              <span class="def-option-type">{{ d.engine_type }}</span>
            </span>
          </el-option>
        </el-select>
        <div v-if="selectedDefinition" class="mt-2 p-3 bg-gray-50 rounded text-xs text-gray-500">
          <span>{{ selectedDefinition.description || t("instance.noDescription") }}</span>
        </div>
      </el-form-item>

      <el-form-item :label="t('instance.form.field.version')" prop="version_id">
        <el-select
          v-model="newFormInline.version_id"
          :placeholder="t('instance.form.field.versionPlaceholder')"
          :loading="versionLoading"
          :disabled="!newFormInline.definition_id"
          style="width: 100%"
        >
          <el-option
            v-for="v in newFormInline.allVersions"
            :key="v.id"
            :label="v.version_no"
            :value="v.id"
          >
            <span>{{ v.version_no }}</span>
            <span class="version-changelog">{{ v.change_log }}</span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item v-if="!isDifyExternal" :label="t('instance.form.field.resourcePool')" prop="resource_pool_id">
        <el-select
          v-model="newFormInline.resource_pool_id"
          :placeholder="t('instance.form.field.resourcePoolPlaceholder')"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="p in visiblePools"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          >
            <span class="pool-option">
              <span class="pool-option-name">{{ p.name }}</span>
              <span class="pool-option-spec">
                CPU {{ p.min_cpu }}~{{ p.max_cpu }} · {{ p.min_memory }}~{{ p.max_memory }}
              </span>
            </span>
          </el-option>
        </el-select>
        <div v-if="selectedPool" class="mt-2 p-3 bg-gray-50 rounded text-xs text-gray-500">
          <span>CPU: {{ selectedPool.min_cpu }}~{{ selectedPool.max_cpu }}</span>
          <el-divider direction="vertical" />
          <span>{{ t("agent.form.field.memory") }}: {{ selectedPool.min_memory }}~{{ selectedPool.max_memory }}</span>
          <el-divider direction="vertical" />
          <span>{{ t("engine.card.replicas") }}: {{ selectedPool.min_replicas }}~{{ selectedPool.max_replicas }}</span>
        </div>
      </el-form-item>

      <!-- 运行时开关：浏览器沙箱（仅 Hermes 引擎实例） -->
      <el-form-item v-if="!isDifyEngine">
        <template #label>
          {{ t("instance.form.field.browserSandbox") }}
          <el-tooltip effect="dark" placement="top">
            <template #content>
              <div style="max-width: 280px; line-height: 1.6;">
                {{ t("instance.form.field.browserSandboxHint") }}
              </div>
            </template>
            <el-icon class="browser-sandbox-tip-icon">
              <QuestionLine />
            </el-icon>
          </el-tooltip>
        </template>
        <el-checkbox v-model="browserSandboxEnabled">
          {{ t("instance.form.field.browserSandboxEnable") }}
        </el-checkbox>
        <p class="browser-sandbox-resource-hint">
          {{ t("instance.form.field.browserSandboxResourceHint") }}
        </p>
      </el-form-item>
    </div>

    <!-- ======== Step 2: Dify 应用（仅 DIFY 引擎实例显示）======== -->
    <div v-if="isDifyEngine && activeStep === 2" class="form-section">
      <el-alert
        :title="t('agent.form.field.difyInstanceStepHint')"
        type="info"
        :closable="false"
        show-icon
        class="mb-4"
      />

      <!-- MANAGED 模式：提示无需配置 -->
      <el-alert
        v-if="difyManaged"
        :title="t('agent.form.field.difyManagedHint')"
        type="success"
        :closable="false"
        show-icon
      />

      <!-- EXTERNAL + 管理员账号：下拉选 + 手填切换 -->
      <template v-else-if="difyExternalWithAdmin">
        <el-form-item :label="t('agent.form.field.difyApp')">
          <div class="w-full flex items-center gap-2">
            <el-select
              v-model="difyAppId"
              :placeholder="t('agent.form.field.difyAppPlaceholder')"
              :loading="loadingDifyApps"
              filterable
              style="flex: 1"
              @change="onDifyAppSelect"
            >
              <el-option
                v-for="app in difyApps"
                :key="app.id"
                :label="app.name"
                :value="app.id"
              >
                <span class="dify-app-option">
                  <span>{{ app.name }}</span>
                  <span class="dify-app-mode">{{ app.mode }}</span>
                </span>
              </el-option>
            </el-select>
            <el-button :loading="loadingDifyApps" @click="loadDifyApps()">
              {{ t("agent.form.field.difyRefreshApps") }}
            </el-button>
          </div>
          <p class="text-gray-400 text-xs mt-1">{{ t("agent.form.field.difyAppHint") }}</p>
        </el-form-item>

        <div v-if="difyAppName" class="mt-2 p-3 bg-gray-50 rounded text-xs text-gray-600">
          <span>{{ t("agent.form.field.difySelectedApp") }}：{{ difyAppName }}</span>
          <el-divider direction="vertical" />
          <span>App Type: {{ difyAppType }}</span>
          <el-divider direction="vertical" />
          <span>Base URL: {{ difyBaseUrl || "—" }}</span>
        </div>
      </template>

      <!-- EXTERNAL + 未配管理员账号：仅手填 -->
      <template v-else-if="difyExternalManual">
        <el-form-item :label="t('agent.form.field.difyBaseUrl')">
          <el-input
            v-model="difyBaseUrl"
            clearable
            :placeholder="t('agent.form.field.difyBaseUrlPlaceholder')"
          />
          <p class="text-gray-400 text-xs mt-1">{{ t("agent.form.field.difyBaseUrlHint") }}</p>
        </el-form-item>
        <el-form-item :label="t('agent.form.field.difyApiKey')">
          <el-input
            v-model="difyApiKey"
            type="password"
            show-password
            clearable
            :placeholder="t('agent.form.field.difyApiKeyPlaceholder')"
          />
          <p class="text-gray-400 text-xs mt-1">{{ t("agent.form.field.difyApiKeyHint") }}</p>
        </el-form-item>
        <el-form-item :label="t('agent.form.field.difyAppType')">
          <el-select
            v-model="difyAppType"
            :placeholder="t('agent.form.field.difyAppTypePlaceholder')"
            style="width: 100%"
          >
            <el-option label="Chat（对话型）" value="chat" />
            <el-option label="Agent（智能体）" value="agent" />
            <el-option label="Workflow（工作流）" value="workflow" />
          </el-select>
          <p class="text-gray-400 text-xs mt-1">{{ t("agent.form.field.difyAppTypeHint") }}</p>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            plain
            :loading="verifyingApiKey"
            @click="verifyDifyApiKey"
          >
            {{ t("agent.form.field.difyVerifyBtn") }}
          </el-button>
          <el-tag v-if="verifiedAppName" type="success" class="ml-3">
            {{ t("agent.form.field.difyVerifiedApp") }}：{{ verifiedAppName }}
          </el-tag>
        </el-form-item>
      </template>

      <!-- EngineConfig 未配置 -->
      <el-alert
        v-else
        :title="t('agent.form.field.difyAppHint')"
        type="warning"
        :closable="false"
        show-icon
      />
    </div>
  </el-form>
</template>

<style scoped>
.form-section {
  min-height: 200px;
}

.def-option {
  display: flex;
  align-items: center;
  gap: 6px;
}

.def-option-logo {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.def-option-type {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-left: auto;
}

.version-changelog {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-left: 8px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pool-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.pool-option-name {
  font-weight: 500;
}

.pool-option-spec {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-family: var(--el-font-family-mono, monospace);
}

.dify-app-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.dify-app-mode {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-family: var(--el-font-family-mono, monospace);
}

/* 浏览器沙箱：label 旁的提示图标 + 资源提醒 */
.browser-sandbox-tip-icon {
  margin-left: 4px;
  vertical-align: middle;
  color: var(--el-text-color-secondary);
  cursor: help;
  font-size: 14px;
}

.browser-sandbox-resource-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>
