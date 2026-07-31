<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { zxcvbn, getPasswordStrengthHint } from "@/utils/zxcvbn";
import { isAllEmpty } from "@pureadmin/utils";
import ReCol from "@/components/ReCol";
import { formRules } from "../utils/rule";
import { FormProps } from "../utils/types";
import { usePublicHooks } from "../../hooks";
import {
  getUserImBindingsApi,
  createUserImBindingApi,
  deleteUserImBindingApi,
  getUserBusinessBindingApi,
  upsertUserBusinessBindingApi,
  deleteUserBusinessBindingApi,
  type ImBindingResponse,
  type BusinessBindingResponse,
} from "@/api/manager/users";
import { message } from "@/utils/message";

const props = withDefaults(defineProps<FormProps>(), {
  formInline: () => ({
    title: "create",
    username: "",
    real_name: "",
    password: "",
    email: "",
    phone: "",
    is_active: true
  }),
  userId: ""
});

const { t } = useI18n();
const ruleFormRef = ref();
const { switchStyle } = usePublicHooks();
const newFormInline = ref(props.formInline);

const isEdit = computed(() => newFormInline.value.title === "edit");

// 密码强度计 — 与后端 _validate_password_strength 对齐：score ≥ 3 才通过
const pwdScore = ref(-1);
const pwdProgress = computed(() => [
  { color: "#e74242", text: t("system.user.pwd.strength.veryWeak") },
  { color: "#EFBD47", text: t("system.user.pwd.strength.weak") },
  { color: "#ffa500", text: t("system.user.pwd.strength.fair") },
  { color: "#1bbf1b", text: t("system.user.pwd.strength.strong") },
  { color: "#008000", text: t("system.user.pwd.strength.veryStrong") }
]);
// score < 3 时的实时中文建议 — 优先用 zxcvbn 给的具体 warning（已翻译成中文）。
// 一直显示：input 时实时提示，blur 后 form-item validator 走相同文案但 form-item 关闭了
// 自带 error 文案（:show-message="false"），所以不会双份重复；同时布局稳定不抖动。
const pwdHint = computed(() => getPasswordStrengthHint(newFormInline.value.password));

watch(
  () => newFormInline.value.password,
  (newPwd) => {
    pwdScore.value = isAllEmpty(newPwd) ? -1 : zxcvbn(newPwd).score;
  }
);

const channelTypeLabel = computed<Record<string, string>>(() => ({
  wecom: `${t("agent.channel.type.wecom")}（${t("agent.channel.typeSub.wecom")}）`,
  wecom_bot_callback: `${t("agent.channel.type.wecom_bot_callback")}（${t("agent.channel.typeSub.wecom_bot_callback")}）`,
  feishu: t("agent.channel.type.feishu"),
  dingtalk: t("agent.channel.type.dingtalk")
}));

// IM 平台 tag 样式：企微两通道同属绿色系——自建应用实心 success，智能机器人描边 plain；
// 飞书 primary、钉钉 warning。同族用 effect 区分，避免引入第五种 type 色。
const channelTagStyle = computed<Record<string, { type: string; effect?: string }>>(() => ({
  wecom: { type: "success" },
  wecom_bot_callback: { type: "success", effect: "plain" },
  feishu: { type: "primary" },
  dingtalk: { type: "warning" }
}));

// IM binding state
const imBindings = ref<ImBindingResponse[]>([]);
const showAddBindingDialog = ref(false);
const bindingLoading = ref(false);
const newBinding = ref({ channel_type: "wecom", im_user_id: "", im_user_name: "" });

// 优先使用独立 userId prop，其次 formInline.id
const resolvedUserId = () => props.userId || props.formInline.id;

async function loadBindings() {
  const uid = resolvedUserId();
  if (!uid || !isEdit.value) {
    imBindings.value = [];
    return;
  }
  try {
    const res = await getUserImBindingsApi(uid);
    imBindings.value = res.items;
  } catch (e) {
    console.warn("[ImBindings] load failed:", e);
  }
}

