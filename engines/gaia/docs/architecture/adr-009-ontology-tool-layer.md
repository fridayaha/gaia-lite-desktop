# ADR-009：本体工具层（本体能力向 Agent 暴露）

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳 |
| **审批日期** | 2026-06-19 |
| **影响层** | 新增 `tools/` + `protocols/` 包；扩展 `services/object_query_service.py`；改造 `services/ai_agent.py`、`routes/ai.py`；解耦 `config/container.py` |
| **相关文档** | [ontology-tool-layer.md](./ontology-tool-layer.md)（完整能力架构）、[reference.md](../reference.md)（**Palantir 原始范式参照**，本 ADR 的能力派生自该文档对 Foundry 本体→AIP 工具体系的拆解）、[ai-integration-guide.md](../engineer/ai-integration-guide.md)（v3.0 AG-UI 集成） |
| **后续 ADR** | HITL 审批机制（Sprint 2）、治理 Principal/权限（Sprint 3）将单独成文 |

---

## 背景

Gaia 已实现数据层（Iceberg/Doris/Trino/Gravitino/PG）+ 本体元数据层（ObjectType/LinkType/ActionType CRUD + 15 个 Service）。本体能力目前只通过 REST 路由对外，**缺一层"本体能力如何提供给 Agent 使用"**。

参照 Palantir Foundry 本体→AIP 的范式（见 `docs/reference.md`）：本体工具应是**从本体元数据自动派生**（建模即工具），而非手工封装；且应**协议化暴露**，让任意外部 Agent 都能消费，不只服务项目内的 pydantic-ai Agent。

现有 `services/ai_agent.py` 的 `suggest_object_types`/`apply_suggestions` 是反例——手工封装、绕过 Service、靠前端二次解析 JSON，正是 reference.md 批判的"手工封装工具"范式，需替换。

## 决策

在现有架构上补一层"本体工具层"，遵循以下核心决策：

### 1. 工具定义用 pydantic-ai 原生能力

工具/工具集用 pydantic-ai `FunctionToolset` / `@tool` 定义，不自造 `ToolContract`/`ToolProvider` 抽象。工具契约（name/description/parameters schema）由函数签名 + 类型注解 + docstring 自动生成。

### 2. MCP 暴露用 FastMCP（pydantic-ai MCP 底层，不算额外引入）

外部 Agent 经 MCP 消费，用 `FastMCP` 把工具暴露为 MCP server。`fastmcp` 是 pydantic-ai MCP 能力的必经底层依赖（`pydantic-ai-slim[mcp]` 即拉入），不视为额外引入协议框架。

### 3. 三入口各自以最自然方式接入同一组工具函数

| 消费者 | 接入方式 | 是否经 MCP |
|--------|---------|-----------|
| 外部 Agent（Cursor/Claude Desktop/自建） | MCP（FastMCP，stdio + Streamable HTTP 双传输） | 是 |
| Gaia 内置 Web UI | AG-UI（pydantic-ai `Agent(toolsets=[...])`，进程内直接挂载） | **否** |
| 脚本/后端 | REST（现有路由） | 否 |

**关键**：AG-UI 入口在 FastAPI 进程内，pydantic-ai Agent 直接持有 FunctionToolset，工具调用是进程内函数调用，**不经 MCP**。MCP 仅是给外部 Agent 用的协议出口，不是内部总线。工具逻辑只写一次，三入口共享，零重复定义。

### 4. 工具粒度：通用式 + 元层配套

实例层工具用通用式 `get_object(object_type=...)`，不按 ObjectType 展开成 `get_Order`/`get_Customer`。配合元层工具（`list_ontologies`/`list_object_types`/`describe_object_type`/`describe_link_type`）让 Agent 先确认有哪些本体对象，再操作。

### 5. 元层/实例层统一用 MCP Tools 暴露

不用 MCP Resources。本体 schema 等自描述数据也作为 Tools 暴露，Agent 主动按需拉取，与 reference.md 对齐。

### 6. 多本体隔离：工具显式带 `ontology` 参数

所有实例层工具带 `ontology` 参数（可选，默认取 Gaia 默认本体）。相信 LLM 能正确传递，不做 session 级上下文绑定（MCP 无状态语义不自然）。

### 7. MCP 独立进程

`ontology-mcp` 作为独立进程（`pyproject.toml` 的 `[project.scripts]` 入口），支持 `--stdio`（本地 IDE）和 `--http`（远程 Agent）双传输。进程内复用 `config/container.py` 的 DI 工厂构造 Service 依赖图，不起 FastAPI HTTP。**需核实并解耦 container 与 FastAPI 的耦合**（CLAUDE.md 分层强调 DI 容器独立于 Routes）。

### 8. 下推语义严格限定

"下推"一词仅指 **VIRTUAL 对象经 Trino 向外部源透传谓词/聚合**。MANAGED 对象走现有 Doris 索引层过滤/聚合（本地计算）+ Iceberg 属性点查链路，Doris 不可用降级 Trino 扫 Iceberg——这不叫"下推"。Doris 是 Gaia 自有索引层，数据在 Doris 里，做聚合是本地计算，不存在"下推到 Doris"的概念。

