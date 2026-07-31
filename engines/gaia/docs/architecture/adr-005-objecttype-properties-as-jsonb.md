# ADR-005: ObjectType.properties 使用 JSONB 存储（后续可降级为关系表）

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳（含演进触发条件） |
| **决策日期** | 2026-05（架构 v5 终稿） |
| **影响层** | `core/models/ontology.py`（ObjectTypeModel.properties JSONB 列）、`layers/metadata/PostgresMetaStore` |
| **相关 ICD** | ICD-01 PostgresMetaStore（create_object_type 含嵌套 properties） |
| **关联文档** | `architecture_plan.md` §3.1.1 关键设计模式、ADR-004（PG 存元数据）、`docs/architecture/architecture_overview.md` §4.2 表结构 |

---

## 背景

ObjectType 拥有 N 个 PropertyDef（属性定义）。属性定义需要在元数据层持久化，有两种存储形态：

| 形态 | 实现 | 优势 | 劣势 |
| ---- | ---- | ---- | ---- |
| **JSONB 内联** | `object_types.properties JSONB` 列存整个属性数组 | 灵活迭代、单次查询取全量、schema 演进无成本 | 属性级查询/审计/索引弱 |
| **独立关系表** | `properties` 表，`object_type_id` FK 关联 | 属性级粒度查询、可独立索引/审计 | 初期开发效率低、JOIN 开销 |

项目初期 ObjectType 频繁迭代（属性增删改、类型调整），且查询模式主要是「按 ObjectType 取全量属性」（查询路由、权限校验、前端渲染），属性级独立查询需求少。

## 决策

**ObjectType.properties 当前用 JSONB 存储（`object_types.properties` 列），后续按触发条件降级为独立关系表。**

### 1. 初期灵活性优先

- 属性结构频繁变动，JSONB 无需 ALTER TABLE，schema 演进零成本
- 查询路径主要是 `get_object_type()` 一次性返回 ObjectType + 全部 properties（eager load），JSONB 内联避免 JOIN
- 符合「先走通再完美」原则（CLAUDE.md 第三原则）

### 2. JSONB 能力满足当前查询

- PG JSONB 支持 `@>`/`?`/路径查询，可做简单的属性存在性检查
- 配合 GIN 索引可加速 JSONB 查询
- 属性级过滤在运行时走 Doris（结构化属性）或 object_state（操作态），不在元数据层做

### 3. 演进路径明确

当触发条件满足时，将 `properties` JSONB 拆分为独立 `properties` 表（`object_type_id` FK + `api_name` 唯一约束），保持对外 API 不变（`get_object_type()` 仍返回 ObjectType 含嵌套 properties），仅改 PostgresMetaStore 内部实现。

### 4. 同模式应用于其他半结构化字段

同样的 JSONB 策略应用于：`parameters` / `rules` / `submission_criteria`（ActionType）、`constraints`（ValueType）、`fields`（Struct）、`extends_interface_ids`（InterfaceType）、`effect_config`/`payload`（outbox）。这些字段的共同特点：结构灵活、整体读写、无独立查询需求。

## 后果

### 正面

- **迭代速度快**：属性增删改无需迁移，适合本体快速演进阶段
- **查询简单**：`get_object_type()` 单次查询返回完整 ObjectType，无 N+1 问题
- **一致性简单**：ObjectType 与其 properties 在同一行，原子更新

### 负面 / 已知限制（技术债，显式管理）

- **属性级查询弱**：无法高效做「查找所有 indexed=True 的属性」「按 data_type 统计」等查询（需解 JSONB）。当前无此需求
- **属性级审计弱**：无法单独追踪某个属性的变更历史。当前靠 ObjectType 整体 updated_at
- **属性级约束弱**：`api_name` 在 ObjectType 内唯一靠应用层校验（`get_object_type` 查重 + UNIQUE 约束无法直接作用于 JSONB 元素），有重复隐患（见 CLAUDE.md 错误模式 #3/#6）
- **JSONB 字段丢失风险**：ORM 写 JSONB 时若未 `flag_modified`，可能静默丢失变更（见 CLAUDE.md 错误模式 #7、transaction-management-best-practices.md）

## 替代方案（否决）

| 方案 | 否决原因 |
| ---- | -------- |
| **直接建关系表（properties 表）** | 初期 ObjectType 频繁迭代，每次属性变更需 ALTER + 迁移，开发效率低；JOIN 增加查询路径复杂度。列为演进目标而非起点 |
| **纯文档数据库（MongoDB）** | 查询能力弱于 PG JSONB（无 GIN/路径查询的组合）；引入新组件增加运维成本；事务性弱于 PG。PG JSONB 已能覆盖文档型场景 |

## 演进触发条件（拆分为关系表）

满足以下**任一**条件时，启动 JSONB → 关系表迁移：

1. **ObjectType 数量 > 100 且属性变更频繁**（每周 > 10 次）——此时 JSONB 写放大和迁移成本超过关系表
2. **出现属性级查询需求**（如「全局查找所有 VECTOR 类型属性」「按 indexed 状态批量重建索引」）——JSONB 无法高效表达
3. **需要属性级审计/版本控制**——JSONB 无法单独追踪属性变更
4. **JSONB 字段丢失 bug 反复出现**——关系表 schema 强约束可根治

迁移时保持对外 API 不变，仅改 PostgresMetaStore 内部实现 + 补 Alembic 迁移。

## 回归条件

本决策是「当前方案 + 演进路径」的组合，回归即触发演进（拆分关系表），见上节触发条件。

## 修订记录

- **2026-05 初始决策**：架构 v5 终稿，properties 用 JSONB，后续可降级
- **2026-07**：当前未触发任何演进条件，JSONB 方案持续有效；`_filter_dict_to_sql` 技术债（参数化绑定 + properties 白名单）列为后续 PR
