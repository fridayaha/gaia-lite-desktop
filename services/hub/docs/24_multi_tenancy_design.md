# 多租户设计与数据模型影响分析

版本：v0.10 | 日期：2026-06-02 | 阶段：MT-2 管理态全部完成。MT-3A `can_runtime_access_item` helper + MT-3B Runtime Discover tenant/visibility filtering 已完成（826 tests）。MT-3C/3D 未实现。

---

## 1. 背景与目标

Hub 当前为单租户模式。随着平台化推进，需要支持多个组织/工作空间在同一 Hub 实例中独立管理能力资产，互不干扰。

### 1.1 多租户范围

- **数据隔离**：不同 workspace 的资产不可互相访问
- **管理隔离**：workspace A 的成员不能管理 workspace B 的资产
- **发现隔离**：Runtime Consumer 默认只能发现本 workspace 的已发布资产
- **存储隔离**：不同 workspace 的 storage key 有独立 prefix
- **可见性控制**：资产可设置为 private / workspace / organization / public

### 1.2 非目标

- 不做 billing / quota（非 Hub 职责）
- 不做跨组织的数据共享/联邦（P3+）
- 不做完整 IAM / OIDC（RBAC-5）
- 不做多 DB / schema隔离（当前单 schema 足够）

---

## 2. 当前多租户基础

### 2.1 已有的基础设施

| 组件 | 已有能力 | 说明 |
|------|----------|------|
| `AuthContext` | `organization_id`, `workspace_id` | 从 Header 注入（`X-Organization-ID` / `X-Workspace-ID`） |
| Event Log | `workspace_id`, `organization_id` | 日志中已捕获租户信息 |
| Runtime API | `workspace_id`, `organization_id` Query params | API 入口接受，注入 AuthContext |
| `ScopedCapabilityAccessPolicy` | role-based access | 仅校验 `runtime_consumer` / `platform_admin` role，不做 workspace 过滤 |
| Ownership Policy | `created_by` based | 仅基于 `actor_id == created_by`，不做 workspace 隔离 |
| `HubItem.created_by` | actor_id 字符串 | 当前对象级 ownership 的唯一锚点 |

### 2.2 已验证的 Header 注入路径

```
Gateway / Load Balancer
  ├── X-Organization-ID: "org-123"
  ├── X-Workspace-ID: "ws-456"
  └── X-Actor-ID: "user-789"
      │
      ▼
AuthContext.from_headers()
  ├── organization_id = "org-123"   ← 已解析
  ├── workspace_id = "ws-456"       ← 已解析
  └── actor_id = "user-789"         ← 已解析
      │
      ▼
Event Log: {"workspace_id": "ws-456", "organization_id": "org-123", ...}
```

**关键发现**：`organization_id` / `workspace_id` 已经可以通过 Header 获取，且日志已记录。但**未在任何查询或策略中使用**。

---

## 3. 当前缺口

### 3.1 核心表无 tenant/workspace 字段

当前各表均无 `workspace_id` / `organization_id` 列：

| 表 | 已有 | 缺 |
|------|------|------|
| `hub_items` | `created_by` | `workspace_id`, `organization_id` |
| `hub_item_versions` | `created_by` | `workspace_id` |
| `hub_item_relations` | `created_by` | `workspace_id` |
| `approval_records` | `operator` | `workspace_id` |
| `lifecycle_events` | `operator` | `workspace_id` |
| `scan_reports` | — | `workspace_id` |
| `scan_findings` | — | `workspace_id`（通过 report） |
| `categories` | — | `workspace_id`（是否全局待定） |
| `tags` | — | `workspace_id`（是否全局待定） |

### 3.2 查询无租户过滤

| 位置 | 当前行为 | 缺口 |
|------|----------|------|
| `HubItemService.list_items()` | 全表查询 | 无 `WHERE workspace_id = ?` |
| `LifecycleService.submit_item()` | 不校验 workspace | 跨 workspace 操作无保护 |
| `ApprovalService` | 全表查询 | 无 workspace 过滤 |
| `ScanService.scan_version()` | 不校验 workspace | 跨 workspace 扫描无限制 |
| `RuntimeDiscoverService.discover()` | 无 workspace 过滤 | 返回所有 workspace 的资产 |
| `RuntimeDiscoverService.resolve()` | 无 workspace 过滤 | 允许跨 workspace resolve |
| `RelationService` | 无 workspace 过滤 | 跨 workspace 关系无限制 |

