# MT-1：多租户数据模型实现计划

版本：v0.1 | 日期：2026-05-29 | 阶段：MT-1（仅设计，待确认后 Build）

---

## 1. 目标

在现有数据模型上增加 `organization_id` / `workspace_id` / `visibility_scope` 字段，为后续 MT-2（管理态过滤）和 MT-3（Runtime 过滤）打下数据基础。

**MT-1 完成后不能声称多租户隔离已实现。** 真正隔离在 MT-2/MT-3。

---

## 2. 当前核心表字段分析

### 2.1 每表字段概览

| 表 | 已有 ID 列 | 通过 FK 可间接获取 tenant？ | 冗余 workspace 必要性 |
|------|------|:---:|:---:|
| `hub_items` | `id`, `created_by` | — | **必须**：主力查询锚点 |
| `hub_item_versions` | `id`, `hub_item_id` | JOIN hub_items | **必须**：版本独立查询常见 |
| `hub_item_relations` | `id`, `source_item_id`, `target_item_id` | JOIN hub_items ×2 | **必须**：关系过滤需索引 |
| `approval_records` | `id`, `hub_item_id`, `hub_item_version_id` | 双 JOIN | **建议**：审计查询直接过滤 |
| `lifecycle_events` | `id`, `hub_item_id`, `hub_item_version_id` | JOIN hub_items | **建议**：事件查询直接过滤 |
| `scan_reports` | `id`, `hub_item_id`, `hub_item_version_id` | 双 JOIN | **建议**：报告查询直接过滤 |
| `scan_findings` | `id`, `scan_report_id` | JOIN scan_reports → hub_items | ❌：通过 scan_report 间接获取 |
| `categories` | `id`, `name` | — | ❌：全局共享 |
| `tags` | `id`, `name` | — | ❌：全局共享 |
| `hub_item_tags` | `hub_item_id`, `tag_id` | JOIN hub_items | ❌：关联表，通过 item 过滤 |

### 2.2 结论

| 分类 | 表 | 字段 |
|------|------|------|
| **直接加 `workspace_id`** | `hub_items`, `hub_item_versions`, `hub_item_relations` | NOT NULL（MT-1B backfill 后） |
| **冗余 `workspace_id` 便于审计** | `approval_records`, `lifecycle_events`, `scan_reports` | NOT NULL（继承自 item） |
| **不加** | `scan_findings`（JOIN report） | 通过 `scan_report_id` 间接获取 |
| **不加（全局共享）** | `categories`, `tags`, `hub_item_tags` | MT-0 决策：跨 workspace 共享 |

### 2.3 `visibility_scope` 归属

`visibility_scope` 仅加在 `hub_items`：
- 版本继承 item 的 visibility
- 关系不独立定义 visibility

### 2.4 `organization_id` 归属

`organization_id` 仅加在 `hub_items`：
- 冗余 `organization_id` 在子表收益低（绝大多数查询按 workspace 过滤）
- `organization_id` 用于 visibility_scope="organization" 的判断
- 子表通过 JOIN `hub_items` 获取

---

## 3. 字段设计

### 3.1 字段规格

```python
# HubItem 新增
organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
visibility_scope: Mapped[str] = mapped_column(String(50), nullable=False, default="workspace")
```

```python
# HubItemVersion 新增
workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
```

```python
# HubItemRelation 新增
workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
```

```python
# ApprovalRecord, LifecycleEvent, ScanReport 新增
workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
```

### 3.2 类型选择：String vs Enum

**推荐 String**，理由：

| 权衡 | Enum | String |
|------|------|--------|
| PostgreSQL | CREATE TYPE + ALTER TYPE 风险 | VARCHAR 无风险 |
| SQLite | Enum → VARCHAR，无额外成本 | VARCHAR |
| 扩展性 | 新增值需 ALTER TYPE（PG 限制多） | 新增值无需 migration |
| 查询 | PostgreSQL 可用 | WHERE visibility_scope = 'organization' |
| 类型安全 | ✅ 编译期 | ⚠️ 运行时 validation |

**决策**：`visibility_scope` 用 `String(50)`，在 API/Service 层做 validation。

### 3.3 `visibility_scope` 取值

| 值 | 说明 |
|------|------|
| `private` | 仅 owner + admin 可见 |
| `workspace` | 同 workspace 可见（默认） |
| `organization` | 同 organization 可见 |
| `public` | 全平台可见 |

