<script setup lang="ts">
import { uploadAvatar, updateMe, changePassword, getPresetAvatars } from "@/api/user";
import { message } from "@/utils/message";
import { onMounted, reactive, ref, computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { zxcvbn } from "@/utils/zxcvbn";
import { type UserInfo, getMine } from "@/api/user";
import type { FormInstance, FormRules } from "element-plus";
import ReCropperPreview from "@/components/ReCropperPreview";
import { ReImageVerify } from "@/components/ReImageVerify";
import { http } from "@/utils/http";
import { createFormData, deviceDetection, isAllEmpty, isEmail, storageLocal } from "@pureadmin/utils";
import uploadLine from "~icons/ri/upload-line";
import galleryLine from "~icons/ri/gallery-line";
import editLine from "~icons/ri/edit-line";
import verifyBadgeLine from "~icons/ri/verified-badge-line";
import { useUserStoreHook } from "@/store/modules/user";
import { type DataInfo, userKey } from "@/utils/auth";

function syncAvatarEverywhere(avatarUrl: string) {
  const userStore = useUserStoreHook();
  userStore.SET_AVATAR(avatarUrl);
  const stored =
    storageLocal().getItem<DataInfo<number>>(userKey) ||
    ({} as DataInfo<number>);
  stored.avatar = avatarUrl;
  storageLocal().setItem(userKey, stored);
}

function syncNicknameEverywhere(nickname: string) {
  const userStore = useUserStoreHook();
  userStore.SET_NICKNAME(nickname);
  const stored =
    storageLocal().getItem<DataInfo<number>>(userKey) ||
    ({} as DataInfo<number>);
  stored.nickname = nickname;
  storageLocal().setItem(userKey, stored);
}


defineOptions({
  name: "Profile"
});

const { t } = useI18n();
const imgSrc = ref("");
const cropperBlob = ref();
const cropRef = ref();
const uploadRef = ref();
const isShow = ref(false);
const userInfoFormRef = ref<FormInstance>();

const userInfos = reactive({
  avatar: "",
  username: "",
  nickname: "",
  email: "",
  phone: "",
  avatar_url: "",
  email_verified: false,
  phone_verified: false
});

// 角色：只读展示，支持多角色。来源 userStore.roles（登录时 /me 写入 localStorage）
const userStore = useUserStoreHook();
const roles = computed(() => userStore.roles ?? []);
function roleTagType(role: string): "danger" | "primary" | "success" | "info" {
  if (role === "系统管理员") return "danger";
  if (role === "运维人员") return "primary";
  if (role === "终端用户") return "success";
  return "info";
}

const rules = computed<FormRules<UserInfo>>(() => ({
  nickname: [{ required: true, message: t("account.profile.nicknameRequired"), trigger: "blur" }]
}));

const onChange = uploadFile => {
  const reader = new FileReader();
  reader.onload = e => {
    imgSrc.value = e.target.result as string;
    isShow.value = true;
  };
  reader.readAsDataURL(uploadFile.raw);
};

const handleClose = () => {
  cropRef.value.hidePopover();
  uploadRef.value.clearFiles();
  isShow.value = false;
};

const onCropper = ({ blob }) => (cropperBlob.value = blob);

const handleSubmitImage = () => {
  const formData = createFormData({
    files: new File([cropperBlob.value], "avatar")
  });
  uploadAvatar(formData)
    .then(({ code, data }) => {
      if (code === 0) {
        userInfos.avatar_url = data.avatar_url;
        userInfos.avatar = data.avatar_url;
        syncAvatarEverywhere(data.avatar_url);
        message(t("account.profile.msg.avatarOk"), { type: "success" });
        handleClose();
      } else {
        message(t("account.profile.msg.avatarFailed"));
      }
    })
    .catch(error => {
      message(t("account.profile.msg.submitError", { error }), { type: "error" });
    });
};

// 更新信息 — nickname + avatar_url only（email/phone 走改绑 dialog）
const onSubmit = async (formEl: FormInstance) => {
  await formEl.validate((valid, fields) => {
    if (valid) {
      updateMe({
        real_name: userInfos.nickname,
        avatar_url: userInfos.avatar_url
      })
        .then(({ code, data }) => {
          if (code === 0) {
            // 同步 store + localStorage，让 aside + navbar 响应式刷新
            syncNicknameEverywhere(data.nickname || userInfos.nickname);
            if (data.avatar_url) {
              syncAvatarEverywhere(data.avatar_url);
            }
            message(t("account.profile.msg.updateOk"), { type: "success" });
          }
        })
        .catch(error => {
          message(t("account.profile.msg.submitError", { error }), {
            type: "error"
          });
        });
    } else {
      console.log("error submit!", fields);
    }
  });
};

// 修改密码
const pwdVisible = ref(false);
const pwdFormRef = ref<FormInstance>();
const pwdForm = reactive({
  old_password: "",
  new_password: "",
  confirm: ""
});
const pwdRules = computed<FormRules>(() => ({
  old_password: [
    { required: true, message: t("account.profile.pwdDialog.msg.weak"), trigger: "blur" }
  ],
  new_password: [
    { required: true, message: t("account.profile.pwdDialog.msg.weak"), trigger: "blur" },
    { min: 8, message: t("account.profile.pwdDialog.msg.weak"), trigger: "blur" }
  ],
  confirm: [
    { required: true, message: t("account.profile.pwdDialog.msg.mismatch"), trigger: "blur" },
    {
      validator: (_rule, value, callback) => {
        if (value !== pwdForm.new_password) {
          callback(new Error(t("account.profile.pwdDialog.msg.mismatch")));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
}));

// 密码强度联动：zxcvbn 评分 0~4 → 5 段进度条 + 文案
const pwdProgress = computed(() => [
  { color: "#e74242", text: t("account.profile.pwdDialog.strength.veryWeak") },
  { color: "#EFBD47", text: t("account.profile.pwdDialog.strength.weak") },
  { color: "#ffa500", text: t("account.profile.pwdDialog.strength.fair") },
  { color: "#1bbf1b", text: t("account.profile.pwdDialog.strength.strong") },
  { color: "#008000", text: t("account.profile.pwdDialog.strength.veryStrong") }
]);
const curScore = ref(-1);
watch(
  () => pwdForm.new_password,
  newPwd => (curScore.value = isAllEmpty(newPwd) ? -1 : zxcvbn(newPwd).score)
);

const openPwdDialog = () => {
  pwdForm.old_password = "";
  pwdForm.new_password = "";
  pwdForm.confirm = "";
  curScore.value = -1;
  pwdVisible.value = true;
};

const onPwdSubmit = async (formEl: FormInstance) => {
  await formEl.validate((valid, fields) => {
    if (valid) {
      changePassword({
        old_password: pwdForm.old_password,
        new_password: pwdForm.new_password
      })
        .then(({ code }) => {
          if (code === 0) {
            message(t("account.profile.pwdDialog.msg.ok"), { type: "success" });
            pwdVisible.value = false;
          }
        })
        .catch(err => {
          const detail = err?.response?.data?.detail;
          if (detail === "wrong_old_password") {
            message(t("account.profile.pwdDialog.msg.wrongOld"), {
              type: "error"
            });
          } else {
            message(t("account.profile.msg.submitError", { error: detail || err?.message }), {
              type: "error"
            });
          }
        });
    } else {
      console.log("error submit!", fields);
    }
  });
};

onMounted(async () => {
  // /auth/me 返回裸 dict（无 {code, data} 包装），直接解构字段
  const data = await getMine();
  if (data) {
    Object.assign(userInfos, data);
    // 后端返回 avatar_url，前端展示字段 alias 到 avatar
    userInfos.avatar = data.avatar_url || "";
    userInfos.nickname = data.real_name || data.nickname || "";
    userInfos.email_verified = !!data.email_verified;
    userInfos.phone_verified = !!data.phone_verified;
    // /me 拉到的最新值回写 Pinia + localStorage，让 navbar 与侧边栏头像同步
    if (userInfos.avatar) syncAvatarEverywhere(userInfos.avatar);
    if (userInfos.nickname) syncNicknameEverywhere(userInfos.nickname);
  }
});

// =========================================
// 改绑邮箱 / 手机号（0.8.104+，Phase 1）
// 流程：点击「修改」→ 输入新 contact → 发码 → 输入 code → 确认
// 后端 endpoint：/me/change-email /me/change-phone（需 Bearer token）
// =========================================

const changeEmailVisible = ref(false);
const changeEmailForm = reactive({
  new_email: "",
  captcha_id: "",
  captcha_answer: "",
  code: ""
});
const changeEmailCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const changeEmailCodeSending = ref(false);
const changeEmailCodeCountdown = ref(0);
let changeEmailTimer: ReturnType<typeof setInterval> | null = null;

const changePhoneVisible = ref(false);
const changePhoneForm = reactive({
  new_phone: "",
  captcha_id: "",
  captcha_answer: "",
  code: ""
});
const changePhoneCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const changePhoneCodeSending = ref(false);
const changePhoneCodeCountdown = ref(0);
let changePhoneTimer: ReturnType<typeof setInterval> | null = null;

function startCountdown(kind: "email" | "phone", seconds = 60) {
  const counter = kind === "email" ? changeEmailCodeCountdown : changePhoneCodeCountdown;
  const existing =
    kind === "email" ? changeEmailTimer : changePhoneTimer;
  if (existing) clearInterval(existing);
  counter.value = seconds;
  const handle = setInterval(() => {
    counter.value -= 1;
    if (counter.value <= 0) {
      clearInterval(handle);
      counter.value = 0;
    }
  }, 1000);
  if (kind === "email") changeEmailTimer = handle;
  else changePhoneTimer = handle;
}

function openChangeEmailDialog() {
  changeEmailForm.new_email = "";
  changeEmailForm.captcha_id = "";
  changeEmailForm.captcha_answer = "";
  changeEmailForm.code = "";
  changeEmailCodeCountdown.value = 0;
  changeEmailVisible.value = true;
  // dialog 渲染后刷新一次 captcha
  setTimeout(() => changeEmailCaptchaRef.value?.refresh?.(), 50);
}

async function sendChangeEmailCode() {
  if (!changeEmailForm.new_email || !isEmail(changeEmailForm.new_email)) {
    message(t("account.profile.emailInvalid"), { type: "warning" });
    return;
  }
  if (!changeEmailForm.captcha_answer) {
    message(t("login.captcha.placeholder"), { type: "warning" });
    return;
  }
  changeEmailCodeSending.value = true;
  try {
    await http.request("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: "email",
        target: changeEmailForm.new_email,
        purpose: "change_email",
        captcha_id: changeEmailForm.captcha_id,
        captcha_answer: changeEmailForm.captcha_answer
      }
    });
    message(t("login.changeEmail.codeSent"), { type: "success" });
    startCountdown("email", 60);
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(t("login.captcha.invalid"), { type: "error" });
    } else if (detail === "code_too_frequent") {
      message(t("login.forgetPassword.step1.tooFrequent"), { type: "warning" });
    } else {
      message(t("login.forgetPassword.step1.sendFailed"), { type: "error" });
    }
    changeEmailCaptchaRef.value?.refresh?.();
    changeEmailForm.captcha_answer = "";
  } finally {
    changeEmailCodeSending.value = false;
  }
}

async function confirmChangeEmail() {
  if (!changeEmailForm.code || changeEmailForm.code.length !== 6) {
    message(t("login.forgetPassword.invalidCode"), { type: "warning" });
    return;
  }
  try {
    await http.request("post", "/api/manager/auth/me/change-email", {
      data: {
        new_email: changeEmailForm.new_email,
        code: changeEmailForm.code
      }
    });
    userInfos.email = changeEmailForm.new_email;
    message(t("login.changeEmail.success"), { type: "success" });
    changeEmailVisible.value = false;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "email_in_use") {
      message(t("login.changeEmail.inUse"), { type: "error" });
    } else if (detail === "invalid_code") {
      message(t("login.changeEmail.invalidCode"), { type: "error" });
    } else {
      message(t("login.changeEmail.failed"), { type: "error" });
    }
  }
}

function openChangePhoneDialog() {
  changePhoneForm.new_phone = "";
  changePhoneForm.captcha_id = "";
  changePhoneForm.captcha_answer = "";
  changePhoneForm.code = "";
  changePhoneCodeCountdown.value = 0;
  changePhoneVisible.value = true;
  setTimeout(() => changePhoneCaptchaRef.value?.refresh?.(), 50);
}

async function sendChangePhoneCode() {
  if (!changePhoneForm.new_phone) {
    message(t("account.profile.phonePlaceholder"), { type: "warning" });
    return;
  }
  if (!changePhoneForm.captcha_answer) {
    message(t("login.captcha.placeholder"), { type: "warning" });
    return;
  }
  changePhoneCodeSending.value = true;
  try {
    await http.request("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: "sms",
        target: changePhoneForm.new_phone,
        purpose: "change_phone",
        captcha_id: changePhoneForm.captcha_id,
        captcha_answer: changePhoneForm.captcha_answer
      }
    });
    message(t("login.changePhone.codeSent"), { type: "success" });
    startCountdown("phone", 60);
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(t("login.captcha.invalid"), { type: "error" });
    } else if (detail === "code_too_frequent") {
      message(t("login.forgetPassword.step1.tooFrequent"), { type: "warning" });
    } else {
      message(t("login.forgetPassword.step1.sendFailed"), { type: "error" });
    }
    changePhoneCaptchaRef.value?.refresh?.();
    changePhoneForm.captcha_answer = "";
  } finally {
    changePhoneCodeSending.value = false;
  }
}

