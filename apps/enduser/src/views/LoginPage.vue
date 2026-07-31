<template>
  <div class="login-page">
    <div class="login-card">
      <!-- Brand -->
      <div class="login-brand">
        <div class="login-logo">
          <img src="/favicon.svg" width="52" height="52" alt="UnionAgents" />
          <span class="login-dot">·</span>
          <span class="login-brand-name">UnionAgents</span>
        </div>
        <p class="login-subtitle">企业级智能体平台</p>
      </div>

      <!-- 登录视图 -->
      <template v-if="view === 'login'">
        <div class="login-tabs">
          <button
            v-for="t in tabs"
            :key="t.value"
            class="login-tab"
            :class="{ 'login-tab-active': activeTab === t.value }"
            type="button"
            @click="switchTab(t.value)"
          >{{ t.label }}</button>
        </div>

        <form @submit.prevent="handleLogin" class="login-form">
          <!-- 账号 tab -->
          <template v-if="activeTab === 'account'">
            <div class="login-field">
              <label class="login-label">用户名</label>
              <input
                v-model="accountForm.username"
                type="text"
                required
                placeholder="请输入用户名"
                class="login-input"
                :disabled="loading"
              />
            </div>
            <div class="login-field">
              <label class="login-label">密码</label>
              <input
                v-model="accountForm.password"
                type="password"
                required
                placeholder="请输入密码"
                class="login-input"
                :disabled="loading"
              />
            </div>
          </template>

          <!-- 邮箱 tab -->
          <template v-else-if="activeTab === 'email'">
            <div class="login-field">
              <label class="login-label">邮箱</label>
              <input
                v-model="emailForm.email"
                type="email"
                required
                placeholder="请输入邮箱"
                class="login-input"
                :disabled="loading"
              />
            </div>
            <div class="login-field">
              <label class="login-label">密码</label>
              <input
                v-model="emailForm.password"
                type="password"
                required
                placeholder="请输入密码"
                class="login-input"
                :disabled="loading"
              />
            </div>
          </template>

          <!-- 手机 tab -->
          <template v-else-if="activeTab === 'phone'">
            <div class="login-field">
              <label class="login-label">手机号</label>
              <input
                v-model="phoneForm.phone"
                type="tel"
                required
                placeholder="请输入手机号"
                class="login-input"
                :disabled="loading"
              />
            </div>
            <div class="login-field">
              <label class="login-label">密码</label>
              <input
                v-model="phoneForm.password"
                type="password"
                required
                placeholder="请输入密码"
                class="login-input"
                :disabled="loading"
              />
            </div>
          </template>

          <!-- 短信验证码 tab -->
          <template v-else>
            <div class="login-field">
              <label class="login-label">手机号</label>
              <input
                v-model="smsCodeForm.phone"
                type="tel"
                required
                placeholder="请输入手机号"
                class="login-input"
                :disabled="loading"
              />
            </div>
            <div class="login-field">
              <label class="login-label">图形验证码</label>
              <div class="login-captcha-row">
                <input
                  v-model="smsCodeForm.captchaAnswer"
                  type="text"
                  required
                  maxlength="4"
                  inputmode="numeric"
                  placeholder="请输入图中数字"
                  class="login-input login-captcha-input"
                  :disabled="loading"
                />
                <img
                  v-if="smsCaptcha.img"
                  :src="smsCaptcha.img"
                  width="120"
                  height="40"
                  class="login-captcha-img"
                  :title="smsCaptcha.loading ? '加载中...' : '点击刷新'"
                  alt="图形验证码"
                  @click="refreshCaptcha('sms')"
                />
                <div
                  v-else
                  class="login-captcha-placeholder"
                  @click="refreshCaptcha('sms')"
                >
                  {{ smsCaptcha.loading ? "加载中..." : "点击获取" }}
                </div>
              </div>
            </div>
            <div class="login-field">
              <label class="login-label">短信验证码</label>
              <div class="login-code-row">
                <input
                  v-model="smsCodeForm.code"
                  type="text"
                  required
                  maxlength="6"
                  inputmode="numeric"
                  placeholder="请输入 6 位验证码"
                  class="login-input login-code-input"
                  :disabled="loading"
                />
                <button
                  type="button"
                  class="login-code-btn"
                  :disabled="loading || smsCountdown > 0"
                  @click="onSendSmsCode"
                >{{ smsCountdown > 0 ? `${smsCountdown}s` : "发送验证码" }}</button>
              </div>
            </div>
          </template>

          <!-- 条件触发的 captcha（账号/邮箱/手机 tab） -->
          <div
            v-if="showCaptchaForCurrentTab() && activeTab !== 'smsCode'"
            class="login-field"
          >
            <label class="login-label">图形验证码</label>
            <div class="login-captcha-row">
              <input
                :value="currentCaptchaAnswer()"
                @input="setCurrentCaptchaAnswer(($event.target as HTMLInputElement).value)"
                type="text"
                required
                maxlength="4"
                inputmode="numeric"
                placeholder="请输入图中数字"
                class="login-input login-captcha-input"
                :disabled="loading"
              />
              <img
                v-if="currentCaptchaImg()"
                :src="currentCaptchaImg()"
                width="120"
                height="40"
                class="login-captcha-img"
                title="点击刷新"
                alt="图形验证码"
                @click="refreshCaptcha(activeTab === 'account' ? 'account' : activeTab === 'email' ? 'email' : 'phone')"
              />
              <div
                v-else
                class="login-captcha-placeholder"
                @click="refreshCaptcha(activeTab === 'account' ? 'account' : activeTab === 'email' ? 'email' : 'phone')"
              >点击获取</div>
            </div>
          </div>

          <div v-if="error" class="login-error">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {{ error }}
          </div>

          <button type="submit" :disabled="loading" class="login-btn">
            <span v-if="loading" class="login-btn-spinner"></span>
            <span>{{ loading ? "登录中..." : "登 录" }}</span>
          </button>

          <div class="login-forget-link">
            <button type="button" class="login-link-btn" @click="goForget">忘记密码？</button>
          </div>
        </form>
      </template>

      <!-- 忘记密码视图 -->
      <form v-else class="login-form" @submit.prevent>
        <div class="login-forget-title">重置密码</div>

        <template v-if="!hasAnyChannel">
          <div class="login-error">系统未开启邮箱/短信验证渠道，请联系管理员</div>
        </template>

        <!-- Step 1: 选渠道 + 输入 target + captcha + 发码 -->
        <template v-else-if="forgetStep === 1">
          <div class="login-field">
            <label class="login-label">验证渠道</label>
            <div class="login-channel-row">
              <button
                v-if="channels.email"
                type="button"
                class="login-tab"
                :class="{ 'login-tab-active': forgetForm.channel === 'email' }"
                @click="switchForgerChannel('email')"
              >邮箱</button>
              <button
                v-if="channels.sms"
                type="button"
                class="login-tab"
                :class="{ 'login-tab-active': forgetForm.channel === 'sms' }"
                @click="switchForgerChannel('sms')"
              >短信</button>
            </div>
          </div>
          <div class="login-field">
            <label class="login-label">{{ forgetForm.channel === "email" ? "邮箱" : "手机号" }}</label>
            <input
              v-model="forgetForm.target"
              :type="forgetForm.channel === 'email' ? 'email' : 'tel'"
              required
              :placeholder="forgetForm.channel === 'email' ? '请输入邮箱' : '请输入手机号'"
              class="login-input"
              :disabled="loading"
            />
          </div>
          <div class="login-field">
            <label class="login-label">图形验证码</label>
            <div class="login-captcha-row">
              <input
                v-model="forgetForm.captchaAnswer"
                type="text"
                required
                maxlength="4"
                inputmode="numeric"
                placeholder="请输入图中数字"
                class="login-input login-captcha-input"
                :disabled="loading"
              />
              <img
                v-if="forgetCaptcha.img"
                :src="forgetCaptcha.img"
                width="120"
                height="40"
                class="login-captcha-img"
                title="点击刷新"
                alt="图形验证码"
                @click="refreshCaptcha('forget')"
              />
              <div
                v-else
                class="login-captcha-placeholder"
                @click="refreshCaptcha('forget')"
              >{{ forgetCaptcha.loading ? "加载中..." : "点击获取" }}</div>
            </div>
          </div>
          <div v-if="error" class="login-error">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {{ error }}
          </div>
          <button
            type="button"
            :disabled="loading"
            class="login-btn"
            @click="onForgetSendCode"
          >{{ loading ? "发送中..." : "发送验证码" }}</button>
        </template>

        <!-- Step 2: 输入 6 位验证码 → 拿 ticket -->
        <template v-else-if="forgetStep === 2">
          <div class="login-field">
            <label class="login-label">验证码</label>
            <input
              v-model="forgetForm.code"
              type="text"
              required
              maxlength="6"
              inputmode="numeric"
              placeholder="请输入 6 位验证码"
              class="login-input"
              :disabled="loading"
            />
          </div>
          <div v-if="error" class="login-error">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {{ error }}
          </div>
          <button
            type="button"
            :disabled="loading"
            class="login-btn"
            @click="onForgetVerifyCode"
          >{{ loading ? "校验中..." : "校验验证码" }}</button>
        </template>

        <!-- Step 3: 新密码 + 确认 → 重置 -->
        <template v-else>
          <div class="login-field">
            <label class="login-label">新密码</label>
            <input
              v-model="forgetForm.newPassword"
              type="password"
              required
              placeholder="请输入新密码"
              class="login-input"
              :disabled="loading"
            />
          </div>
          <div class="login-field">
            <label class="login-label">确认密码</label>
            <input
              v-model="forgetForm.confirmPassword"
              type="password"
              required
              placeholder="请再次输入新密码"
              class="login-input"
              :disabled="loading"
            />
          </div>
          <div v-if="error" class="login-error">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {{ error }}
          </div>
          <button
            type="button"
            :disabled="loading"
            class="login-btn"
            @click="onForgetReset"
          >{{ loading ? "重置中..." : "重置密码" }}</button>
        </template>

        <button type="button" class="login-link-btn login-back-btn" @click="backToLogin">返回登录</button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue"