### 3.4 字段长度

| 字段 | 长度 | 说明 |
|------|:---:|------|
| `organization_id` | `String(128)` | 预留足够长度 |
| `workspace_id` | `String(128)` | 同上 |
| `visibility_scope` | `String(50)` | 4 个枚举值，50 足够 |

### 3.5 索引设计

| 表 | 索引 | 说明 |
|------|------|------|
| `hub_items` | `INDEX (workspace_id)` | 主力查询 |
| `hub_items` | `INDEX (organization_id, workspace_id)` | org-level 查询 |
| `hub_items` | `INDEX (workspace_id, status)` | 管理态列表过滤 |
| `hub_item_versions` | `INDEX (workspace_id)` | 版本查询 |
| `hub_item_relations` | `INDEX (workspace_id)` | 关系过滤 |
| `approval_records` | `INDEX (workspace_id)` | 审计查询 |
| `lifecycle_events` | `INDEX (workspace_id)` | 事件查询 |

### 3.6 唯一约束影响

当前约束保持：
- `hub_item_relations` 的 `uq_relation_source_target_type_scope` 不变（跨 workspace 关系由 MT-2 过滤保障，不需要改约束）
- `hub_items` 不需要唯一约束（`name` 非 unique）
- `hub_item_versions` 的 `ix_hub_item_versions_item_version`（`hub_item_id, version` unique）不变

---

## 4. Migration 方案

### 4.1 分三步

#### MT-1A：新增 nullable 列

```python
# Alembic upgrade
op.add_column('hub_items', sa.Column('organization_id', sa.String(128), nullable=True))
op.add_column('hub_items', sa.Column('workspace_id', sa.String(128), nullable=True))
op.add_column('hub_items', sa.Column('visibility_scope', sa.String(50), nullable=True))

op.add_column('hub_item_versions', sa.Column('workspace_id', sa.String(128), nullable=True))
op.add_column('hub_item_relations', sa.Column('workspace_id', sa.String(128), nullable=True))
op.add_column('approval_records', sa.Column('workspace_id', sa.String(128), nullable=True))
op.add_column('lifecycle_events', sa.Column('workspace_id', sa.String(128), nullable=True))
op.add_column('scan_reports', sa.Column('workspace_id', sa.String(128), nullable=True))
```

#### MT-1B：回填历史数据

策略 A（推荐）：**统一回填默认值**

```python
op.execute("UPDATE hub_items SET organization_id = 'default', workspace_id = 'default', visibility_scope = 'workspace' WHERE workspace_id IS NULL")
op.execute("UPDATE hub_item_versions SET workspace_id = 'default' WHERE workspace_id IS NULL")
op.execute("UPDATE hub_item_relations SET workspace_id = 'default' WHERE workspace_id IS NULL")
op.execute("UPDATE approval_records SET workspace_id = 'default' WHERE workspace_id IS NULL")
op.execute("UPDATE lifecycle_events SET workspace_id = 'default' WHERE workspace_id IS NULL")
op.execute("UPDATE scan_reports SET workspace_id = 'default' WHERE workspace_id IS NULL")
```

策略 B：保持 NULL，新数据才写

| 策略 | 优点 | 缺点 |
|------|------|------|
| A（回填） | 查询简洁（不需要 IS NULL OR = 'default'），索引有效 | 修改历史数据 |
| B（NULL） | 不改历史数据 | 查询复杂（WHERE workspace_id IS NULL OR workspace_id = ?），索引可能失效 |

**推荐策略 A**。历史数据量小（PoC 阶段），回填 `"default"` 代价低。Dev mode 下 `workspace_id=None` 时不过滤即可兼容。

#### MT-1C：生产加固（MT-2 阶段再做）

- 改为 NOT NULL + DEFAULT
- 添加索引
- 可设 `visibility_scope` 默认 `"workspace"`
- 本阶段不执行

### 4.2 Migration 风险

| 风险 | 缓解 |
|------|------|
| PostgreSQL Enum CREATE TYPE（如用 Enum） | 用 String 避免 |
| 回填更新锁表 | PoC 数据量小，直接 UPDATE 安全 |
| SQLite ALTER TABLE 限制 | SQLAlchemy batch mode 自动处理 |
| downgrade 丢失数据 | NULL → backfill 的 reverse 仍是 nullable，不丢数据 |
| 索引创建锁表 | PoC 阶段可接受 |

