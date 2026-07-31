<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, computed, onMounted, reactive, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  listSmsConfigsApi,
  createSmsConfigApi,
  updateSmsConfigApi,
  activateSmsConfigApi,
  deactivateSmsConfigApi,
  deleteSmsConfigApi,
  testSmsConfigApi,
  type SmsConfigResponse,
  type SmsConfigCreate,
  type SmsProvider
} from "@/api/manager/smsConfigs";
import {
  listEmailConfigsApi,
  createEmailConfigApi,
  updateEmailConfigApi,
  activateEmailConfigApi,
  deactivateEmailConfigApi,
  deleteEmailConfigApi,
  testEmailConfigApi,
  type EmailConfigResponse,
  type EmailConfigCreate,
  type EmailProvider,
  type EmailEncryption
} from "@/api/manager/emailConfigs";
import Plus from "~icons/ri/add-line";
import ShieldKeyhole from "~icons/ri/shield-keyhole-line";

defineOptions({ name: "SystemSecurityConfig" });

const { t } = useI18n();

// ── SMS 配置（multi-config list + dialog）─────────────────────
const smsLoading = ref(false);
const smsConfigs = ref<SmsConfigResponse[]>([]);
const smsTestingId = ref<string | null>(null);

// 已存在的 SMS provider 集合（用于新建 dialog 禁用已配置的 provider 选项）
const existingSmsProviders = computed(
  () => new Set(smsConfigs.value.map(r => r.provider))
);

const smsDialogVisible = ref(false);
const isSmsEdit = ref(false);
const editSmsConfigId = ref<string | null>(null);
const smsSaving = ref(false);

const smsForm = reactive<SmsConfigCreate>({
  provider: "aliyun",
  sign_name: "",
  template_code: "",
  access_key_id: "",
  access_key_secret: "",
  sdk_app_id: "",
  region: "",
  daily_limit: 1000,
  interval_seconds: 60
});

// 切 provider 时清空非该 provider 需要的字段，避免误保留
watch(
  () => smsForm.provider,
  (newProvider, oldProvider) => {
    if (oldProvider === newProvider) return;
    if (newProvider !== "tencent") smsForm.sdk_app_id = "";
    if (newProvider === "tencent") smsForm.region = "";
  }
);

// 新建/编辑时的 placeholder（按是否已配置切换）
const smsAkPlaceholder = computed(() => {
  if (isSmsEdit.value && editSmsConfigId.value) {
    const cfg = smsConfigs.value.find(c => c.id === editSmsConfigId.value);
    if (cfg?.access_key_id_configured) {
      return t("system.smsConfig.accessKeyIdPlaceholderConfigured");
    }
  }
  return t("system.smsConfig.accessKeyIdPlaceholderEmpty");
});

const smsSkPlaceholder = computed(() => {
  if (isSmsEdit.value && editSmsConfigId.value) {
    const cfg = smsConfigs.value.find(c => c.id === editSmsConfigId.value);
    if (cfg?.access_key_secret_configured) {
      return t("system.smsConfig.accessKeySecretPlaceholderConfigured");
    }
  }
  return t("system.smsConfig.accessKeySecretPlaceholderEmpty");
});

const smsDialogTitle = computed(() =>
  isSmsEdit.value
    ? t("system.smsConfig.editDialog")
    : t("system.smsConfig.createDialog")
);

function smsProviderLabel(p: SmsProvider): string {
  if (p === "aliyun") return t("system.smsConfig.providerAliyun");
  if (p === "tencent") return t("system.smsConfig.providerTencent");
  if (p === "huawei") return t("system.smsConfig.providerHuawei");
  return p;
}

function resetSmsForm() {
  smsForm.provider = "aliyun";
  smsForm.sign_name = "";
  smsForm.template_code = "";
  smsForm.access_key_id = "";
  smsForm.access_key_secret = "";
  smsForm.sdk_app_id = "";
  smsForm.region = "";
  smsForm.daily_limit = 1000;
  smsForm.interval_seconds = 60;
}

function openSmsCreateDialog() {
  resetSmsForm();
  isSmsEdit.value = false;
  editSmsConfigId.value = null;
  smsDialogVisible.value = true;
}

