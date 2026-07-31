# Gravitino 1.2.0 → 1.3.0 升级记录

## 当前状态

| 组件 | 版本 | 状态 |
|------|------|------|
| Gravitino | **1.2.0** | ✅ 稳定运行 |
| Trino | **470** | ✅ 稳定运行 |
| Trino Gravitino Connector | `gravitino-trino-connector-469-472-1.2.0.jar` | ✅ |
| Entity Store | PostgreSQL `gravitino_store` schema | ✅ 已升级到 1.3.0 表结构（40 张表，向后兼容） |
| Type Converter | `type-converter.custom.mapping` | ❌ **1.3.0 不存在此功能**（2026-06-28 核实）。PG `jsonb` 仍映射为 ExternalType，jsonb 查询仍依赖 `pgnative` workaround |
| SeaTunnel | **2.3.13** | ⚠️ 多处飞线绕过驱动冲突 / timestamptz 类型缺失（见下文「SeaTunnel / Iceberg 同步链路飞线」） |

## 🚀 升级就绪状态（2026-06-28 核实）

**1.3.0 已可升级，三大阻塞全部解除。** 详见下文 §1.3.0 发布跟踪 / §阻塞问题 / §升级步骤。

- `apache/gravitino:1.3.0` Docker tag 已发布（本地已拉取：`4ff340f11606`）
- `gravitino-client-java-runtime-1.3.0.jar` ✅ Maven Central
- `gravitino-trino-connector-473-478-1.3.0.jar` ✅ Maven Central（**无需再从源码编译**）
- GitHub `v1.3.0` release tag 尚未出现（最新正式 release 仍 v1.2.1），但 Docker/Maven 产物已就绪属 Apache 发布流程正常现象

> ⚠️ **但 jsonb 问题在 1.3.0 仍未解决**（升级核心目标未达成）：1.3.0 仍把 PG `jsonb` 映射为 `ExternalType`，
> `DESCRIBE pg.public.action_types` 仍报 `external(jsonb)`。pgnative workaround 保留。
> 详见下文「❌ jsonb 问题在 1.3.0 仍未解决（升级后核实）」。
>
> **升级本身是正向且已完成的**：1.3.0 服务稳定、connector 无需编译、Iceberg REST 正常、密码字段安全改进。不回退。

## ⚠️ 1.3.0 Breaking Changes（升级必读，易踩坑）

> 以下变更由实测 `apache/gravitino:1.3.0` 镜像 + 官方 `how-to-upgrade.md` 确认。

### BC-1：`GRAVITINO_HOME` 路径变更 `/root/gravitino` → `/opt/gravitino`

**影响**：本项目 `docker-compose.yml` gravitino 服务的 `volumes` 挂载路径和 `command` 启动脚本路径全部硬编码了 `/root/gravitino/...`，升级时**必须**全部改为 `/opt/gravitino/...`，否则挂载失效、启动脚本找不到。

涉及行（当前 docker-compose.yml）：
- `volumes: ./config/gravitino/gravitino-iceberg-rest-server.conf:/root/gravitino/conf/...` → 改 `/opt/gravitino/conf/...`
- `command` 里 `/root/gravitino/bin/gravitino.sh` / `/root/gravitino/bin/gravitino-iceberg-rest-server.sh` / `/root/gravitino/logs/...` → 全改 `/opt/gravitino/...`

官方 how-to-upgrade.md 的 Helm 升级表也列了此变更（`env.GRAVITINO_HOME`、log mountPath）。

### BC-2：默认 entrypoint 通过 aux service 内嵌启动 iceberg-rest（✅ 2026-07 修正）

1.3.0 默认 `ENTRYPOINT=/opt/gravitino/docker/docker-entrypoint.sh`，最终 `exec ./bin/gravitino.sh run`。

**原结论（错误）**：认为 `gravitino.sh run` 只启动主服务、不启动 iceberg-rest，故必须保留双脚本 command。

**实测修正**：rewrite 脚本把 `GRAVITINO_ICEBERG_REST_*` env 写成 `gravitino.auxService.names = iceberg-rest` + `gravitino.iceberg-rest.*` 配置项，GravitinoServer 主进程会**内嵌加载 IcebergRESTService aux service 到 9001**（日志见 `AuxService:iceberg-rest registered successfully` + `iceberg-rest web server started on host 0.0.0.0 port 9001`）。

**原双脚本模式的三个危害**：
1. 自定义 `command:` 覆盖 ENTRYPOINT → rewrite 脚本不执行 → `GRAVITINO_ENTITY_STORE_*` env 不生效 → entity store 退回 H2 内存（重启丢 metalake/catalog）
2. entrypoint 的 `jdbc-drivers/postgresql-*.jar` symlink 不执行 → entity store 报 `ClassNotFoundException: org.postgresql.Driver`
3. 独立的 `gravitino-iceberg-rest-server.sh start` 与 aux service 端口冲突（都在 9001）

**正确做法**：不自定义 command，用镜像默认 ENTRYPOINT。env 通过 rewrite 生效，iceberg-rest 由 aux service 内嵌。docker-compose 同样修复（去掉双脚本 command）。

### BC-3：env 重写机制增强（利好，非破坏）

1.3.0 entrypoint 会调 `rewrite_gravitino_server_config.py` 从 `GRAVITINO_*` env 重写 `gravitino.conf`。已确认 rewrite 脚本含完整的 `GRAVITINO_ICEBERG_REST_*` → `gravitino.iceberg-rest.*` 映射表（host/httpPort/uri/catalog-backend/jdbc-*/warehouse/s3-*/credential-providers 等）。本项目当前 env 注入方式**继续有效，无需改动**。

entrypoint 还会自动 symlink `jdbc-drivers/*.jar` 和 `iceberg-bundles/*.jar` 到 libs——未来如需加 PG/JDBC 驱动或 Iceberg bundle，放到这两个目录即可，无需改 conf。

### BC-4：connector artifact 形态变化

1.3.0 的 trino-connector 在 Maven Central 是**单个 `.jar`**（`gravitino-trino-connector-473-478-1.3.0.jar`，289KB），不再是 1.2.0 的 `tar.gz`。部署时配套 `gravitino-client-java-runtime-1.3.0.jar`（依赖）+ `trino-jdbc-478.jar`。详见 §升级步骤 Step 3。