### 4.3 推荐执行顺序

1. MT-1A migration → 新增 nullable 列
2. 运行全部 633 tests 验证正常
3. Commit
4. MT-1B migration → backfill 默认值
5. 运行全部 tests
6. Commit

或合并为一个 migration file 以减少步骤。推荐**合并为一个 migration**：
- `add_column` + `execute(UPDATE)` 在同一 `upgrade()` 中
- `drop_column` 在 `downgrade()` 中

---

## 5. 创建 / 导入写入策略

### 5.1 create item

```python
# HubItemService.create()
item = HubItem(
    name=data.name,
    type=data.type,
    ...
    organization_id=ctx.organization_id or "default",
    workspace_id=ctx.workspace_id or "default",
    visibility_scope=getattr(data, 'visibility_scope', None) or "workspace",
    created_by=ctx.actor_id or data.created_by,
)
```

规则：
- `organization_id` / `workspace_id` 从 AuthContext 取值
- 如 AuthContext 未提供（dev mode），使用 `"default"`
- API 请求 body 不传 `organization_id` / `workspace_id`（从 AuthContext 注入，不信任客户端）
- `visibility_scope` 可从 body 传入，默认 `"workspace"`

### 5.2 create version

```python
# VersionService.create()
item = self.db.get(HubItem, data.hub_item_id)
version = HubItemVersion(
    hub_item_id=data.hub_item_id,
    version=data.version,
    workspace_id=item.workspace_id,  # 继承 item
    ...
)
```

规则：
- `workspace_id` 必须继承 `hub_item.workspace_id`
- 不允许 version 与 item 的 workspace 不一致
- 不重复读取 AuthContext

### 5.3 package import

```python
# ImportService.import_package()
item = HubItem(
    ...
    organization_id=ctx.organization_id or "default",
    workspace_id=ctx.workspace_id or "default",
    visibility_scope="workspace",
)
```

### 5.4 OpenAPI import

```python
# OpenApiImportService.import_from_spec()
item = HubItem(
    ...
    organization_id=ctx.organization_id or "default",
    workspace_id=ctx.workspace_id or "default",
    visibility_scope="workspace",
)
```

### 5.5 presets/init

**建议**：预置资产归属于 `"default"` workspace。

理由：
- 预置资产是 PoC 演示数据，不是生产资产
- Dev mode 下 workspace 过滤在 MT-2 前不做，不影响可用性
- MT-2 后，`"default"` workspace 的资产对 dev mode 仍可见

替代方案：
- 预置资产用 `ctx.workspace_id`（如果 AuthContext 有值）— dev mode 无 Header 时仍是 `"default"`
- 所有 presets 写入当前 ctx workspace — 每次 init 可能抄到不同 workspace

**推荐**：固定 `"default"`，符合 PoC 语义。

### 5.6 relationship create

```python
# RelationService.create()
source_item = self.db.get(HubItem, data.source_item_id)
target_item = self.db.get(HubItem, data.target_item_id)

if source_item.workspace_id != target_item.workspace_id:
    raise ValueError("cross-workspace relations not allowed")

relation = HubItemRelation(
    source_item_id=data.source_item_id,
    target_item_id=data.target_item_id,
    workspace_id=source_item.workspace_id,  # 继承 source
    ...
)
```

### 5.7 approval / lifecycle / scan 写入

```python
# ApprovalService / LifecycleService / ScanService
record.workspace_id = item.workspace_id  # 继承 item
```

---

## 6. MT-1 不实现查询过滤

| 操作 | MT-1 是否过滤 | 说明 |
|------|:---:|------|
| list_items | ❌ | MT-2 实现 |
| get_item | ❌ | MT-2 实现 |
| list_versions | ❌ | MT-2 实现 |
| list_relations | ❌ | MT-2 实现 |
| approve/reject | ❌ | MT-2 实现 |
| lifecycle events | ❌ | MT-2 实现 |
| scan reports | ❌ | MT-2 实现 |
| Runtime Discover | ❌ | MT-3 实现 |
| Runtime Resolve | ❌ | MT-3 实现 |

**MT-1 唯一保证**：数据写入时携带 `workspace_id` / `organization_id` / `visibility_scope`。

---

## 7. Runtime Discover MT-3 影响预览

MT-1 只写字段，不改变 discover 查询。

MT-3 将实现（预览）：

