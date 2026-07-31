<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useData } from "vitepress";

const { lang } = useData();
const isEn = computed(() => lang.value === "en-US");

interface LatestRelease {
  id: string;
  platform: string;
  version: string;
  display_name: string;
  description: string;
  icon_url: string | null;
  size: number | null;
}

const latestAndroid = ref<LatestRelease | null>(null);
const latestHarmony = ref<LatestRelease | null>(null);
const latestLoading = ref(false);

const apkUrl = computed(() =>
  latestAndroid.value
    ? `/api/manager/public/app-releases/${latestAndroid.value.id}/apk`
    : "",
);
const hapUrl = computed(() =>
  latestHarmony.value
    ? `/api/manager/public/app-releases/${latestHarmony.value.id}/apk`
    : "",
);

function formatSize(bytes: number): string {
  const mb = bytes / 1024 / 1024;
  if (mb >= 1) return isEn.value ? `~${mb.toFixed(1)} MB` : `约 ${mb.toFixed(1)} MB`;
  const kb = Math.round(bytes / 1024);
  return isEn.value ? `~${kb} KB` : `约 ${kb} KB`;
}

async function fetchLatest(platform: string): Promise<LatestRelease | null> {
  try {
    const resp = await fetch(
      `${window.location.origin}/api/manager/public/app-releases/latest?platform=${platform}`,
      { headers: { Accept: "application/json" } },
    );
    if (resp.ok) {
      const body = (await resp.json()) as LatestRelease | null;
      if (body && body.id) return body;
    }
  } catch {
    // 网络/服务不可用 → 保持 null，UI 走「敬请期待」分支
  }
  return null;
}

// 同网段访问：dev 跑在 localhost，手机扫码/直接输入 URL 都需要绝对地址
// onMounted 之后才有 window.location，避免 SSR 报错
const fullApkUrl = ref("");
const fullHapUrl = ref("");
onMounted(async () => {
  const origin = window.location.origin;
  latestLoading.value = true;
  const [android, harmony] = await Promise.all([
    fetchLatest("android"),
    fetchLatest("harmony"),
  ]);
  latestAndroid.value = android;
  latestHarmony.value = harmony;
  latestLoading.value = false;
  if (android && apkUrl.value) fullApkUrl.value = origin + apkUrl.value;
  if (harmony && hapUrl.value) fullHapUrl.value = origin + hapUrl.value;
});

const downloading = ref(false);
function triggerDownload(url: string) {
  if (!url || downloading.value) return;
  downloading.value = true;
  // 编程式触发：window.location.href 指向 attachment URL，浏览器自动下载不离开当前页
  // 比依赖 <a download> 更稳：绕过 VitePress SPA router 拦截、规避某些浏览器对 download 属性的中文名处理差异
  window.location.href = url;
  setTimeout(() => (downloading.value = false), 3000);
}

const copiedUrl = ref("");
async function copyLink(url: string) {
  try {
    await navigator.clipboard.writeText(url);
    copiedUrl.value = url;
    setTimeout(() => (copiedUrl.value = ""), 2000);
  } catch {
    // 旧浏览器降级
    const ta = document.createElement("textarea");
    ta.value = url;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    copiedUrl.value = url;
    setTimeout(() => (copiedUrl.value = ""), 2000);
  }
}

interface Platform {
  key: "android" | "ios" | "harmony";
  name: string;
  nameEn: string;
  status: "available" | "coming";
  statusLabel: string;
  statusLabelEn: string;
  sysReq: string;
  sysReqEn: string;
  sizeNote: string;
  sizeNoteEn: string;
  storeLabel: string;
  storeLabelEn: string;
}

