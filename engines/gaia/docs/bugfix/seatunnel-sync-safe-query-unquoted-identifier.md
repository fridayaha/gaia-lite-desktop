# 同步任务 safe_query 未给标识符加引号 → PG camelCase 列名同步失败

> **状态**：已修复（2026-07-24，方案 2）
> **影响**：源表列名含大写字母（camelCase / 大小写敏感）时，"同步此表"创建的托管数据集任务无法提交（SeaTunnel API-06，500）
> **根因**：`DataSourceService._build_safe_query` 拼 `SELECT` 时未对列名/表名加引号，PG 把未加引号的标识符折叠成小写，找不到原列
> **关联**：本 bug 是 [`seatunnel-pg-cdc-timestamptz-blocker.md`](./seatunnel-pg-cdc-timestamptz-blocker.md) 修复 3 引入的 `_build_safe_query` 的实现缺陷

---

## 一、现象

`xiaoling` 数据源（PostgreSQL，44 张表，列名全部 camelCase：`opId` / `aclnnApi` / `displayName` ...）执行"同步此表"流程：

```
POST /api/sync-tasks/xiaolingSyncCannOp/start
→ 500 Internal Server Error
→ OntologyError: Failed to submit SeaTunnel job sync_cann_op_raw
   : Server error '500' for url '.../hazelcast/rest/maps/submit-job?jobName=sync_cann_op_raw'
```

API 日志表面是 SeaTunnel API-06，但 SeaTunnel master 日志的真实根因链：

```
FactoryException:[API-06] Unable to create a source for identifier 'Jdbc'
Caused by: org.postgresql.util.PSQLException:
  ERROR: column "opid" does not exist
  Hint: Perhaps you meant to reference the column "cann_op.opId".
  Position: 8
  at PgPreparedStatement.getMetaData(PgPreparedStatement.java:1149)
  at CatalogUtils.getCatalogTable(CatalogUtils.java:349)
  at PostgresCatalog.getTable(PostgresCatalog.java:239)
```

**44 张表全部失败**，无一例外。

## 二、根因

### 2.1 直接原因：`_build_safe_query` 拼 SQL 漏了引号

`src/ontology/services/datasource_service.py:_build_safe_query` 用 `information_schema.columns` 探出列名（大小写正确，如 `opId`），拼 SELECT 时**列名和表名都没加双引号**：

```python
for row in rows:
    col = row["column_name"]              # "opId" — 大小写正确
    dtype = row["data_type"]
    if dtype in ("timestamp with time zone", "timestamp without time zone"):
        parts.append(f"{col}::text AS {col}")   # ← 未加引号
    else:
        parts.append(f"{col}")                   # ← 未加引号
return f"SELECT {', '.join(parts)} FROM {table}"  # ← table 也未加引号
```

生成的 SQL（PG 日志实测抓到，铁证）：

```sql
SELECT opId, aclnnApi, category, confidence, displayName, ... FROM cann_op
```

### 2.2 PG 标识符折叠规则（根因机制）

PostgreSQL 规定：**未加双引号的标识符折叠成小写**。`opId` → `opid`，而实际列是 `"opId"`（带引号创建的大小写敏感列），于是：

```
ERROR: column "opid" does not exist
HINT: Perhaps you meant to reference the column "cann_op.opId".
```

本地复现：

```sql
-- 加引号：成功
SELECT "opId", "aclnnApi" FROM cann_op LIMIT 1;  -- ✅
-- 不加引号：报和 SeaTunnel 一模一样的错
SELECT opId, aclnnApi FROM cann_op LIMIT 1;       -- ❌ column "opid" does not exist
```

### 2.3 调用链（反编译 + PG 日志双重证实）

```
POST /sync-tasks/.../start
  → _submit_sync_pipeline (datasource_service.py:745)
    → _build_safe_query(ds, "cann_op")           ← ★ bug 在此，生成 SELECT opId, ... FROM cann_op（无引号）
    → source_config_full["query"] = 上面那条 SQL
    → SeaTunnelEngine._build_sync_pipeline
      → HOCON: query = "{{ source.query }}"       ← 原样透传
      → SeaTunnel submit-job
        → JdbcCatalogUtils.getCatalogTable(conn, querySql, dialect)
          → prepareStatement("SELECT opId, ... FROM cann_op").getMetaData()
            → PG JDBC 原样发给 PG
              → PG 折叠 opId→opid → column "opid" does not exist
```

