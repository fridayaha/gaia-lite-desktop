# Gaia 事务管理工程化经验与最佳实践

> 本文档沉淀 Gaia 后端（SQLAlchemy 2.0 async + FastAPI）事务管理的工程化经验，
> 作为开发者的独立参考手册。与 [`engineering_principles_and_best_practices.md`](./engineering_principles_and_best_practices.md)
> 互补：本文专注"事务怎么写对"，红线规范讲"不能做什么"。
>
> 最后更新：2026-06-27
>
> 背景：ActionType 版本快照丢失 bug（见 [`../bugfix/action-type-version-snapshot-not-persisted.md`](../bugfix/action-type-version-snapshot-not-persisted.md)）
> 暴露了事务边界设计缺陷，本文是修复后的经验固化。

---

## 一、核心心智模型：一个请求，一个事务，一个提交点

Gaia 的数据写入遵循 SQLAlchemy 2.0 / FastAPI 业界标准 **unit-of-work** 模式：

```
HTTP 请求
  └─ FastAPI yield 依赖 → 新建 AsyncSession（请求级生命周期）
       └─ Service.use_case_method()
            └─ async with self.transaction():   ← 唯一提交点
                 ├─ meta.method_a(auto_commit=False)  ← 只 flush
                 ├─ meta.method_b(auto_commit=False)  ← 只 flush
                 └─ ...
            （正常退出 → commit；异常 → rollback）
       └─ 请求结束 → service.aclose() → session.close()
```

**三条铁律**：

1. **低层 helper 不 commit，只 flush** —— `flush()` 把 SQL 发到数据库但不结束事务，让上层 use-case 决定提交时机
2. **事务边界在 Service 层** —— use-case 用 `async with self.transaction():` 包裹多步操作，正常退出 commit、异常 rollback
3. **一个 use-case 一个提交点** —— 不要在 use-case 中途 commit 拿主键再继续写（用 `flush()` 拿主键即可）

