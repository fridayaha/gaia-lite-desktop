<template>
  <div class="agent-list-page">
    <div class="agent-list-container">
      <!-- Header -->
      <div class="agent-list-header">
        <div>
          <h1 class="agent-list-title">我的智能体</h1>
          <p class="agent-list-subtitle">选择智能体开始对话</p>
        </div>
        <!-- 用户菜单：头像按钮 + 下拉（用户名/邮箱 + 退出登录） -->
        <div class="user-menu" ref="userMenuRef">
          <button class="user-menu-trigger" @click="userMenuOpen = !userMenuOpen" :aria-expanded="userMenuOpen" aria-label="用户菜单">
            <span class="user-avatar">{{ userInitial }}</span>
          </button>
          <div v-if="userMenuOpen" class="user-menu-dropdown" role="menu">
            <div class="user-menu-info">
              <div class="user-menu-name">{{ auth.currentUser?.username || "用户" }}</div>
              <div class="user-menu-email" v-if="auth.currentUser?.email">{{ auth.currentUser.email }}</div>
            </div>
            <button class="user-menu-item" role="menuitem" @click="handleLogout">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              <span>退出登录</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Loading state -->
      <div v-if="loading" class="agent-grid">
        <div v-for="i in 4" :key="i" class="agent-card agent-card-skeleton">
          <div class="skeleton-line w-3/4"></div>
          <div class="skeleton-line w-full mt-3"></div>
          <div class="skeleton-line w-1/2 mt-2"></div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else-if="agents.length === 0" class="agent-empty">
        <div class="agent-empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        </div>
        <p class="agent-empty-text">暂无可用智能体</p>
        <p class="agent-empty-hint">请联系管理员为您开通智能体访问权限</p>
      </div>

      <!-- Error state -->
      <div v-else-if="loadError" class="agent-empty">
        <div class="agent-empty-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <p class="agent-empty-text">加载失败</p>
        <p class="agent-empty-hint">{{ loadError }}</p>
        <button class="agent-retry-btn" @click="loadAgents">重试</button>
      </div>

      <!-- Agent grid -->
      <div v-else class="agent-grid">
        <div
          v-for="agent in agents"
          :key="agent.id"
          @click="goToAgent(agent.id)"
          class="agent-card"
          role="button"
          tabindex="0"
          @keydown.enter="goToAgent(agent.id)"
        >
          <div class="agent-card-body">
            <div class="agent-card-top">
              <h3 class="agent-card-title">{{ agent.name }}</h3>
              <span class="agent-engine-badge" :class="agent.engine_type.toLowerCase()">
                {{ engineLabel(agent.engine_type) }}
              </span>
            </div>
            <p v-if="agent.description" class="agent-card-desc">{{ agent.description }}</p>
          </div>
          <div v-if="agent.last_accessed_at" class="agent-card-footer">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            {{ formatTime(agent.last_accessed_at) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue"
import { useRouter } from "vue-router"
import { getAccessibleAgents, type AccessibleAgent } from "@/api/endpoints"
import { useAuthStore } from "@/stores/auth"

const router = useRouter()
const auth = useAuthStore()
const agents = ref<AccessibleAgent[]>([])
const loading = ref(true)
const loadError = ref("")
const userMenuOpen = ref(false)
const userMenuRef = ref<HTMLElement | null>(null)

const userInitial = computed(() => {
  const name = auth.currentUser?.username || ""
  return name ? name[0].toUpperCase() : "U"
})

// 点击外部关闭下拉
function handleDocClick(e: MouseEvent) {
  if (userMenuRef.value && !userMenuRef.value.contains(e.target as Node)) {
    userMenuOpen.value = false
  }
}
onMounted(() => {
  loadAgents()
  document.addEventListener("click", handleDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", handleDocClick)
})

async function loadAgents() {
  loading.value = true
  loadError.value = ""
  try {
    agents.value = await getAccessibleAgents()
  } catch (e: any) {
    loadError.value = e.message || "无法加载智能体列表"
  } finally {
    loading.value = false
  }
}

function handleLogout() {
  if (!confirm("确定要退出登录吗？")) return
  auth.logout()
  userMenuOpen.value = false
  router.push("/login")
}

function goToAgent(id: string) {
  router.push(`/agents/${id}`)
}

function engineLabel(type: string) {
  if (type === "HERMES") return "Hermes"
  if (type === "DIFY") return "Dify"
  return "OpenClaw"
}

function formatTime(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "刚刚"
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return d.toLocaleDateString("zh-CN")
}
</script>

<style scoped>
.agent-list-page {
  min-height: 100vh;
  background: var(--bg);
  display: flex;
  justify-content: center;
}
.agent-list-container {
  width: 100%;
  max-width: 720px;
  padding: 40px 16px;
}
.agent-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}
.agent-list-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -.02em;
  margin: 0;
}
.agent-list-subtitle {
  font-size: 13px;
  color: var(--muted);
  margin-top: 2px;
}

/* 用户菜单 dropdown */
.user-menu {
  position: relative;
}
.user-menu-trigger {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--border, #e5e7eb);
  background: var(--surface, #f9fafb);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: background 0.15s;
}
.user-menu-trigger:hover {
  background: var(--hover, #f3f4f6);
}
.user-avatar {
  font-size: 14px;
  font-weight: 600;
  color: var(--text, #111827);
}
.user-menu-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: min(220px, calc(100vw - 32px));
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  z-index: 50;
}
.user-menu-info {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border, #e5e7eb);
}
.user-menu-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text, #111827);
}
.user-menu-email {
  font-size: 12px;
  color: var(--muted, #6b7280);
  margin-top: 2px;
}
.user-menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: none;
  background: transparent;
  color: var(--text, #111827);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}
.user-menu-item:hover {
  background: var(--hover, #f3f4f6);
}

/* Agent grid */
.agent-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 640px) {
  .agent-grid { grid-template-columns: 1fr 1fr; }
}

/* Agent card */
.agent-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  transition: border-color .15s, background .15s, transform .15s;
  outline: none;
}
.agent-card:hover, .agent-card:focus-visible {
  border-color: var(--accent-bg-strong);
  background: var(--surface-subtle-hover);
  transform: translateY(-1px);
}
.agent-card-body {
  padding: 16px 16px 12px;
  flex: 1;
}
.agent-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.agent-card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin: 0;
  flex: 1;
  min-width: 0;
}
.agent-card-desc {
  font-size: 13px;
  color: var(--muted);
  line-height: 1.5;
  margin: 6px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.agent-card-footer {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 16px;
  border-top: 1px solid var(--border-subtle);
  font-size: 11px;
  color: var(--muted);
}
.agent-card-footer svg { flex-shrink: 0; }

/* Engine badge */
.agent-engine-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: .02em;
}
.agent-engine-badge.hermes {
  background: var(--accent-bg);
  color: var(--accent-text);
  border: 1px solid var(--accent-bg-strong);
}
.agent-engine-badge.openclaw {
  background: rgba(77, 208, 225, .12);
  color: var(--blue);
  border: 1px solid rgba(77, 208, 225, .25);
}
.agent-engine-badge.dify {
  background: rgba(27, 100, 243, .12);
  color: #1b64f3;
  border: 1px solid rgba(27, 100, 243, .25);
}
.agent-engine-badge.claude_code {
  background: rgba(217, 119, 87, .12);
  color: #d97757;
  border: 1px solid rgba(217, 119, 87, .25);
}

/* Skeleton */
.agent-card-skeleton { cursor: default; pointer-events: none; }
.agent-card-skeleton:hover { transform: none; border-color: var(--border); background: var(--surface); }
.skeleton-line {
  height: 12px;
  background: var(--border);
  border-radius: 6px;
  animation: skeleton-pulse 1.5s ease-in-out infinite;
}
.skeleton-line:nth-child(2) { animation-delay: .1s; }
.skeleton-line:nth-child(3) { animation-delay: .2s; }
@keyframes skeleton-pulse {
  0%, 100% { opacity: .4; }
  50% { opacity: .7; }
}

/* Empty / Error state */
.agent-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 24px;
  text-align: center;
}
.agent-empty-icon {
  width: 56px; height: 56px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  background: var(--surface);
  border: 1px solid var(--border);
  margin-bottom: 12px;
}
.agent-empty-text {
  font-size: 16px;
  font-weight: 500;
  color: var(--text);
  margin: 0;
}
.agent-empty-hint {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
}
.agent-retry-btn {
  margin-top: 16px;
  padding: 6px 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
  transition: all .15s;
}
.agent-retry-btn:hover { background: var(--hover-bg); border-color: var(--accent); }
</style>