### BC-5：默认 catalog-backend 仍是 memory（本项目覆盖为 jdbc）

1.3.0 镜像默认 `gravitino.iceberg-rest.catalog-backend = memory`、`warehouse = /tmp`。本项目通过 env 覆盖为 `jdbc` + `s3://ontology-warehouse`，**配置不变**，但升级后需验证 env 重写确实生效（curl 9001 config 应返回 jdbc backend 的配置）。

## 为什么升级到 1.3.0

> ⚠️ **2026-06-28 升级后纠错**：本节原以「启用 `type-converter.custom.mapping` 解决 jsonb」为核心诉求，
> 但实测 1.3.0（及 main 分支）**根本不存在 `type-converter.custom.mapping` 这个功能**——官方文档、源码、镜像 jar 均无此配置。
> 1.3.0 的 `PostgreSqlTypeConverter` 仍把 `jsonb` 映射为 `ExternalType("jsonb")`（与 1.2.0 一致），
> `DESCRIBE pg.public.action_types` 仍报 `Unsupported gravitino datatype: external(jsonb)`。
> **因此 jsonb 问题在 1.3.0 仍未解决，pgnative workaround 不能移除。**
> 详见下文「❌ jsonb 问题在 1.3.0 仍未解决（升级后核实）」。

升级到 1.3.0 的真实收益（与 jsonb 无关）：

1. **Trino connector 不再需要源码编译**：1.3.0 的 `gravitino-trino-connector-473-478` 和 `gravitino-client-java-runtime` 已发布到 Maven Central，旧文档记录的三个编译阻塞全部解除。
2. **密码字段安全改进**：catalog API 不再返回 `jdbc-user`/`jdbc-password`，改由 connector 通过 Credential API 获取（1.3.0 connector 配套支持）。
3. **Iceberg REST aux service 机制成熟**：env 重写 `gravitino.conf` 的 `gravitino.iceberg-rest.*` 项有完整映射表，entrypoint 自动 symlink jdbc-drivers / iceberg-bundles。
4. **PG Entity Store 表结构升级**：40 张表（新增 entity_change_log / view_version_info / idp_* / iceberg_cleanup_job）。
5. 为后续真正解决 jsonb（待社区在 PG TypeConverter 增加 jsonb→JSON 映射）打基础。

---

## ❌ jsonb 问题在 1.3.0 仍未解决（升级后核实，2026-06-28）

> 本节是升级后的实测纠错，记录「1.3.0 + type-converter 解决 jsonb」这一原核心预期被证伪的过程与证据。

### 实测过程

1. **创建带 type-converter 属性的 `pg` catalog**（1.3.0 API）：
   ```bash
   curl -X POST http://localhost:8090/api/metalakes/ontology/catalogs \
     -H "Content-Type: application/json" \
     -d '{ "name":"pg", "type":"relational", "provider":"jdbc-postgresql",
           "properties": {
             "jdbc-url":"jdbc:postgresql://postgres:5432/ontology",
             "jdbc-user":"ontology", "jdbc-password":"ontology",
             "jdbc-driver":"org.postgresql.Driver", "jdbc-database":"ontology",
             "type-converter.enabled":"true",
             "type-converter.custom.mapping":"jsonb=JSON,json=JSON,uuid=VARCHAR,..." } }'
   # → code:0, catalog 创建成功（属性被静默接受，不报错）
   ```
2. **Trino 查询**：`DESCRIBE pg.public.action_types` → 仍报
   `Query failed: Unsupported gravitino datatype: external(jsonb)`
   （错误码 `GRAVITINO_UNSUPPORTED_GRAVITINO_DATATYPE`，与 1.2.0 一模一样）

### 根因（源码级确认）

翻 1.3.0 源码（`/home/jason/code/gravitino` main 分支，含 1.3.0 全部代码）：

- `catalogs/catalog-jdbc-postgresql/.../PostgreSqlTypeConverter.java` 的 `toGravitino()` switch：
  - 已识别类型：`BOOLEAN/SMALLINT/INT2/INTEGER/INT4/BIGINT/INT8/REAL/FLOAT4/DOUBLE/FLOAT8/
    NUMERIC/DECIMAL/CHARACTER/VARCHAR/BPCHAR/TEXT/BYTEA/UUID/DATE/TIME/TIMESTAMP/TIMESTAMP_TZ`
  - **`jsonb`/`json` 不在列**，走 `default: return Types.ExternalType.of(typeBean.getTypeName());`
  - 即 jsonb → `ExternalType("jsonb")`，**1.3.0 与 1.2.0 行为完全一致**
- `trino-connector/.../GeneralDataTypeTransformer.getTrinoType()` 的 default 分支：
  `throw new TrinoException(..., "Unsupported gravitino datatype: " + type);`
  - connector 层直接拒绝 ExternalType，发生在交给底层 Trino postgresql connector 之前

### `type-converter.custom.mapping` 功能不存在

- 1.3.0 官方文档 `docs/trino-connector/configuration.md`：配置项只有 `connector.name` /
  `gravitino.metalake` / `gravitino.uri` / `trino.jdbc.*` / `refresh-interval-seconds` /
  `skip-version-validation` / `skip-catalog-patterns` / `use-single-metalake` / `gravitino.client.*`，
  **无任何 `type-converter` / `type-mapping` 项**
- 1.3.0 官方文档 `docs/jdbc-postgresql-catalog.md` 第 120 行明确：「自 0.6.0 起，未列出的类型映射为
  [External Type]」——即 jsonb 是「未列出类型」，设计上就是 ExternalType
- 源码全局搜索 `type-converter` / `typeConverter` / `TypeConverter` 配置入口：**无**
  （只有 `DataTypeConverter` / `JdbcTypeConverter` 这些内部接口，无用户可配的 mapping）
- 镜像 jar 内 strings 扫描：**无 `type-converter` 字面量**

### 结论与对策