import { useRouter, useRoute } from "vue-router"
import { useAuthStore } from "@/stores/auth"
import {
  getCaptcha,
  getVerificationChannels,
  sendVerificationCode,
  verifyVerificationCode,
  resetPassword,
  type VerificationChannels,
} from "@/api/endpoints"
import { ApiError } from "@/api/client"

type LoginTab = "account" | "email" | "phone" | "smsCode"
type ForgetStep = 1 | 2 | 3
type CaptchaTarget = "account" | "email" | "phone" | "sms" | "forget"

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const view = ref<"login" | "forget">("login")
const activeTab = ref<LoginTab>("account")
const channels = ref<VerificationChannels>({ email: false, sms: false })

const tabs = computed(() => {
  const list: { label: string; value: LoginTab }[] = [
    { label: "账号", value: "account" },
    { label: "邮箱", value: "email" },
    { label: "手机", value: "phone" },
  ]
  if (channels.value.sms) list.push({ label: "验证码", value: "smsCode" })
  return list
})

const accountForm = reactive({
  username: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false,
})
const emailForm = reactive({
  email: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false,
})
const phoneForm = reactive({
  phone: "",
  password: "",
  captchaId: "",
  captchaAnswer: "",
  captchaRequired: false,
})
const smsCodeForm = reactive({
  phone: "",
  captchaId: "",
  captchaAnswer: "",
  code: "",
})