关键：**SeaTunnel 对用户传入的 `query` 是原样执行**，不会改写/加引号（反编译 `CatalogUtils.getCatalogTable` 证实：`prepareStatement(querySql).getMetaData()`，querySql 原样透传）。

### 2.4 为什么是自己生成 query，不是 SeaTunnel 生成

`_build_safe_query` 的存在是为了绕 SeaTunnel 2.3.13 的另一个 bug（见 [`seatunnel-pg-cdc-timestamptz-blocker.md`](./seatunnel-pg-cdc-timestamptz-blocker.md)）：PG `timestamptz` 被映射成内部 STRING，Iceberg sink 用 Jackson 序列化 `OffsetDateTime` 缺 JSJ310 模块崩。Workaround 是对 timestamp 列加 `::text` 强制转换——而 SeaTunnel 的 `query` 配置只接受整条 SQL 字符串，没有"只声明某列 cast"的声明式配置，**只能整条 SQL 自己拼**。

副作用：一旦自己拼 SQL，就绕开了 SeaTunnel 内部 `quoteIdentifier` 的保护，camelCase 列名裸露在外 → 折叠小写 → 报错。

**这个 workaround 本身合理，bug 只在"拼 SQL 漏引号"。** 等 SeaTunnel 发版含 PR #10048（timestamptz 读取支持，post-2.3.13）后，可整体删掉 `_build_safe_query`，HOCON 回归 `SELECT *` 由 SeaTunnel `quoteIdentifier` 兜底。

## 三、为什么不是 SeaTunnel 的 bug（已排除）

反编译 `connector-jdbc-2.3.13.jar` 关键类逐一核实：

| 类 / 方法 | 行为 | 是否加引号 |
|---|---|---|
| `PostgresCatalog.getSelectColumnsSql` | 查 `pg_attribute`，`a.attname` 原样读列名 | ✅ 不折叠 |
| `CatalogUtils.getCatalogTable(conn, querySql, ...)` | `prepareStatement(querySql).getMetaData()` | — querySql 原样透传，不展开 `*`、不加引号 |
| `JdbcDialectTypeMapper.mappingColumn`（默认） | `DatabaseMetaData.getColumns()` | ✅ 标准 JDBC 元数据，保留大小写 |
| `PostgresDialect.quoteIdentifier` | 给标识符加双引号 | ✅ 正确 |
| `JdbcDialect.tableIdentifier(TablePath)`（默认） | `TablePath.getFullName()`，**不加引号** | 由子类重写 |
| `PostgresDialect.tableIdentifier` | `getFullNameWithQuoted("\"")` | ✅ 正确 |

SeaTunnel **自己生成**的 SQL（catalog probe / INSERT / UPSERT / CDC 分片扫描）都经 `quoteIdentifier` 正确加引号。**只有用户传入的 `query` 字符串**原样执行——这是合理设计（用户 SQL 不应被改写），所以问题在 Gaia 侧。

## 四、社区同源修复佐证

社区 issue **#6130**（PG2PG 字段大写/数字开头报错）和修复 PR **#6669**（2.3.5）/ **#6951**（2.3.6）的修法**正是"给标识符加引号"**：

```diff
- sql.append(fieldNamesIt.next()).append(predicate);
+ sql.append(jdbcDialect.quoteIdentifier(fieldNamesIt.next())).append(predicate);
```

新增测试用例直接证明社区生成的 SQL 形态（Postgres 用双引号）：

```java
Assertions.assertEquals(
  "SELECT * FROM \"schema1\".\"table1\" WHERE \"id\" >= ? AND NOT (\"id\" = ?) AND \"id\" <= ?",
  splitScanSQL);
```

各方言引号字符不同，按 dialect 走 `quoteIdentifier`：MySQL→反引号、SQL Server→方括号、PG/Oracle→双引号。**我们 2.3.13 已含此修复**，所以 SeaTunnel 内部 SQL 没问题；剩下没加引号的是 Gaia 自己的 `_build_safe_query`。

## 五、修复方案（方案 2：删除 workaround，回归 `SELECT *`）

### 5.0 方案选型依据（live 实测）

在动手前实测验证了"不绕 `_build_safe_query`、直接用 `SELECT *`"是否可行：

