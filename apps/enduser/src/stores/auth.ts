import { defineStore } from "pinia"
import { ref, computed } from "vue"
import {
  login as apiLogin,
  loginByContact as apiLoginByContact,
  loginBySmsCode as apiLoginBySmsCode,
  getMe,
  type UserInfo,
} from "@/api/endpoints"

interface TokenData {
  accessToken: string
  refreshToken: string
}

function saveToken(token: TokenData) {
  localStorage.setItem("ua_token", JSON.stringify(token))
}

function loadToken(): TokenData | null {
  try {
    const raw = localStorage.getItem("ua_token")
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveUser(user: UserInfo) {
  localStorage.setItem("ua_user", JSON.stringify(user))
}

function loadUser(): UserInfo | null {
  try {
    const raw = localStorage.getItem("ua_user")
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore("auth", () => {
  const token = ref<TokenData | null>(loadToken())
  const user = ref<UserInfo | null>(loadUser())
  const chatMode = ref(false)

  const isLoggedIn = computed(() => !!token.value)
  const currentUser = computed(() => user.value)

  function setChatMode(v: boolean) { chatMode.value = v }

  async function login(
    username: string,
    password: string,
    captchaId: string,
    captchaAnswer: string
  ) {
    const result = await apiLogin(username, password, captchaId, captchaAnswer)
    const tokenData: TokenData = {
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    }
    saveToken(tokenData)
    token.value = tokenData

    // Fetch user info
    const userInfo = await getMe()
    saveUser(userInfo)
    user.value = userInfo
  }

  async function loginByContact(
    contactType: "email" | "phone",
    contact: string,
    password: string,
    captchaId: string,
    captchaAnswer: string
  ) {
    const result = await apiLoginByContact({
      contact_type: contactType,
      contact,
      password,
      captcha_id: captchaId,
      captcha_answer: captchaAnswer,
    })
    const tokenData: TokenData = {
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    }
    saveToken(tokenData)
    token.value = tokenData

    const userInfo = await getMe()
    saveUser(userInfo)
    user.value = userInfo
  }

  async function loginBySmsCode(phone: string, code: string) {
    const result = await apiLoginBySmsCode(phone, code)
    const tokenData: TokenData = {
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    }
    saveToken(tokenData)
    token.value = tokenData

    const userInfo = await getMe()
    saveUser(userInfo)
    user.value = userInfo
  }

  function logout() {
    localStorage.removeItem("ua_token")
    localStorage.removeItem("ua_user")
    token.value = null
    user.value = null
  }

  async function restoreSession() {
    const saved = loadToken()
    if (!saved) return false
    token.value = saved
    try {
      const userInfo = await getMe()
      saveUser(userInfo)
      user.value = userInfo
      return true
    } catch {
      logout()
      return false
    }
  }

  return { token, user, isLoggedIn, currentUser, chatMode, login, loginByContact, loginBySmsCode, logout, restoreSession, setChatMode }
})
