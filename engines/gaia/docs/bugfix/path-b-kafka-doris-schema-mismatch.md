# 路径 B Kafka→Doris multi-table 同步（已废弃）

> **⚠️ 2026-07-08 去 SeaTunnel 化后状态：路径 B 已整体删除。** 本文档记录的「路径 B（Kafka→Doris 实时索引）」及其 `create_kafka_to_doris_pipeline` / `create_pg_to_kafka_pipeline` / `ActionSyncService` 已全部删除——object_state 同步改 outbox 驱动（INDEX effect → OutboxExecutor ≤1s → DorisIndexStore.upsert）。下方「已实现 / 端到端打通」等描述均为**历史记录**，不代表当前架构。当前架构见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) + [action-loop-design.md](../architecture/action-loop-design.md) §四.4。本文档保留作事故复盘与溯源。

## 背景

路径 B（Kafka→Doris）负责低延迟实时索引（3-5s），与路径 A（Iceberg→Doris 低频批式）互补。
初始实现用单 schema + `table="${doris_table}"` 动态路由，但发现 Doris idx 表 schema（按 ObjectType 属性展开的宽表）与 Kafka 消息 schema（object_state 原始 JSONB）不一致。

## 根因分析

不一致的本质：两条路径的数据源形态不同。
- 路径 A 源头是 Iceberg 表（列存 parquet 宽表，按属性列展开）
- 路径 B 源头是 PG object_state（JSONB 存储，properties 一列装全量属性）

加上 JSONB key 是 camelCase api_name（`leadsId`），Doris idx 列名是 snake_case backing_column（`leads_id`），存在列名大小写+下划线差异。

## 解法：SeaTunnel Kafka source `tables_configs` multi-table

SeaTunnel 2.3.13 Kafka source 原生支持 `tables_configs`（PR #5992，2.3.6+，`table_list` 的统一替代）：
- 每个 topic 配独立 schema（Schema Independence）
- topic 名作为上游 table identity（#8401），Doris sink `table="${table_name}"` 按其路由
- **1 个 job 处理 N 个 topic**，O(1) 常驻 job，与 ObjectType 数无关

### 动态生成

`ActionSyncService._build_kafka_doris_schemas()` 从 PG 查所有 indexed ObjectType properties，构建 `TableSchemaConfig` 列表：
- `topic` = `action_{ont_snake}__{type_snake}`（PG→Kafka transform 产出的 topic 名）
- `doris_table` = `idx_{ont_snake}__{type_snake}`（IndexSyncService 建的 Doris 表名）
- `fields` = 每个 indexed property 的 `{json_key=api_name(camelCase), doris_column=backing_column(snake)}`

`SeaTunnelEngine.create_kafka_to_doris_pipeline(table_schemas=...)` 渲染 `tables_configs` HOCON 并提交。

### live 验证（2026-07-06）

- 动态生成 119 个 topic 的 `tables_configs` ✅
- `gaia_kafka_to_doris` job 提交成功（200, jobId）+ RUNNING ✅
- `gaia_pg_to_kafka` job RUNNING，CDC 事件流出含 `ontology_api_name` ✅

## 遗留：列名映射（已解决）

Doris stream-load 默认按 JSON key 名匹配列名（"columns are matched by name"）。原问题：Kafka 消息 JSON key 是 camelCase（`leadsId`），Doris idx 列是 snake_case（`leads_id`）——不匹配。

**解法（2026-07-07）**：object_state 的 properties JSONB key 改用 `backing_column`（snake_case 物理列名），与 Iceberg/Doris 数据层一致（见 `src/ontology/core/property_mapping.py` + Alembic migration `7a3c1e9b2d44`）。Action 对外仍用 api_name，在 ActionService 写入边界（`_props_to_backing`）做 api_name→backing_column 转换，读取边界（`_snapshot_to_api` / object_set_executor `_state_props_to_api`）做 backing_column→api_name 转换。

改完后，路径 B 的 `tables_configs` schema field 直接用 `json_key = backing_column = doris_column`（见 `ActionSyncService._build_kafka_doris_schemas`），全链路一致，Doris stream-load 按名匹配无需 per-table jsonpaths。

曾考虑的候选解法（均已放弃）：
1. PG→Kafka transform 把 JSON key 转 snake_case（transform SQL 做 JSONB key 重命名较难）
2. object_state 的 properties JSONB 直接用 snake_case key 存储（✅ 已采用，object_state key 改造）
3. 等 SeaTunnel Doris sink 支持 per-table doris.config（社区特性跟进）
4. 接受 camelCase 列名（Doris idx 表列名改用 camelCase api_name，放弃 snake_case 物理命名——违反红线 9）

## 相关

- SeaTunnel Kafka source tables_configs：https://seatunnel.apache.org/docs/2.3.13/connectors/source/Kafka/
- SeaTunnel multi-table 同步：https://seatunnel.apache.org/docs/2.3.13/architecture/features/multi-table/
- PR #5992（Kafka multi-table source）：https://github.com/apache/seatunnel/pull/5992
- #8401（topic as table name）：https://github.com/apache/seatunnel/commit/3d4f4bb33
- 代码：`src/ontology/layers/pipeline/sea_tunnel_engine.py`（`TableSchemaConfig` + `create_kafka_to_doris_pipeline`）+ `src/ontology/services/action_sync_service.py`（`_build_kafka_doris_schemas`）
