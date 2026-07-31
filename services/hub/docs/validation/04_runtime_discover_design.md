# Runtime Discover / Resolve 设计

> 文档编号：validation/04
> 版本：v0.1
> 日期：2026-05-15
> 用途：设计 Runtime Discover 和 Resolve 接口，定义 Agent 自主搜索能力的边界、Hub/Runtime/IAM 职责划分，以及缓存和性能策略

---

## 1. 为什么 Runtime Discover 是平台特色

### 1.1 当前状态

当前 Hub 已有管理态搜索 API（`GET /api/hub/items`），但它是面向**人类管理员**的：
- 返回所有状态的能力（含 draft/pending_review/disabled）
- 无权限上下文过滤
- 不解析依赖关系
- 不返回运行时所需的结构化配置

Agent / Runtime 需要一个专门的服务接口：只返回**可安全使用的、已发布的、有权限的**能力及其完整配置。

### 1.2 Runtime Discover 的平台价值

| 价值 | 说明 |
|------|------|
| **Agent 自主组装** | Agent 在运行时可以搜索并请求引入新能力，而不需要人类预先配置所有依赖 |
| **可信发现** | Hub 作为唯一权威源保证发现的能力是经过审批、扫描、发布的 |
| **安全边界** | Hub 基于 Agent 的权限上下文过滤能力，Agent 不能发现它没有权限使用的能力 |
| **依赖透明** | resolve 返回完整依赖树，Agent/Runtime 无需自己遍历依赖 |
| **生态互通** | 标准化的 Discover/Resolve 协议使不同 Runtime 实现都能接入 Hub |
| **与开源对齐** | 接口语义参考 AgentRegistry 的 Discover 协议，未来可与外部 Registry 互通 |

### 1.3 不解决的问题

Runtime Discover **不负责**以下内容 — 这些由 Runtime 层处理：

| 不负责 | 归属 |
|--------|------|
| 能力的实际加载和实例化 | Runtime Agent |
| 依赖能力的下载和部署 | Runtime Package Manager |
| 运行时健康检查 | Runtime Health Monitor |
| 能力间的编排和执行 | Runtime Orchestrator |
| 能力执行的沙箱隔离 | Runtime Sandbox |

---

## 2. Agent 自主搜索能力的边界

### 2.1 Agent 可以做的事

| # | 操作 | 条件 |
|---|------|------|
| 1 | 搜索已发布且可发现的能力 | type/keyword/category/tags 过滤 |
| 2 | 查看能力的 manifest 和配置 | 能力已发布且 Agent 在权限上下文中有访问权 |
| 3 | 获取能力依赖关系 | 通过 resolve 接口递归展开 |
| 4 | 查看能力风险摘要 | 了解依赖链的风险概况 |
| 5 | 请求 Runtime 引入能力 | 通过 Runtime 的引入机制，而非直接通过 Hub |

### 2.2 Agent 不可以做的事

| # | 禁止操作 | 原因 |
|---|----------|------|
| 1 | 绕过 permission 过滤发现能力 | Hub 按 agent_id + workspace_id 过滤可见范围 |
| 2 | 获得超出其被授予范围的权限 | 能力的 permission_json 声明只是信息，Agent 的实际权限由 Runtime Policy Engine 判定 |
| 3 | 使用 risk_level=blocking 的能力 | Discover 接口不返回 blocking 能力 |
| 4 | 让 Hub 执行能力 | Hub 不是 Runtime |
| 5 | 修改/审批/发布/下架能力 | 管理态 API 仅限人类用户 |
| 6 | 访问其他 Agent 的私有能力 | workspace_id + agent_id 权限边界 |
| 7 | 发现 disabled/archived 的能力 | 已下架能力不可发现 |

### 2.3 边界设计原则

> Agent 可以搜索和请求能力，但 Hub 只提供**可信的目录**。
> Agent 能否使用能力，最终由 Runtime Policy Engine 根据 Agent 的被授予权限判定。
> Hub 的 Discover 是"门禁"（准入过滤），Runtime Policy Engine 是"门禁后"（精细化策略）。

---

## 3. Discover API 设计

### 3.1 接口定义

```
GET /api/runtime/capabilities/discover
```

**目标**：Agent 调用此接口搜索可用的已发布能力。

