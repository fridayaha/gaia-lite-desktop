# 工程规范

> 本节包含 Gaia 的工程实践标准、性能验证指南、集成指南和事故复盘等。

## 规范与指南

- [工程原则与最佳实践](engineering_principles_and_best_practices) — 开发工作流、测试策略、事务管理等核心工程规范
- [前端标准](frontend-standards) — 类型安全、样式体系、A11y、测试策略
- [前端最佳实践](frontend-best-practices) — 组件设计、状态管理、性能优化
- [事务管理最佳实践](transaction-management-best-practices) — 多步写入的事务边界与 OCC 模式
- [外部系统数据获取最佳实践](external-system-data-fetching-best-practices) — 列表/详情页加载外部系统状态的原则与避坑（懒加载、并发限流、批量、只刷新非终态）
- [AI 集成指南](ai-integration-guide) — AG-UI Agent 与 pydantic-ai 使用指南
- [Agent 对接指南](agent-integration-guide) — 外部 Agent 通过 MCP 对接 Gaia 的操作手册

## 验证与测试

- [验证指南](verification-guide) — 服务健康检查、端到端测试、常见问题排查
- [权限 E2E 测试策略](permission-e2e-test-strategy)
- [权限 Phase2 落地指南](permission-phase2-landing-guide)
- [权限路线图与原则](permission-roadmap-and-principles)

## 部署与运维

- [部署指导书](deployment-guide) — k3s 集群部署 Gaia 的完整步骤（环境确认、镜像加速、配置、验证、排查）
- [部署 Runbook](deployment-runbook) — 从干净机器部署 Gaia 的运维手册

## 基准与评测

- [评测基准原则](research-benchmark-principles)

## 事故复盘

- [SeaTunnel Iceberg REST 互操作](seatunnel-iceberg-rest-interop-postmortem)
- [StarRocks SeaTunnel Dry-run](starrocks-seatunnel-dryrun)
- [CDC Spike 报告](cdc-spike-report)
