# ADR-019：三入口能力分层原则（对外操作面 vs 内部管理面）

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳 |
| **审批日期** | 2026-07-15 |
| **影响层** | `protocols/mcp_server.py`（MCP 工具边界）、`routes/`（REST 管理面定位）、`tools/toolsets/`（AG-UI 操作面定位）、`services/*_logic`（共享逻辑层契约） |
| **相关文档** | [ADR-009 本体工具层](./adr-009-ontology-tool-layer.md)（三入口接入方式，本 ADR 深化其能力边界判据）、[reference.md](../reference.md)（Palantir 范式：Ontology API 吃结构化 IR，NL 转换在 AIP Agent 层） |
| **前置决策** | ADR-009 §3 已确立三入口（MCP/AG-UI/REST）的接入方式，但未明确"哪些能力归哪个入口"的判据。本 ADR 补齐该判据。 |

---

## 背景

ADR-009 确立了本体工具层的三入口接入方式（MCP 对外 / AG-UI 对内 Agent / REST 全功能），但留下一个未决问题：**当一个新能力被实现后，它该进哪个入口？** 当前判据是隐性的——分散在 `mcp_server.py` 的工具注册清单、`ai_agent.py` 的 toolset 挂载、各路由文件的存在性中。

这导致两个风险：

1. **边界漂移**：未来开发者无法判断"MCP 没有 ActionType 定义"是有意为之还是遗漏，可能随手把管理面能力塞进 MCP，破坏对外操作面的简洁性。
2. **契约不稳**：MCP 工具签名是已发布契约（外部客户端依赖），如果管理面能力被错误加入后又移除，对客户端是 breaking change。

Gaia 的定位已明确：**MCP 是对外暴露的操作面，内部细节不需要暴露**。本 ADR 把这个定位固化为可操作的判据。

---

## 决策

### 1. 三入口的角色定位

| 入口 | 定位 | 消费者 | 能力范围 |
|------|------|--------|---------|
| **MCP** | **对外操作面** | 外部 Agent（Cursor / Claude Desktop / 自建 Agent） | "用本体"：查询、推理、执行已定义 Action、即席轻量建模 |
| **AG-UI** | **对内 Agent 操作面** | Gaia 内置 Web UI 的 Agent | 同 MCP（操作面），进程内直挂，不走 MCP 协议 |
| **REST** | **全功能管理面** | 脚本 / 后端 / 运维 / 管理台前端 | "用本体" + "造本体" + "数据工程" + "运维" |

**核心原则**：MCP 和 AG-UI 是操作面，只暴露"用本体"的能力；REST 是管理面，额外暴露"造本体"和"数据工程"能力。AG-UI 与 MCP 的能力集**保持对等**（操作面不应因协议不同而能力不同）。

### 2. 能力分层判据

一个能力是否进入操作面（MCP/AG-UI），用以下判据判定：

| 判据 | 操作面（MCP/AG-UI） | 管理面（REST only） |
|------|---------------------|---------------------|
| **是否产生不可逆 schema 变更** | 否（单对象/属性/链接的即席建模，可删可改） | 是（ActionType 定义/版本回滚、数据源接入、Pipeline 编排） |
| **是否属数据工程内部细节** | 否 | 是（数据源 CRUD、CDC 同步任务、数据集治理） |
| **是否属批量运维** | 否（Agent 应单步决策单步执行） | 是（批量 Action、批量重建索引、批量同步） |
| **操作频率** | 高频（Agent 推理循环中反复调用） | 低频（建模/接入时一次性操作） |
| **是否需要人工评审** | 否（HITL elicit 即可） | 是（schema 变更需评审/确认） |

满足"操作面"全部判据的能力才进 MCP/AG-UI；任一判据落"管理面"侧的，仅 REST 暴露。

### 3. 当前能力归属（依据判据裁定）

#### 操作面（MCP + AG-UI + REST 三入口都有）