| 项 | 结论 |
|----|------|
| 1.3.0 是否解决 jsonb | ❌ 否，行为与 1.2.0 一致 |
| `type-converter.custom.mapping` 是否存在 | ❌ 不存在（旧文档误判） |
| pgnative workaround 是否可移除 | ❌ 不可，保留 |
| 1.3.0 是否值得升级 | ✅ 是（其它收益：connector 免编译、密码安全、aux service 成熟、表结构升级） |

**jsonb 的真正解法**（待社区）：在 `PostgreSqlTypeConverter` 的 switch 里增加 `case JSONB:/case JSON:` →
`Types.StringType.get()` 或新增 Gravitino `JsonType`。当前社区未做此改动。本项目维持 pgnative workaround。

> 📖 **类型兼容机制的完整分析**（为什么 MySQL JSON 能查、PG jsonb 不能查、trino.bypass 为何无效、
> 各数据源对照表）见 [`docs/architecture/gravitino-type-compatibility.md`](../architecture/gravitino-type-compatibility.md)。
> 社区跟踪 issue：[#9892](https://github.com/apache/gravitino/issues/9892)（open）。

---

## ⭐ 1.2.0 下的替代方案（已验证，推荐优先采用）

> 2026-06-18 重新评估结论：**不需要升级到 1.3.0 即可解决 jsonb 查询问题。**
> 本项目实际只有 `jsonb` 一个类型需要处理（`uuid` 主键按 CLAUDE.md 规范用 `varchar(32)` 存储，不存在 PG 原生 `uuid` 类型列；`geometry`/`geography` 当前未使用）。

### 根因分析（为什么 1.2.0 会报错）

实测复现：通过 Gravitino 管理的 `pg` catalog 执行 `DESCRIBE pg.public.action_types` 报错：
```
Query failed: Unsupported gravitino datatype: external(jsonb)
```

调用栈（来自 Gravitino issue #7338 / #10957，环境与本项目一致：Trino + Gravitino 1.2.0 + JDBC PostgreSQL）：
```
io.trino.spi.TrinoException: Unsupported gravitino datatype: external(jsonb)
  at org.apache.gravitino.trino.connector.util.GeneralDataTypeTransformer.getTrinoType(GeneralDataTypeTransformer.java:126)
  at ...GravitinoMetadata.getTableMetadata(GravitinoMetadata.java:133)
  at io.trino.spi.connector.ConnectorMetadata.getTableSchema(...)
```

**关键结论**：
1. Gravitino 1.2.0 的 `PostgreSqlTypeConverter` 把 `jsonb` 映射成 `ExternalType("jsonb")`（自 0.6.0 起不支持类型的统一处理）。
2. Gravitino Trino connector 在 `GeneralDataTypeTransformer.getTrinoType()` 阶段（`GravitinoMetadata.getTableMetadata`）**就拒绝了 `ExternalType`**，发生在把元数据交给底层 Trino postgresql connector **之前**。
3. 因此 `trino.bypass.unsupported-type-handling=CONVERT_TO_VARCHAR` 和 `trino.bypass.jdbc-types-mapped-to-varchar=jsonb,...` **注定无效**——这些 bypass 参数传给底层 connector，但请求根本走不到那一步。（本项目 `pg` catalog 已配置这两个 bypass，实测仍报错，印证此结论。）
4. issue #7338 的修复 PR #7578（2025-07）只针对 **MySQL** 的 external type 做了特殊兜底，PG 的 `jsonb` 在 1.2.0 仍无解。

### 替代方案：Trino 原生 PostgreSQL connector 直连

既然 Gravitino Trino connector 走不通，就直接在 Trino 侧加一个**原生 `postgresql` connector catalog**，绕过 Gravitino 的类型转换层。Trino 470 镜像自带 `postgresql` 插件（`/usr/lib/trino/plugin/postgresql`），无需额外安装。

**已实测验证**（2026-06-18，临时挂载 `pgnative.properties` 直连 `postgres:5432/ontology`）：

| 验证项 | 结果 |
|--------|------|
| `DESCRIBE pgnative.public.action_types` | ✅ `parameters`/`rules`/`submission_criteria` 显示为 `json` 类型 |
| `SELECT connector_config FROM pgnative.public.data_sources` | ✅ 正常返回 jsonb 数据 |
| `json_extract_scalar(connector_config, '$.host')` | ✅ JSON 函数正常工作，返回 `postgres` |
| `DESCRIBE` 含 17 个 jsonb 列的全部表 | ✅ 全部成功 |

Trino 原生 postgresql connector 的类型映射（`JSONB → JSON`、`JSON → JSON`）是内置的，见 Trino 官方文档。

### 落地状态：✅ 已实施（2026-06-18）

已完成的改动：
1. 新增 `config/trino/catalog/pgnative.properties`（含 `unsupported-type-handling=CONVERT_TO_VARCHAR` 兜底，文件头有 TEMPORARY 注释指回本文档）
2. `docker-compose.yml` trino 服务 volumes 挂载该文件
3. 顺手修复了同一 volumes 块里 `iceberg.properties` 挂载路径的 bug（原为 `...:/etc/trino/catalog/iceberg.properties:/etc/trino/catalog/iceberg.properties`，多了一段冒号）
4. `docker compose up -d --force-recreate trino` 重建验证通过
5. **数据层调整**：`data_sources` 表里 PG 数据源的 `gravitino_catalog_name` 从 `pg` 改为 `pgnative`，使 `datasource_service` 的 `list_tables/describe_table/sample_data`（用 `ds.gravitino_catalog_name or ds.api_name` 选 catalog）走原生 connector。新建环境需同样执行：
   ```sql
   UPDATE data_sources SET gravitino_catalog_name='pgnative'
   WHERE connector_type='postgresql' AND gravitino_catalog_name='pg';
   ```

实测结果（`pgnative` 与 Gravitino 管理的 `pg` catalog 共存）：
| 验证项 | 结果 |
|--------|------|
| `DESCRIBE pgnative.public.action_types` | ✅ jsonb 列显示为 `json` |
| `json_extract_scalar(connector_config,'$.host')` | ✅ 返回 `postgres` |
| `DESCRIBE pg.public.action_types`（Gravitino catalog） | ❌ 仍报 `external(jsonb)`（证明 workaround 必要） |

**查询约定**：业务代码里凡是查 PostgreSQL 业务表的，用 `pgnative.` 前缀，不要用 `pg.`（后者在 1.2.0 下对 jsonb 表会失败）。

### 方案权衡

| 维度 | Gravitino 管理 (`pg` catalog) | 原生直连 (`pgnative` catalog) |
|------|------------------------------|------------------------------|
| jsonb 查询 | ❌ 1.2.0 不可用 | ✅ 完美支持 |
| 统一编目 / RBAC | ✅ Gravitino 管控 | ❌ 绕过 Gravitino |
| 跨 catalog 联邦查询 | ✅ 可与 iceberg 等 JOIN | ✅ 同样可 JOIN（都在 Trino） |
| 维护成本 | 需等 1.3.0 + 编译 connector | 零额外依赖 |

**建议**：本项目当前阶段（本体元数据查询、数据预览）采用原生直连方案。Gravitino 的价值在于物理资产编目与 Iceberg catalog 管理（这两个能力不受影响，`iceberg` catalog 走 Gravitino REST 仍正常）。

> ⚠️ **2026-06-28 纠错**：原文本写「待 Gravitino 1.3.0 正式版发布且 connector 编译问题解决后，再回归统一 catalog」——
> 此预期已被证伪：1.3.0 已发布且 connector 无需编译，但 **jsonb 问题在 1.3.0 仍未解决**（见下文）。
> pgnative workaround 在 1.3.0 下仍需保留，回归统一 catalog 的时间点待社区真正支持 PG jsonb 类型映射。

### 1.3.0 升级的触发条件

> ⚠️ 2026-06-28 纠错：原第三条「需要 `type-converter.custom.mapping`」前提错误（该功能不存在），已删除。

升级到 1.3.0 的真实触发条件（已全部满足，升级已完成）：
- 需要 Gravitino 对 PG catalog 做 RBAC / 列级权限管控（原生直连绕过了 Gravitino 权限层）——1.3.0 密码字段安全改进为此铺路
- 需要 1.3.0 的 Entity Store 新表（entity_change_log / idp_* 等）支持的用户/组/审计能力
- 需要不再源码编译 Trino connector（1.3.0 产物已上 Maven Central）
- 需要 Iceberg REST aux service 的 env 重写机制（1.3.0 完整支持）

**jsonb 问题不是升级触发条件**——1.3.0 未解决，维持 pgnative workaround。

### 🔙 1.3.0 升级后的回归 checklist（jsonb 相关——暂不执行）

> ⚠️ **2026-06-28 纠错**：原 checklist 基于「1.3.0 + type-converter 后 jsonb 可解决」的错误前提。
> 实测 1.3.0 仍报 `external(jsonb)`，**以下清理项全部暂不执行**，pgnative workaround 保留。
> 待社区在 PG TypeConverter 增加 jsonb→JSON 映射后再执行。

升级到 Gravitino 1.3.0 并启用 `type-converter.custom.mapping` 后，`pg` catalog 能原生处理 jsonb，`pgnative` workaround 即可移除。**务必完成以下清理，避免遗留两套查询路径**：

- [ ] 验证 `DESCRIBE pg.public.action_types` 不再报 `external(jsonb)`，jsonb 列显示为标准类型  ← **1.3.0 仍报错，未达成**
- [ ] 验证业务查询从 `pgnative.` 改回 `pg.` 前缀后功能正常  ← 暂不执行
- [ ] `data_sources` 表里 PG 数据源的 `gravitino_catalog_name` 从 `pgnative` 改回 `pg`（或与 Gravitino 注册名一致）：
      `UPDATE data_sources SET gravitino_catalog_name='pg' WHERE connector_type='postgresql' AND gravitino_catalog_name='pgnative';`  ← 暂不执行
- [ ] 删除 `config/trino/catalog/pgnative.properties`  ← 暂不执行
- [ ] 从 `docker-compose.yml` trino 服务 volumes 移除 `pgnative.properties` 挂载行  ← 暂不执行
- [ ] 全仓库搜索 `pgnative` 确认无残留引用（`grep -rn pgnative src/ tests/ docs/`）  ← 暂不执行
- [ ] 重建 trino 容器确认 `pgnative` catalog 不再加载  ← 暂不执行

---

## 🚧 SeaTunnel / Iceberg 同步链路飞线（2026-06-18）

> 本节记录为了让「数据源 → SeaTunnel → Iceberg → Trino」全链路在 SeaTunnel 2.3.13 + Gravitino 1.2.0 下跑通而引入的**所有临时性特殊处理**。每一项都标注了根因、当前飞线、升级后的正确做法。**升级 Gravitino / SeaTunnel 时必须逐项回归，移除飞线。**

### 飞线 1：SeaTunnel 移除 openGauss JDBC 驱动

**根因**：`apache/seatunnel:2.3.13` 镜像同时内置 `postgresql-42.4.3.jar` 和 `opengauss-jdbc-5.1.0.jar`。openGauss 驱动 jar 内含一份**完整的 `org/postgresql/Driver.class`**（为 PG 兼容性），两个 jar 注册了**同名** driver 处理 `jdbc:postgresql://` URL。SeaTunnel 的 `AbstractJdbcCatalog.getConnection`（含 PR #8986 的「按类名优先选 driver」逻辑）对**同名类**无效，回退到 `DriverManager` 顺序加载，openGauss 驱动先加载获胜，但其 SCRAM/SHA-256 认证与标准 PG 不兼容，报 `Protocol error. Session setup failed`。社区 issue [#10229](https://github.com/apache/seatunnel/issues/10229) / [#10242](https://github.com/apache/seatunnel/issues/10242)，ClassLoader 级修复 2026-01 合入但**未发版**。

**当前飞线**：`infra/seatunnel-entrypoint.sh` 在容器启动前把 `opengauss-jdbc-*.jar` 从 `/opt/seatunnel/lib` **移到** `/opt/seatunnel/lib.parked/`（不是删除，可逆）。`docker-compose.yml` 的 `seatunnel-master` / `seatunnel-worker` 都挂载并使用该 entrypoint。

**升级后的正确做法**：
- 升级 SeaTunnel 到含 #10229 修复的版本（≥ 下一个含 ClassLoader 隔离的 release）。
- 升级后删除 `infra/seatunnel-entrypoint.sh`，从 `docker-compose.yml` 两个 seatunnel 服务的 `entrypoint` / `volumes` 移除对该脚本的引用。
- 验证 openGauss 数据源（若未来需要）与 PostgreSQL 数据源能共存同步。

### 飞线 2：PG `timestamptz` 列在 source query 里 `::text` cast

**根因**：SeaTunnel 2.3.13 的 Jdbc source `PostgresTypeConverter` 把 PG `timestamptz` 和 `timestamp` **都映射成内部 `LOCAL_DATE_TIME_TYPE`**（甚至 fallback 到 `STRING`），`TIMESTAMP_TZ` 读取支持在 PR [#10048](https://github.com/apache/seatunnel/pull/10048)（2025-11）才合入。Iceberg sink 的 `RowConverter.convertString` 用 Jackson 序列化 `OffsetDateTime` 但 shade 后的 ObjectMapper 没注册 JSR310 模块，抛 `InvalidDefinitionException: Java 8 date/time type java.time.OffsetDateTime not supported by default`。

**当前飞线**：`datasource_service._build_safe_query` 在提交前用 `asyncpg` 连源库查 `information_schema.columns`，对 `timestamp with time zone` / `timestamp without time zone` 列生成 `col::text AS col` 的显式 cast query。Iceberg 表里时间戳列存为 **string**，完整保留时区信息（如 `2026-06-18 03:12:00.669553+00`），下游 `CAST(col AS TIMESTAMP)` 可还原。非 PG 源或连不上时 fallback `SELECT *`。

**代价**：Iceberg 里时间戳列类型是 string 而非 timestamp，丧失原生时间函数 / 谓词下推优化；查询时需手动 cast。

**升级后的正确做法**：
- 升级 SeaTunnel 到含 #10048 的版本（`timestamptz` 原生映射成 `TIMESTAMP_TZ` → Iceberg `TimestampType.withZone()`）。
- 删除 `_build_safe_query` 方法及其在 `_submit_sync_pipeline` 的调用，`source_config_full` 不再传 `query`（让模板用默认 `SELECT *`）。
- 删除 `tests/unit/services/test_datasource_service.py` 里 `test_build_safe_query_*` 三个测试。
- 验证同步后 Iceberg 表 schema 里时间戳列是 `timestamp(6) with time zone` 而非 `varchar`。

### 飞线 3：full_snapshot 同步前删除 Iceberg 目标表

**根因**：SeaTunnel Iceberg sink 的 `schema_save_mode=CREATE_SCHEMA_WHEN_NOT_EXIST` 在表已存在时**不会重建表**，只会按已有 schema 写数据。当源端 schema 变化（最典型：飞线 2 的 `::text` cast 让列类型从 timestamp 变 string），旧表的 timestamp 列会拒绝新 string 值，报 `table ... sink throw error`。

**当前飞线**：`iceberg_store.drop_table_if_exists` 在每次 full_snapshot 同步提交前删除已存在的目标表，让 SeaTunnel 按当前源 schema 重建。`datasource_service._submit_sync_pipeline` 仅在 `task.sync_mode == "full_snapshot"` 时调用它（incremental 不删，append 语义）。

**代价**：每次全量同步丢失 Iceberg 表的历史 snapshot / time-travel 能力（对全量快照场景可接受，但不是理想行为）。

**升级后的正确做法**：
- 评估升级后是否仍需 drop。理想方案是用 SeaTunnel 的 `data_save_mode=DROP_DATA` + `schema_save_mode=RECREATE_SCHEMA` 让 sink 自己处理重建，而非 service 层手动 drop。
- 若 SeaTunnel 新版的 save mode 可靠，删除 `drop_table_if_exists` 方法及其调用，改在 MAIN 模板配置 `schema_save_mode` / `data_save_mode`。
- 保留 incremental 路径的 append 语义不变。

### 飞线 4：Iceberg namespace / table 管理 bypass pyiceberg

**根因**：Gravitino 1.2.0 Iceberg REST server（memory backend）开启 `credential-providers=s3-token` 后，**强制 vended credentials 流程**，拒绝客户端自带的静态 S3 凭证。pyiceberg 的静态凭证路径在 `load_catalog` / `list_namespaces` / `create_namespace` / `drop_table` 时收到 `401 The provided credentials did not support`，但 `IcebergStore` 的 `ensure_namespace` 旧实现用 `try/except: pass` 静默吞掉了这个错误，导致 namespace 实际未创建，SeaTunnel 提交时报 `Handle save mode failed` / `NoSuchNamespaceException`。

**当前飞线**：`iceberg_store.ensure_namespace` 和 `drop_table_if_exists` 从 pyiceberg 改为**直接用 httpx 调 Iceberg REST API**（`POST /v1/namespaces`、`DELETE /v1/namespaces/{ns}/tables/{t}`）。REST 端点本身对默认 catalog 接受未认证的 namespace/table 管理操作。

**代价**：`IcebergStore` 出现两套 Iceberg 访问路径（pyiceberg 用于 read/scan，httpx 用于 DDL），不一致。

**升级后的正确做法**：
- 升级 Gravitino 到 1.3.0+，其 Iceberg REST server 对静态凭证 / vended credentials 的处理更完善；或关闭 `credential-providers=s3-token`（前提是所有客户端都配静态凭证，Trino 已配 `fs.native-s3`）。
- 统一回到 pyiceberg：`ensure_namespace` / `drop_table_if_exists` 恢复用 `self.catalog.create_namespace` / `drop_table`，删除 httpx 直连代码。
- 验证 pyiceberg 的 `load_catalog` 不再 401。

### 飞线 5：Trino iceberg catalog 不设 warehouse

**根因**：Gravitino 1.2.0 Iceberg REST server 把 `/v1/config?warehouse=X` 的 `warehouse` 参数当作 **catalog name 查找键**（issue [#10486](https://github.com/apache/gravitino/issues/10486)），任何不匹配已注册 catalog 名的值返回 `NoSuchCatalogException: Couldn't find Iceberg configuration for catalog X`。Trino iceberg REST connector 默认会把 `iceberg.rest-catalog.warehouse` 透传到 `/v1/config?warehouse=...`，于是查询全部失败。

**当前飞线**：`config/trino/catalog/iceberg.properties` **不设** `iceberg.rest-catalog.warehouse`。Trino 客户端不发 warehouse 参数，Gravitino 返回默认 catalog config（即 `catalog-backend-name=ontology`），查询正常。S3 访问改用 native 文件系统（`fs.native-s3.enabled=true` + 静态凭证 + `s3.endpoint`），`iceberg.rest-catalog.vended-credentials-enabled=false`。

**升级后的正确做法**：
- 升级 Gravitino 到 1.3.0+（Iceberg REST 对 warehouse 参数处理已修正，支持按 warehouse 路由 catalog）。
- 恢复 `iceberg.rest-catalog.warehouse=s3://ontology-warehouse`。
- 评估重新启用 `vended-credentials-enabled=true`（Gravitino 1.3.0 的 vended credentials 更成熟，可去掉 Trino 侧静态 S3 凭证）。
- 验证 `SHOW SCHEMAS IN iceberg` 仍返回 `ontology`，`SELECT count(*)` 能读到数据。

### 飞线 6：Gravitino metalake 清理 `default_catalog`（一次性）

**根因**：早期 SeaTunnel 用 memory backend 时在 Gravitino metalake `ontology` 里注册了一个名为 `default_catalog` 的 lakehouse-iceberg catalog（`catalog-backend=memory`）。Gravitino Trino connector 1.2.0 加载它时报 `Unsupported backend type: memory`，该失败**级联影响同 Trino 实例上的 `iceberg` catalog 查询**（metadata 处理时触发 catalog 加载）。

**当前处理**（已完成，非持续性飞线）：`DELETE /api/metalakes/ontology/catalogs/default_catalog?force=true` 删除该 catalog。metalake 现仅剩 `pg` catalog。

**升级后注意**：
- 升级后若重新创建 Iceberg catalog，避免用 `default_catalog` 这个名字，且避免 memory backend（用 jdbc / hive backend）。
- Gravitino 1.3.0 的 Trino connector 若已支持 memory backend，此问题自然消失，但 memory backend 仍不推荐用于生产（无持久化）。

### 非飞线：本次的正确性修复（升级后保留）

以下改动是 bug 修复，**不是飞线**，升级后应保留：

- **`SeaTunnelEngine._submit_job` 解析 SeaTunnel 的 `{status:"fail"}` 响应体**：SeaTunnel 对失败也返回 HTTP 200，旧代码只看 HTTP 状态导致静默成功。这是 SeaTunnel API 契约的正确处理，任何版本都应保留。
- **`SeaTunnelEngine.get_job_status` 改查 `running-jobs` + `finished-jobs`**：旧的 `/job-info?jobName=` 用错端点（job-info 要 jobId 不是 jobName）。正确实现，保留。
- **`datasource_service.start_sync` 真正 (re-)submit job**：旧实现调 no-op 的 `engine.start()` 就写 RUNNING。正确行为，保留。
- **`datasource_service.refresh_sync_status` + `/sync-tasks/{name}/refresh` 端点**：让 UI 显示 SeaTunnel 真实状态。正确能力，保留。
- **MAIN 模板 `job.mode` 按 `sync_mode` 动态选（full_snapshot→BATCH）**：全量快照是有限作业，应跑完即停。正确改进，保留（升级后无需改）。
- **前端 `SyncTaskCard` 去掉「RUNNING+full_snapshot→显示 COMPLETED」的臆造逻辑**：显示真实状态。正确修复，保留。
- **前端 `useDataSource.startSync/stopSync` 用返回值就地更新单行**：修掉用 `data_source_id` 当 `dsApiName` 导致列表被清空的 bug。正确修复，保留。

### 🔙 SeaTunnel / Iceberg 链路升级回归 checklist

升级 SeaTunnel（≥ 含 #10048 / #10229 的版本）和 / 或 Gravitino（≥ 1.3.0）后，逐项回归飞线 1–5：

- [ ] **飞线 1**：删除 `infra/seatunnel-entrypoint.sh`，从 `docker-compose.yml` 两个 seatunnel 服务的 `entrypoint` / `volumes` 移除引用；验证 PG 同步仍工作（openGauss 驱动不再冲突）
- [ ] **飞线 2**：删除 `datasource_service._build_safe_query` 及其调用，删除 `test_build_safe_query_*` 测试；MAIN 模板用默认 `SELECT *`；验证同步后 Iceberg 表时间戳列类型为 `timestamp(6) with time zone`（不是 varchar）
- [ ] **飞线 3**：评估改用 SeaTunnel `schema_save_mode=RECREATE_SCHEMA` + `data_save_mode=DROP_DATA` 替代手动 `drop_table_if_exists`；若可行则删除 `drop_table_if_exists` 方法及调用
- [ ] **飞线 4**：`ensure_namespace` / `drop_table_if_exists` 恢复用 pyiceberg（`self.catalog.create_namespace` / `drop_table`），删除 httpx 直连代码；验证 pyiceberg `load_catalog` 不再 401
- [ ] **飞线 5**：恢复 `iceberg.rest-catalog.warehouse=s3://ontology-warehouse`；评估重新启用 `vended-credentials-enabled=true` 并移除 Trino 侧静态 S3 凭证；验证 Trino 查询正常
- [ ] **飞线 6**：确认 metalake 里无 `default_catalog` / memory backend catalog 残留
- [ ] 端到端：PG `action_types`（含 timestamptz + jsonb）→ SeaTunnel → Iceberg → Trino `SELECT count(*)` 返回正确行数，且时间戳列在 Trino 里可 `CAST` 为 `timestamp`

---

## 1.3.0 发布跟踪

> 跟踪 Gravitino 1.3.0 正式发布状态。**发布 ≠ 可直接升级**——发布后仍需解决
> §阻塞问题 1–3（Trino connector 编译 / 密码字段 / client-java-runtime 发布），
> 再按 §升级步骤 Step 1–5 执行。

### 触发条件

- [ ] `docker pull apache/gravitino:1.3.0` 成功（Docker Hub 有 `1.3.0` 正式 tag，非 rc/SNAPSHOT）
- [ ] GitHub releases 出现 `v1.3.0`（非 prerelease）：https://github.com/apache/gravitino/releases
- [ ] `gravitino-client-java-runtime-1.3.0` 上架 Maven Central

任一未满足，均不可启动升级。三者全满足后，进入 §阻塞问题 逐项解决 → §升级步骤。

### 检查命令

```bash
# 1. Docker Hub 是否有 1.3.0 正式 tag
docker pull apache/gravitino:1.3.0

# 2. GitHub 最新正式 release
curl -s 'https://api.github.com/repos/apache/gravitino/releases?per_page=8' | \
  python3 -c "import sys,json; [print(r['tag_name'], r['published_at']) for r in json.load(sys.stdin) if not r['prerelease']]"

# 3. Maven Central 是否有 client-java-runtime 1.3.0
curl -s -o /dev/null -w '%{http_code}' \
  https://repo1.maven.org/maven2/org/apache/gravitino/gravitino-client-java-runtime/1.3.0/gravitino-client-java-runtime-1.3.0.jar
# 200 = 已发布；404 = 未发布
```

### 检查记录

| 日期 | 最新正式 release | 1.3.0 正式 tag | client-java-runtime 1.3.0 | 备注 |
|------|------------------|----------------|---------------------------|------|
| 2026-06-23 | v1.2.1 (2026-05-12) | ❌ 否（仅 rc1–rc3） | ❌ 否（最新 1.2.1） | branch-1.3 已 bump 1.3.1-SNAPSHOT (06-18)，源码就绪，待 PMC 公布 |

---

## 升级组件清单

| # | 组件 | 当前 | 目标 | 状态 |
|---|------|------|------|------|
| 1 | Gravitino 镜像 | `apache/gravitino:1.2.0` | `apache/gravitino:1.3.0-rc2` | ✅ 已验证可启动 |
| 2 | PG Entity Store DDL | 34 张表 (v1.2) | 40 张表 (v1.3) | ✅ 已执行 upgrade-1.2.0-to-1.3.0-postgresql.sql |
| 3 | docker-compose Gravitino | 自定义 command + volumes | 默认 entrypoint（1.3.0 内置 iceberg-rest aux service） | ✅ 已适配 |
| 4 | Gravitino JVM 内存 | 256m-512m | 512m-1024m | ✅ 已调整 |
| 5 | Trino 镜像 | `trinodb/trino:470` | `trinodb/trino:478`（或 473） | ⚠️ 编译依赖 |
| 6 | Trino Connector | `gravitino-trino-connector-469-472-1.2.0.jar` | `gravitino-trino-connector-473-478-1.3.0.jar` | ❌ 阻塞 |
| 7 | Trino JDBC | `trino-jdbc-469.jar` | `trino-jdbc-478.jar` | ❌ 依赖 #6 |

## 阻塞问题（✅ 已全部解除，见下文）

### 问题 1：Trino Connector 编译失败

**现象**：Gravitino 1.3.0-rc2 源码中的 `GravitinoMetadata478.java` 与 Trino 478 的 SPI 不兼容：

```
error: executeTableExecute(ConnectorSession,ConnectorTableExecuteHandle)
in GravitinoMetadata478 cannot implement executeTableExecute(...)
return type Map<String,Long> is not compatible with void
```

**尝试过的版本**：
- `v1.3.0-rc2` tag：同样问题
- `main` 分支：同样问题

**解决方向**：需要修正 `GravitinoMetadata478.java` 中 `executeTableExecute` 的返回值类型，使其匹配 Trino 478 的 `void` 签名。或者用 Trino 473（API 变更较小，可能兼容）。

### 问题 2：Gravitino 1.3.0 隐藏密码字段

**现象**：Gravitino 1.3.0 的 catalog API 不再返回 `jdbc-password` 和 `jdbc-user` 属性（安全改进），改用 Credential Vending 机制。Trino connector 需要从 `JdbcCredential` 获取密码，而非从 catalog properties 的 `jdbc-password` 映射。

**1.2.x connector 不可用**：1.2.x 版本通过 `jdbc-password → connection-password` 映射获取密码，在 1.3.0 Gravitino 下因密码缺失而失败：
```
Missing required property: connection-password
```

**1.3.0 connector 需要配套**：1.3.0 connector 的 `JDBCCatalogPropertyConverter.applyJdbcCredential()` 方法通过 Gravitino 的 Credential API 获取密码。

### 问题 3：client-java-runtime 未发布

Gravitino 1.3.0-rc2 的 `gravitino-client-java-runtime` 未上传到 Maven Central（最新为 1.2.1）。Trino connector 依赖此包，需从源码编译。

**已解决**：v1.4.0-SNAPSHOT（main 分支）编译成功：
```bash
cd /home/jason/code/gravitino
export JAVA_HOME=/home/jason/.local/jdks/jdk-24.0.2+12
./gradlew :clients:client-java-runtime:build -x test -PskipTrinoConnector=true
# 产物: clients/client-java-runtime/build/libs/gravitino-client-java-runtime-1.4.0-SNAPSHOT.jar
```

## 已完成的准备工作

以下工作已完成，升级时可直接复用：

### 1. PG 表结构升级（已执行，幂等）
```bash
# 从 1.3.0 镜像提取升级脚本并执行（原 rc2 已改为正式版）
docker run --rm --entrypoint cat apache/gravitino:1.3.0 \
  /opt/gravitino/scripts/postgresql/upgrade-1.2.0-to-1.3.0-postgresql.sql | \
  docker exec -i ontology-postgres psql -U ontology -d ontology
```

新增表：`entity_change_log`, `view_version_info`, `idp_user_meta`, `idp_group_meta`, `idp_user_group_rel`, `iceberg_cleanup_job`
（实测当前 gravitino_store 已 40 张表，6 个新表均存在）

### 2. ❌ Gravitino Catalog 类型转换配置（作废——该功能不存在）

> ⚠️ **2026-06-28 核实作废**：原计划在 catalog properties 里配
> ```json
> { "type-converter.enabled": "true",
>   "type-converter.custom.mapping": "jsonb=JSON,json=JSON,uuid=VARCHAR,..." }
> ```
> 实测 1.3.0（及 main 分支）**不存在 `type-converter.*` 这套配置**——
> 官方 trino-connector/configuration.md、jdbc-postgresql-catalog.md 均无此项，
> 源码 / 镜像 jar 也无对应入口。创建带这些属性的 `pg` catalog 不报错（被静默忽略），
> 但 `DESCRIBE pg.public.action_types` 仍报 `external(jsonb)`。
> 详见下文「❌ jsonb 问题在 1.3.0 仍未解决」。

### 3. ❌ Trino gravitino.properties 的 type-mapping 配置（作废——1.3.0 已移除）

> ⚠️ **2026-06-28 核实作废**：原配置
> ```properties
> gravitino.type-mapping.enabled=true
> gravitino.push-down-type-conversion=true
> gravitino.strict-type-check=false
> ```
> 在 1.3.0 trino-connector 的 configuration.md 里**已全部移除**（1.3.0 只保留 connector.name /
> gravitino.metalake / gravitino.uri / trino.jdbc.* / refresh-interval / skip-version-validation /
> skip-catalog-patterns / use-single-metalake 等配置项）。
> 实测：保留这些旧配置项不会导致启动失败（1.3.0 connector 静默忽略未知属性），
> 但对 jsonb 查询**完全无效**。
>
> **待办**：这些配置项应从 `config/trino/catalog/gravitino.properties` 删除（避免误导）。
> 当前暂保留是因为删除需重建 trino 验证，且不影响功能。

### 4. docker-compose Gravitino 适配
- 镜像改为 `apache/gravitino:1.3.0-rc2`
- 删除自定义 `command`（1.3.0 使用默认 entrypoint）
- 删除 `volumes` 中的 iceberg-rest-server.conf（1.3.0 内置 aux service）
- 移除端口 `9001`（iceberg-rest 改为内置，不需要单独端口）
- JVM 内存增加到 512m-1024m

## 升级步骤（1.3.0 已就绪，可直接执行）

### Step 0：备份数据库（官方 how-to-upgrade.md 强制要求）
```bash
docker exec ontology-postgres pg_dump -U ontology -d ontology -n gravitino_store -Fc -f /tmp/gravitino_backup.dump
docker cp ontology-postgres:/tmp/gravitino_backup.dump ./gravitino_backup_$(date +%Y%m%d).dump
```

### Step 1：确认镜像与依赖就绪
```bash
docker pull apache/gravitino:1.3.0   # 本地已有（4ff340f11606，2026-06-28 push）
for a in gravitino-client-java-runtime gravitino-trino-connector-473-478; do
  curl -s -o /dev/null -w "$a 1.3.0 -> %{http_code}\n" \
    https://repo1.maven.org/maven2/org/apache/gravitino/$a/1.3.0/$a-1.3.0.jar
done
# 两者均应 200
```

### Step 2：升级 PG Entity Store DDL（如未执行）
> 本项目 entity store 已升级到 1.3.0 表结构（40 张表），可跳过。新环境执行：
```bash
docker run --rm --entrypoint cat apache/gravitino:1.3.0 \
  /opt/gravitino/scripts/postgresql/upgrade-1.2.0-to-1.3.0-postgresql.sql | \
  docker exec -i ontology-postgres psql -U ontology -d ontology
```

### Step 3：下载 Trino connector 产物（无需编译）
```bash
TRINO_PLUGIN=/home/jason/code/gaia/config/trino/gravitino
mkdir -p "$TRINO_PLUGIN" && cd "$TRINO_PLUGIN"
# 1.3.0 connector 主包（289KB jar，非 tar.gz）
curl -LO https://repo1.maven.org/maven2/org/apache/gravitino/gravitino-trino-connector-473-478/1.3.0/gravitino-trino-connector-473-478-1.3.0.jar
# client-java-runtime（connector 依赖）
curl -LO https://repo1.maven.org/maven2/org/apache/gravitino/gravitino-client-java-runtime/1.3.0/gravitino-client-java-runtime-1.3.0.jar
# trino-jdbc-478.jar 从 Trino 官方 Maven 下载
# 清理旧 1.2.0 产物：rm -f gravitino-trino-connector-469-472-1.2.0.jar trino-jdbc-469.jar
```

### Step 4：更新 docker-compose.yml
按上文「1.3.0 Gravitino 段参考配置」修改 gravitino 服务：
- 镜像 `1.2.0` → `1.3.0`
- volumes/command 路径 `/root/gravitino` → `/opt/gravitino`
- JVM `-Xms256m -Xmx512m` → `-Xms512m -Xmx1024m`
- trino 镜像 `470` → `478`

### Step 5：重建并验证
```bash
docker compose up -d --force-recreate gravitino trino
curl -sf http://localhost:8090/api/health && echo " <- gravitino主服务"
curl -sf -o /dev/null -w "%{http_code}" http://localhost:9001/iceberg/v1/config && echo " <- iceberg-rest"
# jsonb 查询回归（升级核心目标）
docker exec ontology-trino trino --execute "DESCRIBE pg.public.action_types"
# 期望：parameters/rules/submission_criteria 显示为 JSON，不报 external(jsonb)
```

### Step 6：执行 pgnative workaround 回归 checklist
升级成功后，按上文「1.3.0 升级后的回归 checklist」逐项移除 pgnative 飞线。

## 降级回退

如果升级失败，回退步骤：
```bash
# 1. docker-compose 恢复 1.2.0 配置
# 2. 恢复 Trino connector jar 到 1.2.0 版本
# 3. docker compose up -d --force-recreate gravitino trino
# 4. 重建 pg catalog（type-converter 属性会在 1.2.0 中被忽略）
```

PG 表结构已升级到 1.3.0（40 张表），1.2.0 Gravitino 向后兼容，无需回退 DDL。