async function confirmChangePhone() {
  if (!changePhoneForm.code || changePhoneForm.code.length !== 6) {
    message(t("login.forgetPassword.invalidCode"), { type: "warning" });
    return;
  }
  try {
    await http.request("post", "/api/manager/auth/me/change-phone", {
      data: {
        new_phone: changePhoneForm.new_phone,
        code: changePhoneForm.code
      }
    });
    userInfos.phone = changePhoneForm.new_phone;
    message(t("login.changePhone.success"), { type: "success" });
    changePhoneVisible.value = false;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "phone_in_use") {
      message(t("login.changePhone.inUse"), { type: "error" });
    } else if (detail === "invalid_code") {
      message(t("login.changePhone.invalidCode"), { type: "error" });
    } else {
      message(t("login.changePhone.failed"), { type: "error" });
    }
  }
}

// =========================================
// 认证邮箱 / 手机（0.8.111+）
// 流程：点击「认证」→ 图形验证码 → 发码到当前 email/phone → 输入 code → 确认
// 后端 endpoint：/me/verify-email /me/verify-phone（需 Bearer token）
// 发码复用公开 /verification-code/send（已要求图形验证码防滥用）
// =========================================

const verifyEmailVisible = ref(false);
const verifyEmailForm = reactive({
  captcha_id: "",
  captcha_answer: "",
  code: ""
});
const verifyEmailCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const verifyEmailCodeSending = ref(false);
const verifyEmailCodeCountdown = ref(0);
let verifyEmailTimer: ReturnType<typeof setInterval> | null = null;

