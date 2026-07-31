# Hub API 设计

## 当前阶段 API 清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/hub/items` | 创建能力资产 |
| GET | `/api/hub/items` | 资产列表（分页+筛选） |
| GET | `/api/hub/items/{item_id}` | 资产详情 |
| PUT | `/api/hub/items/{item_id}` | 更新资产 |
| POST | `/api/hub/items/{item_id}/versions` | 创建版本 |
| GET | `/api/hub/items/{item_id}/versions` | 版本列表 |
| GET | `/api/hub/items/{item_id}/versions/{version_id}` | 版本详情 |
| POST | `/api/hub/items/{item_id}/submit` | 提交资产审核 |
| POST | `/api/hub/versions/{version_id}/submit-review` | 提交版本审核 |
| POST | `/api/hub/versions/{version_id}/approve` | 审批通过 |
| POST | `/api/hub/versions/{version_id}/reject` | 审批驳回 |
| POST | `/api/hub/versions/{version_id}/request-change` | 要求修改 |
| POST | `/api/hub/versions/{version_id}/publish` | 发布版本 |
| POST | `/api/hub/items/{item_id}/disable` | 禁用资产 |
| POST | `/api/hub/items/{item_id}/archive` | 归档资产 |
| POST | `/api/hub/items/{item_id}/rollback` | 回滚到历史版本 |
| POST | `/api/hub/presets/init` | 初始化预置样例数据 |
| POST | `/api/hub/imports/package` | 导入能力包（JSON/YAML/ZIP） |
| POST | `/api/hub/relations` | 创建能力关系 |
| GET | `/api/hub/relations/{relation_id}` | 查询关系详情 |
| GET | `/api/hub/items/{item_id}/relations` | 查询某资产的出入关系 |
| DELETE | `/api/hub/relations/{relation_id}` | 删除能力关系 |

## 当前 API 不包含的能力

以下功能明确不在本轮 API 范围：

- 安全扫描（扫描触发/报告查询）
- Runtime discover / lookup
- 上传包解析
- 评分评论
- 前端页面
- 前端页面

---

## 健康检查

```
GET /api/health
```

**响应** `200 OK`
```json
{"status": "ok"}
```

---

## HubItem 接口

### 创建资产

```
POST /api/hub/items
```

**请求体**
```json
{
  "name": "My Agent",
  "type": "agent",
  "description": "示例 Agent",
  "industry": "金融",
  "scenario": "客服",
  "category_id": null,
  "source_type": "manual",
  "risk_level": "low",
  "created_by": "admin"
}
```

**默认值**：status=draft, source_type=manual, risk_level=low, discoverable=true, allow_existing_references=true, force_disabled=false

**响应** `201 Created`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "My Agent",
  "type": "agent",
  "description": "示例 Agent",
  "industry": "金融",
  "scenario": "客服",
  "category_id": null,
  "source_type": "manual",
  "status": "draft",
  "risk_level": "low",
  "current_version_id": null,
  "discoverable": true,
  "allow_existing_references": true,
  "force_disabled": false,
  "created_by": "admin",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**错误码**：422 参数校验失败

---

### 查询资产列表

```
GET /api/hub/items
```

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| type | string | - | 筛选类型：agent/mcp/skill/tool |
| status | string | - | 筛选状态 |
| risk_level | string | - | 筛选风险等级 |
| source_type | string | - | 筛选来源 |
| keyword | string | - | 按 name/description 模糊匹配 |
| skip | int | 0 | 分页偏移 |
| limit | int | 20 | 每页数量（1-100） |

**响应** `200 OK`
```json
{
  "items": [ ... ],
  "total": 42
}
```

---

### 查询资产详情

```
GET /api/hub/items/{item_id}
```

**响应** `200 OK`（同创建响应格式）

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | 资产不存在 |

---

### 更新资产

```
PUT /api/hub/items/{item_id}
```

**请求体**（全部字段可选）
```json
{
  "name": "Updated Name",
  "description": "更新后的描述"
}
```

**响应** `200 OK`（完整 HubItem）

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | 资产不存在 |
| 422 | 参数校验失败 |

---

## HubItemVersion 接口

### 创建版本

```
POST /api/hub/items/{item_id}/versions
```

**请求体**
```json
{
  "hub_item_id": "550e8400-e29b-41d4-a716-446655440000",
  "version": "1.0.0",
  "description": "初始版本",
  "manifest_json": {},
  "config_json": {},
  "input_schema": {},
  "output_schema": {},
  "permission_json": {},
  "runtime_compatibility": {},
  "risk_level": "low",
  "package_hash": "abc123",
  "change_log": {},
  "created_by": "admin"
}
```

**默认值**：status=draft, risk_level=low

