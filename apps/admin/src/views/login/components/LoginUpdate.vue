<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import Motion from "../utils/motion";
import { message } from "@/utils/message";
import type { FormInstance } from "element-plus";
import { transformI18n, $t } from "@/plugins/i18n";
import { useUserStoreHook } from "@/store/modules/user";
import { getVerificationChannels } from "@/api/user";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import { http } from "@/utils/http";
import { ReImageVerify } from "@/components/ReImageVerify";
import Lock from "~icons/ri/lock-fill";
import Keyhole from "~icons/ri/shield-keyhole-line";
import EmailLine from "~icons/ri/mail-line";
import PhoneLine from "~icons/ri/phone-line";

const { t } = useI18n();
const loading = ref(false);
const step = ref<1 | 2 | 3>(1);

const channels = ref<{ email: boolean; sms: boolean }>({
  email: false,
  sms: false
});
const hasAnyChannel = computed(
  () => channels.value.email || channels.value.sms
);

onMounted(async () => {
  try {
    channels.value = await getVerificationChannels();
    if (channels.value.email) form.channel = "email";
    else if (channels.value.sms) form.channel = "sms";
  } catch {
    channels.value = { email: false, sms: false };
  }
});

const form = reactive({
  channel: "email" as "email" | "sms",
  target: "",
  captchaId: "",
  captchaAnswer: "",
  code: "",
  ticket: "",
  newPassword: "",
  confirmPassword: ""
});

const formRef = ref<FormInstance>();
const captchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);

const updatePasswordRules = {
  newPassword: [
    {
      required: true,
      message: transformI18n($t("login.forgetPassword.step3.newPassword")),
      trigger: "blur"
    }
  ],
  confirmPassword: [
    {
      validator: (_rule, value, callback) => {
        if (!value) {
          callback(new Error(transformI18n($t("login.purePassWordSureReg"))));
        } else if (form.newPassword !== value) {
          callback(
            new Error(transformI18n($t("login.purePassWordDifferentReg")))
          );
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
};

async function onSendCode() {
  if (!form.target) {
    message(
      transformI18n($t("login.forgetPassword.step1.targetRequired")),
      { type: "warning" }
    );
    return;
  }
  if (!form.captchaAnswer) {
    message(
      transformI18n($t("login.captcha.placeholder")),
      { type: "warning" }
    );
    return;
  }
  loading.value = true;
  try {
    await http.request<
      { sent: boolean; expires_in: number }
    >("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: form.channel,
        target: form.target,
        purpose: "reset_password",
        captcha_id: form.captchaId,
        captcha_answer: form.captchaAnswer
      }
    });
    message(
      transformI18n($t("login.forgetPassword.step1.codeSent")),
      { type: "success" }
    );
    step.value = 2;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(transformI18n($t("login.captcha.invalid")), { type: "error" });
    } else if (detail === "code_too_frequent") {
      message(transformI18n($t("login.forgetPassword.step1.tooFrequent")), {
        type: "warning"
      });
    } else if (detail === "ip_code_banned") {
      message(transformI18n($t("login.forgetPassword.step1.ipBanned")), {
        type: "error"
      });
    } else {
      message(transformI18n($t("login.forgetPassword.step1.sendFailed")), {
        type: "error"
      });
    }
    captchaRef.value?.refresh?.();
    form.captchaAnswer = "";
  } finally {
    loading.value = false;
  }
}

async function onVerifyCode() {
  if (!form.code || form.code.length !== 6) {
    message(transformI18n($t("login.forgetPassword.invalidCode")), {
      type: "warning"
    });
    return;
  }
  loading.value = true;
  try {
    const res = await http.request<{ ticket: string }>(
      "post",
      "/api/manager/auth/verification-code/verify",
      {
        data: {
          channel: form.channel,
          target: form.target,
          purpose: "reset_password",
          code: form.code
        }
      }
    );
    form.ticket = res.ticket;
    step.value = 3;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "invalid_code") {
      message(transformI18n($t("login.forgetPassword.invalidCode")), {
        type: "error"
      });
    } else {
      message(transformI18n($t("login.forgetPassword.verifyFailed")), {
        type: "error"
      });
    }
  } finally {
    loading.value = false;
  }
}

async function onResetPassword(formEl: FormInstance | undefined) {
  if (!formEl) return;
  await formEl.validate(async valid => {
    if (!valid) return;
    loading.value = true;
    try {
      await http.request("post", "/api/manager/auth/reset-password", {
        data: {
          ticket: form.ticket,
          new_password: form.newPassword
        }
      });
      message(
        transformI18n($t("login.forgetPassword.success")),
        { type: "success" }
      );
      onBack();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail === "ticket_invalid") {
        message(transformI18n($t("login.forgetPassword.ticketInvalid")), {
          type: "error"
        });
      } else {
        message(transformI18n($t("login.forgetPassword.resetFailed")), {
          type: "error"
        });
      }
    } finally {
      loading.value = false;
    }
  });
}

