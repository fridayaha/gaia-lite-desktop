# ADR-011: Action P1 补全（上下文注入、CDL 快照、Link mutation、三层权限、版本管控、副作用扩展）

**状态**: 已实施（2026-06-22）
**关联**: [action-architecture.md](./action-architecture.md) · [action-loop-design.md](./action-loop-design.md) · [adr-010-ontology-hitl.md](./adr-010-ontology-hitl.md)
**对标**: Palantir Foundry Action (OSv2) — OntologyEdit / Transactional Outbox / 三层权限 / CDL

## 背景

[Action 完整待补清单](.) 对照 Palantir Foundry Action 三阶全解后，识别出 27 项 P1 缺口，分布在底层内核、OMA 建模、Workshop 前端三层。本 ADR 记录这批补全的关键决策。

## 决策

### 1. 上下文注入：ActionContext 贯穿规则引擎与权限

新增 `ActionContext` schema（current_user / current_timestamp / workspace_id / selected_object / user_roles），由 route 层从 `X-User-Id` / `X-Workspace-Id` / `X-User-Roles` 请求头构造（MVP，Sprint 3 接 Principal）。

- `ActionRuleEngine.evaluate` 与 `evaluate_submission_criteria` 接收 context，把内置变量注入 simpleeval 命名空间，规则表达式可引用 `currentUser` / `selectedObject['owner']` 等。
- `ParameterValidator.resolve_defaults` 按 `default_source`（current_user / current_timestamp / workspace_id / selected_object_field）从 context 填充缺失参数。

**理由**：Palantir Action 内置全局变量是其规则引擎从「玩具」变「可用」的关键；不引入 context，规则只能引用入参，无法表达「仅工单创建人可关闭」这类业务约束。

### 2. CDL 变更前后快照：execution_log 加 before/after_snapshot

`ActionExecutionLogModel` 加 `before_snapshot` / `after_snapshot` JSONB 列。ActionService 在 OCC 写入前采集 before（对 UPDATE/DELETE 调 `get_object_state`），写入后采集 after，同事务落盘。

**理由**：原 execution_log 只存 mutations 意图，无法回答「这个对象被谁从什么值改成什么值」——合规场景不可用。before/after 全字段快照是 Palantir CDL 的核心。

### 3. Link mutation：独立 object_links 表，非 JSONB 内嵌

新增 `ObjectLinkModel`（object_links 表），RELATE/UNRELATE/CLEAR_LINKS mutation 写入此表，与 object_state 同 PG 事务。

**理由**：把 Link 存进 object_state 的 properties JSONB 会让关系查询无法索引，且 LinkTraversalService（Sprint 3）需要读一致的关系图。独立表 + (link_type, source, target) 唯一约束 + 索引是最小可行方案。

### 4. 三层权限：MVP 用 parameters.permissions 约定，不新建表

新建 `ActionAuthorizer` 服务，实现三层：
1. 执行权限（roles allowlist + 动态 condition）
2. 行级写权限（复用 catalog.check_access，per-object 过滤契约先到位）
3. 参数级权限（sensitive_params 角色白名单）

权限配置存在 `ActionType.parameters.permissions` JSONB 子结构里，**不新建 ORM 表**。

**理由**：Sprint 3 才接真实 Principal/RBAC，现在建权限表会返工。用 JSONB 约定让权限配置「能存能查能校验」，未配置 = open access（向后兼容），Sprint 3 替换 internals 不影响调用方。

### 5. 副作用扩展：sub_action + kafka_topic，复用 OutboxExecutor

`ActionEffectConfig.type` 从 `webhook | write_back` 扩展到 `webhook | write_back | sub_action | kafka_topic`。OutboxExecutor 加两个分支：
- `_execute_sub_action`：调 ActionService.execute_action，idempotency_key 派生自 outbox id 防环
- `_publish_kafka`：aiokafka 发送，未安装时 graceful degrade

**理由**：sub_action 是 Palantir 链式编排（审批→自动更新）的物质基础；kafka_topic 让 Action 副作用能进事件流（ActionSyncService 已用 Kafka 做索引同步，副作用却不能发，是明显割裂）。

### 6. ActionType 版本管控：action_type_versions 历史快照表

新增 `ActionTypeVersionModel`。define/update 自动出版本快照，rollback 用历史 snapshot 覆盖当前配置并追加新版本（rollback 本身可审计、可逆）。新增 `preview_action` dry-run 方法 + route。

**理由**：原改 ActionType 直接覆盖，无版本追溯；OMA 调试面板（preview）是开发期排错的高 ROI 能力。

### 7. submission_criteria 从死字段接入规则引擎

`ActionTypeCreate.submission_criteria` 从 `dict[str, Any]` 升级为 `list[SubmissionCriterion] | dict`（union 兼容旧 dict）。ActionService 归一化为 `list[SubmissionCriterion]`，交给 `evaluate_submission_criteria` 评估。

**理由**：字段原本是死字段（存了不用），要么接入要么删。接入让全局提交校验（如「库存>0 且 状态=open」）可用，对齐 Palantir OMA 模块5。

### 8. 前端：类型化控件 + onApplied 回调 + 测试体系

- 抽出 `lib/actionForm.ts`（coerceValue / extractParamDefs / controlKindFor 纯函数）
- `ActionParameterField` 组件按 data_type + enum_values + object_type_ref 渲染 checkbox/select/date/datetime/text
- `ExecuteActionDialog` 加 `onApplied` 回调（read-your-writes 刷新）+ 展示 forbidden_objects
- `ObjectDetailPanel` 动作区加「执行」按钮
- `ActionsOverview` 修复上下文断裂（ot id → api_name 自动解析，去掉手动输入）
- 新增 Vitest + @testing-library 测试体系，26 个前端测试

## 迁移

已并入 Alembic 初始 revision `9575abae4046_initial_schema_baseline`（原 `scripts/migrations/20260622_action_p1.sql` 已随 Alembic 接入删除）：
- action_types 加 version/operation_kind/batch_enabled
- action_execution_logs 加 before_snapshot/after_snapshot
- object_state 加 modified_by
- 新建 object_links、action_type_versions 表
- 回填现存 ActionType 的 v1 版本快照（全新库无数据，无需回填）

## 后续（P2+）

本 ADR 未覆盖的 P1 之外项仍按原路标：
- Batch Action 分片调度（P2）
- Scenario 沙箱事务（P2）
- Function-Backed Action / OntologyEdit 沙箱（P4）
- 行级 RBAC 真实实现（Sprint 3 Principal）
- ActionType 灰度发布（P2）