const verifyPhoneVisible = ref(false);
const verifyPhoneForm = reactive({
  captcha_id: "",
  captcha_answer: "",
  code: ""
});
const verifyPhoneCaptchaRef = ref<InstanceType<typeof ReImageVerify> | null>(null);
const verifyPhoneCodeSending = ref(false);
const verifyPhoneCodeCountdown = ref(0);
let verifyPhoneTimer: ReturnType<typeof setInterval> | null = null;

function startVerifyCountdown(kind: "email" | "phone", seconds = 60) {
  const counter = kind === "email" ? verifyEmailCodeCountdown : verifyPhoneCodeCountdown;
  const existing = kind === "email" ? verifyEmailTimer : verifyPhoneTimer;
  if (existing) clearInterval(existing);
  counter.value = seconds;
  const handle = setInterval(() => {
    counter.value -= 1;
    if (counter.value <= 0) {
      clearInterval(handle);
      counter.value = 0;
    }
  }, 1000);
  if (kind === "email") verifyEmailTimer = handle;
  else verifyPhoneTimer = handle;
}

function openVerifyEmailDialog() {
  verifyEmailForm.captcha_id = "";
  verifyEmailForm.captcha_answer = "";
  verifyEmailForm.code = "";
  verifyEmailCodeCountdown.value = 0;
  verifyEmailVisible.value = true;
  setTimeout(() => verifyEmailCaptchaRef.value?.refresh?.(), 50);
}

