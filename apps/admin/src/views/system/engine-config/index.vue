<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, computed, onMounted, reactive } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  getEngineConfigApi,
  upsertEngineConfigApi,
  testConnectionApi,
  testLangfuseApi,
  listDifyAppsApi,
  type EngineConfigResponse,
  type EngineConfigUpsert,
  type DifyAppOption
} from "@/api/manager/engineConfigs";
import Settings3Line from "~icons/ri/settings-3-line";

defineOptions({ name: "SystemEngineConfig" });

const { t } = useI18n();

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const testingLangfuse = ref(false);
const loadingApps = ref(false);
const config = ref<EngineConfigResponse | null>(null);
const difyApps = ref<DifyAppOption[]>([]);

const form = reactive<EngineConfigUpsert>({
  mode: "EXTERNAL",
  base_url: "",
  admin_email: "",
  admin_password: "",
  langfuse_host: "",
  langfuse_public_key: "",
  langfuse_secret_key: ""
});

const isExternal = computed(() => form.mode === "EXTERNAL");

async function loadConfig() {
  loading.value = true;
  try {
    const cfg = await getEngineConfigApi("DIFY");
    config.value = cfg;
    if (cfg) {
      form.mode = cfg.mode;
      form.base_url = cfg.base_url || "";
      form.admin_email = cfg.admin_email || "";
      form.admin_password = ""; // 留空表示不修改
      form.langfuse_host = cfg.langfuse_host || "";
      form.langfuse_public_key = cfg.langfuse_public_key || "";
      form.langfuse_secret_key = ""; // 留空表示不修改
      // 如果配了管理员账号，自动加载应用列表
      if (cfg.admin_password_configured && cfg.mode === "EXTERNAL") {
        await loadDifyApps();
      }
    }
  } catch (e: any) {
    ElMessage.error(t("system.engineConfig.saveFailed") + ": " + (e?.message || ""));
  } finally {
    loading.value = false;
  }
}

async function save() {
  // EXTERNAL 模式必须填 base_url
  if (isExternal.value && !form.base_url) {
    ElMessage.error(t("system.engineConfig.baseUrl") + " " + t("common.rule.required"));
    return;
  }
  saving.value = true;
  try {
    const payload: EngineConfigUpsert = {
      engine_type: "DIFY",
      mode: form.mode,
      base_url: isExternal.value ? form.base_url : null,
      admin_email: isExternal.value ? form.admin_email || null : null,
      admin_password: isExternal.value && form.admin_password ? form.admin_password : null,
      langfuse_host: isExternal.value ? form.langfuse_host || null : null,
      langfuse_public_key: isExternal.value ? form.langfuse_public_key || null : null,
      langfuse_secret_key:
        isExternal.value && form.langfuse_secret_key ? form.langfuse_secret_key : null
    };
    const cfg = await upsertEngineConfigApi(payload);
    config.value = cfg;
    form.admin_password = ""; // 清空密码字段
    form.langfuse_secret_key = ""; // 清空 secret_key 字段
    ElMessage.success(t("system.engineConfig.saved"));
    // 保存后如果配了 admin，自动加载应用列表
    if (cfg.admin_password_configured && cfg.mode === "EXTERNAL") {
      await loadDifyApps();
    } else {
      difyApps.value = [];
    }
  } catch (e: any) {
    ElMessage.error(t("system.engineConfig.saveFailed") + ": " + (e?.message || ""));
  } finally {
    saving.value = false;
  }
}

async function testConnection() {
  if (!config.value?.id) {
    ElMessage.warning(t("system.engineConfig.saveFailed"));
    return;
  }
  testing.value = true;
  try {
    const r = await testConnectionApi(config.value.id);
    if (r.ok) {
      ElMessage.success(t("system.engineConfig.testSuccess", { count: r.apps_count ?? 0 }));
      await loadDifyApps();
    } else {
      ElMessage.error(t("system.engineConfig.testFailed", { error: r.error || "" }));
    }
  } catch (e: any) {
    ElMessage.error(t("system.engineConfig.testFailed", { error: e?.message || "" }));
  } finally {
    testing.value = false;
  }
}

