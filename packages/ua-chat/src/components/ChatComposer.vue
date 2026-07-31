<template>
  <div class="composer-box" id="composerBox" :class="{ 'drag-active': isDragging }"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop">
    <!-- Drop hint overlay -->
    <div v-show="isDragging" class="drop-hint">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      <span>释放鼠标以添加附件</span>
    </div>
    <!-- Attachments tray -->
    <div v-if="attachments.length > 0" class="attach-tray">
      <div v-for="(file, i) in attachments" :key="i" class="attach-chip" :class="attachChipClass(file)">
        <template v-if="isImageFile(file)">
          <img class="attach-thumb" :src="getBlobUrl(file)" :alt="file.name" :title="file.name" />
        </template>
        <template v-else-if="isAudioFile(file)">
          <span class="attach-chip-media">🎵 {{ file.name }}</span>
          <audio controls preload="metadata" :src="getBlobUrl(file)" style="height:24px;max-width:200px"></audio>
        </template>
        <template v-else-if="isVideoFile(file)">
          <span class="attach-chip-media">🎬 {{ file.name }}</span>
        </template>
        <template v-else>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          <span>{{ file.name }}</span>
        </template>
        <button class="attach-chip-remove" @click="removeAttachment(i)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
    </div>
    <!-- Upload progress bar -->
    <div v-if="uploadProgress !== null" class="upload-bar-wrap">
      <div class="upload-bar">
        <span>上传中…</span>
        <div class="upload-bar-progress"><div class="upload-bar-fill" :style="{ width: uploadProgress + '%' }"></div></div>
        <span>{{ uploadProgress }}%</span>
      </div>
    </div>
    <textarea
      id="msg"
      v-model="messageText"
      rows="1"
      :placeholder="disabled ? '等待回复...' : '发送消息给智能体…'"
      :disabled="disabled"
      @keydown="onKeydown"
      @input="autoResize"
      @paste="onPaste"
    ></textarea>
    <div class="composer-footer">
      <div class="composer-left">
        <!-- Attach button -->
        <button v-if="!hideAttach" type="button" class="icon-btn has-tooltip has-tooltip--top" data-tooltip="上传附件" @click.stop="triggerFileInput">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        </button>
        <input v-if="!hideAttach" type="file" ref="fileInputRef" class="file-input-visually-hidden" multiple
          accept="image/*,text/*,application/pdf,application/json,.md,.py,.js,.ts,.yaml,.yml,.toml,.csv,.sh,.txt,.log,.xls,.xlsx,.doc,.docx,.zip,.tar,.gz"
          @change="onFileChange" />
        <!-- Divider -->
        <div v-if="!hideAttach" class="composer-divider"></div>
        <!-- Model chip -->
        <div v-if="!hideModel" class="composer-model-wrap" ref="modelWrap">
          <button type="button" class="composer-model-chip" @click.stop="toggleModelDropdown">
            <span class="composer-model-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/></svg></span>
            <span class="composer-model-label">{{ currentModel }}</span>
            <span class="composer-model-chevron"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span>
          </button>
          <div v-if="showModelDropdown" class="model-dropdown open">
            <button v-for="m in models" :key="m" type="button" class="model-dropdown-item" :class="{ active: m === currentModel }" @click="selectModel(m)">
              {{ m }}
            </button>
          </div>
        </div>
      </div>
      <div class="composer-right">
        <!-- Stop button (when streaming) -->
        <button v-if="disabled" class="stop-btn has-tooltip has-tooltip--left" data-tooltip="停止生成" @click="$emit('stop')">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor"><rect x="3" y="3" width="8" height="8" rx="1.5"/></svg>
        </button>
        <!-- Send button (when idle) -->
        <button v-else class="send-btn has-tooltip has-tooltip--left" data-tooltip="发送消息" :disabled="!canSend" @click="send">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue"

const props = defineProps<{
  disabled?: boolean
  models?: string[]
  currentModel?: string
  sendKey?: "enter" | "ctrl+enter"
  /** 隐藏附件按钮（宿主后端不支持附件上传时）。 */
  hideAttach?: boolean
  /** 隐藏模型选择 chip（宿主不支持运行时切模型时）。 */
  hideModel?: boolean
}>()