const accountCaptcha = ref({ img: "", loading: false })
const emailCaptcha = ref({ img: "", loading: false })
const phoneCaptcha = ref({ img: "", loading: false })
const smsCaptcha = ref({ img: "", loading: false })
const forgetCaptcha = ref({ img: "", loading: false })

const loading = ref(false)
const error = ref("")

const smsCountdown = ref(0)
let smsTimer: number | null = null
function startSmsCountdown() {
  smsCountdown.value = 60
  if (smsTimer) window.clearInterval(smsTimer)
  smsTimer = window.setInterval(() => {
    if (smsCountdown.value > 0) smsCountdown.value--
    else if (smsTimer) {
      window.clearInterval(smsTimer)
      smsTimer = null
    }
  }, 1000)
}

const forgetForm = reactive({
  channel: "email" as "email" | "sms",
  target: "",
  captchaId: "",
  captchaAnswer: "",
  code: "",
  ticket: "",
  newPassword: "",
  confirmPassword: "",
})
const forgetStep = ref<ForgetStep>(1)
const hasAnyChannel = computed(() => channels.value.email || channels.value.sms)

async function refreshCaptcha(target: CaptchaTarget) {
  const store =
    target === "account" ? accountCaptcha
    : target === "email" ? emailCaptcha
    : target === "phone" ? phoneCaptcha
    : target === "sms" ? smsCaptcha
    : forgetCaptcha
  store.value.loading = true
  try {
    const c = await getCaptcha()
    if (target === "account") accountForm.captchaId = c.captcha_id
    else if (target === "email") emailForm.captchaId = c.captcha_id
    else if (target === "phone") phoneForm.captchaId = c.captcha_id
    else if (target === "sms") smsCodeForm.captchaId = c.captcha_id
    else forgetForm.captchaId = c.captcha_id
    store.value.img = c.image_base64
  } catch {
    // 静默失败，不影响登录主流程；用户点击图片可重试
  } finally {
    store.value.loading = false
  }
}