onMounted(() => {
  loadBindings();
  loadBusinessBinding();
});

async function handleAddBinding() {
  if (!newBinding.value.im_user_id) {
    message(t("system.user.form.msg.imUserIdRequired"), { type: "warning" });
    return;
  }
  const uid = resolvedUserId();
  if (!uid) {
    message(t("system.user.form.msg.uidUnavailable"), { type: "error" });
    console.error("handleAddBinding: missing userId", props.userId, props.formInline);
    return;
  }
  bindingLoading.value = true;
  try {
    const res = await createUserImBindingApi(uid, {
      channel_type: newBinding.value.channel_type,
      im_user_id: newBinding.value.im_user_id,
      im_user_name: newBinding.value.im_user_name || undefined,
    });
    imBindings.value.push(res);
    showAddBindingDialog.value = false;
    newBinding.value = { channel_type: "wecom", im_user_id: "", im_user_name: "" };
    message(t("system.user.form.msg.bindOk"), { type: "success" });
  } catch (e: any) {
    message(e?.response?.data?.detail || t("system.user.form.msg.bindFailed"), { type: "error" });
  } finally {
    bindingLoading.value = false;
  }
}

async function handleRemoveBinding(row: ImBindingResponse) {
  const uid = resolvedUserId();
  if (!uid) return;
  try {
    await deleteUserImBindingApi(uid, row.id);
    imBindings.value = imBindings.value.filter(b => b.id !== row.id);
    message(t("system.user.form.msg.unbindOk"), { type: "success" });
  } catch (e: any) {
    message(t("system.user.form.msg.unbindFailed"), { type: "error" });
  }
}

// Business binding state（1:1，业务系统用户身份）
const businessBinding = ref<BusinessBindingResponse | null>(null);
const businessForm = ref({ business_username: "", business_phone: "", business_email: "" });
const businessLoading = ref(false);

async function loadBusinessBinding() {
  const uid = resolvedUserId();
  if (!uid || !isEdit.value) {
    businessBinding.value = null;
    businessForm.value = { business_username: "", business_phone: "", business_email: "" };
    return;
  }
  try {
    const res = await getUserBusinessBindingApi(uid);
    businessBinding.value = res;
    if (res) {
      businessForm.value = {
        business_username: res.business_username || "",
        business_phone: res.business_phone || "",
        business_email: res.business_email || "",
      };
    } else {
      businessForm.value = { business_username: "", business_phone: "", business_email: "" };
    }
  } catch (e) {
    console.warn("[BusinessBinding] load failed:", e);
  }
}

async function handleSaveBusinessBinding(silent = false) {
  if (!businessForm.value.business_username) {
    if (!silent) message(t("system.user.form.msg.businessUsernameRequired"), { type: "warning" });
    return false;
  }
  const uid = resolvedUserId();
  if (!uid) return false;
  businessLoading.value = true;
  try {
    const res = await upsertUserBusinessBindingApi(uid, {
      business_username: businessForm.value.business_username,
      business_phone: businessForm.value.business_phone || undefined,
      business_email: businessForm.value.business_email || undefined,
    });
    businessBinding.value = res;
    if (!silent) message(t("system.user.form.msg.businessSaveOk"), { type: "success" });
    return true;
  } catch (e: any) {
    if (!silent) message(e?.response?.data?.detail || t("system.user.form.msg.businessSaveFailed"), { type: "error" });
    return false;
  } finally {
    businessLoading.value = false;
  }
}

async function handleRemoveBusinessBinding() {
  const uid = resolvedUserId();
  if (!uid) return;
  businessLoading.value = true;
  try {
    await deleteUserBusinessBindingApi(uid);
    businessBinding.value = null;
    businessForm.value = { business_username: "", business_phone: "", business_email: "" };
    message(t("system.user.form.msg.businessRemoveOk"), { type: "success" });
  } catch (e: any) {
    message(t("system.user.form.msg.businessRemoveFailed"), { type: "error" });
  } finally {
    businessLoading.value = false;
  }
}

