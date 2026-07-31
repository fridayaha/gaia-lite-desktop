# Harness / OfficeClaw Discover 兼容层设计

版本：v0.1 | 日期：2026-05-28 | 状态：设计阶段。**真实 schema 待对接方确认，代码未落地。**

---

## 1. 背景

Harness 和 OfficeClaw 是 Hub 的两个下游兼容调用方。它们各自有旧的 Discover 接口实现，需要在 Hub 作为能力中心后，通过薄 Adapter 兼容层对接，不复制 Hub 业务逻辑，不绕过安全过滤。

**当前状态：**
- Hub 标准 Runtime API 已完成（Discover / Resolve / Manifest / Tool Definition）
- RBAC-4 Runtime Consumer 权限已完成
- Harness / OfficeClaw 真实 schema **未获取**
- 本文档定义候选兼容契约，待对接方确认

---

## 2. 当前 Hub Runtime API 能力梳理

### 2.1 Discover

```
GET /api/runtime/capabilities/discover
```

**Query 参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | `tool\|agent\|skill\|mcp` | 无 | 资产类型过滤 |
| `keyword` | `str` | 无 | name/description ILIKE 搜索 |
| `risk_level_max` | `low\|medium\|high` | `high` | 风险等级上限（blocking 永不可见） |
| `limit` | `int` | 20 (1-100) | 分页大小 |
| `offset` | `int` | 0 | 分页偏移 |
| `agent_id` | `str` | 无 | 兼容 query param（不信任来源） |
| `workspace_id` | `str` | 无 | 同上 |

**Auth：** `require_runtime_permission("capability:discover")`
- 需要 `platform_admin` 或 `runtime_consumer` 角色
- 需要 `capability:discover` scope
- `HUB_AUTH_MODE=dev` 时自动满足

**响应：**

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "string",
      "type": "tool|agent|skill|mcp",
      "description": "string|null",
      "version": "string",
      "risk_level": "low|medium|high"
    }
  ],
  "total": 42
}
```

**不可见资产处理：**
- 硬过滤：`status!=published`, `!discoverable`, `force_disabled`, `risk_level==blocking`, `version.status!=published`, `version.risk_level==blocking`
- Policy 过滤：`ScopedCapabilityAccessPolicy.can_discover()` → deny 时静默排除
- `total` 反映 policy 过滤后的真实数量

### 2.2 Resolve

```
GET /api/runtime/capabilities/{item_id}/resolve?depth=1
```

**Query 参数：** `depth` (1-3), 同 Discover 的 auth 参数

**Auth：** `require_runtime_permission("capability:resolve")`

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id`, `name`, `type`, `description`, `version`, `status`, `risk_level` | 基本字段 | - |
| `manifest_json` | `dict\|null` | 完整 manifest |
| `config_json` | `dict\|null` | **含可能敏感配置** |
| `input_schema` | `dict\|null` | JSON Schema |
| `output_schema` | `dict\|null` | JSON Schema |
| `permission_json` | `dict\|null` | 权限声明 |
| `runtime_compatibility` | `dict\|null` | 运行环境要求 |
| `relations` | `RuntimeRelationSummary[]` | 直接关系 |
| `dependencies` | `RuntimeDependencyNode[]` | 递归依赖树 |
| `dependency_warnings` | `RuntimeDependencyWarning[]` | 依赖可访问性警告 |

**Policy deny 行为：** 返回 **404**（隐藏资产存在性）

### 2.3 Manifest

```
GET /api/runtime/capabilities/{item_id}/manifest
```

**Auth：** `capability:manifest` scope（fallback: `capability:resolve`）

本质是 `resolve(depth=1)` 后去 `status` 加 `exported_at`。

### 2.4 Tool Definition

```
GET /api/runtime/capabilities/{item_id}/tool-definition
```

**Auth：** `capability:tool_definition` scope（fallback: `capability:resolve`）

**响应：** OpenAI Function Calling 格式

```json
{
  "type": "function",
  "function": {
    "name": "sanitized_name",
    "description": "...",
    "parameters": { "type": "object", "properties": {...} }
  }
}
```

**约束：** 仅 `tool` 类型可用，非 tool 返回 404。