### 3.2 请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `type` | string | — | — | agent / mcp / skill / tool |
| `keyword` | string | — | — | 名称/描述全文搜索（ILIKE） |
| `category` | string | — | — | 分类过滤 |
| `tags` | string[] | — | — | 标签过滤（逗号分隔，AND 语义） |
| `scenario` | string | — | — | 场景过滤 |
| `industry` | string | — | — | 行业过滤 |
| `risk_level_max` | string | — | medium | 风险上限。`low` 只返回 low；`medium` 返回 low+medium；`high` 返回 low+medium+high |
| `runtime` | string | — | — | 运行时兼容性过滤（如 `python>=3.10`） |
| `agent_id` | string | — | — | Agent 身份（预留，接入 IAM 后必填） |
| `workspace_id` | string | — | — | 工作空间（预留，接入 IAM 后必填） |
| `limit` | int | — | 50 | 返回数量上限（最大 200） |
| `offset` | int | — | 0 | 分页偏移 |

### 3.3 返回结构

```json
{
  "items": [
    {
      "item_id": "uuid",
      "name": "长文档摘要 Skill",
      "type": "skill",
      "description": "长文档摘要 Skill，支持 50 页以上 PDF",
      "category": "文档处理",
      "tags": ["摘要", "PDF"],
      "industry": "通用",
      "scenario": "文档审阅",
      "risk_level": "low",
      "current_version": "0.1.0",
      "current_version_id": "uuid",
      "publisher": "文档处理团队"
    }
  ],
  "total": 42,
  "limit": 10,
  "offset": 0
}
```

**注意**：Discover 只返回能力摘要，不返回完整 manifest/config。Agent 需要详细信息应调用 Resolve。

### 3.4 系统级过滤规则（自动应用，不暴露为参数）

以下过滤规则在服务端自动执行，调用方无法绕过：

| # | 过滤规则 | 原因 |
|---|----------|------|
| 1 | `status = published` | 只有发布的能力可用 |
| 2 | `status != disabled AND status != archived` | 下架和归档的能力不可发现 |
| 3 | `risk_level != blocking` | blocking 能力禁止使用 |
| 4 | `discoverable = true` | 管理员可标记不可发现 |
| 5 | 权限上下文过滤 | 如果传入 agent_id/workspace_id，按权限策略过滤 |

---

## 4. Resolve API 设计

### 4.1 按 Item Resolve

```
GET /api/runtime/capabilities/{item_id}/resolve
```

**返回内容**：当前 published version 的完整信息 + 依赖树。

```json
{
  "item_id": "uuid",
  "name": "合规审查 Agent",
  "type": "agent",
  "version": "0.1.0",
  "version_id": "uuid",

  "manifest_json": { "system_prompt": "...", "model_config": {} },
  "config_json": {},
  "input_schema": {},
  "output_schema": {},
  "permission_json": {
    "network": false,
    "file_read": true,
    "file_write": false,
    "shell_exec": false
  },
  "runtime_compatibility": {
    "python": ">=3.10",
    "memory_mb": 512
  },

  "risk_summary": {
    "risk_level": "low",
    "finding_count": 2,
    "findings_by_severity": { "low": 2 },
    "findings_by_type": { "Permission 风险": 2 }
  },

  "dependencies": [
    {
      "item_id": "uuid",
      "name": "长文档摘要 Skill",
      "type": "skill",
      "version": "0.1.0",
      "version_id": "uuid",
      "relation_type": "uses",
      "required": true,
      "risk_level": "low",
      "dependencies": [
        {
          "item_id": "uuid",
          "name": "PDF文本抽取 Tool",
          "type": "tool",
          "version": "0.1.0",
          "version_id": "uuid",
          "relation_type": "invokes",
          "required": true,
          "risk_level": "low"
        }
      ]
    },
    {
      "item_id": "uuid",
      "name": "文件系统 MCP",
      "type": "mcp",
      "version": "0.1.0",
      "version_id": "uuid",
      "relation_type": "depends_on",
      "required": true,
      "risk_level": "medium",
      "config_json": { "transport": "stdio", "command": "..." }
    }
  ],

  "aggregated_permissions": {
    "network": false,
    "file_read": true,
    "file_write": false,
    "shell_exec": false
  },

  "dependency_risk_level": "medium"
}
```

### 4.2 依赖展开规则

1. **只展开 scope=runtime 的关系**（management 关系不在 resolve 中返回）
2. **递归展开**：直接依赖 + 间接依赖（transitive）完整展开
3. **去重**：同一个 Item 出现在多条路径上只返回一次
4. **深度限制**：最大展开 10 层，超过截断并告警
5. **循环跳过**：已在当前解析路径上的 Item 不再展开

### 4.3 聚合权限计算

```
aggregated_permissions = merge(
    current_item.permission_json,
    ...all dependency's permission_json (recursive)
)

merge 规则：取最宽松的并集
  - 如果任意一个依赖声明了 network: true → aggregated 中 network = true
  - 如果任意一个依赖声明了 shell_exec: true → aggregated 中 shell_exec = true
```

