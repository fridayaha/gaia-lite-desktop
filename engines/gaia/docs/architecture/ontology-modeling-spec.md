# Ontology Modeling Spec — Skill 迁移到 pydantic-ai Capability

> **版本**: v1.0 | **日期**: 2026-07-08
> **状态**: 已落地（form A）
> **源 skill**: `~/.pi/agent/skills/ontology-modeling/`（pi 的 ontology-modeling 技能）
> **落地点**: `src/ontology/services/ontology_modeling.py` → `build_modeling_capability()`
> **挂载点**: `src/ontology/services/ai_agent.py` → `build_agent()` 的 `capabilities=[...]`

---

## 一、迁移决策：为什么用 pydantic-ai Capability（form A）

### skill 的本质

`ontology-modeling` skill 是一套**给 LLM 的建模方法论提示词**（Palantir 范式：六步法 + 13 红线 + 数据类型规范 + 置信度 + ActionType 语义契约），不是确定性函数库。它的价值在于指导 LLM **怎么建模**，而非**校验已建的模型**。

### 两个候选落地形态

| 形态 | 机制 | 适用场景 | 本次决策 |
|------|------|----------|----------|
| **A. AG-UI Agent 的 Capability** | `Capability(defer_loading=True)`，`get_instructions()` 注入方法论，指导 LLM 调用已有的 `define_object_type` 等工具（带 HITL） | 对话式、逐个/批量建模 | ✅ **采用** |
| B. 独立草稿生成端点 | `output_type=OntologyDraft` + `output_validator`，一次性生成完整草稿 | 从零整体预览建模 | ❌ 不做（后续可选） |

**选 A 的理由**：
1. AG-UI Agent 已挂载全部 write/action toolset（`define_object_type` / `add_property` / `define_link_type` / `invoke_action` + HITL 审批），缺的只是**建模纪律**——正是 skill 方法论的内容。
2. pydantic-ai 的 `Capability` 是"一坨 instructions（+ 可选 tools/hooks）按需加载"的**原生扩展点**（文档原话："If you already keep your skills as Markdown files … you can wrap each one in a Capability"）。skill 迁移到 Capability 是框架设计的正道。
3. `defer_loading=True` 实现 skill-style 渐进式披露：查询/探索类对话不加载方法论（保持 `buildOntologyQueryPrompt` 精简），建模类对话 LLM 主动调 `load_capability` 加载。

---

## 二、pydantic-ai 扩展机制（本次用到的）

| 机制 | 用法 | 在本项目的作用 |
|------|------|----------------|
| `Capability(defer_loading=True)` | 把 instructions 打包成可复用单元，模型按需 `load_capability` 加载 | 承载 skill 方法论，查询时不污染 prompt |
| `get_instructions()` | capability 自带 instructions，经 `_get_instructions` 合并进每个 ModelRequest | 方法论的注入通道（独立于 client/server system prompt 模式） |
| `Agent(capabilities=[...])` | Agent 构造时挂载 capability | `build_agent()` 挂载 `build_modeling_capability()` |

### 关键机制：deferred capability 的 instructions 如何到达模型

deferred capability 的 instructions **不走** `CombinedCapability.get_instructions()`（该方法 `continue` 跳过 `defer_loading=True` 的 capability）。实际路径：

1. 模型看到 catalog（id + description）+ `load_capability` 工具。
2. 模型调用 `load_capability(id='ontology-modeling')`。
3. `DeferredCapabilityLoaderToolset._load_capability` 把 methodology 文本作为**工具返回值**（`{'instructions': methodology_text}`）返回，进入对话历史（`LoadCapabilityReturnPart`）。
4. 后续请求模型在 history 里看到 methodology，据此建模。
5. `loaded_capability_ids` 从 history 重建（`parse_loaded_capabilities`），capability 的 tools（如有）在后续请求暴露。

**本 capability 只带 instructions，不带 tools**——write/action 工具已在 Agent 上（`build_write_toolset` / `build_action_toolset` + `MetadataApprovalToolset` HITL），不重复挂载。

---

## 三、skill 规则 → Gaia 机制映射

### 迁移的（skill 方法论 → instructions）

这些是 skill 的**跨工具建模规范**，单个工具 docstring 装不下，编码进 `_MODELING_METHODOLOGY`：

| skill 规则 | Gaia instructions 内容 |
|------------|------------------------|
| 六步法（实体→动作→规则→数据/安全→校验→迭代） | `## 一、建模六步法`，映射到 Gaia 工具（`define_object_type` 等） |
| 数据类型红线（金额 decimal / 时间 timestamp / 布尔 boolean） | `## 二、数据类型红线`，对齐 `DataType` enum |
| M:N 必须拆分 | `## 三、关系规则`，说明 Gaia LinkType 只有 ONE/MANY，M:N 需引入中间 ObjectType |
| ActionType 仅语义契约，禁运行时策略 | `## 四、ActionType 语义契约`，列出禁止字段（idempotent/retry/timeout/rollback） |
| 置信度标记（confirmed/high/tentative） | `## 五、置信度标记` |
| 并行建模 → 批量审批 | `## 六、并行建模`，对齐 AG-UI 批量审批面板 |

### 不迁移的（Gaia 已有更专业的实现，或架构上无对应机制）

