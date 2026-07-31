<template>
  <div class="browser-view">
    <div v-if="status === 'connecting'" class="bv-overlay">
      <div class="bv-spinner"></div>
      <span>正在连接云桌面…</span>
    </div>
    <div v-else-if="status === 'disconnected'" class="bv-overlay bv-error">
      <span>云桌面未连接</span>
      <button class="bv-retry" @click="connect">重新连接</button>
    </div>
    <!-- noVNC RFB 把 canvas 挂到该容器；KasmVNC Keyboard 的 IME/触摸 input 锚点由 connect()
      命令式创建并挂入此处（避免 vue-tsc 对 <input ref> 的类型推断 quirks） -->
    <div ref="screenRef" class="bv-screen" :class="{ 'view-only': viewOnly }"></div>
    <div v-if="viewOnly && status === 'connected'" class="bv-viewonly-mask">
      <span>智能体操作中，云桌面只读</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount, nextTick } from "vue"
import RFB, { MouseButtonMapper, XVNC_BUTTONS } from "@/lib/kasm-novnc"
import { getAccessToken, refreshAccessToken } from "@/api/auth"

const props = defineProps<{
  agentId: string
  viewOnly: boolean        // true=Agent 自动化中（只读+遮罩），false=用户接管（可输入）
  active: boolean          // 面板展开时 true → 建立 VNC 连接；折叠/离开 false → 断开
}>()

const screenRef = ref<HTMLDivElement | null>(null)
let touchInput: HTMLInputElement | null = null  // KasmVNC Keyboard 的 IME/触摸输入锚点
const status = ref<"idle" | "connecting" | "connected" | "disconnected">("idle")
let rfb: any = null
let reconnectTimer: number | null = null
let connectRetries = 0  // 非正常断开重试计数（连上后清零），防 refresh 死循环

function buildUrl(token: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws"
  return `${proto}://${location.host}/api/gateway/v1/browser/${encodeURIComponent(props.agentId)}/vnc?token=${encodeURIComponent(token)}`
}

let connecting = false  // 防并发建连：rapid toggle/重试时 connect() 可能重入，避免同时开 2 条
// WS（kasm VNC 单会话，并发会触发 "Server is already in use" → 旧连接被 code 1000 踢掉，
// 控制台报 "failed when connecting"）。
async function connect() {
  if (connecting) return
  connecting = true
  try {
    if (rfb) disconnect()
    status.value = "connecting"
    let token = getAccessToken()
    if (!token) {
      token = await refreshAccessToken()
      if (!token) {
        status.value = "disconnected"
        return
      }
    }
    await nextTick()
    // await 期间面板可能已折叠（watch(active)→disconnect），复查避免给已关闭面板开 WS
    // （kasm 单会话，残留 WS 会与下次连撞 "Server is already in use"）。
    if (!props.active) return
    const screen = screenRef.value
    if (!screen) return
    // KasmVNC noVNC fork 构造签名：(target, touchInput, url, options) —— touchInput 是
    // Keyboard 的 IME/触摸输入 <input> 锚点（_keyboardInputReset 会写 .value）。命令式创建，
    // 避开 vue-tsc 对 <input ref> 的类型推断。隐藏但可聚焦。
    if (!touchInput) {
      touchInput = document.createElement("input")
      touchInput.type = "text"
      touchInput.autocapitalize = "off"
      touchInput.autocomplete = "off"
      touchInput.setAttribute("autocorrect", "off")
      touchInput.spellcheck = false
      touchInput.tabIndex = -1
      touchInput.setAttribute("aria-hidden", "true")
      // 命令式创建的元素不受 Vue scoped 样式作用，用内联样式藏到屏幕外（kasm Keyboard
      // 仅用它做 IME/触摸锚点，桌面端键盘走 canvas；不可见但可聚焦）。
      touchInput.style.cssText =
        "position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;border:0;padding:0;margin:0;"
      screen.appendChild(touchInput)
    }
    try {
      // kasm 设 -SecurityTypes None，RFB 层走 NoAuth，无需 RFB 密码；WS 升级层的 Basic auth
      // （kasm_user:VNC_PW）由 gateway bridge_vnc_ws 注入，noVNC 只持 JWT（?token=）。
      rfb = new RFB(screen, touchInput, buildUrl(token), {
        shared: false,
        retry: false,
      })
      // KasmVNC RFB 构造里 mouseButtonMapper=null，鼠标事件会 .get() 报错。kasm web app
      // 默认注入左/中/右/侧键映射，这里复刻（app/ui.js initMouseButtonMapper）。
      const mbm = new MouseButtonMapper()
      mbm.set(0, XVNC_BUTTONS.LEFT_BUTTON)
      mbm.set(1, XVNC_BUTTONS.MIDDLE_BUTTON)
      mbm.set(2, XVNC_BUTTONS.RIGHT_BUTTON)
      mbm.set(3, XVNC_BUTTONS.BACK_BUTTON)
      mbm.set(4, XVNC_BUTTONS.FORWARD_BUTTON)
      rfb.mouseButtonMapper = mbm
      rfb.scaleViewport = true
      rfb.resizeSession = false
      rfb.viewOnly = props.viewOnly
      rfb.showDotCursor = true  // 无远端光标时显本地圆点（view-only 时光标可见）
      rfb.addEventListener("connect", () => {
        status.value = "connected"
        connectRetries = 0  // 连上后清零重试计数
      })
      rfb.addEventListener("disconnect", async (e: any) => {
        const clean = e?.detail?.clean
        rfb = null
        if (clean) {
          status.value = "idle"
          return
        }
        // 非正常断开：限次重试（最多 2 次），避免 403/404（无 browser Pod / 无权限）时
        // refreshAccessToken + 重连死循环刷 /auth/refresh。仅首次重试刷新 token。
        if (!props.active || connectRetries >= 2) {
          status.value = "disconnected"
          return
        }
        connectRetries++
        if (connectRetries === 1) {
          await refreshAccessToken()  // 仅首次可能 token 过期，刷一次
        }
        status.value = "connecting"
        reconnectTimer = window.setTimeout(() => {
          if (props.active) connect()
        }, 1200)
      })
    } catch (e) {
      console.error("VNC connect failed:", e)
      status.value = "disconnected"
    }
  } finally {
    connecting = false
  }
}

function disconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (rfb) {
    try {
      rfb.disconnect()
    } catch { /* noop */ }
    rfb = null
  }
}

// active 变化：展开→连接，折叠→断开（折叠仅 UI 隐藏，但为省资源断开；重新展开重连）
watch(() => props.active, (v) => {
  if (v) {
    connectRetries = 0  // 手动展开重置重试计数
    connect()
  } else {
    disconnect()
  }
}, { immediate: true })

// viewOnly 变化：切换输入权限（接管/释放）
watch(() => props.viewOnly, (v) => {
  if (rfb) rfb.viewOnly = v
})

onBeforeUnmount(() => disconnect())
</script>

<style scoped>
.browser-view {
  position: relative;
  width: 100%;
  height: 100%;
  background: #000;
  overflow: hidden;
}
.bv-screen {
  width: 100%;
  height: 100%;
  /* noVNC scaleViewport 会把 canvas 按比例缩放并居中；容器用 flex 居中 canvas，
     不用 !important 强拉 canvas（强拉 + object-fit:contain 会导致画面 letterbox 而
     canvas 元素满铺，点击坐标映射偏移几十 px） */
  display: flex;
  align-items: center;
  justify-content: center;
}
.bv-screen :deep(canvas) {
  /* 尺寸交给 noVNC scaleViewport 控制（保持 1024x768 比例），不在此强拉 */
}
/* KasmVNC noVNC Keyboard 的触摸/IME 输入锚点：屏幕外隐藏，保留可聚焦 */
.bv-keyboard-input {
  position: absolute;
  left: -9999px;
  top: 0;
  width: 1px;
  height: 1px;
  opacity: 0;
  border: 0;
  padding: 0;
}
.bv-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #cbd5e1;
  background: rgba(15, 23, 42, 0.92);
  font-size: 13px;
  z-index: 5;
}
.bv-error .bv-retry {
  background: var(--accent, #3b82f6);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.bv-spinner {
  width: 26px;
  height: 26px;
  border: 3px solid rgba(148, 163, 184, 0.3);
  border-top-color: #93c5fd;
  border-radius: 50%;
  animation: bv-spin 0.8s linear infinite;
}
@keyframes bv-spin { to { transform: rotate(360deg); } }
.bv-viewonly-mask {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 6px 10px;
  background: linear-gradient(transparent, rgba(0,0,0,0.6));
  color: #e2e8f0;
  font-size: 12px;
  text-align: center;
  pointer-events: none;
}
</style>
