/** HTTP 客户端 — 自动附加 JWT，401 时尝试 refresh 续期，失败再跳登录 */

import {
  getAccessToken,
  refreshAccessToken,
  redirectToLogin,
} from "./auth"

const BASE_URL = "/api"

interface RequestOptions {
  method?: string
  headers?: Record<string, string>
  body?: any
}

class ApiError extends Error {
  status: number
  retryAfter: number | null
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.retryAfter = retryAfter
  }
}

async function request<T = any>(path: string, options: RequestOptions = {}, _retried = false): Promise<T> {
  const token = getAccessToken()
  const isFormData = options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...options.headers,
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? (isFormData ? options.body : JSON.stringify(options.body)) : undefined,
  })

  if (res.status === 204) {
    return undefined as T
  }

  if (res.status === 401) {
    // Token 过期/无效：先尝试 refresh 续期，成功则用新 token 重试一次（仅一次，避免死循环）
    if (!_retried) {
      const newToken = await refreshAccessToken()
      if (newToken) return request<T>(path, options, true)
    }
    // refresh 失败或重试仍 401 → 清 token 跳登录
    redirectToLogin()
    throw new ApiError(401, "Unauthorized")
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    // 423 account_locked 时后端在 Retry-After header 返回锁定剩余秒数
    const retryAfterHeader = res.headers.get("Retry-After")
    const retryAfter = retryAfterHeader ? parseInt(retryAfterHeader, 10) : null
    throw new ApiError(
      res.status,
      err.detail || err.message || "Request failed",
      retryAfter
    )
  }

  return res.json()
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, body?: any) => request<T>(path, { method: "POST", body }),
  put: <T = any>(path: string, body?: any) => request<T>(path, { method: "PUT", body }),
  delete: <T = any>(path: string, body?: any) => request<T>(path, { method: "DELETE", body }),
}

export { ApiError }