async function sendVerifyEmailCode() {
  if (!userInfos.email) {
    message(t("account.profile.emailPlaceholder"), { type: "warning" });
    return;
  }
  if (!verifyEmailForm.captcha_answer) {
    message(t("login.captcha.placeholder"), { type: "warning" });
    return;
  }
  verifyEmailCodeSending.value = true;
  try {
    await http.request("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: "email",
        target: userInfos.email,
        purpose: "verify_email",
        captcha_id: verifyEmailForm.captcha_id,
        captcha_answer: verifyEmailForm.captcha_answer
      }
    });
    message(t("login.verifyEmail.codeSent"), { type: "success" });
    startVerifyCountdown("email", 60);
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(t("login.captcha.invalid"), { type: "error" });
    } else if (detail === "code_too_frequent") {
      message(t("login.forgetPassword.step1.tooFrequent"), { type: "warning" });
    } else {
      message(t("login.verifyEmail.failed"), { type: "error" });
    }
    verifyEmailCaptchaRef.value?.refresh?.();
    verifyEmailForm.captcha_answer = "";
  } finally {
    verifyEmailCodeSending.value = false;
  }
}

async function confirmVerifyEmail() {
  if (!verifyEmailForm.code || verifyEmailForm.code.length !== 6) {
    message(t("login.forgetPassword.invalidCode"), { type: "warning" });
    return;
  }
  try {
    await http.request("post", "/api/manager/auth/me/verify-email", {
      data: { code: verifyEmailForm.code }
    });
    userInfos.email_verified = true;
    message(t("login.verifyEmail.success"), { type: "success" });
    verifyEmailVisible.value = false;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "invalid_code") {
      message(t("login.verifyEmail.invalidCode"), { type: "error" });
    } else if (detail === "email_already_verified") {
      message(t("login.verifyEmail.alreadyVerified"), { type: "error" });
    } else if (detail === "user_no_email") {
      message(t("account.profile.emailPlaceholder"), { type: "warning" });
    } else {
      message(t("login.verifyEmail.failed"), { type: "error" });
    }
  }
}

