<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useData } from "vitepress";

const { lang } = useData();
const isEn = computed(() => lang.value === "en-US");

// SSR 安全：window 只在客户端存在，onMounted 仅在客户端执行。
// 跨子域跳转 URL 基于当前 hostname 动态计算：
// - 域名访问（生产）：https://admin.<domain> / https://chat.<domain>（子域 ingress）
// - IP 访问（demo/NodePort）：http://<ip>:30080 / http://<ip>:30081（NodePort）
//   IP 上加 admin. 前缀不是合法域名，DNS 解析不了，必须走端口直连。
// - 本地 dev localhost → admin.localhost（死链，dev 看样式即可）
const hostname = ref("");
onMounted(() => {
  hostname.value = window.location.hostname;
});
const isIp = computed(() => /^\d{1,3}(\.\d{1,3}){3}$/.test(hostname.value));
const adminUrl = computed(() => {
  if (!hostname.value) return "#";
  return isIp.value ? `http://${hostname.value}:30080/` : `https://admin.${hostname.value}`;
});
const chatUrl = computed(() => {
  if (!hostname.value) return "#";
  return isIp.value ? `http://${hostname.value}:30081/` : `https://chat.${hostname.value}`;
});

// 所有 CTA 按钮 target="_blank" 新标签页打开，同时避免 VitePress SPA router
// 拦截 /docs/（docs 是独立 VitePress 站点，跟 landing 不共享 router）
const docsUrl = "/docs/guide/getting-started";
const learnMoreUrl = "/docs/features/overview";
const downloadUrl = "/download";
</script>

<template>
  <div class="landing-cta">
    <a class="cta-btn primary" :href="adminUrl" target="_blank" rel="noopener noreferrer">
      {{ isEn ? "Open Console" : "进入控制台" }}
    </a>
    <a class="cta-btn" :href="chatUrl" target="_blank" rel="noopener noreferrer">
      {{ isEn ? "Chat Portal" : "进入用户门户" }}
    </a>
    <a class="cta-btn" :href="downloadUrl" target="_blank" rel="noopener noreferrer">
      {{ isEn ? "Download App" : "下载 App" }}
    </a>
    <a class="cta-btn" :href="docsUrl" target="_blank" rel="noopener noreferrer">
      {{ isEn ? "Read Docs" : "查看文档" }}
    </a>
    <a class="cta-btn" :href="learnMoreUrl" target="_blank" rel="noopener noreferrer">
      {{ isEn ? "Learn More" : "了解更多" }}
    </a>
  </div>
</template>