async function testLangfuse() {
  if (!config.value?.id) {
    ElMessage.warning(t("system.engineConfig.saveFailed"));
    return;
  }
  testingLangfuse.value = true;
  try {
    const r = await testLangfuseApi(config.value.id);
    if (r.ok) {
      ElMessage.success(
        t("system.engineConfig.testLangfuseSuccess", { count: r.trace_count ?? 0 })
      );
    } else {
      ElMessage.error(t("system.engineConfig.testLangfuseFailed", { error: r.error || "" }));
    }
  } catch (e: any) {
    ElMessage.error(t("system.engineConfig.testLangfuseFailed", { error: e?.message || "" }));
  } finally {
    testingLangfuse.value = false;
  }
}

async function loadDifyApps() {
  if (!config.value?.id) return;
  loadingApps.value = true;
  try {
    difyApps.value = await listDifyAppsApi(config.value.id);
  } catch (e: any) {
    ElMessage.error(t("system.engineConfig.appsLoadFailed", { error: e?.message || "" }));
    difyApps.value = [];
  } finally {
    loadingApps.value = false;
  }
}

const adminConfigured = computed(() => config.value?.admin_password_configured === true);
const showDifyApps = computed(
  () => isExternal.value && adminConfigured.value && !!config.value?.id
);

onMounted(loadConfig);
</script>