function openVerifyPhoneDialog() {
  verifyPhoneForm.captcha_id = "";
  verifyPhoneForm.captcha_answer = "";
  verifyPhoneForm.code = "";
  verifyPhoneCodeCountdown.value = 0;
  verifyPhoneVisible.value = true;
  setTimeout(() => verifyPhoneCaptchaRef.value?.refresh?.(), 50);
}

async function sendVerifyPhoneCode() {
  if (!userInfos.phone) {
    message(t("account.profile.phonePlaceholder"), { type: "warning" });
    return;
  }
  if (!verifyPhoneForm.captcha_answer) {
    message(t("login.captcha.placeholder"), { type: "warning" });
    return;
  }
  verifyPhoneCodeSending.value = true;
  try {
    await http.request("post", "/api/manager/auth/verification-code/send", {
      data: {
        channel: "sms",
        target: userInfos.phone,
        purpose: "verify_phone",
        captcha_id: verifyPhoneForm.captcha_id,
        captcha_answer: verifyPhoneForm.captcha_answer
      }
    });
    message(t("login.verifyPhone.codeSent"), { type: "success" });
    startVerifyCountdown("phone", 60);
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "captcha_invalid") {
      message(t("login.captcha.invalid"), { type: "error" });
    } else if (detail === "code_too_frequent") {
      message(t("login.forgetPassword.step1.tooFrequent"), { type: "warning" });
    } else {
      message(t("login.verifyPhone.failed"), { type: "error" });
    }
    verifyPhoneCaptchaRef.value?.refresh?.();
    verifyPhoneForm.captcha_answer = "";
  } finally {
    verifyPhoneCodeSending.value = false;
  }
}

async function confirmVerifyPhone() {
  if (!verifyPhoneForm.code || verifyPhoneForm.code.length !== 6) {
    message(t("login.forgetPassword.invalidCode"), { type: "warning" });
    return;
  }
  try {
    await http.request("post", "/api/manager/auth/me/verify-phone", {
      data: { code: verifyPhoneForm.code }
    });
    userInfos.phone_verified = true;
    message(t("login.verifyPhone.success"), { type: "success" });
    verifyPhoneVisible.value = false;
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    if (detail === "invalid_code") {
      message(t("login.verifyPhone.invalidCode"), { type: "error" });
    } else if (detail === "phone_already_verified") {
      message(t("login.verifyPhone.alreadyVerified"), { type: "error" });
    } else if (detail === "user_no_phone") {
      message(t("account.profile.phonePlaceholder"), { type: "warning" });
    } else {
      message(t("login.verifyPhone.failed"), { type: "error" });
    }
  }
}

