# 能力关系模型设计

> 文档编号：validation/03
> 版本：v0.1
> 日期：2026-05-15
> 用途：设计 HubItemRelation 模型，定义 Agent/Skill/Tool/MCP 之间的能力关系、管理态关系与运行态依赖的区别，以及 P0 API 和前端展示方案

---

## 1. 为什么需要 HubItemRelation

### 1.1 当前状态

当前 Hub PoC 已完成四类能力资产的管理闭环，但能力之间的关系完全缺失：

- Agent "合规审查"需要 Skill "长文档摘要"，该依赖无法在 Hub 中表达
- Skill "数据分析"调用 Tool "PDF文本抽取"，该调用链无法追溯
- MCP "文件系统MCP"提供 Tool "文件读取"，该提供关系不可见
- 用户查看一个 Agent 详情时，无法知道它依赖哪些能力
- Runtime 引入一个 Skill 时，无法自动解析其所需 Tool 和 MCP

### 1.2 核心价值

| 价值维度 | 说明 |
|----------|------|
| **能力可追溯** | 人和 Agent 可以理解"这个能力需要什么才能工作" |
| **依赖解析** | Runtime 引入一个 Agent 时，可自动解析并列出所有直接和间接依赖 |
| **影响分析** | 某个 Tool 发布新版本，一眼看到哪些 Agent/Skill 调用了它 |
| **安全审计** | 依赖链上的能力风险可聚合评估（一个 blocking Tool 影响所有使用它的 Agent） |
| **市场展示** | 能力详情页展示"被谁依赖"、"依赖了谁"，增强可发现性 |
| **下架安全** | 下架一个能力时，检查是否被其他已发布能力依赖 |

### 1.3 不解决的问题

HubItemRelation **不解决**以下问题 — 这些由 Runtime 负责：

| 不解决 | 归属 |
|--------|------|
| 能力的执行顺序编排 | Runtime 编排引擎 |
| 运行时调用链的动态拓扑 | Runtime Agent |
| 能力间数据流的格式转换 | Runtime Adapter |
| 依赖的能力在线与否 | Runtime Health Check |
| 依赖版本冲突的运行时协商 | Runtime Resolver |

---

## 2. Agent / Skill / Tool / MCP 的关系类型

### 2.1 关系网络示意

```
                    ┌──────────────┐
                    │   Agent      │
                    │  （合规审查）  │
                    └──┬───┬───┬──┘
               uses    │   │   │  depends_on
                       │   │   │
          ┌────────────┘   │   └──────────────────┐
          ▼                ▼                       ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│   Skill      │  │   Skill      │  │   MCP                │
│ （长文档摘要）│  │ （合同分析）  │  │ （文件系统MCP）        │
└──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘
       │                 │                      │
       │ invokes         │ invokes              │ provides
       ▼                 ▼                      ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│   Tool       │  │   Tool       │  │   Tool               │
│（PDF文本抽取）│  │（条款比对）   │  │ （文件读取）           │
└──────────────┘  └──────────────┘  └──────────────────────┘
```

### 2.2 按源类型-目标类型的合法关系矩阵

| 源类型 | 目标 Agent | 目标 Skill | 目标 Tool | 目标 MCP |
|:---:|:---:|:---:|:---:|:---:|
| **Agent** | `uses` | `uses` | `invokes` | `depends_on` |
| **Skill** | — | `uses` | `invokes` | `depends_on` |
| **Tool** | — | — | — | — |
| **MCP** | — | — | `provides` | — |

**规则说明**：

- Tool **不依赖**任何其他能力（Tool 是原子能力，无子任务）
- MCP **只提供** Tool，不依赖其他能力（在当前模型下）
- Agent 可 `uses` 其他 Agent（Agent 组合/编排）
- Skill 可 `uses` 其他 Skill（Skill 组合）
- MCP 不依赖 Skill/Agent/MCP（MCP 是基础设施能力）

### 2.3 关系类型详解

| 关系类型 | 标识 | 语义 | 典型场景 | Runtime 行为 |
|----------|------|------|----------|-------------|
| 使用 | `uses` | 源能力需要目标能力完成其核心任务 | Agent uses Skill；Agent uses Agent | Runtime 在引入源能力时自动引入目标能力（当 scope=runtime 时） |
| 调用 | `invokes` | 源能力在执行中调用目标 Tool | Skill invokes Tool | Runtime 确保 Tool 对源能力可用 |
| 依赖 | `depends_on` | 源能力运行时需要目标 MCP 提供基础设施 | Agent depends_on MCP | Runtime 确保 MCP 连接在源能力启动前就绪 |
| 提供 | `provides` | 源 MCP 声明对外提供哪些 Tool | MCP provides Tool | 声明式关系，用于市场展示和工具发现 |

---

## 3. Management Relation 与 Runtime Dependency 的区别

这是能力关系模型中最重要的概念区分。