<template>
  <div v-loading="loading" class="main">
    <DocsLink to="system.html#engine-config" />
    <div class="welcome" style="max-width: 1100px">
      <!-- 页面标题 -->
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
          <IconifyIconOffline
            :icon="Settings3Line"
            v-bind="{ width: '22', height: '22', color: '#6366f1' } as any"
          />
          <h3 class="text-lg font-semibold m-0">{{ t("system.engineConfig.title") }}</h3>
        </div>
      </div>
      <p class="text-sm text-gray-500 mb-6 -mt-2">{{ t("system.engineConfig.subtitle") }}</p>

      <!-- Dify 引擎配置卡片 -->
      <el-card shadow="never" class="mb-4">
        <template #header>
          <span class="font-semibold">{{ t("system.engineConfig.difyTitle") }}</span>
        </template>

        <el-form label-width="140px" class="engine-config-form">
          <!-- 对接模式 -->
          <el-form-item :label="t('system.engineConfig.mode')">
            <el-radio-group v-model="form.mode">
              <el-radio value="EXTERNAL">
                <div class="flex flex-col">
                  <span>{{ t("system.engineConfig.modeExternal") }}</span>
                  <span class="text-xs text-gray-400">{{
                    t("system.engineConfig.modeExternalDesc")
                  }}</span>
                </div>
              </el-radio>
              <el-radio value="MANAGED">
                <div class="flex flex-col">
                  <span>{{ t("system.engineConfig.modeManaged") }}</span>
                  <span class="text-xs text-gray-400">{{
                    t("system.engineConfig.modeManagedDesc")
                  }}</span>
                </div>
              </el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- 外接模式才显示 base_url + 管理员凭据 -->
          <template v-if="isExternal">
            <el-form-item :label="t('system.engineConfig.baseUrl')">
              <el-input
                v-model="form.base_url"
                :placeholder="t('system.engineConfig.baseUrlPlaceholder')"
                clearable
                style="max-width: 480px"
              />
            </el-form-item>

            <el-form-item :label="t('system.engineConfig.adminEmail')">
              <el-input
                v-model="form.admin_email"
                :placeholder="t('system.engineConfig.adminEmailPlaceholder')"
                clearable
                style="max-width: 360px"
              />
            </el-form-item>

            <el-form-item :label="t('system.engineConfig.adminPassword')">
              <div class="flex flex-col gap-2">
                <div class="flex items-center gap-3 flex-wrap">
                  <el-input
                    v-model="form.admin_password"
                    type="password"
                    show-password
                    :placeholder="adminConfigured
                      ? t('system.engineConfig.adminPasswordPlaceholderConfigured')
                      : t('system.engineConfig.adminPasswordPlaceholder')"
                    clearable
                    style="max-width: 360px"
                  />
                  <el-tag v-if="adminConfigured" type="success" size="small">
                    {{ t("system.engineConfig.adminPasswordConfigured") }}
                  </el-tag>
                  <el-tag v-else type="info" size="small">
                    {{ t("system.engineConfig.adminPasswordNotConfigured") }}
                  </el-tag>
                </div>
                <p class="text-xs text-gray-400 leading-relaxed" style="max-width: 540px; margin: 0">
                  {{ t("system.engineConfig.adminOptionalHint") }}
                </p>
              </div>
            </el-form-item>

            <!-- 测试 Dify 连接（放在管理员密码配置下面） -->
            <el-form-item v-if="adminConfigured && config?.id">
              <el-button :loading="testing" @click="testConnection">
                {{ testing ? t("system.engineConfig.testing") : t("system.engineConfig.testConnection") }}
              </el-button>
            </el-form-item>

            <!-- Langfuse 集成配置（Dify 外接模式 per-app 用量反查用） -->
            <el-divider content-position="left">
              <span class="text-sm font-medium">{{ t("system.engineConfig.langfuseIntegration") }}</span>
            </el-divider>
            <el-form-item :label="t('system.engineConfig.langfuseHost')">
              <el-input
                v-model="form.langfuse_host"
                :placeholder="t('system.engineConfig.langfuseHostPlaceholder')"
                clearable
                style="max-width: 480px"
              />
            </el-form-item>
            <el-form-item :label="t('system.engineConfig.langfusePublicKey')">
              <el-input
                v-model="form.langfuse_public_key"
                :placeholder="t('system.engineConfig.langfusePublicKeyPlaceholder')"
                clearable
                style="max-width: 480px"
              />
            </el-form-item>
            <el-form-item :label="t('system.engineConfig.langfuseSecretKey')">
              <div class="flex flex-col gap-2">
                <div class="flex items-center gap-3 flex-wrap">
                  <el-input
                    v-model="form.langfuse_secret_key"
                    type="password"
                    show-password
                    :placeholder="config?.langfuse_secret_key_configured
                      ? t('system.engineConfig.langfuseSecretKeyPlaceholderConfigured')
                      : t('system.engineConfig.langfuseSecretKeyPlaceholder')"
                    clearable
                    style="max-width: 480px"
                  />
                  <el-tag v-if="config?.langfuse_secret_key_configured" type="success" size="small">
                    {{ t("system.engineConfig.langfuseSecretKeyConfigured") }}
                  </el-tag>
                  <el-tag v-else type="info" size="small">
                    {{ t("system.engineConfig.langfuseSecretKeyNotConfigured") }}
                  </el-tag>
                </div>
                <p class="text-xs text-gray-400 leading-relaxed" style="max-width: 540px; margin: 0">
                  {{ t("system.engineConfig.langfuseHint") }}
                </p>
              </div>
            </el-form-item>

            <!-- 测试 Langfuse 连接（放在 Secret Key 配置下面） -->
            <el-form-item v-if="config?.langfuse_secret_key_configured && config?.id">
              <el-button :loading="testingLangfuse" @click="testLangfuse">
                {{ testingLangfuse
                  ? t("system.engineConfig.testingLangfuse")
                  : t("system.engineConfig.testLangfuse") }}
              </el-button>
            </el-form-item>
          </template>

          <!-- 保存按钮 -->
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="save">
              {{ t("system.engineConfig.save") }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- Dify 应用列表（外接 + 配了管理员账号才显示） -->
      <el-card v-if="showDifyApps" shadow="never">
        <template #header>
          <div class="flex items-center justify-between">
            <div class="flex flex-col">
              <span class="font-semibold">{{ t("system.engineConfig.difyAppsTitle") }}</span>
              <span class="text-xs text-gray-400">{{
                t("system.engineConfig.difyAppsSubtitle")
              }}</span>
            </div>
            <el-button :loading="loadingApps" size="small" @click="loadDifyApps">
              <IconifyIconOffline
                :icon="Settings3Line"
                v-bind="{ width: '14', height: '14' } as any"
                class="mr-1"
              />
              {{ t("system.engineConfig.refreshApps") }}
            </el-button>
          </div>
        </template>

        <el-table v-loading="loadingApps" :data="difyApps" stripe>
          <el-table-column
            :label="t('system.engineConfig.col.appName')"
            prop="name"
            min-width="200"
          />
          <el-table-column :label="t('system.engineConfig.col.mode')" prop="mode" width="160">
            <template #default="{ row }">
              <el-tag size="small">{{ row.mode }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('system.engineConfig.col.description')"
            prop="description"
            min-width="240"
            show-overflow-tooltip
          />
          <el-table-column :label="t('system.engineConfig.col.appId')" prop="id" width="280">
            <template #default="{ row }">
              <code class="text-xs">{{ row.id }}</code>
            </template>
          </el-table-column>
          <template #empty>
            <div class="py-4 text-gray-400">{{ t("system.engineConfig.noApps") }}</div>
          </template>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.engine-config-form :deep(.el-radio__label) {
  white-space: normal;
}
</style>