| 源表列类型 | `SELECT *`（无 cast）SeaTunnel 2.3.13 表现 | 实测方式 |
|---|---|---|
| 普通类型（text/bigint/boolean/double/name/int/varchar） | ✅ 完全正常 | `SELECT * FROM cann_op`（camelCase 列名）提交 SeaTunnel job FINISHED，628 行写 Iceberg |
| `timestamp`（NTZ 无时区） | ✅ 完全正常，正确写 Iceberg | 造 `ntz_test` 表（2 个 NTZ 列）端到端写 Iceberg，Trino 验证 3 行数据正确 |
| `timestamptz`（有时区） | ❌ Jackson 序列化 OffsetDateTime 崩 | 造 `tstz_test` 表（2 个 timestamptz 列）端到端测，job FAILED，异常栈 `InvalidDefinitionException: Java 8 date/time type java.time.OffsetDateTime not supported` |
| `jsonb` / `json` | ✅ 正常，以字符串写 Iceberg | 单类型表 `tt_jsonb`/`tt_json`，FINISHED，Trino 验证 `{"k":"v1"}` |
| `uuid` | ✅ 正常 | 单类型表 `tt_uuid`，FINISHED，Trino 验证 `a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11` |
| `inet` | ✅ 正常 | 单类型表 `tt_inet`，FINISHED，Trino 验证 `192.168.1.1` |
| `text[]`（数组） | ✅ 正常 | 单类型表 `tt_array`，FINISHED，Trino 验证 `[a, b]` |
| `bytea` | ✅ 正常 | 单类型表 `tt_bytea`，FINISHED，Trino 验证 `01 02 03` |
| `interval` | ✅ 正常 | 单类型表 `tt_interval`，FINISHED，Trino 验证 `1 day` |

**关键洞察**：`SELECT *` 由 PG 内部用真实列名展开，不存在未加引号标识符被折叠的问题（PG 语义层面保证）。而 SeaTunnel 2.3.13 对 NTZ `timestamp` 及 jsonb/uuid/inet/array/bytea/interval 等 PG 特有类型都能正常读→写 Iceberg——因为这些类型经 PG JDBC ResultSet 读出后被当作字符串处理，不经过 Gravitino 类型层（区别于联邦查询路径，见 [`gravitino-external-type-blocks-datasource-preview.md`](./gravitino-external-type-blocks-datasource-preview.md)）。**唯一会崩的是 `timestamptz`**（Jackson 缺 jsr310 模块，OffsetDateTime 序列化失败）。

xiaoling 全部 44 表没有任何 timestamp 列（只有 text/bigint/boolean/double/name/int/varchar），且用户可控制数据源使用 NTZ `timestamp` 而非 `timestamptz`——因此**完全不需要 `_build_safe_query`**，直接删掉它、HOCON 回归 `SELECT *` 即可。

### 5.1 删除 `_build_safe_query` 方法

`src/ontology/services/datasource_service.py`：删除整个 `_build_safe_query` 方法（含 asyncpg 探 schema 逻辑）。

### 5.2 改调用处

`_submit_sync_pipeline` 中：
- `safe_query = None`（full_snapshot 模式，让 HOCON 模板 `source.query | default('SELECT * FROM ' ~ source.table, true)` 自动回退）
- incremental 模式：`base_sql = f"SELECT * FROM {source_table}"`，再经 `_ingestion_filter.rewrite_incremental_query` 追加 `WHERE (gaia_sync_tx IS NULL OR gaia_sync_tx != '...')` 反馈环过滤

### 5.3 约束：数据源类型支持矩阵（2026-07-24 完整实测）

方案 2 删除 `_build_safe_query` 后，源表类型支持情况如下（均为当前实现 `SELECT *` → SeaTunnel JDBC source → Iceberg sink 路径的一手实测）：

