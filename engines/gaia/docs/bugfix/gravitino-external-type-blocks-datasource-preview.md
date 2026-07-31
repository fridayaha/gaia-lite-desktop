# Gravitino external(...) 类型阻塞外部数据源预览

## 现象

在「数据源 → 浏览 Schema → 预览数据」时，对于**外部 PostgreSQL 数据源**（如 k3s 环境的 `xiaoling`），含 `jsonb`/`uuid`/`inet` 等列的表，预览返回：

> ⚠ 该表含有 Gravitino 无法解析的列类型（如 jsonb/uuid/inet），暂时无法预览数据。可先创建同步任务将数据落入 Iceberg 后再查看。

文案出自 `src/ontology/services/datasource_service.py:595`，是 `sample_data` 的最终降级兜底分支。

用户实测：把 `xiaoling` 库某张表的 `model_instance` 列从原类型（疑似 jsonb）改成 `text` 后，**仍然查不出数据**。

## 根因（与项目业务 PG 的 pgnative workaround 是两回事）

链路：

```
前端预览 → DataSourceService.sample_data
  → Trino: SELECT * FROM "<catalog>"."<schema>"."<table>" LIMIT 10
  → <catalog> 是 Gravitino 动态注册的 JDBC catalog（外部数据源）
  → Gravitino Trino Connector 在 query planning 阶段整表校验 schema
  → 遇到 external(jsonb)/external(uuid)/external(inet) → 抛 GRAVITINO_UNSUPPORTED_GRAVITINO_DATATYPE
  → 降级：过滤掉 external 列，只 SELECT 安全列重试
  → 仍失败（connector 整表校验，列裁剪发生在校验之后）→ 抛 ValidationError 兜底文案
```

**关键边界**：这与 `docs/bugfix/gravitino-1.3.0-upgrade.md` 里已记录的 jsonb 问题是**同一个 Gravitino 缺陷**，但**影响范围不同**：

| 维度 | 项目业务 PG（ontology 库） | 外部数据源 PG（如 xiaoling） |
|------|--------------------------|----------------------------|
| catalog 来源 | Trino 静态挂载的 `pgnative.properties`（原生 postgresql connector） | Gravitino REST 动态注册的 JDBC catalog |
| jsonb 预览 | ✅ 已有 pgnative workaround 绕过 | ❌ **没有原生 connector 兜底，本问题** |
| 权限/RBAC | 绕过 Gravitino | 走 Gravitino |
| 触发方式 | 业务代码查 ontology 业务表 | 用户在数据源管理页预览外部表 |

`register_jdbc_catalog`（`src/ontology/layers/catalog/gravitino_registry.py:348`）只能往 Gravitino 注册 catalog，**没有**给外部数据源额外挂一个 Trino 原生 catalog 的机制。所以 pgnative workaround 救不了外部数据源。

## 待验证假设（用户「改成 text 仍查不出」的两种可能根因）

> ⚠️ 必须实测确认是哪一种，处理方向完全不同。诊断命令见下「诊断步骤」。

### 假设 A：Gravitino schema 缓存未刷新

Gravitino JDBC catalog 首次访问表时会缓存 schema 元数据。用户改了源 PG 的列类型，但 Gravitino 那边记录的可能还是旧类型（`external(jsonb)`）。

- 判据：`DESCRIBE xiaoling.<schema>.<table>` 显示 `model_instance` 仍为 `external(jsonb)` 或直接报错；但源 PG 里 `\d` 已经是 `text`。
- 处理方向：重建/刷新 Gravitino catalog（删 catalog 重注册，或重启 Gravitino，或等其 schema 缓存 TTL）。

### 假设 B：表里还有其他 external(...) 列

`model_instance` 改成 text 了，但同一张表里还有别的 `uuid`/`inet`/`jsonb` 列。代码逻辑是只要表里**还有任何一个** `external(...)` 列，整表 `SELECT *` 和 `sample_data_columns` 都会失败（connector 在 planning 阶段整表校验，列裁剪发生在校验之后）。

- 判据：`DESCRIBE` 能成功，`model_instance` 显示 `text`，但其他列显示 `external(xxx)`。
- 处理方向：短期——把该表所有 external 列都改 text（治标不治本，用户不该被要求这么干）；中期——走方案 2 给外部 PG 数据源也挂原生 catalog。

## 诊断步骤（在 k3s 环境执行）

```bash
# 1. 确认 xiaoling catalog 的 provider / 配置
kubectl -n gaia exec -it deploy/gaia-api -- \
  curl -s http://gravitino:8090/api/metalakes/ontology/catalogs/xiaoling | python3 -m json.tool

# 2. 让 Trino DESCRIBE 这张表，看每列类型（关键：model_instance 现在显示什么，还有没有别的 external 列）
kubectl -n gaia exec -it deploy/gaia-api -- \
  curl -s -X POST http://trino:8080/v1/statement \
    -H 'Content-Type: text/plain' -H 'X-Trino-User: gaia' \
    -d 'DESCRIBE xiaoling.<schema>.<table>'

# 3. 直接 SELECT * 看原始报错（绕过我们的降级逻辑）
kubectl -n gaia exec -it deploy/gaia-api -- \
  curl -s -X POST http://trino:8080/v1/statement \
    -H 'Content-Type: text/plain' -H 'X-Trino-User: gaia' \
    -d 'SELECT * FROM xiaoling.<schema>.<table> LIMIT 1'

# 4. 对照：源 PG 里该列真实类型（确认改 text 已落库）
kubectl -n gaia exec -it <pg-pod> -- psql -U <user> -d xiaoling -c '\d <schema>.<table>'
```

