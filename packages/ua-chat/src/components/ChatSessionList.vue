<template>
  <div class="session-list" id="sessionList">
    <!-- Empty state -->
    <div v-if="sessions.length === 0" class="session-empty">暂无对话</div>

    <!-- Date groups -->
    <template v-for="group in groupedSessions" :key="group.label">
      <div class="session-date-group">
        <div class="session-date-header" @click="toggleGroup(group.label)">
          <span class="session-date-caret" :class="{ collapsed: collapsedLabels[group.label] }">&#x25BE;</span>
          <span class="session-date-label">{{ group.label }}</span>
        </div>
        <div v-show="!collapsedLabels[group.label]" class="session-date-body">
          <div
            v-for="s in group.items"
            :key="s.session_id"
            class="session-item"
            :class="{
              active: s.session_id === currentSessionId && !renamingId,
              selected: selectedIds.has(s.session_id),
              'menu-open': menuSession?.session_id === s.session_id,
              'swipe-open': swipeSessionId === s.session_id,
            }"
            :style="swipeSessionId === s.session_id ? { transform: 'translateX(-72px)' } : {}"
            @click="onSessionClick(s)"
            @touchstart="onItemTouchStart($event, s)"
            @touchmove="onItemTouchMove($event, s)"
            @touchend="onItemTouchEnd($event, s)"
          >
            <!-- Mobile: 左滑删除按钮（绝对定位右侧） -->
            <button
              v-if="!selectMode"
              class="session-swipe-delete"
              @click.stop="deleteSession(s)"
              aria-label="删除对话"
            >删除</button>
            <!-- Checkbox (multi-select mode) -->
            <label v-if="selectMode" class="session-select-cb-wrapper" @click.stop>
              <input type="checkbox" :checked="selectedIds.has(s.session_id)" @change="toggleSelect(s.session_id)" class="session-select-cb">
            </label>

            <!-- Session content -->
            <div class="session-text">
              <div class="session-title-row">
                <!-- Inline rename input -->
                <input
                  v-if="renamingId === s.session_id"
                  ref="renameInputRef"
                  v-model="renameText"
                  class="session-title-input"
                  @keydown.enter="finishRename(s)"
                  @keydown.escape="cancelRename"
                  @blur="onRenameBlur(s)"
                  @click.stop
                  @mousedown.stop
                  @pointerdown.stop
                >
                <!-- Normal title -->
                <span v-else class="session-title" :title="s.title">{{ s.title || "未命名" }}</span>
                <span class="session-time">{{ formatTime(s.last_message_at || s.created_at) }}</span>
              </div>
            </div>

            <!-- Three-dot actions button -->
            <div class="session-actions" v-if="!selectMode">
              <button class="session-actions-trigger" @click.stop="openMenu($event, s)" title="对话操作" aria-label="对话操作">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="3" r="1.25"/><circle cx="8" cy="8" r="1.25"/><circle cx="8" cy="13" r="1.25"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Multi-select toggle -->
    <div v-if="!selectMode" class="session-select-toggle" @click="enterSelectMode">选择</div>

    <!-- Batch action bar -->
    <div v-if="selectMode" class="batch-action-bar">
      <span class="batch-count">已选 {{ selectedIds.size }} 项</span>
      <button class="batch-action-btn batch-action-btn-danger" :disabled="selectedIds.size === 0" @click="confirmBatchDelete">删除</button>
      <button class="batch-action-btn" @click="exitSelectMode">完成</button>
    </div>

    <!-- Context menu -->
    <teleport to="body">
      <div v-if="menuSession" class="session-action-menu" :style="menuStyle" ref="menuRef">
        <button class="session-action-opt" @click="doRename(menuSession)">
          <span class="ws-opt-action">
            <span class="ws-opt-icon"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M11.5 2.5l2 2L5 13H3v-2z"/><path d="M10 4l2 2"/></svg></span>
            <span class="session-action-copy"><span class="ws-opt-name">重命名</span></span>
          </span>
        </button>
        <button class="session-action-opt danger" @click="confirmDeleteOne(menuSession)">
          <span class="ws-opt-action">
            <span class="ws-opt-icon"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M3.5 4.5h9M6.5 4.5V3h3v1.5M4.5 4.5v8.5h7v-8.5"/><line x1="7" y1="7" x2="7" y2="11"/><line x1="9" y1="7" x2="9" y2="11"/></svg></span>
            <span class="session-action-copy"><span class="ws-opt-name">删除</span></span>
          </span>
        </button>
      </div>
    </teleport>

    <!-- Confirm dialog -->
    <teleport to="body">
      <div v-if="dialog.show" class="app-dialog-overlay" @click.self="dialog.show = false">
        <div class="app-dialog">
          <div class="app-dialog-header">{{ dialog.title }}</div>
          <div class="app-dialog-desc">{{ dialog.message }}</div>
          <div class="app-dialog-actions">
            <button class="app-dialog-btn" @click="dialog.show = false">取消</button>
            <button class="app-dialog-btn confirm" :class="{ danger: dialog.danger }" @click="dialog.onConfirm()">删除</button>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onUnmounted, nextTick } from "vue"

