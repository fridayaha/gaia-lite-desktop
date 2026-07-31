<script setup lang="ts">
import { useI18n } from "vue-i18n";
import Motion from "./utils/motion";
import { useRouter } from "vue-router";
import { message } from "@/utils/message";
import { loginRules } from "./utils/rule";
import TypeIt from "@/components/ReTypeit";
import { debounce } from "@pureadmin/utils";
import { useNav } from "@/layout/hooks/useNav";
import { useEventListener } from "@vueuse/core";
import type { FormInstance, FormRules } from "element-plus";
import { $t, transformI18n } from "@/plugins/i18n";
import { operates, thirdParty } from "./utils/enums";
import { useLayout } from "@/layout/hooks/useLayout";
import LoginPhone from "./components/LoginPhone.vue";
import LoginRegist from "./components/LoginRegist.vue";
import LoginUpdate from "./components/LoginUpdate.vue";
import LoginQrCode from "./components/LoginQrCode.vue";
import { useUserStoreHook } from "@/store/modules/user";
import { getVerificationChannels } from "@/api/user";
import { initRouter, getTopMenu } from "@/router/utils";
import { bg, avatar, illustration } from "./utils/static";
import { ReImageVerify } from "@/components/ReImageVerify";
import { ref, toRaw, reactive, watch, computed, onMounted } from "vue";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import { useTranslationLang } from "@/layout/hooks/useTranslationLang";
import { useDataThemeChange } from "@/layout/hooks/useDataThemeChange";
import { http } from "@/utils/http";

import dayIcon from "@/assets/svg/day.svg?component";
import darkIcon from "@/assets/svg/dark.svg?component";
import globalization from "@/assets/svg/globalization.svg?component";
import Lock from "~icons/ri/lock-fill";
import Check from "~icons/ep/check";
import User from "~icons/ri/user-3-fill";
import Keyhole from "~icons/ri/shield-keyhole-line";
import EmailLine from "~icons/ri/mail-line";
import PhoneLine from "~icons/ri/phone-line";

defineOptions({
  name: "Login"
});

const imgCode = ref("");
const router = useRouter();
const loading = ref(false);
const disabled = ref(false);
const { t } = useI18n();
const { initStorage } = useLayout();
initStorage();
const { dataTheme, themeMode, dataThemeChange } = useDataThemeChange();
dataThemeChange(themeMode.value);
const { title, getDropdownItemStyle, getDropdownItemClass } = useNav();
const { locale, translationCh, translationEn } = useTranslationLang();

type LoginTab = "account" | "email" | "phone" | "smsCode";
const activeTab = ref<LoginTab>("account");
const channels = ref<{ email: boolean; sms: boolean }>({
  email: false,
  sms: false
});

const tabs = computed(() => {
  const list: { label: string; value: LoginTab }[] = [
    { label: t("login.tabs.account"), value: "account" },
    { label: t("login.tabs.email"), value: "email" },
    { label: t("login.tabs.phone"), value: "phone" }
  ];
  if (channels.value.sms) {
    list.push({ label: t("login.tabs.smsCode"), value: "smsCode" });
  }
  return list;
});

onMounted(async () => {
  try {
    channels.value = await getVerificationChannels();
  } catch {
    channels.value = { email: false, sms: false };
  }
});

const currentPage = computed(() => useUserStoreHook().currentPage);

const accountForm = reactive({
  username: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false
});
const emailForm = reactive({
  email: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false
});
const phoneForm = reactive({
  phone: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false
});
const smsCodeForm = reactive({
  phone: "",
  captchaId: "",
  captchaAnswer: "",
  code: ""
});

const accountFormRef = ref<FormInstance>();
const emailFormRef = ref<FormInstance>();
const phoneFormRef = ref<FormInstance>();
const smsCodeFormRef = ref<FormInstance>();
const captchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const accountCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const emailCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const phoneCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);