### 3.3 Storage key 无 tenant prefix

当前 `LocalStorageAdapter` key 示例：
```
imports/package_abc123.zip
exports/1234abcd.json
```

缺少 `{workspace_id}/` prefix，无法隔离不同 workspace 的存储。

### 3.4 Role binding 无作用域

当前 `ScopedCapabilityAccessPolicy`：
```python
def _has_access(self, context):
    return "platform_admin" in context.roles or "runtime_consumer" in context.roles
```

没有 `workspace_id` 关联。`runtime_consumer` 是全局 role，能访问所有 workspace 的资产。

### 3.5 Runtime Discover 无 workspace 过滤

`discover()` 方法的 SQL 查询完全不含 `workspace_id` 条件。策略层 `ScopedCapabilityAccessPolicy` 的 `can_discover` 只校验 role，不校验 workspace。

---

## 4. visibility_scope 设计

### 4.1 模型

为 `hub_items` 新增列：

```python
visibility_scope: str  # "private" | "workspace" | "organization" | "public"
```

取值与行为：

| 值 | Discover 可见 | Resolve 可见 | 说明 |
|------|:---:|:---:|------|
| `private` | ❌ | 仅 owner + admin | 不进入 discover，但 owner 可以 resolve |
| `workspace` | ✅ 仅本 workspace | ✅ 仅本 workspace | 默认值 |
| `organization` | ✅ 本 organization | ✅ 本 organization | 跨 workspace 但同 org |
| `public` | ✅ 所有人 | ✅ 所有人 | 平台全局可见 |

### 4.2 跨 workspace 可见性判断

```python
def can_see(visibility: str, viewer_ws: str, viewer_org: str,
            item_ws: str, item_org: str) -> bool:
    if visibility == "private":
        return False  # discover 不可见（resolve 需 owner 判断）
    if visibility == "workspace":
        return viewer_ws == item_ws
    if visibility == "organization":
        return viewer_org == item_org
    if visibility == "public":
        return True
```

### 4.3 Resource ownership with workspace

在保证 `created_by`（actor-level）的前提下，增加 workspace-level 隔离：

```python
def is_in_workspace(item: HubItem, context: AuthContext) -> bool:
    if not context.workspace_id or not item.workspace_id:
        return False  # 缺失 workspace 信息时保守拒绝
    return context.workspace_id == item.workspace_id
```

### 4.4 API 层默认行为

- **管理态 API**（CRUD）：自动注入当前 AuthContext 的 `workspace_id` 到 WHERE
- **Runtime Discover**：默认只返回 `visibility_scope != 'private'` 且 `workspace_id` 匹配的资产
- **Runtime Resolve**：`private` 资产可被 owner resolve，但不能被 resolve 发现

---

## 5. 数据模型变更

### 5.1 需影响的表

#### HubItem（primary anchor）

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 所属 workspace |
| `organization_id` | `String(100)` NOT NULL | 所属 organization（冗余，便于 org-level 过滤） |
| `visibility_scope` | `String(50)` NOT NULL DEFAULT "workspace" | 可见性级别 |

Index：`INDEX (workspace_id)`，`INDEX (organization_id, workspace_id)`

#### HubItemVersion

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 继承自 HubItem，冗余便于查询 |

#### HubItemRelation

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 所属 workspace |

#### ApprovalRecord

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 审批记录的 workspace |

#### LifecycleEvent

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 事件 workspace |

#### ScanReport

| 新增列 | 类型 | 说明 |
|--------|------|------|
| `workspace_id` | `String(100)` NOT NULL | 扫描报告的 workspace |

### 5.2 暂不影响的表

| 表 | 决策 | 理由 |
|------|:---:|------|
| `categories` | **全局共享** | 分类体系跨 workspace 保持一致 |
| `tags` | **全局共享** | 标签作为规范化元数据，跨 workspace 共享 |
| `scan_findings` | 同 ScanReport | `workspace_id` 继承自父表 join 即可，不冗余 |

