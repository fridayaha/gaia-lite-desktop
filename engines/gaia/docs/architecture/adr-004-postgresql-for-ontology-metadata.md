# ADR-004: 使用 PostgreSQL 存储业务本体元数据

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳 |
| **决策日期** | 2026-05（架构 v5 终稿） |
| **影响层** | `layers/metadata/PostgresMetaStore`、`core/models/ontology.py`（ORM）、`alembic/`（迁移） |
| **相关 ICD** | ICD-01 PostgresMetaStore |
| **关联文档** | `architecture_plan.md` §2.1 组件职责矩阵、ADR-005（properties 用 JSONB）、`docs/architecture/implementation-status.md` |

---

## 背景

Metadata 层负责存储全部业务本体元数据：Ontology、ObjectType、PropertyDef、LinkTypeDef、ActionType、InterfaceType、ValueType、Struct、ObjectTypeGroup、Branch，以及运行态的 object_state（Action 操作态）、outbox（副作用队列）、action_execution_logs（审计）、datasets（治理记录）。

这些数据的特点：
- **强事务性**：本体定义的创建/修改必须原子完成（如 ObjectType 连同其 properties 一起创建），Action 执行需要 OCC 乐观锁 + 原子提交
- **强一致性**：元数据是查询路由、权限校验的基础，不能容忍最终一致
- **复杂查询**：按 api_name 查、按 ontology 列表、按 interface 反查 object_type、按 effect_type 聚合 outbox
- **JSONB 灵活字段**：parameters / rules / constraints / properties 等半结构化数据
- **与 Iceberg REST Catalog 共享一个 PG 实例**（不同 schema：业务表在 public，Iceberg 元数据在 iceberg_tables，Gravitino 元数据在 gravitino_store）

候选方案：

| 方案 | 定位 |
| ---- | ---- |
| PostgreSQL | 成熟关系型数据库，JSONB + 事务 + 丰富索引 |
| MySQL | 最流行的开源关系型数据库 |
| etcd | 分布式 KV 存储 |

## 决策

**采用 PostgreSQL 16 存储业务本体元数据**（并复用同一实例承载 Iceberg REST Catalog 的 jdbc backend 和 Gravitino entity store）。

### 1. 业务需求契合度

- **强事务 + ACID**：PG 的事务语义完整，支持 ObjectType + properties 原子创建、Action OCC 乐观锁（`WHERE version = :expected`）、outbox 原子写入
- **JSONB 原生支持**：`parameters`/`rules`/`constraints`/`properties` 等 JSONB 列可直接建 GIN 索引、做 `@>`/`?` 查询，无需额外文档数据库（见 ADR-005）
- **复杂查询能力**：CTE、窗口函数、`DISTINCT ON`（解决 `get_object_type` 多结果隐患，见 CLAUDE.md 错误模式 #6）、丰富的类型系统
- **与 Iceberg/Gravitino 共享实例**：Iceberg REST Catalog 的 jdbc backend 和 Gravitino entity store 本就需要 PG，业务元数据复用同一实例（不同 schema），减少组件数

### 2. 成熟生态与 Python 异步支持

- **SQLAlchemy 2.0 async ORM** 原生支持 PG（`asyncpg` 驱动），与项目编码规范红线 #1 一致
- **Alembic** 迁移工具成熟，业务表 schema 单一真相源
- **PostGIS / TimescaleDB 扩展**：同一 PG 实例升级为 `ngosang/timescaledb-postgis` 一体镜像后，额外承载 GeoTime 层的时空分析（ADR-015），无需引入新数据库

### 3. 与 MySQL 的区分

Doris FE 已使用 MySQL 协议（9030 端口）。若 Metadata 层也用 MySQL，会在运维时造成端口/协议混淆（"连的是哪个 MySQL？"）。PG 用独立协议（5432），与 Doris MySQL 协议物理隔离，降低运维认知负担。

## 后果

### 正面

- **强一致性元数据**：查询路由、权限校验、Action OCC 全部基于强一致元数据，无最终一致风险
- **JSONB 灵活字段**：ObjectType.properties 等半结构化数据无需额外文档数据库（ADR-005）
- **单实例多用途**：业务元数据 + Iceberg REST backend + Gravitino entity store + PostGIS + TimescaleDB 共享一个 PG 实例，组件数最少
- **成熟迁移工具链**：Alembic 管理业务表 schema，单一真相源

### 负面 / 已知限制

- **单点风险**：开发环境单实例 PG，生产需 HA（Patroni）。PG 宕机 → 本体定义不可用（已注册物理表不受影响，见降级策略）
- **JSONB 查询性能**：properties 用 JSONB 存储（ADR-005），属性级粒度查询/审计/版本控制弱于关系表，触发条件满足时需拆分
- **连接池管理**：需监控连接池使用率（>80% 告警），高并发下需调优

## 替代方案（否决）

| 方案 | 否决原因 |
| ---- | -------- |
| **MySQL** | Doris 已用 MySQL 协议，Metadata 也用 MySQL 会造成运维混淆；JSONB 支持不如 PG（MySQL JSON 无 GIN 索引）；`DISTINCT ON`、CTE 等高级查询能力弱于 PG |
| **etcd** | 分布式 KV 存储，不适合复杂关系查询（按 ontology 列表、按 interface 反查、outbox 聚合）；无事务性 JSONB；适合配置分发而非业务元数据持久层 |

## 回归条件

出现以下任一情况，需重新评估：

1. 元数据规模或并发超过单 PG 实例能力（ObjectType > 10万、QPS > 5000），需要分片或换分布式数据库
2. properties JSONB 拆分为关系表（ADR-005 演进触发），届时评估是否部分元数据迁出 PG

## 修订记录

- **2026-05 初始决策**：架构 v5 终稿选定 PostgreSQL 16
- **2026-07（ADR-015）**：PG 实例升级为 `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6` 一体镜像，额外承载 PostGIS + TimescaleDB，本决策范围扩展但不变更
