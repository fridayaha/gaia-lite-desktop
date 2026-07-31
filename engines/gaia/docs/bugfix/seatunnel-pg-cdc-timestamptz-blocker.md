# SeaTunnel PG-CDC TIMESTAMPTZ 阻塞 + 列类型修复

> **状态**：已修复（2026-07-06）
> **影响**：Action 闭环的 PG→Iceberg / PG→Kafka CDC pipeline 无法启动
> **根因**：SeaTunnel 2.3.13 Postgres-CDC 不支持 `TIMESTAMP WITH TIME ZONE` 列 + Gaia ORM 全表用 `DateTime(timezone=True)`

---

## 一、现象

`scripts/verify_action_cdc_live.py` 提交 PG→Iceberg / PG→Kafka CDC pipeline 时，SeaTunnel 返回：

```
ErrorCode:[API-06], ErrorDescription:[Factory initialize failed]
  - Unable to create a source for identifier 'Postgres-CDC'.
```

API-06 是 surface message。从 SeaTunnel master 日志的 Caused-by 链提取真实根因：

```
java.lang.UnsupportedOperationException: Unsupported type: TIMESTAMP_TZ
  at ...SeaTunnelRowDebeziumDeserializationConverters#createNotNullConverter
  at PostgresIncrementalSource.createDebeziumDeserializationSchema
  at PostgresDialect.discoverDataCollections
```

## 二、根因

**两个独立问题叠加**：

### 问题 1：CDC 模板字段名错误（遗留代码 bug）

三个内置 CDC 模板（`PIPELINE_CDC_TEMPLATE` / `PIPELINE_PG_TO_KAFKA_TEMPLATE` / `PIPELINE_DUAL_TEMPLATE`）的字段名基于 MySQL-CDC spike（ADR-014，只 live 验证过 MySQL）的错误推断，对 Postgres-CDC 全错：

| 项 | 旧（错） | 2.3.13 官方文档（对） |
|----|---------|---------------------|
| source 标识符 | `PostgreSQL-CDC` | `Postgres-CDC`（`PostgreSQL-CDC` 报 API-06 factory-not-found；官方页标题叫 "PostgreSQL CDC" 但 HOCON block + plugin-mapping.properties 用 `Postgres-CDC`） |
| JDBC URL 字段 | `base-url`（MySQL-CDC 的） | `url` |
| 解码插件字段 | `plugin.name` | `decoding.plugin.name` |
| 表指定 | `table = "..."`（单数） | `database-names` + `schema-names` + `table-names`（3 段式 `db.schema.table`） |
| transform 接线 | `source_table_name`/`result_table_name`（已废弃） | `plugin_input`/`plugin_output` |
| DELETE 过滤 | `WHERE op != 'd'`（Debezium 风格，default format 无此字段） | `FilterRowKind { exclude_kinds = [...] }`（CDC default format 用 RowKind +I/-U/+U/-D） |
| Kafka sink key | `topic.key` | `assign_key` |
| Doris sink format | `doris.sink.properties` | `doris.config`（2.3.13 required） |
| SeaTunnel 访问 PG/Kafka | `pg_host`/`kafka_bootstrap_servers`（API-host 的 localhost，SeaTunnel 容器不可达） | `seatunnel_pg_host`/`seatunnel_kafka_bootstrap_servers`（compose 服务名） |

### 问题 2：TIMESTAMPTZ 上游不支持（SeaTunnel #11005）

SeaTunnel 2.3.13 的 Postgres-CDC 在 source 初始化的 schema 发现阶段（`PostgresDialect.discoverDataCollections` → `createNotNullConverter`）为每列建 converter，遇到 `TIMESTAMP WITH TIME ZONE`（TIMESTAMPTZ）直接抛 `UnsupportedOperationException`。

- PG-CDC 的 TIMESTAMP_TZ **读取**支持（PR #10048）在 2.3.13 刚加入，但 Debezium row converter 的对应分支（PR #11069）**未合并**（截至 2026-07-06 仍 open）。
- `debezium.column.exclude.list` 透传**无法绕过**——schema 发现阶段用 JDBC 直接读表完整 schema，在 Debezium 属性生效前就抛错。