| PG 类型 | 同步路径 | 备注 |
|---|---|---|
| text / bigint / boolean / double / integer / varchar / name | ✅ | 基础类型，无限制 |
| `timestamp`（NTZ 无时区） | ✅ | Iceberg 存为 timestamp，Trino 查询带 UTC 后缀呈现 |
| `jsonb` / `json` | ✅ | 以字符串写 Iceberg，值完整（如 `{"k":"v1"}`） |
| `uuid` | ✅ | 以字符串写 Iceberg（如 `a0eebc99-...`） |
| `inet` | ✅ | 以字符串写 Iceberg（如 `192.168.1.1`） |
| `text[]` 等数组类型 | ✅ | 以字符串写 Iceberg（如 `[a, b]`） |
| `bytea` | ✅ | 以字符串写 Iceberg（如 `01 02 03`） |
| `interval` | ✅ | 以字符串写 Iceberg（如 `1 day`） |
| **`timestamptz`（有时区）** | **❌** | **唯一不支持**：SeaTunnel 2.3.13 Iceberg sink 用 Jackson 序列化 `OffsetDateTime` 缺 jsr310 模块，job FAILED |

**约束**：数据源表的 timestamp 列必须使用 `timestamp`（NTZ 无时区），不能用 `timestamptz`（有时区）。这是方案 2 的唯一前提，由数据源侧保证。等 SeaTunnel 发版含 PR #10048 后此约束解除。

**重要区分**：jsonb/uuid/inet 在**联邦查询路径**（Trino → Gravitino Connector → 外部 PG）会崩（`external(jsonb)` 整表拒绝，见 [`gravitino-external-type-blocks-datasource-preview.md`](./gravitino-external-type-blocks-datasource-preview.md)），但在**同步路径**（SeaTunnel JDBC → Iceberg）完全正常——两者是不同链路，不要混淆。

## 六、live 验证（2026-07-24，方案 2 实施后）

| 验证项 | 结果 |
|---|---|
| `POST /sync-tasks/xiaolingSyncCannOp/start`（API 热重载后） | ✅ 200，任务状态 RUNNING，`pipeline_name=sync_cann_op_raw` |
| SeaTunnel job `sync_cann_op_raw` 执行 | ✅ FINISHED，无 errorMessage |
| Iceberg 表 `cann_op_raw` 有数据 | ✅ Trino `SELECT count(*)` = 628 行 |
| camelCase 列名数据正确 | ✅ `opId`/`aclnnApi`/`displayName` 值正确（如 `ops-transformer:attention:attention_update`） |
| 无 `::text` cast | ✅ 源表无 timestamp 列，`SELECT *` 原生处理 |
| NTZ timestamp 列端到端（前置实测） | ✅ `ntz_test` 表（2 NTZ 列）写 Iceberg，Trino 验证 3 行值正确 |

## 七、后续

- **批量同步剩余 43 张表**：xiaoling 44 表中除 `cann_op`（已验证）外的 43 表，用 `scripts/batch_sync_xiaoling.py` 批量创建同步任务 + 数据集并启动。
- **SeaTunnel 升级含 PR #10048 后**：timestamptz 约束解除，支持源表使用 `timestamptz` 类型（届时 SeaTunnel 原生处理，无需任何 workaround）。
- **schema 过滤**：当前 incremental 模式 `base_sql = SELECT * FROM {source_table}` 假设表在默认 schema，未来支持非默认 schema 时需处理表名限定。
- **单元测试**：已补 2 个测试覆盖新行为——`test_submit_sync_pipeline_full_snapshot_query_is_none`（full_snapshot query=None）、`test_submit_sync_pipeline_incremental_query_filters_feedback_loop`（incremental 带 gaia_sync_tx 过滤、无 `::text`）。删掉 3 个旧 `_build_safe_query` 测试。

## 八、相关

- 上游 issue（同类问题）：https://github.com/apache/seatunnel/issues/6130
- 上游修复 PR（加引号范式源头）：https://github.com/apache/seatunnel/pull/6669 / https://github.com/apache/seatunnel/pull/6951
- 上游 timestamptz 读取修复（删 workaround 前提）：https://github.com/apache/seatunnel/pull/10048
- 关联 bugfix：[`seatunnel-pg-cdc-timestamptz-blocker.md`](./seatunnel-pg-cdc-timestamptz-blocker.md)（`_build_safe_query` 的引入来源）
- 代码位置：`src/ontology/services/datasource_service.py:_build_safe_query`（line 783）、`src/ontology/layers/pipeline/sea_tunnel_engine.py` HOCON 模板（line 63 `query`）
- 数据源：`xiaoling`（PG，44 表 camelCase 列名，本地 k3s `gaia-postgres:5432/xiaoling`）