const props = defineProps<{ sessions: any[]; currentSessionId?: string | null }>()
const emit = defineEmits<{ select: [id: string]; rename: [id: string, title: string]; delete: [id: string]; batchDelete: [ids: string[]] }>()

// ── Context menu ──
const menuSession = ref<any>(null)
const menuStyle = ref({})
const menuRef = ref<HTMLElement | null>(null)

function openMenu(e: MouseEvent, s: any) {
  if (menuSession.value?.session_id === s.session_id) { closeMenu(); return }
  const btn = e.currentTarget as HTMLElement
  const rect = btn.closest('.session-item')?.getBoundingClientRect()
  if (!rect) return
  const menuW = 220
  let left = rect.right - menuW
  if (left < 8) left = 8
  let top = rect.bottom + 4
  const maxH = window.innerHeight - top - 12
  if (maxH < 120 && rect.top > 160) top = rect.top - 4 - 80
  menuStyle.value = { left: `${left}px`, top: `${top}px` }
  menuSession.value = s
}

function closeMenu() { menuSession.value = null }

// ── Inline rename ──
const renamingId = ref<string | null>(null)
const renameText = ref("")
const renameInputRef = ref<HTMLInputElement | null>(null)
let _renameOriginal = ""

function doRename(s: any) {
  closeMenu()
  if (!s) return
  renamingId.value = s.session_id
  renameText.value = s.title || "未命名"
  _renameOriginal = renameText.value
  setTimeout(() => {
    const inp = document.querySelector(".session-title-input") as HTMLInputElement | null
    inp?.focus()
    inp?.select()
  }, 50)
}

async function finishRename(s: any) {
  const tid = renamingId.value
  renamingId.value = null
  const t = renameText.value.trim()
  if (t && t !== _renameOriginal && tid) emit("rename", tid, t)
}

function cancelRename() { renamingId.value = null }
function onRenameBlur(s: any) {
  // Small delay so click on Enter fires before blur
  setTimeout(() => { if (renamingId.value) finishRename(s) }, 100)
}

// ── Confirm dialog ──
const dialog = reactive({ show: false, title: "", message: "", danger: false, onConfirm: () => {} })

function showDialog(title: string, message: string, danger: boolean, onConfirm: () => void) {
  dialog.title = title
  dialog.message = message
  dialog.danger = danger
  dialog.onConfirm = () => { dialog.show = false; onConfirm() }
  dialog.show = true
}

function confirmDeleteOne(s: any) {
  closeMenu()
  swipeSessionId.value = null
  showDialog("删除对话？", "此操作不可恢复，确定删除吗？", true, () => emit("delete", s.session_id))
}

// ── Mobile: 左滑显示删除按钮 ──
const swipeSessionId = ref<string | null>(null)
const touchStart = { x: 0, y: 0 }
let longPressTimer: number | null = null