function onSwitchChannel(channel: "email" | "sms") {
  form.channel = channel;
  form.target = "";
}

function onBack() {
  step.value = 1;
  form.code = "";
  form.ticket = "";
  form.newPassword = "";
  form.confirmPassword = "";
  form.captchaAnswer = "";
  captchaRef.value?.refresh?.();
  useUserStoreHook().SET_CURRENTPAGE(0);
}
</script>

<template>
  <el-form
    ref="formRef"
    :model="form"
    size="large"
    class="w-full"
  >
    <!-- Step 1: 账号 + 图形验证码 → 发码 -->
    <template v-if="step === 1">
      <template v-if="hasAnyChannel">
        <Motion>
          <el-form-item>
            <el-radio-group v-model="form.channel" @change="onSwitchChannel(form.channel)">
              <el-radio-button v-if="channels.email" label="email">
                {{ t("login.forgetPassword.step1.emailChannel") }}
              </el-radio-button>
              <el-radio-button v-if="channels.sms" label="sms">
                {{ t("login.forgetPassword.step1.smsChannel") }}
              </el-radio-button>
            </el-radio-group>
          </el-form-item>
        </Motion>
        <Motion :delay="50">
          <el-form-item>
            <el-input
              v-model="form.target"
              clearable
              :placeholder="
                form.channel === 'email'
                  ? t('login.forgetPassword.step1.emailPlaceholder')
                  : t('login.forgetPassword.step1.phonePlaceholder')
              "
              :prefix-icon="
                useRenderIcon(form.channel === 'email' ? EmailLine : PhoneLine)
              "
            />
          </el-form-item>
        </Motion>
        <Motion :delay="100">
          <el-form-item>
            <div class="w-full flex justify-between">
              <el-input
                v-model="form.captchaAnswer"
                clearable
                :placeholder="t('login.captcha.placeholder')"
                :prefix-icon="useRenderIcon(Keyhole)"
                style="flex: 1"
              />
              <ReImageVerify
                ref="captchaRef"
                v-model:captcha-id="form.captchaId"
                class="ml-2!"
              />
            </div>
          </el-form-item>
        </Motion>
        <Motion :delay="150">
          <el-form-item>
            <el-button
              class="w-full"
              size="default"
              type="primary"
              :loading="loading"
              @click="onSendCode"
            >
              {{ t("login.forgetPassword.step1.sendCode") }}
            </el-button>
          </el-form-item>
        </Motion>
      </template>
      <Motion v-else :delay="50">
        <el-form-item>
          <el-alert
            :title="t('login.forgetPassword.noChannel')"
            type="warning"
            :closable="false"
            show-icon
          />
        </el-form-item>
      </Motion>
    </template>

    <!-- Step 2: 输入验证码 → 拿 ticket -->
    <template v-else-if="step === 2">
      <Motion>
        <el-form-item>
          <el-input
            v-model="form.code"
            clearable
            maxlength="6"
            :placeholder="t('login.forgetPassword.step2.codePlaceholder')"
            :prefix-icon="useRenderIcon(Keyhole)"
          />
        </el-form-item>
      </Motion>
      <Motion :delay="100">
        <el-form-item>
          <el-button
            class="w-full"
            size="default"
            type="primary"
            :loading="loading"
            @click="onVerifyCode"
          >
            {{ t("login.forgetPassword.step2.verify") }}
          </el-button>
        </el-form-item>
      </Motion>
    </template>

    <!-- Step 3: 输入新密码 + 确认 → 重置 -->
    <template v-else>
      <Motion>
        <el-form-item prop="newPassword">
          <el-input
            v-model="form.newPassword"
            clearable
            show-password
            :placeholder="t('login.forgetPassword.step3.newPassword')"
            :prefix-icon="useRenderIcon(Lock)"
          />
        </el-form-item>
      </Motion>
      <Motion :delay="100">
        <el-form-item prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            clearable
            show-password
            :placeholder="t('login.forgetPassword.step3.confirmPassword')"
            :prefix-icon="useRenderIcon(Lock)"
          />
        </el-form-item>
      </Motion>
      <Motion :delay="150">
        <el-form-item>
          <el-button
            class="w-full"
            size="default"
            type="primary"
            :loading="loading"
            @click="onResetPassword(formRef)"
          >
            {{ t("login.forgetPassword.step3.reset") }}
          </el-button>
        </el-form-item>
      </Motion>
    </template>

    <Motion :delay="200">
      <el-form-item>
        <el-button class="w-full" size="default" @click="onBack">
          {{ t("login.pureBack") }}
        </el-button>
      </el-form-item>
    </Motion>
  </el-form>
</template>
