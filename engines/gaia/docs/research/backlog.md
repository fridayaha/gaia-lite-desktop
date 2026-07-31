# Gaia 项目遗留问题清单

> 最后更新：2026-07-17
> 来源：`implementation-status.md` + `gaia-vs-industry-trends-gap-analysis.md`
> 优先级定义：P0 阻塞发版 | P1 下一里程碑 | P2 规划中 | 远期 探索性

---

## 一、架构层

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| A1 | Gravitino jsonb→JSON 映射 bug | P2 | 当前 pgnative workaround 绕过。需等 Gravitino 社区修复 PG TypeConverter 后回归 `pg` catalog。参考 `docs/bugfix/gravitino-1.3.0-upgrade.md` |
| A2 | Doris 索引表命名空间隔离 | ✅ 已修复 (v5.2) | `idx_{ont}__{type}` 表名加本体前缀。已落地 |

---

## 二、Context Catalog（P0-P1）

> 目标：将本体 + Catalog + 质量指标打包为 AI 可消费的统一上下文层。这是 Gaia 最大差异化优势。

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| CC1 | **统一上下文层（ECL）** — 本体+Catalog+质量指标未打包为 AI Agent 的统一上下文接口 | P0 | 当前 AI Agent 只看到本体工具，看不到底层数据质量、血缘、新鲜度。需要设计统一上下文 API（MCP Server 或 REST），单次调用返回对象语义 + 物理表元信息 + 质量指标 + 权限边界 |
| CC2 | **Catalog MCP Server** — Gravitino 物理资产元数据不通过 MCP 暴露 | P1 | AI 无法通过 MCP 自我发现「有哪些数据源、有哪些表、Schema 是什么」。已有 GravitinoRegistry 底层能力，只需 FastMCP 包装 |
| CC3 | **质量 MCP Server** — 数据质量指标不通过 MCP 暴露 | P1 | AI 无法自查「这张表的数据质量如何、有没有漂移」。已有数据质量探查能力，只需 MCP 暴露 |
| CC4 | **管道 MCP Server** — 管道状态不通过 MCP 暴露 | P1 | AI 无法查询「上次同步时间、管道是否健康」。已有管道管理能力，只需 MCP 暴露 |
| CC5 | **术语表（Glossary）** — 无双语术语、Synonyms/Aliases 支持 | P2 | 本体有 displayName/apiName，但缺乏正式的术语管理（同义词、缩写、业务定义） |
| CC6 | **多模态资产治理** — Catalog 仅管理表，不管理 ML 模型/AI Agent/Notebook | P2 | 需扩展 Gravitino 资产类型或建立独立的 AI 资产注册机制 |
| CC7 | **AI 自动资产发现** — Catalog 无法自动发现新数据资产并推荐本体映射 | 远期 | 需 Schema 爬取 + Semantic Matching + 推荐引擎 |

---

## 三、Agentic Data Engineering（P1）

> 目标：AI Agent 拥有数据管道的全生命周期——NL→生成→部署→监控→修复。2026 年数据工程最大变革方向。

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| DE1 | **NL→Pipeline 生成** — TextQL 能做 NL→SQL，但不能做 NL→Pipeline | P1 | 用户用自然语言描述数据接入/清洗需求 → Agent 自动生成 SeaTunnel 管道配置 + 测试 + 部署。可与现有 AI Agent 框架（pydantic-ai）复用 |
| DE2 | **管道自监控 Agent** — 无 Agent 自动分析异常并推荐修复 | P1 | 在 ConflictDetector 基础上叠加 Agent 智能层：异常检测→Agent 分析→推荐修复方案→人工审批或自动执行 |
| DE3 | **管道自愈** — 管道失败后无 Agent 尝试修复 | P2 | 管道失败 → Agent 分析日志和错误类型 → 尝试已知修复（重试/跳过坏行/调整超时） → 失败则升级告警 |
| DE4 | **变更影响分析** — 上游 Schema 变更无法自动分析下游影响 | P2 | 本体层已有对象→属性→关系的完整链路，Schema 变更时 Agent 自动分析受影响的下游 ObjectType/管道/Action |
| DE5 | **管道层模型推理变换接入** — SeaTunnel Embedding/LLM Transform 上游已具备但未接线 | P1 | `SeaTunnelEngine` 模板需支持生成 Embedding/LLM Transform 配置块 + live dry-run 验证 + 本地 ONNX vs 云端 API 决策（ADR） |
| DE6 | SeaTunnel backfill pipeline success 回调自动触发投影 | P2 | 当前 `ProjectSyncService` 需手动调 admin 路由触发。待接自动回调 |

---

## 四、语义检索与图推理（P1）

> 目标：底层能力齐备但用户不可用——投入产出比最高的短平快补齐。

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| SR1 | **语义检索对外暴露** — `nearestNeighbors` ObjectSet IR type 未实现 | P1 | VECTOR base type + Doris IVF ANN + ONNX embedding 底层齐备，只需实现 IR type + 查询路径。参考 Palantir `ObjectSetNearestNeighborsType` |
| SR2 | **search_objects 工具** — 对象实例语义检索未暴露给 Agent/REST/前端 | P1 | 新增 `tools/toolsets/object_query.py` 的 `search_objects` 工具 + `POST /objects/{ont}/search` 端点。底层 `DorisIndexStore.vector_search` 已有 |
| SR3 | **Hybrid Search** — 结构化 filter + ANN TopN 混合检索未实现 | P1 | 一条 SQL 实现 `WHERE` 倒排预过滤 + `ORDER BY ... LIMIT k` ANN 排序 |
| SR4 | **GraphRAG** — 向量匹配→图节点定位→图遍历扩展→LLM 推理的组合模式 | P1 | 图基础（Neo4j find_paths/search_around）已完成，差组合编排：语义匹配定位起点→图遍历获取关联→关联对象属性注入 LLM 上下文 |
| SR5 | **推理路径可视化** — EvidenceChain 有记录但无可视化展示 | P2 | 图的边如何支撑了 AI 结论——需要前端展示「推理链路图」 |
| SR6 | Embedding 生成路径从 IndexSyncService 迁移到管道层 | P2 | 依赖 DE5（管道层模型推理变换接入）。外部接入路径的 embedding 由 IndexSyncService 代劳是临时方案 |