| 维度 | Management Relation | Runtime Dependency |
|------|-------------------|-------------------|
| **定义** | 人维护的、用于理解和管理能力关系的声明 | Hub 自动解析、供 Runtime 引入时使用的依赖关系 |
| **作用域标识** | `relation_scope = management` | `relation_scope = runtime` |
| **使用场景** | 能力详情页展示依赖图；市场浏览时理解能力关联 | Runtime 调用 Discover/Resolve 时自动传递依赖 |
| **是否触发自动引入** | 否 — 仅供人查看 | 是 — Runtime 在 resolve 时递归展开 |
| **维护者** | 能力维护者手动创建 | 能力维护者创建（声明为 runtime）；Hub 通过 manifest 导入时也可自动创建 |
| **典型例子** | "Agent A 的实现参考了 Skill B 的设计" | "Agent A 运行时必须加载 Skill B" |

### 为什么需要这个区分

如果 Agent A `uses` Skill B，但只是"概念参考"而非"运行时依赖"，让 Runtime 自动拉取 Skill B 会导致：

- 不必要的依赖膨胀（Runtime 加载了不需要的能力）
- 权限污染（Agent 获得了它不需要的 Skill 的权限声明）
- 性能下降（依赖树深度增加）

通过 `relation_scope` 分离两种语义，让管理态和运行态各取所需。

---

## 4. RelationType 枚举

```python
class RelationType(str, Enum):
    uses = "uses"
    invokes = "invokes"
    depends_on = "depends_on"
    provides = "provides"
```

本枚举不按源类型拆分。通过创建关系时的 API 层校验（见 §7）保证类型组合的合法性。

---

## 5. RelationScope 枚举

```python
class RelationScope(str, Enum):
    management = "management"
    runtime = "runtime"
```

- `management`：仅人浏览使用，不参与 Runtime 解析
- `runtime`：Runtime Discover/Resolve 时解析并递归展开

**默认值**：用户手动创建关系时默认为 `management`（避免无意间触发自动拉取）；从 manifest 导入时，manifest 中 `scope` 字段决定。

---

## 6. Required 字段

| 值 | 语义 | Runtime 行为 |
|:---:|------|-------------|
| `true` | 必须依赖 | Runtime 必须能解析到目标能力，否则引入失败 |
| `false` | 可选依赖 | Runtime 尽量解析，解析不到不阻塞引入 |

**典型场景**：
- Agent "智能搜索" `depends_on` MCP "企业知识库MCP" → required=true（没有知识库无法工作）
- Agent "文档助手" `uses` Skill "OCR识别" → required=false（有 OCR 更好，没有也能工作）

---

## 7. VersionPolicy 枚举

```python
class VersionPolicy(str, Enum):
    current = "current"       # 使用目标的当前 published 版本
    fixed = "fixed"           # 绑定到 target_version_id 指定的版本
    compatible = "compatible" # Major 相同的最新版本
```

| 策略 | 适用场景 | 风险 |
|------|----------|------|
| `current` | 总是用最新的，适合内部工具链 | 目标版本更新可能引入 breaking change |
| `fixed` | 生产关键路径，版本锁定 | 目标版本修复 bug 或安全漏洞后需手动更新关系 |
| `compatible` | 希望跟随大版本内更新 | 目标 Minor/Patch 更新引入意外行为 |

**默认值**：`compatible`（在稳定与灵活之间取平衡）。

---

## 8. P0 API

### 8.1 创建关系

```
POST /api/hub/relations
```

**Request Body**：

```json
{
  "source_item_id": "uuid",
  "source_version_id": "uuid | null",
  "target_item_id": "uuid",
  "target_version_id": "uuid | null",
  "relation_type": "uses",
  "relation_scope": "runtime",
  "required": true,
  "version_policy": "compatible",
  "description": "Agent 合规审查使用长文档摘要 Skill 生成文档概览"
}
```

**校验规则**（P0 阶段）：

| 规则 | 拒绝码 |
|------|:---:|
| source_item_id 和 target_item_id 必须不同 | 400 |
| relation_type 必须符合 §2.2 的合法关系矩阵 | 400 |
| 不能创建已存在的 (source_item_id, target_item_id, relation_type) 组合 | 409 |
| source_item 和 target_item 必须存在且未被 archived | 404 |
| target_item 的 status 不能为 disabled 或 archived | 400 |
| 不允许创建导致循环依赖的关系 | 400 |

**循环依赖检测算法**（P0 简化版）：

```
在创建 (A uses B) 之前：
  1. 从 B 出发做 BFS/DFS
  2. 如果路径上出现 A → 拒绝创建（A-B-A 环）
  3. 深度限制：≤ 10 层（能力关系图预期深度远小于 10）
```

### 8.2 查询关系

**按源能力查询（谁被依赖）**：
```
GET /api/hub/items/{item_id}/relations?direction=outgoing
```

**按目标能力查询（依赖了谁）**：
```
GET /api/hub/items/{item_id}/relations?direction=incoming
```

**查询指定 scope**：
```
GET /api/hub/items/{item_id}/relations?scope=runtime
```

**查询指定 type**：
```
GET /api/hub/items/{item_id}/relations?relation_type=uses
```

### 8.3 删除关系

```
DELETE /api/hub/relations/{relation_id}
```

