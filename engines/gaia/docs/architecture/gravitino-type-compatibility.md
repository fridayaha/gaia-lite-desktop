# Gravitino 对数据源特殊字段类型的兼容机制

> 2026-06-28 调研整理。背景：Gravitino 1.3.0 升级后，PostgreSQL `jsonb` 类型查询仍报
> `Unsupported gravitino datatype: external(jsonb)`。本文档系统梳理 Gravitino 的类型兼容
> 体系，解释为什么 MySQL 的 `JSON` 能查、PG 的 `jsonb` 不能查，以及各种数据源特殊类型
> 的处理差异。
>
> 相关文档：[`docs/bugfix/gravitino-1.3.0-upgrade.md`](../bugfix/gravitino-1.3.0-upgrade.md)

---

## 一、类型系统的三层架构

Gravitino 的类型处理分三层，特殊类型（jsonb/geometry/数组等）的兼容性由这三层共同决定：

```
┌─────────────────────────────────────────────────────────────┐
│  Trino 引擎层 (io.trino.spi.type.Type)                       │
│  VARCHAR / JSON / INTEGER / TIMESTAMP / ...                  │
└───────────────────────────▲─────────────────────────────────┘
                            │ ② trino-connector 侧
                            │   DataTypeTransformer.getTrinoType()
┌─────────────────────────────────────────────────────────────┐
│  Gravitino 标准类型层 (org.apache.gravitino.rel.types.Type)  │
│  StringType / IntegerType / ExternalType / ...              │
└───────────────────────────▲─────────────────────────────────┘
                            │ ① catalog 侧 (服务端)
                            │   TypeConverter.toGravitino()
┌─────────────────────────────────────────────────────────────┐
│  数据源原生类型层                                            │
│  PG: jsonb/uuid/geometry  MySQL: json/enum/geometry        │
│  Doris: ...  Iceberg: ...  Hive: ...                        │
└─────────────────────────────────────────────────────────────┘
```

### 第 ① 层：catalog 侧 TypeConverter（服务端，每个 catalog 一个）

- 接口：`org.apache.gravitino.connector.DataTypeConverter<ToType, FromType>`
- 实现：每个 catalog 有自己的 `*TypeConverter`，双向转换
  - `toGravitino(typeBean)`：数据源类型 → Gravitino 类型
  - `fromGravitino(type)`：Gravitino 类型 → 数据源类型（写回时用）
- **关键设计**：能识别的类型映射成 Gravitino 标准类型；**不能识别的统一映射成 `ExternalType(catalogString)`**，原样保留数据源类型名。

### 第 ② 层：trino-connector 侧 DataTypeTransformer（客户端）

- 基类：`GeneralDataTypeTransformer`
  - `getTrinoType(gravitinoType)`：Gravitino 类型 → Trino 类型
  - **default 分支直接抛 `GRAVITINO_UNSUPPORTED_GRAVITINO_DATATYPE`**（这就是看到的报错）
- 每个 catalog 在 trino-connector 侧可继承 `GeneralDataTypeTransformer` 做 override：
  - 处理精度调整（timestamp/time 的精度对齐）
  - **处理 ExternalType**（关键差异化能力，见下文）

---

## 二、ExternalType 的设计意图

`Types.ExternalType`（自 0.6.0 引入，PR #3501）：

```java
/** Represents a type that is defined in an external catalog. */
public static class ExternalType implements Type {
  private final String catalogString;  // 原样保留数据源类型名，如 "jsonb"
  public static ExternalType of(String catalogString) { ... }
  public String catalogString() { return catalogString; }
  public Name name() { return Name.EXTERNAL; }
  public String simpleString() { return String.format("external(%s)", catalogString); }
}
```

**设计意图**（官方 `jdbc-postgresql-catalog.md` 第 120 行）：
> 自 0.6.0 起，未列出的类型映射为 **[External Type]**，表示一个「无法解析的数据类型」。

即：Gravitino **不试图把所有数据源的私有类型都标准化**，而是用 ExternalType 作为「保底容器」原样携带类型名，交给下游引擎自行决定能否处理。这是个**务实的兼容设计**——避免 Gravitino 主类型系统膨胀，同时不丢信息。

**代价**：下游引擎（如 Trino connector）若不专门处理 ExternalType，就会在 `GeneralDataTypeTransformer.getTrinoType()` 的 default 分支抛错。