function switchTab(tab: LoginTab) {
  activeTab.value = tab
  error.value = ""
  if (tab === "smsCode" && !smsCaptcha.value.img) refreshCaptcha("sms")
}

function showCaptchaForCurrentTab(): boolean {
  if (activeTab.value === "account") return accountForm.captchaRequired
  if (activeTab.value === "email") return emailForm.captchaRequired
  if (activeTab.value === "phone") return phoneForm.captchaRequired
  if (activeTab.value === "smsCode") return true
  return false
}

function currentCaptchaImg(): string {
  if (activeTab.value === "account") return accountCaptcha.value.img
  if (activeTab.value === "email") return emailCaptcha.value.img
  if (activeTab.value === "phone") return phoneCaptcha.value.img
  return smsCaptcha.value.img
}

function currentCaptchaAnswer(): string {
  if (activeTab.value === "account") return accountForm.captchaAnswer
  if (activeTab.value === "email") return emailForm.captchaAnswer
  if (activeTab.value === "phone") return phoneForm.captchaAnswer
  return smsCodeForm.captchaAnswer
}

function setCurrentCaptchaAnswer(v: string) {
  if (activeTab.value === "account") accountForm.captchaAnswer = v
  else if (activeTab.value === "email") emailForm.captchaAnswer = v
  else if (activeTab.value === "phone") phoneForm.captchaAnswer = v
  else smsCodeForm.captchaAnswer = v
}

async function onSendSmsCode() {
  error.value = ""
  if (!smsCodeForm.phone) {
    error.value = "请输入手机号"
    return
  }
  if (!smsCodeForm.captchaAnswer) {
    error.value = "请输入图形验证码"
    return
  }
  loading.value = true
  try {
    await sendVerificationCode({
      channel: "sms",
      target: smsCodeForm.phone,
      purpose: "login",
      captcha_id: smsCodeForm.captchaId,
      captcha_answer: smsCodeForm.captchaAnswer,
    })
    startSmsCountdown()
  } catch (e: unknown) {
    handleApiError(e, "sms")
  } finally {
    loading.value = false
  }
}

async function handleLogin() {
  error.value = ""
  loading.value = true
  try {
    if (activeTab.value === "account") {
      await auth.login(
        accountForm.username,
        accountForm.password,
        accountForm.captchaRequired ? accountForm.captchaId : "",
        accountForm.captchaRequired ? accountForm.captchaAnswer : "",
      )
    } else if (activeTab.value === "email") {
      await auth.loginByContact(
        "email",
        emailForm.email,
        emailForm.password,
        emailForm.captchaId,
        emailForm.captchaAnswer,
      )
    } else if (activeTab.value === "phone") {
      await auth.loginByContact(
        "phone",
        phoneForm.phone,
        phoneForm.password,
        phoneForm.captchaId,
        phoneForm.captchaAnswer,
      )
    } else {
      await auth.loginBySmsCode(smsCodeForm.phone, smsCodeForm.code)
    }
    const redirect = (route.query.redirect as string) || "/agents"
    router.push(redirect)
  } catch (e: unknown) {
    handleApiError(e)
  } finally {
    loading.value = false
  }
}

