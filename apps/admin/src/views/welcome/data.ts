import dayjs from "dayjs";

/** 平台概览统计（系统管理员） */
export const platformStats = {
  agentCount: 23,
  agentWeeklyChange: 2,
  userCount: 156,
  userWeeklyChange: 5,
  todayConversations: 1284,
  convesationChange: "+12%",
  monthlyTokens: 8200000,
  tokenChange: "+8%"
};

/** Agent 状态分布 */
export const agentStatusDistribution = [
  { name: "已发布", value: 12, color: "#00a870" },
  { name: "草稿", value: 8, color: "#f59e0b" },
  { name: "已下架", value: 3, color: "#909399" }
];

/** 引擎分布 */
export const engineDistribution = [
  { name: "Hermes", value: 18, color: "#386bf5" },
  { name: "OpenClaw", value: 5, color: "#e6a23c" }
];

/** 系统服务状态 */
export const serviceStatus = [
  { name: "Manager", status: "正常" as const, type: "api" as const },
  { name: "Controller", status: "正常" as const, type: "api" as const },
  { name: "Gateway", status: "正常" as const, type: "api" as const },
  { name: "PostgreSQL", status: "正常" as const, type: "db" as const }
];

/** 全平台资源消耗（24h 趋势） */
export function generateResourceData() {
  const now = Date.now();
  const labels: string[] = [];
  const cpuData: number[] = [];
  const memData: number[] = [];
  for (let i = 23; i >= 0; i--) {
    const t = dayjs(now - i * 3600000);
    labels.push(t.format("HH:mm"));
    cpuData.push(Math.round((40 + Math.random() * 35) * 10) / 10);
    memData.push(Math.round((512 + Math.random() * 256) * 10) / 10);
  }
  return { labels, cpuData, memData };
}

/** 最近操作动态 */
export const recentActivities = [
  { user: "Admin", action: "发布了智能体", target: "代码审查助手", time: "10:32", type: "publish" },
  { user: "张三", action: "创建了智能体", target: "数据分析 Agent", time: "09:45", type: "create" },
  { user: "Admin", action: "新增了用户", target: "李四", time: "09:12", type: "user" },
  { user: "李四", action: "修改了智能体配置", target: "文档总结助手", time: "08:30", type: "edit" },
  { user: "王五", action: "从技能市场安装了", target: "SQL 优化器", time: "昨天 17:20", type: "install" },
  { user: "Admin", action: "下架了智能体", target: "旧版翻译助手", time: "昨天 15:00", type: "offline" }
];

/** 用户组管理员的组概览（mock） */
export const groupStats = {
  groupName: "研发组",
  agentCount: 8,
  memberCount: 15,
  todayConversations: 347,
  monthlyTokens: 2100000
};

/** 用户组内的 Agent 状态分布 */
export const groupAgentDistribution = [
  { name: "已发布", value: 5, color: "#00a870" },
  { name: "草稿", value: 2, color: "#f59e0b" },
  { name: "已下架", value: 1, color: "#909399" }
];

/** 用户可访问的 Agent 列表（mock） */
export const myAgents = [
  { id: "a1", name: "代码审查", description: "审查代码质量", engine_type: "HERMES" as const, status: "PUBLISHED" as const, usage: 128 },
  { id: "a2", name: "数据分析", description: "分析数据趋势", engine_type: "HERMES" as const, status: "PUBLISHED" as const, usage: 89 },
  { id: "a3", name: "文档总结", description: "自动总结对话", engine_type: "HERMES" as const, status: "PUBLISHED" as const, usage: 45 },
  { id: "a4", name: "知识检索", description: "检索知识库", engine_type: "OPENCLAW" as const, status: "PUBLISHED" as const, usage: 256 },
  { id: "a5", name: "SQL 优化", description: "优化 SQL 查询", engine_type: "OPENCLAW" as const, status: "DRAFT" as const, usage: 0 }
];
