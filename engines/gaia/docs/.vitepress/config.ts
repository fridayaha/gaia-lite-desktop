import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

// 开源版 Palantir 文档站点导航配置
// 深度参考文档（architecture/design/engineer/bugfix/research 等）含大量 <> 标记
//（TypeScript 泛型、XML 示例、JSX 组件等），VitePress 默认用 Vue SFC 解析 .md 文件
// 会导致 "Element is missing end tag" 等构建错误。
// 通过自定义 Vite 插件，在 transform 阶段将深度参考文档中的 < 转义为 &lt;
// 只影响非 guide/ 目录，不影响站点核心文档的 Vue/Mermaid 功能。
// 详见 docs/research/doc-engineering-master-plan.md

export default withMermaid({
  lang: "zh-CN",
  title: "开源版 Palantir",
  description: "开源 Palantir Foundry 风格的分层数据架构 · 本体驱动的智能决策平台",
  lastUpdated: true,
  cleanUrls: true,
  ignoreDeadLinks: true,

  head: [
    ["meta", { name: "theme-color", content: "#3c8c4c" }],
  ],

  themeConfig: {
    outline: {
      level: [2, 3],
      label: "本页内容",
    },
    docFooter: {
      prev: "上一篇",
      next: "下一篇",
    },
    lastUpdatedText: "最后更新",
    returnToTopLabel: "回到顶部",
    sidebarMenuLabel: "目录",
    darkModeSwitchLabel: "主题",
    lightModeSwitchTitle: "切换到浅色模式",
    darkModeSwitchTitle: "切换到深色模式",

    nav: [
      {
        text: "文档指南",
        items: [
          { text: "从 Palantir 到开源版", link: "/guide/01-overview/01-palantir-and-gaia" },
          { text: "本体体系", link: "/guide/01-overview/03-ontology-system" },
          { text: "数据流场景", link: "/guide/01-overview/04-data-flow" },
          { text: "设计哲学与红线", link: "/guide/01-overview/05-design-principles" },
          { text: "教程", link: "/guide/02-tutorials/01-quickstart" },
          { text: "操作指南", link: "/guide/03-how-to/" },
          { text: "概念深度", link: "/guide/04-concepts/" },
          { text: "参考", link: "/guide/05-reference/api-index" },
          { text: "路线图", link: "/guide/06-roadmap/implementation-status" },
        ],
      },
      { text: "架构", link: "/architecture/" },
      { text: "设计", link: "/design/" },
      { text: "工程", link: "/engineer/" },
      { text: "事故", link: "/bugfix/" },
      { text: "研究", link: "/research/" },
      { text: "培训", link: "/training/" },
    ],

    sidebar: {
      // ========== guide/ ==========
      "/guide/": [
        {
          text: "概览",
          collapsed: false,
          items: [
            { text: "从 Palantir 到开源版", link: "/guide/01-overview/01-palantir-and-gaia" },
            { text: "开源版 Palantir：分析与观点", link: "/guide/01-overview/02-palantir-paradigm-analysis" },
            { text: "本体体系", link: "/guide/01-overview/03-ontology-system" },
            { text: "数据流场景", link: "/guide/01-overview/04-data-flow" },
            { text: "设计哲学与红线", link: "/guide/01-overview/05-design-principles" },
          ],
        },
        {
          text: "教程",
          collapsed: false,
          items: [
            { text: "快速开始", link: "/guide/02-tutorials/01-quickstart" },
            { text: "对话式本体建模", link: "/guide/02-tutorials/02-model-ontology" },
            { text: "连接数据源", link: "/guide/02-tutorials/03-connect-data" },
            { text: "图探索决策分析", link: "/guide/02-tutorials/04-explore-graph" },
          ],
        },
        {
          text: "操作指南",
          collapsed: false,
          items: [
            { text: "索引", link: "/guide/03-how-to/" },
            { text: "建模", link: "/guide/03-how-to/modeling/" },
            { text: "数据接入", link: "/guide/03-how-to/data/" },
            { text: "Action", link: "/guide/03-how-to/actions/" },
            { text: "查询", link: "/guide/03-how-to/query/" },
            { text: "权限治理", link: "/guide/03-how-to/permissions/" },
            { text: "运维", link: "/guide/03-how-to/ops/" },
          ],
        },
        {
          text: "概念深度",
          collapsed: false,
          items: [
            { text: "索引", link: "/guide/04-concepts/" },
            { text: "本体建模", link: "/guide/04-concepts/01-ontology-modeling" },
            { text: "8 层数据引擎", link: "/guide/04-concepts/02-data-layers" },
            { text: "Action 闭环", link: "/guide/04-concepts/03-action-loop" },
            { text: "本体工具层", link: "/guide/04-concepts/04-tool-layer" },
            { text: "TextQL 自然语言查询", link: "/guide/04-concepts/05-textql" },
            { text: "图关联推理", link: "/guide/04-concepts/06-graph-reasoning" },
            { text: "多源数据融合", link: "/guide/04-concepts/07-multi-source" },
            { text: "权限治理", link: "/guide/04-concepts/08-permission" },
            { text: "AI Agent", link: "/guide/04-concepts/09-ai-agent" },
          ],
        },
        {
          text: "参考",
          collapsed: false,
          items: [
            { text: "API 总览", link: "/guide/05-reference/api-index" },
            { text: "配置项", link: "/guide/05-reference/config-reference" },
            { text: "Schema 参考", link: "/guide/05-reference/schema-reference" },
            { text: "命令与脚本", link: "/guide/05-reference/cli-reference" },
            { text: "术语表", link: "/guide/05-reference/glossary" },
          ],
        },
        {
          text: "状态与路线图",
          collapsed: false,
          items: [
            { text: "实现状态", link: "/guide/06-roadmap/implementation-status" },
            { text: "版本演进", link: "/guide/06-roadmap/changelog" },
          ],
        },
      ],
      // ========== architecture/ ==========
      "/architecture/": [
        { text: "架构索引", link: "/architecture/" },
        {
          text: "总览",
          collapsed: false,
          items: [
            { text: "架构总览", link: "/architecture/architecture_overview" },
            { text: "架构规划", link: "/architecture/architecture_plan" },
            { text: "实现状态", link: "/architecture/implementation-status" },
          ],
        },
        {
          text: "核心架构",
          collapsed: false,
          items: [
            { text: "Action 架构", link: "/architecture/action-architecture" },
            { text: "Action 闭环设计", link: "/architecture/action-loop-design" },
            { text: "本体工具层", link: "/architecture/ontology-tool-layer" },
            { text: "索引加速设计", link: "/architecture/index-acceleration-design" },
          ],
        },
        {
          text: "TextQL",
          items: [
            { text: "TextQL 设计", link: "/architecture/textql-design" },
            { text: "TextQL 4+1 视图", link: "/architecture/textql-4plus1-views" },
          ],
        },
        {
          text: "图关联推理",
          items: [
            { text: "图推理设计", link: "/architecture/graph-reasoning-design" },
            { text: "推理进度", link: "/architecture/graph-reasoning-progress" },
            { text: "前端设计 v2", link: "/architecture/graph-reasoning-frontend-design-v2" },
            { text: "前端设计 v3", link: "/architecture/graph-reasoning-frontend-design-v3" },
            { text: "前端设计（旧）", link: "/architecture/graph-reasoning-frontend-design" },
          ],
        },
        {
          text: "ICD 接口契约",
          items: [
            { text: "ICD-01 PostgresMetaStore", link: "/architecture/icd-01-postgres-meta-store" },
            { text: "ICD-02 GravitinoRegistry", link: "/architecture/icd-02-gravitino-registry" },
            { text: "ICD-03 IcebergStore", link: "/architecture/icd-03-iceberg-store" },
            { text: "ICD-04 DorisIndexStore", link: "/architecture/icd-04-doris-index-store" },
            { text: "ICD-05 TrinoQueryEngine", link: "/architecture/icd-05-trino-query-engine" },
          ],
        },
        {
          text: "ADR 架构决策",
          items: [
            { text: "ADR-001 Doris 在线读主源", link: "/architecture/adr-001-doris-as-online-read-source" },
            { text: "ADR-002 SeaTunnel 而非 Flink", link: "/architecture/adr-002-seatunnel-over-flink" },
            { text: "ADR-003 RustFS 而非 MinIO", link: "/architecture/adr-003-rustfs-over-minio" },
            { text: "ADR-004 PG 存本体元数据", link: "/architecture/adr-004-postgresql-for-ontology-metadata" },
            { text: "ADR-005 ObjectType.properties→JSONB", link: "/architecture/adr-005-objecttype-properties-as-jsonb" },
            { text: "ADR-006 Python + FastAPI", link: "/architecture/adr-006-python-fastapi-over-typescript-go" },
            { text: "ADR-007 Iceberg REST Catalog", link: "/architecture/adr-007-iceberg-rest-catalog-access" },
            { text: "ADR-008 Iceberg→Doris 同步", link: "/architecture/adr-008-iceberg-doris-sync-path" },
            { text: "ADR-009 本体工具层", link: "/architecture/adr-009-ontology-tool-layer" },
            { text: "ADR-010 本体 HITL 审批", link: "/architecture/adr-010-ontology-hitl" },
            { text: "ADR-011 Action P1", link: "/architecture/adr-011-action-p1" },
            { text: "ADR-012 TextQL NL 查询", link: "/architecture/adr-012-textql-ontology-driven-nl-query" },
            { text: "ADR-013 React Aria Components", link: "/architecture/adr-013-react-aria-components" },
            { text: "ADR-014 多源融合连接器", link: "/architecture/adr-014-multi-source-data-fusion-connectors" },
            { text: "ADR-015 Agent 驱动图探索", link: "/architecture/adr-015-agent-driven-graph-explore" },
            { text: "ADR-016 权限治理体系", link: "/architecture/adr-016-permission-governance" },
            { text: "ADR-017 权限技术选型", link: "/architecture/adr-017-permission-tech-stack" },
            { text: "ADR-018 Pipeline Builder", link: "/architecture/adr-018-pipeline-builder" },
            { text: "— Action 映射", link: "/architecture/adr-action-mutation-mapping" },
          ],
        },
        {
          text: "其他",
          items: [
            { text: "Gravitino 类型兼容", link: "/architecture/gravitino-type-compatibility" },
            { text: "本体建模规范", link: "/architecture/ontology-modeling-spec" },
            { text: "本体建模 E2E 评审", link: "/architecture/ontology-modeling-e2e-review" },
            { text: "权限治理评估", link: "/architecture/permission-governance-landing-assessment" },
          ],
        },
      ],
      // ========== design/ ==========
      "/design/": [
        { text: "设计索引", link: "/design/" },
        {
          text: "数据模型与绑定",
          collapsed: false,
          items: [
            { text: "数据集-本体绑定", link: "/design/dataset-ontology-binding" },
            { text: "数据层设计", link: "/design/data-layer-design" },
            { text: "数据流图", link: "/design/data-flow-diagrams" },
            { text: "多源数据融合设计", link: "/design/multi-source-data-fusion-design" },
          ],
        },
        {
          text: "Action 同步",
          items: [
            { text: "Action Outbox 设计", link: "/design/action-sync-outbox-design" },
          ],
        },
        {
          text: "本体建模",
          items: [
            { text: "对象脚手架", link: "/design/buildwith-object-scaffolding" },
            { text: "命名空间隔离与清理", link: "/design/ontology-namespace-isolation-and-cleanup" },
          ],
        },
        {
          text: "前端设计",
          items: [
            { text: "前端数据层设计", link: "/design/frontend-data-layer-design" },
            { text: "数据源-数据集拆分", link: "/design/frontend-data-source-dataset-split" },
            { text: "前端 HCI 评审", link: "/design/frontend-hci-review" },
          ],
        },
        {
          text: "权限治理",
          items: [
            { text: "权限治理设计", link: "/design/permission-governance-design" },
            { text: "权限治理交接", link: "/design/permission-governance-handoff" },
          ],
        },
        {
          text: "管道与场景",
          items: [
            { text: "Pipeline Builder 设计", link: "/design/pipeline-builder-design" },
            { text: "场景决策穷尽", link: "/design/scenario-and-decision-exhaust-design" },
            { text: "场景数据引擎实现", link: "/design/scenario-data-engine-implementation" },
            { text: "场景实现细节", link: "/design/scenario-implementation-details" },
          ],
        },
      ],
      // ========== engineer/ ==========
      "/engineer/": [
        { text: "工程索引", link: "/engineer/" },
        {
          text: "规范与指南",
          collapsed: false,
          items: [
            { text: "工程原则与最佳实践", link: "/engineer/engineering_principles_and_best_practices" },
            { text: "前端标准", link: "/engineer/frontend-standards" },
            { text: "前端最佳实践", link: "/engineer/frontend-best-practices" },
            { text: "事务管理最佳实践", link: "/engineer/transaction-management-best-practices" },
            { text: "AI 集成指南", link: "/engineer/ai-integration-guide" },
            { text: "Agent 对接指南", link: "/engineer/agent-integration-guide" },
          ],
        },
        {
          text: "验证与测试",
          items: [
            { text: "验证指南", link: "/engineer/verification-guide" },
            { text: "权限 E2E 测试策略", link: "/engineer/permission-e2e-test-strategy" },
            { text: "权限 Phase2 落地指南", link: "/engineer/permission-phase2-landing-guide" },
            { text: "权限路线图与原则", link: "/engineer/permission-roadmap-and-principles" },
          ],
        },
        {
          text: "部署与运维",
          collapsed: false,
          items: [
            { text: "部署指导书", link: "/engineer/deployment-guide" },
            { text: "部署 Runbook", link: "/engineer/deployment-runbook" },
          ],
        },
        {
          text: "基准与评测",
          items: [
            { text: "评测基准原则", link: "/engineer/research-benchmark-principles" },
          ],
        },
        {
          text: "事故复盘",
          items: [
            { text: "SeaTunnel Iceberg 互操作", link: "/engineer/seatunnel-iceberg-rest-interop-postmortem" },
            { text: "StarRocks SeaTunnel Dry-run", link: "/engineer/starrocks-seatunnel-dryrun" },
            { text: "CDC Spike 报告", link: "/engineer/cdc-spike-report" },
          ],
        },
      ],
      // ========== bugfix/ ==========
      "/bugfix/": [
        { text: "事故索引", link: "/bugfix/" },
        {
          text: "事故报告",
          collapsed: false,
          items: [
            { text: "ACTION_TYPE_VERSION 快照未持久化", link: "/bugfix/action-type-version-snapshot-not-persisted" },
            { text: "benchmark 检测到的后端缺陷", link: "/bugfix/benchmark-detected-backend-defects" },
            { text: "DB 连接泄漏与点查性能", link: "/bugfix/db-connection-leak-and-point-lookup-perf" },
            { text: "eval: Doris 全量替换 Dataset", link: "/bugfix/eval-doris-full-data-replace-dataset-lookup" },
            { text: "Gravitino 1.3.0 升级", link: "/bugfix/gravitino-1.3.0-upgrade" },
            { text: "HITL 批量审批 pending", link: "/bugfix/hitl-batch-approval-pending-pydantic-ai" },
            { text: "Managed Dataset 治理记录缺失", link: "/bugfix/managed-dataset-governance-record-missing" },
            { text: "Object Picker 异步 ComboBox", link: "/bugfix/object-picker-async-combobox" },
            { text: "本体废弃/删除 UX", link: "/bugfix/ontology-deprecate-delete-ux" },
            { text: "Path B: Kafka-Doris Schema 不匹配", link: "/bugfix/path-b-kafka-doris-schema-mismatch" },
            { text: "SeaTunnel 索引管道不可用", link: "/bugfix/seatunnel-index-pipeline-iceberg-doris-unavailable" },
            { text: "SeaTunnel PG CDC timestamptz 阻塞", link: "/bugfix/seatunnel-pg-cdc-timestamptz-blocker" },
            { text: "SeaTunnel Worker OOM + Doris BE", link: "/bugfix/seatunnel-worker-oom-and-doris-be-mem-limit" },
            { text: "同步任务状态卡 RUNNING", link: "/bugfix/sync-task-status-stuck-running" },
          ],
        },
      ],
      // ========== research/ ==========
      "/research/": [
        { text: "研究索引", link: "/research/" },
        {
          text: "文档工程",
          collapsed: false,
          items: [
            { text: "文档工程总纲", link: "/research/doc-engineering-master-plan" },
            { text: "技术文档写作研究", link: "/research/tech-doc-writing-research" },
          ],
        },
        {
          text: "Palantir 调研",
          items: [
            { text: "能力差距分析", link: "/research/palantir-capability-gap-analysis" },
            { text: "权限隔离参考", link: "/research/palantir-permission-isolation-reference" },
            { text: "权限回顾与行业对比", link: "/research/palantir-permission-review-and-industry-comparison" },
          ],
        },
        {
          text: "权限专项",
          items: [
            { text: "数据下推与 Python 组件", link: "/research/permission-data-pushdown-and-python-components" },
            { text: "前端 UX 与开发者体验", link: "/research/permission-frontend-ux-and-developer-experience" },
            { text: "技术栈深潜", link: "/research/permission-tech-stack-deep-dive" },
          ],
        },
      ],
      // ========== training/ ==========
      "/training/": [
        { text: "培训索引", link: "/training/" },
        {
          text: "课程",
          collapsed: false,
          items: [
            { text: "Data × AI 三日培训", link: "/training/" },
            { text: "完整课程大纲", link: "/training/ai-data-3day-curriculum" },
          ],
        },
      ],
      // ========== web-ui/ ==========
      "/web-ui/": [
        { text: "前端索引", link: "/web-ui/" },
        {
          text: "前端文档",
          collapsed: false,
          items: [
            { text: "前端集成测试计划", link: "/web-ui/frontend-integration-test-plan" },
            { text: "本体管理器", link: "/web-ui/ontology-manager" },
          ],
        },
      ],
    },

    socialLinks: [{ icon: "github", link: "https://gitcode.com/Ascend-SACT/union_agent" }],

    search: {
      provider: "local",
      options: {
        translations: {
          button: { buttonText: "搜索", buttonAriaLabel: "搜索" },
          modal: {
            noResultsText: "无结果",
            resetButtonTitle: "清除查询",
            footer: { selectText: "选择", navigateText: "切换", closeText: "关闭" },
          },
        },
      },
    },

    footer: {
      message: "基于 CC-BY-4.0 发布",
      copyright: "开源版 Palantir 文档工程",
    },
  },
  vite: {
    plugins: [
      {
        name: "escape-html-in-deep-refs",
        enforce: "pre",
        transform(code: string, id: string) {
          // 转义深度参考文档中的 HTML 标签字符，防止 Vue SFC 编译器报错
          // guide/ 下的文档是站点核心内容，不做转义（保留 Mermaid/Vue 能力）
          if (
            id.endsWith(".md") &&
            !id.includes("/guide/") &&
            !id.includes("node_modules")
          ) {
            // 将 <xxx> 替换为 &lt;xxx&gt;，但保留已有的 &lt;/&gt; 等实体
            // 同时保护 markdown 链接 ![alt](url)、代码块、行内代码
            let result = code;
            // 保护 fenced code blocks：暂存后还原
            const blocks: string[] = [];
            result = result.replace(
              /```[\s\S]*?```/g,
              (m) => `__CODE_BLOCK_${blocks.push(m) - 1}__`,
            );
            // 保护行内代码
            result = result.replace(
              /`[^`]+`/g,
              (m) => `__INLINE_CODE_${blocks.push(m) - 1}__`,
            );
            // 转义未被保护的 < 为 &lt;
            result = result.replace(/</g, "&lt;");
            // 还原代码块和行内代码
            result = result.replace(
              /__CODE_BLOCK_(\d+)__/g,
              (_, i) => blocks[Number(i)],
            );
            result = result.replace(
              /__INLINE_CODE_(\d+)__/g,
              (_, i) => blocks[Number(i)],
            );
            return result;
          }
          return code;
        },
      },
    ],
  },

  mermaid: {
    // mermaid 配置；暗色主题由插件自动切换
    theme: "default",
  },
});