function handleApiError(
  e: unknown,
  captchaTarget: CaptchaTarget = activeTab.value as CaptchaTarget,
) {
  if (e instanceof ApiError && e.status === 423 && e.retryAfter) {
    const minutes = Math.max(1, Math.ceil(e.retryAfter / 60))
    error.value = `账号已锁定，请 ${minutes} 分钟后重试`
    return
  }
  if (e instanceof ApiError && e.message === "captcha_required") {
    if (activeTab.value === "account") accountForm.captchaRequired = true
    else if (activeTab.value === "email") emailForm.captchaRequired = true
    else if (activeTab.value === "phone") phoneForm.captchaRequired = true
    error.value = "账号存在异常登录尝试，请完成图形验证码后重试"
    refreshCaptcha(captchaTarget)
    return
  }
  if (e instanceof ApiError && e.message === "captcha_invalid") {
    error.value = "验证码错误或已过期，请重新输入"
    refreshCaptcha(captchaTarget)
    return
  }
  if (e instanceof ApiError && e.message === "invalid_code") {
    error.value = "短信验证码错误或已过期"
    return
  }
  if (e instanceof ApiError && e.message === "code_too_frequent") {
    error.value = "验证码发送过于频繁，请稍后再试"
    return
  }
  if (e instanceof ApiError && e.message === "ip_code_banned") {
    error.value = "验证码请求受限，请稍后再试"
    return
  }
  if (e instanceof ApiError && e.message === "no_active_provider") {
    error.value = "暂未开启该验证渠道"
    return
  }
  if (e instanceof Error) error.value = e.message || "登录失败，请重试"
  else error.value = "登录失败，请重试"
}

function goForget() {
  view.value = "forget"
  forgetStep.value = 1
  error.value = ""
  if (hasAnyChannel.value) {
    forgetForm.channel = channels.value.email ? "email" : "sms"
    refreshCaptcha("forget")
  }
}

function backToLogin() {
  view.value = "login"
  forgetStep.value = 1
  forgetForm.target = ""
  forgetForm.code = ""
  forgetForm.ticket = ""
  forgetForm.newPassword = ""
  forgetForm.confirmPassword = ""
  forgetForm.captchaAnswer = ""
  error.value = ""
}

function switchForgerChannel(c: "email" | "sms") {
  forgetForm.channel = c
  forgetForm.target = ""
  refreshCaptcha("forget")
}

async function onForgetSendCode() {
  error.value = ""
  if (!forgetForm.target) {
    error.value = forgetForm.channel === "email" ? "请输入邮箱" : "请输入手机号"
    return
  }
  if (!forgetForm.captchaAnswer) {
    error.value = "请输入图形验证码"
    return
  }
  loading.value = true
  try {
    await sendVerificationCode({
      channel: forgetForm.channel,
      target: forgetForm.target,
      purpose: "reset_password",
      captcha_id: forgetForm.captchaId,
      captcha_answer: forgetForm.captchaAnswer,
    })
    forgetStep.value = 2
  } catch (e: unknown) {
    if (e instanceof ApiError && e.message === "captcha_invalid") {
      error.value = "图形验证码错误或已过期"
      refreshCaptcha("forget")
    } else if (e instanceof ApiError && e.message === "code_too_frequent") {
      error.value = "验证码发送过于频繁，请稍后再试"
    } else if (e instanceof ApiError && e.message === "ip_code_banned") {
      error.value = "验证码请求受限，请稍后再试"
    } else if (e instanceof ApiError && e.message === "no_active_provider") {
      error.value = "暂未开启该验证渠道"
    } else if (e instanceof Error) {
      error.value = e.message || "验证码发送失败"
    }
  } finally {
    loading.value = false
  }
}

async function onForgetVerifyCode() {
  error.value = ""
  if (!forgetForm.code || forgetForm.code.length !== 6) {
    error.value = "请输入 6 位验证码"
    return
  }
  loading.value = true
  try {
    const res = await verifyVerificationCode({
      channel: forgetForm.channel,
      target: forgetForm.target,
      purpose: "reset_password",
      code: forgetForm.code,
    })
    forgetForm.ticket = res.ticket
    forgetStep.value = 3
  } catch (e: unknown) {
    if (e instanceof ApiError && e.message === "invalid_code") {
      error.value = "验证码错误或已过期"
    } else if (e instanceof Error) {
      error.value = e.message || "验证失败"
    }
  } finally {
    loading.value = false
  }
}