**响应** `201 Created`
```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "hub_item_id": "550e8400-e29b-41d4-a716-446655440000",
  "version": "1.0.0",
  "description": "初始版本",
  "manifest_json": {},
  "config_json": {},
  "input_schema": {},
  "output_schema": {},
  "permission_json": {},
  "runtime_compatibility": {},
  "status": "draft",
  "risk_level": "low",
  "package_hash": "abc123",
  "change_log": {},
  "created_by": "admin",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | HubItem 不存在 |
| 409 | 同一 HubItem 下 version 重复 |
| 422 | 参数校验失败 |

---

### 查询版本列表

```
GET /api/hub/items/{item_id}/versions
```

**响应** `200 OK`
```json
[
  { ... },
  { ... }
]
```

**错误码**：404（HubItem 不存在）

---

### 查询版本详情

```
GET /api/hub/items/{item_id}/versions/{version_id}
```

**响应** `200 OK`（同创建响应格式）

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | HubItem 不存在或版本不存在 |

---

## 审批接口

### 审批通过

```
POST /api/hub/versions/{version_id}/approve
```

**请求体**
```json
{
  "operator": "approver",
  "comment": "looks good"
}
```

**规则**：只有 `pending_review` 状态的版本可审批；`blocking` 风险等级禁止通过。

**响应** `200 OK`
```json
{"detail": "ok"}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 400 | 版本状态非 pending_review 或风险等级为 blocking |
| 404 | 版本不存在 |

---

### 审批驳回

```
POST /api/hub/versions/{version_id}/reject
```

**请求体**
```json
{
  "operator": "approver",
  "comment": "needs improvement"
}
```

**响应** `200 OK`
```json
{"detail": "ok"}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 400 | 版本状态非 pending_review |
| 404 | 版本不存在 |

---

### 要求修改

```
POST /api/hub/versions/{version_id}/request-change
```

**请求体**
```json
{
  "operator": "approver",
  "comment": "please fix the config"
}
```

**规则**：版本状态变为 `change_required`，开发者修改后可调用 `submit-review` 重新提交。

**响应** `200 OK`
```json
{"detail": "ok"}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 400 | 版本状态非 pending_review |
| 404 | 版本不存在 |

---

### approve 与 publish 的区别

| 操作 | 负责服务 | 效果 |
|------|----------|------|
| approve | approval_service | 版本 status=approved，写入 ApprovalRecord + LifecycleEvent |
| publish | lifecycle_service | 版本 status=published，item status=published，设置 current_version_id |

- `approve` **不会**修改 HubItem.current_version_id
- `approve` **不会**修改 HubItem.status
- `approve` **不会**触发发布
- `publish` 由 lifecycle_service 单独调用，且要求版本已处于 `approved` 状态

---

## 扫描接口

### 触发扫描

```
POST /api/hub/versions/{version_id}/scan
```

**请求体**（可选）
```json
{"operator": "scanner"}
```

**响应** `200 OK` — 返回完整的 ScanReport（含 findings）
```json
{
  "id": "uuid",
  "hub_item_id": "uuid",
  "hub_item_version_id": "uuid",
  "risk_level": "blocking",
  "summary": {
    "total_findings": 2,
    "severity_counts": {"critical": 1, "high": 1},
    "risk_types": ["secret:api_key", "rm -rf"]
  },
  "scanner_version": "0.1.0",
  "findings": [
    {
      "id": "uuid",
      "scan_report_id": "uuid",
      "risk_type": "secret:api_key",
      "severity": "critical",
      "file_path": "config_json.env",
      "evidence": {
        "field": "config_json.env.API_KEY",
        "matched": "sk-1234",
        "message": "secret key 'API_KEY' detected"
      },
      "recommendation": "Remove hardcoded secret in 'API_KEY'",
      "created_at": "..."
    }
  ],
  "created_at": "..."
}
```

**副作用**：version.risk_level 和 item.risk_level 同步更新为扫描结果。

---

### 查询最新扫描报告

```
GET /api/hub/versions/{version_id}/scan-report
```

**响应** `200 OK` — 返回该版本最新一份 ScanReport（含 findings）

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | 版本不存在或未扫描 |

---

### 多次扫描行为

- 每次 POST /scan 新建独立的 ScanReport 和 ScanFinding
- 不删除历史扫描报告
- GET /scan-report 始终返回 `created_at` 最新的一份

---

## 预置样例接口

### 初始化预置数据

```
POST /api/hub/presets/init
```

**说明**：创建 4 类预置样例（Agent/MCP/Skill/Tool），source_type=preset，status=draft，初始版本 0.1.0。不自动审批/发布/扫描。按 name + type 去重，重复调用不重复创建。

**响应** `200 OK`
```json
{
  "created": 4,
  "skipped": 0,
  "items": [
    {
      "id": "uuid",
      "name": "招投标合规检查 Agent",
      "type": "agent",
      "source_type": "preset",
      "status": "draft"
    }
  ]
}
```

---

## 导入接口

### 导入能力包

```
POST /api/hub/imports/package
Content-Type: multipart/form-data
Body: file（max 5MB）
```

**支持格式**：`.json` / `.yaml` / `.yml` / `.zip`（内含 manifest）