---

## 三、为什么 MySQL 的 JSON 能查、PG 的 jsonb 不能查

### 核心差异：trino-connector 侧是否有 ExternalType 映射表

**MySQL 有**（PR #7578，issue #7338）：
- `trino-connector/.../jdbc/mysql/MySQLExternalDataType.java` —— 一个枚举映射表
- `MySQLDataTypeTransformer.getTrinoType()` override 了 `EXTERNAL` 分支：

```java
} else if (Name.EXTERNAL == type.name()) {
  String catalogString = ((Types.ExternalType) type).catalogString();
  return MySQLExternalDataType.safeValueOf(catalogString).getTrinoType();
}
```

`MySQLExternalDataType` 枚举（节选）：

| MySQL 类型名 | Trino 类型 |
|-------------|-----------|
| `json` | `JSON_TYPE` |
| `enum` / `set` / `tinytext` / `mediumtext` / `longtext` | `VARCHAR` |
| `geometry` / `varbinary` / `blob` 系列 | `VARBINARY` |
| `mediumint` / `year` / ... | `INTEGER` / `DATE` / ... |
| `unknown`（兜底） | `VARCHAR` |

`safeValueOf()` 找不到匹配时返回 `UNKNOWN → VARCHAR`，**永不抛错**。

**PostgreSQL 没有**：
- trino-connector 侧**没有** `PostgreSqlExternalDataType` 枚举
- `PostgreSQLDataTypeTransformer.getTritoType()` 只 override 了 STRING/TIMESTAMP/TIME 三个分支，**没有 override EXTERNAL 分支**
- 于是 PG 的 `jsonb`（ExternalType）走 `super.getTrinoType()` → default → 抛 `Unsupported gravitino datatype: external(jsonb)`

### 为什么 PG 没做？（社区状态）

- **issue #9892「[FEATURE] JDBC 数据源需要支持 JSON 字段类型」状态：open** —— 社区知道这个需求，尚未实现
- PG 的 `PostgreSqlTypeConverter`（服务端）的 switch case 也没有 `JSONB`/`JSON`，走 default → ExternalType
- 要彻底解决需要两处改动（任一即可让 Trino 能查）：
  1. **服务端**：`PostgreSqlTypeConverter` 增加 `case JSONB:/case JSON:` → `Types.StringType.get()` 或新增 `JsonType`
  2. **connector 侧**：新增 `PostgreSqlExternalDataType` 枚举 + `PostgreSQLDataTypeTransformer` override EXTERNAL 分支（仿 MySQL）

方案 2 改动小、与 MySQL 一致，是社区最可能的实现路径。

---

## 四、各数据源 TypeConverter 的特殊类型处理对照

> 来源：`/home/jason/code/gravitino` main 分支源码（含 1.3.0 全部代码）

| Catalog | TypeConverter | trino-connector 侧 ExternalDataType 枚举 | 特殊类型处理 |
|---------|---------------|------------------------------------------|------------|
| **MySQL** | `MysqlTypeConverter` | ✅ `MySQLExternalDataType` | json/enum/set/geometry/text 系列/blob 系列全映射，兜底 VARCHAR |
| **PostgreSQL** | `PostgreSqlTypeConverter` | ❌ 无 | jsonb/json/geometry/数组 走 ExternalType → connector 抛错 |
| **Doris** | `DorisTypeConverter` | ❌ 无 | 同上，未识别类型走 ExternalType |
| **StarRocks** | `StarRocksTypeConverter` | ❌ 无 | 同上 |
| **ClickHouse** | `ClickHouseTypeConverter` | ❌ 无 | 同上（contrib） |
| **Iceberg** | `IcebergDataTypeConverter` | N/A（无 ExternalType 概念） | Iceberg 类型本身是标准化的，直接映射 |
| **Hive** | `HiveDataTypeConverter` | N/A | 同上 |
| **Glue** | `GlueTypeConverter` | N/A | 同上 |

**结论**：只有 MySQL 在 trino-connector 侧做了 ExternalType 兜底映射。其余 JDBC catalog（PG/Doris/StarRocks/ClickHouse）的特殊类型在 Trino 里都会抛 `Unsupported gravitino datatype: external(...)`。

---