### 5.3 Migration 规模

- 每个表新增 1-3 列
- 约 6-8 个 `op.add_column` 操作
- 需要 `op.execute("UPDATE ... SET workspace_id = 'default', organization_id = 'default'")` 填充历史数据
- 新增 3-5 个 index

---

## 6. 查询过滤策略

### 6.1 管理态 API 自动注入

所有管理态 API（CRUD、Lifecycle、Approval、Scan、Import/Export）的查询增加：

```python
def _ensure_workspace_filter(query, table, context: AuthContext):
    if context.workspace_id:
        query = query.filter(table.workspace_id == context.workspace_id)
    return query
```

`platform_admin` 角色（含 `organization_id` scope）可查看同 org 所有 workspace。

### 6.2 Runtime Discover 改造

```python
def discover(self, filters, context):
    query = (...).filter(HubItem.workspace_id == context.workspace_id)
    # 或 visibility-aware:
    # .filter(HubItem.visibility_scope.in_(["workspace", "organization", "public"]))
    # .filter(or_(workspace match, org match, public))
```

### 6.3 策略层扩展

`ScopedCapabilityAccessPolicy` 增加 workspace 校验：

```python
class ScopedCapabilityAccessPolicy:
    def can_discover(self, item, version, context):
        if not self._has_access(context):
            return False
        if item.visibility_scope == "private":
            return False
        return self._workspace_match(item, context)

    def can_resolve(self, item, version, context):
        if not self._has_access(context):
            return False
        if item.visibility_scope == "private":
            return item.created_by == context.actor_id
        return self._workspace_match(item, context)

    def _workspace_match(self, item, context):
        if context.workspace_id is None:
            return True  # dev mode 兼容
        return item.workspace_id == context.workspace_id
```

---

## 7. Storage key 多租户 prefix

### 7.1 当前 key 模式

```
imports/package_abc123.zip
exports/1234abcd.json
```

### 7.2 改造后

```
{workspace_id}/imports/package_abc123.zip
{workspace_id}/exports/1234abcd.json
```

### 7.3 实现方式

在 `StorageAdapter` 层透明注入 prefix：

```python
class TenantPrefixStorageAdapter:
    """Wraps StorageAdapter, prepends workspace_id prefix."""
    def __init__(self, inner: StorageAdapter):
        self._inner = inner
        self._workspace_id: str | None = None

    def bind_workspace(self, workspace_id: str):
        self._workspace_id = workspace_id

    def _prefix(self, key: str) -> str:
        if self._workspace_id:
            return f"{self._workspace_id}/{key}"
        return key

    def put_bytes(self, key, data, content_type=None):
        return self._inner.put_bytes(self._prefix(key), data, content_type)

    def get_bytes(self, key):
        return self._inner.get_bytes(self._prefix(key))
```

### 7.4 安全约束

- workspace_id 必须由 AuthContext 提供，不接受客户端直接传值
- workspace_id 进行 `^[a-zA-Z0-9._\-]+$` 正则校验
- 禁止路径穿越（`..`）

---

## 8. Role binding 是否需要组织/工作空间作用域

### 8.1 当前状态

Role 通过 Header `X-Roles` 注入，为全局 role：
```
X-Roles: platform_admin, editor
```

### 8.2 MT-0 建议

**暂不实现 workspace-level role binding**。理由：

1. Role binding 是 IAM 系统职责，不是 Hub 职责；
2. Hub 通过 Header 接收 role，role 的 workspace scope 应由 Gateway/IAM 决定；
3. Hub 只需信任 `workspace_id` 和 `roles` 都由 Gateway 注入即可；
4. 完整的 workspace-scoped role binding 需要 IAM 系统支持，当前不做。

### 8.3 MT 阶段的 role 策略

| Role | scope | 说明 |
|------|-------|------|
| `platform_admin` | global | 可跨 workspace 操作 |
| `workspace_admin` | per workspace | Gateway 注入时限制 scope |
| `editor` | per workspace | Gateway 注入时限制 scope |
| `reviewer` | per workspace | Gateway 注入时限制 scope |
| `runtime_consumer` | per workspace | Gateway 注入时限制 scope |

