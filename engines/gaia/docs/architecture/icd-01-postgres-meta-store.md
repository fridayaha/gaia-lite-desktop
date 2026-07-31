# ICD-01: PostgresMetaStore — 业务本体元数据持久层接口契约

| 字段 | 内容 |
| ---- | ---- |
| **接口版本** | v1.0 |
| **实现类** | `ontology.layers.metadata.PostgresMetaStore` |
| **所属层** | Metadata Layer |
| **依赖组件** | PostgreSQL 16（业务表 public schema；同实例另承载 Iceberg REST jdbc backend + Gravitino entity store） |
| **关联 ADR** | ADR-004（PG 存元数据）、ADR-005（properties 用 JSONB） |
| **关联 Schema** | `core/schemas/ontology.py`（pydantic 领域模型）、`core/models/ontology.py`（ORM） |

---

## 1. 职责边界

| 允许 | 禁止 |
| ---- | ---- |
| 存业务本体元数据（Ontology/ObjectType/PropertyDef/LinkType/ActionType/InterfaceType/ValueType/Struct/ObjectTypeGroup/Branch） | 存物理表元数据（物理表元数据在 Gravitino） |
| 存运行态：object_state（Action 操作态，OCC 乐观锁）、outbox（副作用队列）、action_execution_logs（审计）、datasets（治理记录） | 参与数据查询（查询走 Doris/Trino，PG 只存元数据+操作态） |
| 提供事务接口（`commit_transaction()` / `rollback_transaction()`） | — |

> 红线 #2：PostgreSQL 仅存业务本体元数据，不存物理表元数据、不参与查询。

---

## 2. 构造

```python
class PostgresMetaStore:
    def __init__(self, session: AsyncSession) -> None: ...
```

- 依赖注入：由 `config/container.py` 注入 `AsyncSession`
- session 属性：`@property def session(self) -> AsyncSession`

---

## 3. 接口方法签名（v1.0 基线）

### 3.1 Ontology 容器

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `create_ontology` | `(ontology: Ontology) -> Ontology` | 含 id/created_at 的 Ontology | `ConflictError`（api_name 重复） |
| `get_ontology` | `(api_name: str, *, include_non_active: bool = False) -> Ontology` | Ontology；不存在 → `NotFoundError` | `NotFoundError` |
| `list_ontologies` | `(*, include_non_active: bool = False) -> list[Ontology]` | Ontology 列表（默认排除软删除） | — |
| `list_ontologies_with_counts` | `() -> list[...]` | 含 object_type/action_type/link_type 计数的元组列表 | — |
| `update_ontology` | `(api_name: str, ...) -> Ontology` | 更新后的 Ontology | `NotFoundError` |
| `delete_ontology` | `(api_name: str) -> None` | 软删除（治理记录保留） | `NotFoundError` |
| `restore_ontology` | `(api_name: str) -> Ontology` | 恢复软删除的 Ontology | `NotFoundError` |
| `get_ontology_impact` | `(api_name: str) -> dict[str, Any]` | 删除影响评估（关联 OT/Action/Link 计数） | — |

### 3.2 ObjectType

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `create_object_type` | `(object_type: ObjectType) -> ObjectType` | 含嵌套 properties 的 ObjectType | `ConflictError`（api_name 重复）、`NotFoundError`（ontology 不存在） |
| `get_object_type` | `(ontology_api_name: str, api_name: str) -> ObjectType` | 含 eager-loaded properties 的 ObjectType | `NotFoundError` |
| `get_object_type_by_api_name` | `(ontology_id: str, api_name: str) -> ObjectType` | 按 ontology_id 查 | `NotFoundError`、`MultipleResultsFound`（查重防线，见错误模式 #6） |
| `list_object_types` | `(ontology_api_name: str, *, include_non_active: bool = False) -> list[ObjectType]` | ObjectType 列表 | `NotFoundError`（ontology 不存在） |
| `list_object_type_summaries` | `(ontology_id: str) -> list[tuple[ObjectTypeModel, int, int, int]]` | 含 property/link/action 计数 | — |
| `update_object_type` | `(id: str, updates: dict[str, Any]) -> ObjectType` | 部分更新 | `NotFoundError` |
| `delete_object_type` | `(id: str) -> None` | 软删除 | `NotFoundError` |

### 3.3 Property

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `add_property` | `(object_type_id: str, prop: PropertyDef) -> PropertyDef` | 含 id 的 PropertyDef | `ConflictError`、`NotFoundError` |
| `update_property_backing_mapping` | `(property_id: str, ...) -> PropertyDef` | 更新物理列映射 | `NotFoundError` |
| `get_properties` | `(object_type_id: str) -> list[PropertyDef]` | 属性列表 | — |
| `delete_property` | `(property_id: str) -> None` | 删除属性 | `NotFoundError` |

> properties 以 JSONB 存储（ADR-005）。写 JSONB 字段后必须 `flag_modified` 防止静默丢失（见错误模式 #7）。

### 3.4 SharedProperty / LinkType / ActionType / InterfaceType / ValueType / Struct / Group / Branch

