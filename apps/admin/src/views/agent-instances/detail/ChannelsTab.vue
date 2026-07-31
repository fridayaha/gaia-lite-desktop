<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getChannelsApi,
  createChannelApi,
  updateChannelApi,
  deleteChannelApi
} from "@/api/manager/agentInstances";
import type {
  ChannelResponse,
  ChannelCreatePayload
} from "@/api/manager/agentInstances";
import { copyTextToClipboard } from "@pureadmin/utils";
import dayjs from "dayjs";


// Icons
import WechatFill from "~icons/ri/wechat-fill";
import FlyFill from "~icons/ri/flight-takeoff-fill";
import Message2Fill from "~icons/ri/message-2-fill";
import GlobalLine from "~icons/ri/global-line";

const props = defineProps<{ instanceId: string }>();
const { t } = useI18n();

const loading = ref(false);
const channelList = ref<ChannelResponse[]>([]);
const dialogVisible = ref(false);
const editingId = ref<string | null>(null);

// HTTP 渠道独立为启用开关，不占表格行；表格只展示 IM 渠道
const httpChannel = computed(
  () => channelList.value.find(c => c.channel_type === "http") || null
);
const imChannels = computed(() =>
  channelList.value.filter(c => c.channel_type !== "http")
);

// ── 渠道类型配置 ─────────────────────────────────────

const channelTypes = computed<
  Record<
    string,
    {
      label: string;
      sub?: string;
      desc: string;
      icon: any;
      color: string;
      fields: { key: string; label: string; placeholder: string }[];
      modes?: { key: string; label: string; disabled?: boolean; soon?: boolean }[];
    }
  >
>(() => ({
  wecom: {
    label: t("agent.channel.type.wecom"),
    sub: t("agent.channel.typeSub.wecom"),
    desc: t("agent.channel.typeDesc.wecom"),
    icon: WechatFill,
    color: "#07c160",
    fields: [
      { key: "corp_id", label: t("agent.channel.field.corp_id"), placeholder: "ww-xxxxxxxx" },
      {
        key: "secret",
        label: t("agent.channel.field.secret"),
        placeholder: t("agent.channel.placeholder.wecomSecret")
      },
      {
        key: "token",
        label: t("agent.channel.field.token"),
        placeholder: t("agent.channel.placeholder.callbackToken")
      },
      {
        key: "encoding_aes_key",
        label: t("agent.channel.field.encoding_aes_key"),
        placeholder: t("agent.channel.placeholder.aesKey")
      },
      { key: "agent_id", label: t("agent.channel.field.agent_id"), placeholder: "1000001" }
    ]
  },
  wecom_bot_callback: {
    label: t("agent.channel.type.wecom_bot_callback"),
    sub: t("agent.channel.typeSub.wecom_bot_callback"),
    desc: t("agent.channel.typeDesc.wecom_bot_callback"),
    icon: WechatFill,
    color: "#6366f1",
    fields: [
      {
        key: "token",
        label: t("agent.channel.field.token"),
        placeholder: t("agent.channel.placeholder.callbackToken")
      },
      {
        key: "encoding_aes_key",
        label: t("agent.channel.field.encoding_aes_key"),
        placeholder: t("agent.channel.placeholder.aesKey")
      }
    ],
    modes: [
      { key: "callback", label: t("agent.channel.mode.callback") },
      { key: "ws", label: t("agent.channel.mode.ws"), disabled: true, soon: true }
    ]
  },
  feishu: {
    label: t("agent.channel.type.feishu"),
    desc: t("agent.channel.typeDesc.feishu"),
    icon: FlyFill,
    color: "#3370ff",
    fields: [
      { key: "app_id", label: "App ID", placeholder: "cli_xxxxxxxx" },
      {
        key: "app_secret",
        label: t("agent.channel.field.app_secret"),
        placeholder: t("agent.channel.placeholder.feishuAppSecret")
      },
      {
        key: "verification_token",
        label: t("agent.channel.field.verification_token"),
        placeholder: t("agent.channel.placeholder.eventToken")
      },
      {
        key: "encrypt_key",
        label: t("agent.channel.field.encrypt_key"),
        placeholder: t("agent.channel.placeholder.encryptKey")
      }
    ]
  },
  dingtalk: {
    label: t("agent.channel.type.dingtalk"),
    desc: t("agent.channel.typeDesc.dingtalk"),
    icon: Message2Fill,
    color: "#0089ff",
    fields: [
      {
        key: "client_id",
        label: t("agent.channel.field.client_id"),
        placeholder: t("agent.channel.placeholder.dingtalkClientId")
      },
      {
        key: "client_secret",
        label: t("agent.channel.field.client_secret"),
        placeholder: t("agent.channel.placeholder.dingtalkClientSecret")
      }
    ]
  }
}));

// ── 表单数据 ─────────────────────────────────────────

const formData = ref<{
  channel_type: string;
  config: Record<string, string>;
  connection_mode: string;
}>({
  channel_type: "wecom",
  config: {},
  connection_mode: "callback"
});