Hub 不做 role scope 解析，但**查询层根据 `workspace_id` 过滤**。

### 8.4 后续（MT-5+）

如果需要 `platform_admin` 查看同 organization 所有 workspace：
- 在策略层加 `organization_id` 匹配；
- 不需要额外的 role scope 字段。

---

## 9. 历史数据处理

### 9.1 迁移策略

采用 **nullable → backfill → NOT NULL** 三步策略：

1. **Migration 1**：新增 `workspace_id` / `organization_id` / `visibility_scope` 列，允许 NULL
2. **Backfill**：执行 `UPDATE ... SET workspace_id = 'default', organization_id = 'default', visibility_scope = 'workspace'`
3. **Migration 2**：将列改为 NOT NULL + DEFAULT

### 9.2 默认值

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `workspace_id` | `"default"` | 历史数据归入默认 workspace |
| `organization_id` | `"default"` | 历史数据归入默认 organization |
| `visibility_scope` | `"workspace"` | 默认仅本 workspace 可见 |

### 9.3 Dev mode 兼容

当 `HUB_AUTH_MODE=dev` 且 AuthContext 无 `workspace_id` 时：
- 管理态 API 不做 workspace 过滤（兼容现有 dev 模式行为）
- Runtime API 不做 workspace 过滤（兼容现有 discover/resolve）

---

## 10. 是否需要 DB migration

**需要**。

| 操作 | 说明 |
|------|------|
| 新增列（6-8个表） | Alembic `op.add_column` |
| 回填历史数据 | Alembic `op.execute(UPDATE ...)` |
| 新增 Index（3-5个） | Alembic `op.create_index` |
| 设置 NOT NULL | 分两步 migration |

Migration 文件数：
- `MT-1`：1-2 个 migration files
- 不涉及 Enum 变更，不涉及表结构大改

---

## 11. 分阶段路线

| 阶段 | 内容 | 输出 |
|:---:|------|------|
| **MT-0** | 多租户设计与数据模型影响分析（本阶段） | ✅ 本文档（`docs/24_multi_tenancy_design.md`） |
| **MT-1** | 数据模型实现：新增列、DB migration、回填历史数据 | ✅ Alembic migration + 模型更新（681 tests，写入路径已修复） |
| **MT-1.1** | 写入路径修复：create/import/approval/lifecycle/scan 正确写入 tenant | ✅ 681 tests（approval_records、lifecycle_events 所有路径已覆盖） |
| **MT-2** | 管理态过滤：所有 CRUD/Approval/Lifecycle/Scan API 注入 workspace 过滤 | 📋 MT-2A TenantPolicy 基础策略已实现（39 tests） |
| **MT-2A** | TenantPolicy 基础策略层 | ✅ 720 tests（39 tenant policy tests + 681 回归） |
| **MT-2B** | HubItem list/detail/update + version list/detail tenant guard | ✅ 737 tests（17 filter tests，已接入 API） |
| **MT-2C** | lifecycle/approval/scan guard | ✅ 757 tests |
| **MT-2D** | relation/export/import guard | ✅ 771 tests（14 filter tests，已接入 API + import workspace 匹配修复） |
| **MT-3** | Runtime 过滤：Discover/Resolve/ToolDefinition 增加 workspace + visibility_scope 过滤 | `ScopedCapabilityAccessPolicy` 改造 |
| **MT-3A** | `can_runtime_access_item` helper + unit tests | ✅ 802 passed（28 runtime tests + 771 回归） |
| **MT-3B** | Runtime Discover tenant/visibility filter | ✅ 826 passed（24 discover tests + 802 回归） |
| **MT-3C** | Resolve / Manifest / Tool Definition tenant guard | 📋 未实现 |
| **MT-3D** | Dependency tenant behavior（递归展开） | 📋 未实现 |
| **MT-4** | Storage prefix：`TenantPrefixStorageAdapter` + import/export key 改造 | 📋 未实现（Storage 层改造） |
| **MT-5** | Scoped roles（P2）：如果平台 IAM 支持，对接 workspace-scoped role binding | 📋 未实现（策略层扩展） |
| **P3+** | Cross-workspace sharing、visiblity override、audit trail per workspace | 不做 |

