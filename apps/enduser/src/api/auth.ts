/**
 * 鉴权共享工具：token 存取、刷新、过期校验、登录跳转
 *
 * 设计要点：
 * - 不依赖 stores/auth（避免 client ↔ store 循环引用），直接读写 localStorage
 * - refresh 用裸 fetch（不走 api 包装层），避免 401 递归
 * - 并发请求 401 时合并为同一次 refresh（refreshing 单例 promise）
 * - SSE 流的裸 fetch 也复用这套逻辑（见 useChat.ts 的 gwFetch）
 */

const TOKEN_KEY = "ua_token"
const USER_KEY = "ua_user"
const REFRESH_URL = "/api/manager/auth/refresh"

export interface TokenData {
  accessToken: string
  refreshToken: string
}

export function loadToken(): TokenData | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY)
    return raw ? (JSON.parse(raw) as TokenData) : null
  } catch {
    return null
  }
}

export function saveToken(t: TokenData) {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(t))
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getAccessToken(): string | null {
  return loadToken()?.accessToken || null
}

export function getRefreshToken(): string | null {
  return loadToken()?.refreshToken || null
}

/** 解析 access token 的 exp（秒级 Unix 时间戳）；无 exp / 解析失败返回 null */
function getAccessExp(): number | null {
  const token = getAccessToken()
  if (!token) return null
  const parts = token.split(".")
  if (parts.length < 2) return null
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")))
    const exp = payload?.exp
    return typeof exp === "number" ? exp : null
  } catch {
    return null
  }
}

/** access token 是否仍有效（含 30s 提前量，避免临界点请求撞过期） */
export function isAccessTokenValid(): boolean {
  const exp = getAccessExp()
  if (exp === null) {
    // 无 exp claim 或无 token：无 token 视为无效；有 token 无 exp 视为有效（交由后端判定）
    return !!getAccessToken()
  }
  return Date.now() < exp * 1000 - 30_000
}

let refreshing: Promise<string | null> | null = null

/**
 * 用 refresh token 换取新的 access token。
 * 成功：写入 localStorage 并返回新 access token；失败：返回 null。
 * 并发调用合并为同一次请求。
 */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshing) return refreshing
  const refreshToken = getRefreshToken()
  if (!refreshToken) return Promise.resolve(null)
  refreshing = (async () => {
    try {
      const res = await fetch(REFRESH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!res.ok) return null
      const data = await res.json()
      if (!data?.access_token) return null
      saveToken({
        accessToken: data.access_token,
        refreshToken: data.refresh_token || refreshToken,
      })
      return data.access_token as string
    } catch {
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

/**
 * 统一鉴权失败出口：清 token + 跳登录页（带 redirect 回跳参数）。
 * 已在登录页则不再跳，避免循环。
 */
export function redirectToLogin() {
  clearToken()
  if (window.location.pathname.startsWith("/login")) return
  const currentPath = window.location.pathname + window.location.search
  window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`
}

/**
 * 主动校验：access token 过期时尝试 refresh，refresh 失败则跳登录。
 * 供路由守卫、visibilitychange 重聚焦等场景调用。返回 true 表示当前鉴权可用。
 */
export async function ensureAuthenticated(): Promise<boolean> {
  if (isAccessTokenValid()) return true
  const refreshed = await refreshAccessToken()
  if (refreshed) return true
  redirectToLogin()
  return false
}
