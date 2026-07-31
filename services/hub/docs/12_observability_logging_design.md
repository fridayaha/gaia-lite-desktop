# Stage 7A + 7B：结构化日志设计

日期：2026-05-27 | 版本：v0.6 | 状态：P2-1 CompositeScanner 已实现，scanner.external_failed 事件已记录

---

## 1. 当前日志现状

| 能力 | 状态 |
|------|:---:|
| request_id | ✅ |
| structured JSON log | ✅ |
| API access log | ✅（hub.access） |
| 业务事件日志 | ✅（hub.event） |
| actor_id 日志 | ✅（Stage RBAC-1） |
| audit log | ✅（LifecycleEvent DB + 事件日志双通道） |
| ownership 事件 | ✅（`ownership.missing_owner` / `ownership.policy_denied`） |
| discover/resolve 调用日志 | ✅ |
| scan 日志 | ✅ |
| import/export 日志 | ✅ |
| /metrics endpoint | ❌ |

---

## 2. Stage 7A 实现范围

本阶段实现最小可观测性基础：

- **request_id middleware**：自动生成/透传/返回 X-Request-ID
- **JSON access log**：stdout 输出结构化 JSON 格式的 API 请求日志
- **零新增依赖**：全部使用 Python 标准库

### 2.1 request_id 设计

#### Middleware 行为

```
请求进入 → 检查 X-Request-ID header
  ├── 存在且有效（非空、≤128字符）→ 复用
  └── 不存在或无效 → 生成 uuid4
       ↓
  写入 request.state.request_id
  contextvars 存储（供任意日志调用方获取）
  响应头始终返回 X-Request-ID
```

#### 规则

- 空字符串 / 空白字符串 → 视为无效，重新生成
- 长度 > 128 → 视为无效，重新生成
- 不记录超长/异常 header 到日志
- 对所有 HTTP 状态码（200/400/404/409/500）均返回 X-Request-ID

#### 跨服务链路

```
Gateway/Hermes → [X-Request-ID] → Hub → [X-Request-ID] → Runtime
```

### 2.2 JSON access log

#### 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | ISO 8601 | UTC 时间戳 |
| `level` | string | INFO/WARN/ERROR |
| `event` | string | 固定为 `hub.http.request` |
| `request_id` | string | request_id |
| `method` | string | HTTP method |
| `path` | string | URL path（不含 query string） |
| `status_code` | int | HTTP 状态码 |
| `duration_ms` | int | 请求耗时（毫秒） |
| `result` | string | `ok` 或 `error` |
| `error_code` | string | 可选，not_found/conflict/validation_error/server_error/internal_error |

#### 不记录字段（红线）

- token / secret / API key
- 完整 request body
- 完整 response body
- manifest_json / config_json / input_schema / output_schema
- permission_json
- 完整 OpenAPI spec
- 完整能力包

---

## 3. Stage 7B：业务事件日志

### 3.1 设计

采用统一事件日志工具 `backend/app/core/event_log.py`：

- `log_event(event: str, **fields) -> None`
- 自动带 `request_id`（通过 contextvars）
- 自动过滤 `None` 值
- 使用标准 `logging`，logger name `hub.event`
- 输出 JSON 到 stdout
- 零新增依赖

#### 统一字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | ISO 8601 | UTC 时间戳 |
| `level` | string | INFO/WARN/ERROR |
| `event` | string | 点分隔事件名 |
| `request_id` | string | request_id |
| `item_id` | string | 资产 ID |
| `item_type` | string | 资产类型 |
| `version_id` | string | 版本 ID |
| `operation` | string | 操作类型 |
| `result` | string | ok / error |
| `status_code` | int | HTTP 状态码 |
| `duration_ms` | int | 耗时（毫秒） |
| `error_code` | string | 错误码 |
| `detail` | string | 详情 |

### 3.2 事件列表

#### Runtime 事件

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `runtime.discover.completed` | 能力发现完成 | `backend/app/api/runtime.py` |
| `runtime.resolve.completed` | 能力解析完成 | `backend/app/api/runtime.py` |
| `runtime.tool_definition.completed` | 工具定义生成完成 | `backend/app/api/runtime.py` |
| `runtime.dependency_warning` | 依赖解析警告 | `backend/app/services/runtime_discover_service.py` |

记录字段：`item_id`, `item_type`, `depth`, `result_count`, `result_total`, `dependency_count`, `warning_count`, `warning_type`, `duration_ms`, `status_code`

不记录：`permission_json`, `manifest_json`, `input_schema`, `output_schema`

#### 扫描事件

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `scan.started` | 扫描开始 | `backend/app/services/scan_service.py` |
| `scan.completed` | 扫描完成 | `backend/app/services/scan_service.py` |
| `scan.blocked` | 扫描阻断 | `backend/app/services/scan_service.py` |

记录字段：`item_id`, `version_id`, `item_type`, `risk_level`, `total_findings`, `blocking_count`, `scanner_version`, `duration_ms`

不记录：完整 finding evidence、完整 manifest/config

#### OpenAPI 导入事件

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `openapi.import.started` | 导入开始 | `backend/app/services/openapi_import_service.py` |
| `openapi.import.completed` | 导入完成 | `backend/app/services/openapi_import_service.py` |
| `openapi.import.failed` | 导入失败 | `backend/app/services/openapi_import_service.py` |

记录字段：`spec_title`, `spec_version`, `operation_count`, `tools_created`, `warnings_count`, `failed_count`, `duration_ms`

不记录：完整 OpenAPI spec