function onItemTouchStart(e: TouchEvent, s: any) {
  if (selectMode.value) return
  const t = e.touches[0]
  touchStart.x = t.clientX
  touchStart.y = t.clientY
  // 长按 500ms 触发上下文菜单
  if (longPressTimer) clearTimeout(longPressTimer)
  longPressTimer = window.setTimeout(() => {
    const item = (e.target as HTMLElement).closest('.session-item') as HTMLElement
    if (item) {
      const rect = item.getBoundingClientRect()
      const menuW = 220
      let left = rect.right - menuW
      if (left < 8) left = 8
      let top = rect.bottom + 4
      const maxH = window.innerHeight - top - 12
      if (maxH < 120 && rect.top > 160) top = rect.top - 4 - 80
      menuStyle.value = { left: `${left}px`, top: `${top}px` }
      menuSession.value = s
      swipeSessionId.value = null
    }
  }, 500)
}

function onItemTouchMove(e: TouchEvent, s: any) {
  if (selectMode.value) return
  const t = e.touches[0]
  const dx = t.clientX - touchStart.x
  const dy = t.clientY - touchStart.y
  if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null }
  }
  // 横向滑动 > 纵向 且向左滑 → 阻止冒泡防止触发 click
  if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {
    e.stopPropagation()
  }
}

function onItemTouchEnd(e: TouchEvent, s: any) {
  if (selectMode.value) return
  if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null }
  const t = e.changedTouches[0]
  const dx = t.clientX - touchStart.x
  const dy = t.clientY - touchStart.y
  if (Math.abs(dx) < Math.abs(dy) || Math.abs(dx) < 60) {
    // 竖向滑动或位移太小，回弹
    return
  }
  if (dx < 0) {
    // 左滑 → 显示删除按钮
    swipeSessionId.value = s.session_id
  } else {
    // 右滑 → 收起删除按钮
    swipeSessionId.value = null
  }
}

function deleteSession(s: any) {
  swipeSessionId.value = null
  confirmDeleteOne(s)
}

function confirmBatchDelete() {
  const ids = [...selectedIds]
  showDialog("删除对话？", `确定删除 ${ids.length} 个对话？`, true, () => {
    ids.forEach((id) => emit("delete", id))
    selectedIds.clear()
    selectMode.value = false
  })
}

// ── Multi-select mode ──
const selectMode = ref(false)
const selectedIds = reactive(new Set<string>())

function enterSelectMode() { selectMode.value = true }
function exitSelectMode() { selectMode.value = false; selectedIds.clear() }
function toggleSelect(id: string) { if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id) }

// ── Date grouping with collapse ──
const collapsedLabels = reactive<Record<string, boolean>>({})

function loadCollapsed() {
  try { const d = JSON.parse(localStorage.getItem("ua-date-groups-collapsed") || "{}"); Object.assign(collapsedLabels, d) } catch {}
}
function saveCollapsed() {
  try { localStorage.setItem("ua-date-groups-collapsed", JSON.stringify({ ...collapsedLabels })) } catch {}
}
function toggleGroup(label: string) { collapsedLabels[label] = !collapsedLabels[label]; saveCollapsed() }
function isCollapsed(label: string): boolean { return !!collapsedLabels[label] }

const groupedSessions = computed(() => {
  const groups: { label: string; items: any[] }[] = []
  const items = [...props.sessions]
  const now = new Date()
  const todayStart = new Date(now); todayStart.setHours(0, 0, 0, 0)
  const yesterdayStart = new Date(todayStart); yesterdayStart.setDate(yesterdayStart.getDate() - 1)
  const weekStart = new Date(todayStart); weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7))
  const lastWeekStart = new Date(weekStart); lastWeekStart.setDate(lastWeekStart.getDate() - 7)

  let cur: { label: string; items: any[] } | null = null
  for (const s of items) {
    const ts = Number(s.last_message_at || s.created_at) * 1000
    let label = "更早"
    if (ts >= todayStart.getTime()) label = "今天"
    else if (ts >= yesterdayStart.getTime()) label = "昨天"
    else if (ts >= weekStart.getTime()) label = "本周"
    else if (ts >= lastWeekStart.getTime()) label = "上周"
    if (!cur || cur.label !== label) { cur = { label, items: [] }; groups.push(cur) }
    cur.items.push(s)
  }
  return groups
})

function formatTime(t: number | string | null): string {
  if (!t) return ""
  const ts = Number(t) * 1000
  if (!Number.isFinite(ts)) return ""
  const diff = Math.max(0, Date.now() - ts)
  const min = 60000, hr = 3600000
  if (diff < min) return "1分钟"
  if (diff < hr) return Math.floor(diff / min) + "分钟"
  if (diff < 86400000) return Math.floor(diff / hr) + "小时"
  return Math.floor(diff / 86400000) + "天"
}