const platforms = computed<Platform[]>(() => {
  const androidAvailable = latestAndroid.value !== null;
  const harmonyAvailable = latestHarmony.value !== null;
  const androidSizeNote =
    latestAndroid.value?.size != null && latestAndroid.value.size > 0
      ? formatSize(latestAndroid.value.size)
      : isEn.value
        ? "Pending release"
        : "待发布";
  const harmonySizeNote =
    latestHarmony.value?.size != null && latestHarmony.value.size > 0
      ? formatSize(latestHarmony.value.size)
      : isEn.value
        ? "Pending release"
        : "待发布";

  if (isEn.value) {
    return [
      {
        key: "android",
        name: "Android",
        nameEn: "Android",
        status: androidAvailable ? "available" : "coming",
        statusLabel: androidAvailable ? "Available" : "Coming soon",
        statusLabelEn: androidAvailable ? "Available" : "Coming soon",
        sysReq: "Android 8.0 (API 26)+",
        sysReqEn: "Android 8.0 (API 26)+",
        sizeNote: androidSizeNote,
        sizeNoteEn: androidSizeNote,
        storeLabel: androidAvailable ? "Download APK" : "Notify me",
        storeLabelEn: androidAvailable ? "Download APK" : "Notify me",
      },
      {
        key: "harmony",
        name: "HarmonyOS",
        nameEn: "HarmonyOS",
        status: harmonyAvailable ? "available" : "coming",
        statusLabel: harmonyAvailable ? "Available" : "Coming soon",
        statusLabelEn: harmonyAvailable ? "Available" : "Coming soon",
        sysReq: "HarmonyOS NEXT (API 12)+",
        sysReqEn: "HarmonyOS NEXT (API 12)+",
        sizeNote: harmonySizeNote,
        sizeNoteEn: harmonySizeNote,
        storeLabel: harmonyAvailable ? "Download HAP" : "Notify me",
        storeLabelEn: harmonyAvailable ? "Download HAP" : "Notify me",
      },
      {
        key: "ios",
        name: "iOS",
        nameEn: "iOS",
        status: "coming",
        statusLabel: "Coming soon",
        statusLabelEn: "Coming soon",
        sysReq: "iOS 15+",
        sysReqEn: "iOS 15+",
        sizeNote: "TestFlight beta",
        sizeNoteEn: "TestFlight beta",
        storeLabel: "Notify me",
        storeLabelEn: "Notify me",
      },
    ];
  }
  return [
    {
      key: "android",
      name: "Android",
      nameEn: "Android",
      status: androidAvailable ? "available" : "coming",
      statusLabel: androidAvailable ? "已发布" : "敬请期待",
      statusLabelEn: androidAvailable ? "Available" : "Coming soon",
      sysReq: "Android 8.0（API 26）及以上",
      sizeNote: androidSizeNote,
      storeLabel: androidAvailable ? "下载 APK" : "通知我",
    },
    {
      key: "harmony",
      name: "鸿蒙",
      nameEn: "HarmonyOS",
      status: harmonyAvailable ? "available" : "coming",
      statusLabel: harmonyAvailable ? "已发布" : "敬请期待",
      statusLabelEn: harmonyAvailable ? "Available" : "Coming soon",
      sysReq: "HarmonyOS NEXT（API 12）及以上",
      sizeNote: harmonySizeNote,
      storeLabel: harmonyAvailable ? "下载 HAP" : "通知我",
    },
    {
      key: "ios",
      name: "iOS",
      nameEn: "iOS",
      status: "coming",
      statusLabel: "敬请期待",
      statusLabelEn: "Coming soon",
      sysReq: "iOS 15 及以上",
      sizeNote: "TestFlight 内测",
      storeLabel: "通知我",
    },
  ];
});

const heroBadge = computed(() => {
  if (latestAndroid.value) return `v${latestAndroid.value.version}`;
  if (latestHarmony.value) return `v${latestHarmony.value.version}`;
  return isEn.value ? "Coming soon" : "敬请期待";
});

const features = computed(() =>
  isEn.value
    ? [
        { title: "Web login reuse", details: "Same username/password as the web portal — no separate account." },
        { title: "Agent list", details: "Browse accessible agents, switch engines/models on the fly." },
        { title: "SSE streaming chat", details: "Real-time content/reasoning/tool-progress streaming for HERMES & non-HERMES engines." },
        { title: "Session management", details: "Drawer-style session list, create/switch/rename/delete." },
        { title: "Approval requests", details: "Inline approve/deny for dangerous tool calls." },
        { title: "Workspace browser", details: "Browse agent files and preview text/images inline." },
      ]
    : [
        { title: "复用 Web 登录", details: "与网页端共用账号，无需单独注册。" },
        { title: "智能体列表", details: "浏览可访问智能体，按需切换引擎和模型。" },
        { title: "SSE 流式对话", details: "支持 HERMES 与非 HERMES 引擎，内容/思考/工具进度实时流式渲染。" },
        { title: "会话管理", details: "抽屉式会话列表，新建/切换/重命名/删除。" },
        { title: "审批请求", details: "危险工具调用弹窗确认（本次/会话/总是/拒绝）。" },
        { title: "工作区文件", details: "浏览智能体工作区文件，文本/图片直接预览。" },
      ],
);