工具层不感知 `storage_type`，由 `ObjectQueryService` 按现有 `_load_physical`/`_load_virtual` 路由模式分叉。

### 9. MVP 范围：14 个只读工具

元层 4 + 检索 5 + 聚合 2 + 关系 2，全只读、无 HITL。动作族（`invoke_action`/`validate_action`）+ 写类工具留 Sprint 2（待 HITL 方案定）。完整能力清单与工具描述见 [ontology-tool-layer.md](./ontology-tool-layer.md)。

### 10. 现有演示工具直接删除

`ai_agent.py` 的 `suggest_object_types`/`apply_suggestions`/`confirm_action`/`AppState` 演示字段直接删。前端 `AiSuggestPanel` 的"建议→应用"流程破坏性变更——MVP 期间 Gaia 内置 Web UI 对话降级为只读（Agent 能查不能建），写类工具留 Sprint 2。

> **✅ Sprint 2 已完成（commit 584af2c）**：写工具（define_object_type/add_property/define_link_type/link_dataset）+ 动作工具已挂 Agent，经 MetadataApprovalToolset HITL 批量审批 + impact_builder 自然语言影响预览；ontology_modeling.py 以 Capability form A 按需注入建模方法论。多轮对话式本体建模已可用（AG-UI Thread 多轮上下文 + message_history 透传）。

### 11. HITL 与治理 Principal 暂遗留

MCP 下的 HITL（MCP elicitation vs 业务层确认）与治理 Principal（权限/审计主体）暂遗留，Sprint 2/3 单独 ADR。MVP 审计先记 anonymous。

## 替代方案

| 方案 | 否决理由 |
|------|---------|
| 自造 ToolContract/ToolProvider/Protocol Adapter 抽象 | 过度设计，pydantic-ai toolset + FastMCP 已覆盖 |
| 用 `mcp` SDK 手写 server（不装 fastmcp） | 代码量大，pydantic-ai MCP 路径官方走 fastmcp |
| 元层用 MCP Resources 暴露 | 与 reference.md 不一致，Agent 用法不直观 |
| 工具按 ObjectType 展开成 `get_Order` 等 | 工具数随本体线性增长，通用式 + 元层配套更平衡 |
| AG-UI 也经 MCP 调工具 | 进程内调用无需协议跃层，徒增序列化开销 |
| 多本体用 session 上下文（`set_context`） | MCP 无状态，session 语义不自然 |
| 统一下推走 Trino | 违反 CLAUDE.md 分层红线，MANAGED 应走 Doris 索引层 |

## 后续工作

| 项 | 阶段 | 说明 |
|----|------|------|
| `FunctionToolset` → `FastMCP` 桥接 API 落地核实 | ✅ 已验证 | fastmcp 3.4.2：`FastMCP.add_tool(tool.function)` 自动用函数名作 MCP 工具名、从注解+docstring 生 schema，与 pydantic-ai 一致；13 工具全经 MCP 可见，6 端到端测试通过（`tests/unit/protocols/test_mcp_server.py`） |
| `config/container.py` 与 FastAPI 解耦核实 | Sprint 1 实现期 | 确认 container 能脱离 `app` 生命周期独立构造 Service |
| `DorisIndexStore` 聚合能力扩展核实 | Sprint 1 实现期 | 核实 Doris 4.0.5 对 sum/avg/min/max/groupby 的支持，必要时扩 Layer 方法 |
| VIRTUAL 对象 Trino 聚合下推核实 | Sprint 1 实现期 | Trino→外部源的谓词/聚合透传依赖外部源能力，可能不全支持 |
| 动作族工具 + HITL 方案 | Sprint 2 | MCP elicitation 客户端兼容性评估，单独 ADR |
| 写类工具（`define_object_type` 等） | Sprint 2 | 随 HITL 一起，恢复前端"建议→应用"流程 |
| 治理 Principal + 权限 + 审计入库 | Sprint 3 | 单独 ADR |
| 语义检索（Doris 向量索引） | Sprint 3 | 依赖 Doris 向量索引成熟度 |
| 函数族（Ontology Function） | 远期 | 需先建 Function 抽象 |
| 场景族（CoW 沙箱） | 远期 | 工作量大，按真实需求触发 |

## 参考

- [docs/reference.md](../reference.md) — Palantir 本体→Agent 工具体系深度拆解
- [docs/architecture/ontology-tool-layer.md](./ontology-tool-layer.md) — 完整能力架构（交接文档）
- [docs/architecture/adr-010-ontology-hitl.md](./adr-010-ontology-hitl.md) — Sprint 2 HITL 分级审批（后续 ADR）
- [docs/architecture/implementation-status.md](./implementation-status.md) — 实现状态 + 后续路标（§三-bis）
- [docs/engineer/ai-integration-guide.md](../engineer/ai-integration-guide.md) — v3.0 AG-UI 集成（本 ADR 改造其 `ai_agent.py`/`routes/ai.py`）
- [CLAUDE.md](../../CLAUDE.md) — 分层红线（Doris 索引层 / Trino 联邦 / VIRTUAL 表定义）+ 规范 8（联邦查询 SQL 不手写翻译器）