| visibility_scope | discover 是否返回 | 条件 |
|------|:---:|------|
| `private` | ❌ | resolve 需 owner/admin |
| `workspace` | ✅ | `item.workspace_id == ctx.workspace_id` |
| `organization` | ✅ | `item.organization_id == ctx.organization_id` |
| `public` | ✅ | 无条件 |

---

## 8. Storage key MT-4 影响预览

MT-1 不改 storage key。

MT-4 将引入 `TenantPrefixStorageAdapter`（参见 `docs/24_multi_tenancy_design.md` §7）：

```
{workspace_id}/imports/package_abc.zip
{workspace_id}/exports/1234abcd.json
```

---

## 9. 测试计划

| # | 测试 | 类型 | 说明 |
|:---:|------|:---:|------|
| 1 | create item 写入 organization_id / workspace_id | 单元 | mock AuthContext |
| 2 | create item 默认 visibility_scope=workspace | 单元 | |
| 3 | create item organization_id/workspace_id fallback "default" | 单元 | dev mode 无 Header |
| 4 | create version 继承 item.workspace_id | 单元 | |
| 5 | create relation 校验 source/target 同 workspace | 单元 | |
| 6 | create relation 写入 workspace_id | 单元 | |
| 7 | package import 写入 tenant | 单元 | |
| 8 | OpenAPI import 写入 tenant | 单元 | |
| 9 | presets/init 写入 default workspace | 单元 | |
| 10 | list_items 不按 workspace 过滤（MT-1）| 单元 | 验证 MT-1 不改查询 |
| 11 | migration upgrade 在 SQLite 通过 | 集成 | |
| 12 | migration upgrade 在 PostgreSQL 通过（如可用）| 集成 | |
| 13 | migration downgrade 恢复 | 集成 | |
| 14 | 现有 633 tests 继续通过 | 回归 | |
| 15 | visibility_scope 非法值拒绝 | 单元 | 仅在 API/Service 层校验 |

---

## 10. 是否建议 MT-1 进入 Build

**建议进入 Build**。理由：

1. 数据模型变更**最小**（每个表 1 列，不用 ENUM）
2. Migration **安全**（nullable → backfill，可撤销）
3. 不改变查询行为（MT-2 前不要求过滤）
4. 现有 633 tests 不受影响（只加列，不改逻辑）
5. 写入端逻辑简单（从 AuthContext 取值或继承 item）

前置条件：
- 确认 `visibility_scope` 用 String 而非 Enum
- 确认 backfill 策略 A（统一回填 `"default"`）
- 确认 MT-1B 与 MT-1A 合并为一个 migration

---

## 11. 文档更新计划

MT-1 Build 后更新：

| 文档 | 更新内容 |
|------|------|
| `docs/24_multi_tenancy_design.md` | 标注 MT-1 已完成，更新字段规格 |
| `docs/02_solution_design.md` | "多租户隔离" → "MT-1 数据模型已完成，MT-2 查询过滤未实现" |
| `docs/08_roadmap_workload.md` | MT-1 → ✅ |
| `docs/20_current_baseline_summary.md` | 测试基线更新，MT-1 状态 |
| `README.md` | 如需要 |

---

## 12. 不做事项（MT-1 边界）

| 不做 | 说明 |
|------|------|
| 管理态查询过滤 | MT-2 |
| Runtime workspace 过滤 | MT-3 |
| Storage tenant prefix | MT-4 |
| Scoped role binding | MT-5 |
| visibility_scope "public" 完整策略 | MT-3 |
| NOT NULL 强制 | MT-1C（MT-2 后） |
| owner transfer | 不在 MT scope |
| tenant admin UI | 不在 MT scope |
| 前端变更 | 不在 MT scope |
| demo worktree 修改 | 禁止 |

---

## 13. 人工确认事项

| 事项 | 确认 |
|------|:---:|
| visibility_scope 用 String(50) | ⏳ |
| backfill 策略 A（统一 "default"） | ⏳ |
| MT-1A + MT-1B 合并为单 migration | ⏳ |
| presets/init → "default" workspace | ⏳ |
| organization_id 仅加 hub_items（子表不冗余） | ⏳ |
| scan_findings 不加 workspace_id（JOIN report） | ⏳ |
| categories/tags/hub_item_tags 全局共享 | ⏳ |
| 不改变查询行为 | ⏳ |