const installStepsAndroid = computed(() =>
  isEn.value
    ? [
        "Download the APK file from the Android card above.",
        "Tap the downloaded file in the notification drawer or Files app.",
        "Allow installs from this source if prompted by Android.",
        "Open the 知行 app and sign in with your portal account.",
      ]
    : [
        "点击 Android 卡片的「下载 APK」按钮下载安装包。",
        "在通知栏或文件管理器中点击下载好的文件。",
        "如系统提示「未知来源」，按引导允许当前来源安装。",
        "打开「知行」App，用门户账号登录即可使用。",
      ],
);

const installStepsHarmony = computed(() =>
  isEn.value
    ? [
        "On your phone, enable Developer Mode: Settings > About phone > tap the HarmonyOS version number repeatedly until prompted.",
        "In Settings > System & updates > Developer options, turn on USB debugging.",
        "On your computer (Windows or macOS), download the auto-installer tool and the HAP package from this page.",
        "Sign in to auto-installer with your Huawei ID — it signs the HAP with a debug certificate for your device.",
        "Connect the phone to the computer via USB, then select the HAP in auto-installer to install.",
        "Open the 知行 app and sign in with your portal account.",
      ]
    : [
        "手机开启「开发者模式」：设置 > 关于手机 > 连续点击 HarmonyOS 版本号，直到提示已开启。",
        "进入 设置 > 系统和更新 > 开发人员选项，开启「USB 调试」。",
        "电脑（Windows 或 macOS）下载 auto-installer 工具，并从本页下载 HAP 安装包。",
        "在 auto-installer 中登录华为账号，工具会用调试证书为你的设备签名 HAP。",
        "手机通过 USB 连接电脑，在 auto-installer 中选择 HAP 完成安装。",
        "打开「知行」App，用门户账号登录即可使用。",
      ],
);

const harmonyNote = computed(() =>
  isEn.value
    ? "Debug-signed builds expire after 14 days (unverified Huawei ID) — reinstall with auto-installer when prompted."
    : "调试签名有效期为 14 天（未实名认证的华为账号），过期后需用 auto-installer 重新安装。",
);
</script>

<template>
  <div class="download-page">
    <section class="hero">
      <h1 class="title">{{ isEn ? "UnionAgents Client (Beta)" : "UnionAgents 客户端（内测版本）" }}</h1>
      <p class="subtitle">
        {{ isEn ? "Native client for the enduser portal — pick your platform." : "终端门户原生客户端，选择你的平台下载。" }}
      </p>
      <div class="badges">
        <span class="badge">{{ heroBadge }}</span>
        <span class="badge">{{ isEn ? "3 platforms" : "3 个平台" }}</span>
      </div>
    </section>

    <section class="platform-grid">
      <div
        v-for="p in platforms"
        :key="p.key"
        class="platform-card"
        :class="{ 'is-available': p.status === 'available' }"
      >
        <div class="platform-head">
          <h3 class="platform-name">{{ isEn ? p.nameEn : p.name }}</h3>
          <span
            class="platform-status"
            :class="p.status === 'available' ? 'status-available' : 'status-coming'"
          >
            {{ isEn ? p.statusLabelEn : p.statusLabel }}
          </span>
        </div>
        <dl class="platform-meta">
          <div class="meta-row">
            <dt>{{ isEn ? "System" : "系统要求" }}</dt>
            <dd>{{ isEn ? p.sysReqEn : p.sysReq }}</dd>
          </div>
          <div class="meta-row">
            <dt>{{ isEn ? "Package" : "安装包" }}</dt>
            <dd>{{ isEn ? p.sizeNoteEn : p.sizeNote }}</dd>
          </div>
        </dl>
        <div class="platform-actions">
          <button
            v-if="p.key === 'android' && p.status === 'available'"
            class="cta-btn primary"
            type="button"
            :disabled="downloading"
            @click="triggerDownload(apkUrl)"
          >
            {{ isEn ? p.storeLabelEn : p.storeLabel }}
          </button>
          <button
            v-else-if="p.key === 'harmony' && p.status === 'available'"
            class="cta-btn primary"
            type="button"
            :disabled="downloading"
            @click="triggerDownload(hapUrl)"
          >
            {{ isEn ? p.storeLabelEn : p.storeLabel }}
          </button>
          <button
            v-else
            class="cta-btn disabled"
            type="button"
            disabled
          >
            {{ isEn ? p.storeLabelEn : p.storeLabel }}
          </button>
        </div>
      </div>
    </section>

    <section v-if="fullApkUrl" class="link-box">
      <span class="link-label">{{ isEn ? "Android direct link:" : "Android 直链：" }}</span>
      <code>{{ fullApkUrl }}</code>
      <button class="copy-btn" type="button" @click="copyLink(fullApkUrl)">
        {{ copiedUrl === fullApkUrl ? (isEn ? "Copied!" : "已复制！") : isEn ? "Copy" : "复制" }}
      </button>
    </section>

    <section v-if="fullHapUrl" class="link-box">
      <span class="link-label">{{ isEn ? "HarmonyOS direct link:" : "鸿蒙直链：" }}</span>
      <code>{{ fullHapUrl }}</code>
      <button class="copy-btn" type="button" @click="copyLink(fullHapUrl)">
        {{ copiedUrl === fullHapUrl ? (isEn ? "Copied!" : "已复制！") : isEn ? "Copy" : "复制" }}
      </button>
    </section>

    <section class="meta">
      <div class="meta-block">
        <h2>{{ isEn ? "Install (Android)" : "安装步骤（Android）" }}</h2>
        <ol>
          <li v-for="(step, i) in installStepsAndroid" :key="i">{{ step }}</li>
        </ol>
      </div>
      <div v-if="latestHarmony" class="meta-block">
        <h2>{{ isEn ? "Install (HarmonyOS)" : "安装步骤（鸿蒙）" }}</h2>
        <ol>
          <li v-for="(step, i) in installStepsHarmony" :key="i">{{ step }}</li>
        </ol>
        <p class="note">{{ harmonyNote }}</p>
      </div>
    </section>

    <section class="features">
      <h2>{{ isEn ? "Features" : "主要功能" }}</h2>
      <div class="feature-grid">
        <div v-for="f in features" :key="f.title" class="feature-card">
          <h3>{{ f.title }}</h3>
          <p>{{ f.details }}</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.download-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}