**响应** `201 Created`
```json
{
  "item_id": "uuid",
  "version_id": "uuid",
  "name": "my-agent",
  "type": "agent",
  "version": "0.1.0",
  "status": "draft",
  "message": "imported successfully",
  "warnings": []
}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 400 | manifest 格式错误、缺少必填字段、非法 type、zip slip、无 manifest、manifest 校验错误（Stage 2） |
| 409 | 同一 item 下 version 重复 |
| 413 | 文件超过 5MB |

**导入行为**：
- source_type=upload，status=draft
- 不自动扫描、审批、发布
- name+type 大小写不敏感去重

---

## 关系接口

### 创建能力关系

```
POST /api/hub/relations
```

**请求体**
```json
{
  "source_item_id": "550e8400-e29b-41d4-a716-446655440000",
  "target_item_id": "660e8400-e29b-41d4-a716-446655440001",
  "relation_type": "uses",
  "relation_scope": "management",
  "required": false,
  "description": "Agent 引用 Skill 能力包",
  "created_by": "admin"
}
```

**默认值**：relation_scope=management, required=false

**类型组合白名单**：仅允许 agent uses skill、agent invokes tool、agent depends_on mcp、skill invokes tool、skill depends_on mcp、mcp provides tool。超出返回 400。

**响应** `201 Created`
```json
{
  "id": "770e8400-e29b-41d4-a716-446655440002",
  "source_item_id": "550e8400-...",
  "target_item_id": "660e8400-...",
  "relation_type": "uses",
  "relation_scope": "management",
  "required": false,
  "description": "Agent 引用 Skill 能力包",
  "source_item": {"id": "...", "name": "MyAgent", "type": "agent"},
  "target_item": {"id": "...", "name": "MySkill", "type": "skill"},
  "created_by": "admin",
  "created_at": "2025-01-01T00:00:00Z"
}
```

**错误码**

| 状态码 | 说明 |
|--------|------|
| 400 | 自引用（source == target）或非法类型组合 |
| 404 | source_item 或 target_item 不存在 |
| 409 | 同 source/target/type/scope 关系已存在 |

---

### 查询关系详情

```
GET /api/hub/relations/{relation_id}
```

**响应** `200 OK`（格式同创建，含 source_item / target_item 最小摘要）

**错误码**：404

---

### 查询资产关系列表

```
GET /api/hub/items/{item_id}/relations
```

**响应** `200 OK`
```json
{
  "outgoing": [ ... ],
  "incoming": [ ... ]
}
```

**错误码**：404（item 不存在）

---

### 删除能力关系

```
DELETE /api/hub/relations/{relation_id}
```

**响应** `204 No Content`

**错误码**：404

---

## Runtime Discover / Resolve 接口

Stage 3 P0 实现。Runtime 通过该接口查询和解析已发布、可发现、风险可接受的能力。

### 发现能力

```
GET /api/runtime/capabilities/discover
```

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| type | string | - | 资产类型：agent/skill/tool/mcp |
| keyword | string | - | 按 name/description 模糊匹配 |
| risk_level_max | string | high | 允许的最高风险等级：low/medium/high |
| limit | int | 20 | 每页数量（1-100） |
| offset | int | 0 | 分页偏移 |
| agent_id | string | - | 预留，不做权限过滤 |
| workspace_id | string | - | 预留，不做权限过滤 |

**硬过滤规则**：只返回 status=published、discoverable=true、risk_level≠blocking、current_version 已发布且风险可接受的能力。详见 `docs/16_runtime_discover.md`。

**响应** `200 OK`

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "My Agent",
      "type": "agent",
      "description": "...",
      "version": "1.0.0",
      "risk_level": "low"
    }
  ],
  "total": 42
}
```

### 解析能力

```
GET /api/runtime/capabilities/{item_id}/resolve
```

**响应** `200 OK` — 能力完整描述（manifest/config/schema/permission/runtime_compatibility + runtime scope 依赖）。

**错误码**

| 状态码 | 说明 |
|--------|------|
| 404 | 能力不可用（不存在/未发布/不可发现/blocking/当前版本不可用） |
| 409 | 有 required=true 的 runtime 依赖不可用 |
| 422 | 参数校验失败

### 下载 Runtime manifest

```
GET /api/runtime/capabilities/{item_id}/manifest
```

返回 application/json，内容与 resolve 一致（不含 status 字段），额外包含 `exported_at` 时间戳。不可发现能力返回 404。

---

## 下载与导出接口

Stage 4 P0 实现。详见 `docs/17_download_export.md`。

### 版本能力包下载

```
GET /api/hub/exports/items/{item_id}/versions/{version_id}/package
```

**响应** `200` — `application/zip`，包含 manifest.json / relations.json / README.md 及各 schema/config 文件。

**错误码**：404（item 或 version 不存在 / version 不属于 item）

### Item 管理导出

```
GET /api/hub/exports/items/{item_id}
```

**响应** `200` — `application/zip`，包含 item.json / versions.json / relations.json / README.md。

**错误码**：404（item 不存在）