---

## 3. Harness / OfficeClaw 旧接口已知信息

**来源：** `docs/02_solution_design.md` 第 111、267、293-295 行

| 项目 | 状态 |
|------|:--:|
| 旧接口 endpoint | ❌ 未知 |
| request schema | ❌ 未知 |
| response schema | ❌ 未知 |
| 字段映射表 | ❌ 不存在 |
| typed array 结构 | ❌ 未知 |
| 是否需要 tools/agents/mcp_servers/skills 分组 | ❌ 未知 |
| 是否需要 function calling 格式 | ❌ 未知 |
| 是否依赖 endpoint/command/args/env 等敏感字段 | ❌ 未知 |
| 是否需要分页 | ❌ 未知 |
| 是否需要 capability type 过滤 | ❌ 未知 |
| 是否需要 runtime profile | ❌ 未知 |
| 是否有 OpenAPI/JSON schema 文档 | ❌ 不存在 |

**结论：当前无法实现完全兼容，只能先做候选兼容契约。**

### 架构定位（已有共识）

```
Harness / OfficeClaw → 薄 Adapter → Hub Runtime API
```

- Adapter 只做 request/response 格式映射
- 不复制 Hub 业务规则
- 不绕过安全过滤
- 不直接读 Hub 数据库
- 真实 schema 待对接方确认

---

## 4. Response Profile 设计

兼容层通过 profile 参数控制返回格式。profile 在 Hub 标准过滤之后，仅做格式转换。

### 4.1 Profile 列表

| Profile | 用途 | 返回内容 |
|---------|------|----------|
| `standard` | Hub 标准（默认） | 当前 Runtime API 格式 |
| `summary` | 轻量摘要 | Discover 字段，不含 config |
| `detail` | 完整详情 | Resolve 字段 |
| `tool_function` | Function Calling | Tool Definition 格式 |
| `compat_harness` | Harness 兼容 | typed arrays: tools/agents/mcp_servers/skills |
| `compat_officeclaw` | OfficeClaw 兼容 | tool/use-case centric |

### 4.2 Profile 不是权限绕过

- profile 仅控制**格式**，不扩大数据可见范围
- 所有 profile 必须通过 `ScopedCapabilityAccessPolicy` 过滤
- 所有 profile 不返回 blocked/unpublished/archived/disabled 资产
- 敏感字段在各 profile 中独立控制

### 4.3 compat_harness Profile 格式（候选）

```json
{
  "tools": [
    {
      "id": "uuid",
      "name": "string",
      "description": "string",
      "version": "string",
      "risk_level": "low|medium|high",
      "input_schema": {...},
      "output_schema": {...},
      "permission_summary": { "auth_required": false, "allowed_domains": [] },
      "tool_definition": { "type": "function", "function": {...} },
      "dependencies": ["uuid", ...],
      "runtime_compatibility": { "platform": "linux", ... }
    }
  ],
  "agents": [ { ... } ],
  "mcp_servers": [ { ... } ],
  "skills": [ { ... } ]
}
```

### 4.4 compat_officeclaw Profile 格式（候选）

```json
{
  "capabilities": [
    {
      "id": "uuid",
      "name": "string",
      "type": "tool|agent|skill|mcp",
      "description": "string",
      "version": "string",
      "risk": "low|medium|high",
      "schema_in": {...},
      "schema_out": {...},
      "permissions": { "domains": [], "auth": false },
      "function": { "name": "...", "description": "...", "parameters": {...} }
    }
  ]
}
```

---

## 5. 安全边界

### 5.1 兼容层默认不返回

| 字段 | 原因 |
|------|------|
| `raw env` | 可能含凭据 |
| `command` | MCP server 启动命令 |
| `args` | MCP server 启动参数 |
| `token` / `api_key` / `secret` | 凭据 |
| `raw permission_json` | 可能含内部配置 |
| `raw config_json` | 可能含凭据 |
| 完整 MCP server endpoint | 可能含凭据的 URL |

### 5.2 安全替代方案