Gaia 的**全部元数据/操作态表**的 `created_at`/`updated_at`/`deleted_at`/`next_retry_at`/`last_run_at` 列都是 timestamptz（ORM 统一用 `DateTime(timezone=True)`）。这些是 **Gaia 自己的表**，不是用户数据源表（用户表走 external CDC 模板，schema 各异，是用户自己的事）。

## 三、修复

### 修复 1：CDC 模板字段名（`src/ontology/layers/pipeline/sea_tunnel_engine.py`）

按 SeaTunnel 2.3.13 官方文档（https://seatunnel.apache.org/docs/2.3.13/connectors/source/PostgreSQL-CDC/ + Transform Common Options + Kafka/Doris sink）重写三个模板：

- source：`Postgres-CDC` + `url` + `decoding.plugin.name` + `database-names`/`schema-names`/`table-names` + `plugin_output`
- transform：`FilterRowKind`（`exclude_kinds = ["UPDATE_BEFORE", "DELETE"]`，只保留 INSERT + UPDATE_AFTER = 最新状态）+ `Sql`（`plugin_input`/`plugin_output`，扁平列引用计算动态 topic）
- Kafka sink：`plugin_input` + `topic = "${dynamic_topic}"` + `assign_key`
- Iceberg sink：`catalog-impl` + catalog.config 不带 `warehouse` + `primary-keys` + `upsert-mode-enabled`（避 #10747）
- Doris sink（Kafka→Doris）：`doris.config { format/read_json_by_line }`
- 各 pipeline 独立 replication slot（避 PG 拒绝重复 slot）
- SeaTunnel-facing host：`seatunnel_pg_host` / `seatunnel_kafka_bootstrap_servers` / `seatunnel_doris_host`（compose 服务名，非 localhost）

同时修复 `datasource_service.py` Kafka→TimescaleDB sink 的同类 host bug（`kafka_bootstrap_servers` → `seatunnel_kafka_bootstrap_servers`）。

### 修复 2：timestamptz → timestamp（Alembic migration `0e2239a90155`）

把 Gaia 全部 ORM 表的 timestamptz 列改成 `timestamp`（无时区）：

- ORM：`src/ontology/core/models/ontology.py` + `datasource.py`，`DateTime(timezone=True)` → `DateTime`（46 列）
- Migration：`alembic/versions/20260706_0752_0e2239a90155_drop_timezone_from_timestamp_columns_.py`，92 个 `ALTER COLUMN ... TYPE TIMESTAMP WITHOUT TIME ZONE`

**为什么这样改可行**：
- 这些列是审计时间戳（`created_at`/`updated_at` 等），应用层统一用 UTC 写入，下游 Iceberg/Doris 查询索引不需要时区信息。
- 改成 `timestamp` 后值仍是 UTC 时刻，读取时当 UTC 解释即可，无业务损失。
- 应用层无代码依赖 `tzinfo`（grep 确认无 `.tzinfo`/`astimezone` 调用）。

**为什么全表统一改**：避免 schema 风格分裂（部分表 timestamptz 部分 timestamp），且这些表都是 Gaia 自有元数据表，统一处理风险可控。

**等 SeaTunnel #11069 发版后无需回退**：`timestamp`（无时区）对审计字段足够，且应用层已统一 UTC 写入，保留这个改动反而更简单一致。

### 修复 3：utcnow() 返回 naive UTC datetime（连带修复）

migration 把列改成 naive timestamp 后，发现一个连带问题：应用层 `datetime.now(UTC)` 返回 **aware** datetime（带 tzinfo），asyncpg 写入/查询 naive-timestamp 列时报 `can't subtract offset-naive and offset-aware datetimes`（最先在 OutboxExecutor 后台任务的 `WHERE next_retry_at <= $2` 查询暴露，返回 HTTP 500）。