const emailRules: FormRules = {
  email: [
    {
      required: true,
      message: transformI18n($t("login.purePhoneReg")),
      trigger: "blur"
    }
  ],
  password: [
    {
      validator: (_rule, value, callback) => {
        if (value === "") {
          callback(new Error(transformI18n($t("login.purePassWordReg"))));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
};

const phonePasswordRules: FormRules = {
  phone: [
    {
      required: true,
      message: transformI18n($t("login.purePhoneReg")),
      trigger: "blur"
    }
  ],
  password: [
    {
      validator: (_rule, value, callback) => {
        if (value === "") {
          callback(new Error(transformI18n($t("login.purePassWordReg"))));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
};

const smsCountdown = ref(0);
let smsTimer: number | null = null;
function startCountdown() {
  smsCountdown.value = 60;
  if (smsTimer) window.clearInterval(smsTimer);
  smsTimer = window.setInterval(() => {
    if (smsCountdown.value > 0) {
      smsCountdown.value--;
    } else if (smsTimer) {
      window.clearInterval(smsTimer);
      smsTimer = null;
    }
  }, 1000);
}

async function onSendSmsCode() {
  if (!smsCodeForm.phone) {
    message(transformI18n($t("login.purePhoneReg")), { type: "warning" });
    return;
  }
  if (!smsCodeForm.captchaAnswer) {
    message(transformI18n($t("login.captcha.placeholder")), {
      type: "warning"
    });
    return;
  }
  loading.value = true;
  try {
    await http.request("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: "sms",
        target: smsCodeForm.phone,
        purpose: "login",
        captcha_id: smsCodeForm.captchaId,
        captcha_answer: smsCodeForm.captchaAnswer
      }
    });
    message(transformI18n($t("login.smsLogin.codeSent")), {
      type: "success"
    });
    startCountdown();
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(transformI18n($t("login.captcha.invalid")), {
        type: "error"
      });
    } else if (detail === "code_too_frequent") {
      message(
        transformI18n($t("login.forgetPassword.step1.tooFrequent")),
        { type: "warning" }
      );
    } else if (detail === "ip_code_banned") {
      message(transformI18n($t("login.forgetPassword.step1.ipBanned")), {
        type: "error"
      });
    } else if (detail === "no_active_provider") {
      message(transformI18n($t("login.forgetPassword.noChannel")), {
        type: "error"
      });
    } else {
      message(transformI18n($t("login.forgetPassword.step1.sendFailed")), {
        type: "error"
      });
    }
    captchaRef.value?.refresh?.();
    smsCodeForm.captchaAnswer = "";
  } finally {
    loading.value = false;
  }
}

async function doLogin(loginPromise: Promise<any>) {
  loading.value = true;
  disabled.value = true;
  try {
    await loginPromise;
    await initRouter();
    const topMenu = getTopMenu(true);
    if (topMenu?.path) {
      router.push(topMenu.path).then(() => {
        message(t("login.pureLoginSuccess"), { type: "success" });
      });
    } else {
      message(t("login.failed.noMenuPermission"), { type: "warning" });
      router.push("/account-settings");
    }
  } catch (err: any) {
    const detail = err?.response?.data?.detail;
    let msg = t("login.failed.invalidCredentials");
    if (detail === "account_locked") {
      const retryAfter = parseInt(
        err.response.headers["retry-after"] || "0",
        10
      );
      const minutes = Math.max(1, Math.ceil(retryAfter / 60));
      msg = t("login.failed.accountLocked", { minutes });
    } else if (detail === "captcha_required") {
      // 后端检测到该账号连续失败 >= 2 次，要求图形验证码：显示 captcha UI + 自动刷新
      if (activeTab.value === "account") accountForm.captchaRequired = true;
      else if (activeTab.value === "email") emailForm.captchaRequired = true;
      else if (activeTab.value === "phone") phoneForm.captchaRequired = true;
      msg = t("login.captcha.required");
    } else if (detail === "captcha_invalid") {
      msg = t("login.captcha.invalid");
    } else if (detail === "too_many_requests") {
      msg = t("login.failed.tooManyRequests");
    } else if (detail === "ip_banned") {
      msg = t("login.failed.ipBanned");
    } else if (detail === "invalid_code") {
      msg = t("login.smsLogin.invalidCode");
    }
    message(msg, { type: "error" });
  } finally {
    // 失败后刷新当前 tab 的 captcha（captcha 一次性消费，旧 id 已失效）
    if (activeTab.value === "account" && accountForm.captchaRequired) {
      accountCaptchaRef.value?.refresh?.();
      accountForm.captchaAnswer = "";
    } else if (activeTab.value === "email" && emailForm.captchaRequired) {
      emailCaptchaRef.value?.refresh?.();
      emailForm.captchaAnswer = "";
    } else if (activeTab.value === "phone" && phoneForm.captchaRequired) {
      phoneCaptchaRef.value?.refresh?.();
      phoneForm.captchaAnswer = "";
    } else if (activeTab.value === "smsCode") {
      captchaRef.value?.refresh?.();
      smsCodeForm.captchaAnswer = "";
    }
    disabled.value = false;
    loading.value = false;
  }
}

function onLoginAccount() {
  accountFormRef.value?.validate(valid => {
    if (!valid) return;
    const payload: {
      username: string;
      password: string;
      captcha_id?: string;
      captcha_answer?: string;
    } = {
      username: accountForm.username,
      password: accountForm.password
    };
    if (accountForm.captchaRequired) {
      payload.captcha_id = accountForm.captchaId;
      payload.captcha_answer = accountForm.captchaAnswer;
    }
    doLogin(useUserStoreHook().loginByUsername(payload));
  });
}

function onLoginEmail() {
  emailFormRef.value?.validate(valid => {
    if (!valid) return;
    const payload: {
      contact_type: "email";
      contact: string;
      password: string;
      captcha_id?: string;
      captcha_answer?: string;
    } = {
      contact_type: "email",
      contact: emailForm.email,
      password: emailForm.password
    };
    if (emailForm.captchaRequired) {
      payload.captcha_id = emailForm.captchaId;
      payload.captcha_answer = emailForm.captchaAnswer;
    }
    doLogin(useUserStoreHook().loginByContact(payload));
  });
}

function onLoginPhone() {
  phoneFormRef.value?.validate(valid => {
    if (!valid) return;
    const payload: {
      contact_type: "phone";
      contact: string;
      password: string;
      captcha_id?: string;
      captcha_answer?: string;
    } = {
      contact_type: "phone",
      contact: phoneForm.phone,
      password: phoneForm.password
    };
    if (phoneForm.captchaRequired) {
      payload.captcha_id = phoneForm.captchaId;
      payload.captcha_answer = phoneForm.captchaAnswer;
    }
    doLogin(useUserStoreHook().loginByContact(payload));
  });
}

function onLoginSmsCode() {
  if (!smsCodeForm.phone) {
    message(transformI18n($t("login.purePhoneReg")), { type: "warning" });
    return;
  }
  if (!smsCodeForm.code || smsCodeForm.code.length !== 6) {
    message(transformI18n($t("login.forgetPassword.invalidCode")), {
      type: "warning"
    });
    return;
  }
  doLogin(
    useUserStoreHook().loginBySmsCode({
      phone: smsCodeForm.phone,
      code: smsCodeForm.code
    })
  );
}

function submitCurrentTab() {
  if (activeTab.value === "account") onLoginAccount();
  else if (activeTab.value === "email") onLoginEmail();
  else if (activeTab.value === "phone") onLoginPhone();
  else onLoginSmsCode();
}

const immediateDebounce: any = debounce(submitCurrentTab, 1000, true);

useEventListener(document, "keydown", ({ code }) => {
  if (
    ["Enter", "NumpadEnter"].includes(code) &&
    !disabled.value &&
    !loading.value
  )
    immediateDebounce();
});

watch(imgCode, value => {
  useUserStoreHook().SET_VERIFYCODE(value);
});
</script>

<template>
  <div class="select-none">
    <img :src="bg" class="wave" />
    <div class="flex-c absolute right-5 top-3">
      <!-- 主题 -->
      <el-switch
        v-model="dataTheme"
        inline-prompt
        :active-icon="dayIcon"
        :inactive-icon="darkIcon"
        @change="dataThemeChange"
      />
      <!-- 国际化 -->
      <el-dropdown trigger="click">
        <globalization
          class="hover:text-primary hover:bg-transparent! size-5 ml-1.5 cursor-pointer outline-hidden duration-300"
        />
        <template #dropdown>
          <el-dropdown-menu class="translation">
            <el-dropdown-item
              :style="getDropdownItemStyle(locale, 'zh')"
              :class="['dark:text-white!', getDropdownItemClass(locale, 'zh')]"
              @click="translationCh"
            >
              <IconifyIconOffline
                v-show="locale === 'zh'"
                class="check-zh"
                :icon="Check"
              />
              简体中文
            </el-dropdown-item>
            <el-dropdown-item
              :style="getDropdownItemStyle(locale, 'en')"
              :class="['dark:text-white!', getDropdownItemClass(locale, 'en')]"
              @click="translationEn"
            >
              <span v-show="locale === 'en'" class="check-en">
                <IconifyIconOffline :icon="Check" />
              </span>
              English
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
    <div class="login-container">
      <div class="img">
        <component :is="toRaw(illustration)" />
        <p class="login-slogan">{{ t("login.slogan") }}</p>
      </div>
      <div class="login-box">
        <div class="login-form">
          <div class="login-logo-wrap">
            <img src="/favicon.svg" class="login-logo-img" width="128" height="128" alt="logo" />
          </div>
          <Motion>
            <h2 class="outline-hidden">
              <TypeIt
                :options="{ strings: [title], cursor: false, speed: 100 }"
              />
            </h2>
          </Motion>

          <!-- 忘记密码（currentPage === 4 时单独渲染） -->
          <LoginUpdate v-if="currentPage === 4" />

          <!-- 登录表单（ currentPage === 0 才显示） -->
          <template v-else>
            <Motion :delay="50">
              <el-segmented
                v-model="activeTab"
                :options="tabs"
                size="large"
                class="mb-4 w-full"
              />
            </Motion>

            <!-- 账号密码 tab -->
            <el-form
              v-if="activeTab === 'account'"
              ref="accountFormRef"
              :model="accountForm"
              :rules="loginRules"
              size="large"
            >
              <Motion :delay="100">
                <el-form-item
                  :rules="[
                    {
                      required: true,
                      message: transformI18n($t('login.pureUsernameReg')),
                      trigger: 'blur'
                    }
                  ]"
                  prop="username"
                >
                  <el-input
                    v-model="accountForm.username"
                    clearable
                    :placeholder="t('login.pureUsername')"
                    :prefix-icon="useRenderIcon(User)"
                  />
                </el-form-item>
              </Motion>
              <Motion :delay="150">
                <el-form-item prop="password">
                  <el-input
                    v-model="accountForm.password"
                    clearable
                    show-password
                    :placeholder="t('login.purePassword')"
                    :prefix-icon="useRenderIcon(Lock)"
                  />
                </el-form-item>
              </Motion>
              <Motion v-if="accountForm.captchaRequired" :delay="200">
                <el-form-item>
                  <div class="w-full flex justify-between">
                    <el-input
                      v-model="accountForm.captchaAnswer"
                      clearable
                      maxlength="4"
                      :placeholder="t('login.captcha.placeholder')"
                      :prefix-icon="useRenderIcon(Keyhole)"
                      style="flex: 1"
                    />
                    <ReImageVerify
                      ref="accountCaptchaRef"
                      v-model:captcha-id="accountForm.captchaId"
                      class="ml-2!"
                    />
                  </div>
                </el-form-item>
              </Motion>
              <Motion :delay="250">
                <el-form-item>
                  <div class="w-full flex-bc">
                    <span></span>
                    <el-button
                      link
                      type="primary"
                      @click="useUserStoreHook().SET_CURRENTPAGE(4)"
                    >
                      {{ t("login.pureForget") }}
                    </el-button>
                  </div>
                  <el-button
                    class="w-full mt-4!"
                    size="default"
                    type="primary"
                    :loading="loading"
                    :disabled="disabled"
                    @click="onLoginAccount"
                  >
                    {{ t("login.pureLogin") }}
                  </el-button>
                </el-form-item>
              </Motion>
            </el-form>

            <!-- 邮箱 tab -->
            <el-form
              v-else-if="activeTab === 'email'"
              ref="emailFormRef"
              :model="emailForm"
              :rules="emailRules"
              size="large"
            >
              <Motion :delay="100">
                <el-form-item prop="email">
                  <el-input
                    v-model="emailForm.email"
                    clearable
                    :placeholder="t('login.email')"
                    :prefix-icon="useRenderIcon(EmailLine)"
                  />
                </el-form-item>
              </Motion>
              <Motion :delay="150">
                <el-form-item prop="password">
                  <el-input
                    v-model="emailForm.password"
                    clearable
                    show-password
                    :placeholder="t('login.purePassword')"
                    :prefix-icon="useRenderIcon(Lock)"
                  />
                </el-form-item>
              </Motion>
              <Motion v-if="emailForm.captchaRequired" :delay="200">
                <el-form-item>
                  <div class="w-full flex justify-between">
                    <el-input
                      v-model="emailForm.captchaAnswer"
                      clearable
                      maxlength="4"
                      :placeholder="t('login.captcha.placeholder')"
                      :prefix-icon="useRenderIcon(Keyhole)"
                      style="flex: 1"
                    />
                    <ReImageVerify
                      ref="emailCaptchaRef"
                      v-model:captcha-id="emailForm.captchaId"
                      class="ml-2!"
                    />
                  </div>
                </el-form-item>
              </Motion>
              <Motion :delay="250">
                <el-form-item>
                  <div class="w-full flex-bc">
                    <span></span>
                    <el-button
                      link
                      type="primary"
                      @click="useUserStoreHook().SET_CURRENTPAGE(4)"
                    >
                      {{ t("login.pureForget") }}
                    </el-button>
                  </div>
                  <el-button
                    class="w-full mt-4!"
                    size="default"
                    type="primary"
                    :loading="loading"
                    :disabled="disabled"
                    @click="onLoginEmail"
                  >
                    {{ t("login.pureLogin") }}
                  </el-button>
                </el-form-item>
              </Motion>
            </el-form>

            <!-- 手机 tab -->
            <el-form
              v-else-if="activeTab === 'phone'"
              ref="phoneFormRef"
              :model="phoneForm"
              :rules="phonePasswordRules"
              size="large"
            >
              <Motion :delay="100">
                <el-form-item prop="phone">
                  <el-input
                    v-model="phoneForm.phone"
                    clearable
                    :placeholder="t('login.phone')"
                    :prefix-icon="useRenderIcon(PhoneLine)"
                  />
                </el-form-item>
              </Motion>
              <Motion :delay="150">
                <el-form-item prop="password">
                  <el-input
                    v-model="phoneForm.password"
                    clearable
                    show-password
                    :placeholder="t('login.purePassword')"
                    :prefix-icon="useRenderIcon(Lock)"
                  />
                </el-form-item>
              </Motion>
              <Motion v-if="phoneForm.captchaRequired" :delay="200">
                <el-form-item>
                  <div class="w-full flex justify-between">
                    <el-input
                      v-model="phoneForm.captchaAnswer"
                      clearable
                      maxlength="4"
                      :placeholder="t('login.captcha.placeholder')"
                      :prefix-icon="useRenderIcon(Keyhole)"
                      style="flex: 1"
                    />
                    <ReImageVerify
                      ref="phoneCaptchaRef"
                      v-model:captcha-id="phoneForm.captchaId"
                      class="ml-2!"
                    />
                  </div>
                </el-form-item>
              </Motion>
              <Motion :delay="250">
                <el-form-item>
                  <div class="w-full flex-bc">
                    <span></span>
                    <el-button
                      link
                      type="primary"
                      @click="useUserStoreHook().SET_CURRENTPAGE(4)"
                    >
                      {{ t("login.pureForget") }}
                    </el-button>
                  </div>
                  <el-button
                    class="w-full mt-4!"
                    size="default"
                    type="primary"
                    :loading="loading"
                    :disabled="disabled"
                    @click="onLoginPhone"
                  >
                    {{ t("login.pureLogin") }}
                  </el-button>
                </el-form-item>
              </Motion>
            </el-form>

            <!-- 手机验证码 tab -->
            <el-form
              v-else
              ref="smsCodeFormRef"
              :model="smsCodeForm"
              size="large"
            >
              <Motion :delay="100">
                <el-form-item>
                  <el-input
                    v-model="smsCodeForm.phone"
                    clearable
                    :placeholder="t('login.phone')"
                    :prefix-icon="useRenderIcon(PhoneLine)"
                  />
                </el-form-item>
              </Motion>
              <Motion :delay="150">
                <el-form-item>
                  <div class="w-full flex justify-between">
                    <el-input
                      v-model="smsCodeForm.captchaAnswer"
                      clearable
                      :placeholder="t('login.captcha.placeholder')"
                      :prefix-icon="useRenderIcon(Keyhole)"
                      style="flex: 1"
                    />
                    <ReImageVerify
                      ref="captchaRef"
                      v-model:captcha-id="smsCodeForm.captchaId"
                      class="ml-2!"
                    />
                  </div>
                </el-form-item>
              </Motion>
              <Motion :delay="200">
                <el-form-item>
                  <div class="w-full flex justify-between gap-2">
                    <el-input
                      v-model="smsCodeForm.code"
                      clearable
                      maxlength="6"
                      :placeholder="t('login.smsLogin.code')"
                      :prefix-icon="useRenderIcon(Keyhole)"
                      style="flex: 1"
                    />
                    <el-button
                      :loading="loading"
                      :disabled="smsCountdown > 0"
                      @click="onSendSmsCode"
                    >
                      {{
                        smsCountdown > 0
                          ? `${smsCountdown}s`
                          : t("login.smsLogin.sendCode")
                      }}
                    </el-button>
                  </div>
                </el-form-item>
              </Motion>
              <Motion :delay="250">
                <el-form-item>
                  <div class="w-full flex-bc">
                    <span></span>
                    <el-button
                      link
                      type="primary"
                      @click="useUserStoreHook().SET_CURRENTPAGE(4)"
                    >
                      {{ t("login.pureForget") }}
                    </el-button>
                  </div>
                  <el-button
                    class="w-full mt-4!"
                    size="default"
                    type="primary"
                    :loading="loading"
                    :disabled="disabled"
                    @click="onLoginSmsCode"
                  >
                    {{ t("login.pureLogin") }}
                  </el-button>
                </el-form-item>
              </Motion>
            </el-form>
          </template>

          <Motion v-if="false" :delay="350">
            <el-form-item>
              <el-divider>
                <p class="text-gray-500 text-xs">
                  {{ t("login.pureThirdLogin") }}
                </p>
              </el-divider>
              <div class="w-full flex justify-evenly">
                <span
                  v-for="(item, index) in thirdParty"
                  :key="index"
                  :title="t(item.title)"
                >
                  <IconifyIconOnline
                    :icon="`ri:${item.icon}-fill`"
                    width="20"
                    class="cursor-pointer text-gray-500 hover:text-blue-400"
                  />
                </span>
              </div>
            </el-form-item>
          </Motion>
          <!-- 手机号登录 -->
          <LoginPhone v-if="false" />
          <!-- 二维码登录 -->
          <LoginQrCode v-if="false" />
          <!-- 注册 -->
          <LoginRegist v-if="false" />
        </div>
      </div>
    </div>
    <div
      class="w-full flex-c absolute bottom-3 text-sm text-[rgba(0,0,0,0.6)] dark:text-[rgba(220,220,242,0.8)]"
    >
      Copyright © 2026-2028 {{ title }}
    </div>
  </div>
</template>

<style scoped>
@import url("@/style/login.css");
</style>

<style lang="scss" scoped>
:deep(.el-input-group__append, .el-input-group__prepend) {
  padding: 0;
}

.login-logo-wrap {
  text-align: center;
  margin-bottom: 10px;
}

.login-logo-img {
  display: inline-block;
  vertical-align: middle;
}

.login-slogan {
  margin-top: 24px;
  text-align: center;
  font-size: 22px;
  font-weight: 500;
  letter-spacing: 4px;
  color: #ffffff;
  opacity: 0.95;
}

.translation {
  :deep(.el-dropdown-menu__item) {
    padding: 5px 40px;
  }

  .check-zh {
    position: absolute;
    left: 20px;
  }

  .check-en {
    position: absolute;
    left: 20px;
  }
}
</style>
