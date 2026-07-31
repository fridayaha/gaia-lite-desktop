# 架构参考

> 本节包含 Gaia 分层数据架构的完整设计文档、架构决策记录（ADR）和组件间接口定义（ICD）。

## 架构导航

### 总览
- [架构总览](architecture_overview) — 全景概览
- [架构规划](architecture_plan) — 架构规划与路线图
- [实现状态](implementation-status) — 各组件实现状态

### 核心架构
- [Action 架构](action-architecture) — Action 执行架构
- [Action 闭环设计](action-loop-design) — Outbox 驱动的同步闭环
- [本体工具层](ontology-tool-layer) — 22 工具 8 Toolset
- [索引加速设计](index-acceleration-design) — Iceberg→Doris 同步

### TextQL
- [TextQL 设计](textql-design) — 本体驱动 NL 查询
- [TextQL 4+1 视图](textql-4plus1-views)

### 图关联推理
- [图关联推理设计](graph-reasoning-design)
- [推理进度](graph-reasoning-progress)
- [前端设计 v2](graph-reasoning-frontend-design-v2)
- [前端设计 v3](graph-reasoning-frontend-design-v3)

### 接口定义（ICD）

| 编号 | 接口 | 文件 |
|------|------|------|
| ICD-01 | PostgresMetaStore | [icd-01-postgres-meta-store](icd-01-postgres-meta-store) |
| ICD-02 | GravitinoRegistry | [icd-02-gravitino-registry](icd-02-gravitino-registry) |
| ICD-03 | IcebergStore | [icd-03-iceberg-store](icd-03-iceberg-store) |
| ICD-04 | DorisIndexStore | [icd-04-doris-index-store](icd-04-doris-index-store) |
| ICD-05 | TrinoQueryEngine | [icd-05-trino-query-engine](icd-05-trino-query-engine) |

### 架构决策（ADR）

| 编号 | 标题 | 文件 |
|------|------|------|
| ADR-001 | Doris 作为在线读主源 | [adr-001-doris-as-online-read-source](adr-001-doris-as-online-read-source) |
| ADR-002 | SeaTunnel 而非 Flink | [adr-002-seatunnel-over-flink](adr-002-seatunnel-over-flink) |
| ADR-003 | RustFS 而非 MinIO | [adr-003-rustfs-over-minio](adr-003-rustfs-over-minio) |
| ADR-004 | PostgreSQL 存业务本体元数据 | [adr-004-postgresql-for-ontology-metadata](adr-004-postgresql-for-ontology-metadata) |
| ADR-005 | ObjectType.properties 用 JSONB | [adr-005-objecttype-properties-as-jsonb](adr-005-objecttype-properties-as-jsonb) |
| ADR-006 | Python + FastAPI | [adr-006-python-fastapi-over-typescript-go](adr-006-python-fastapi-over-typescript-go) |
| ADR-007 | Iceberg REST Catalog 访问通道 | [adr-007-iceberg-rest-catalog-access](adr-007-iceberg-rest-catalog-access) |
| ADR-008 | Iceberg→Doris 索引同步路径 | [adr-008-iceberg-doris-sync-path](adr-008-iceberg-doris-sync-path) |
| ADR-009 | 本体工具层 | [adr-009-ontology-tool-layer](adr-009-ontology-tool-layer) |
| ADR-010 | 本体 HITL 审批机制 | [adr-010-ontology-hitl](adr-010-ontology-hitl) |
| ADR-011 | Action P1 | [adr-011-action-p1](adr-011-action-p1) |
| ADR-012 | 本体驱动自然语言查询 TextQL | [adr-012-textql-ontology-driven-nl-query](adr-012-textql-ontology-driven-nl-query) |
| ADR-013 | 前端 React Aria Components | [adr-013-react-aria-components](adr-013-react-aria-components) |
| ADR-014 | 多源异构数据融合连接器体系 | [adr-014-multi-source-data-fusion-connectors](adr-014-multi-source-data-fusion-connectors) |
| ADR-015 | AG-UI Agent 驱动图探索画布 | [adr-015-agent-driven-graph-explore](adr-015-agent-driven-graph-explore) |
| ADR-016 | 权限治理体系 | [adr-016-permission-governance](adr-016-permission-governance) |
| ADR-017 | 权限治理技术选型 | [adr-017-permission-tech-stack](adr-017-permission-tech-stack) |
| ADR-018 | Pipeline Builder | [adr-018-pipeline-builder](adr-018-pipeline-builder) |
| — | Action 映射 | [adr-action-mutation-mapping](adr-action-mutation-mapping) |

### 规范与评估
- [Gravitino 类型兼容](gravitino-type-compatibility)
- [本体建模规范](ontology-modeling-spec)
- [本体建模 E2E 评审](ontology-modeling-e2e-review)
- [权限治理评估](permission-governance-landing-assessment)