| 能力 | MCP 工具 | AG-UI 工具 | REST 端点 | 判据 |
|------|---------|-----------|----------|------|
| 列举本体 | `list_ontologies` | — | `GET /ontologies` | 只读发现 |
| 列举/描述对象类型 | `list_object_types` / `describe_object_type` | 同 | `GET /ontologies/{ont}/object-types` | 只读发现 |
| 列举/描述链接类型 | `list_link_types` / `describe_link_type` | 同 | `GET /ontologies/{ont}/link-types` | 只读发现 |
| SQL 属性查询 | `query_with_sql` | 同 | `POST /objects/{ont}/textsql` | 只读查询 |
| ObjectSet IR 推理查询 | `query_with_dataframe` | 同 | `POST /objects/{ont}/query-dataframe` | 只读查询 |
| 单跳关系遍历 | `traverse_link` | 同 | `POST /objects/{ont}/traverse` | 只读推理 |
| 关系存在性检查 | `exists_link` | 同 | `POST /objects/{ont}/exists-link` | 只读推理 |
| 多跳路径推理 | `find_paths` | 同 | `POST /objects/{ont}/find-paths` | 只读推理 |
| Action 参数预检 | `validate_action` | 同 | `POST /actions/validate/{ont}/{ot}/{action}` | 只读预检 |
| Action 执行 | `invoke_action` | 同 | `POST /actions/execute/{ont}/{ot}/{action}` | HITL 确认后执行已定义 Action |

> **注**：`list_ontologies` 在 AG-UI 不暴露是有意为之（Web UI 总有固定 ontology 上下文，无需列举）。这是 AG-UI 的唯一偏差，不破坏操作面能力对等原则——外部 Agent 才需要发现有哪些本体。

#### 即席建模（AG-UI + REST 有，MCP 不暴露）

| 能力 | MCP | AG-UI 工具 | REST 端点 | 说明 |
|------|:---:|-----------|----------|------|
| 即席对象类型建模 | ❌ | `define_object_type` / `add_property` | `POST /ontologies/{ont}/object-types` | 单对象轻量建模，可删改 |
| 即席链接类型建模 | ❌ | `define_link_type` | `POST /ontologies/{ont}/link-types` | 单链接轻量建模，可删改 |
| 对象类型绑定数据集 | ❌ | `link_dataset` | `PATCH /ontologies/{ont}/object-types/{type}/dataset-link` | 绑定属性到物理列 |

> **有意不对等**：即席建模是「造本体」能力，属于产品内部 Agent（AG-UI）与数据工程师（REST 管理面）的职责范围，不对 MCP 外部 Agent 开放。外部 Agent 只在现有本体上查询与执行 Action；需要新增类型时，由用户在产品内完成或由数据工程师经 REST 完成。这是 §4 对等原则的**有意例外**，与 `list_ontologies` 的 AG-UI 偏差性质不同（后者是协议必然差异，此处是能力范围裁定）。

#### 管理面（仅 REST）

| 能力 | REST 端点 | 不进操作面的判据 |
|------|----------|-----------------|
| ActionType 定义/更新/版本回滚 | `POST/PUT /actions/types/...` | 产生不可逆 schema 变更 + 需评审 |
| 批量 Action 执行 | `POST /actions/execute-batch` | 批量运维（Agent 应单步执行） |
| Action 预览（mutation dry-run） | `POST /actions/preview/...` | 诊断工具，操作面用 `validate_action` 即可 |
| 数据源 CRUD + 探索 | `/api/datasources/...` | 数据工程内部细节 |
| 数据集治理 + CDC/时序同步任务 | `/api/datasources/.../sync` | 数据工程内部细节 |
| Pipeline 建模 + 编排 | `/pipelines/...` | 数据编排内部细节 |
| 索引 provision/rebuild/deprovision | `/api/datasources/.../index/...` | 运维（批量 + 不可逆） |
| 权限/Principal 管理 | `/api/permissions/...` | 安全配置（需评审） |

