import { defineConfig } from "vitepress";

export default defineConfig({
  lang: "zh-CN",
  title: "UnionAgents",
  description: "企业级 AI 智能体平台 — 文档中心",
  // 挂在 admin nginx /docs/ 子路径下，必须设 base 让 VitePress 生成 /docs/assets/... 的绝对路径
  // 否则默认 base="/" 会让浏览器请求 /assets/...（404）
  base: "/docs/",
  cleanUrls: true,
  // lastUpdated 关闭：需 git CLI 取 last commit 时间，docs build 环境无 git。
  // 如需开启，admin Dockerfile build-stage 需 apt-get install -y git。
  lastUpdated: false,
  srcDir: "content",

  // 忽略死链：/api/manager/docs 是 Swagger 代理路径（非 markdown 文件），
  // assets/skills/* 是仓库外资源链接（迁移自 docs/ 旧相对路径）
  ignoreDeadLinks: true,

  head: [
    ["meta", { name: "theme-color", content: "#3c8dc5" }],
  ],

  themeConfig: {
    siteTitle: "UnionAgents 文档",

    nav: [
      { text: "首页", link: "/" },
      { text: "用户指南", link: "/guide/getting-started" },
      { text: "API 调用", link: "/guide/api-usage" },
      { text: "API 参考", link: "/api-reference" },
      { text: "架构", link: "/architecture/architecture-v3" },
      { text: "进入控制台", link: "http://190.92.230.115:30080/" },
    ],

    sidebar: {
      "/guide/": [
        {
          text: "快速开始",
          items: [
            { text: "入门指引", link: "/guide/getting-started" },
            { text: "API 调用指导", link: "/guide/api-usage" },
          ],
        },
        {
          text: "页面操作指南",
          items: [
            { text: "首页概览", link: "/guide/pages/welcome" },
            { text: "智能体开发", link: "/guide/pages/agent-definitions" },
            { text: "智能体实例", link: "/guide/pages/agent-instances" },
            { text: "资源池", link: "/guide/pages/resource-pools" },
            { text: "能力中心 Hub", link: "/guide/pages/hub" },
            { text: "技能工作室", link: "/guide/pages/skill-studio" },
            { text: "知识库", link: "/guide/pages/knowledge" },
            { text: "模型组与 API Key", link: "/guide/pages/litellm" },
            { text: "监控中心", link: "/guide/pages/monitoring" },
            { text: "系统管理", link: "/guide/pages/system" },
            { text: "社区", link: "/guide/pages/community" },
            { text: "账户设置", link: "/guide/pages/account-settings" },
          ],
        },
      ],
      "/architecture/": [
        {
          text: "架构设计",
          items: [
            { text: "V3 总体架构", link: "/architecture/architecture-v3" },
            { text: "V3 生命周期", link: "/architecture/architecture-v3-lifecycle" },
            { text: "Profile 多租户", link: "/architecture/architecture-v2-profile-multitenancy" },
            { text: "IM 渠道架构", link: "/architecture/architecture-im-channels" },
            { text: "Profile 架构", link: "/architecture/architecture-profiles" },
          ],
        },
      ],
      "/features/": [
        {
          text: "功能说明",
          items: [
            { text: "平台总览", link: "/features/overview" },
            { text: "完整功能目录", link: "/features/full-feature-catalog" },
            { text: "Agent 详情页运维", link: "/features/2026-06-05" },
            { text: "Gateway 网关", link: "/features/gateway" },
            { text: "Hermes 引擎", link: "/features/hermes-engine" },
            { text: "终端门户", link: "/features/enduser-portal" },
            { text: "IM 渠道接入", link: "/features/im-channels" },
            { text: "Skill 凭证管理", link: "/features/skill-credentials" },
            { text: "Skill Secret Sidecar", link: "/features/skill-secret-sidecar-design" },
          ],
        },
      ],
      "/deployment/": [
        {
          text: "部署",
          items: [
            { text: "部署指南", link: "/deployment/guide" },
          ],
        },
      ],
      "/changelog/": [
        {
          text: "更新日志",
          items: [
            { text: "2026-07-25", link: "/changelog/2026-07-25" },
            { text: "2026-07-03", link: "/changelog/2026-07-03" },
            { text: "2026-06-15", link: "/changelog/2026-06-15" },
            { text: "2026-06-11", link: "/changelog/2026-06-11" },
            { text: "2026-06-05", link: "/changelog/2026-06-05" },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: "github", link: "https://gitcode.com/Ascend-SACT/union_agent" },
    ],

    outline: {
      level: [2, 3],
      label: "本页导航",
    },

    docFooter: {
      prev: "上一页",
      next: "下一页",
    },

    lastUpdatedText: "最后更新",

    search: {
      provider: "local",
      options: {
        translations: {
          button: {
            buttonText: "搜索文档",
            buttonAriaLabel: "搜索文档",
          },
          modal: {
            noResultsText: "无法找到相关结果",
            footer: {
              selectText: "选择",
              navigateText: "切换",
            },
          },
        },
      },
    },

    footer: {
      message: "基于内网部署的企业级 AI 智能体平台",
      copyright: "© 2026 UnionAgents",
    },
  },
});