function openSmsEditDialog(row: SmsConfigResponse) {
  // 预填表单（AK/SK 留空表示不修改）
  smsForm.provider = row.provider;
  smsForm.sign_name = row.sign_name || "";
  smsForm.template_code = row.template_code || "";
  smsForm.access_key_id = "";
  smsForm.access_key_secret = "";
  smsForm.sdk_app_id = row.sdk_app_id || "";
  smsForm.region = row.region || "";
  smsForm.daily_limit = row.daily_limit;
  smsForm.interval_seconds = row.interval_seconds;
  isSmsEdit.value = true;
  editSmsConfigId.value = row.id;
  smsDialogVisible.value = true;
}

async function loadSmsConfigs() {
  smsLoading.value = true;
  try {
    smsConfigs.value = await listSmsConfigsApi();
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.saveFailed") + ": " + (e?.message || "")
    );
  } finally {
    smsLoading.value = false;
  }
}

async function saveSms() {
  // 基础字段校验
  if (!smsForm.sign_name) {
    ElMessage.error(
      t("system.smsConfig.signName") + " " + t("common.rule.required")
    );
    return;
  }
  if (!smsForm.template_code) {
    ElMessage.error(
      t("system.smsConfig.templateCode") +
        " " +
        t("common.rule.required")
    );
    return;
  }
  if (smsForm.provider === "tencent" && !smsForm.sdk_app_id) {
    ElMessage.error(
      t("system.smsConfig.sdkAppId") + " " + t("common.rule.required")
    );
    return;
  }
  if (smsForm.provider !== "tencent" && !smsForm.region) {
    ElMessage.error(
      t("system.smsConfig.region") + " " + t("common.rule.required")
    );
    return;
  }
  if (!isSmsEdit.value && !smsForm.access_key_id) {
    ElMessage.error(
      t("system.smsConfig.accessKeyId") +
        " " +
        t("common.rule.required")
    );
    return;
  }
  if (!isSmsEdit.value && !smsForm.access_key_secret) {
    ElMessage.error(
      t("system.smsConfig.accessKeySecret") +
        " " +
        t("common.rule.required")
    );
    return;
  }

  smsSaving.value = true;
  try {
    const payload: SmsConfigCreate = {
      provider: smsForm.provider,
      sign_name: smsForm.sign_name || null,
      template_code: smsForm.template_code || null,
      access_key_id: smsForm.access_key_id || null,
      access_key_secret: smsForm.access_key_secret || null,
      sdk_app_id:
        smsForm.provider === "tencent" ? smsForm.sdk_app_id || null : null,
      region: smsForm.provider !== "tencent" ? smsForm.region || null : null,
      daily_limit: smsForm.daily_limit,
      interval_seconds: smsForm.interval_seconds
    };

    if (isSmsEdit.value && editSmsConfigId.value) {
      await updateSmsConfigApi(editSmsConfigId.value, payload);
      ElMessage.success(t("system.smsConfig.saved"));
    } else {
      await createSmsConfigApi(payload);
      ElMessage.success(t("system.smsConfig.saved"));
    }
    smsDialogVisible.value = false;
    await loadSmsConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.saveFailed") + ": " + (e?.message || "")
    );
  } finally {
    smsSaving.value = false;
  }
}