> 对应业界最佳实践（[FastAPI + SQLAlchemy Best Practices](https://mshaeri.com/blog/fastapi-sqlalchemy-best-practices/)）：
> item 10「Keep Transaction Boundaries Explicit」、item 12「Do Not Commit Inside Low-Level Helpers」。

---

## 二、分层事务职责

Gaia 五层架构中，事务职责严格分层（架构 §1：层间不互调，Service 经层方法编排）：

| 层 | 事务职责 | 禁止 |
|----|---------|------|
| **Routes** | 无事务感知，薄层转发 | 直接操作 session |
| **Services** | **事务边界唯一拥有者**：用 `transaction()` 包裹 use-case | 跨 use-case 共享事务 |
| **Layer（PostgresMetaStore 等）** | 提供原子操作方法（`auto_commit` 参数）+ `transaction()` 上下文管理器 | 在方法内擅自 commit（除非 `auto_commit=True` 默认行为） |
| **Core Models** | 无事务感知，纯 ORM 映射 | — |

### 2.1 Service 层：用 `transaction()` 包裹 use-case

```python
# ✅ 正确：ActionType 写入 + 版本快照原子提交
async def update_action_type(self, ontology_api_name, api_name, updates, published_by="system"):
    async with self.transaction():                      # 唯一提交点
        updated = await self._metadata.update_action_type(
            ontology_api_name, api_name, updates, auto_commit=False  # 只 flush
        )
        await self._publish_version_snapshot(updated, published_by)  # 内部 auto_commit=False
    return updated
```

```python
# ❌ 错误：低层方法各自 commit，无法原子化（快照失败时 update 已提交，数据不一致）
async def update_action_type(self, ontology_api_name, api_name, updates, published_by="system"):
    updated = await self._metadata.update_action_type(ontology_api_name, api_name, updates)
    await self._publish_version_snapshot(updated, published_by)  # 失败了 update 不会回滚
    return updated
```

### 2.2 Layer 层：方法提供 `auto_commit` 参数

低层方法默认 `auto_commit=True`（单步操作自提交，向后兼容），事务内传 `False`（只 flush）：

```python
# PostgresMetaStore
async def create_action_type(self, action: ActionType, auto_commit: bool = True) -> ActionType:
    self._session.add(model)
    if auto_commit:
        await self._flush_and_commit()   # 单步操作：自提交
    else:
        await self._session.flush()      # 事务内：只 flush，由 Service 统一提交
    return ActionType.model_validate(model)
```

**何时用 `auto_commit=False`**：当本方法是 Service `transaction()` 单元的一部分时。
**何时用默认 `auto_commit=True`**：单步操作（如独立的 CRUD endpoint），无后续依赖操作。

---

## 三、`transaction()` 上下文管理器

### 3.1 用法

`MetadataOwnerMixin.transaction()` 是 Service 层的事务单元入口，委托给 `PostgresMetaStore.transaction()`：

```python
async with self.transaction():
    # 事务内的所有 metadata 方法传 auto_commit=False
    a = await self._metadata.method_a(..., auto_commit=False)
    b = await self._metadata.method_b(..., auto_commit=False)
# 正常退出 → commit；异常 → rollback
```

### 3.2 实现要点（已踩的坑）

**为什么用 `try/commit/except/rollback` 而非 `session.begin()` 上下文管理器？**

SQLAlchemy 2.0 的 `AsyncSession` 默认 **autobegin**：首次查询（哪怕只读的 `get_ontology`）会隐式开启事务。Service 在进入 `transaction()` 前常会先做只读查询，此时 `session.begin()` 会报 `InvalidRequestError: A transaction is already begun`。

```python
# ✅ 正确实现：兼容 autobegin
@asynccontextmanager
async def transaction(self):
    try:
        yield
        await self._session.commit()        # 正常退出统一提交
    except IntegrityError as exc:
        await self._session.rollback()
        raise ConflictError(...) from exc   # 唯一约束 → HTTP 409
    except Exception:
        await self._session.rollback()
        raise

# ❌ 错误：要求进入时无活动事务，与 autobegin 冲突
@asynccontextmanager
async def transaction(self):
    async with self._session.begin():       # 报 InvalidRequestError
        yield
```

> **规则**：`transaction()` 用 `try/commit/except/rollback`，不要用 `session.begin()`。
> 这与现有 `commit_transaction()` / `rollback_transaction()` 语义一致。

### 3.3 IntegrityError → ConflictError 映射

事务内唯一约束冲突统一包装为 `ConflictError`（HTTP 409），与全局错误处理一致：

```python
except IntegrityError as exc:
    await self._session.rollback()
    raise ConflictError("Resource already exists (unique constraint violation)") from exc
```

---

## 四、`flush()` vs `commit()` 使用决策

| 场景 | 用什么 | 原因 |
|------|--------|------|
| 需要数据库生成的主键继续后续操作 | `flush()` | 拿到主键但不结束事务 |
| 事务内多步操作的中间步 | `flush()`（`auto_commit=False`） | 不提前结束事务 |
| use-case 边界，全部操作完成 | `commit()`（由 `transaction()` 完成） | 持久化 |
| 单步 CRUD endpoint | `_flush_and_commit()`（`auto_commit=True`） | 自包含提交 |
| 仅查询，无写入 | 无需 flush/commit | 读操作不改变事务状态 |

**最常见错误**：在 use-case 中途 `commit()` 只为拿主键。正确做法是 `flush()`——它发送 SQL 并让数据库生成主键，但事务继续：

```python
# ✅ flush 拿主键，事务继续
async with self.transaction():
    order = await self._metadata.create_order(order_def, auto_commit=False)  # flush 后 order.id 可用
    item = await self._metadata.create_order_item(order.id, ..., auto_commit=False)
# commit

# ❌ commit 拿主键，破坏原子性
order = await self._metadata.create_order(order_def)  # commit
item = await self._metadata.create_order_item(order.id, ...)  # 若失败，order 已残留
```

---

## 五、已知坑点与预防（本项目实战）

### 5.1 `MissingGreenlet` —— commit 后 ORM 关系懒加载失败

**现象**：`_flush_and_commit()` 后访问 ORM model 的 relationship 抛 `MissingGreenlet`。

**根因**：commit 关闭事务、session 进入"detached"状态，ORM 的 lazy-load relationship 无法再触发 SQL。

**预防**：创建/更新后**直接构造 pydantic 对象**，不要 `model_validate(orm)` 后再访问关系字段：

```python
# ✅ 直接构造 pydantic，不依赖懒加载
created = await self._metadata.create_action_type(action_type, auto_commit=False)
return created  # created 已是 ActionType（pydantic），不含懒加载关系

# ❌ commit 后 model_validate 触发懒加载
model = await self._session.get(...)
await self._session.commit()
return ActionType.model_validate(model)  # 若 model 有 relationship 且被访问 → MissingGreenlet
```

> 见 CLAUDE.md 通用错误模式 #2。

### 5.2 "best-effort" 吞异常导致故障不可观测

**现象**：版本快照静默丢失，审计/回滚能力失效，无任何日志/告警。

**根因**：`except Exception: pass` 吞掉所有异常（包括"根本没写入"这种静默丢失）。

**预防**："best-effort" 操作（不影响主流程）必须配套可观测性：

```python
# ✅ 记录日志后 raise，让外层事务决定（原子性优先）
try:
    await self._metadata.publish_action_type_version(..., auto_commit=False)
except Exception:
    self._logger.warning("Failed to publish version snapshot",
                         extra={"action_type_id": ..., "version": ...}, exc_info=True)
    raise  # 让 transaction() 回滚整个 use-case

# ❌ 静默吞异常
try:
    await self._metadata.publish_action_type_version(...)
except Exception:
    pass  # 故障完全不可观测
```

**规则**：
- 若 best-effort 操作在事务内 → **不要吞**，raise 让事务回滚（原子性）
- 若 best-effort 操作在事务外（如异步通知）→ `logger.warning(...)` + 不 raise，但必须有日志/指标

### 5.3 低层方法 commit 责任不清

**现象**：`create_action_type` 自带 commit，`publish_action_type_version` 不 commit（docstring 说"caller manages"但无人履约）—— 行为不一致导致 bug。

**预防**：低层方法 commit 行为必须**统一且显式**：

- 统一用 `auto_commit: bool = True` 参数（本项目约定）
- docstring 必须说明 `auto_commit` 语义
- 同类方法行为一致（CRUD 方法都加 `auto_commit`，不要有的加有的不加）

### 5.4 datetime 无法写入 JSON 列

**现象**：`action_type.model_dump()` 含 datetime，写入 `JSON` 列报 `Object of type datetime is not JSON serializable`。

**预防**：写入 JSON 列前用 `model_dump(mode="json")` 让 pydantic 序列化 datetime 为字符串：

```python
snapshot = action_type.model_dump(mode="json")  # ✅ datetime → ISO 字符串
```

---

## 六、Service 层编写清单

新增/修改 Service 的 use-case 方法时，逐项检查：

- [ ] **是否多步写入？** 是 → 用 `async with self.transaction():` 包裹
- [ ] **事务内低层方法是否传 `auto_commit=False`？** 必须传，否则提前 commit 破坏原子性
- [ ] **是否在中途 commit 拿主键？** 改用 `flush()`（`auto_commit=False` 的方法已 flush）
- [ ] **IntegrityError 是否映射为 ConflictError？** 由 `transaction()` 统一处理，service 内不要自己 catch
- [ ] **best-effort 操作是否可观测？** 事务内不吞异常（raise），事务外记日志
- [ ] **返回值是否依赖 ORM 懒加载？** 直接构造 pydantic，不要 `model_validate(orm)` 后访问关系
- [ ] **写入 JSON 列的数据是否可序列化？** 用 `model_dump(mode="json")`
- [ ] **单测是否 mock 了 `transaction()`？** 见下节

---

## 七、测试事务代码

### 7.1 mock `transaction()` 为 noop

Service 单测中，`mock_metadata` 是 `AsyncMock(spec=PostgresMetaStore)`，`transaction()` 需配置为真正的 async context manager，否则 `async with` 失败：

```python
@pytest.fixture
def mock_metadata() -> AsyncMock:
    meta = AsyncMock(spec=PostgresMetaStore)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_transaction():
        yield                          # 单测不触碰真实 session/事务

    meta.transaction = _noop_transaction
    return meta
```

### 7.2 断言 `auto_commit=False`

事务内的低层方法调用必须传 `auto_commit=False`，单测应断言：

```python
mock_metadata.update_action_type.assert_awaited_once_with(
    "hr", "approve", {"description": "new"}, auto_commit=False
)
```

### 7.3 测试原子性（异常回滚）

快照/后续操作失败时，前置操作不应残留（事务回滚）：

```python
async def test_update_snapshot_failure_rolls_back_whole_transaction(self, service, mock_metadata):
    mock_metadata.update_action_type.return_value = updated
    mock_metadata.publish_action_type_version.side_effect = RuntimeError("snapshot boom")

    with pytest.raises(RuntimeError, match="snapshot boom"):
        await service.update_action_type("hr", "approve", {"description": "new"})
    # update 被调过但 auto_commit=False，未真正提交；异常由事务回滚
    mock_metadata.update_action_type.assert_awaited_once()
```

---

## 八、推广计划

事务单元模式（`transaction()` + `auto_commit`）已应用于 `ActionService`。以下 Service 存在多步写入，建议迁移以消除 commit 责任不清：

| Service | 多步操作 | 迁移优先级 |
|---------|---------|-----------|
| `OntologyService.define_object_type_batch` | 对象类型 + 属性 + 关系 + 动作同事务 | 高（已有 batch endpoint，但内部可能各自 commit） |
| `OntologyService.update_object_type_batch` | 删除并重建属性/关系 | 高 |
| `ActionService.execute_action` | object_state + execution_log + outbox | 中（已用 `_flush_and_commit` 手动管理，可统一到 `transaction()`） |
| `IndexSyncService` | Doris 建表 + 索引同步 | 低（跨组件，非纯 PG 事务） |

**迁移步骤**：
1. 低层 metadata 方法加 `auto_commit` 参数（默认 True 保持兼容）
2. Service use-case 用 `async with self.transaction():` 包裹，内部传 `auto_commit=False`
3. 单测 mock `transaction()` + 断言 `auto_commit=False` + 测试异常回滚
4. 跑现有测试确认不回归

---

## 九、参考

- [SQLAlchemy 2.0 Session Transaction 文档](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [SQLAlchemy 2.0 Async ORM 文档](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [FastAPI + SQLAlchemy Best Practices](https://mshaeri.com/blog/fastapi-sqlalchemy-best-practices/)（item 10、12、14）
- [FastAPI dependencies with yield](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/)
- 本项目 bugfix：[`../bugfix/action-type-version-snapshot-not-persisted.md`](../bugfix/action-type-version-snapshot-not-persisted.md)
- 本项目实现：`src/ontology/layers/metadata/postgres_meta_store.py`（`transaction()`）、`src/ontology/services/_metadata_owner.py`（`MetadataOwnerMixin.transaction()`）、`src/ontology/services/action_service.py`（应用示例）