## 五、`trino.bypass.*` 机制为何对 PG jsonb 无效

Gravitino trino-connector 支持用 `trino.bypass.` 前缀透传配置给底层 Trino connector（如 `trino.bypass.unsupported-type-handling=CONVERT_TO_VARCHAR`、`trino.bypass.jdbc-types-mapped-to-varchar=jsonb`）。

**理论上**：底层 Trino postgresql connector 原生支持 `JSONB → JSON` 映射，bypass 应该能让它处理 jsonb。

**实际**：无效。根因——
1. Gravitino trino-connector 在 **`GeneralDataTypeTransformer.getTrinoType()` 阶段**（`GravitinoMetadata.getTableMetadata`）就拿到 ExternalType 并抛错
2. 这个阶段发生在**把元数据交给底层 Trino postgresql connector 之前**
3. bypass 参数传给了底层 connector，但**请求根本走不到底层 connector**

实测验证（1.3.0，2026-06-28）：给 `pg` catalog 加 `trino.bypass.unsupported-type-handling=CONVERT_TO_VARCHAR` + `trino.bypass.jdbc-types-mapped-to-varchar=jsonb,json`，`DESCRIBE pg.public.action_types` 仍报 `external(jsonb)`。1.2.0 与 1.3.0 行为一致。

**这是 Gravitino 架构层面的限制**：只要 catalog 侧把类型映射成 ExternalType、且 connector 侧没有对应的 ExternalDataType 映射表，bypass 就救不了。

---

## 六、本项目的对策

| 数据源 | 特殊类型 | 对策 |
|--------|---------|------|
| PostgreSQL `jsonb`/`json` | Trino 原生 `postgresql` connector 直连（`pgnative` catalog），绕过 Gravitino 类型层 | 已实施，详见 [bugfix 文档](../bugfix/gravitino-1.3.0-upgrade.md)「1.2.0 下的替代方案」 |
| PostgreSQL `uuid` | 本项目按规范用 `varchar(32)` 存储，无 PG 原生 uuid 列 | 无需处理 |
| PostgreSQL `geometry`/`geography` | 当前未使用 | 暂不处理 |
| Iceberg 表 | 无 ExternalType 问题，走 Gravitino REST 正常 | 无需处理 |

**长期路径**：跟踪 issue #9892，待社区实现 PG 的 ExternalType 映射（或服务端 jsonb→StringType），即可移除 pgnative workaround，回归统一 `pg` catalog。

---

## 七、关键源码索引

> 路径相对 `/home/jason/code/gravitino`（main 分支，含 1.3.0 代码）

| 文件 | 作用 |
|------|------|
| `api/src/main/java/org/apache/gravitino/rel/types/Types.java:1333` | `ExternalType` 定义 |
| `connector/src/main/java/org/apache/gravitino/connector/DataTypeConverter.java` | 类型转换接口 |
| `catalogs/catalog-jdbc-postgresql/.../PostgreSqlTypeConverter.java:104` | PG default → ExternalType（jsonb 走这里） |
| `catalogs/catalog-jdbc-mysql/.../MysqlTypeConverter.java:104` | MySQL default → ExternalType（json 也走这里） |
| `trino-connector/trino-connector/.../GeneralDataTypeTransformer.java:167` | default 抛 `Unsupported gravitino datatype` |
| `trino-connector/trino-connector/.../jdbc/mysql/MySQLDataTypeTransformer.java:60` | MySQL override EXTERNAL 分支 |
| `trino-connector/trino-connector/.../jdbc/mysql/MySQLExternalDataType.java` | MySQL 外部类型映射表（含 JSON→JSON_TYPE） |
| `trino-connector/trino-connector/.../jdbc/postgresql/PostgreSQLDataTypeTransformer.java` | PG **未** override EXTERNAL 分支（根因） |

**社区 issue**：
- [#9892](https://github.com/apache/gravitino/issues/9892) [FEATURE] JDBC 数据源支持 JSON 字段类型（open）
- [#7338](https://github.com/apache/gravitino/issues/7338) / PR [#7578](https://github.com/apache/gravitino/pull/7578) MySQL external type 支持（已合并，1.2.0+）
- [#3500](https://github.com/apache/gravitino/issues/3500) / PR [#3501](https://github.com/apache/gravitino/pull/3501) ExternalType 引入（0.6.0）