| skill 能力 | 不迁原因 | Gaia 现状 |
|------------|----------|-----------|
| api_name 命名 pattern 校验 | 保存时已强制 | pydantic `Field(pattern=...)` + `naming.py` |
| 同本体唯一 / 主键存在 | 保存时已强制 | `define_object_type_batch` → `ConflictError` / `ValidationError` |
| VIRTUAL 写入 guard | 保存时已强制 | `ActionService.execute_action` 拒绝 VIRTUAL（红线 9） |
| Doris 表名带本体前缀 | 保存时已强制 | `naming.doris_index_table` |
| 数据管道映射 | Gaia 有自己的体系 | `DataSourceService` + `IndexSyncService` + `Pipeline`，与 skill 的 source_system/sync_mode 模型不同 |
| 安全策略（Markings/Organizations/Roles） | Gaia 无此安全模型 | `principal=anonymous`，无 Markings 机制；仅保留"敏感属性在 description 标注"的最小切片 |
| 存量编辑 / 版本追踪 / 回滚 | Gaia 有自己的机制 | 软删除 + `ImpactReport`；skill 的 3 阶段确认 + 回滚快照不迁 |
| 契约测试 | Gaia 有自己的测试体系 | pytest + testcontainers |

### 不可迁移的（skill 红线中 Gaia 结构上已保证的）

| skill 红线 | Gaia 结构保证 |
|------------|---------------|
| ActionType 含运行时策略 | `ActionTypeCreate` schema 本身就没有 idempotent/retry/timeout 字段（结构上不可能塞进去）；instructions 里仍提醒，属 belt-and-braces |
| 抽象 ObjectType 实例化 | Gaia 无 abstract ObjectType 概念（所有 ObjectType 都可实例化） |

---

## 四、与现有 AI 能力的关系

```
POST /ai/agent (AG-UI)
  ├── Agent (build_agent)
  │   ├── toolsets: metadata / object_query / link_traversal / reasoning /
  │   │            canvas_control / write(HITL) / action(HITL)
  │   ├── instructions: [_current_date, _injected_schema, _canvas_state]  ← 每轮注入
  │   └── capabilities: [ontology-modeling (deferred)]                    ← 按需加载 ← NEW
  │
POST /ai/scaffold          ← 单数据集 → 单 ObjectType（data-first，不变）
POST /ai/generate          ← LLM 原语（不变）
POST /ai/stream            ← 流式文本（不变）
```

- **不冲突**：capability 只加 instructions，不改现有 toolset / instructions / output_type。
- **不替代**：`/ai/scaffold`（data-first 单对象）和 capability（business-first 对话式建模）互补。
- **前端无需改动**：capability 是后端 Agent 内部挂载，前端 AG-UI 协议不变；`load_capability` 工具调用由 assistant-ui 标准 `ToolCallPart` 渲染器自动展示。

---

## 五、验证

- **单元测试**: `tests/unit/ai/test_ontology_modeling.py`（14 用例）
  - capability 构建（deferred / instructions-only / description 触发词）
  - 方法论内容（Gaia 工具引用 / 六步法 / 数据类型红线 / M:N 拆分 / ActionType 契约 / VIRTUAL guard / 置信度 / 并行建模）
  - 渐进式披露（查询轮 methodology 不在 prompt / load_capability 工具可见 / 加载后 methodology 在 history）
- **机制验证**: 用 TestModel + `LoadCapabilityCallPart`/`LoadCapabilityReturnPart` 验证 deferred 加载的完整生命周期。
- **端到端验证**（真实 LLM，Marketing 本体）：
  - 简单建模（单对象）：LLM 凭自身能力直接调 `define_object_type`，不加载 capability（合理——简单场景方法论非必需）。
  - 复杂建模（采购系统：供应商/物料/订单/明细 + M:N + 动作）：LLM **主动调 `load_capability`**，方法论被完整应用——M:N 拆出中间实体 `SupplierMaterial`、金额用 DECIMAL、时间用 TIMESTAMP、布尔用 BOOLEAN、状态枚举在 description 列举、敏感属性标注、5 个对象并行建模进一个审批批次。

### 端到端发现并修复的问题

端到端测试发现一个**既有 bug**（非本次引入，但严重影响建模 UX）：write/action 工具的 `ontology` 参数必填且无 `ctx.deps.ontology` 回退，LLM 频繁传 `ontology=""`，用户审批后执行 NotFoundError 失败（"approve then fail" 反模式）。read-only 工具早已有此回退，write/action 缺失。

**修复**：`write.py` / `action.py` 的 6 个写工具（`define_object_type` / `add_property` / `define_link_type` / `link_dataset` / `invoke_action` / `validate_action`）工具体加 `ontology = ontology or ctx.deps.ontology`，与 read-only 工具对齐。测试见 `tests/unit/tools/test_write_action_ontology_default.py`（7 用例）。这是"把简单留给用户"的关键——LLM 不需要记本体名，用户审批后不会因空 ontology 失败。

---

## 六、后续（不在本次范围）

- **form B（草稿生成端点）**: 若需"业务描述→完整本体草稿一次性预览"，可新增 `POST /ai/model-ontology`，用 `output_type=OntologyDraft` + `output_validator`（跨实体校验 M:N/循环依赖用 `ModelRetry`）。与 form A 互补。
- **存量编辑 3 阶段确认**: skill 的 Diff/影响分析/回滚，可基于 Gaia 现有 `ImpactReport` + 软删除扩展。
- **前端建模向导**: `AiSuggestPanel` 接入 capability 加载状态可视化（显示"建模模式已激活"）。
