# Harness / OfficeClaw Discover Schema 对接信息请求

版本：v0.1 | 日期：2026-05-28 | 状态：待发送给对接方

---

## 1. 对接目标

Hub 计划作为统一能力中心，替代 Harness / OfficeClaw 现有能力发现接口。为避免"自定义兼容"，需要对接方提供**真实 request / response schema**，以确保兼容层能正确转换格式，不破坏现有客户端。

**当前已知：**
- Hub 标准 Runtime API 已完成（Discover / Resolve / Manifest / Tool Definition）
- RBAC-4 Runtime Consumer 权限已完成
- Harness / OfficeClaw 旧接口 schema 完全未知
- 兼容层设计已完成（`docs/21_harness_officeclaw_compat_design.md`）

---

## 2. 需要提供的信息

### 2.1 基础接口

| # | 问题 | 示例 |
|:--:|------|------|
| 1 | 旧 Discover endpoint URL | `GET /v1/capabilities` |
| 2 | HTTP method | `GET` / `POST` |
| 3 | Auth 方式 | `Authorization: Bearer <token>` / Header 注入 |
| 4 | 必需 request headers | `X-Workspace-ID`, `X-Agent-ID` |
| 5 | 必需 query params | `type`, `page`, `limit` |
| 6 | 可选 query params | `keyword`, `risk`, `sort` |
| 7 | 是否需要 request body | 否 / `{"filters": {...}}` |
| 8 | response envelope | `{"data": [...], "meta": {...}}` |
| 9 | error response 格式 | `{"error": {"code": "...", "message": "..."}}` |
| 10 | pagination 方式 | offset/limit 或 cursor 或 page |
| 11 | sorting 支持 | `sort=name` / `sort=-created_at` |
| 12 | filtering 支持 | type, keyword, risk, status, tags |
| 13 | versioning 方式 | URL path `/v1/` / header `Accept: version=1` |

### 2.2 资产结构（每个 type 的字段）

请按 asset type 分别提供完整字段列表。**标注必填字段。**

#### Tool 字段

| # | 字段名 | 类型 | 必填？ | 示例值 |
|:--:|--------|------|:--:|--------|
| 1 | name / id | | | |
| 2 | description | | | |
| 3 | version | | | |
| 4 | input_schema | | | |
| 5 | output_schema | | | |
| 6 | endpoint | | | |
| 7 | function_calling | | | |
| 8 | permissions | | | |
| 9 | ... | | | |

#### Agent 字段 / Skill 字段 / MCP 字段

（同样格式，请分别提供）

### 2.3 Typed Array 结构

| # | 问题 |
|:--:|------|
| 1 | 是否按 type 分组为 typed arrays？（`tools: [...]`, `agents: [...]`） |
| 2 | typed array 名称是什么？ |
| 3 | 是否支持混合列表（`capabilities: [{type: "tool", ...}, {type: "agent", ...}]`）？ |
| 4 | 是否需要 `total` / `count` 字段？ |
| 5 | 是否需要 `next_cursor` / `has_more`？ |

### 2.4 Runtime 字段

| # | 问题 | 说明 |
|:--:|------|------|
| 1 | 是否需要 `endpoint`（Tool 执行 URL）？ | Hub 不返回裸 endpoint |
| 2 | 是否需要 `command`？ | MCP server 启动命令，Hub 不返回 |
| 3 | 是否需要 `args`？ | MCP server 启动参数，Hub 不返回 |
| 4 | 是否需要 `env`？ | 可能含凭据，Hub 不返回 |
| 5 | 是否需要 raw `config_json`？ | 可能含凭据 |
| 6 | 是否需要 `runtime_profile`？ | Hub 可返回 runtime_compatibility |
| 7 | 是否需要 function calling schema？ | 仅 Tool 类型，Hub 已支持 |
| 8 | 是否需要 MCP launch params？ | Hub 不托管 MCP |

### 2.5 安全字段

| # | 问题 | 说明 |
|:--:|------|------|
| 1 | 是否需要 `permissions`？ | Hub 可返回 permission_summary |
| 2 | 是否需要 `auth_required`？ | Hub 可返回 |
| 3 | 是否需要 `scopes`？ | Hub 可返回 required_scopes |
| 4 | 是否需要 `allowed_domains`？ | Hub 可返回 |
| 5 | 是否需要 `risk_level`？ | Hub 默认返回 |
| 6 | 是否需要 scan status？ | Hub 可返回 risk_level |
| 7 | 是否需要 lifecycle status？ | Hub Runtime API 只返回 published |

### 2.6 兼容要求

| # | 问题 | 说明 |
|:--:|------|------|
| 1 | 字段名是否必须完全一致？ | 如 `risk_level` 必须等于 `risk_level`，不能是 `risk` |
| 2 | 是否允许 unknown field？ | response 中多出字段客户端是否报错 |
| 3 | 是否有强 schema 校验？ | 客户端是否用 JSON Schema / protobuf 严格校验 |
| 4 | 是否接受新增字段？ | 向后兼容扩展 |
| 5 | 是否可接受安全摘要替代敏感字段？ | `permission_summary` 替代 `permission_json` |
| 6 | 是否允许分阶段迁移？ | 先兼容基础字段，后补全 |

---

## 3. Hub 默认安全边界

Hub 作为能力治理中心，**不会直接返回**以下字段：

| 字段 | 原因 | Hub 替代方案 |
|------|------|-------------|
| raw `env` | 可能含 API key / token | `runtime_profile_ref` |
| `command` | MCP server 启动命令 | `transport_type` |
| `args` | MCP server 启动参数 | 同上 |
| `token` / `api_key` / `secret` | 凭据 | 不可返回 |
| raw `permission_json` | 可能含敏感内部配置 | `permission_summary` |
| raw `config_json` | 可能含凭据 | `redacted_config` |
| 完整 MCP server endpoint | 可能含凭据 URL | `safe_endpoint_summary` |

**如果旧接口依赖以上字段，请协商替换为 safe summary 或 runtime_profile_ref。**

---

## 4. 请对接方返回的材料

请提供以下任一形式的材料：

| 优先级 | 材料 | 格式 |
|:--:|------|------|
| **P0** | OpenAPI / JSON Schema 文档 | `.json` / `.yaml` |
| **P0** | 真实 response 样例（可脱敏） | `.json` |
| **P0** | 真实 request 样例 | `.json` / curl |
| P1 | 失败响应样例 | `.json` |
| P1 | 客户端字段依赖说明 | 文档 |
| P1 | 当前接口调用样例 | curl / 代码片段 |
| P2 | 可脱敏日志样例 | 日志片段 |
| P2 | 客户端代码仓库地址 | URL |

---

## 5. 后续路线

收到 schema 后：

1. Hub 侧更新 `docs/21_harness_officeclaw_compat_design.md` 的字段映射表；
2. 实现 `ResponseProfileFormatter`（纯格式转换，不通 DB）；
3. 实现 compat endpoint（`GET /api/compat/harness/discover`）；
4. 联调验证。

**在获取真实 schema 之前，不声称已兼容 Harness / OfficeClaw。**