#### 生命周期事件

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `lifecycle.submit_review` | 提交审核 | `backend/app/services/lifecycle_service.py` |
| `lifecycle.approve` | 审核通过 | `backend/app/services/approval_service.py` |
| `lifecycle.reject` | 审核驳回 | `backend/app/services/approval_service.py` |
| `lifecycle.request_change` | 请求修改 | `backend/app/services/approval_service.py` |
| `lifecycle.publish` | 发布 | `backend/app/services/lifecycle_service.py` |
| `lifecycle.disable` | 禁用 | `backend/app/services/lifecycle_service.py` |
| `lifecycle.rollback` | 回滚 | `backend/app/services/lifecycle_service.py` |
| `lifecycle.archive` | 归档 | `backend/app/services/lifecycle_service.py` |

记录字段：`item_id`, `version_id`, `action`, `from_status`, `to_status`, `result`

注意：生命周期事件已有 DB 记录，JSON log 用于平台日志检索，不替代 DB 审计记录。

#### 身份不一致事件（RBAC-3C-0 新增）

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `auth.operator_mismatch` | 认证身份 actor_id 与请求体兼容字段 operator 不一致 | `backend/app/core/operator.py` |

记录字段：`action`, `body_operator`, `item_id`, `version_id`, `result`

说明：该事件为观察性事件（`result="observed"`），只记录不阻断。表示认证身份与兼容字段不一致，用于 operator 迁移期的审计观察。actor_id 由 event_log 自动注入，无需手动记录。

#### 对象级 ownership 事件（RBAC-3D-2 新增）

| 事件 | 说明 | 触发位置 |
|------|------|---------|
| `ownership.missing_owner` | 资产的 created_by 为空或 unknown，ownership 策略 fail-open | `backend/app/policies/ownership_policy.py` |
| `ownership.policy_denied` | ownership 策略拒绝操作（非 own 资产） | `backend/app/policies/ownership_policy.py` |

记录字段：`item_id`, `result`

说明：`ownership.missing_owner` 记录 `result="allowed_legacy"`，表示历史数据因缺失 owner 信息而被放行。`ownership.policy_denied` 记录 `result="denied"`，表示非 owner 操作被拒绝。

#### 外部扫描器错误事件（P2-1 新增）

| 事件 | 说明 | 位置 |
|------|------|------|
| `scanner.external_failed` | 外部扫描器执行异常（不阻断整体扫描） | `backend/app/scanners/composite_scanner.py` |

记录字段：`scanner_name`, `scanner_version`, `error_type`, `result`

说明：`scanner.external_failed` 记录 `result="warning"`，表示外部 scanner 失败已被容忍，扫描继续。内置扫描器失败仍视为系统错误。

### 3.3 敏感信息红线

日志中禁止记录：

- token / secret / API key
- raw permission_json
- full manifest_json
- full config_json
- full input_schema / output_schema
- full OpenAPI spec
- env / command args 中的敏感内容
- 用户上传原始能力包内容

只记录：ID、数量、状态、风险等级、结果、摘要。

### 3.4 access log 与业务事件日志区别

| 维度 | access log | 业务事件日志 |
|------|-----------|-------------|
| logger | `hub.access` | `hub.event` |
| event | `hub.http.request` | `runtime.*`, `scan.*`, `openapi.*`, `lifecycle.*` |
| 触发层 | middleware | API / Service |
| 粒度 | 每个 HTTP 请求 | 每个关键业务操作 |
| 用途 | 请求量统计、延迟分析、错误率 | 业务操作追踪、审计、排查 |

---

## 4. 后续规划

| 阶段 | 内容 |
|:---:|------|
| P1 | ✅ request_id + JSON access log（Stage 7A） |
| P1 | ✅ 业务事件日志（Stage 7B） |
| P2 | /metrics endpoint（Prometheus 格式） |
| P2 | OpenTelemetry（tracing + metrics + logs） |
| P2+ | 审计日志独立 channel |

---

## 5. 部署形态

### 5.1 单独部署

```
Hub → stdout JSON log
  ├── Docker log driver
  ├── systemd journald
  └── 可选本地文件（RotatingFileHandler）
```

### 5.2 平台集成部署

```
Gateway/Hermes → X-Request-ID + actor_id 注入
       ↓
Hub → stdout JSON log
       ↓
统一日志平台采集（ELK/Loki 由底座提供）
```

- Hub 不关心日志采集方式，只保证 stdout JSON 格式
- Gateway 注入的 X-Request-ID 被 Hub 复用

---

## 6. 文件清单

| 文件 | 操作 |
|------|:---:|
| `backend/app/core/request_id.py` | 新增（Stage 7A） |
| `backend/app/core/logging.py` | 新增（Stage 7A） |
| `backend/app/core/event_log.py` | 新增（Stage 7B） |
| `backend/app/api/runtime.py` | 修改（Stage 7B） |
| `backend/app/services/runtime_discover_service.py` | 修改（Stage 7B） |
| `backend/app/services/scan_service.py` | 修改（Stage 7B） |
| `backend/app/services/openapi_import_service.py` | 修改（Stage 7B） |
| `backend/app/services/lifecycle_service.py` | 修改（Stage 7B） |
| `backend/app/services/approval_service.py` | 修改（Stage 7B） |
| `backend/app/main.py` | 修改（Stage 7A） |
| `backend/tests/test_observability.py` | 扩展（Stage 7B） |
| `docs/12_observability_logging_design.md` | 更新（Stage 7B） |