.hero {
  text-align: center;
  margin-bottom: 40px;
}

.title {
  font-size: 36px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 8px;
  color: var(--vp-c-text-1);
}

.subtitle {
  font-size: 16px;
  color: var(--vp-c-text-2);
  margin: 0 0 16px;
}

.badges {
  display: inline-flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
  background: var(--vp-c-bg-soft);
}

.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}

.platform-card {
  display: flex;
  flex-direction: column;
  padding: 20px;
  border-radius: 12px;
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-divider);
}

.platform-card.is-available {
  border-color: var(--vp-c-brand);
  box-shadow: 0 0 0 1px var(--vp-c-brand-soft);
}

.platform-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.platform-name {
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  color: var(--vp-c-text-1);
}

.platform-status {
  font-size: 12px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 999px;
}

.status-available {
  color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
}

.status-coming {
  color: var(--vp-c-text-2);
  background: var(--vp-c-bg-alt);
  border: 1px solid var(--vp-c-divider);
}

.platform-meta {
  margin: 0 0 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.meta-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
}

.meta-row dt {
  flex: 0 0 64px;
  color: var(--vp-c-text-2);
}

.meta-row dd {
  margin: 0;
  color: var(--vp-c-text-1);
}

.platform-actions {
  margin-top: auto;
}

.cta-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  border: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg-alt);
  color: var(--vp-c-text-1);
  width: 100%;
  box-sizing: border-box;
}

.cta-btn.primary {
  background: var(--vp-c-brand-1);
  color: var(--vp-c-white);
  border-color: var(--vp-c-brand-1);
}

.cta-btn.primary:hover {
  background: var(--vp-c-brand-2);
}

.cta-btn.disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.link-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: 8px;
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-divider);
  margin-bottom: 16px;
}

.link-label {
  font-size: 13px;
  color: var(--vp-c-text-2);
}

.link-box code {
  flex: 1;
  font-size: 13px;
  color: var(--vp-c-text-1);
  word-break: break-all;
}

.copy-btn {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1);
  cursor: pointer;
}

.copy-btn:hover {
  background: var(--vp-c-bg-alt);
}

.meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 32px;
  margin-top: 32px;
  margin-bottom: 48px;
}

.meta-block h2 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px;
  color: var(--vp-c-text-1);
}

.meta-block ol {
  padding-left: 20px;
  margin: 0;
  color: var(--vp-c-text-2);
  font-size: 14px;
  line-height: 1.8;
}

.meta-block .note {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--vp-c-text-2);
}

.features h2 {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 16px;
  color: var(--vp-c-text-1);
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.feature-card {
  padding: 16px;
  border-radius: 8px;
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-divider);
}

.feature-card h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 6px;
  color: var(--vp-c-text-1);
}

.feature-card p {
  font-size: 13px;
  margin: 0;
  color: var(--vp-c-text-2);
  line-height: 1.6;
}

@media (max-width: 640px) {
  .title {
    font-size: 28px;
  }
  .download-page {
    padding: 20px 16px 48px;
  }
  .platform-grid {
    grid-template-columns: 1fr;
  }
}
</style>