这意味着 Agent 引入一个能力时，必须告知 Runtime 这个能力**整体上**需要哪些权限（包括它依赖的所有子能力的权限并集）。

### 4.4 依赖风险等级计算

```
dependency_risk_level = max(current_item.risk_level, ...all dependency's risk_level)

优先级：blocking > high > medium > low
```

如果一个依赖的 risk_level 是 high，即使当前 Item 是 low，`dependency_risk_level` 返回 high。

### 4.5 按 Version Resolve

```
GET /api/runtime/capabilities/versions/{version_id}/resolve
```

与 Item Resolve 语义相同，但解析**指定版本**而非当前 published 版本。

**准入条件**：目标版本 status 必须为 `published` 或 `deprecated`（已经发布过的版本允许 resolve）。

---

## 5. 过滤规则详解

### 5.1 硬过滤（不可绕过）

| 规则 | 实现 | 拒绝时响应 |
|------|------|-----------|
| status != published | WHERE status = 'published' | 不返回该条目 |
| risk_level = blocking | WHERE risk_level != 'blocking' | 不返回该条目 |
| discoverable = false | WHERE discoverable = true | 不返回该条目 |
| status = disabled / archived | WHERE status NOT IN ('disabled', 'archived') | 不返回该条目 |

### 5.2 软过滤（Agent 上下文）

| 规则 | 实现 | 阶段 |
|------|------|:---:|
| agent_id 可见范围 | 通过 agent_capability_policies 表判定 | 阶段 7 |
| workspace_id 隔离 | WHERE workspace_id = ? | 阶段 7 |
| Agent 权限匹配 | 能力要求的权限 ⊆ Agent 被授予的权限 | 阶段 7 |
| Agent 不可见标记 | 管理员标记某能力对某 Agent 不可见 | 阶段 7 |

**当前 PoC/准生产阶段**：不传 agent_id/workspace_id 时跳过软过滤（返回所有已发布且 non-blocking 能力）。

---

## 6. Agent 自身权限管控

### 6.1 两级权限模型

```
Hub 层（准入过滤）
  └── Agent 能发现哪些能力
        ↓
Runtime Policy Engine 层（精细化策略）
  └── Agent 能实际使用哪些能力
        └── Agent 使用时的权限边界（sandbox 限制）
```

### 6.2 Hub 的权限职责

| 职责 | 说明 |
|------|------|
| 根据 agent_id 过滤可见能力列表 | Discover 接口在 agent_id 传入时只返回该 Agent 允许访问的能力 |
| 传递能力的 permission_json | Resolve 接口原样返回能力的权限声明，不评估 Agent 是否"够格" |
| 传递 aggregated_permissions | 让 Runtime 了解引入该能力的**整体**权限需求 |

### 6.3 Runtime Policy Engine 的权限职责

| 职责 | 说明 |
|------|------|
| 根据 Agent 的被授予权限评估能否引入该能力 | 如果 Agent 被授予的权限不覆盖 aggregated_permissions → 拒绝引入 |
| 设置 Sandbox 权限边界 | 即使能力声明了 file_read，Runtime 可能限制读取路径 |
| 运行时动态权限判定 | Agent 运行中可能动态请求权限，由 Policy Engine 实时判定 |

### 6.4 Hub 不做的权限事

- ❌ Hub 不评估"Agent A 有没有资格使用 Tool B"
- ❌ Hub 不授予或撤销 Agent 权限
- ❌ Hub 不执行能力的 permission 声明
- ✅ Hub 只做：准入过滤 + 权限信息传递

---

## 7. Hub / Runtime / IAM 的边界

### 7.1 完整请求链路

```
Agent / Runtime
  │
  │ ① 携带 agent_id + workspace_id + token
  ▼
API Gateway / IAM
  │
  │ ② 验证 token → 解析 agent_id / workspace_id / roles
  │ ③ 注入 context header (X-Agent-ID, X-Workspace-ID, X-Roles)
  ▼
Hub Discover / Resolve
  │
  │ ④ 根据 context 过滤能力 → 返回结果
  ▼
Agent / Runtime
  │
  │ ⑤ 将结果传递给 Runtime Policy Engine
  ▼
Runtime Policy Engine
  │
  │ ⑥ 评估 Agent 是否可使用这些能力
  │ ⑦ 设置 sandbox 权限边界
  │ ⑧ 触发能力加载/执行
  ▼
能力开始运行
```

### 7.2 各组件职责