删除关系不删除源或目标能力本身。删除后，Runtime 不再解析该依赖。

---

## 9. 前端展示方案

### 9.1 能力详情页 — 依赖与被依赖

在能力详情页底部新增两个表格：

**依赖的能力**（outgoing）：
| 类型 | 名称 | 关系 | 版本策略 | 必须 |
|------|------|------|----------|:---:|
| Skill | 长文档摘要 | uses | compatible | ✅ |
| Tool | PDF文本抽取 | invokes | current | ✅ |
| MCP | 文件系统MCP | depends_on | fixed(v0.1.0) | — |

**被依赖的能力**（incoming）：
| 类型 | 名称 | 关系 | 版本策略 |
|------|------|------|----------|
| Agent | 合同审查Agent | uses | compatible |
| Skill | 知识检索 Skill | uses | current |

### 9.2 能力详情页 — 依赖关系图（P1）

使用简单的力导向图或树形图展示直接依赖 + 间接依赖（最多展开 2 层）。

### 9.3 创建关系表单

在能力详情页增加"添加依赖"按钮 → 弹出搜索框 → 搜索目标能力 → 选择关系类型/scope/required/version_policy → 提交。

### 9.4 Scope 视觉区分

- `runtime` 关系：蓝色标记 + "运行时"
- `management` 关系：灰色标记 + "管理"

---

## 10. 不做工作流编排的边界

本节明确 HubItemRelation 与工作流编排的区别，避免关系模型被误解为编排引擎。

| 能力关系（Hub） | 工作流编排（Runtime） |
|-----------------|----------------------|
| **声明式**：声明 Agent 需要 Skill | **命令式**：定义 Agent 调用 Skill 的时机和条件 |
| **静态**：关系在能力注册时创建 | **动态**：运行时根据上下文选择执行路径 |
| **版本时**：关联的是能力版本 | **实例时**：关联的是实际运行的 instance |
| **无顺序**：不使用 → 表示 "A 之后 B" | **有序**：DAG 定义执行顺序和条件分支 |
| **无数据流**：不定义能力间数据传递格式 | **有数据流**：定义输入输出的转换和映射 |
| **无状态**：不记录关系的运行时状态 | **有状态**：记录执行成功/失败/重试/超时 |
| **Hub 负责** | **Runtime 负责** |

### 不应在 HubItemRelation 中做的事

- ❌ 定义 Agent 调用 Skill 的顺序
- ❌ 定义条件逻辑（"if A 失败 then B"）
- ❌ 定义并行/串行执行策略
- ❌ 定义重试次数和超时时间
- ❌ 定义能力间的数据映射

### 可以在 HubItemRelation 中做的事

- ✅ 声明 Agent 需要 Skill B 才能工作
- ✅ 标记这个需要是必须的还是可选的
- ✅ 指定使用的版本策略（current/fixed/compatible）
- ✅ 声明这个关系是给人看的还是给 Runtime 用的

---

## 11. 与现有模型的集成

### 11.1 与 HubItem 的关系

```
HubItem (1) ────── (N) HubItemRelation (作为 source)
HubItem (1) ────── (N) HubItemRelation (作为 target)
```

### 11.2 与 HubItemVersion 的关系

- `source_version_id` → 可空。空表示"适用于该 Item 的所有版本"
- `target_version_id` → 可空。空表示"取当前 published 版本"；如果 `version_policy=fixed` 则必须指定

### 11.3 与 Manifest 导入的关系

Manifest Spec v0.1 中的 `relations` 字段在导入时自动创建 HubItemRelation 记录：

```json
{
  "relations": [
    {
      "target_name": "pdf-text-extractor",
      "relation_type": "invokes",
      "relation_scope": "runtime",
      "required": true,
      "version_policy": "compatible"
    }
  ]
}
```

导入时 `target_name` 解析为 `target_item_id`（需在已有能力中查找；找不到则告警并跳过该关系）。

---

## 12. P0 实施清单

| # | 任务 | 依赖 |
|---|------|------|
| 1 | HubItemRelation 模型 + Alembic migration | 无 |
| 2 | RelationType / RelationScope / VersionPolicy 枚举 | #1 |
| 3 | 创建关系 API（含校验：类型矩阵 + 唯一性 + 循环依赖） | #1 #2 |
| 4 | 查询关系 API（outgoing / incoming / scope / type 过滤） | #1 |
| 5 | 删除关系 API | #1 |
| 6 | 前端详情页"依赖的能力"表格 | #4 |
| 7 | 前端详情页"被依赖的能力"表格 | #4 |
| 8 | 前端"添加依赖"按钮 + 搜索弹窗 + 表单 | #3 |
| 9 | Manifest 导入时解析 relations 字段 | #3 |
| 10 | tests: 关系 CRUD + 唯一约束 + 循环检测 + 类型矩阵校验 | #1-#9 |

---

> 配套文档：
> - `docs/validation/04_runtime_discover_design.md` — Runtime Discover/Resolve 设计（依赖解析依赖 HubItemRelation）
> - `docs/14_hub_capability_market_solution_design.md` — 整体方案设计（§4·§5）