// 选择预置头像
const presetVisible = ref(false);
const presetList = ref<string[]>([]);
const selectedPreset = ref("");

const openPresetDialog = async () => {
  try {
    const { data } = await getPresetAvatars();
    presetList.value = data.items;
    selectedPreset.value = userInfos.avatar_url || "";
    presetVisible.value = true;
  } catch (error) {
    message(t("account.profile.msg.submitError", { error }), { type: "error" });
  }
};

const submitPreset = async () => {
  if (!selectedPreset.value) return;
  try {
    const { code, data } = await updateMe({ avatar_url: selectedPreset.value });
    if (code === 0) {
      userInfos.avatar_url = data.avatar_url;
      userInfos.avatar = data.avatar_url;
      syncAvatarEverywhere(data.avatar_url);
      message(t("account.profile.msg.avatarOk"), { type: "success" });
      presetVisible.value = false;
    }
  } catch (error) {
    message(t("account.profile.msg.submitError", { error }), { type: "error" });
  }
};
</script>

<template>
  <div :class="['min-w-45', deviceDetection() ? 'max-w-full' : 'max-w-[70%]']">
    <h3 class="my-8!">{{ t("account.profile.title") }}</h3>
    <el-form
      ref="userInfoFormRef"
      label-position="top"
      :rules="rules"
      :model="userInfos"
    >
      <el-form-item :label="t('account.profile.avatar')">
        <el-avatar :size="80" :src="userInfos.avatar" />
        <el-upload
          ref="uploadRef"
          accept="image/*"
          action="#"
          :limit="1"
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onChange"
        >
          <el-button plain class="ml-4!">
            <IconifyIconOffline :icon="uploadLine" />
            <span class="ml-2">{{ t("account.profile.updateAvatar") }}</span>
          </el-button>
        </el-upload>
        <el-button plain class="ml-2" @click="openPresetDialog">
          <IconifyIconOffline :icon="galleryLine" />
          <span class="ml-2">{{ t("account.profile.presetAvatar") }}</span>
        </el-button>
      </el-form-item>
      <el-form-item :label="t('account.profile.roles')">
        <div v-if="roles.length" class="flex flex-wrap gap-2">
          <el-tag
            v-for="role in roles"
            :key="role"
            :type="roleTagType(role)"
            effect="light"
          >
            {{ role }}
          </el-tag>
        </div>
        <span v-else class="text-gray-400">—</span>
      </el-form-item>
      <el-form-item :label="t('account.profile.nickname')" prop="nickname">
        <el-input v-model="userInfos.nickname" :placeholder="t('account.profile.nicknamePlaceholder')" />
      </el-form-item>
      <el-form-item :label="t('account.profile.email')">
        <el-input
          :model-value="userInfos.email"
          :placeholder="t('account.profile.emailPlaceholder')"
          readonly
        />
        <el-button plain class="ml-2" @click="openChangeEmailDialog">
          <IconifyIconOffline :icon="editLine" />
          <span class="ml-2">{{ t("login.changeEmail.title") }}</span>
        </el-button>
        <el-button
          v-if="userInfos.email && !userInfos.email_verified"
          type="success"
          plain
          class="ml-2"
          @click="openVerifyEmailDialog"
        >
          <IconifyIconOffline :icon="verifyBadgeLine" />
          <span class="ml-2">{{ t("login.verifyEmail.title") }}</span>
        </el-button>
      </el-form-item>
      <el-form-item :label="t('account.profile.phone')">
        <el-input
          :model-value="userInfos.phone"
          :placeholder="t('account.profile.phonePlaceholder')"
          readonly
        />
        <el-button plain class="ml-2" @click="openChangePhoneDialog">
          <IconifyIconOffline :icon="editLine" />
          <span class="ml-2">{{ t("login.changePhone.title") }}</span>
        </el-button>
        <el-button
          v-if="userInfos.phone && !userInfos.phone_verified"
          type="success"
          plain
          class="ml-2"
          @click="openVerifyPhoneDialog"
        >
          <IconifyIconOffline :icon="verifyBadgeLine" />
          <span class="ml-2">{{ t("login.verifyPhone.title") }}</span>
        </el-button>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSubmit(userInfoFormRef)">
          {{ t("account.profile.update") }}
        </el-button>
        <el-button @click="openPwdDialog">
          {{ t("account.profile.changePassword") }}
        </el-button>
      </el-form-item>
    </el-form>
    <el-dialog
      v-model="isShow"
      width="40%"
      :title="t('account.profile.editAvatarTitle')"
      destroy-on-close
      :closeOnClickModal="false"
      :before-close="handleClose"
      :fullscreen="deviceDetection()"
    >
      <ReCropperPreview ref="cropRef" :imgSrc="imgSrc" @cropper="onCropper" />
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="handleClose">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="handleSubmitImage">
            {{ t("common.action.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="pwdVisible"
      width="40%"
      :title="t('account.profile.pwdDialog.title')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <el-form
        ref="pwdFormRef"
        label-position="top"
        :rules="pwdRules"
        :model="pwdForm"
      >
        <el-form-item :label="t('account.profile.pwdDialog.old')" prop="old_password">
          <el-input
            v-model="pwdForm.old_password"
            type="password"
            show-password
            :placeholder="t('account.profile.pwdDialog.oldPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('account.profile.pwdDialog.new')" prop="new_password">
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            show-password
            :placeholder="t('account.profile.pwdDialog.newPlaceholder')"
          />
        </el-form-item>
        <div v-if="pwdForm.new_password" class="mb-4 flex">
          <div
            v-for="(item, idx) in pwdProgress"
            :key="idx"
            class="flex-1"
            :style="{ marginLeft: idx !== 0 ? '4px' : 0 }"
          >
            <el-progress
              striped
              striped-flow
              :duration="curScore === idx ? 6 : 0"
              :percentage="curScore >= idx ? 100 : 0"
              :color="item.color"
              :stroke-width="10"
              :show-text="false"
            />
            <p
              class="text-center"
              :style="{ color: curScore === idx ? item.color : '' }"
            >
              {{ item.text }}
            </p>
          </div>
        </div>
        <el-form-item :label="t('account.profile.pwdDialog.confirm')" prop="confirm">
          <el-input
            v-model="pwdForm.confirm"
            type="password"
            show-password
            :placeholder="t('account.profile.pwdDialog.confirmPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="pwdVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="onPwdSubmit(pwdFormRef)">
            {{ t("account.profile.pwdDialog.submit") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="presetVisible"
      width="40%"
      :title="t('account.profile.presetAvatarTitle')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <div class="grid grid-cols-4 gap-3">
        <div
          v-for="path in presetList"
          :key="path"
          class="flex flex-col items-center cursor-pointer rounded p-2"
          :class="selectedPreset === path ? 'ring-2 ring-[var(--el-color-primary)]' : 'hover:bg-[var(--el-fill-color-light)]'"
          @click="selectedPreset = path"
        >
          <el-avatar :size="64" :src="path" />
        </div>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="presetVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="submitPreset">
            {{ t("common.action.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="changeEmailVisible"
      width="40%"
      :title="t('login.changeEmail.title')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <el-form label-position="top" :model="changeEmailForm">
        <el-form-item :label="t('login.changeEmail.newEmail')">
          <el-input
            v-model="changeEmailForm.new_email"
            clearable
            :placeholder="t('account.profile.emailPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('login.captcha.label')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="changeEmailForm.captcha_answer"
              clearable
              :placeholder="t('login.captcha.placeholder')"
              style="flex: 1"
            />
            <ReImageVerify
              ref="changeEmailCaptchaRef"
              v-model:captcha-id="changeEmailForm.captcha_id"
              class="ml-2!"
            />
          </div>
        </el-form-item>
        <el-form-item :label="t('login.changeEmail.code')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="changeEmailForm.code"
              clearable
              maxlength="6"
              :placeholder="t('login.forgetPassword.step2.codePlaceholder')"
              style="flex: 1"
            />
            <el-button
              class="ml-2!"
              :disabled="changeEmailCodeCountdown > 0"
              :loading="changeEmailCodeSending"
              @click="sendChangeEmailCode"
            >
              {{
                changeEmailCodeCountdown > 0
                  ? `${changeEmailCodeCountdown}s`
                  : t("login.changeEmail.sendCode")
              }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="changeEmailVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="confirmChangeEmail">
            {{ t("login.changeEmail.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="changePhoneVisible"
      width="40%"
      :title="t('login.changePhone.title')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <el-form label-position="top" :model="changePhoneForm">
        <el-form-item :label="t('login.changePhone.newPhone')">
          <el-input
            v-model="changePhoneForm.new_phone"
            clearable
            :placeholder="t('account.profile.phonePlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('login.captcha.label')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="changePhoneForm.captcha_answer"
              clearable
              :placeholder="t('login.captcha.placeholder')"
              style="flex: 1"
            />
            <ReImageVerify
              ref="changePhoneCaptchaRef"
              v-model:captcha-id="changePhoneForm.captcha_id"
              class="ml-2!"
            />
          </div>
        </el-form-item>
        <el-form-item :label="t('login.changePhone.code')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="changePhoneForm.code"
              clearable
              maxlength="6"
              :placeholder="t('login.forgetPassword.step2.codePlaceholder')"
              style="flex: 1"
            />
            <el-button
              class="ml-2!"
              :disabled="changePhoneCodeCountdown > 0"
              :loading="changePhoneCodeSending"
              @click="sendChangePhoneCode"
            >
              {{
                changePhoneCodeCountdown > 0
                  ? `${changePhoneCodeCountdown}s`
                  : t("login.changePhone.sendCode")
              }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="changePhoneVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="confirmChangePhone">
            {{ t("login.changePhone.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="verifyEmailVisible"
      width="40%"
      :title="t('login.verifyEmail.title')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <el-form label-position="top" :model="verifyEmailForm">
        <el-form-item :label="t('login.captcha.label')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="verifyEmailForm.captcha_answer"
              clearable
              :placeholder="t('login.captcha.placeholder')"
              style="flex: 1"
            />
            <ReImageVerify
              ref="verifyEmailCaptchaRef"
              v-model:captcha-id="verifyEmailForm.captcha_id"
              class="ml-2!"
            />
          </div>
        </el-form-item>
        <el-form-item :label="t('login.verifyEmail.code')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="verifyEmailForm.code"
              clearable
              maxlength="6"
              :placeholder="t('login.forgetPassword.step2.codePlaceholder')"
              style="flex: 1"
            />
            <el-button
              class="ml-2!"
              :disabled="verifyEmailCodeCountdown > 0"
              :loading="verifyEmailCodeSending"
              @click="sendVerifyEmailCode"
            >
              {{
                verifyEmailCodeCountdown > 0
                  ? `${verifyEmailCodeCountdown}s`
                  : t("login.verifyEmail.sendCode")
              }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="verifyEmailVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="confirmVerifyEmail">
            {{ t("login.verifyEmail.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
    <el-dialog
      v-model="verifyPhoneVisible"
      width="40%"
      :title="t('login.verifyPhone.title')"
      destroy-on-close
      :closeOnClickModal="false"
      :fullscreen="deviceDetection()"
    >
      <el-form label-position="top" :model="verifyPhoneForm">
        <el-form-item :label="t('login.captcha.label')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="verifyPhoneForm.captcha_answer"
              clearable
              :placeholder="t('login.captcha.placeholder')"
              style="flex: 1"
            />
            <ReImageVerify
              ref="verifyPhoneCaptchaRef"
              v-model:captcha-id="verifyPhoneForm.captcha_id"
              class="ml-2!"
            />
          </div>
        </el-form-item>
        <el-form-item :label="t('login.verifyPhone.code')">
          <div class="w-full flex justify-between">
            <el-input
              v-model="verifyPhoneForm.code"
              clearable
              maxlength="6"
              :placeholder="t('login.forgetPassword.step2.codePlaceholder')"
              style="flex: 1"
            />
            <el-button
              class="ml-2!"
              :disabled="verifyPhoneCodeCountdown > 0"
              :loading="verifyPhoneCodeSending"
              @click="sendVerifyPhoneCode"
            >
              {{
                verifyPhoneCodeCountdown > 0
                  ? `${verifyPhoneCodeCountdown}s`
                  : t("login.verifyPhone.sendCode")
              }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button bg text @click="verifyPhoneVisible = false">{{ t("common.action.cancel") }}</el-button>
          <el-button bg text type="primary" @click="confirmVerifyPhone">
            {{ t("login.verifyPhone.confirm") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>