---

## 12. 影响范围评估

| 模块 | 影响 | Phase |
|------|:---:|:---:|
| `models/` (HubItem, Version, Relation, Approval, LifecycleEvent, ScanReport) | 新增 `workspace_id` / `organization_id` / `visibility_scope` 列 | MT-1 |
| `alembic/` | 新增 migration file(s) | MT-1 |
| `services/hub_item_service.py` | 写入支持 `organization_id`/`workspace_id` 透传（从 AuthContext）；查询过滤 | MT-1.1 ✅（写入）/ MT-2（过滤） |
| `services/version_service.py` | 版本创建继承 `workspace_id` | MT-1 ✅ |
| `services/lifecycle_service.py` | 全部写入路径已支持 tenant（submit/publish/disable/archive/rollback） | MT-1.1 ✅ |
| `services/approval_service.py` | approval_records + lifecycle_events 写入 tenant（approve/reject/request-change） | MT-1.1 ✅ |
| `services/scan_service.py` | scan_reports + lifecycle_events 写入 tenant | MT-1.1 ✅ |
| `services/relation_service.py` | 关系写入继承 source item tenant | MT-1 ✅ |
| `services/import_service.py` | 支持 `organization_id`/`workspace_id` 透传 | MT-1.1 ✅ |
| `services/openapi_import_service.py` | 支持 `organization_id`/`workspace_id` 透传 | MT-1.1 ✅ |
| `services/runtime_discover_service.py` | Discover/Resolve 增加 workspace + visibility_scope 过滤 | MT-3 |
| `policies/capability_access.py` | `ScopedCapabilityAccessPolicy` 增加 workspace + visibility 判断 | MT-3 |
| `policies/ownership_policy.py` | 增加 workspace-level 校验（补充 created_by） | MT-3 |
| `adapters/storage.py` | `TenantPrefixStorageAdapter` wrapper | MT-4 |
| `adapters/local_storage.py` | 无需改动（通过 wrapper 透明） | MT-4 |
| `api/runtime.py` | API 层将 workspace_id 传入 | MT-3 |
| `api/hub_items.py` 等管理态 API | 无需代码改动（Service 层统一处理） | MT-2 |
| `tests/` | 所有测试需注入 `workspace_id` / `organization_id` 到 fixture | MT-1+ |
| `docs/` | 更新架构/准入/Roadmap 文档 | 全程 |

---

## 13. 风险与边界

### 风险

| 风险 | 缓解 |
|------|------|
| workspace_id 缺失导致拒绝服务 | Dev mode 兼容：`workspace_id is None` 时不过滤 |
| Migration 历史数据不完整 | backfill 默认值 + 日志记录 |
| 跨 workspace 资产关系断裂 | 关系创建时校验 source/target 在同一 workspace |
| Storage key 迁移后旧 key 不可访问 | `TenantPrefixStorageAdapter` 仅在启用 MT 后生效，不迁移旧数据 |
| 测试覆盖下降 | 所有 CRUD 测试强制 `workspace_id`；旧测试在 dev mode 或默认 `workspace_id` 下仍通过 |

### 边界

| 边界 | 说明 |
|------|------|
| 单 schema 多 workspace | 不创建独立 DB/schema per tenant |
| 不做跨 workspace 共享 | P3+ |
| 不做 billing / quota | 非 Hub 职责 |
| 不做 IAM role scope 解析 | Hub 只消费 Header 中的 role，不解析 scope |
| 不做 OIDC / JWT | RBAC-5 |
| workspace_id 格式不约束 | 由平台 Gateway 注入，Hub 不做格式校验（仅防注入） |

---

## 14. 文档参考

| 文档 | 说明 |
|------|------|
| `docs/07_rbac_approval_design.md` | 当前 RBAC 设计 |
| `docs/13_rbac_auth_integration_plan.md` | Gateway Header 注入方案 |
| `docs/14_rbac_decision_record.md` | RBAC 决策记录 |
| `docs/02_solution_design.md` | 整体架构 |
| `docs/05_admission_security_design.md` | 安全准入 |