| 敏感字段 | 安全替代 | 示例 |
|----------|----------|------|
| `env` | `runtime_profile_ref` | `"openai-compatible"` |
| `permission_json` | `permission_summary` | `{"auth_required": true, "allowed_domains": ["api.example.com"]}` |
| `config_json` | `redacted_config` | `{"stripe_key": "[REDACTED]"}` |
| `command` / `args` | `transport_type` | `"stdio" \| "sse" \| "streamable_http"` |
| endpoint | `safe_endpoint_summary` | `{"scheme": "https", "host": "api.example.com"}` |

**原则：** 兼容 Adapter 只做格式转换，不降低 Hub 的安全治理边界。

---

## 6. 字段映射设计

### 6.1 Hub Standard → Harness compat

| Hub 字段 | Harness 字段 | 说明 |
|----------|-------------|------|
| `id` | `id` | 直通 |
| `name` | `name` | 直通 |
| `type` | — | 用于分组到 `tools`/`agents`/... |
| `description` | `description` | 直通 |
| `version` | `version` | 直通 |
| `risk_level` | `risk_level` | 直通 |
| `input_schema` | `input_schema` | 直通 |
| `output_schema` | `output_schema` | 直通 |
| `permission_json` | `permission_summary` | 脱敏后摘要 |
| `manifest_json.invocation` | `tool_definition` | Tool 类型 → Function Calling |
| `dependencies` | `dependencies` | item ID 列表 |
| `runtime_compatibility` | `runtime_compatibility` | 直通（无秘密字段） |

### 6.2 Hub Standard → OfficeClaw compat

| Hub 字段 | OfficeClaw 字段 | 说明 |
|----------|----------------|------|
| `id` | `id` | 直通 |
| `name` | `name` | 直通 |
| `type` | `type` | 直通 |
| `description` | `description` | 直通 |
| `version` | `version` | 直通 |
| `risk_level` | `risk` | 字段名差异 |
| `input_schema` | `schema_in` | 字段名差异 |
| `output_schema` | `schema_out` | 字段名差异 |
| `permission_json` | `permissions` | 脱敏摘要 |
| `manifest_json.invocation` | `function` | Function Calling 格式 |

### 6.3 不可映射 / 需确认字段

| 字段 | 问题 | 待确认方 |
|------|------|:--:|
| `command` | MCP 启动命令，Hub 不返回 | Harness |
| `env` | 环境变量，可能含凭据 | Harness |
| MCP launch params | MCP server 完整启动配置 | Harness |
| `runtime_profile` | 运行时 profile 名 | Harness |
| `workspace` / `tenant` | 多租户隔离 | 双方 |
| `endpoint` | Tool 执行端点 | OfficeClaw |
| `auth_mode` | 鉴权方式 | OfficeClaw |
| pagination 格式 | cursor vs offset | 双方 |
| response envelope | `{data:[],meta:{}}` vs plain | 双方 |
| error envelope | 错误格式 | 双方 |

---

## 7. Adapter 架构设计

```
                   ┌─────────────────────────┐
                   │ Compat Adapter Endpoint │  ← 新增 API
                   │ (Harness / OfficeClaw)  │
                   └───────────┬─────────────┘
                               │
                   ┌───────────▼─────────────┐
                   │ RuntimeDiscoverService  │  ← 复用现有
                   │ (标准过滤 + RBAC + Policy)│
                   └───────────┬─────────────┘
                               │
                   ┌───────────▼─────────────┐
                   │ ResponseProfileFormatter│  ← 新增（纯转换）
                   │ (standard / summary /   │
                   │  compat_harness / ...)  │
                   └───────────┬─────────────┘
                               │
                       返回兼容格式
```

**关键约束：**
- Adapter 不直接读 DB
- Adapter 不复制安全过滤、RBAC、Policy 逻辑
- Adapter 不返回 blocked / unpublished / archived / disabled 资产
- Adapter 不执行能力

---

## 8. API 方案

### 方案 A：profile 参数扩展

```
GET /api/runtime/capabilities/discover?profile=compat_harness&type=tool
```

**优点：** 接口少，复用现有鉴权  
**缺点：** 标准 API 变复杂

### 方案 B：新增 compat endpoint（推荐）

```
GET /api/compat/harness/discover?type=tool
GET /api/compat/officeclaw/discover?type=tool
```

