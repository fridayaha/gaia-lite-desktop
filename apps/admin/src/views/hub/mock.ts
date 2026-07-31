/**
 * 能力中心静态选项 — 标签池与第三方平台列表。
 * 能力项数据已改为从 @/api/hub 真实接口获取，本文件仅保留 UI 静态选项。
 */

// ── 标签池（导入表单的标签建议；列表展示的标签来自后端） ─────────

export const ALL_TAGS = [
  "合规检查",
  "文档处理",
  "DevOps",
  "数据分析",
  "对话交互",
  "搜索增强",
  "安全审计",
  "流程编排",
  "知识管理",
  "多模态",
  "代码生成",
  "API 集成",
];

// ── 第三方平台列表（导入框架，第三方导入尚未开放）───────────────

export const THIRD_PARTY_PLATFORMS = [
  { value: "dify", label: "Dify" },
  { value: "coze", label: "Coze (扣子)" },
  { value: "openai", label: "OpenAI GPTs" },
  { value: "github", label: "GitHub" },
  { value: "huggingface", label: "Hugging Face" },
];