| 方法 | 签名 | 返回 |
| ---- | ---- | ---- |
| `create_shared_property` | `(prop: SharedProperty) -> SharedProperty` |
| `list_shared_properties` | `() -> list[SharedProperty]` |
| `link_shared_property` | `(object_type_id: str, shared_property_id: str) -> None` |
| `create_link_type` | `(link: LinkTypeDef) -> LinkTypeDef` |
| `get_link_types` | `(ontology_api_name: str, *, include_non_active: bool = False) -> list[LinkTypeDef]` |
| `delete_link_type` | `(link_id: str) -> None` |
| `create_action_type` | `(action: ActionType, auto_commit: bool = True) -> ActionType` |
| `update_action_type` | `(...) -> ActionType` |
| `get_action_type` | `(ontology_api_name: str, api_name: str) -> ActionType` |
| `list_action_types` | `(ontology_api_name: str) -> list[ActionType]` |
| `create_interface_type` | `(iface: InterfaceType) -> InterfaceType` |
| `get_interface_types` | `(ontology_api_name: str) -> list[InterfaceType]` |
| `get_interface_type` | `(...) -> InterfaceType` |
| `get_object_types_by_interface` | `(...) -> list[ObjectType]` |
| `get_rids_by_interface` | `(...) -> list[str]` |
| `add_interface_to_object_type` | `(...) -> None` |
| `remove_interface_from_object_type` | `(...) -> None` |
| `create_value_type` | `(vt: ValueType) -> ValueType` |
| `create_struct` | `(struct: Struct) -> Struct` |
| `create_group` | `(group: ObjectTypeGroup) -> ObjectTypeGroup` |
| `create_branch` | `(branch: Branch) -> Branch` |

### 3.5 Action 执行态（object_state / outbox / 审计）

| 方法 | 签名 | 返回 | 说明 |
| ---- | ---- | ---- | ---- |
| `create_execution_log` | `(...) -> ActionExecutionLogModel` | 审计日志 | 幂等性由 idempotency_key 保障 |
| `get_execution_by_idempotency_key` | `(idempotency_key: str) -> ActionExecutionLogModel \| None` | 幂等查重 | — |
| `create_outbox_record` | `(...) -> OutboxModel` | 副作用队列 | effect_type: WEBHOOK/WRITE_BACK/INDEX/ARCHIVE/SUB_ACTION/KAFKA_TOPIC |
| `fetch_pending_outbox` | `(batch_size, ...) -> list[dict]` | 待处理 outbox | — |
| `mark_outbox_completed` | `(outbox_id: str) -> None` | — | — |
| `retry_outbox` | `(outbox_id, retry_count, error, next_retry_at) -> None` | 重试 | — |
| `move_outbox_to_dlq` | `(outbox_id, error) -> None` | 死信队列 | — |
| `claim_pending_by_ontology` | `(...) -> list[dict]` | 按本体认领（批量） | — |
| `mark_outbox_batch_completed` | `(outbox_ids: list[str]) -> int` | 批量完成 | 返回影响行数 |
| `delete_old_completed_outbox` | `(retention_days: int = 7) -> int` | 清理过期 | 返回删除行数 |
| `upsert_object_state` | `(...) -> int` | OCC upsert | 返回新版本号；影响行=0 → ConflictError（version 不匹配） |
| `get_object_state` | `(rid: str) -> dict[str, Any] \| None` | 操作态 | — |
| `get_object_states_by_rids` | `(...) -> list[dict]` | 批量取 | — |
| `get_object_states_by_type` | `(...) -> list[dict]` | 按类型取 | — |
| `get_rids_by_type` | `(...) -> list[str]` | 按类型取 ID | — |

### 3.6 事务

| 方法 | 签名 | 说明 |
| ---- | ---- | ---- |
| `commit_transaction` | `() -> None` | 显式提交（多步写入用 `async with transaction()` 包裹） |
| `rollback_transaction` | `() -> None` | 回滚 |
| `close` | `() -> None` | 关闭 session |

---

## 4. 异常契约

| 异常 | HTTP 映射 | 触发场景 |
| ---- | --------- | -------- |
| `NotFoundError(OntologyError)` | 404 | 资源不存在 |
| `ConflictError(OntologyError)` | 409 | 唯一约束冲突 / OCC 版本不匹配 |
| `ValidationError(OntologyError)` | 422 | 参数校验失败 |

> 完整异常层级见 `core/exceptions.py` 与 `architecture_overview.md` §10。

---

## 5. 关键设计约束

1. **主键**：Palantir RID（`ri.ontology.main.object.{uuid}`，系统分配，稳定不变），禁止自增 ID（红线 #5）。RID 是系统身份，与 primary key（业务身份）正交分离。详见 [graph-reasoning-design.md §4.1 身份模型说明](../architecture/graph-reasoning-design.md)。注：Ontology/ObjectType/LinkType 等元数据资源的主键仍用裸 UUID（`uuid.uuid4().hex`），仅 object_state/object_links 的 rid 采用完整 RID 格式。
2. **唯一约束**：`api_name` 在所属范围内唯一（Ontology 内全局唯一 / ObjectType 内唯一）
3. **外键删除**：统一 `ON DELETE CASCADE`；`ActionType.affected_object_type_id` 例外（`SET NULL`）
4. **JSONB 字段**：properties/parameters/rules/constraints/fields 等用 JSONB（ADR-005）；写入后必须 `flag_modified`
5. **ORM 与 Schema 分离**：`core/models/` 放 ORM，`core/schemas/` 放 pydantic；禁止直接暴露 ORM 对象（红线 #2）
6. **schema 变更**：必须走 Alembic 迁移（`alembic/versions/`），禁止手写 SQL / 手改 init 脚本
7. **事务管理**：多步写入用 `async with self.transaction():` 包裹 + `auto_commit=False`（见 transaction-management-best-practices.md）

---

## 6. 变更管理

- 任何公开方法签名变更（增删参数、改返回类型）需 bump ICD 版本号并评审
- 新增方法不破坏 v1.0 兼容性，记为 v1.x
- 破坏性变更需 v2.0 并提供迁移说明
- 实体文件：`docs/architecture/icd-01-postgres-meta-store.md`（本文件）
- 索引表：`architecture_plan.md` §十六 ICD-01、`CLAUDE.md` ICD 基线索引
