import { defineConfig } from "vitepress";

export default defineConfig({
  lang: "zh-CN",
  title: "UnionAgents",
  description: "企业级 AI 智能体平台（参考实现）",
  base: "/",
  cleanUrls: true,
  srcDir: "content",
  ignoreDeadLinks: true,
  lastUpdated: false,

  locales: {
    root: {
      label: "简体中文",
      lang: "zh-CN",
      themeConfig: {
        nav: [
          { text: "文档", link: "/docs/guide/getting-started" },
          { text: "API 参考", link: "/docs/api-reference" },
          { text: "下载 App", link: "/download" },
        ],
        footer: {
          message: "基于内网部署的企业级 AI 智能体平台（参考实现）",
          copyright: "© 2026 UnionAgents",
        },
      },
    },
    en: {
      label: "English",
      lang: "en-US",
      description: "Enterprise AI Agent Platform (Reference Implementation)",
      themeConfig: {
        nav: [
          { text: "Docs", link: "/docs/guide/getting-started" },
          { text: "API Reference", link: "/docs/api-reference" },
          { text: "Download App", link: "/en/download" },
        ],
        footer: {
          message: "Self-hosted enterprise AI agent platform (reference implementation)",
          copyright: "© 2026 UnionAgents",
        },
      },
    },
  },

  themeConfig: {
    socialLinks: [
      { icon: "github", link: "https://gitcode.com/Ascend-SACT/union_agent" },
    ],
  },
});
