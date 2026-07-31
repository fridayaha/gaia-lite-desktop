# 待办：数据库连接/事务管理治理 + 点查路径性能优化

**记录时间**: 2026-06-25
**状态**: 🟡 已定位根因，待治理（短期已止血，长期需重构）
**关联**: benchmark read-path `error_rate=1.0` 排查、`docs/engineer/seatunnel-iceberg-rest-interop-postmortem.md`

---

## 背景

跑 benchmark 第 7 步（`04_run_read_benchmark --scale golden`）时，perf 部分出现 `error_rate=1.0`（所有并发请求失败）。排查发现是**两个独立问题叠加**，其中第一个是后端 PG 连接泄漏，第二个是 SeaTunnel worker OOM 导致数据未入库（见关联复盘文档）。本文件聚焦第一个——后端侧的连接/事务管理问题，以及随之暴露的点查性能问题。

---

## 待办 1：数据库连接与事务管理 —— 避免连接泄漏

### 现象

并发压测（并发 10）下，PG 连接池迅速耗尽：

```
SELECT state, count(*) FROM pg_stat_activity WHERE datname='ontology' GROUP BY state;
        state        | count
---------------------+-------
 active              |     1
 idle                |     2
 idle in transaction |    30   ← 连接池 size 20 + overflow 10 全部泄漏
```

后续请求拿不到连接 → 30s 超时 → HTTP 500 → benchmark `error_rate=1.0`。

### 根因

`src/ontology/config/container.py` 的 `metadata` property（已标 DEPRECATED）每次访问都新建一个 `AsyncSession` 且**永不关闭**：

```python
@property
def metadata(self) -> PostgresMetaStore:
    ...
    session = async_session_factory()
    return PostgresMetaStore(session)   # ← session 无 owner，请求结束不 close
```

而多个 service 通过 `metadata=self.metadata` 构造（`ObjectQueryService`、`OntologyService`、`ActionService`、`ActionAuthorizer`、`DataSourceService`、`ConflictDetector`），路由层用 `Depends(get_xxx_service)` 每次请求新建 service → 新建 session → 请求结束 service 被回收但 **session 未 close** → 连接滞留"idle in transaction"。

### 短期止血（已完成）

1. 新增 `src/ontology/services/_metadata_owner.py` 的 `MetadataOwnerMixin`，提供 `aclose()` 关闭持有的 metadata session
2. 6 个请求级 service 继承 mixin：`ObjectQueryService`、`OntologyService`、`ActionService`、`ActionAuthorizer`、`DataSourceService`、`ConflictDetector`
3. 路由层 `get_xxx_service` 改为 **async yield dependency**，请求结束调用 `service.aclose()`：
   - `routes/query/__init__.py`
   - `routes/ontology/__init__.py`
   - `routes/action/__init__.py`
   - `routes/datasource.py`（含全部 handler 改用 `Depends(get_datasource_service)` 注入）
4. `OutboxExecutor`（后台常驻单例）在其 `close()` 里补关 metadata session（不加 mixin，避免请求路径误调）

验证：并发 10 压测后 `idle in transaction` 从 30 降到 1，`error_rate` 从 1.0 降到 0.0。

### 长期治理（待做）

短期方案是"每个请求级 service 各自关自己的 session"，治标不治本。根本问题是 **session 生命周期管理分散在 service 层**，违背"谁创建谁负责"原则。待做：

- [ ] **彻底废弃 `container.metadata` property**：当前仅靠 DEPRECATED 警告 + 注释约束，没有强制力。应改为只在 `metadata_session()` 上下文管理器内可用，或直接删除 property 让编译期失败。
- [ ] **统一 session 生命周期模型**：明确区分两类 session：
  - 请求级（request-scoped）：路由依赖注入，请求结束关闭
  - 后台级（background）：`OutboxExecutor`、`ConflictDetector` 等常驻任务，lifespan 启动时创建、关闭时释放
  当前两者混用同一个 `container.metadata`，靠注释区分，易错。