| 组件 | 职责 | 本阶段状态 |
|------|------|:---:|
| **IAM** | Token 签发/验证、Agent 身份管理、角色分配 | 阶段 7 |
| **API Gateway** | Token 验证、context 注入、限流、路由 | 准生产 |
| **Hub Discover** | 能力搜索、准入过滤、依赖解析、权限信息传递 | 阶段 5（自研） |
| **Runtime Policy Engine** | Agent 权限评估、sandbox 设置、动态权限判定 | Runtime 负责 |
| **Hub 管理态** | 能力注册/审批/发布/扫描/下架/回滚 | ✅ 已实现 |

---

## 8. 缓存和性能考虑

### 8.1 PublishedCapabilitySnapshot（推荐方案）

维护一张预计算的已发布能力快照表：

```sql
CREATE TABLE published_capability_snapshot (
    item_id UUID PRIMARY KEY,
    name VARCHAR(200),
    type VARCHAR(20),
    description TEXT,
    category VARCHAR(100),
    tags JSONB,
    risk_level VARCHAR(20),
    current_version_id UUID,
    current_version VARCHAR(50),
    discoverable BOOLEAN,
    workspace_id UUID,
    snapshot_at TIMESTAMPTZ DEFAULT now()
);
```

**更新策略**：
- 能力发布/下架/风险变更时，异步更新对应行
- 全量刷新：定时任务（每 5 分钟）重算

**优势**：Discover 查询从多表 JOIN 变为单表查询。

### 8.2 Redis 缓存（准生产阶段）

| 缓存对象 | Key 模式 | TTL | 失效触发 |
|----------|----------|:---:|----------|
| Discover 结果（无 agent 上下文） | `discover:{type}:{category}:{tags_hash}:{limit}:{offset}` | 60s | 任何能力状态变更触发批量失效 |
| Resolve 结果（单个 Item） | `resolve:item:{item_id}` | 120s | 能力版本变更/关系变更触发单条失效 |
| Resolve 依赖树 | `resolve:deps:{version_id}` | 300s | 该版本关系变更触发失效 |

### 8.3 性能目标

| 操作 | PoC | 准生产 | 优先级 |
|------|-----|--------|:---:|
| Discover (1000 条能力) | < 500ms | < 200ms | P0 |
| Resolve (含 3 层依赖展开) | < 1000ms | < 500ms | P0 |
| Discover (10000 条能力) | — | < 500ms | P1 |
| Resolve (含 10 层依赖展开) | — | < 1000ms | P1 |

### 8.4 限流策略

| 限流维度 | PoC | 准生产 |
|----------|-----|--------|
| per agent_id | 100 req/min | 1000 req/min |
| per workspace_id | 200 req/min | 2000 req/min |
| Global Discover | 500 req/min | 5000 req/min |

---

## 9. P0 / P1 演进

### 9.1 P0（阶段 5，下一轮）

| # | 任务 | 说明 |
|---|------|------|
| 1 | Discover API | type/keyword/category/tags 过滤；硬过滤规则 |
| 2 | Resolve API（by Item） | 返回当前 published version + 依赖树展开 |
| 3 | Resolve API（by Version） | 指定版本 resolve |
| 4 | 依赖展开（scope=runtime only） | 递归 + 去重 + 深度限制 + 循环检测 |
| 5 | aggregated_permissions 计算 | 当前能力 + 所有依赖的权限并集 |
| 6 | dependency_risk_level 计算 | max of all |
| 7 | PublishedCapabilitySnapshot | 快照表 + 异步更新 |
| 8 | 基本测试 | Discover + Resolve 接口测试 |

### 9.2 P1（后续阶段）

| # | 任务 | 阶段 |
|---|------|:---:|
| 1 | agent_id / workspace_id 权限过滤 | 阶段 7 |
| 2 | permission 匹配过滤 | 阶段 7 |
| 3 | Redis 缓存层 | 准生产 |
| 4 | 限流 | 准生产 |
| 5 | Runtime 兼容性过滤 | 阶段 7 |
| 6 | 请求级 performance tracing | 生产 |
| 7 | AgentRegistry 协议兼容 Adapter | 阶段 B |

---

## 10. 与其他验证文档的关系

```
03_item_relation_design.md
  └── HubItemRelation 模型 + relation_scope = runtime
        ↓
04_runtime_discover_design.md (本文档)
  └── Resolve API 依赖展开 ← HubItemRelation(scope=runtime)

02_unified_vs_separate_management.md
  └── 统一治理面 → Discover/Resolve 在同一接口覆盖四类 Asset

01_open_source_reuse_matrix.md
  └── AgentRegistry Discover 协议 → Hub Discover 的语义参考
```

---

> 配套文档：
> - `docs/validation/03_item_relation_design.md` — 能力关系模型设计
> - `docs/14_hub_capability_market_solution_design.md` — 整体方案设计（§9 Runtime Discover / Resolve）