**优点：** 老系统迁移清晰，逐步兼容，不影响标准 Runtime API  
**缺点：** endpoint 增多

**推荐 P1 用方案 B**，显式兼容层，避免污染标准 Runtime API。

---

## 9. 鉴权方案

| 项目 | 方案 |
|------|------|
| 角色 | `runtime_consumer`（复用） |
| Scope | `capability:discover` / `capability:resolve`（复用） |
| 身份来源 | Gateway Header 注入 |
| Service Account | Harness / OfficeClaw 各自服务账号 |
| Dev 模式 | `HUB_AUTH_MODE=dev` 自动满足 |
| Policy deny | 与 Runtime API 一致（discover 静默排除，resolve 404） |

不新增无鉴权兼容接口。

**Header 示例：**
```
X-Actor-ID: harness-service-account
X-Actor-Type: service
X-Roles: runtime_consumer
X-Scopes: capability:discover,capability:resolve,capability:tool_definition
X-Workspace-ID: ws-default
```

---

## 10. 缺失信息清单

必须向 Harness / OfficeClaw 对接方确认：

| # | 问题 | 必填？ |
|:--:|------|:--:|
| 1 | 旧 Discover endpoint URL | 是 |
| 2 | request 参数列表 | 是 |
| 3 | response envelope 结构 | 是 |
| 4 | typed array 名称（tools/agents/mcp_servers/skills） | 是 |
| 5 | pagination 格式（cursor/offset/page） | 是 |
| 6 | sorting 支持 | 否 |
| 7 | error code 约定 | 否 |
| 8 | auth header 格式 | 是 |
| 9 | 是否需要 `command` / `env` 字段 | 是 |
| 10 | 是否需要 raw `config_json` | 是 |
| 11 | 是否支持 partial response | 否 |
| 12 | 是否要求字段名完全一致 | 是 |
| 13 | 是否已有客户端强 schema 校验 | 是 |
| 14 | 是否有 OpenAPI / JSON schema 文档 | 是 |

---

## 11. 实施阶段拆分

| 阶段 | 内容 | 状态 |
|:--:|------|:--:|
| HOC-0 | schema 对接确认（本文档） | ✅ 设计完成 |
| HOC-0.5 | Schema 对接包（`docs/22_harness_officeclaw_schema_request.md`） | ✅ 待发送 |
| HOC-1 | ResponseProfileFormatter + 测试（不新增 endpoint） | 📋 设计完成，待 Build |
| HOC-2 | Harness compat endpoint | 🔲 |
| HOC-3 | OfficeClaw compat endpoint | 🔲 |
| HOC-4 | 真实客户端联调 | 🔲 |

### HOC-1 设计要点

`backend/app/formatters/response_profiles.py`：
- `format_summary(item)` / `format_detail(resolve)` / `format_tool_function(tool)`
- `format_compat_harness(items)` / `format_compat_officeclaw(items)`
- Formatter 不查 DB、不做 RBAC、不做 policy filtering
- compat profiles 标记 `compatibility: "candidate"`、`generated_from: "hub_standard"`
- 候选 schema 见 `docs/22_harness_officeclaw_schema_request.md`
- Build 前需确认进入代码实现

---

## 12. 测试计划

| # | 测试 | 说明 |
|:--:|------|------|
| 1 | compat_harness 不返回 unpublished | 硬过滤验证 |
| 2 | compat_harness 不返回 blocked | 硬过滤验证 |
| 3 | compat_harness 分组 tools/agents/skills/mcp_servers | typed array 验证 |
| 4 | compat_harness 不返回 raw env/command | 安全边界验证 |
| 5 | compat_officeclaw 返回 tool-centric 格式 | 格式验证 |
| 6 | 无 runtime_consumer role → 403 | RBAC 验证 |
| 7 | policy deny 的 asset 不出现在 compat discover | 静默排除 |
| 8 | snapshot test 验证 schema 稳定 | schema 回归 |
| 9 | schema 不包含 Secret/Match/Line/sk_/api_key | 脱敏验证 |
| 10 | 回归：现有 608 tests 继续通过 | 回归验证 |