- [ ] **service 不再构造时注入 metadata，改为按需获取**：理想形态是 service 内部 `async with container.metadata_session() as meta: ...`，从根上消除"持有 session 但不负责关闭"的可能。但这要改所有 service 的 `self._metadata.xxx` 调用点，工作量大，需排期。
- [ ] **事务边界明确化**：当前 read-your-writes 的 `object_state` 查询隐式开了事务（asyncpg autocommit off），导致连接"idle in transaction"。只读查询应显式 `begin()`/`commit()` 或用 read-only session，避免长事务占连接。
- [ ] **加连接池监控告警**：暴露 `pool.checkedout` / `pool.overflow` 到 Prometheus，连接池接近耗尽时告警，而不是等 30s 超时。
- [ ] **回归测试**：写一个并发压测单测（并发 N 次 `/objects/load`），断言结束后 `pg_stat_activity` 无 idle-in-transaction 残留，防止回退。

---

## 待办 2：点查路径性能优化 —— 跳过无谓的 PG read-your-writes 查询

### 现象

`read_l1_001`（按主键查历史航班 flight 1024）单次延迟 ~130ms，并发 10 时 p95 ~1000ms、qps 仅 1.8。

### 根因

`ObjectQueryService._load_physical` 对**所有**查询无差别先查 PG `object_state`（read-your-writes 一致性保证）：

```python
ryw = await self._read_your_writes(object_type, request)   # ← 每次必查 PG
if ryw is not None:
    return ryw
# ... fall through to Doris/Iceberg
```

`object_state` 只存"被 Action 改过的对象"。benchmark 的纯历史数据（flight 1024 从无 Action 写入）100% 查空，这次 PG 往返是纯开销。点查路径实际为：**PG 空查 → Trino Iceberg 点查**，两次串行 IO。

### 为什么现在不能直接删

read-your-writes 是 `action-architecture.md` 的核心一致性保证：Action 写 PG `object_state` → CDC 异步同步 Iceberg（秒级延迟）。若查询跳过 PG，用户刚执行的 Action 改动在 CDC 同步前不可见，破坏读写一致性。

### 待做的优化方向

- [ ] **有条件跳过 PG**：仅当 object_state 里确实可能有该对象时才查。可选策略：
  - 维护"最近 N 分钟被 Action 改过的 object_type / rid"缓存（如 Redis-like 内存 LRU），查询前先判断
  - 或查 PG 前先看 object_state 表是否有该 object_type 的记录（轻量 exists 查询）
- [ ] **点查的版本比较优化**：先查 Iceberg 拿到 version，仅当 object_state 有更新版本时才合并 PG 结果（需要 object_state 与 Iceberg 行都带 version 字段，目前已有）
- [ ] **read-your-writes 只对"写后立即读"窗口生效**：Action 执行后短时间内（如 5s）才走 PG，超时后信任 Iceberg 已同步。需要一个时间窗判断。
- [ ] **benchmark 场景显式跳过**：benchmark 是纯历史数据读取，可加请求 header（如 `X-Skip-Read-Your-Writes`）或配置项，让 benchmark 跑出"纯查询引擎"性能基线，与"含一致性开销"的生产路径分开计量。
- [ ] **性能基线对比**：优化前后用 benchmark 量化 PG 空查的占比（p95 降低多少、qps 提升多少），用数据驱动决策。

---

## 关联代码索引

| 位置 | 内容 |
|------|------|
| `src/ontology/config/container.py` | `metadata` property（DEPRECATED，泄漏源）、`metadata_session()`（正确用法） |
| `src/ontology/services/_metadata_owner.py` | `MetadataOwnerMixin`（短期止血） |
| `src/ontology/services/object_query_service.py` | `_load_physical` / `_read_your_writes`（点查路径 + read-your-writes） |
| `src/ontology/layers/metadata/postgres_meta_store.py` | `PostgresMetaStore.close()` |
| `src/ontology/routes/{query,ontology,action,datasource}.py` | yield dependency 清理 |
| `docs/engineer/seatunnel-iceberg-rest-interop-postmortem.md` | 关联的数据层根因复盘 |

---

## 教训

1. **DEPRECATED 不等于已修复**：`container.metadata` 标了 DEPRECATED 但仍在用，泄漏持续发生。废弃 API 要有强制迁移路径（删除或编译期失败），不能只靠注释。
2. **连接池耗尽的表象会误导根因定位**：`error_rate=1.0` + 500 看起来像"数据层/查询引擎问题"，实际是后端连接管理。排查时先看 `pg_stat_activity` 和后端日志的 `QueuePool` 警告，再看数据层。
3. **正确性设计（read-your-writes）不能无差别施加于所有路径**：一致性保证要区分"需要一致的查询"和"纯历史读"，否则正确性变成性能税。