const emit = defineEmits<{
  send: [text: string, files: File[]]
  stop: []
  "select-model": [model: string]
}>()

const messageText = ref("")
const showModelDropdown = ref(false)
const attachments = ref<File[]>([])
const fileInputRef = ref<HTMLInputElement>()
const isDragging = ref(false)
const uploadProgress = ref<number | null>(null)
const blobUrls = ref<Map<File, string>>(new Map())

const canSend = computed(() => messageText.value.trim().length > 0 || attachments.value.length > 0)

const IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"]
const AUDIO_EXTS = [".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".webm"]
const VIDEO_EXTS = [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]

function extOf(name: string): string {
  const m = name.toLowerCase().match(/\.[^.]+$/)
  return m ? m[0] : ""
}
function isImageFile(f: File): boolean { return IMAGE_EXTS.includes(extOf(f.name)) || f.type.startsWith("image/") }
function isAudioFile(f: File): boolean { return AUDIO_EXTS.includes(extOf(f.name)) || f.type.startsWith("audio/") }
function isVideoFile(f: File): boolean { return VIDEO_EXTS.includes(extOf(f.name)) || f.type.startsWith("video/") }

function getBlobUrl(f: File): string {
  if (!blobUrls.value.has(f)) {
    blobUrls.value.set(f, URL.createObjectURL(f))
  }
  return blobUrls.value.get(f)!
}

function attachChipClass(f: File): string {
  if (isImageFile(f)) return "attach-chip--media attach-chip--image"
  if (isAudioFile(f)) return "attach-chip--media attach-chip--audio"
  if (isVideoFile(f)) return "attach-chip--media attach-chip--video"
  return ""
}

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = "auto"
  el.style.height = Math.min(el.scrollHeight, 200) + "px"
}

function onKeydown(e: KeyboardEvent) {
  // 输入法组合中（选词/确认候选）的回车不应提交：isComposing 为 true 或 keyCode 229
  // 都是 IME 正在处理的信号，此时回车是"确认候选词"而非"发送消息"。
  if (e.isComposing || e.keyCode === 229) return
  if (props.sendKey === "ctrl+enter") {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      send()
    }
  } else {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }
}

function send() {
  if (!canSend.value || props.disabled) return
  const text = messageText.value.trim()
  const files = [...attachments.value]
  emit("send", text, files)
  messageText.value = ""
  attachments.value = []
  const textarea = document.getElementById("msg") as HTMLTextAreaElement
  if (textarea) textarea.style.height = "auto"
}

function toggleModelDropdown() { showModelDropdown.value = !showModelDropdown.value }
function selectModel(m: string) { showModelDropdown.value = false; emit("select-model", m) }

function triggerFileInput() { fileInputRef.value?.click() }
function onFileChange(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files) {
    for (const f of files) attachments.value.push(f)
  }
  ;(e.target as HTMLInputElement).value = ""
}
function removeAttachment(i: number) {
  const f = attachments.value[i]
  const url = blobUrls.value.get(f)
  if (url) { URL.revokeObjectURL(url); blobUrls.value.delete(f) }
  attachments.value.splice(i, 1)
}

// ── Drag & drop ──
function onDragOver(e: DragEvent) {
  if (e.dataTransfer?.types?.includes("Files")) isDragging.value = true
}
function onDragLeave(e: DragEvent) {
  const related = e.relatedTarget as HTMLElement | null
  if (!related || !related.closest(".composer-box")) isDragging.value = false
}
function onDrop(e: DragEvent) {
  isDragging.value = false
  if (!e.dataTransfer?.files) return
  for (const f of e.dataTransfer.files) attachments.value.push(f)
}

// ── Paste ──
function onPaste(e: ClipboardEvent) {
  if (!e.clipboardData?.items) return
  for (const item of e.clipboardData.items) {
    if (item.kind === "file") {
      const f = item.getAsFile()
      if (f) attachments.value.push(f)
    }
  }
}

function onClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target.closest(".composer-model-wrap")) showModelDropdown.value = false
}

onMounted(() => document.addEventListener("click", onClickOutside))
onUnmounted(() => {
  document.removeEventListener("click", onClickOutside)
  blobUrls.value.forEach(url => URL.revokeObjectURL(url))
  blobUrls.value.clear()
})
</script>
