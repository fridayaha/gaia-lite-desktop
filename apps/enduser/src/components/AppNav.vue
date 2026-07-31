<template>
  <nav :class="navClass">
    <div :class="innerClass">
      <div class="flex justify-between h-14 items-center">
        <div class="flex items-center gap-3">
          <router-link to="/agents" class="text-lg font-semibold hover:text-blue-400"
            :class="auth.chatMode ? 'text-gray-100' : 'text-gray-800'">
            知行 · UnionAgents
          </router-link>
        </div>
        <div class="flex items-center gap-4" v-if="auth.currentUser">
          <span class="text-sm hidden sm:inline" :class="auth.chatMode ? 'text-gray-400' : 'text-gray-500'">
            {{ auth.currentUser.username }}
          </span>
          <button @click="handleLogout"
            class="text-sm transition-colors"
            :class="auth.chatMode ? 'text-gray-500 hover:text-red-400' : 'text-gray-400 hover:text-red-500'">
            退出登录
          </button>
        </div>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from "vue"
import { useAuthStore } from "@/stores/auth"
import { useRouter } from "vue-router"

const auth = useAuthStore()
const router = useRouter()

const navClass = computed(() =>
  auth.chatMode
    ? "bg-[#1a1a2e] border-b border-gray-800"
    : "bg-white border-b border-gray-200"
)

const innerClass = computed(() =>
  auth.chatMode
    ? "px-4"
    : "max-w-7xl mx-auto px-4 sm:px-6 lg:px-8"
)

function handleLogout() {
  auth.logout()
  router.push({ name: "Login" })
}
</script>