async function activateSms(id: string) {
  try {
    await activateSmsConfigApi(id);
    ElMessage.success(t("system.smsConfig.activateSuccess"));
    await loadSmsConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

async function deactivateSms(id: string) {
  try {
    await deactivateSmsConfigApi(id);
    ElMessage.success(t("system.smsConfig.deactivateSuccess"));
    await loadSmsConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

async function testSmsConfig(row: SmsConfigResponse) {
  smsTestingId.value = row.id;
  try {
    const r = await testSmsConfigApi(row.id);
    if (r.ok) {
      ElMessage.success(t("system.smsConfig.testSuccess"));
    } else {
      ElMessage.error(
        t("system.smsConfig.testFailed", { error: r.error || "" })
      );
    }
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.testFailed", { error: e?.message || "" })
    );
  } finally {
    smsTestingId.value = null;
  }
}

async function removeSms(row: SmsConfigResponse) {
  try {
    await ElMessageBox.confirm(
      t("system.smsConfig.deleteConfirm"),
      t("common.tip"),
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await deleteSmsConfigApi(row.id);
    ElMessage.success(t("common.msg.deleteSuccess"));
    await loadSmsConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.smsConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

// ── 邮件配置（multi-config list + dialog）─────────────────────
const emailLoading = ref(false);
const emailConfigs = ref<EmailConfigResponse[]>([]);
const emailTestingId = ref<string | null>(null);

// 已存在的 Email provider 集合
const existingEmailProviders = computed(
  () => new Set(emailConfigs.value.map(r => r.provider))
);

const dialogVisible = ref(false);
const isEdit = ref(false);
const editConfigId = ref<string | null>(null);
const emailSaving = ref(false);

const emailForm = reactive<EmailConfigCreate>({
  provider: "smtp",
  smtp_host: "",
  smtp_port: 465,
  encryption: "ssl",
  username: "",
  password: "",
  access_key_id: "",
  access_key_secret: "",
  region: "",
  from_email: "",
  from_name: "",
  daily_limit: 200,
  interval_seconds: 60
});

// encryption 切换时自动填默认端口
watch(
  () => emailForm.encryption,
  (enc: EmailEncryption | null) => {
    if (enc === "ssl") emailForm.smtp_port = 465;
    else if (enc === "starttls") emailForm.smtp_port = 587;
    else if (enc === "none") emailForm.smtp_port = 25;
  }
);

// 新建/编辑时的 placeholder（按是否已配置切换）
const emailPasswordPlaceholder = computed(() => {
  if (isEdit.value && editConfigId.value) {
    const cfg = emailConfigs.value.find(c => c.id === editConfigId.value);
    if (cfg?.password_configured) {
      return t("system.emailConfig.passwordPlaceholderConfigured");
    }
  }
  return t("system.emailConfig.passwordPlaceholderEmpty");
});

const emailAkPlaceholder = computed(() => {
  if (isEdit.value && editConfigId.value) {
    const cfg = emailConfigs.value.find(c => c.id === editConfigId.value);
    if (cfg?.access_key_id_configured) {
      return t("system.emailConfig.accessKeyIdPlaceholderConfigured");
    }
  }
  return t("system.emailConfig.accessKeyIdPlaceholderEmpty");
});

const emailSkPlaceholder = computed(() => {
  if (isEdit.value && editConfigId.value) {
    const cfg = emailConfigs.value.find(c => c.id === editConfigId.value);
    if (cfg?.access_key_secret_configured) {
      return t("system.emailConfig.accessKeySecretPlaceholderConfigured");
    }
  }
  return t("system.emailConfig.accessKeySecretPlaceholderEmpty");
});

const emailRegionPlaceholder = computed(() =>
  t("system.emailConfig.regionPlaceholder")
);

const dialogTitle = computed(() =>
  isEdit.value
    ? t("system.emailConfig.editDialog")
    : t("system.emailConfig.createDialog")
);

function providerLabel(p: EmailProvider): string {
  if (p === "smtp") return t("system.emailConfig.providerSmtp");
  if (p === "aliyun") return t("system.emailConfig.providerAliyun");
  if (p === "tencent") return t("system.emailConfig.providerTencent");
  if (p === "huawei") return t("system.emailConfig.providerHuawei");
  return p;
}

function mainFieldOf(row: EmailConfigResponse): string {
  return row.provider === "smtp"
    ? row.smtp_host || ""
    : row.from_email || "";
}

function resetEmailForm() {
  emailForm.provider = "smtp";
  emailForm.smtp_host = "";
  emailForm.smtp_port = 465;
  emailForm.encryption = "ssl";
  emailForm.username = "";
  emailForm.password = "";
  emailForm.access_key_id = "";
  emailForm.access_key_secret = "";
  emailForm.region = "";
  emailForm.from_email = "";
  emailForm.from_name = "";
  emailForm.daily_limit = 200;
  emailForm.interval_seconds = 60;
}

function openCreateDialog() {
  resetEmailForm();
  isEdit.value = false;
  editConfigId.value = null;
  dialogVisible.value = true;
}

function openEditDialog(row: EmailConfigResponse) {
  // 预填表单（password/AK/SK 留空表示不修改）
  emailForm.provider = row.provider;
  emailForm.smtp_host = row.smtp_host || "";
  emailForm.smtp_port = row.smtp_port ?? 465;
  emailForm.encryption = row.encryption || "ssl";
  emailForm.username = row.username || "";
  emailForm.password = "";
  emailForm.access_key_id = "";
  emailForm.access_key_secret = "";
  emailForm.region = row.region || "";
  emailForm.from_email = row.from_email || "";
  emailForm.from_name = row.from_name || "";
  emailForm.daily_limit = row.daily_limit;
  emailForm.interval_seconds = row.interval_seconds;
  isEdit.value = true;
  editConfigId.value = row.id;
  dialogVisible.value = true;
}

async function loadEmailConfigs() {
  emailLoading.value = true;
  try {
    emailConfigs.value = await listEmailConfigsApi();
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.saveFailed") + ": " + (e?.message || "")
    );
  } finally {
    emailLoading.value = false;
  }
}

async function saveEmail() {
  // 基础字段校验
  if (emailForm.provider === "smtp") {
    if (!emailForm.smtp_host) {
      ElMessage.error(
        t("system.emailConfig.smtpHost") + " " + t("common.rule.required")
      );
      return;
    }
    if (!emailForm.username) {
      ElMessage.error(
        t("system.emailConfig.username") + " " + t("common.rule.required")
      );
      return;
    }
    if (!isEdit.value && !emailForm.password) {
      ElMessage.error(
        t("system.emailConfig.password") + " " + t("common.rule.required")
      );
      return;
    }
  } else {
    if (!emailForm.region) {
      ElMessage.error(
        t("system.emailConfig.region") + " " + t("common.rule.required")
      );
      return;
    }
    if (!emailForm.from_email) {
      ElMessage.error(
        t("system.emailConfig.fromEmail") + " " + t("common.rule.required")
      );
      return;
    }
    if (!isEdit.value && !emailForm.access_key_id) {
      ElMessage.error(
        t("system.emailConfig.accessKeyId") + " " + t("common.rule.required")
      );
      return;
    }
    if (!isEdit.value && !emailForm.access_key_secret) {
      ElMessage.error(
        t("system.emailConfig.accessKeySecret") +
          " " +
          t("common.rule.required")
      );
      return;
    }
  }

  emailSaving.value = true;
  try {
    const payload: EmailConfigCreate = {
      provider: emailForm.provider,
      smtp_host: emailForm.provider === "smtp" ? emailForm.smtp_host || null : null,
      smtp_port: emailForm.provider === "smtp" ? emailForm.smtp_port ?? null : null,
      encryption: emailForm.provider === "smtp" ? emailForm.encryption : null,
      username: emailForm.provider === "smtp" ? emailForm.username || null : null,
      password: emailForm.provider === "smtp" ? emailForm.password || null : null,
      access_key_id:
        emailForm.provider !== "smtp" ? emailForm.access_key_id || null : null,
      access_key_secret:
        emailForm.provider !== "smtp"
          ? emailForm.access_key_secret || null
          : null,
      region: emailForm.provider !== "smtp" ? emailForm.region || null : null,
      from_email:
        emailForm.provider !== "smtp" ? emailForm.from_email || null : null,
      from_name: emailForm.from_name || null,
      daily_limit: emailForm.daily_limit,
      interval_seconds: emailForm.interval_seconds
    };

    if (isEdit.value && editConfigId.value) {
      await updateEmailConfigApi(editConfigId.value, payload);
      ElMessage.success(t("system.emailConfig.saved"));
    } else {
      await createEmailConfigApi(payload);
      ElMessage.success(t("system.emailConfig.saved"));
    }
    dialogVisible.value = false;
    await loadEmailConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.saveFailed") + ": " + (e?.message || "")
    );
  } finally {
    emailSaving.value = false;
  }
}

async function activateEmail(id: string) {
  try {
    await activateEmailConfigApi(id);
    ElMessage.success(t("system.emailConfig.activateSuccess"));
    await loadEmailConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

async function deactivateEmail(id: string) {
  try {
    await deactivateEmailConfigApi(id);
    ElMessage.success(t("system.emailConfig.deactivateSuccess"));
    await loadEmailConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

async function testEmailConfig(row: EmailConfigResponse) {
  emailTestingId.value = row.id;
  try {
    const r = await testEmailConfigApi(row.id);
    if (r.ok) {
      ElMessage.success(t("system.emailConfig.testSuccess"));
    } else {
      ElMessage.error(
        t("system.emailConfig.testFailed", { error: r.error || "" })
      );
    }
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.testFailed", { error: e?.message || "" })
    );
  } finally {
    emailTestingId.value = null;
  }
}

async function removeEmail(row: EmailConfigResponse) {
  try {
    await ElMessageBox.confirm(
      t("system.emailConfig.deleteConfirm"),
      t("common.tip"),
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await deleteEmailConfigApi(row.id);
    ElMessage.success(t("common.msg.deleteSuccess"));
    await loadEmailConfigs();
  } catch (e: any) {
    ElMessage.error(
      t("system.emailConfig.saveFailed") + ": " + (e?.message || "")
    );
  }
}

onMounted(() => {
  loadSmsConfigs();
  loadEmailConfigs();
});
</script>

<template>
  <div class="main">
    <DocsLink to="system.html#security-config" />
    <div class="welcome" style="max-width: 1100px">
      <!-- 页面标题 -->
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
          <IconifyIconOffline
            :icon="ShieldKeyhole"
            v-bind="{ width: '22', height: '22', color: '#6366f1' } as any"
          />
          <h3 class="text-lg font-semibold m-0">
            {{ t("system.securityConfig.title") }}
          </h3>
        </div>
      </div>
      <p class="text-sm text-gray-500 mb-6 -mt-2">
        {{ t("system.securityConfig.subtitle") }}
      </p>

      <!-- 短信服务商配置卡（multi-config list + dialog） -->
      <el-card shadow="never" v-loading="smsLoading">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">{{
              t("system.smsConfig.section.provider")
            }}</span>
            <el-button type="primary" :icon="Plus" @click="openSmsCreateDialog">
              {{ t("system.smsConfig.create") }}
            </el-button>
          </div>
        </template>

        <el-table
          :data="smsConfigs"
          v-loading="smsLoading"
          :empty-text="t('system.smsConfig.emptyText')"
        >
          <el-table-column
            :label="t('system.smsConfig.provider')"
            width="240"
          >
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-tag>{{ smsProviderLabel(row.provider) }}</el-tag>
                <el-tag
                  v-if="row.is_active"
                  type="success"
                >
                  {{ t("system.smsConfig.active") }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('system.smsConfig.mainField')"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.sign_name }} / {{ row.template_code }}
            </template>
          </el-table-column>

          <el-table-column :label="t('system.smsConfig.region')" width="140">
            <template #default="{ row }">
              {{ row.region || "—" }}
            </template>
          </el-table-column>

          <el-table-column
            :label="t('common.action.operation')"
            width="360"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button size="small" @click="openSmsEditDialog(row)">
                {{ t("common.action.edit") }}
              </el-button>
              <el-button
                v-if="!row.is_active"
                size="small"
                @click="activateSms(row.id)"
              >
                {{ t("system.smsConfig.activate") }}
              </el-button>
              <el-button
                v-else
                size="small"
                type="warning"
                @click="deactivateSms(row.id)"
              >
                {{ t("system.smsConfig.deactivate") }}
              </el-button>
              <el-button
                size="small"
                :loading="smsTestingId === row.id"
                @click="testSmsConfig(row)"
              >
                {{ t("system.smsConfig.test") }}
              </el-button>
              <el-button
                size="small"
                type="danger"
                @click="removeSms(row)"
              >
                {{ t("common.action.delete") }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- SMS Create/Edit Dialog -->
      <el-dialog
        v-model="smsDialogVisible"
        :title="smsDialogTitle"
        width="600"
        :close-on-click-modal="false"
      >
        <el-form :model="smsForm" label-width="160px">
          <el-form-item :label="t('system.smsConfig.provider')">
            <el-radio-group v-model="smsForm.provider" :disabled="isSmsEdit">
              <el-radio
                value="aliyun"
                :disabled="!isSmsEdit && existingSmsProviders.has('aliyun')"
              >
                {{ t("system.smsConfig.providerAliyun") }}
              </el-radio>
              <el-radio
                value="tencent"
                :disabled="!isSmsEdit && existingSmsProviders.has('tencent')"
              >
                {{ t("system.smsConfig.providerTencent") }}
              </el-radio>
              <el-radio
                value="huawei"
                :disabled="!isSmsEdit && existingSmsProviders.has('huawei')"
              >
                {{ t("system.smsConfig.providerHuawei") }}
              </el-radio>
            </el-radio-group>
            <div
              v-if="!isSmsEdit && existingSmsProviders.has(smsForm.provider)"
              class="text-xs text-gray-500 mt-1"
            >
              {{ t("system.smsConfig.providerInUse") }}
            </div>
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.signName')">
            <el-input
              v-model="smsForm.sign_name"
              :placeholder="t('system.smsConfig.signNamePlaceholder')"
              clearable
              style="max-width: 360px"
            />
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.templateCode')">
            <el-input
              v-model="smsForm.template_code"
              :placeholder="t('system.smsConfig.templateCodePlaceholder')"
              clearable
              style="max-width: 360px"
            />
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.accessKeyId')">
            <div class="flex items-center gap-3 flex-wrap">
              <el-input
                v-model="smsForm.access_key_id"
                :placeholder="smsAkPlaceholder"
                clearable
                style="max-width: 360px"
              />
              <el-tag
                v-if="
                  isSmsEdit &&
                  smsConfigs.find(c => c.id === editSmsConfigId)
                    ?.access_key_id_configured
                "
                type="success"
                size="small"
              >
                {{ t("system.smsConfig.configured") }}
              </el-tag>
              <el-tag v-else-if="isSmsEdit" type="info" size="small">
                {{ t("system.smsConfig.notConfigured") }}
              </el-tag>
            </div>
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.accessKeySecret')">
            <div class="flex items-center gap-3 flex-wrap">
              <el-input
                v-model="smsForm.access_key_secret"
                type="password"
                show-password
                :placeholder="smsSkPlaceholder"
                clearable
                style="max-width: 360px"
              />
              <el-tag
                v-if="
                  isSmsEdit &&
                  smsConfigs.find(c => c.id === editSmsConfigId)
                    ?.access_key_secret_configured
                "
                type="success"
                size="small"
              >
                {{ t("system.smsConfig.configured") }}
              </el-tag>
              <el-tag v-else-if="isSmsEdit" type="info" size="small">
                {{ t("system.smsConfig.notConfigured") }}
              </el-tag>
            </div>
          </el-form-item>

          <el-form-item
            v-if="smsForm.provider === 'tencent'"
            :label="t('system.smsConfig.sdkAppId')"
          >
            <el-input
              v-model="smsForm.sdk_app_id"
              :placeholder="t('system.smsConfig.sdkAppIdPlaceholder')"
              clearable
              style="max-width: 360px"
            />
          </el-form-item>

          <el-form-item
            v-if="smsForm.provider !== 'tencent'"
            :label="t('system.smsConfig.region')"
          >
            <el-input
              v-model="smsForm.region"
              :placeholder="t('system.smsConfig.regionPlaceholder')"
              clearable
              style="max-width: 360px"
            />
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.dailyLimit')">
            <el-input-number
              v-model="smsForm.daily_limit"
              :min="1"
              :max="100000"
              :step="100"
            />
            <span class="ml-2 text-xs text-gray-400">{{
              t("system.smsConfig.dailyLimitHint")
            }}</span>
          </el-form-item>

          <el-form-item :label="t('system.smsConfig.intervalSeconds')">
            <el-input-number
              v-model="smsForm.interval_seconds"
              :min="30"
              :max="3600"
              :step="10"
            />
            <span class="ml-2 text-xs text-gray-400">{{
              t("system.smsConfig.intervalSecondsHint")
            }}</span>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="smsDialogVisible = false">
            {{ t("common.action.cancel") }}
          </el-button>
          <el-button type="primary" :loading="smsSaving" @click="saveSms">
            {{ t("system.smsConfig.save") }}
          </el-button>
        </template>
      </el-dialog>

      <!-- 邮件服务商配置卡（multi-config list + dialog） -->
      <el-card shadow="never" class="mt-4" v-loading="emailLoading">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">{{
              t("system.emailConfig.section.provider")
            }}</span>
            <el-button type="primary" :icon="Plus" @click="openCreateDialog">
              {{ t("system.emailConfig.create") }}
            </el-button>
          </div>
        </template>

        <el-table
          :data="emailConfigs"
          v-loading="emailLoading"
          :empty-text="t('system.emailConfig.emptyText')"
        >
          <el-table-column
            :label="t('system.emailConfig.provider')"
            width="240"
          >
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-tag>{{ providerLabel(row.provider) }}</el-tag>
                <el-tag
                  v-if="row.is_active"
                  type="success"
                >
                  {{ t("system.emailConfig.active") }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('system.emailConfig.mainField')"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ mainFieldOf(row) }}
            </template>
          </el-table-column>

          <el-table-column :label="t('system.emailConfig.region')" width="140">
            <template #default="{ row }">
              {{ row.region || "—" }}
            </template>
          </el-table-column>

          <el-table-column
            :label="t('common.action.operation')"
            width="360"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button size="small" @click="openEditDialog(row)">
                {{ t("common.action.edit") }}
              </el-button>
              <el-button
                v-if="!row.is_active"
                size="small"
                @click="activateEmail(row.id)"
              >
                {{ t("system.emailConfig.activate") }}
              </el-button>
              <el-button
                v-else
                size="small"
                type="warning"
                @click="deactivateEmail(row.id)"
              >
                {{ t("system.emailConfig.deactivate") }}
              </el-button>
              <el-button
                size="small"
                :loading="emailTestingId === row.id"
                @click="testEmailConfig(row)"
              >
                {{ t("system.emailConfig.test") }}
              </el-button>
              <el-button
                size="small"
                type="danger"
                @click="removeEmail(row)"
              >
                {{ t("common.action.delete") }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- Create/Edit Dialog -->
      <el-dialog
        v-model="dialogVisible"
        :title="dialogTitle"
        width="600"
        :close-on-click-modal="false"
      >
        <el-form :model="emailForm" label-width="140px">
          <el-form-item :label="t('system.emailConfig.provider')">
            <el-radio-group v-model="emailForm.provider" :disabled="isEdit">
              <el-radio
                value="smtp"
                :disabled="!isEdit && existingEmailProviders.has('smtp')"
              >
                {{ t("system.emailConfig.providerSmtp") }}
              </el-radio>
              <el-radio
                value="aliyun"
                :disabled="!isEdit && existingEmailProviders.has('aliyun')"
              >
                {{ t("system.emailConfig.providerAliyun") }}
              </el-radio>
              <el-radio
                value="tencent"
                :disabled="!isEdit && existingEmailProviders.has('tencent')"
              >
                {{ t("system.emailConfig.providerTencent") }}
              </el-radio>
              <el-radio
                value="huawei"
                :disabled="!isEdit && existingEmailProviders.has('huawei')"
              >
                {{ t("system.emailConfig.providerHuawei") }}
              </el-radio>
            </el-radio-group>
            <div
              v-if="!isEdit && existingEmailProviders.has(emailForm.provider)"
              class="text-xs text-gray-500 mt-1"
            >
              {{ t("system.emailConfig.providerInUse") }}
            </div>
          </el-form-item>

          <!-- SMTP 字段 -->
          <template v-if="emailForm.provider === 'smtp'">
            <el-form-item :label="t('system.emailConfig.smtpHost')">
              <el-input
                v-model="emailForm.smtp_host"
                :placeholder="t('system.emailConfig.smtpHostPlaceholder')"
                clearable
                style="max-width: 360px"
              />
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.encryption')">
              <el-radio-group v-model="emailForm.encryption">
                <el-radio value="ssl">SSL/TLS（465）</el-radio>
                <el-radio value="starttls">STARTTLS（587）</el-radio>
                <el-radio value="none">None（25，不推荐）</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.smtpPort')">
              <el-input-number
                v-model="emailForm.smtp_port"
                :min="1"
                :max="65535"
              />
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.username')">
              <el-input
                v-model="emailForm.username"
                :placeholder="t('system.emailConfig.usernamePlaceholder')"
                clearable
                style="max-width: 360px"
              />
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.password')">
              <div class="flex items-center gap-3 flex-wrap">
                <el-input
                  v-model="emailForm.password"
                  type="password"
                  show-password
                  :placeholder="emailPasswordPlaceholder"
                  clearable
                  style="max-width: 360px"
                />
                <el-tag
                  v-if="
                    isEdit &&
                    emailConfigs.find(c => c.id === editConfigId)
                      ?.password_configured
                  "
                  type="success"
                  size="small"
                >
                  {{ t("system.emailConfig.configured") }}
                </el-tag>
                <el-tag v-else-if="isEdit" type="info" size="small">
                  {{ t("system.emailConfig.notConfigured") }}
                </el-tag>
              </div>
            </el-form-item>
          </template>

          <!-- 云厂商字段 -->
          <template v-else>
            <el-form-item :label="t('system.emailConfig.accessKeyId')">
              <div class="flex items-center gap-3 flex-wrap">
                <el-input
                  v-model="emailForm.access_key_id"
                  :placeholder="emailAkPlaceholder"
                  clearable
                  style="max-width: 360px"
                />
                <el-tag
                  v-if="
                    isEdit &&
                    emailConfigs.find(c => c.id === editConfigId)
                      ?.access_key_id_configured
                  "
                  type="success"
                  size="small"
                >
                  {{ t("system.emailConfig.configured") }}
                </el-tag>
                <el-tag v-else-if="isEdit" type="info" size="small">
                  {{ t("system.emailConfig.notConfigured") }}
                </el-tag>
              </div>
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.accessKeySecret')">
              <div class="flex items-center gap-3 flex-wrap">
                <el-input
                  v-model="emailForm.access_key_secret"
                  type="password"
                  show-password
                  :placeholder="emailSkPlaceholder"
                  clearable
                  style="max-width: 360px"
                />
                <el-tag
                  v-if="
                    isEdit &&
                    emailConfigs.find(c => c.id === editConfigId)
                      ?.access_key_secret_configured
                  "
                  type="success"
                  size="small"
                >
                  {{ t("system.emailConfig.configured") }}
                </el-tag>
                <el-tag v-else-if="isEdit" type="info" size="small">
                  {{ t("system.emailConfig.notConfigured") }}
                </el-tag>
              </div>
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.region')">
              <el-input
                v-model="emailForm.region"
                :placeholder="emailRegionPlaceholder"
                clearable
                style="max-width: 360px"
              />
            </el-form-item>
            <el-form-item :label="t('system.emailConfig.fromEmail')">
              <el-input
                v-model="emailForm.from_email"
                :placeholder="t('system.emailConfig.fromEmailPlaceholder')"
                clearable
                style="max-width: 360px"
              />
            </el-form-item>
          </template>

          <!-- 共享字段 -->
          <el-form-item :label="t('system.emailConfig.fromName')">
            <el-input
              v-model="emailForm.from_name"
              :placeholder="t('system.emailConfig.fromNamePlaceholder')"
              clearable
              style="max-width: 360px"
            />
          </el-form-item>
          <el-form-item :label="t('system.emailConfig.dailyLimit')">
            <el-input-number
              v-model="emailForm.daily_limit"
              :min="1"
              :max="100000"
              :step="50"
            />
            <span class="ml-2 text-xs text-gray-400">{{
              t("system.emailConfig.dailyLimitHint")
            }}</span>
          </el-form-item>
          <el-form-item :label="t('system.emailConfig.intervalSeconds')">
            <el-input-number
              v-model="emailForm.interval_seconds"
              :min="30"
              :max="3600"
              :step="10"
            />
            <span class="ml-2 text-xs text-gray-400">{{
              t("system.emailConfig.intervalSecondsHint")
            }}</span>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">
            {{ t("common.action.cancel") }}
          </el-button>
          <el-button type="primary" :loading="emailSaving" @click="saveEmail">
            {{ t("system.emailConfig.save") }}
          </el-button>
        </template>
      </el-dialog>
    </div>
  </div>
</template>