### 4. 操作面能力对等原则

**MCP 与 AG-UI 的操作面能力集必须对等**——同一能力要么两入口都有，要么都没有。例外：

1. **协议必然差异**：如 `list_ontologies` 因 AG-UI 有隐式 ontology 上下文而省略。
2. **能力范围裁定**：即席建模（`define_object_type` / `add_property` / `define_link_type` / `link_dataset`）经裁定为产品内部能力，仅 AG-UI + REST 暴露，MCP 不暴露。依据：造本体是内部职责，外部 Agent 应在现有本体上操作（见 §3「即席建模」表）。

这确保：
- 外部 Agent 和内置 Agent 在「用本体」能力上对等（查询/推理/执行 Action 三入口一致）
- 共享 `*_logic` 函数是真正的单一真相源（不是"MCP 版"和"AG-UI 版"两套逻辑）
- 工具契约（`_contracts.py` 中的 `*_DESC`）只写一份，三入口共用

**历史教训**：`find_paths` 曾长期只在 REST + AG-UI 暴露，MCP 缺失——外部 Agent 无法做多跳路径推理，而内置 Agent 可以。这违反了对等原则，已于 2026-07-15 修复（MCP 补 `find_paths` 工具）。

### 5. 边界的可演化性

能力归属不是永久的。如果一个"管理面"能力未来被证明适合操作面（如某个数据工程操作被简化为单步、可逆、高频），可按判据重新裁定后加入 MCP/AG-UI。但每次变更必须：
1. 更新本 ADR 的能力归属表
2. 更新 MCP `instructions` 的能力边界声明
3. 评估对已发布 MCP 客户端的兼容性（新增是兼容的，移除是 breaking）

---

## 后果

### 正面

- **外部集成者边界清晰**：MCP 握手即知能力边界（`instructions` 声明 + 本 ADR 固化），不会误试管理面操作
- **契约稳定**：操作面能力集有判据约束，不会因个别开发者随手加工具而膨胀
- **共享逻辑层正当性确立**：`*_logic` 函数作为三入口单一真相源，有了明确的"服务对象"（操作面能力）
- **演化路径明确**：能力归属变更走 ADR 流程，避免隐性漂移

### 负面 / 约束

- **MCP 客户端无法做管理面操作**：外部 Agent 不能经 MCP 定义 ActionType 或管理数据源——这是有意的边界，外部 Agent 需管理面能力时应走 REST（需另行鉴权）
- **AG-UI 与 MCP 必须同步维护**：新增操作面工具时两个入口都要注册，增加维护成本（但 `_contracts.py` 共享描述已降低该成本）
- **判据需随业务演化**：如"即席建模"与"schema 工程"的边界模糊时，需用判据表重新裁定

---

## 实施记录

| 日期 | 事项 |
|------|------|
| 2026-07-15 | ADR 创建。同步修复 MCP `find_paths` 缺失（操作面对等原则）、REST 补 `/actions/validate` 端点（操作面三入口对等）、MCP `instructions` 补能力边界声明 |
| 2026-07-15 | cursor 分页能力下推到 `query_with_dataframe_logic`（操作面完整性，见本 ADR §4 对等原则）：MCP / AG-UI / REST 三入口统一走 logic 函数，cursor 作为 logic 首类参数透传到 `DataFrameQueryService.execute`；REST 从直接调 service 改为走 logic，获得与 MCP/AG-UI 一致的 audit 覆盖 |
| 2026-07-16 | 即席建模从操作面收紧为「AG-UI + REST 有、MCP 无」。MCP 撤除 `define_object_type` / `add_property` / `define_link_type` / `link_dataset` 四个工具注册（`invoke_action` 保留）；§3 拆出「即席建模」独立表；§4 对等原则增加「能力范围裁定」例外类。同步修正 `link_dataset` REST 端点记录错误（原误记为 link-types，实为 `PATCH /ontologies/{ont}/object-types/{type}/dataset-link`） |