function onSessionClick(s: any) {
  if (selectMode.value) { toggleSelect(s.session_id); return }
  if (renamingId.value) return
  emit("select", s.session_id)
}

// ── Global listeners ──
function onDocClick(e: MouseEvent) {
  if (menuSession.value) {
    const el = menuRef.value
    const target = e.target as Node
    if (el && !el.contains(target) && !(target as HTMLElement)?.closest?.(".session-actions-trigger")) closeMenu()
  }
}
function onDocKey(e: KeyboardEvent) {
  if (e.key === "Escape") {
    if (menuSession.value) closeMenu()
    else if (selectMode.value) exitSelectMode()
    else if (renamingId.value) cancelRename()
  }
}

onMounted(() => {
  loadCollapsed()
  document.addEventListener("click", onDocClick)
  document.addEventListener("keydown", onDocKey)
})
onUnmounted(() => {
  document.removeEventListener("click", onDocClick)
  document.removeEventListener("keydown", onDocKey)
  if (longPressTimer) clearTimeout(longPressTimer)
})
</script>

<style scoped>
/* ── Empty state ── */
.session-empty { padding: 12px; color: var(--muted); font-size: 12px; text-align: center; }

/* ── Date group header ── */
.session-date-group { padding: 0; }
.session-date-header { display: flex; align-items: center; gap: 4px; padding: 8px 10px 2px; cursor: pointer; user-select: none; }
.session-date-header:hover { opacity: 0.8; }
.session-date-caret { font-size: 10px; color: var(--muted); transition: transform .15s; transform: rotate(0deg); }
.session-date-caret.collapsed { transform: rotate(-90deg); }
.session-date-label { font-size: 10px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }

/* ── Session item ── */
.session-item { display: flex; align-items: center; padding: 6px 10px; border-radius: var(--radius-md); cursor: pointer; margin-bottom: 1px; transition: background .15s; border: none; background: none; width: 100%; text-align: left; color: var(--text); font-family: var(--font-ui); position: relative; }
.session-item:hover { background: var(--hover-bg); }
.session-item.active { background: var(--accent-bg-strong); }
.session-item.active .session-title { color: var(--accent-text); }
.session-item.active .session-time { color: var(--accent-text); }
.session-item.selected { background: var(--accent-bg); }

/* ── Checkbox ── */
.session-select-cb-wrapper { display: flex; align-items: center; margin-right: 6px; cursor: pointer; }
.session-select-cb { width: 14px; height: 14px; accent-color: var(--accent); cursor: pointer; }

/* ── Session text ── */
.session-text { flex: 1; min-width: 0; overflow: hidden; }
.session-title-row { display: flex; align-items: center; gap: 8px; }
.session-title { flex: 1; font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.4; min-width: 0; }
.session-time { margin-left: auto; font-size: 10px; color: var(--muted); white-space: nowrap; flex-shrink: 0; display: inline-flex; transition: opacity .12s; }

/* ── Inline rename input ── */
.session-title-input { flex: 1; font-size: 13px; font-weight: 500; line-height: 1.4; min-width: 0; background: var(--input-bg); border: 1px solid var(--accent-bg-strong); border-radius: 4px; padding: 2px 4px; color: var(--text); outline: none; box-shadow: 0 0 0 2px var(--accent-bg-strong); font-family: var(--font-ui); }

/* ── Three-dot button ── */
.session-item:hover .session-time { display: none; }
.session-actions { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); display: flex; align-items: center; justify-content: center; opacity: 0; pointer-events: none; transition: opacity .12s; }
.session-item:hover .session-actions { opacity: 1; pointer-events: auto; }
.session-actions-trigger { width: 26px; height: 26px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--muted); cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; transition: background .12s, color .12s; }
.session-actions-trigger:hover { background: var(--hover-bg); color: var(--text); }
.session-actions-trigger svg { display: block; }