function resetForm() {
  formData.value = { channel_type: "wecom", config: {}, connection_mode: "callback" };
  editingId.value = null;
}

// ── CRUD ─────────────────────────────────────────────

async function fetchChannels() {
  loading.value = true;
  try {
    const res = await getChannelsApi(props.instanceId);
    channelList.value = res.items;
  } catch {
    ElMessage.error(t("agent.channel.msg.loadFailed"));
  } finally {
    loading.value = false;
  }
}

function openCreateDialog() {
  resetForm();
  dialogVisible.value = true;
}

function openEditDialog(channel: ChannelResponse) {
  formData.value = {
    channel_type: channel.channel_type,
    config: { ...(channel.config || {}) },
    connection_mode: "callback"
  };
  editingId.value = channel.id;
  dialogVisible.value = true;
}

async function handleSave() {
  const payload: any = {
    config: formData.value.config
  };
  if (editingId.value) {
    await updateChannelApi(props.instanceId, editingId.value, payload);
  } else {
    payload.channel_type = formData.value.channel_type;
    await createChannelApi(props.instanceId, payload);
  }
  dialogVisible.value = false;
  ElMessage.success(t("common.msg.saveSuccess"));
  await fetchChannels();
}

async function handleToggleEnabled(channel: ChannelResponse) {
  try {
    await updateChannelApi(props.instanceId, channel.id, {
      enabled: !channel.enabled
    });
    channel.enabled = !channel.enabled;
    ElMessage.success(
      channel.enabled ? t("common.status.enabled") : t("common.status.disabled")
    );
  } catch {
    ElMessage.error(t("common.msg.operationFailed"));
  }
}

async function handleDelete(channel: ChannelResponse) {
  try {
    await ElMessageBox.confirm(
      t("agent.channel.msg.confirmDelete", {
        type: typeDisplayLabel(channel.channel_type)
      }),
      t("common.action.confirmDelete"),
      {
        confirmButtonText: t("common.action.delete"),
        cancelButtonText: t("common.action.cancel"),
        type: "warning"
      }
    );
    await deleteChannelApi(props.instanceId, channel.id);
    ElMessage.success(t("common.msg.deleteSuccess"));
    await fetchChannels();
  } catch {
    // 取消删除不做操作
  }
}

// ── 复制 Callback URL ───────────────────────────────

function copyUrl(url: string) {
  if (copyTextToClipboard(url)) {
    ElMessage.success(t("agent.channel.msg.copied"));
  } else {
    ElMessage.error(t("common.msg.operationFailed"));
  }
}

// ── 类型变更时重置配置表单 ──────────────────────────

function onTypeChange() {
  formData.value.config = {};
  formData.value.connection_mode = "callback";
}

/** 敏感字段（secret/key/token）以密码框显示，其余明文 */
function isSensitive(key: string): boolean {
  const k = key.toLowerCase();
  return k.includes("secret") || k.includes("key") || k.includes("token");
}

// 与后端 schemas.SENSITIVE_MASK 一致：编辑态回显的固定星号掩码
const SENSITIVE_MASK = "********";

/** 编辑态敏感字段回显的是固定掩码，聚焦时清空，避免在掩码后追加新输入；
 *  留空保存 → 后端保留原值。 */
function onSensitiveFieldFocus(key: string) {
  if (isSensitive(key) && formData.value.config[key] === SENSITIVE_MASK) {
    formData.value.config[key] = "";
  }
}

/** 渠道类型展示名（主标签 + 括号限定词），用于表格/删除确认等需唯一识别的场景 */
function typeDisplayLabel(type: string): string {
  const cfg = channelTypes.value[type];
  if (!cfg) return type;
  return cfg.sub ? `${cfg.label}（${cfg.sub}）` : cfg.label;
}

onMounted(async () => {
  await fetchChannels();
});
</script>