---

## 五、本体工具层（P2-Sprint 3）

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| T1 | **ApprovalStore Redis 持久化** | P1 | 当前进程内 dict，重启丢失所有待审批操作。Redis 替换同接口。参考 `docs/architecture/adr-010-ontology-hitl.md` §9.3 |
| T2 | **高危输名称确认（AG-UI）** | P2 | 当前高危操作只弹是/否确认，CLAUDE.md 要求高危输名称确认。前端补输入框 + 后端校验 |
| T3 | **Claude Desktop elicitation 实测** | P2 | 代码就绪（MCPApprovalHandler + Context.elicit），需真实 Claude Desktop 环境验证 |
| T4 | **工具过滤优化** — 22 工具导致 LLM tool selection 退化 | P2 | `prepare_tools` 按对话场景动态过滤可用工具列表，减少 LLM 选择压力 |
| T5 | **函数族（Ontology Function）** | 远期 | 封装业务规则的可组合执行单元——声明式 DSL 优先，Python 沙箱后续 |
| T6 | **场景族（Scenario）** | 远期 | 基于写时复制的沙箱推演——模拟决策结果、What-if 分析 |

---

## 六、数据产品与治理（P2）

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| DP1 | **Data Product 概念** — 无显式的数据产品建模 | P2 | 缺失：Owner、SLA、质量承诺、消费者契约、生命周期状态。可以在 DatasetGovernance 基础上升级 |
| DP2 | **Data Contract** — 无 Schema 版本化契约 | P2 | 缺失：Schema 版本、契约自动校验、变更通知机制 |
| DP3 | **领域 Owner** — 本体级别无所有者概念 | P2 | 谁负责这个本体的数据质量？谁批准 Schema 变更？需在组织模型中加入 Domain Owner |
| DP4 | **跨域数据产品发现** — 不同 Ontology 间无统一发现机制 | P2 | 数据产品目录/市场——消费者如何找到跨域的数据产品 |

---

## 七、运维与部署（P2）

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| O1 | **Better Auth Server Docker 部署 + RS256/JWKS** | P2 | 权限治理二期。当前 dev 模式用 HS256 JWT + X-User-Id fallback。生产需独立 Auth Server + 非对称密钥 |
| O2 | **LLM 辅助策略生成** | 远期 | Cedar 策略的 LLM 辅助编写——自然语言描述权限需求→生成 Cedar 策略→人工审核 |
| O3 | **标记血缘传播** | 远期 | Marking 从数据源自动继承到所有派生数据产品 |

---

## 八、实时流处理 Agent（P2-远期）

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| R1 | **真流处理** — Outbox 是 1s 轮询不是事件驱动 | P2 | 需要 Kafka 作为 AI Agent 实时上下文来源。当前 CDC 管道仅用于外部数据接入 |
| R2 | **Streaming Lakehouse** — 无 CDC→Kafka→Flink→Iceberg 的实时链路 | P2 | SeaTunnel CDC 可做但当前主要用批模式 |
| R3 | **Flink Agents 方向** | 远期 | 流处理引擎直接作为 Agent 运行时——评估 Gaia 是否需要这个方向 |
| R4 | **A2A（Agent-to-Agent）协议** | 远期 | Agent 之间如何通信协作。需等 MCP 生态成熟后再评估 |

---

## 九、文档与体验（持续）

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| D1 | 对话式建模体验打磨 | P2 | Capability 按需加载改条件注入 + prepare_tools 按场景过滤工具 |
| D2 | CLAUDE.md 服务清单与目录结构同步 | P2 | 当前以 implementation-status.md 为准，CLAUDE.md 概览滞后 |
| D3 | 全链路血缘与来源追踪 | 远期 | 数据来自哪个系统/区域/业务单元、如何加工映射、是否可写回、权威来源 |

---

## 统计

| 优先级 | 数量 | 关键项 |
|--------|------|--------|
| P0 | 1 | CC1 统一上下文层 |
| P1 | 10 | CC2-CC4 Catalog/质量/管道 MCP · DE1 NL→Pipeline · DE2 管道自监控 · DE5 管道变换接入 · SR1-SR4 语义检索+GraphRAG · T1 ApprovalStore Redis |
| P2 | 12 | CC5-CC6 术语表/多模态 · DE3-DE4 自愈/影响分析 · DE6 投影回调 · SR5-SR6 可视化/embedding迁移 · T2-T4 高危确认/elicitation/工具过滤 · DP1-DP4 数据产品 · O1 Auth Server · R1-R2 真流处理 |
| 远期 | 7 | CC7 AI资产发现 · T5-T6 函数/场景 · O2-O3 LLM策略/标记传播 · R3-R4 Flink Agents/A2A · D3 血缘 |