修复：
- `src/ontology/core/models/defaults.py` 的 `utcnow()` 改为返回 naive UTC：`datetime.now(UTC).replace(tzinfo=None)`
- 把 metadata/services/routes 里所有写 DB / 查询参数的 `datetime.now(UTC)` 改为 `utcnow()`（naive），共 8 个文件 24 处
- `ActionContext.current_timestamp` 保持 aware（不写 DB，只给规则引擎 `.isoformat()` 用，输出带时区字符串）
- `ai_agent.py` 的 `today_bj` 保持 aware（逻辑用，不写 DB）
- 测试 `test_outbox_executor.py` 的 `datetime.now(UTC)` 改为 `utcnow()`（与实现对齐）

这样所有 DB 时间戳列的写入/查询参数统一是 naive UTC，与 `timestamp` 列类型一致，消除 aware/naive 混用错误。

## 四、live 验证（2026-07-06，完整 dev 集群）

| 链路 | 验证结果 |
|------|---------|
| PG wal_level=logical + pgoutput slot | ✅ |
| object_state → Iceberg（snapshot 1282 行 + INSERT/UPDATE 增量 upsert） | ✅ |
| object_state → Kafka（snapshot + per-type 动态 topic 路由，消息 JSON 正确） | ✅ |
| **端到端**：POST /actions/execute → object_state → Kafka topic 出现含 Action 数据的消息 | ✅ |
| action_execution_logs → Iceberg（submit + RUNNING） | ✅ |
| OutboxExecutor 后台任务（naive/aware 修复后无 500） | ✅ |
| TIMESTAMPTZ-free 表模板正确性 probe（隔离证明，修复前已验证） | ✅ |

## 五、依赖

- **SeaTunnel 2.3.13**（connector-cdc-postgres 2.3.13 jar，PG JDBC driver 在 `/opt/seatunnel/lib/`）
- **PostgreSQL `wal_level = logical`**（`config/postgres/postgresql.conf`）
- **`REPLICA IDENTITY FULL`**（CDC pipeline 要求，object_state/action_execution_logs 已设）
- **SeaTunnel-facing host**：`seatunnel_pg_host=ontology-postgres` / `seatunnel_kafka_bootstrap_servers=kafka:9092` / `seatunnel_doris_host=ontology-doris-fe`（compose 服务名）
- **Alembic migration `0e2239a90155`**（timestamptz → timestamp，本地 DB + 云 DB 同步执行）

## 六、后续

- **~~Kafka→Doris 动态表名路由~~**（已废弃）：路径 B（PG→Kafka→Doris）与 Iceberg→Doris STREAMING 模板均于 2026-07 去 SeaTunnel 化删除。object_state 同步改 outbox INDEX effect → OutboxExecutor ≤1s → DorisIndexStore.upsert；外部接入数据改 ObjectIndexFunnel。本文档的 timestamptz blocker 教训（migration + 用户表 schema 提示）仍有效。
- **SeaTunnel #11069 发版后**：可选择性回退 migration（把 timestamp 改回 timestamptz），但**不推荐**——当前 timestamp 方案更简单一致，回退无收益。
- **用户数据源表的 timestamptz**：external CDC 模板（ADR-014）已按 connector 分支渲染（PG 分支用 `url`/`decoding.plugin.name`），用户表若有 timestamptz 列会遇到同样的 #11005，属于用户侧 schema 责任，文档提示即可。

## 七、相关

- 上游 issue：https://github.com/apache/seatunnel/issues/11005
- 上游修复 PR（未合并）：https://github.com/apache/seatunnel/pull/11069
- 官方 PG-CDC 文档：https://seatunnel.apache.org/docs/2.3.13/connectors/source/PostgreSQL-CDC/
- 官方 Transform Common Options（plugin_input/plugin_output）：https://seatunnel.apache.org/docs/2.3.13/transforms/common-options/
- Migration：`alembic/versions/20260706_0752_0e2239a90155_drop_timezone_from_timestamp_columns_.py`
- Live 验证脚本：`scripts/verify_action_cdc_live.py`
- 相关 ADR：ADR-008（Iceberg→Doris 同步）、ADR-014（多源 CDC connector）