<template>
  <div v-loading="loading" class="channels-tab">
    <!-- 操作栏 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-button type="primary" @click="openCreateDialog">
          {{ t("agent.channel.create") }}
        </el-button>
      </div>
    </div>

    <!-- HTTP（Web 聊天）渠道：独立启用开关，发布后自动创建 -->
    <el-card shadow="never" class="http-channel-card mb-4">
      <div class="http-channel-row">
        <div class="http-channel-info">
          <div class="http-channel-icon">
            <GlobalLine width="22" height="22" />
          </div>
          <div>
            <div class="http-channel-title">{{ t("agent.channel.http.title") }}</div>
            <div class="http-channel-desc">{{ t("agent.channel.http.desc") }}</div>
          </div>
        </div>
        <el-tooltip
          v-if="!httpChannel"
          :content="t('agent.channel.http.autoCreate')"
          placement="top"
        >
          <el-switch :model-value="false" disabled />
        </el-tooltip>
        <el-switch
          v-else
          :model-value="httpChannel.enabled"
          @change="handleToggleEnabled(httpChannel)"
        />
      </div>
    </el-card>

    <!-- IM 渠道列表 -->
    <el-table :data="imChannels" :empty-text="t('agent.channel.empty')" stripe>
      <el-table-column :label="t('agent.channel.col.channel')" width="140">
        <template #default="{ row }">
          <div class="channel-type-cell">
            <component
              :is="channelTypes[row.channel_type]?.icon"
              v-if="channelTypes[row.channel_type]"
              width="18"
              height="18"
            />
            <span>{{ typeDisplayLabel(row.channel_type) }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column :label="t('agent.channel.col.status')" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="handleToggleEnabled(row)" />
        </template>
      </el-table-column>
      <el-table-column label="Profile" width="100">
        <template #default>
          <el-tag size="small" type="success">
            {{ t("common.profile.independent") }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('agent.channel.col.callback')" min-width="280">
        <template #default="{ row }">
          <div v-if="row.callback_url" class="callback-url-cell">
            <el-tooltip
              :content="row.callback_url"
              placement="top"
              :show-after="300"
              :visible-aria-attribute="undefined"
            >
              <span class="callback-url-text">{{ row.callback_url }}</span>
            </el-tooltip>
            <el-button link type="primary" size="small" @click="copyUrl(row.callback_url!)">
              {{ t("common.action.copy") }}
            </el-button>
          </div>
          <span v-else class="text-gray-400">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('agent.channel.col.createdAt')" width="160">
        <template #default="{ row }">
          {{ dayjs(row.created_at).format("YYYY-MM-DD HH:mm") }}
        </template>
      </el-table-column>
      <el-table-column :label="t('agent.channel.col.operation')" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openEditDialog(row)">
            {{ t("common.action.edit") }}
          </el-button>
          <el-button link size="small" type="danger" @click="handleDelete(row)">
            {{ t("common.action.delete") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑 弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? t('agent.channel.dialogTitle.edit') : t('agent.channel.dialogTitle.create')"
      width="560px"
      :close-on-click-modal="false"
      @close="resetForm"
    >
      <el-form label-position="top">
        <el-form-item :label="t('agent.channel.formType')">
          <div class="channel-type-grid">
            <div
              v-for="(cfg, key) in channelTypes"
              :key="key"
              class="channel-type-card"
              :class="{
                'is-selected': formData.channel_type === key,
                'is-disabled': !!editingId
              }"
              @click="!editingId && ((formData.channel_type = key), onTypeChange())"
            >
              <div
                class="channel-type-icon"
                :style="{ background: cfg.color + '18', color: cfg.color }"
              >
                <component :is="cfg.icon" width="22" height="22" />
              </div>
              <div class="channel-type-text">
                <span class="channel-type-label">
                  {{ cfg.label }}<span v-if="cfg.sub" class="channel-type-sub">（{{ cfg.sub }}）</span>
                </span>
                <span class="channel-type-desc">{{ cfg.desc }}</span>
              </div>
            </div>
          </div>
        </el-form-item>

        <el-form-item
          v-if="channelTypes[formData.channel_type]?.modes?.length"
          :label="t('agent.channel.connMode')"
        >
          <el-radio-group v-model="formData.connection_mode">
            <el-radio
              v-for="m in channelTypes[formData.channel_type].modes"
              :key="m.key"
              :value="m.key"
              :disabled="m.disabled"
            >
              {{ m.label }}
              <el-tag
                v-if="m.soon"
                size="small"
                type="info"
                effect="plain"
                class="ml-1"
              >
                {{ t("agent.channel.modeSoon") }}
              </el-tag>
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <template v-if="channelTypes[formData.channel_type]">
          <el-form-item
            v-for="field in channelTypes[formData.channel_type].fields"
            :key="field.key"
            :label="field.label"
          >
            <el-input
              v-model="formData.config[field.key]"
              :placeholder="field.placeholder"
              :type="isSensitive(field.key) ? 'password' : 'text'"
              :show-password="isSensitive(field.key)"
              clearable
              @focus="onSensitiveFieldFocus(field.key)"
            />
          </el-form-item>
        </template>

        <p v-if="editingId" class="text-gray-400 text-sm">
          {{ t("agent.channel.tip.credentials") }}
        </p>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">
          {{ t("common.action.cancel") }}
        </el-button>
        <el-button type="primary" @click="handleSave">
          {{ t("common.action.save") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.channels-tab {
  min-height: 200px;
}

.channel-type-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  width: 100%;
}

.channel-type-card {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.channel-type-card:hover:not(.is-disabled) {
  border-color: var(--el-color-primary);
}

.channel-type-card.is-selected {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.channel-type-card.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.channel-type-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  flex-shrink: 0;
}

.channel-type-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.channel-type-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.channel-type-sub {
  font-size: 10px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  margin-left: 1px;
}

.channel-type-desc {
  font-size: 10px;
  color: var(--el-text-color-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.3;
}

.channel-type-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.http-channel-card {
  border-radius: 8px;
}

.http-channel-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.http-channel-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.http-channel-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  background: #41b6ff18;
  color: #41b6ff;
  flex-shrink: 0;
}

.http-channel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.http-channel-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.callback-url-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.callback-url-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-gray-400 {
  color: #9ca3af;
}

.text-sm {
  font-size: 12px;
}
</style>