async function onForgetReset() {
  error.value = ""
  if (!forgetForm.newPassword) {
    error.value = "请输入新密码"
    return
  }
  if (forgetForm.newPassword !== forgetForm.confirmPassword) {
    error.value = "两次密码不一致"
    return
  }
  loading.value = true
  try {
    await resetPassword(forgetForm.ticket, forgetForm.newPassword)
    backToLogin()
    error.value = "密码已重置，请使用新密码登录"
  } catch (e: unknown) {
    if (e instanceof ApiError && e.message === "ticket_invalid") {
      error.value = "重置凭证已失效，请重新发起"
    } else if (e instanceof Error) {
      error.value = e.message || "重置失败"
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    channels.value = await getVerificationChannels()
  } catch {
    channels.value = { email: false, sms: false }
  }
})
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  padding: 0 16px;
}
.login-card {
  width: 100%;
  max-width: 360px;
}
.login-brand {
  text-align: center;
  margin-bottom: 32px;
}
.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 16px;
}
.login-logo img { display: block; }
.login-dot {
  font-size: 32px;
  color: #fff;
  line-height: 1;
  font-weight: 300;
}
.login-brand-name {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -.02em;
}
.login-subtitle {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
}
.login-form {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.login-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
}
.login-input {
  width: 100%;
  padding: 8px 12px;
  background: var(--input-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 16px;
  font-family: var(--font-ui);
  outline: none;
  transition: border-color .15s, box-shadow .15s;
  box-sizing: border-box;
}
.login-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--focus-ring);
}
.login-input::placeholder { color: var(--muted); opacity: .6; }
.login-input:disabled { opacity: .5; cursor: not-allowed; }
.login-tabs {
  display: flex;
  gap: 4px;
  background: var(--input-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px;
  margin-bottom: 16px;
}
.login-tab {
  flex: 1;
  padding: 6px 12px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background .15s, color .15s;
  font-family: var(--font-ui);
}
.login-tab:hover { color: var(--text); }
.login-tab-active {
  background: var(--accent);
  color: var(--bg);
}
.login-tab-active:hover { color: var(--bg); }
.login-captcha-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.login-captcha-input {
  flex: 1;
}
.login-captcha-img,
.login-captcha-placeholder {
  cursor: pointer;
  flex-shrink: 0;
  width: 120px;
  height: 40px;
  border: 1px solid var(--border);
  border-radius: 8px;
  display: block;
}
.login-captcha-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--muted);
  background: var(--input-bg);
}
.login-captcha-img:hover,
.login-captcha-placeholder:hover {
  border-color: var(--accent);
}
.login-code-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.login-code-input {
  flex: 1;
}
.login-code-btn {
  flex-shrink: 0;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  font-family: var(--font-ui);
  transition: border-color .15s;
}
.login-code-btn:hover:not(:disabled) { border-color: var(--accent); }
.login-code-btn:disabled { opacity: .5; cursor: not-allowed; }
.login-error {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--error);
  background: rgba(239, 83, 80, .1);
  border: 1px solid rgba(239, 83, 80, .2);
  border-radius: 8px;
  padding: 8px 12px;
}
.login-error svg { flex-shrink: 0; }
.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 9px 16px;
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s;
  font-family: var(--font-ui);
}
.login-btn:hover { background: var(--accent-hover); }
.login-btn:disabled { opacity: .5; cursor: not-allowed; }
.login-btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--bg);
  border-top-color: transparent;
  border-radius: 50%;
  animation: login-spin .6s linear infinite;
}
@keyframes login-spin { to { transform: rotate(360deg); } }
.login-forget-link {
  text-align: right;
  margin-top: -4px;
}
.login-link-btn {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
  font-family: var(--font-ui);
}
.login-link-btn:hover { text-decoration: underline; }
.login-forget-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  text-align: center;
  margin-bottom: 16px;
}
.login-channel-row {
  display: flex;
  gap: 4px;
}
.login-back-btn {
  display: block;
  margin: 16px auto 0;
}
</style>