/* ── Context menu ── */
.session-action-menu { position: fixed; min-width: 220px; background: var(--surface); border: 1px solid var(--border2); border-radius: 10px; box-shadow: 0 -4px 24px rgba(0,0,0,.4); z-index: 9999; overflow: hidden; transform-origin: top right; animation: menuEnter .12s ease-out; }
@keyframes menuEnter { from { opacity: 0; transform: translate3d(0,-4px,0) scale(.96); } to { opacity: 1; transform: translate3d(0,0,0) scale(1); } }
.session-action-opt { width: 100%; background: none; border: none; text-align: left; font: inherit; color: var(--text); padding: 0; cursor: pointer; }
.session-action-opt .ws-opt-action { display: flex; flex-direction: row; align-items: center; gap: 10px; width: 100%; padding: 8px 14px; }
.session-action-opt .ws-opt-icon { color: var(--muted); flex-shrink: 0; display: flex; align-items: center; width: 16px; }
.session-action-opt .ws-opt-name { font-size: 13px; }
.session-action-opt:hover { background: var(--hover-bg); }
.session-action-opt:hover .ws-opt-icon { color: var(--text); }

/* ── Danger (Delete) ── */
.session-action-opt.danger:hover { background: rgba(239,83,80,.08); }
.session-action-opt.danger .ws-opt-icon,
.session-action-opt.danger .ws-opt-name { color: var(--error, #e94560); }
.session-action-opt.danger:hover .ws-opt-icon,
.session-action-opt.danger:hover .ws-opt-name { color: var(--error, #e94560); }

/* ── Select toggle ── */
.session-select-toggle { font-size: 11px; padding: 8px 10px; color: var(--muted); cursor: pointer; text-align: center; }
.session-select-toggle:hover { color: var(--text); }

/* ── Batch action bar ── */
.batch-action-bar { display: flex; align-items: center; margin: 0 10px 8px; padding: 8px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); gap: 6px; font-size: 12px; flex-wrap: wrap; }
.batch-count { flex: 1; font-size: 11px; color: var(--muted); padding: 0 4px; }
.batch-action-btn { padding: 4px 10px; border: 1px solid var(--border2); border-radius: 6px; background: transparent; color: var(--text); cursor: pointer; font-size: 11px; }
.batch-action-btn:hover { background: var(--hover-bg); }
.batch-action-btn:disabled { opacity: .4; cursor: default; }
.batch-action-btn-danger { color: var(--error, #e94560); border-color: var(--error, #e94560); }
.batch-action-btn-danger:hover { background: rgba(239,83,80,.08); }

/* ── Confirm dialog ── */
.app-dialog-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; z-index: 99998; }
.app-dialog { background: var(--surface); border: 1px solid var(--border2); border-radius: 12px; box-shadow: 0 -8px 32px rgba(0,0,0,.5); min-width: 320px; max-width: 420px; padding: 20px; }
.app-dialog-header { font-size: 15px; font-weight: 600; margin-bottom: 8px; color: var(--text); }
.app-dialog-desc { font-size: 13px; color: var(--muted); margin-bottom: 16px; line-height: 1.4; }
.app-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.app-dialog-btn { padding: 6px 16px; border: 1px solid var(--border2); border-radius: 8px; background: transparent; color: var(--text); cursor: pointer; font-size: 13px; }
.app-dialog-btn:hover { background: var(--hover-bg); }
.app-dialog-btn.confirm { background: var(--accent); color: #fff; border-color: var(--accent); }
.app-dialog-btn.confirm.danger { background: transparent; border-color: var(--error, #e94560); color: var(--error, #e94560); }
.app-dialog-btn.confirm.danger:hover { background: rgba(239,83,80,.12); }

/* ── Mobile: 左滑删除 + 隐藏三点菜单 ── */
.session-item { transition: background .15s, transform .2s ease-out; }
.session-swipe-delete {
  position: absolute;
  right: -72px;
  top: 0;
  bottom: 0;
  width: 72px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--error, #e94560);
  color: #fff;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
}
@media (max-width: 768px) {
  /* mobile 隐藏 hover 三点菜单，改用左滑删除 + 长按上下文菜单 */
  .session-actions { display: none; }
}
@media (min-width: 769px) {
  /* desktop 用三点菜单，隐藏移动端左滑删除按钮（否则 right:-72px 戳出红块） */
  .session-swipe-delete { display: none; }
}
</style>