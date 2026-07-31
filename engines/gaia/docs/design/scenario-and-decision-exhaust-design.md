# 设计指导书：决策捕获与反馈飞轮 + Scenario 沙箱

> **范围**：基于 Gaia 当前代码库，设计并实现两个 Palantir 核心能力——(1) 决策捕获与反馈飞轮（Decision Exhaust），(2) Scenario 沙箱与 What-if 推演。
> **目标**：深度与细节足以支撑直接照此编码。每个改造点标注**文件路径**、**函数签名**、**数据结构**、**测试要求**。
> **前置研究**：[`docs/research/palantir-capability-gap-analysis.md`](../research/palantir-capability-gap-analysis.md)（P0-A 决策捕获、P1-A Scenario）
> **关联文档**：[`docs/architecture/action-architecture.md`](../architecture/action-architecture.md) · [`docs/architecture/adr-011-action-p1.md`](../architecture/adr-011-action-p1.md) · [`docs/architecture/adr-action-mutation-mapping.md`](../architecture/adr-action-mutation-mapping.md)
> **日期**：2026-07-06

---

## 目录

- [〇、现状基线与改造切入点](#〇现状基线与改造切入点)
- [第一部分：Scenario 沙箱与 What-if 推演](#第一部分scenario-沙箱与-what-if-推演)
  - [1. 设计目标与 Palantir 范式对齐](#1-设计目标与-palantir-范式对齐)
  - [1.2 What-if 分析的完整逻辑（核心，必须先理解）](#12-what-if-分析的完整逻辑核心必须先理解)
  - [2. 数据模型设计](#2-数据模型设计)
  - [3. Scenario 读写语义与 overlay 求值](#3-scenario-读写语义与-overlay-求值)
  - [4. Service 层设计](#4-service-层设计)
  - [5. ActionService 改造](#5-actionservice-改造)
  - [6. 查询层改造](#6-查询层改造)
  - [7. API 路由设计](#7-api-路由设计)
  - [8. 前端交互](#8-前端交互)
  - [9. 数据库迁移](#9-数据库迁移)
  - [10. 测试策略](#10-测试策略)
- [第二部分：决策捕获与反馈飞轮](#第二部分决策捕获与反馈飞轮)
  - [11. 设计目标与 Palantir 范式对齐](#11-设计目标与-palantir-范式对齐)
  - [12. 数据模型设计](#12-数据模型设计)
  - [13. 决策物化机制](#13-决策物化机制)
  - [14. 数据版本锚定](#14-数据版本锚定)
  - [15. Writeback webhook 增强](#15-writeback-webhook-增强)
  - [16. 决策结果回流与反馈飞轮](#16-决策结果回流与反馈飞轮)
  - [17. API 路由设计](#17-api-路由设计)
  - [18. 前端交互](#18-前端交互)
  - [19. 数据库迁移](#19-数据库迁移)
  - [20. 测试策略](#20-测试策略)
- [第三部分：实施计划与依赖](#第三部分实施计划与依赖)

---

## 〇、现状基线与改造切入点

### 0.1 已就位的基础设施（Scenario + 决策捕获的底座）

核查代码确认，Gaia 已具备实现这两个能力的全部底座：

| 基础设施 | 位置 | 说明 |
|---------|------|------|
| **`branches` 表 + `BranchModel`** | `core/models/ontology.py:315` | **已建表但完全未接线**！无任何 service/route 引用。这是 Scenario 的天然载体 |
| **`object_state` 表（OCC）** | `core/models/ontology.py:400` | `rid` PK + `version` OCC + `properties` JSONB + `ontology_id`。Scenario 的 overlay 写入点 |
| **`object_links` 表** | `core/models/ontology.py:429` | 关系实例，Scenario 的 RELATE/UNRELATE overlay 写入点 |
| **`action_execution_logs`** | `core/models/ontology.py:337` | 已有 `before_snapshot`/`after_snapshot` JSONB（CDL）、`performed_by`、`idempotency_key`、`read_snapshot_id`。决策捕获的基础 |
| **`outbox` 表** | `core/models/ontology.py:368` | `effect_type`（WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC）+ payload + retry。Writeback webhook 基础 |
| **`analysis_records` 表** | `core/models/ontology.py:481` | 证据链快照（ObjectSet IR + 结果摘要）。决策推理链的雏形 |
| **ActionService.execute_action** | `services/action_service.py:385` | Step 1-12 完整流程，Step 8 写 object_state + Step 9 写 execution_log/outbox + Step 10 commit。Scenario 的改造切入点 |
| **ActionContext** | `core/schemas/action.py:291` | `current_user`/`current_timestamp`/`workspace_id`/`ontology_snapshot_version`/`selected_object`/`user_roles`。需加 `scenario_id` |
| **upsert_object_state (OCC)** | `layers/metadata/postgres_meta_store.py:1171` | CREATE 用 `ON CONFLICT DO NOTHING`，UPDATE 用 `WHERE version = expected`。需加 `scenario_id` 维度 |
| **IcebergStore snapshot** | `layers/dataset/iceberg_store.py:300` | `get_snapshots` + `load_by_ids_as_of(snapshot_id)`。决策版本锚定的数据源 |
| **Alembic migration 基础** | `alembic/versions/` | 3 个 migration 已落地，流程成熟 |

### 0.2 关键约束与红线（不可违反）

| # | 约束 | 来源 |
|---|------|------|
| 1 | **Iceberg 是主数据唯一写入入口**（Action 操作态写 PG object_state 例外） | CLAUDE.md 红线 3 |
| 2 | **object_state 是 Action 的同步写目标**，保证 read-your-writes | action-architecture.md |
| 3 | **OCC：expected_version 不匹配 → ConflictError 409** | action_service.py Step 8 |
| 4 | **Action 原子提交：object_state + execution_log + outbox 同一 PG 事务** | action_service.py Step 10 |
| 5 | **Doris 索引表名带本体前缀 `idx_{ont}__{type}`** | CLAUDE.md 红线 8 |
| 6 | **物理资源命名走 snake_case**（`core/naming.py`） | CLAUDE.md 红线 10 |
| 7 | **schema 变更必须走 Alembic**，autogenerate 产物需人工 review | CLAUDE.md Schema 变更 |
| 8 | **VIRTUAL 目标禁止写入** | CLAUDE.md 红线 9 |
| 9 | **Ontology API 层不吃自然语言** | CLAUDE.md 红线 11 |
| 10 | **多步写入用 `async with self.transaction():` 包裹 + auto_commit=False** | transaction-management-best-practices.md |

### 0.3 设计原则

1. **Scenario 优先于 Global Branching**：Scenario 是本体数据层的增量 fork（运营决策推演），Global Branching 是环境级 fork（开发隔离）。本研究确认 Scenario 价值远高于 Global Branching，**本设计只做 Scenario，不做 Global Branching**。
2. **复用 `branches` 表**：已存在的 `BranchModel` 改造为 Scenario 的载体（语义对齐 Palantir 的 Scenario = 数据 fork），不新建表。
3. **overlay 语义**：Scenario 只存"相对 base 的 edits"，不复制全量数据（copy-on-write），对齐 Palantir。
4. **决策物化是可选的**：不是所有 Action 都物化为 Decision 对象，由 ActionType 配置 `capture_decision` 控制（默认 false，避免破坏现有行为）。
5. **向后兼容**：所有改造在 `scenario_id IS NULL` / `capture_decision=false` 时走原有路径，零行为变更。

---

# 第一部分：Scenario 沙箱与 What-if 推演

## 1. 设计目标与 Palantir 范式对齐

### 1.1 Palantir Scenario 机制（深度研究结果）

**核心定义**（官方《Workshop → Scenarios → Core concepts》）：
> "A Scenario is fork of the data in the Ontology created by applying a set of Actions and evaluating a set of Models. The fork contains only the edits or changes from the base Ontology including modified Object properties, created Objects, deleted Objects, created link types, and deleted link types."

**关键特性**：
1. **增量 fork**：只存 edits，不复制全量（copy-on-write）
2. **不可变**：创建后不可修改，要改就新建（可 duplicate）
3. **= Actions + Models**：Scenario 内应用 Action 改对象 + 评估 Model 预测属性
4. **Scenario-aware 查询**：可在 base + scenario overlay 上求值，多 Scenario 并排对比
5. **限制**：单 Scenario ≤30000 edits、≤50 Actions、加载 ≤10000 对象

**Palantir Scenario API 矩阵**（从 OSDK `OntologyScenario.d.ts` 提取，这是精确的 API 设计参照）：

| 方法 | 路径 | 作用 |
|------|------|------|
| createScenario | `POST /v2/ontologies/{ontology}/scenarios/create` | 创建场景 |
| listScenarioEditedObjectTypes | `GET /scenarios/{rid}/objectTypes/edited` | 列出被编辑的对象类型 |
| listScenarioEditedObjects | `GET /scenarios/{rid}/objects/{ot}/edited` | 列出被编辑的对象（分页） |
| listScenarioEditedLinkTypes | `GET /scenarios/{rid}/objectTypes/{ot}/outgoingLinkTypes/edited` | 列出被编辑的链接类型 |
| listScenarioEditedLinks | `GET /scenarios/{rid}/objects/{ot}/links/{lt}/edited` | 列出被编辑的链接（分页） |
| listScenarioEditedEntityTypes | `GET /scenarios/{rid}/editedEntityTypes` | 汇总所有被编辑的实体类型 |

**Apply Action 的 Scenario 支持**（官方 Apply Action API）：
> `scenarioRid`：The ID of an Ontology scenario to apply the action against.

即 Action 可指定 `scenarioRid`，写入指定 Scenario 而非 main。这是 Scenario 的写入入口。

### 1.2 What-if 分析的完整逻辑（核心，必须先理解）

> ⚠️ 这一节回答的核心问题：**用户到底怎么用 Scenario 做 what-if 分析？数据怎么流动？多个 Action 怎么累积？多 Scenario 怎么对比？**
> 之前的 §1.1 讲了"Scenario 是什么"，这一节讲"Scenario 怎么用"。理解这一节是理解后续所有设计的前提。

#### 1.2.1 一个完整的 what-if 场景 walkthrough

以"航空公司航班调度"为例（对齐 Gaia benchmark 的 flight 场景），用户想分析：

> "如果把 3 个延误航班改签到备用飞机，总成本和准点率会怎样？对比两种改签方案。"

用户的完整操作流程（对应 Palantir Workshop 的 Scenario 工作流）：

```
步骤 1：用户在 Scenario Manager 点「创建」→ 创建 Scenario A「方案一：改签到 B-777」
         （此时 Scenario A 是空的 fork，所有数据 = base 本体数据）

步骤 2：用户在 Object Table 选中航班 F-001，点「改签」Action
         - Action 配置「Apply to Scenario = Scenario A」
         - 填入参数：target_aircraft = B-777
         - Action 执行 → 写入 Scenario A 的 overlay（不是 main！）
         - F-001 在 Scenario A 中 aircraft = B-777，但 main 中 F-001 仍是原飞机

步骤 3：用户继续对 F-002、F-003 重复改签 Action
         - 3 个 overlay 累积在 Scenario A 中
         - 每个 Action 都在 Scenario A 的「当前状态」上叠加（第 2 个 Action 看到第 1 个的效果）

步骤 4：用户在 Scenario Manager 再点「创建」→ Scenario B「方案二：改签到 A-320」
         （Scenario B 也是空的 fork）

步骤 5：用户在 Scenario B 上对同样的 3 个航班改签到 A-320

步骤 6：用户在 Object Table 开启「对比」→ 表格并排显示：
         ┌────────┬──────────┬────────────┬────────────┐
         │ flight │ base飞机  │ A: B-777   │ B: A-320   │
         ├────────┼──────────┼────────────┼────────────┤
         │ F-001  │ B-737    │ B-777 (改) │ A-320 (改) │  ← 只在差异列显示
         │ F-002  │ B-737    │ B-777 (改) │ A-320 (改) │
         │ F-003  │ B-737    │ B-777 (改) │ A-320 (改) │
         │ F-004  │ A-320    │  (未改)    │  (未改)    │  ← 未改的列不显示
         └────────┴──────────┴────────────┴────────────┘

步骤 7：用户看 Metric Card「总成本」和「准点率」→ 这两个指标在两个 Scenario 下各算一次
         - 成本(A) = 基于 Scenario A 的飞机分配重新计算
         - 成本(B) = 基于 Scenario B 的飞机分配重新计算
         - 用户对比两个成本，选最优方案

步骤 8：用户选定方案 A，点「应用到生产」→ Scenario A 的 overlay 重放到 main
         （3 个改签 Action 在 main 上重新执行，main 的 F-001/002/003 更新）

步骤 9：用户丢弃 Scenario B（overlay 删除，不影响 main）
```

#### 1.2.2 What-if 的三个核心机制

上面的 walkthrough 依赖三个机制，缺一不可：

**机制 1：Scenario 作为独立的"假设世界"**

- Scenario 是 base 本体的**增量 fork**：只存"相对 base 改了什么"，未改的对象直接读 base
- Scenario 内的对象状态 = `base 数据 + 该 Scenario 的 overlay`
- 多个 Scenario 互相隔离：Scenario A 的改动对 Scenario B 不可见
- **关键**：Scenario 永不修改 base，base 改了 Scenario 也能感知（读时合并）

**机制 2：Action 写入 Scenario（而非 main）**

- Action 执行时指定 `scenario_id`，mutations 写入该 Scenario 的 overlay
- 同一 Scenario 内连续多个 Action **累积叠加**：第 N 个 Action 看到的是 base + 前 N-1 个 Action 的 overlay
- 这正是 §3.1 写入语义和 §3.2 读取语义配合的结果——每次 Action 写 overlay，每次读取做 base+overlay 合并
- **累积的物理实现**：`object_state` 表里同一 `rid` 在同一 `scenario_id` 下只有一行，新 Action 的 UPDATE 覆盖该行（version+1），不会产生多行

**机制 3：多 Scenario 并排对比**

- 查询时传入多个 `scenario_ids`，返回每个对象在 base + 每个 Scenario 下的属性值
- 只在**差异列**显示（未改的属性不重复显示）——这是前端渲染逻辑，后端返回全量
- **微妙规则**（来自 Palantir 官方澄清）：
  - `load_from`（单 Scenario）决定**表格里出现哪些对象**——Scenario 内新建的对象只有作为 `load_from` 才会出现
  - `compare_against`（多 Scenario）决定**并排显示哪些列**——只对比属性值，不影响对象列表
  - 例如：Scenario A 新建了对象 F-999，若 `load_from=base`、`compare=A`，F-999 不会出现（base 没有）；若 `load_from=A`，F-999 出现

#### 1.2.3 Model 评估在 what-if 中的角色（可选，依赖 P1-C Functions）

Palantir 的 Scenario = Actions + **Models**。Model 是"给定对象属性估算其他属性"的函数（预测/优化）。

在 what-if 中的用法：
```
用户改了 F-001 的飞机 = B-777（Action 写 overlay）
  → 触发 Model「成本预测模型」在 Scenario A 上评估
  → Model 读 Scenario A 的 F-001（aircraft=B-777），预测 cost = 85000
  → 预测值写入 Scenario A 的 F-001.cost_estimate overlay
  → Metric Card 显示 85000
```

**Gaia 的 MVP 决策**：Model 评估依赖 P1-C Functions（尚未实现），本设计**预留接口但不实现**。
- 替代方案：用户用**派生属性 / Expression ValueSource** 在 Action 规则里计算（如改签 Action 的 rule 里算 cost）
- 这样 Scenario 的 what-if 闭环不依赖 Model 也能跑通（只是预测逻辑写在 Action rule 里而非独立 Model）

#### 1.2.4 Scenario 与 Gaia 现有 preview_action 的区别（重要澄清）

Gaia 已有 `ActionService.preview_action`（干跑，不落库）。它和 Scenario 的关系常被混淆，这里澄清：

| 维度 | preview_action（已有） | Scenario（新增） |
|------|:---:|:---:|
| 目的 | 看**单个 Action** 会发生什么 | 构建**一组假设**的完整世界 |
| 持久化 | 不落库 | 落 overlay（Scenario 内） |
| 多 Action 累积 | ❌ 每次独立 | ✅ 累积叠加 |
| 多方案对比 | ❌ | ✅ 并排 |
| 可查询 | ❌ 一次性结果 | ✅ 任意查询/聚合 |
| 可 apply 到生产 | ❌ | ✅ 重放到 main |

**关系**：preview 是"一次性沙箱"，Scenario 是"持久化沙箱"。两者互补：
- 用户可先 preview 一个 Action 看效果，满意了再 apply 到 Scenario
- Scenario 内也可对后续 Action 做 preview（preview 支持 `scenario_id`，在 Scenario 当前状态上干跑）

**设计决策 D9**：`preview_action` 增加 `scenario_id` 参数，干跑时基于 Scenario 当前状态（base+overlay）求值 before_snapshot，但不写 overlay。这样用户在 Scenario 内也能预览"再加一个 Action 会怎样"。

#### 1.2.5 What-if 的数据流图（端到端）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        base 本体（main, scenario_id=NULL）           │
│  object_state: F-001{aircraft:B-737, cost:70k}, F-002{...}, ...      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 读（base 数据）
               ┌───────────────┴───────────────┐
               ▼                               ▼
┌──────────────────────────┐        ┌──────────────────────────┐
│ Scenario A overlay       │        │ Scenario B overlay       │
│ F-001{aircraft:B-777}    │        │ F-001{aircraft:A-320}    │
│ F-002{aircraft:B-777}    │        │ F-002{aircraft:A-320}    │
│ F-003{aircraft:B-777}    │        │ F-003{aircraft:A-320}    │
└───────────┬──────────────┘        └───────────┬──────────────┘
            │ 读时合并                         │ 读时合并
            ▼                                  ▼
┌──────────────────────────┐        ┌──────────────────────────┐
│ Scenario A 视图          │        │ Scenario B 视图          │
│ F-001{aircraft:B-777,    │        │ F-001{aircraft:A-320,    │
│       cost:70k(base)}    │        │       cost:70k(base)}    │
│ F-002{aircraft:B-777,...}│        │ F-002{...}               │
└──────────────────────────┘        └──────────────────────────┘
            │ 聚合（成本/准点率）             │ 聚合
            ▼                                  ▼
         成本A=85k                         成本B=78k

  并排对比：A(85k) vs B(78k) → 选 B → apply B 到 main
```

**关键**：overlay 只存"改了的属性"，未改属性读时从 base 补（COALESCE）。聚合（如总成本）在合并后的视图上计算，不是在 overlay 上单独算。

#### 1.2.6 "累积叠加"的精确语义（易错点）

这是实现时最容易出错的地方，单独说明：

**场景**：Scenario A 内连续执行两个 Action：
- Action 1：UPDATE F-001 {aircraft: B-777}
- Action 2：UPDATE F-001 {cost: 85000}（基于 aircraft=B-777 重新算成本）

**object_state 表的演变**：
```
执行前：
  base 行: F-001 (scenario_id=NULL, version=5, {aircraft:B-737, cost:70k})
  Scenario A 行: 无

Action 1 后：
  base 行: F-001 (scenario_id=NULL, version=5, {aircraft:B-737, cost:70k})  ← 不变
  Scenario A 行: F-001 (scenario_id=A, version=1, {aircraft:B-777, cost:70k})
  （overlay 行 = base properties 的副本 + aircraft 覆盖；cost 仍是 base 的 70k）

Action 2 后：
  base 行: F-001 (scenario_id=NULL, version=5, {...})  ← 仍不变
  Scenario A 行: F-001 (scenario_id=A, version=2, {aircraft:B-777, cost:85000})
  （overlay 行的 cost 被覆盖为 85000；aircraft 保持 B-777）
```

**要点**：
1. 每个 Action 的 UPDATE 读取的是 **Scenario 当前视图**（base + 上一轮 overlay），所以 Action 2 看到 aircraft=B-777
2. overlay 行是**单行累积覆盖**，不是每个 Action 一行（Action 2 的 UPDATE 作用于 Action 1 产生的 overlay 行，version 递增）
3. base 行的 version 始终是 5（Scenario 不改 base）——但 Action 2 的 `expected_version` 校验的是 **base 行的 version**（D6 决策），确保用户基于的 base 数据没被别人改过
4. 若 Action 1 和 Action 2 之间 base 的 F-001 被别人改了（version 5→6），Action 2 的 expected_version=5 校验失败 → ConflictError。这是正确行为："你基于的假设世界的基础变了"

#### 1.2.7 Gaia what-if 的 API 交互序列

把上面的逻辑映射到 Gaia 的 API 调用序列：

```python
# 步骤 1: 创建 Scenario A
POST /ontologies/{ont}/scenarios
  body: {name: "plan-a-b777", display_name: "方案一：B-777"}
  → {id: "scn_001", status: "ACTIVE"}

# 步骤 2: 在 Scenario A 上改签 F-001
POST /actions/ReassignFlight/execute
  body: {parameters: {flight_id: "F-001", target_aircraft: "B-777"},
         scenario_id: "scn_001"}
  → {status: COMPLETED, affected: {F-001: overlay_v1}}

# 步骤 3: 在 Scenario A 上改签 F-002（累积）
POST /actions/ReassignFlight/execute
  body: {parameters: {flight_id: "F-002", target_aircraft: "B-777"},
         scenario_id: "scn_001"}
  → {status: COMPLETED, affected: {F-002: overlay_v1}}
  （F-001 的 overlay 不受影响，仍在 Scenario A）

# 步骤 4: 创建 Scenario B
POST /ontologies/{ont}/scenarios
  body: {name: "plan-b-a320", display_name: "方案二：A-320"}
  → {id: "scn_002"}

# 步骤 5: 在 Scenario B 上改签（独立于 A）
POST /actions/ReassignFlight/execute
  body: {parameters: {flight_id: "F-001", target_aircraft: "A-320"},
         scenario_id: "scn_002"}
  → ...

# 步骤 6: 单 Scenario 视图查询（load_from=Scenario A）
POST /scenarios/scn_001/objects/Flight/query
  body: {filters: {status: "DELAYED"}}
  → [F-001{aircraft:B-777,cost:70k}, F-002{aircraft:B-777,...}, ...]
  （返回 base+overlay 合并后的数据）

# 步骤 7: 多 Scenario 并排对比
POST /scenarios/scn_001/objects/compare
  body: {compare_scenario_ids: ["scn_002"],
         object_type: "Flight",
         filters: {status: "DELAYED"}}
  → [{rid: "F-001",
      base: {aircraft: "B-737", cost: 70000},
      scenarios: {scn_001: {aircraft: "B-777"},   ← 只含改的属性
                  scn_002: {aircraft: "A-320"}}},
     ...]

# 步骤 8: 聚合（Scenario A 的总成本）——复用现有 aggregate，传 scenario_id
POST /objects/{ont}/Flight/aggregate
  body: {aggregation: {type: "SUM", field: "cost"},
         scenario_id: "scn_001"}
  → {value: 255000}  （在 base+overlay 合并视图上聚合）

# 步骤 9: 选定方案 A，应用到 main
POST /scenarios/scn_001/apply
  body: {dry_run: false}
  → {applied: true, action_results: [{F-001: ok}, {F-002: ok}, {F-003: ok}]}
  （overlay 的 3 个改签重放到 main，main 的 F-001/002/003 更新）

# 步骤 10: 丢弃 Scenario B
DELETE /scenarios/scn_002
  → （overlay 删除，main 不受影响）
```

#### 1.2.8 What-if 的边界（什么能做、什么不能做）

对齐 Palantir 的限制（官方文档），Gaia 的 Scenario what-if 有以下边界：

**能做**：
- Scenario 内 CREATE/UPDATE/DELETE 对象 + RELATE/UNRELATE 链接
- 同 Scenario 多 Action 累积
- 多 Scenario 并排对比
- 任意查询/聚合在 Scenario 视图上求值
- Scenario apply 到 main（重放）
- Scenario duplicate（复制为新的可编辑 Scenario）

**不能做（对齐 Palantir 限制）**：
- ❌ 单 Scenario 超过 30000 edits / 50 Actions（软限制，超限报 400）
- ❌ Scenario 内查询超过 10000 对象（分页强制）
- ❌ 跨 Scenario 的 Function 调用（一个 Function 不能同时读多个 Scenario）
- ❌ 带外部 side effect 的 Action（WEBHOOK_WRITEBACK）在 Scenario 内执行——因为 side effect 会影响真实系统，违反"假设"语义。**设计决策 D8**：Scenario 内的 Action 禁止 WEBHOOK_WRITEBACK effect，只允许本体 overlay 写入
- ❌ Scenario 不可变后（IMMUTABLE）继续写入

### 1.3 Gaia 设计目标

| 目标 | Palantir 对齐 | Gaia 实现 |
|------|:---:|--------|
| 创建 Scenario（命名 + 描述 + base branch） | ✅ | 复用 `branches` 表，`is_main=false` |
| 在 Scenario 上应用 Action（写 overlay） | ✅ | ActionService.execute_action 增加 `scenario_id` 参数 |
| 查询 Scenario 内对象（base + overlay 求值） | ✅ | ObjectQueryService 增加 `scenario_id` 参数 + overlay 合并 |
| 列出 Scenario 被编辑的实体 | ✅ | 新增 `/scenarios/{id}/edited` 端点 |
| 多 Scenario 并排对比 | ✅ | 查询返回多 Scenario 属性数组 |
| Scenario 不可变 + duplicate | ✅ | status=IMMUTABLE + duplicate API |
| Model 评估（Function-backed） | 🟡 后置 | 依赖 P1-C Functions，本设计预留接口不实现 |
| apply Scenario 到 main | ✅ | 新增 `apply_scenario` 操作（把 overlay 重放到 main） |
| discard Scenario | ✅ | 删除 overlay 数据 |

### 1.3 关键设计决策

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | Scenario 数据存储 | **复用 `object_state` + `object_links`，加 `scenario_id` 列** | 不复制全量数据，overlay 语义；base 行 `scenario_id IS NULL`，Scenario 行 `scenario_id = X` |
| D2 | Scenario 元数据 | **复用 `branches` 表**（已存在！） | `is_main=true` 是 main，`is_main=false` 是 Scenario；加 `display_name`/`description`/`base_branch_id`/`status` 字段 |
| D3 | Scenario 不可变性 | **status=IMMUTABLE 后禁止写入** | 对齐 Palantir；要改就 duplicate |
| D4 | overlay 查询求值 | **PG 层 UNION ALL + COALESCE** | base 行 LEFT JOIN scenario 行，scenario 行覆盖 base |
| D5 | apply Scenario 到 main | **重放 Scenario 的 edits 到 main**（逐个 Action 重放） | 对齐 Palantir "apply scenario" 概念；利用现有 OCC |
| D6 | Scenario 内 Action 的 OCC | **base 对象的 expected_version 仍对 base 校验** | Scenario 不改 base，base 改了 Scenario 仍可读（但 apply 时可能冲突） |
| D7 | Model 评估 | **预留 `evaluate_model` 接口，MVP 不实现** | 依赖 P1-C Functions；Scenario 结构已支持；what-if 的预测逻辑 MVP 用 Action rule 的 Expression ValueSource 替代（见 §1.2.3） |
| D8 | Scenario 内禁止外部 side effect | **Scenario 内 Action 禁止 WEBHOOK_WRITEBACK effect** | side effect 影响真实系统，违反"假设"语义；对齐 Palantir 限制（见 §1.2.8） |
| D9 | preview 支持 Scenario | **preview_action 增加 scenario_id 参数** | 在 Scenario 当前状态上干跑，不写 overlay；与 Scenario 互补（见 §1.2.4） |

## 2. 数据模型设计

### 2.1 改造 `BranchModel`（复用为 Scenario 载体）

**文件**：`src/ontology/core/models/ontology.py`（`BranchModel`，约 line 313）

**现状**：
```python
class BranchModel(Base):
    __tablename__ = "branches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(...)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(...)
    updated_at: Mapped[datetime] = mapped_column(...)
```

**改造后**：
```python
class BranchModel(Base):
    """Scenario / branch carrier (reused for Palantir Scenario semantics).

    is_main=True rows are the production "main" branch (one per ontology).
    is_main=False rows are Scenarios — incremental forks of main used for
    what-if analysis. A Scenario stores only its edits (overlay) in
    object_state/object_links (scenario_id column); base data lives in
    scenario_id IS NULL rows.

    Lifecycle: ACTIVE (writable) → IMMUTABLE (frozen, read-only) →
    APPLIED (overlay has been replayed to main) / DISCARDED (deleted).
    """
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    # Scenario lineage: which branch this scenario forked from (main by default).
    base_branch_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("branches.id", ondelete="SET NULL"), nullable=True
    )
    # ACTIVE (writable) / IMMUTABLE (frozen) / APPLIED / DISCARDED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # Who created this scenario (audit).
    created_by: Mapped[str] = mapped_column(String(255), default="system")
    # Edit count limit guard (Palantir: 30000 edits, 50 actions).
    edit_count: Mapped[int] = mapped_column(default=0)
    action_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="branches")
    base_branch: Mapped["BranchModel | None"] = relationship(
        remote_side="BranchModel.id", foreign_keys=[base_branch_id]
    )

    __table_args__ = (
        UniqueConstraint("ontology_id", "name", name="uq_branches_ontology_name"),
    )
```

**要点**：
- `base_branch_id` 自引用 FK，记录 Scenario 从哪个 branch fork（默认 main）
- `status` 生命周期：`ACTIVE`（可写）→ `IMMUTABLE`（冻结）→ `APPLIED`/`DISCARDED`
- `edit_count`/`action_count` 软限制（对齐 Palantir 的 30000/50 限制，超限报错）
- `uq_branches_ontology_name` 唯一约束（同本体下 Scenario 名唯一）

### 2.2 改造 `ObjectStateModel`（加 scenario_id 维度）

**文件**：`src/ontology/core/models/ontology.py`（`ObjectStateModel`，约 line 386）

**改造**：加 `scenario_id` 列，并把主键改为复合主键 `(rid, scenario_id)`。

```python
class ObjectStateModel(Base):
    __tablename__ = "object_state"
    # 复合主键：同一对象在 main (scenario_id=NULL) 和多个 Scenario 各有一行
    rid: Mapped[str] = mapped_column(String(128), primary_key=True)  # Palantir RID
    scenario_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True, nullable=True
    )
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(default=1)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    ontology_id: Mapped[str] = mapped_column(...)
    modified_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(...)
    updated_at: Mapped[datetime] = mapped_column(...)
```

**⚠️ 关键迁移风险**：现有 `object_state` 的 PK 是单列 `rid`，改为复合 PK 是破坏性变更。迁移策略见 §9.1。

**索引**：
```python
__table_args__ = (
    # 复合 PK 已隐含 (rid, scenario_id) 索引
    Index("ix_object_state_type_scenario", "object_type_api_name", "scenario_id"),
)
```

### 2.3 改造 `ObjectLinkModel`（加 scenario_id 维度）

**文件**：`src/ontology/core/models/ontology.py`（`ObjectLinkModel`，约 line 429）

```python
class ObjectLinkModel(Base):
    __tablename__ = "object_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(...)
    scenario_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("branches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    link_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(...)

    __table_args__ = (
        # 同一 Scenario 内关系唯一；base (scenario_id=NULL) 关系唯一
        UniqueConstraint(
            "link_type_api_name", "source_rid", "target_rid", "scenario_id",
            name="uq_object_links_scenario",
        ),
    )
```

**注意**：原 `uq_object_links` 约束需改为含 `scenario_id`。`scenario_id` 是可空列，PG 中 `NULL` 在 UNIQUE 约束里互不冲突（符合 SQL 标准），所以同对象在 base 和不同 Scenario 可以各有关系行。

### 2.4 改造 `ActionExecutionLogModel`（加 scenario_id + 数据版本锚定）

**文件**：`src/ontology/core/models/ontology.py`（约 line 337）

```python
class ActionExecutionLogModel(Base):
    __tablename__ = "action_execution_logs"
    # ... 现有字段不变 ...
    # 新增：Action 执行在哪个 Scenario（NULL = main）
    scenario_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("branches.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 新增：决策时刻的数据版本锚定（Iceberg snapshot_id，决策回放用）
    # 注：read_snapshot_id 字段已存在但目前未充分使用，本设计激活它
    # read_snapshot_id: 已有，记录决策时刻 Iceberg snapshot
```

**要点**：`scenario_id` 记录 Action 在哪个 Scenario 执行；`read_snapshot_id` 激活为决策数据版本锚定（见 §14）。

### 2.5 新增 `ScenarioEditIndexModel`（可选，加速 edited 查询）

**目的**：Palantir 有 `listScenarioEditedObjects` API，需要快速查询"某 Scenario 改了哪些对象"。若无索引表，需扫 `object_state WHERE scenario_id=X`，数据量大时慢。

```python
class ScenarioEditIndexModel(Base):
    """Index of edits within a Scenario (accelerates listScenarioEdited* queries).

    Mirrors Palantir's edited-objects API. One row per (scenario, object_type,
    rid) edited. INSERT/UPDATE/DELETE overlay writes maintain this index.
    """
    __tablename__ = "scenario_edit_index"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    scenario_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ontology_id: Mapped[str] = mapped_column(...)
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    rid: Mapped[str] = mapped_column(String(128), nullable=False)
    edit_type: Mapped[str] = mapped_column(String(10), nullable=False)  # CREATED/UPDATED/DELETED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("scenario_id", "object_type_api_name", "rid", name="uq_scenario_edit"),
        Index("ix_scenario_edit_type", "scenario_id", "object_type_api_name"),
    )
```

**决策**：MVP 阶段可不用此表，直接查 `object_state WHERE scenario_id=X`（PG 索引足够快）。数据量上来后再加。**本设计标注为可选**。

## 3. Scenario 读写语义与 overlay 求值

### 3.1 写入语义（Action 在 Scenario 上执行）

当 `ActionExecutionRequest` 携带 `scenario_id` 时：

| Mutation 类型 | base (scenario_id=NULL) | Scenario (scenario_id=X) |
|--------------|:---:|:---:|
| CREATE_OBJECT | 不动 | INSERT 新行 (scenario_id=X) |
| UPDATE_PROPERTY | 不动 | UPSERT overlay 行 (scenario_id=X)，记录 base 版本 |
| DELETE_OBJECT | 不动 | INSERT overlay 行 mark `__deleted=true` |
| RELATE | 不动 | INSERT object_links (scenario_id=X) |
| UNRELATE | 不动 | INSERT object_links (scenario_id=X) mark `__deleted=true` 或删除该 scenario 的 overlay 行 |
| CLEAR_LINKS | 不动 | 同 UNRELATE 批量 |

**关键**：**Scenario 写入永不修改 base 行**。这是 overlay 语义的核心，保证 Scenario 不影响生产数据。

**累积叠加语义**（对应 §1.2.6，实现必读）：同一 Scenario 内对同一对象的多次 UPDATE，作用在同一行 overlay 上（version 递增），不是每个 Action 一行。每次 UPDATE 读取的是 **Scenario 当前视图**（base + 上一轮 overlay），所以后一个 Action 能看到前一个 Action 的改动。具体：

```
Action 1: UPDATE F-001 {aircraft: B-777}
  → 读 base F-001 {aircraft:B-737, cost:70k}（Scenario 当前视图，无 overlay）
  → 写 overlay 行 v1: {aircraft:B-777, cost:70k}  ← base properties 副本 + aircraft 覆盖

Action 2: UPDATE F-001 {cost: 85000}
  → 读 Scenario 当前视图 = base + overlay v1 = {aircraft:B-777, cost:70k}
  → 写 overlay 行 v2: {aircraft:B-777, cost:85000}  ← 在 v1 基础上覆盖 cost
```

即 `upsert_object_state_scenario` 的 UPDATE 分支必须：读当前 overlay 行（若有）作为 before，合并新 properties，写回 overlay 行（version+1）。**不是**用 base properties 做合并基底——否则会丢失前序 Action 的改动。

### 3.2 读取语义（base + overlay 求值）

查询对象时携带 `scenario_id`，求值规则：

```sql
-- 单对象读取（base + scenario overlay 合并）
-- 注：overlay 行存的是"合并后的完整 properties"（见 §3.1 累积叠加），
-- 不是"只改的字段"。所以 COALESCE 优先取 overlay 即可，无需逐字段合并。
SELECT
  COALESCE(s.properties, b.properties) AS properties,
  CASE
    WHEN s.rid IS NOT NULL AND s.properties->>'__deleted' = 'true' THEN 'DELETED'
    WHEN s.rid IS NOT NULL AND b.rid IS NULL THEN 'CREATED'
    WHEN s.rid IS NOT NULL THEN 'UPDATED'
    ELSE 'BASE'
  END AS source
FROM object_state b
LEFT JOIN object_state s
  ON s.rid = b.rid
  AND s.scenario_id = :scenario_id
WHERE b.rid = :rid
  AND b.scenario_id IS NULL
  AND (s.properties->>'__deleted' IS NULL OR s.properties->>'__deleted' != 'true')

-- Scenario 内新建的对象（base 没有，只能从 scenario 行读）
UNION ALL
SELECT s.properties, 'CREATED' AS source
FROM object_state s
WHERE s.scenario_id = :scenario_id
  AND s.properties->>'__deleted' != 'true'
  AND NOT EXISTS (
    SELECT 1 FROM object_state b
    WHERE b.rid = s.rid AND b.scenario_id IS NULL
  )
```

**要点**：
- Scenario overlay 行 `__deleted=true` 标记软删除（查询时过滤）
- Scenario 新建的对象（base 没有）单独 UNION
- `COALESCE` 让 Scenario 属性覆盖 base 属性

### 3.3 多 Scenario 并排对比

查询携带 `scenario_ids: [X, Y]`，返回每个对象的属性按 Scenario 分列：

```python
# 返回结构
{
  "rid": "obj-123",
  "base": {...properties},
  "scenarios": {
    "X": {...properties},  # 或 null（未编辑）
    "Y": {...properties}
  }
}
```

**实现**：对每个 scenario_id 各做一次 LEFT JOIN，PG 层一次查询完成。

## 4. Service 层设计

### 4.1 新增 `ScenarioService`

**新文件**：`src/ontology/services/scenario_service.py`

```python
class ScenarioService:
    """Scenario lifecycle management — create / list / freeze / duplicate /
    apply / discard.

    Mirrors Palantir's Scenario concept (incremental ontology fork for
    what-if analysis). Reuses the existing `branches` table (is_main=False).
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        action_service: ActionService,
        query_service: ObjectQueryService,
    ) -> None: ...

    # ── Lifecycle ──────────────────────────────────────────────
    async def create_scenario(
        self, *, ontology_id: str, name: str, display_name: str = "",
        description: str = "", base_branch_id: str | None = None,
        created_by: str = "system",
    ) -> ScenarioResponse:
        """Create a new Scenario (ACTIVE status).

        base_branch_id defaults to the ontology's main branch.
        Raises ConflictError if name already exists in this ontology.
        """

    async def get_scenario(self, scenario_id: str) -> ScenarioResponse: ...
    async def list_scenarios(
        self, *, ontology_id: str, status: str | None = None,
    ) -> list[ScenarioResponse]: ...

    async def freeze_scenario(self, scenario_id: str) -> ScenarioResponse:
        """ACTIVE → IMMUTABLE. After freeze, no more Actions can be applied."""

    async def duplicate_scenario(
        self, scenario_id: str, *, new_name: str, created_by: str = "system",
    ) -> ScenarioResponse:
        """Create a new ACTIVE Scenario copying all edits from source.
        Copies object_state + object_links overlay rows to new scenario_id."""

    async def discard_scenario(self, scenario_id: str) -> None:
        """Delete a Scenario and all its overlay data (CASCADE)."""

    # ── Apply to main ──────────────────────────────────────────
    async def apply_scenario(
        self, scenario_id: str, *, ctx: ActionContext, dry_run: bool = False,
    ) -> ApplyScenarioResult:
        """Replay a Scenario's edits onto main branch.

        Reads the scenario's execution logs in order, re-applies each Action
        to main (scenario_id=None) with OCC. Returns per-action results
        (success / OCC conflict). Dry_run returns what would happen without
        writing. Scenario status → APPLIED on success.
        """

    # ── Edited entities query ─────────────────────────────────
    async def list_edited_object_types(self, scenario_id: str) -> list[str]: ...
    async def list_edited_objects(
        self, scenario_id: str, object_type: str,
        *, page_size: int = 100, page_token: str | None = None,
    ) -> tuple[list[EditedObjectRef], str | None]: ...
    async def list_edited_link_types(
        self, scenario_id: str, object_type: str,
    ) -> list[str]: ...
    async def list_edited_entity_types(
        self, scenario_id: str,
    ) -> EditedEntityTypesResponse: ...
```

**注入 container**：在 `config/container.py` 注册 `scenario_service`，依赖 `metadata`/`action_service`/`query_service`。

### 4.2 PostgresMetaStore 扩展

**文件**：`src/ontology/layers/metadata/postgres_meta_store.py`

新增方法（签名）：

```python
# ── Scenario (branch) CRUD ──────────────────────────────────
async def create_branch(self, branch: BranchModel) -> BranchModel: ...
async def get_branch(self, branch_id: str) -> BranchModel | None: ...
async def get_branch_by_name(self, ontology_id: str, name: str) -> BranchModel | None: ...
async def list_branches(self, ontology_id: str, *, is_main: bool | None = None) -> list[BranchModel]: ...
async def update_branch_status(self, branch_id: str, status: str) -> None: ...
async def get_main_branch(self, ontology_id: str) -> BranchModel:
    """Get or lazily create the main branch (is_main=True) for an ontology."""

# ── Overlay-aware object state ──────────────────────────────
async def upsert_object_state_scenario(
    self, *, rid: str, scenario_id: str | None,
    object_type_api_name: str, ontology_id: str,
    properties: dict, expected_version: int, modified_by: str = "system",
) -> int:
    """OCC upsert scoped to scenario_id. NULL = main.

    For Scenario UPDATE: if base row exists, this INSERTs an overlay row
    (scenario_id=X) with properties = merged base+changes. expected_version
    is checked against the BASE row's version (Scenario doesn't change base).
    """

async def get_object_state_with_overlay(
    self, rid: str, scenario_id: str,
) -> dict | None:
    """Read object applying scenario overlay (§3.2 query)."""

async def list_objects_with_overlay(
    self, *, ontology_id: str, object_type_api_name: str,
    scenario_id: str, filters: dict | None = None,
    limit: int = 1000, offset: int = 0,
) -> list[dict]:
    """Bulk read with overlay. Returns base+overlay merged per §3.2."""

async def list_edited_rids(
    self, scenario_id: str, object_type_api_name: str,
) -> list[tuple[str, str]]:
    """Return [(rid, edit_type)] for objects edited in scenario."""

async def count_scenario_edits(self, scenario_id: str) -> int: ...
```

## 5. ActionService 改造

### 5.1 ActionContext 加 scenario_id

**文件**：`src/ontology/core/schemas/action.py`（`ActionContext`，line 291）

```python
class ActionContext(BaseModel):
    current_user: str = "anonymous"
    current_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workspace_id: str = ""
    ontology_snapshot_version: int | None = None
    selected_object: dict[str, Any] | None = None
    user_roles: list[str] = Field(default_factory=list)
    # 新增：Action 执行的 Scenario（None = main）
    scenario_id: str | None = None
```

### 5.2 ActionExecutionRequest 加 scenario_id

**文件**：`src/ontology/core/schemas/action.py`（`ActionExecutionRequest`）

```python
class ActionExecutionRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    # 新增：目标 Scenario（None = main）
    scenario_id: str | None = None
```

### 5.3 execute_action 流程改造

**文件**：`src/ontology/services/action_service.py`（`execute_action`，line 385）

改造点（在现有 Step 1-12 中插入）：

```
Step 0 (新增): Scenario 校验
  - 若 ctx.scenario_id is not None:
    - 查 BranchModel，确认存在且 status=ACTIVE（IMMUTABLE/APPLIED/DISCARDED 报 409）
    - 确认 scenario.ontology_id == action_type.ontology_id
    - 检查 scenario.action_count < 50（软限制，超限报 400）
  - 若 ctx.scenario_id is None: 走原流程

Step 1-7: 不变（解析 ActionType、校验、构建 mutations）
  注：before_snapshot 采集时，若 scenario_id，读 base 行（scenario_id=NULL）
       的当前状态作为 before（因为 Scenario 是对 base 的 overlay）

Step 8 (改造): apply mutations to object_state
  - 改 _apply_mutations 调用 upsert_object_state_scenario 而非 upsert_object_state
  - Scenario UPDATE 的 expected_version 校验对 BASE 行（Scenario 不改 base 版本）
  - Scenario CREATE 直接 INSERT overlay 行 (scenario_id=X)
  - Scenario DELETE 软删除（overlay 行 properties['__deleted']=true）

Step 8.6 (新增): 维护 Scenario edit 计数
  - scenario.edit_count += len(affected_objects)
  - scenario.action_count += 1
  - 检查 edit_count ≤ 30000

Step 9: create_execution_log 带 scenario_id
  - execution_log.scenario_id = ctx.scenario_id

Step 10-12: 不变（commit + 同步 outbox + 图投影）
  注：Scenario 写入不触发归档同步（不写 Iceberg），只留在 PG object_state
       → Step 11 (_create_sync_outbox_records) 在 scenario_id is not None 时跳过 INDEX/ARCHIVE outbox 追加
       → Step 12 (graph projection) 在 scenario_id is not None 时跳过
  （2026-07-08 起 Step 11 改为 outbox 驱动，原 ensure_cdc_pipelines 已删除）
```

**关键实现细节**：

```python
# Step 8 改造伪代码
for mutation in mutations:
    if mutation["type"] in ("CREATE_OBJECT", "UPDATE_OBJECT", "UPDATE_PROPERTY"):
        new_version = await self._metadata.upsert_object_state_scenario(
            rid=mutation["rid"],
            scenario_id=ctx.scenario_id,  # None for main
            object_type_api_name=...,
            ontology_id=...,
            properties=mutation["properties"],
            expected_version=mutation["expected_version"],
            modified_by=ctx.current_user,
        )
    elif mutation["type"] == "DELETE_OBJECT":
        if ctx.scenario_id:
            # Scenario: 软删除（overlay 行标记 __deleted）
            await self._metadata.upsert_object_state_scenario(
                rid=mutation["rid"],
                scenario_id=ctx.scenario_id,
                ...,
                properties={"__deleted": True, "__deleted_at": utcnow().isoformat()},
                expected_version=mutation["expected_version"],
            )
        else:
            await self._metadata.delete_object_state(...)  # 原逻辑
```

**⚠️ 设计决策 D8 落实（Scenario 禁外部 side effect）**：在 Step 0 校验后、Step 7 构建完 effects 时，检查 ActionType 的 effects 是否含 `WEBHOOK_WRITEBACK`。若 `ctx.scenario_id is not None` 且含该 effect，报 400 `WEBHOOK_WRITEBACK_NOT_ALLOWED_IN_SCENARIO`。理由：side effect 影响真实系统，违反"假设"语义（见 §1.2.8）。

### 5.4 Scenario UPDATE 的 OCC 语义（关键设计）

**问题**：Scenario UPDATE 的 `expected_version` 校验哪个版本？

**决策 D6**：校验 **base 行的 version**，但 Scenario overlay 行有自己的独立 version 序列。

**理由**：
- Scenario 是 base 的 fork，"预期版本"应基于 base 当前状态（用户看到的是 base + 之前的 overlay）
- Scenario overlay 行的 version 是 overlay 自身的修订次数（用于 Scenario 内部的二次编辑 OCC）

**实现**（⚠️ 注意合并基底，修正累积语义 bug）：
```python
async def upsert_object_state_scenario(...):
    # 1. 读 base 行（scenario_id IS NULL）的 version
    base = await get_object_state_main(rid)
    if base is None and expected_version > 0:
        # base 不存在但期望更新 → 对齐 Palantir：Scenario 不能更新不存在的对象
        raise NotFoundError(...)
    if base and base["version"] != expected_version:
        return 0  # OCC 冲突（base 被改了）

    # 2. UPSERT overlay 行（scenario_id=X）
    # ⚠️ 合并基底是"当前 overlay 行"（若有），不是 base 行！
    # 否则会丢失同 Scenario 内前序 Action 的改动（见 §3.1 累积叠加语义）
    existing_overlay = await get_object_state(rid, scenario_id)
    if existing_overlay:
        # 后续 UPDATE：在现有 overlay 上覆盖（累积）
        merged_props = {**existing_overlay["properties"], **properties}
        new_overlay_version = existing_overlay["version"] + 1
        UPDATE overlay SET properties=merged_props, version=new_overlay_version
    else:
        # 首次 UPDATE：用 base properties 副本 + 新 properties 作 overlay
        merged_props = {**base["properties"], **properties} if base else properties
        INSERT overlay (scenario_id=X, version=1, properties=merged_props)
```

## 6. 查询层改造

### 6.1 ObjectQueryService 加 scenario_id

**文件**：`src/ontology/services/object_query_service.py`

所有查询方法（`load_objects`/`filter_objects`/`exists_objects`/`count_objects`/`aggregate_objects`/`topn_objects`）增加可选 `scenario_id: str | None = None` 参数。

**分支逻辑**：
- `scenario_id is None`：走原路径（Doris 主 / Trino 降级）
- `scenario_id is not None`：**只走 PG object_state**（Scenario 是操作态，不查 Doris/Iceberg）。调 `list_objects_with_overlay`

```python
async def filter_objects(
    self, ontology_api_name: str, object_type_api_name: str,
    filters: dict | None = None, *, scenario_id: str | None = None,
    limit: int = 1000, offset: int = 0,
) -> list[dict]:
    if scenario_id is not None:
        # Scenario 查询：PG object_state + overlay
        return await self._metadata.list_objects_with_overlay(
            ontology_id=..., object_type_api_name=object_type_api_name,
            scenario_id=scenario_id, filters=filters,
            limit=limit, offset=offset,
        )
    # 原路径：Doris 主 / Trino 降级
    ot, _ = await self._resolve_query_target(...)
    ...
```

**注意**：Scenario 查询的 filter 在 PG 层执行（JSONB 操作符），不是 Doris SQL。需复用 `_filter_dict_to_sql` 的操作符映射，但目标方言是 PG。

### 6.2 多 Scenario 并排对比查询

```python
async def compare_objects_across_scenarios(
    self, ontology_api_name: str, object_type_api_name: str,
    *, scenario_ids: list[str], filters: dict | None = None,
    limit: int = 1000,
) -> list[ObjectComparisonRow]:
    """Return objects with base + each scenario's properties side-by-side.

    Only returns objects that are edited in at least one scenario
    (or match filters in base). Each row:
      {rid, base: {...}, scenarios: {sid: {...}|None}}
    """
```

## 7. API 路由设计

**新文件**：`src/ontology/routes/scenario.py`

```python
router = APIRouter(prefix="/scenarios", tags=["scenarios"])

# ── Lifecycle ───────────────────────────────────────────────
POST   /ontologies/{ontology}/scenarios                  # create
GET    /ontologies/{ontology}/scenarios                  # list (filter by status)
GET    /scenarios/{id}                                   # get detail
POST   /scenarios/{id}/freeze                            # ACTIVE → IMMUTABLE
POST   /scenarios/{id}/duplicate                         # copy to new ACTIVE
DELETE /scenarios/{id}                                   # discard
POST   /scenarios/{id}/apply                             # replay to main (body: {dry_run?})

# ── Edited entities (对齐 Palantir API) ────────────────────
GET    /scenarios/{id}/editedEntityTypes                 # 汇总
GET    /scenarios/{id}/objectTypes/edited                # 被编辑的对象类型
GET    /scenarios/{id}/objects/{objectType}/edited       # 被编辑的对象(分页)
GET    /scenarios/{id}/objectTypes/{ot}/outgoingLinkTypes/edited
GET    /scenarios/{id}/objects/{ot}/links/{lt}/edited   # 被编辑的链接(分页)

# ── Scenario 内查询 ────────────────────────────────────────
POST   /scenarios/{id}/objects/{objectType}/query        # filter in scenario
POST   /scenarios/{id}/objects/compare                   # 多 scenario 并排
```

**Action 路由改造**：`POST /actions/{action_type}/execute` body 增加 `scenario_id` 字段（可选）。

**请求/响应 Schema**（`core/schemas/scenario.py` 新文件）：

```python
class ScenarioCreate(BaseModel):
    name: str  # api_name 风格
    display_name: str = ""
    description: str = ""
    base_branch_id: str | None = None

class ScenarioResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str
    is_main: bool
    base_branch_id: str | None
    status: Literal["ACTIVE", "IMMUTABLE", "APPLIED", "DISCARDED"]
    edit_count: int
    action_count: int
    created_by: str
    created_at: datetime

class ApplyScenarioRequest(BaseModel):
    dry_run: bool = False

class ApplyScenarioResult(BaseModel):
    scenario_id: str
    applied: bool  # false if dry_run or any conflict
    action_results: list[ActionResult]
    conflicts: list[ActionConflict]  # OCC 冲突的对象

class EditedObjectRef(BaseModel):
    rid: str
    edit_type: Literal["CREATED", "UPDATED", "DELETED"]
```

## 8. 前端交互

### 8.1 Scenario Manager 组件

**新文件**：`src/web-ui/src/components/ScenarioManager.tsx`

功能（对齐 Palantir Scenario Manager widget）：
- 创建 Scenario（输入 name + display_name + description）
- 列出当前本体的 Scenarios（状态徽章）
- 冻结 / 复制 / 丢弃 / 应用到 main 操作
- 选中 Scenario 后，ObjectTable 切换到 Scenario 视图（显示 overlay 数据）

### 8.2 ObjectTable 增强

**文件**：`src/web-ui/src/components/PreviewTable.tsx`（现有）

改造：
- 增加 `scenarioId` prop，传入时调 `/scenarios/{id}/objects/{ot}/query`
- 增加 `compareScenarioIds: string[]` prop，传入时调 `/scenarios/{id}/objects/compare`，渲染差异列（对齐 Palantir "只在差异列显示"）
- Scenario 列用不同颜色区分（CREATED 绿/UPDATED 黄/DELETED 红）

### 8.3 ExecuteActionDialog 增强

**文件**：`src/web-ui/src/components/ExecuteActionDialog.tsx`

增加 Scenario 选择器：执行 Action 前可选目标 Scenario（默认 main）。

## 9. 数据库迁移

### 9.1 ⚠️ object_state 复合主键迁移（高风险）

**文件**：`alembic/versions/20260706_xxxx_add_scenario_support.py`

这是破坏性变更，必须谨慎：

```python
def upgrade():
    # 1. branches 表加字段
    op.add_column("branches", sa.Column("display_name", sa.String(255), server_default=""))
    op.add_column("branches", sa.Column("description", sa.Text(), server_default=""))
    op.add_column("branches", sa.Column("base_branch_id", sa.String(32), nullable=True))
    op.add_column("branches", sa.Column("created_by", sa.String(255), server_default="system"))
    op.add_column("branches", sa.Column("edit_count", sa.Integer, server_default="0"))
    op.add_column("branches", sa.Column("action_count", sa.Integer, server_default="0"))
    op.create_foreign_key("fk_branches_base", "branches", "branches", ["base_branch_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_branches_ontology_name", "branches", ["ontology_id", "name"])

    # 2. object_state 加 scenario_id 列 + 改复合主键
    op.add_column("object_state", sa.Column("scenario_id", sa.String(32), nullable=True))
    op.create_index("ix_object_state_type_scenario", "object_state", ["object_type_api_name", "scenario_id"])
    # ⚠️ 先删旧主键，再加复合主键
    op.drop_constraint("object_state_pkey", "object_state", type_="primary")
    op.create_primary_key("object_state_pkey", "object_state", ["rid", "scenario_id"])
    op.create_foreign_key(
        "fk_object_state_scenario", "object_state", "branches", ["scenario_id"], ["id"], ondelete="CASCADE"
    )

    # 3. object_links 加 scenario_id
    op.add_column("object_links", sa.Column("scenario_id", sa.String(32), nullable=True))
    op.drop_constraint("uq_object_links", "object_links", type_="unique")
    op.create_unique_constraint(
        "uq_object_links_scenario", "object_links",
        ["link_type_api_name", "source_rid", "target_rid", "scenario_id"],
    )
    op.create_foreign_key("fk_object_links_scenario", "object_links", "branches", ["scenario_id"], ["id"], ondelete="CASCADE")

    # 4. action_execution_logs 加 scenario_id
    op.add_column("action_execution_logs", sa.Column("scenario_id", sa.String(32), nullable=True))
    op.create_index("ix_execution_log_scenario", "action_execution_logs", ["scenario_id"])

    # 5. 为每个 ontology 确保 main branch 存在
    op.execute("""
        INSERT INTO branches (id, ontology_id, name, is_main, status, created_at, updated_at)
        SELECT replace(gen_random_uuid()::text, '-', ''), id, 'main', true, 'ACTIVE', now(), now()
        FROM ontologies
        WHERE NOT EXISTS (
            SELECT 1 FROM branches b WHERE b.ontology_id = ontologies.id AND b.is_main = true
        )
    """)
```

**⚠️ 风险与缓解**：
1. **复合主键变更**：PG 允许，但若有外键引用 `object_state.rid` 需同步改。检查：`object_links.source_rid`/`target_rid` 不是 FK 到 object_state（是逻辑引用），安全。
2. **回滚**：downgrade 需先把所有 scenario_id 非 NULL 行删除，再恢复单列 PK。
3. **autogenerate 检测不出**：复合 PK 变更必须手写 migration，**autogenerate 不可靠**。
4. **本地验证**：`alembic upgrade head` → `alembic check` → 跑全量测试。

### 9.2 可选：scenario_edit_index 表

若启用 §2.5 的索引表，单独 migration。

## 10. 测试策略

### 10.1 单元测试（必须）

**新文件**：`tests/unit/services/test_scenario_service.py`

```python
class TestScenarioLifecycle:
    async def test_create_scenario_default_main_base(self): ...
    async def test_create_scenario_name_conflict_409(self): ...
    async def test_freeze_immutable_blocks_writes(self): ...
    async def test_duplicate_copies_overlay(self): ...
    async def test_discard_cascades_overlay(self): ...

class TestScenarioActionExecution:
    async def test_action_in_scenario_does_not_touch_base(self):
        """核心：Scenario UPDATE 后 base 行 version 不变"""
    async def test_action_in_scenario_creates_overlay_row(self): ...
    async def test_action_in_scenario_occ_checks_base_version(self): ...
    async def test_action_in_immutable_scenario_raises_409(self): ...
    async def test_action_count_limit_50_raises_400(self): ...

class TestScenarioOverlayQuery:
    async def test_query_in_scenario_merges_overlay(self):
        """base properties + scenario overlay 合并正确"""
    async def test_query_in_scenario_with_deleted_object_filters(self): ...
    async def test_query_in_scenario_with_created_object_unions(self): ...
    async def test_query_multiple_scenarios_side_by_side(self): ...

class TestScenarioApply:
    async def test_apply_replays_edits_to_main(self): ...
    async def test_apply_occ_conflict_reported(self): ...
    async def test_apply_dry_run_does_not_write(self): ...
    async def test_apply_sets_status_applied(self): ...
```

### 10.2 集成测试

**新文件**：`tests/integration/test_scenario_e2e.py`

端到端：创建 Scenario → 应用 Action → 查询 overlay → 对比 → apply 到 main → 验证 main 数据变更。

### 10.3 测试红线

- **不能只断言 `commit.assert_awaited()`**——必须验证 object_state 实际写入的字段值（scenario_id、properties 合并、version）
- **必须验证 base 行未被修改**（核心不变量）
- **OCC 冲突场景必须用真 DB**（mock 测不出 SQL 行为）

---

# 第二部分：决策捕获与反馈飞轮

## 11. 设计目标与 Palantir 范式对齐

### 11.1 Palantir 决策捕获机制（深度研究结果）

**核心概念：Decision Exhaust（决策废气）**

Palantir 官方反复强调（多份博客交叉印证）：
> "decision was made, atop which version of enterprise data, and through which application"
> "Closing the action loop as decisions are made in real-time is what distinguishes an operational system from an analytical system."

三层机制：

1. **Action 执行记录**（Gaia 已有 `action_execution_logs` + CDL before/after snapshot）
2. **决策物化为本体对象**（关键差异）：决策不是孤立 audit 表，而是可查询的 ObjectType（如 `Decision`），与被决策对象 Link 关联
3. **结果回流形成反馈环**：决策 → 写回业务系统 → 业务结果产生新数据 → 与原决策对照 → 评估决策质量

**数据版本锚定**（Agentic Runtime 博客）：
> "Every data query can be tied to a full version history for the given data source"

即决策记录必须锚定"用的是哪个版本的数据"。

**Writeback 机制**（官方《Webhooks》）：
- Writeback webhook：本体修改前执行，失败则阻断（事务性）
- Side effect webhook：本体修改后执行，best-effort
- 输出参数可被后续规则使用

### 11.2 Gaia 设计目标

| 目标 | Palantir 对齐 | Gaia 实现 |
|------|:---:|--------|
| 决策物化为本体对象 | ✅ | ActionType 配置 `capture_decision`，执行后自动创建 Decision 对象 |
| 决策关联被决策对象 | ✅ | Decision 对象通过 Link 关联 affected objects |
| 决策锚定数据版本 | ✅ | execution_log.read_snapshot_id 记录 Iceberg snapshot |
| Writeback webhook 事务性 | ✅ | outbox effect_type=WEBHOOK，writeback 模式失败回滚 |
| 决策结果回流对照 | ✅ | DecisionOutcome 对象 + 定时对照任务 |
| 决策可查询/聚合 | ✅ | Decision 是普通 ObjectType，走现有查询层 |

### 11.3 关键设计决策

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D10 | 决策物化是否独立表 | **否，复用 object_state** | Decision 是 ObjectType，存在 object_state；与 Palantir 一致（决策即对象） |
| D11 | 决策物化触发 | **ActionType 配置 `capture_decision.enabled`** | 默认 false，向后兼容；启用后自动创建 |
| D12 | Decision 对象的 ObjectType | **首次启用时自动创建 `__Decision` ObjectType** | 系统级 ObjectType，api_name=`__Decision`，跨本体共享 |
| D13 | 决策-对象关联 | **`__DecisionLink` LinkType** | 连接 Decision 与 affected objects |
| D14 | 数据版本锚定 | **激活 `read_snapshot_id`** | 执行前采集 Iceberg snapshot_id，写入 execution_log |
| D15 | Writeback webhook 双模式 | **outbox effect_type 区分** | WEBHOOK_WRITEBACK（事务性）/ WEBHOOK_SIDE_EFFECT（best-effort） |
| D16 | 决策结果回流 | **DecisionOutcome + 定时审计任务** | 新增后台任务，对照 Decision 与对象后续状态 |

## 12. 数据模型设计

### 12.1 系统级 Decision ObjectType（自动创建）

当任一 ActionType 首次启用 `capture_decision` 时，系统自动创建：

```python
# 自动创建的 ObjectType（存在 object_types 表）
ObjectTypeModel(
    api_name="__Decision",
    display_name="Decision",
    description="System object type for captured decisions (Decision Exhaust)",
    ontology_id=<ontology_id>,
    properties={  # properties 元数据
        "decision_id": {"type": "string", "primary_key": True},
        "action_type": {"type": "string"},          # 哪个 ActionType 产生
        "action_execution_id": {"type": "string"},   # 关联 execution_log
        "decided_at": {"type": "datetime"},
        "decided_by": {"type": "string"},
        "parameters": {"type": "object"},            # Action 入参快照
        "rationale": {"type": "string"},             # 决策理由（可选，用户填）
        "data_version": {"type": "object"},          # 锚定的数据版本
        "outcome_status": {"type": "string"},        # PENDING/MEASURED/EXPIRED
        "outcome_metrics": {"type": "object"},       # 结果指标（回流后填）
        "scenario_id": {"type": "string"},           # 若在 Scenario 内决策
    }
)
```

**注意**：`__Decision` 是系统保留 api_name（双下划线前缀），用户不能创建同名的。`core/naming.py` 加保留名校验。

### 12.2 系统级 DecisionLink LinkType

```python
LinkTypeModel(
    api_name="__DecisionLink",
    display_name="Decision Link",
    source_object_type_api_name="__Decision",
    target_object_type_api_name="*",  # 可关联任意对象（需特殊处理）
    cardinality="MANY_TO_MANY",
)
```

**⚠️ 通配 target 的处理**：`object_links` 表存 `target_rid` + `target_object_type_api_name`（需扩展，见下）。

**object_links 表扩展**（可选）：
```python
# 加 target_object_type_api_name 列，支持跨类型 Link
target_object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=True)
```
MVP 可不加，用 Decision 的 properties 记录 `affected_objects: [{type, id}]` 代替。

### 12.3 ActionType 配置扩展

**文件**：`src/ontology/core/schemas/action.py`（`ActionTypeCreate`）

```python
class DecisionCaptureConfig(BaseModel):
    """Configuration for capturing an action execution as a Decision object."""
    enabled: bool = False
    rationale_required: bool = False  # 是否强制用户填理由
    outcome_tracking: bool = False    # 是否启用结果回流
    outcome_object_type: str | None = None  # 对照哪个 ObjectType 的后续状态
    outcome_delay_hours: int = 24     # 多久后评估结果

class ActionTypeCreate(BaseModel):
    # ... 现有字段 ...
    capture_decision: DecisionCaptureConfig = Field(default_factory=DecisionCaptureConfig)
```

**ORM**：`ActionTypeModel.parameters` JSONB 里存 `capture_decision` 子配置（不新建列，复用现有 JSONB）。

## 13. 决策物化机制

### 13.1 ActionService 改造（决策物化）

**文件**：`src/ontology/services/action_service.py`

在 `execute_action` 的 Step 9（create_execution_log）之后，插入 **Step 9.5**：

```python
# Step 9.5 (新增): 决策物化（若 ActionType 启用 capture_decision）
capture_cfg = ActionTypeModel.parameters.get("capture_decision", {})
if capture_cfg.get("enabled"):
    decision_id = await self._materialize_decision(
        execution=execution,
        action_type=action_type,
        affected_objects=affected_objects,
        ctx=ctx,
        capture_cfg=capture_cfg,
    )
    # decision_id 写入 execution_log 的扩展字段或 link
```

**新方法**：

```python
async def _materialize_decision(
    self, *, execution: ActionExecutionLogModel,
    action_type: ActionTypeModel, affected_objects: dict[str, int],
    ctx: ActionContext, capture_cfg: dict,
) -> str:
    """Create a __Decision object linking to affected objects.

    Writes to object_state (scenario_id=None for main decisions) within
    the same PG transaction. Returns the decision_id.
    """
    decision_id = f"dec_{execution.action_id}"
    decision_props = {
        "decision_id": decision_id,
        "action_type": action_type.api_name,
        "action_execution_id": execution.id,
        "decided_at": ctx.current_timestamp.isoformat(),
        "decided_by": ctx.current_user,
        "parameters": execution.parameters,
        "rationale": "",  # 前端可后续 PATCH 填充
        "data_version": {
            "iceberg_snapshot_id": execution.read_snapshot_id,
            "ontology_snapshot_version": ctx.ontology_snapshot_version,
        },
        "outcome_status": "PENDING" if capture_cfg.get("outcome_tracking") else "NOT_TRACKED",
        "scenario_id": ctx.scenario_id,
        "affected_objects": [
            {"object_type": action_type.object_type_api_name, "rid": oid}
            for oid in affected_objects
        ],
    }
    await self._metadata.upsert_object_state(
        rid=decision_id,
        object_type_api_name="__Decision",
        ontology_id=action_type.ontology_id,
        properties=decision_props,
        expected_version=0,  # CREATE
        modified_by=ctx.current_user,
    )
    return decision_id
```

**要点**：
- Decision 写入与 Action 执行在同一 PG 事务（Step 10 commit 一起提交）
- `affected_objects` 内嵌在 Decision properties（MVP 不用 object_links，避免通配 LinkType 复杂性）
- `rationale` 可后续 PATCH（决策理由可不强制）

### 13.2 决策 PATCH（补充理由）

**API**：`PATCH /decisions/{decision_id}` body `{"rationale": "..."}`

更新 `__Decision` 对象的 `rationale` 属性（走标准 object update）。

## 14. 数据版本锚定

### 14.1 Iceberg snapshot 采集

**文件**：`src/ontology/services/action_service.py`

在 `execute_action` 的 Step 1（解析 ActionType）之后，插入 **Step 1.7**：

```python
# Step 1.7 (新增): 采集数据版本锚定（Iceberg snapshot_id）
if action_type.object_type_api_name:
    ot = await self._metadata.get_object_type(...)
    if ot and ot.storage_type == "MANAGED":
        dataset = self._naming.managed_dataset_api_name(ot.api_name, ...)
        snapshots = await self._iceberg.get_snapshots(dataset)
        if snapshots:
            ctx.ontology_snapshot_version = snapshots[-1].snapshot_id
            # 注入到后续 execution_log.read_snapshot_id
```

**注意**：
- 只对 MANAGED 对象采集（VIRTUAL 无 Iceberg snapshot）
- 采集失败不阻断 Action（best-effort，记日志）
- `read_snapshot_id` 字段已存在于 `ActionExecutionLogModel`，激活使用

### 14.2 决策回放（基于版本锚定）

**API**：`GET /decisions/{decision_id}/replay`

用 `read_snapshot_id` 调 `IcebergStore.load_by_ids_as_of(snapshot_id)` 读取决策时刻的数据，返回"决策时看到的世界"。

## 15. Writeback webhook 增强

### 15.1 outbox effect_type 扩展

**现状**：`OutboxModel.effect_type` 已支持 `WEBHOOK`/`WRITE_BACK`/`SUB_ACTION`/`KAFKA_TOPIC`。

**改造**：细化 WEBHOOK 为双模式：

```python
# effect_type 枚举扩展（语义层，不改 DB）
# WEBHOOK_WRITEBACK: 事务性，本体修改前执行，失败回滚（对齐 Palantir writeback webhook）
# WEBHOOK_SIDE_EFFECT: best-effort，本体修改后执行（对齐 Palantir side effect webhook）
# 旧的 WEBHOOK 视为 WEBHOOK_SIDE_EFFECT（向后兼容）
```

### 15.2 ActionEffectConfig 扩展

**文件**：`src/ontology/core/schemas/action.py`（`ActionEffectConfig`）

```python
class ActionEffectConfig(BaseModel):
    type: Literal[
        "WEBHOOK_WRITEBACK", "WEBHOOK_SIDE_EFFECT",
        "WRITE_BACK", "SUB_ACTION", "KAFKA_TOPIC",
    ]
    # WEBHOOK_* 专用
    webhook_url: str | None = None
    webhook_method: str = "POST"
    webhook_headers: dict[str, str] = Field(default_factory=dict)
    # 输出参数映射（writeback 返回值写入本体）
    output_mapping: dict[str, str] = Field(default_factory=dict)
    # ... 现有字段 ...
```

### 15.3 writeback 事务性实现

**文件**：`src/ontology/services/action_service.py`

**问题**：现有 outbox 是"事务后异步执行"。但 Palantir 的 writeback webhook 要在**本体修改前同步执行，失败则回滚**。

**改造方案**（关键）：

```
Step 8 之前插入 Step 7.8 (新增): 同步执行 WEBHOOK_WRITEBACK
  - 对每个 WEBHOOK_WRITEBACK effect:
    - 调用 webhook（同步，带超时）
    - 成功：把输出参数加入 context，供后续 rule 使用
    - 失败：rollback + 抛错（对齐 Palantir "失败则不修改本体"）
  - 这些 effect 仍在 outbox 记录（status=EXECUTED）用于审计

Step 9: create_execution_log + outbox
  - WEBHOOK_WRITEBACK 的 outbox record status=EXECUTED（已同步执行）
  - WEBHOOK_SIDE_EFFECT 的 outbox record status=PENDING（异步）

Step 10: commit
  - 若 Step 7.8 失败，Step 10 不会执行（已 rollback）
```

**实现**：新增 `WebhookExecutor` 服务（同步 HTTP 调用 + 超时 + 重试策略）。

**注意**：这改变了 Action 的执行时序（增加了同步外部调用），需文档说明。`WEBHOOK_WRITEBACK` 的超时应设短（如 5s），避免 Action 长时间阻塞。

## 16. 决策结果回流与反馈飞轮

### 16.1 DecisionOutcome 机制

**设计**：不新建独立表，结果回流填充 `__Decision` 对象的 `outcome_status` + `outcome_metrics` 属性。

**流转**：
1. 决策创建时：`outcome_status=PENDING`，`outcome_metrics={}`
2. 到达 `outcome_delay_hours` 后，后台任务评估
3. 评估：读 affected objects 的当前状态，与决策时的状态（before_snapshot）对照，计算指标
4. 更新 Decision：`outcome_status=MEASURED`，`outcome_metrics={actual_value, predicted_value, deviation, ...}`

### 16.2 后台评估任务

**新文件**：`src/ontology/services/decision_outcome_evaluator.py`

```python
class DecisionOutcomeEvaluator:
    """Background task: evaluate pending decisions whose outcome_delay has
    elapsed, comparing current object state to decision-time state.

    Mirrors Palantir's "decision feedback loop": decision → action → result
    → measurement → learning.
    """

    async def run_eval_loop(self) -> None:
        """Lifespan background task. Polls PENDING decisions periodically."""

    async def evaluate_decision(self, decision_id: str) -> None:
        """Evaluate a single decision's outcome.

        1. Read __Decision object (parameters, affected_objects, data_version)
        2. Read before_snapshot from execution_log (decision-time state)
        3. Read current object_state (post-decision state)
        4. Compute outcome_metrics per outcome_object_type config
        5. UPDATE __Decision: outcome_status=MEASURED, outcome_metrics={...}
        """
```

**注入 container + lifespan**：在 `main.py` lifespan 启动，类似 `OutboxExecutor`/`ConflictDetector`。

### 16.3 outcome_metrics 计算

由 ActionType 的 `capture_decision.outcome_object_type` + `outcome_metrics` 配置驱动：

```python
# ActionType 配置示例
capture_decision = {
    "enabled": True,
    "outcome_tracking": True,
    "outcome_object_type": "SalesOrder",
    "outcome_delay_hours": 72,
    "outcome_metrics": [
        {
            "name": "actual_revenue",
            "source": "CURRENT_PROPERTY",  # 读对象当前属性
            "property": "revenue"
        },
        {
            "name": "predicted_revenue",
            "source": "DECISION_PARAMETER",
            "parameter": "predicted_revenue"
        },
        {
            "name": "deviation_pct",
            "source": "EXPRESSION",
            "expression": "(actual_revenue - predicted_revenue) / predicted_revenue * 100"
        }
    ]
}
```

**实现**：复用 `ActionRuleEngine._safe_eval` 求值 EXPRESSION。

## 17. API 路由设计

**新文件**：`src/ontology/routes/decision.py`

```python
router = APIRouter(prefix="/decisions", tags=["decisions"])

GET    /ontologies/{ontology}/decisions               # list (filter by action_type/outcome_status/time)
GET    /decisions/{id}                                 # get detail
PATCH  /decisions/{id}                                 # update rationale
GET    /decisions/{id}/replay                          # 决策时刻数据回放(snapshot_id)
GET    /decisions/{id}/trace                           # 完整决策链(execution_log + before/after + evidence)
POST   /decisions/{id}/evaluate                        # 手动触发结果评估
GET    /ontologies/{ontology}/decisions/analytics      # 决策分析(成功率/偏差分布)
```

**请求/响应 Schema**（`core/schemas/decision.py`）：

```python
class DecisionResponse(BaseModel):
    decision_id: str
    action_type: str
    action_execution_id: str
    decided_at: datetime
    decided_by: str
    parameters: dict[str, Any]
    rationale: str
    data_version: dict[str, Any]
    outcome_status: Literal["PENDING", "MEASURED", "EXPIRED", "NOT_TRACKED"]
    outcome_metrics: dict[str, Any]
    scenario_id: str | None
    affected_objects: list[ObjectRef]

class DecisionAnalytics(BaseModel):
    total: int
    by_outcome_status: dict[str, int]
    by_action_type: dict[str, int]
    avg_deviation: float | None
    # ...
```

## 18. 前端交互

### 18.1 Decision Timeline 组件

**新文件**：`src/web-ui/src/components/DecisionTimeline.tsx`

- 时间轴展示决策历史
- 每个决策卡片：action_type + decided_by + time + outcome_status 徽章
- 点击展开：parameters + rationale + before/after diff + outcome_metrics

### 18.2 Decision Detail Panel

**新文件**：`src/web-ui/src/components/DecisionDetailPanel.tsx`

- 决策详情：参数、理由（可编辑）、数据版本
- before/after 属性对比（CDL 快照）
- outcome_metrics 可视化（实际 vs 预测）
- "回放决策时刻数据"按钮 → 调 `/replay`

### 18.3 OperationsDashboard 增强

**文件**：`src/web-ui/src/pages/OperationsDashboard.tsx`

加"决策反馈飞轮"卡片：本周决策数、已评估比例、平均偏差、高偏差决策列表。

## 19. 数据库迁移

### 19.1 决策捕获迁移

**文件**：`alembic/versions/20260706_yyyy_add_decision_capture.py`

```python
def upgrade():
    # 1. action_execution_logs 激活 read_snapshot_id（已有列，无需改）
    #    仅加注释说明语义

    # 2. 无新表（__Decision 是 object_state 里的数据，不是新表）
    #    __Decision ObjectType 在首次启用时由代码自动创建

    # 3. 可选：为 __Decision 查询加索引
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_object_state_decision
        ON object_state (object_type_api_name, (properties->>'outcome_status'))
        WHERE object_type_api_name = '__Decision'
    """)  # 部分索引，只索引 Decision 行

def downgrade():
    op.drop_index("ix_object_state_decision", "object_state")
```

**注意**：决策捕获复用 object_state，**几乎无 schema 变更**（read_snapshot_id 已存在）。这是设计的优雅之处。

### 19.2 与 Scenario 迁移的顺序

决策捕获迁移应**在 Scenario 迁移之后**（因为 Decision 的 scenario_id 字段依赖 object_state 的 scenario_id 列）。

## 20. 测试策略

### 20.1 单元测试

**新文件**：`tests/unit/services/test_decision_capture.py`

```python
class TestDecisionMaterialization:
    async def test_action_with_capture_creates_decision_object(self): ...
    async def test_action_without_capture_no_decision(self):
        """向后兼容：capture_decision.enabled=false 不创建 Decision"""
    async def test_decision_links_affected_objects(self): ...
    async def test_decision_in_scenario(self): ...
    async def test_decision_rationale_patch(self): ...

class TestDataVersionAnchoring:
    async def test_execution_log_records_snapshot_id(self): ...
    async def test_replay_returns_decision_time_data(self): ...
    async def test_snapshot_capture_failure_non_blocking(self): ...

class TestWritebackWebhook:
    async def test_writeback_failure_rolls_back_action(self):
        """事务性：webhook 失败 → object_state 未修改"""
    async def test_writeback_output_used_in_subsequent_rules(self): ...
    async def test_side_effect_executed_after_commit(self): ...

class TestDecisionOutcomeEvaluator:
    async def test_evaluator_computes_metrics(self): ...
    async def test_evaluator_updates_outcome_status(self): ...
    async def test_evaluator_respects_delay_hours(self): ...
    async def test_manual_evaluate_endpoint(self): ...
```

### 20.2 集成测试

**新文件**：`tests/integration/test_decision_loop_e2e.py`

完整飞轮：定义带 capture_decision 的 ActionType → 执行 Action → 验证 Decision 创建 → 模拟时间流逝 + 对象状态变化 → 触发评估 → 验证 outcome_metrics → 查询决策分析。

### 20.3 测试红线

- **Decision 物化必须验证 object_state 实际写入**（不能只断言调用）
- **WEBHOOK_WRITEBACK 事务性必须用真 HTTP mock**（验证 rollback）
- **outcome 评估必须验证实际指标计算**（不能只断言 status 变更）

---

# 第三部分：实施计划与依赖

## 21. 实施阶段

### Phase 1：Scenario 基础（2-3 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 1.1 数据模型 + migration | `core/models/ontology.py` + alembic | 无 |
| 1.2 ScenarioService 骨架 | `services/scenario_service.py` | 1.1 |
| 1.3 PostgresMetaStore overlay 方法 | `layers/metadata/postgres_meta_store.py` | 1.1 |
| 1.4 Scenario CRUD 路由 | `routes/scenario.py` | 1.2 |
| 1.5 单元测试 | `tests/unit/services/test_scenario_service.py` | 1.2-1.4 |

### Phase 2：Scenario + Action 集成（2-3 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 2.1 ActionContext + Request 加 scenario_id | `core/schemas/action.py` | 1.1 |
| 2.2 ActionService.execute_action 改造 | `services/action_service.py` | 2.1, 1.3 |
| 2.3 overlay 查询求值 | `layers/metadata/postgres_meta_store.py` | 1.3 |
| 2.4 ObjectQueryService scenario 分支 | `services/object_query_service.py` | 2.3 |
| 2.5 apply_scenario 实现 | `services/scenario_service.py` | 2.2 |
| 2.6 集成测试 | `tests/integration/test_scenario_e2e.py` | 2.1-2.5 |

### Phase 3：决策物化（2 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 3.1 DecisionCaptureConfig + ActionType 扩展 | `core/schemas/action.py` | Phase 1（scenario_id） |
| 3.2 __Decision ObjectType 自动创建 | `services/decision_service.py` | 3.1 |
| 3.3 ActionService._materialize_decision | `services/action_service.py` | 3.2 |
| 3.4 数据版本锚定（snapshot 采集） | `services/action_service.py` | 3.3 |
| 3.5 决策路由 + PATCH rationale | `routes/decision.py` | 3.3 |
| 3.6 单元测试 | `tests/unit/services/test_decision_capture.py` | 3.1-3.5 |

### Phase 4：Writeback webhook + 反馈飞轮（2-3 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 4.1 WebhookExecutor + writeback 事务性 | `services/webhook_executor.py` + action_service | 3.3 |
| 4.2 ActionEffectConfig 双模式 | `core/schemas/action.py` | 4.1 |
| 4.3 DecisionOutcomeEvaluator 后台任务 | `services/decision_outcome_evaluator.py` | 3.3 |
| 4.4 main.py lifespan 启动 evaluator | `main.py` | 4.3 |
| 4.5 决策分析 API | `routes/decision.py` | 4.3 |
| 4.6 集成测试 | `tests/integration/test_decision_loop_e2e.py` | 4.1-4.5 |

### Phase 5：前端（3-4 天，可与后端并行）

| 任务 | 文件 |
|------|------|
| 5.1 ScenarioManager 组件 | `components/ScenarioManager.tsx` |
| 5.2 ObjectTable scenario 支持 | `components/PreviewTable.tsx` |
| 5.3 ExecuteActionDialog scenario 选择 | `components/ExecuteActionDialog.tsx` |
| 5.4 DecisionTimeline + DetailPanel | `components/Decision*.tsx` |
| 5.5 OperationsDashboard 决策卡片 | `pages/OperationsDashboard.tsx` |

## 22. 依赖关系图

```
Phase 1 (Scenario 基础)
   ↓
Phase 2 (Scenario + Action) ──────┐
   ↓                              ↓
Phase 3 (决策物化) ←── scenario_id ┘
   ↓
Phase 4 (Writeback + 飞轮)
   ↓
Phase 5 (前端)  ← 可从 Phase 2 开始并行
```

## 23. 风险与缓解

| 风险 | 级别 | 缓解 |
|------|:---:|------|
| object_state 复合 PK 迁移破坏现有数据 | 🔴 高 | 充分本地测试 + 回滚脚本 + 备份 |
| Scenario overlay 查询性能（大对象集） | 🟡 中 | PG 索引 + 限制 Scenario 内查询对象数（≤10000，对齐 Palantir） |
| WEBHOOK_WRITEBACK 同步执行阻塞 Action | 🟡 中 | 短超时（5s）+ 异步 fallback 选项 |
| DecisionOutcomeEvaluator 后台任务与 ConflictDetector 冲突 | 🟢 低 | 不同轮询间隔 + 不同对象集 |
| __Decision 保留名冲突 | 🟢 低 | naming.py 加保留名校验 |
| Scenario apply 时 base 已变（OCC 冲突） | 🟡 中 | apply 返回冲突列表，用户决定强制/跳过 |

## 24. 与现有架构的契合度

| 改造点 | 契合度 | 说明 |
|--------|:---:|------|
| Scenario 复用 branches 表 | ✅ 高 | 表已存在，仅扩展字段 |
| Scenario overlay 复用 object_state | ✅ 高 | 加 scenario_id 列，语义自然 |
| 决策物化复用 object_state | ✅ 高 | Decision 即对象，零新表 |
| Writeback 复用 outbox | ✅ 高 | effect_type 细化，机制不变 |
| 数据版本锚定激活 read_snapshot_id | ✅ 高 | 字段已存在 |
| ActionService 改造 | 🟡 中 | 插入 Step，不重构 |
| ObjectQueryService scenario 分支 | 🟡 中 | 新分支，不动原路径 |

> **结论**：设计最大程度复用现有基础设施（branches / object_state / outbox / execution_log / read_snapshot_id），破坏性变更集中在 object_state 复合 PK。整体契合度高，风险可控。

## 25. 参考

### Palantir 官方（第一手）
- Scenario Core concepts: https://palantir.com/docs/foundry/workshop/scenarios-concepts/
- Scenario API (OSDK): https://cdn.jsdelivr.net/npm/@osdk/foundry.ontologies@2.65.0/build/browser/public/OntologyScenario.d.ts
- Apply Action API (scenarioRid): https://palantir.com/docs/foundry/api/ontologies-v2-resources/actions/apply-action/
- Object backend (OSv2): https://palantir.com/docs/foundry/object-backend/overview/
- Webhooks (writeback/side effect): https://palantir.com/docs/foundry/action-types/webhooks/
- Connecting AI to Decisions: https://blog.palantir.com/connecting-ai-to-decisions-with-the-palantir-ontology-c73f7b0a1a72
- Agentic Runtime (data version anchoring): https://blog.palantir.com/securing-agents-in-production-agentic-runtime-1-5191a0715240
- On dataset versioning: https://blog.palantir.com/on-dataset-versioning-in-palantir-foundry-8f23de22cc4c

### Gaia 内部
- 实现状态: `docs/architecture/implementation-status.md`
- Action 架构: `docs/architecture/action-architecture.md`
- ADR-011 Action P1: `docs/architecture/adr-011-action-p1.md`
- 差距分析: `docs/research/palantir-capability-gap-analysis.md`
- 事务管理最佳实践: `docs/engineer/transaction-management-best-practices.md`