function getRef() {
  return ruleFormRef.value;
}

defineExpose({ getRef, saveBusinessBinding: handleSaveBusinessBinding });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="formRules"
    label-width="82px"
  >
    <el-row :gutter="30">
      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item :label="t('system.user.form.username')" prop="username">
          <el-input
            v-model="newFormInline.username"
            clearable
            :placeholder="t('system.user.form.usernamePlaceholder')"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item :label="t('system.user.form.realName')" prop="real_name">
          <el-input
            v-model="newFormInline.real_name"
            clearable
            :placeholder="t('system.user.form.realNamePlaceholder')"
          />
        </el-form-item>
      </re-col>

      <re-col
        v-if="!isEdit"
        :value="12"
        :xs="24"
        :sm="24"
      >
        <el-form-item
          :label="t('system.user.form.password')"
          prop="password"
          :show-message="false"
        >
          <el-input
            v-model="newFormInline.password"
            clearable
            :placeholder="t('system.user.form.passwordPlaceholder')"
            type="password"
            show-password
          />
          <div v-if="pwdScore >= 0" class="w-full mt-2">
            <div class="flex">
              <div
                v-for="(item, idx) in pwdProgress"
                :key="idx"
                class="flex-1"
                :style="{ marginLeft: idx !== 0 ? '4px' : 0 }"
              >
                <el-progress
                  striped
                  striped-flow
                  :duration="pwdScore === idx ? 6 : 0"
                  :percentage="pwdScore >= idx ? 100 : 0"
                  :color="item.color"
                  :stroke-width="8"
                  :show-text="false"
                />
                <p
                  class="text-center text-xs"
                  :style="{ color: pwdScore === idx ? item.color : '' }"
                >
                  {{ item.text }}
                </p>
              </div>
            </div>
            <p v-if="pwdHint" class="text-xs mt-1" style="color: #e74242;">
              {{ pwdHint }}
            </p>
          </div>
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item :label="t('system.user.form.email')" prop="email">
          <el-input
            v-model="newFormInline.email"
            clearable
            :placeholder="t('system.user.form.emailPlaceholder')"
          />
        </el-form-item>
      </re-col>

      <re-col :value="12" :xs="24" :sm="24">
        <el-form-item :label="t('system.user.form.phone')" prop="phone">
          <el-input
            v-model="newFormInline.phone"
            clearable
            :placeholder="t('system.user.form.phonePlaceholder')"
          />
        </el-form-item>
      </re-col>

      <re-col
        v-if="!isEdit"
        :value="12"
        :xs="24"
        :sm="24"
      >
        <el-form-item :label="t('system.user.form.status')">
          <el-switch
            v-model="newFormInline.is_active"
            inline-prompt
            :active-text="t('common.status.enabled')"
            :inactive-text="t('common.status.disabled')"
            :style="switchStyle"
          />
        </el-form-item>
      </re-col>
    </el-row>

    <!-- IM 渠道绑定 -->
    <template v-if="isEdit">
      <el-divider content-position="left">{{ t("system.user.form.imDivider") }}</el-divider>
      <el-row :gutter="30">
        <re-col :value="24" :xs="24" :sm="24">
          <div class="im-bindings-section">
            <el-table :data="imBindings" stripe size="small">
              <el-table-column :label="t('system.user.form.col.platform')" prop="channel_type" min-width="180">
                <template #default="{ row }">
                  <el-tag
                    :type="channelTagStyle[row.channel_type]?.type || 'info'"
                    :effect="channelTagStyle[row.channel_type]?.effect"
                    size="small"
                  >
                    {{ channelTypeLabel[row.channel_type] || row.channel_type }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="t('system.user.form.col.imUserId')" prop="im_user_id" min-width="150" />
              <el-table-column :label="t('system.user.form.col.displayName')" prop="im_user_name" min-width="110" />
              <el-table-column :label="t('system.user.form.col.operation')" width="80" fixed="right">
                <template #default="{ row }">
                  <el-button type="danger" link size="small" @click="handleRemoveBinding(row)">{{ t("common.action.delete") }}</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button type="primary" link class="mt-2" @click="showAddBindingDialog = true">
              {{ t("system.user.form.addBinding") }}
            </el-button>
          </div>
        </re-col>
      </el-row>

      <!-- 添加绑定对话框 -->
      <el-dialog v-model="showAddBindingDialog" :title="t('system.user.form.bindingDialogTitle')" width="400px" append-to-body>
        <el-form :model="newBinding" label-width="100px">
          <el-form-item :label="t('system.user.form.platformType')" required>
            <el-select v-model="newBinding.channel_type" :placeholder="t('system.user.form.selectPlaceholder')">
              <el-option :label="channelTypeLabel.wecom" value="wecom" />
              <el-option :label="channelTypeLabel.wecom_bot_callback" value="wecom_bot_callback" />
              <el-option :label="channelTypeLabel.feishu" value="feishu" />
              <el-option :label="channelTypeLabel.dingtalk" value="dingtalk" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('system.user.form.imUserId')" required>
            <el-input v-model="newBinding.im_user_id" :placeholder="t('system.user.form.imUserIdPlaceholder')" />
          </el-form-item>
          <el-form-item :label="t('system.user.form.displayName')">
            <el-input v-model="newBinding.im_user_name" :placeholder="t('system.user.form.displayNamePlaceholder')" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showAddBindingDialog = false">{{ t("common.action.cancel") }}</el-button>
          <el-button type="primary" :loading="bindingLoading" @click="handleAddBinding">{{ t("system.user.confirm.ok") }}</el-button>
        </template>
      </el-dialog>

      <!-- 业务用户绑定（1:1，业务系统用户身份） -->
      <el-divider content-position="left">{{ t("system.user.form.businessDivider") }}</el-divider>
      <el-row :gutter="30" class="business-binding">
        <re-col :value="12" :xs="24" :sm="12">
          <el-form-item :label="t('system.user.form.businessUsername')" required label-width="100px">
            <el-input
              v-model="businessForm.business_username"
              clearable
              :placeholder="t('system.user.form.businessUsernamePlaceholder')"
            />
          </el-form-item>
        </re-col>
        <re-col :value="12" :xs="24" :sm="12">
          <el-form-item :label="t('system.user.form.businessPhone')" label-width="100px">
            <el-input
              v-model="businessForm.business_phone"
              clearable
              :placeholder="t('system.user.form.businessPhonePlaceholder')"
            />
          </el-form-item>
        </re-col>
        <re-col :value="12" :xs="24" :sm="12">
          <el-form-item :label="t('system.user.form.businessEmail')" label-width="100px">
            <el-input
              v-model="businessForm.business_email"
              clearable
              :placeholder="t('system.user.form.businessEmailPlaceholder')"
            />
          </el-form-item>
        </re-col>
        <re-col v-if="businessBinding" :value="12" :xs="24" :sm="12">
          <el-form-item label-width="100px">
            <el-button type="danger" link size="small" @click="handleRemoveBusinessBinding">
              {{ t("system.user.form.removeBusiness") }}
            </el-button>
          </el-form-item>
        </re-col>
      </el-row>
    </template>
  </el-form>
</template>

<style scoped>
/* 业务绑定 label 与 IM 平台绑定表格列头（el-table th）同色同粗 */
.business-binding :deep(.el-form-item__label) {
  color: #909399 !important;
  font-weight: 600 !important;
  font-size: 12px !important;
}
</style>