第 2 步输出是分水岭：`model_instance` 显示 `external(jsonb)` → 假设 A；显示 `text` 但有别的 `external(xxx)` → 假设 B。

## 解决方案（按优先级）

### 方案 0：修误导文案（短期，立即可做）

当前文案「Gravitino 无法解析的列类型」暴露了实现细节，用户不知道 Gravitino 是什么。按项目规范（用户文案不暴露实现细节），改为用户可理解的说法：

> ⚠ 该表包含暂不支持的列类型，无法直接预览。可先创建同步任务将数据落入托管存储后再查看。

### 方案 1：Gravitino catalog schema 刷新机制（若假设 A 成立）

如果根因是缓存，需要：
- 确认 Gravitino 1.3.0 JDBC catalog 是否有 schema 失效/刷新 API
- 或在 `datasource_service` 改完源表类型后提供「刷新 schema」按钮（删 catalog 重注册）

### 方案 2：外部数据源也挂 Trino 原生 catalog（中期，真正解决）

复用 pgnative workaround 思路：用户接入外部 PG 数据源时，**除了**在 Gravitino 注册 catalog（管物理资产登记/权限），**额外**在 Trino 侧动态注册一个原生 `postgresql` catalog（只用于纯预览这种只读场景）。

- 代价：Trino 原生 catalog 的动态注册需要写 catalog properties 文件 + reload（Trino 不支持纯 REST 动态建 catalog，需 `ADD CATALOG` 或文件 + 重载）
- 收益：彻底绕过 Gravitino 类型转换层，jsonb/uuid/inet 全部可预览
- 边界：仅限 PG/MySQL 这类有原生 Trino connector 的 provider；lakehouse/kafka/fileset 仍只能走 Gravitino

### 方案 3：DESCRIBE 降级路径（待实测）

代码注释（`datasource_service.py:571`）称 `DESCRIBE` 同样会因 `external(...)` 失败，所以走了 Gravitino REST API。但 **`SHOW COLUMNS FROM`** 走的是 connector 的 `listTableColumns`，与 `DESCRIBE` 实现不同——值得实测在 Gravitino connector 上对含 external 类型表的行为。若 `SHOW COLUMNS` 能返回，可作为第三条降级路径（拼出安全列名后用子查询绕过整表校验，如 `SELECT col1,col2 FROM (SELECT col1,col2 FROM tbl) LIMIT 10` —— 但这仍受 planning 阶段整表校验限制，可能无效）。

### 方案 4：跟踪社区

Gravitino 的 PG `PostgreSqlTypeConverter` 把 `jsonb`/`json`/`uuid`/`inet` 等映射为 `ExternalType`，由 `GeneralDataTypeTransformer.getTrinoType()` 在交给底层 Trino connector 之前直接拒绝。根因详见 `docs/bugfix/gravitino-1.3.0-upgrade.md`（1.3.0 仍未修复）。

- 社区修复方向：`PostgreSqlTypeConverter` 增加 `jsonb → Types.StringType` 或新增 `JsonType`；或在 connector 层对 `ExternalType` 改为 CONVERT_TO_VARCHAR 兜底而非硬拒绝。
- 跟踪方式：关注 Gravitino release notes / `PostgreSqlTypeConverter` 相关 PR。修复后可移除 pgnative workaround + 本问题的降级逻辑，回归统一 catalog。

## 相关代码位置

- 降级兜底文案：`src/ontology/services/datasource_service.py:595`
- `sample_data` / `sample_data_columns`：`src/ontology/services/datasource_service.py:533-600`
- Trino 查询：`src/ontology/layers/engine/trino_query_engine.py:197-251`
- Gravitino 动态注册 catalog：`src/ontology/layers/catalog/gravitino_registry.py:348`（`register_jdbc_catalog`）
- 列类型格式化：`src/ontology/layers/catalog/gravitino_registry.py:629`（`_format_gravitino_column_type`）
- pgnative workaround（仅项目业务 PG）：`docs/bugfix/gravitino-1.3.0-upgrade.md` + `config/trino/catalog/pgnative.properties`

## 状态

- [x] 已记录问题与根因
- [ ] 已实测确认假设 A / B（待用户提供 `DESCRIBE` 输出）
- [ ] 方案 0 修误导文案
- [ ] 方案 2 外部数据源挂原生 catalog（若确认需中期解决）
- [ ] 跟踪 Gravitino 社区修复（见 `gravitino-1.3.0-upgrade.md` 回归 checklist）
