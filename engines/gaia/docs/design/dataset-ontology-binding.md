# 数据集与本体关联设计 - 术语统一与全链路实施方案

> **版本**: v1.0
> **日期**: 2026-06-18
> **对标系统**: Palantir Foundry (Dataset 层 Virtual Table + Ontology 层 Datasource Mapping)
> **核心目标**: 统一全局术语体系(Managed / Virtual),打通"本体对象 → 数据集"的关联链路,落地 Palantir 式的"先登记、后绑定、单向引用"模型。
>
> **前置文档**:
> - [架构设计](../architecture/architecture_plan.md)
> - [数据层设计](./data-layer-design.md) - 本文档纠正其中 Virtual Table 的错误定义
> - [实现状态路标](../architecture/implementation-status.md)
>
> **本文件性质**: 供开发直接实施的设计基准。所有改动项均带精确文件定位与验收标准。

---

## 目录

- [〇、背景与问题](#〇背景与问题)
- [一、统一术语体系(全局基准)](#一统一术语体系全局基准)
- [二、概念模型与分层边界](#二概念模型与分层边界)
- [三、后端实施方案](#三后端实施方案)
- [四、前端实施方案](#四前端实施方案)
- [五、数据迁移](#五数据迁移)
- [六、实施顺序与依赖](#六实施顺序与依赖)
- [七、验收标准](#七验收标准)
- [八、未覆盖与后续](#八未覆盖与后续)

---

## 〇、背景与问题

### 0.1 现状诊断

当前"数据集 ↔ 本体"关联与"虚拟表"概念存在三类问题:

**问题 A:术语污染,且与 Palantir 官方语义冲突**

`docs/design/data-layer-design.md` 把"虚拟表"定义为 Gravitino SQL View(`is_view` 标记),并断言"MySQL/PG 等关系库在 Palantir 也不支持虚拟表--必须先同步到 Iceberg"。这与 Palantir 官方定义**完全相反**:

> Palantir Foundry: A Virtual Table is a pointer to a table in a source system **outside of Foundry**. 它正是外部关系库(Snowflake/MySQL/BigQuery...)的联邦代理,不落地存储。

代码中"联邦表""虚拟表""Gravitino View"三个词混用,指代不清。

**问题 B:数据集关联前端是空壳**

- `CreateObjectWizard` Step 0 用写死的 `MOCK_DATASETS`,未接 `listDatasets()`
- 选中数据集后只存 `datasource_path: string`,提交 payload 时**完全丢弃**,不写 `physical_mapping`
- `PropertyDraft.source_column` 类型存在但 UI 不渲染、不提交
- 编辑回填不还原 `physical_mapping`,编辑会抹掉已有关联
- 对象详情/列表无数据集关联展示

**问题 C:Virtual Table(Palantir 语义)链路缺失**

- 外部数据源表(`DataSourceService.explore/describe_table` 能浏览)没有"登记"动作,无法成为可被本体引用的资源
- `is_view` 字段恒为 false,从未被设为 true
- `VirtualTableService` / `GravitinoRegistry.create_view` 是"写了从未接线"的死代码(无 route、无调用方),且其语义(查自有 catalog 的 SQL view)与 Palantir Virtual Table 冲突

### 0.2 本文档要解决什么

1. 建立全局统一的术语体系(§一),作为后续一切工作的基准
2. 定义清晰的概念模型与分层边界(§二)
3. 给出后端(§三)、前端(§四)、迁移(§五)的精确实施方案,每项含文件定位与验收标准

> **文档校准已完成**:全局术语统一(PHYSICAL→MANAGED、废弃"联邦表"/"虚拟表=Gravitino View"、Gravitino View 死代码标注)已在 12 份现有文档中落地,不再列为独立实施项。
4. 排出实施顺序(§六)与验收清单(§七)

### 0.3 本文档不解决什么

- **Action 闭环**(OutboxExecutor / CDC pipeline / WriteBackManager 接线):明确延后,见 [implementation-status.md](../architecture/implementation-status.md) P0 #1。
- **Doris 索引加速接通**:依赖本文档的"数据集关联"落地后才能正确取索引字段,作为本文档的下游工作,见 §八。
- **Foundry View 子类型实现**(`is_view=true` 的 Managed 派生视图):字段保留占位,实现延后。

---

## 一、统一术语体系(全局基准)

### 1.1 术语表

全局只用以下一套术语,**废弃所有其他叫法**。

| 统一术语 | 英文 | 含义 | `kind` / `storage_type` 取值 | 数据存哪 | 可写 |
|---------|------|------|------------------------------|---------|------|
| **托管表** | Managed Table | Gaia catalog 托管、数据落地 Iceberg 的表(由 sync task 从数据源同步而来) | `MANAGED` | Iceberg | 是 |
| **虚拟表** | Virtual Table | 外部数据源表的代理指针,不落地,Trino 联邦查询 | `VIRTUAL` | 外部系统 | 否 |

### 1.2 废弃叫法清单

以下叫法在代码、文档、注释中**全部替换或删除**:

| 废弃叫法 | 替换为 | 说明 |
|---------|--------|------|
| ❌ 联邦表 | Virtual Table | "联邦"是查询**机制**(Trino 的能力),不作为资源类型名 |
| ❌ 外部联邦表指针 | Virtual Table | 统一用 Palantir 官方术语 |
| ❌ 虚拟表 = Gravitino View | (删除该定义) | 这是文档现有错误定义,与 Palantir 相反 |
| ❌ `PHYSICAL`(作为 storage_type / kind 值) | `MANAGED` | 对齐 Palantir Managed 术语 |
| ❌ 实体对象 / 实体表 | 托管对象 / 托管表 | 文案统一 |

### 1.3 保留的机制名(不属资源类型)

- **联邦查询(Federated Query)**:指 Trino 跨 catalog 查询的**能力/机制**。代码注释中 `trino_query_engine.py` 的 "federated query" 描述属此类,**保留**。它不是资源类型,不与 Virtual Table 混淆。

### 1.4 `is_view` 字段语义收窄

`is_view` 字段**保留**,但语义收窄为:

> **Managed Table 的 Foundry View 子类型标记**。仅当 `kind=MANAGED` 时可能有意义;当前该子类型**未实现**,`is_view` 恒为 `false`。

| `kind` | `is_view` | 含义 |
|--------|-----------|------|
| `MANAGED` | `false`(当前唯一值) | 托管表,数据落地 Iceberg |
| `MANAGED` | `true`(未来) | Foundry View,不存文件的派生数据集(未实现) |
| `VIRTUAL` | `false`(恒) | Virtual Table,外部联邦代理,不适用此标记 |

**砍掉 Gravitino SQL View 这条线后**,`is_view` 不再表示"Gravitino View",文档与代码注释中所有"`is_view` = Gravitino View"的表述必须纠正。

---

## 二、概念模型与分层边界

### 2.1 分层(对标 Palantir)

```
┌─ Ontology 语义层 ──────────────────────────────────────┐
│  ObjectType (storage_type: MANAGED | VIRTUAL)          │
│    └─ Property.physical_mapping → Dataset (单向引用)    │
│                                                         │
│  规则:本体只记录数据集标识,数据集不感知本体            │
│  规则:MANAGED 对象才能写入;VIRTUAL 对象只读           │
└──────────────────────┬──────────────────────────────────┘
                       │ Datasource Mapping(绑定)
┌──────────────────────▼──────────────────────────────────┐
│─ Dataset 资源层 ────────────────────────────────────────│
│  DatasetGovernance (kind: MANAGED | VIRTUAL)            │
│                                                         │
│  ├─ MANAGED: 托管表                                      │
│  │   · 数据在 Iceberg(sync task 从数据源同步落地)      │
│  │   · storage_location = s3://... 或 Iceberg 表路径     │
│  │   · is_view: Managed 子类型标记(当前恒 false)       │
│  │   · 可被 MANAGED 对象绑定,支持写入                   │
│  │                                                       │
│  └─ VIRTUAL: 虚拟表                                      │
│      · 外部数据源表的代理指针,不落地                     │
│      · storage_location = "catalog.schema.table" 三段式  │
│      · data_source_api_name = 来源数据源                 │
│      · Trino 联邦查询实时拉外部数据,只读                 │
│      · 可被 VIRTUAL 对象绑定,不支持写入                  │
└──────────────────────────────────────────────────────────┘
                       ▲
                       │ 登记(Register)
┌──────────────────────┴──────────────────────────────────┐
│─ DataSource 接入层 ─────────────────────────────────────│
│  DataSource + credential + Gravitino JDBC catalog       │
│  · explore / describe_table: 浏览外部库表、懒加载列      │
│  · sync task: 把外部表同步成 MANAGED 托管表              │
│  · register virtual table: 把外部表登记成 VIRTUAL 虚拟表 │
└──────────────────────────────────────────────────────────┘
```

### 2.2 三条核心原则(贯穿前后端)

**原则 1:单向引用**
本体记录数据集标识(`PhysicalColumnRef` 挂在 `PropertyDef` 上),数据集侧**不得**有任何 `object_type_id` / `ontology_id` 反向引用。

> 当前实现已满足:`DatasetGovernanceModel`(`datasets` 表)无任何本体外键。保持。

**原则 2:先登记后绑定**
- MANAGED 托管表:由 sync task 同步后自动产生 `DatasetGovernance(kind=MANAGED)` 记录
- VIRTUAL 虚拟表:由"登记虚拟表"动作显式产生 `DatasetGovernance(kind=VIRTUAL)` 记录
- 本体对象只能绑定**已登记**的数据集,不能凭空创建数据集

> 对标 Palantir:"Ontology Manager 仅能选择并绑定已存在于目录中的资源,不能创建虚拟表。"

**原则 3:MANAGED 可写,VIRTUAL 只读**
- `storage_type=MANAGED` 的对象:支持 Action 写入(数据写 Iceberg)
- `storage_type=VIRTUAL` 的对象:只读,Action 的 CREATE/UPDATE/DELETE 被禁用

> 本文档落地前端 guard(§四 F5);后端 Action 校验属 Action 闭环工作,延后。

### 2.3 映射关系(文档化,不合并字段)

`ObjectType.storage_type` 与 `DatasetGovernance.kind` 是两个视角的字段,**不强行统一**,但映射关系固定:

| 对象 `storage_type` | 绑定的数据集 `kind` |
|---------------------|---------------------|
| `MANAGED` | `MANAGED` |
| `VIRTUAL` | `VIRTUAL` |

前端在数据集选择器中按对象的 `storage_type` 过滤候选数据集的 `kind`。

#### 2.3.1 OT 级主数据源字段 `backing_dataset_api_name`(对标 Palantir "backing datasource")

`ObjectType` 上新增可空字段 `backing_dataset_api_name`,对标 Palantir Foundry 的 "backing datasource" 概念(见 Palantir [Create an object type](https://palantir.com/docs/foundry/object-link-types/create-object-type/) 的 "Choosing a backing datasource")。

**职责**:
- OT 的**默认/主数据源**便捷引用 —— 列表徽章、详情页快速展示,不必反查 property 的 `backing_mapping`
- 单表 MANAGED 场景的 1:1 便捷字段(此时等于所有 property 的 `backing_dataset_api_name`)
- 后续权限锰点的预留位置(Palantir 中对象权限由 backing datasource 的位置决定)

**关键约束(不破坏属性级绑定)**:
- **不是权威物理绑定** —— 权威绑定仍是每个 `PropertyDef.backing_mapping`(支持 column-wise MDO:一个 OT 多个 dataset)
- **首次绑定锰定主源** —— `OntologyService.link_dataset` 在 OT 字段为 None 时写入本次 dataset;后续绑定**不同** dataset **不覆盖**(首绑即主源,MDO 场景额外 dataset 为辅,仅通过 property 级 `backing_mapping` 体现)
- **可空** —— 未绑定时为 None;纯 MDO 类型无明确主源时也可为 None
- **unlink 不清空** —— `unlink_dataset` 清空全部 property 映射时不清空 OT 字段(保留历史主源引用,对标 Palantir "backing datasource 仍在 metadata")

| 场景 | OT `backing_dataset_api_name` | property `backing_mapping.dataset_api_name` |
|------|------------------------------|---------------------------------------------|
| 未绑定 | None | None |
| 单表 MANAGED(首绑) | = dataset A | 全 = A |
| 单表重绑到 B | 仍 = A(不覆盖) | 全 = B |
| column-wise MDO(A主 + B辅) | = A | 部分 = A, 部分 = B |

---

---

## 三、后端实施方案

### 3.1 B1 - `DatasetGovernance` 增加 `kind` 字段

**目标**:数据集资源类型显式分类。

**改动文件**:
- `src/ontology/core/schemas/datasource.py` - `DatasetGovernance` 与 `DatasetGovernanceCreate` 加字段
- `src/ontology/core/models/datasource.py` - `DatasetGovernanceModel` ORM 加字段
- `src/ontology/layers/metadata/postgres_meta_store.py` - `create_dataset` / `list_datasets` / `get_dataset` 读写 `kind`

**Schema 定义**:

```python
# src/ontology/core/schemas/datasource.py
from typing import Literal

class DatasetGovernance(BaseModel):
    # ... 现有字段 ...
    kind: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    is_view: bool = False  # 保留,语义收窄为 Managed 子类型标记

class DatasetGovernanceCreate(BaseModel):
    # ... 现有字段 ...
    kind: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    is_view: bool = False
```

**ORM 定义**:

```python
# src/ontology/core/models/datasource.py
class DatasetGovernanceModel(Base):
    # ... 现有字段 ...
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="MANAGED")
```

**存量兼容**:默认值 `MANAGED`,存量记录(均为落地表)迁移后即为 `MANAGED`,见 §五。

**验收**:
- `GET /datasets` 返回项含 `kind` 字段
- 存量记录 `kind=MANAGED`
- 新建数据集不传 `kind` 时默认 `MANAGED`

---

### 3.2 B2 - 新增"登记虚拟表"接口

**目标**:把外部数据源表登记为 VIRTUAL 虚拟表,进入数据集目录。

**新增 route**:

```
POST /datasources/{datasourceApiName}/virtual-tables
Body: { database, table, api_name?, display_name? }
Response: 201, DatasetGovernance
```

**改动文件**:
- `src/ontology/routes/datasource.py` - 新增 route handler
- `src/ontology/services/datasource_service.py` - 新增 `register_virtual_table` 方法

**编排逻辑**(`register_virtual_table`):

```python
async def register_virtual_table(
    self,
    datasource_api_name: str,
    database: str,
    table: str,
    api_name: str | None = None,
    display_name: str = "",
) -> DatasetGovernance:
    """把外部数据源的一张表登记为 VIRTUAL 虚拟表。

    编排:
    1. 校验数据源存在
    2. describe_table 拉取列(确认外部表可联)
    3. 构造三段式定位符 catalog.schema.table
    4. 写 DatasetGovernance(kind=VIRTUAL, data_source_api_name, storage_location=三段式)
    """
    ds = await self.metadata.get_datasource(datasource_api_name)
    catalog_name = ds.gravitino_catalog_name or ds.api_name

    # 确认外部表可联(复用现有 describe_table)
    table_info = await self.describe_table(datasource_api_name, database, table)
    if not table_info.columns:
        raise ValidationError(f"External table {database}.{table} has no columns or is unreachable")

    final_api_name = api_name or table
    locator = f"{catalog_name}.{database}.{table}"

    return await self.metadata.create_dataset(DatasetGovernanceCreate(
        api_name=final_api_name,
        display_name=display_name or table,
        storage_location=locator,
        data_source_api_name=datasource_api_name,
        kind="VIRTUAL",
        is_view=False,
    ))
```

**注意**:
- `api_name` 唯一性冲突返回 409(复用 `create_dataset` 的 `ConflictError` → 409 映射)
- `describe_table` 失败说明外部表不可联,返回 422
- 登记动作**不**创建任何物理表,只写 PG 元数据

**验收**:
- `POST /datasources/{ds}/virtual-tables` 成功后,`GET /datasets` 能列出该虚拟表,`kind=VIRTUAL`
- 外部表不可联时返回 422 且不写记录
- 重复 `api_name` 返回 409

---

### 3.3 B3 - `get_dataset_schema` 按 `kind` 分流

**目标**:统一 schema 拉取入口,让 VIRTUAL 虚拟表也能懒加载列。

**当前问题**:`get_dataset_schema` 只走 `IcebergStore.get_schema`(`_load_table` 走 Iceberg REST catalog),VIRTUAL 虚拟表不在 Iceberg catalog,会 `NotFoundError`。

**改动文件**:`src/ontology/services/datasource_service.py` - `get_dataset_schema` 方法

**分流逻辑**:

```python
async def get_dataset_schema(self, api_name: str) -> DatasetSchema:
    ds = await self.metadata.get_dataset(api_name)

    if ds.kind == "MANAGED":
        # 现有逻辑:走 IcebergStore
        try:
            return await self.dataset.get_schema(api_name)
        except NotFoundError:
            return DatasetSchema(columns=[])
        except Exception as exc:
            raise IcebergUnavailableError(...) from exc

    elif ds.kind == "VIRTUAL":
        # 新增:解析三段式定位符,走 Gravitino 联邦拉列
        # storage_location = "catalog.schema.table"
        parts = ds.storage_location.split(".")
        if len(parts) != 3:
            return DatasetSchema(columns=[])
        catalog, schema, table = parts
        try:
            grav_cols = await self.catalog.get_table_columns(catalog, schema, table)
            return DatasetSchema(columns=[
                ColumnDef(
                    name=str(col.get("name", "")),
                    type=GravitinoRegistry._format_gravitino_column_type(col.get("type", "unknown")),
                    nullable=bool(col.get("nullable", True)),
                )
                for col in grav_cols
            ])
        except Exception as exc:
            raise GravitinoUnavailableError(
                f"Virtual table '{api_name}' schema unavailable: {exc}"
            ) from exc

    return DatasetSchema(columns=[])
```

**复用现有能力**:`GravitinoRegistry.get_table_columns`(`gravitino_registry.py:242`)已存在,`describe_table` 已在用,此处复用同一底层调用。

**验收**:
- MANAGED 数据集 schema 拉取行为不变
- VIRTUAL 数据集 `GET /datasets/{api}/schema` 返回外部表的真实列
- 外部不可联时抛 `GravitinoUnavailableError`(前端可区分"无数据"与"服务不可用")

---

### 3.4 B4 - 删除 Gravitino SQL View 死代码

**目标**:砍掉与 Palantir Virtual Table 语义冲突的 Gravitino View 线路。

**删除清单**:

| 文件 | 删除内容 |
|------|---------|
| `src/ontology/services/virtual_table_service.py` | **整个文件删除** |
| `src/ontology/layers/catalog/gravitino_registry.py` | 删除 `create_view`(:78)方法;`is_view`(:107)方法**保留**(B3 可能用于运行时探测,且语义可重定义) |
| `src/ontology/config/container.py` | 删除 `VirtualTableService` import(:35)、`virtual_table_service` property(:153-159) |
| `src/ontology/routes/` | 确认无 `/virtual-tables` route(grep 确认当前无) |
| `tests/` | 删除引用 `VirtualTableService` / `create_view` / `query_view` 的测试 |

**`is_view` 方法保留理由**:`GravitinoRegistry.is_view` 是运行时探测某表是否为 Gravitino view 的能力,属底层 catalog 能力,保留无害。`DatasetGovernance.is_view` 字段语义已收窄(§1.4),两者不再绑定。

**风险**:grep 确认 `VirtualTableService` / `create_view` **无任何 route、无 service 调用方**,是纯死代码,删除零风险。

**验收**:
- 删除后 `pytest` 全绿(无 import 错误)
- `grep -rn "VirtualTableService\|create_view\|query_view" src/ontology` 无残留(`is_view` 探测方法除外)

---

### 3.5 B5a - `ObjectType.storage_type` 统一为 `MANAGED` / `VIRTUAL`

**目标**:对象侧术语对齐 Palantir(`PHYSICAL` → `MANAGED`)。

**改动文件与精确位置**(共 7 文件 17 处 + 前端):

| 文件 | 行 | 改动 |
|------|----|------|
| `src/ontology/core/schemas/ontology.py` | 149, 168, 185 | `Literal["PHYSICAL","VIRTUAL"]` → `Literal["MANAGED","VIRTUAL"]` |
| `src/ontology/core/schemas/ontology.py` | 421 | `storage_type: str = "PHYSICAL"` → `"MANAGED"` |
| `src/ontology/core/schemas/ai.py` | 73-75 | `Literal["PHYSICAL","VIRTUAL"]` → `MANAGED`;default `"MANAGED"`;description 更新 |
| `src/ontology/core/models/ontology.py` | 66 | 注释 `# PHYSICAL | VIRTUAL` → `# MANAGED | VIRTUAL` |
| `src/ontology/services/ontology_service.py` | 85, 116, 211, 242, 309 | `"PHYSICAL"` → `"MANAGED"`;cast 类型同步;注释更新 |
| `src/ontology/services/object_query_service.py` | 4, 58 | 注释/docstring `PHYSICAL` → `MANAGED` |
| `src/ontology/routes/ontology/__init__.py` | 140 | cast 类型 `MANAGED` |
| `src/ontology/layers/metadata/postgres_meta_store.py` | 194 | cast 类型 `MANAGED` |

**前端**(见 §四 F-types):
- `src/web-ui/src/types/index.ts:33` - `StorageType = 'MANAGED' \| 'VIRTUAL'`
- `src/web-ui/src/types/wizard.ts:32` - 同步
- `src/web-ui/src/pages/OntologyWorkspace.tsx:66,82` - 同步
- `src/web-ui/src/components/CreateObjectWizard.tsx:80,81,402,404,911` - 值与文案
- `src/web-ui/src/api/prompts.ts:13` - `"storage_type": "MANAGED"`

**存量数据迁移**:见 §五,PG 里 `object_types.storage_type` 列的 `'PHYSICAL'` 值需 UPDATE 为 `'MANAGED'`。

**验收**:
- `grep -rn "PHYSICAL" src/ontology src/web-ui/src` 无残留(除本设计文档与历史 ADR)
- 存量对象 `storage_type=MANAGED`
- 创建/编辑对象 API 接受 `MANAGED`/`VIRTUAL`,拒绝 `PHYSICAL`(422)

---

### 3.6 后端改动汇总文件清单

```
新增:
  (无新文件,B2 在现有 route/service 内追加)

修改:
  src/ontology/core/schemas/datasource.py          (B1)
  src/ontology/core/schemas/ontology.py            (B5a)
  src/ontology/core/schemas/ai.py                  (B5a)
  src/ontology/core/models/datasource.py           (B1)
  src/ontology/core/models/ontology.py             (B5a 注释)
  src/ontology/layers/metadata/postgres_meta_store.py (B1, B5a)
  src/ontology/services/datasource_service.py      (B2, B3)
  src/ontology/services/ontology_service.py        (B5a)
  src/ontology/services/object_query_service.py    (B5a 注释)
  src/ontology/routes/datasource.py                (B2 route)
  src/ontology/routes/ontology/__init__.py         (B5a)
  src/ontology/config/container.py                 (B4 删除 VirtualTableService)
  src/ontology/layers/catalog/gravitino_registry.py (B4 删 create_view)

删除:
  src/ontology/services/virtual_table_service.py   (B4)
```

---

## 四、前端实施方案

### 4.1 F-types - 类型对齐

**改动文件**:
- `src/web-ui/src/types/index.ts`
- `src/web-ui/src/types/wizard.ts`

**改动**:

```typescript
// types/index.ts
export type StorageType = 'MANAGED' | 'VIRTUAL';  // 原 'PHYSICAL' | 'VIRTUAL'

export interface DatasetGovernance {
  // ... 现有字段 ...
  kind: 'MANAGED' | 'VIRTUAL';   // 新增
  is_view: boolean;              // 保留
}
```

```typescript
// types/wizard.ts
export interface ObjectWizardData {
  // ...
  storage_type: 'MANAGED' | 'VIRTUAL';        // 原 PHYSICAL
  dataset_api_name: string;                    // 替换 datasource_path: string
  dataset_schema?: { name: string; type: string; nullable: boolean }[];  // 新增,缓存选中数据集列
}

export interface PropertyDraft {
  // ...
  source_column?: string;                      // 既有,本次启用
  physical_mapping?: PhysicalColumnRef | null; // 新增,提交时构造
}
```

---

### 4.2 F0 - 数据源详情页增加"登记虚拟表"入口

**目标**:在 `DataSourceDetail` 的 explore tab,每张外部表旁加登记按钮。

**改动文件**:
- `src/web-ui/src/pages/DataSourceDetail.tsx`
- 新建 `src/web-ui/src/components/RegisterVirtualTableDialog.tsx`
- `src/web-ui/src/api/client.ts` - 新增 `registerVirtualTable`

**API client**:

```typescript
// api/client.ts
export function registerVirtualTable(
  datasourceApiName: string,
  data: { database: string; table: string; api_name?: string; display_name?: string },
): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(
    `${DATA_API}/datasources/${datasourceApiName}/virtual-tables`,
    { method: 'POST', body: JSON.stringify(data) },
  );
}
```

**交互**:

```
DataSourceDetail > explore tab:
┌────────────────────────────────────────────────────────────┐
│ 📊 orders        [查看列] [采样] [登记为虚拟表]            │
│ 📊 customers     [查看列] [采样] [登记为虚拟表]            │
│ 📊 products      [查看列] [采样] [登记为虚拟表]            │
└────────────────────────────────────────────────────────────┘
```

点击「登记为虚拟表」弹 `RegisterVirtualTableDialog`:

```
┌─ 登记虚拟表 ──────────────────────────────────┐
│                                                  │
│  来源: mysql_prod.orders                         │
│                                                  │
│  API name *  [orders           ]  (默认表名)     │
│  显示名称    [Orders           ]                 │
│                                                  │
│  i 登记后该表将作为虚拟表进入数据集目录,         │
│    可被虚拟对象(VIRTUAL)绑定,只读,不落地。    │
│                                                  │
│                        [取消]  [登记]            │
└──────────────────────────────────────────────────┘
```

- 登记成功后 toast 提示,可提供"去数据集目录查看"链接
- 登记失败(409 重名 / 422 不可联)显示对应错误

**验收**:
- explore tab 每行有「登记为虚拟表」按钮
- 登记成功后 `GET /datasets` 能看到该虚拟表
- 重名/不可联错误正确展示

---

### 4.3 F1 - 创建向导 Step 0 统一数据集选择

**目标**:删 mock,接真实数据集目录;storage_type 提顶;按 kind 过滤。

**改动文件**:`src/web-ui/src/components/CreateObjectWizard.tsx`

**布局**:

```
┌─ 存储类型(segmented control,置顶)──────────────────┐
│  [ 托管对象 MANAGED ]    [ 虚拟对象 VIRTUAL ]          │
│  数据落地 Iceberg,可写      外部表代理,只读不落地     │
└────────────────────────────────────────────────────────┘

┌─ 数据集绑定 ──────────────────────────────────────────┐
│  🔍 [搜索数据集...]                                    │
│  ┌──────────────────────────────────────────────────┐ │
│  │ 📊 work_orders     托管表 · 12,300 行              │ │  ← MANAGED 对象只列 kind=MANAGED
│  │ 📊 customers       托管表 · 45,000 行              │ │
│  └──────────────────────────────────────────────────┘ │
│  (VIRTUAL 对象时只列 kind=VIRTUAL 的虚拟表)           │
│                                                        │
│  ☐ 暂不关联(稍后可在对象详情补关联)  ← 仅 MANAGED 可见│
└────────────────────────────────────────────────────────┘
```

**关键逻辑**:

1. **删 `MOCK_DATASETS`**(`CreateObjectWizard.tsx:30`),改用 `listDatasets()`
2. **storage_type 提顶**:从底部 select 移到顶部 segmented control
3. **按 kind 过滤**:
   - `storage_type === 'MANAGED'` → 只显示 `kind === 'MANAGED'` 的数据集
   - `storage_type === 'VIRTUAL'` → 只显示 `kind === 'VIRTUAL'` 的数据集
4. **“暂不关联”选项**:
   - 仅 MANAGED 显示(MANAGED 对象允许延迟关联,§2.2 原则2)
   - VIRTUAL **不显示**:虚拟对象必须绑一张虚拟表才有数据来源,不绑则查不到任何东西
   - 文案改为"暂不关联(稍后可在对象详情补关联)",删除原"系统自动生成空数据集,通过 Action 写入"的错误描述
5. **选中数据集后**:调 `getDatasetSchema(apiName)` 拉列,缓存到 `dataset_schema`,供 F2 列映射用

**校验**(Step 0 → Next):

| 条件 | 规则 |
|------|------|
| MANAGED + 未勾"暂不关联" | 必须选中一个数据集,否则报红"请选择数据集" |
| MANAGED + 勾"暂不关联" | 放行,Review 显示⚠"未关联数据集" |
| VIRTUAL | 必须选一个虚拟表,否则报红"请选择虚拟表" |

**加载/空态/错误态**:
- 加载中:skeleton(复用 `Skeleton` 组件)
- 空态:MANAGED 时"暂无托管表,请先创建同步任务";VIRTUAL 时"暂无虚拟表,请先在数据源详情登记"
- 错误:`formatError` 展示

**验收**:
- Step 0 不再出现 mock 数据
- 切换 storage_type 时数据集列表按 kind 过滤
- VIRTUAL 无"暂不关联"选项
- 选中后 `dataset_schema` 有列数据

---

### 4.4 F2 - Step 1 属性表增加"源列"映射

**目标**:属性 → 物理列映射的 UI 入口,让 `physical_mapping` 有数据来源。

**改动文件**:
- `src/web-ui/src/components/CreateObjectWizard.tsx` - Step 1 属性表
- 新建 `src/web-ui/src/lib/typeMapping.ts` - 类型映射表

**typeMapping.ts**(Trino/Iceberg 类型串 → `DataType` 枚举,对齐后端 `_iceberg_type_from_str`):

```typescript
// lib/typeMapping.ts
import type { DataType } from '../types';

const TYPE_MAP: Record<string, DataType> = {
  string: 'STRING', varchar: 'STRING', char: 'STRING',
  boolean: 'BOOLEAN',
  int: 'INTEGER', integer: 'INTEGER',
  smallint: 'SHORT', short: 'SHORT',
  bigint: 'LONG', long: 'LONG',
  float: 'FLOAT', real: 'FLOAT',
  double: 'DOUBLE',
  decimal: 'DECIMAL',
  date: 'DATE',
  timestamp: 'TIMESTAMP',
  // 复杂类型降级为 STRING
};

export function trinoTypeToDataType(trinoType: string): DataType {
  const base = trinoType.toLowerCase().replace(/\(.*\)/, '').trim();
  return TYPE_MAP[base] ?? 'STRING';
}
```

**属性表新增"源列"列**:

```
API name   │ Property name │ Type      │ 源列          │ Searchable │
───────────┼───────────────┼───────────┼───────────────┼────────────│
 alert_id  │ Alert ID      │ String ▼  │ alert_id ▼    │  ☑         │
 status    │ Status        │ String ▼  │ status_cd ▼   │  ☑         │
───────────┴───────────────┴───────────┴───────────────┴────────────│
```

- **源列下拉**:选项来自 `dataset_schema`(F1 缓存的列)
- **自动映射**:新增属性时若 api_name 与某列名匹配则自动选中
- **未选数据集时**(MANAGED 暂不关联 / 无 schema):源列列显示灰色"-",不可编辑
- **必填性**:绑定了数据集时,每个属性必须映射一个源列(否则该属性无数据来源);未绑定则源列可选

**「从数据集生成属性」按钮**(属性表上方):

- 仅当 `dataset_schema` 有值时可用
- 点击后把所有列批量生成为属性(`api_name` = 列名,`data_type` = `trinoTypeToDataType(列类型)`,`source_column` = 列名)
- 用户再删减/改 searchable

**验收**:
- 绑定数据集后,源列下拉可选项 = 数据集列
- "从数据集生成属性"能一键生成全部属性
- 未绑定数据集时源列不可编辑
- 类型映射覆盖常见 Trino/Iceberg 类型

---

### 4.5 F3 - 提交 payload 带 `physical_mapping` + 编辑回填修复

**目标**:让"选数据集"不再是摆设,关联真正落库;编辑不丢关联。

**改动文件**:
- `src/web-ui/src/pages/OntologyWorkspace.tsx` - `handleWizardComplete`(:401 起)
- `src/web-ui/src/components/CreateObjectWizard.tsx` - `handleFinish`(:232 起)+ 编辑回填(:386 起)

**提交 payload 构造**(`handleWizardComplete`):

```typescript
properties: data.properties.map((p) => ({
  api_name: p.api_name,
  display_name: p.display_name,
  data_type: p.data_type,
  searchable: p.searchable !== false,
  physical_mapping: data.dataset_api_name && p.source_column
    ? {
        dataset_api_name: data.dataset_api_name,
        catalog_name: <从 dataset 详情取>,
        schema_name: <从 dataset 详情取>,
        table_name: data.dataset_api_name,
        column_name: p.source_column,
      }
    : null,
})),
```

> `catalog_name` / `schema_name` 的取值:MANAGED 数据集从 `storage_location` 解析或固定 `iceberg`/`ontology`;VIRTUAL 数据集从 `storage_location`(三段式)解析。实现时从 `getDataset(apiName)` 拿详情后解析。

**编辑回填修复**(`handleEditObject`,`OntologyWorkspace.tsx:386`):

```typescript
setEditingObjectData({
  ...,
  storage_type: fullOt.storage_type,  // 现已是 MANAGED/VIRTUAL
  dataset_api_name: fullOt.properties[0]?.physical_mapping?.dataset_api_name || '',
  properties: fullOt.properties.map((p) => ({
    api_name: p.api_name,
    display_name: p.display_name,
    data_type: p.data_type,
    is_primary_key: p.is_primary_key,
    searchable: p.indexed !== false,
    source_column: p.physical_mapping?.column_name,  // ← 补这个,原代码缺失
  })),
});
```

并在 Step 0 / Step 1 用 `dataset_api_name` / `source_column` 预填选中状态(需在选中 dataset 后拉 schema 才能预填源列下拉)。

**验收**:
- 创建对象后,`GET /object-types/{type}` 返回的 properties 含 `physical_mapping`
- 编辑对象打开向导,Step 0 已选中原数据集,Step 1 各属性源列已回填
- 编辑保存后 `physical_mapping` 不丢失

---

### 4.6 F4 - 对象详情"数据集"区块 + 列表徽章

**目标**:让用户看到对象的数据来源与关联状态。

**改动文件**:
- `src/web-ui/src/components/ObjectTypeViews.tsx` - CardView / TableView 加徽章
- `src/web-ui/src/components/ObjectDetailPanel.tsx` - 新增数据集区块
- 新建 `src/web-ui/src/components/DatasetLinkDialog.tsx` - 独立管理关联模态

**列表徽章**(`ObjectTypeViews`):

| 状态 | 徽章 | 颜色 |
|------|------|------|
| MANAGED 已绑定全列 | `已关联` | 绿 |
| MANAGED 部分列未映射 | `部分映射` | 黄 |
| MANAGED 未绑定 | `未关联` | 黄 + ⚠ |
| VIRTUAL | `虚拟` | 灰 |

`TableView` 新增 "Dataset" 列:已绑定显示 dataset `api_name`(mono);未绑定显示徽章。

**详情区块**(`ObjectDetailPanel`):

```
MANAGED 对象:
┌─ 数据集 ────────────────────────────────────┐
│  📊 work_orders  托管表 · 12,300 行           │
│  [管理关联]                                   │
│  列映射:                                      │
│   work_order_id → work_order_id (PK)         │
│   status_cd     → status                     │
│   ...                                         │
└───────────────────────────────────────────────┘

VIRTUAL 对象:
┌─ 数据集 ────────────────────────────────────┐
│  🔗 orders  虚拟表 · 来自 mysql_prod          │
│  🔒 只读 - 虚拟对象不支持写入                 │
│  [管理关联]                                   │
└───────────────────────────────────────────────┘

未关联 (仅 MANAGED):
┌─ 数据集 ────────────────────────────────────┐
│  ⚠ 未关联数据集   [关联数据集]                │
└───────────────────────────────────────────────┘
```

**`DatasetLinkDialog`**:复用 F1 的数据集选择器 + F2 的列映射表,提交时调**后端 A1**(`PATCH /object-types/{type}/dataset-link`,**待实现,见 §八**)。

> ⚠️ F4 的 `DatasetLinkDialog` 依赖后端 A1(独立管理关联 API)。A1 未实现前,F4 先只做**展示**部分(徽章 + 详情区块只读),"管理关联"按钮置灰或引导走编辑向导。

**验收**:
- 列表/卡片正确显示关联状态徽章
- 详情区块按对象类型展示对应形态
- VIRTUAL 显示只读提示

---

### 4.7 F5 - VIRTUAL 写入约束前端 guard

**目标**:体现"VIRTUAL 只读"原则(纯前端,不依赖后端 Action 校验)。

**改动文件**:
- `src/web-ui/src/components/CreateObjectWizard.tsx` - Step 3(配置操作)
- `src/web-ui/src/pages/ActionsOverview.tsx` - VIRTUAL 对象 action 标记

**CreateObjectWizard Step 3**:

- 当 `storage_type === 'VIRTUAL'` 时:
  - 禁用 CREATE / UPDATE / DELETE 类 action 的添加
  - 只允许"只读/派生"类(如有)
  - 显示提示"虚拟对象不支持写操作,仅可定义只读/派生操作"

**ActionsOverview**:VIRTUAL 对象的 action 卡片标记"只读"。

**验收**:
- VIRTUAL 对象向导 Step 3 无法添加写操作
- 提示文案清晰

---

### 4.8 F6 - DatasetDetail 页面按 `kind` 展示

**目标**:数据集详情页对齐新术语。

**改动文件**:`src/web-ui/src/pages/DatasetDetail.tsx`

**改动**:
- 第 179 行 `dataset.is_view` 徽章 → 改为 `dataset.kind` 徽章(`MANAGED` / `VIRTUAL`)
- schema tab 对 VIRTUAL 数据集走 B3(已自动生效,因 `getDatasetSchema` 分流)
- 文案"虚拟表"统一指 VIRTUAL kind

**验收**:
- 托管表详情显示 `MANAGED` 徽章
- 虚拟表详情显示 `VIRTUAL` 徽章,schema tab 能拉到外部表列

---

### 4.9 前端改动汇总文件清单

```
新增:
  src/web-ui/src/components/RegisterVirtualTableDialog.tsx  (F0)
  src/web-ui/src/components/DatasetLinkDialog.tsx           (F4, 展示先行)
  src/web-ui/src/lib/typeMapping.ts                         (F2)

修改:
  src/web-ui/src/types/index.ts            (F-types)
  src/web-ui/src/types/wizard.ts           (F-types)
  src/web-ui/src/api/client.ts             (F0 registerVirtualTable)
  src/web-ui/src/api/prompts.ts            (B5a 术语)
  src/web-ui/src/pages/DataSourceDetail.tsx (F0)
  src/web-ui/src/pages/OntologyWorkspace.tsx (F3)
  src/web-ui/src/pages/DatasetDetail.tsx   (F6)
  src/web-ui/src/pages/ActionsOverview.tsx (F5)
  src/web-ui/src/components/CreateObjectWizard.tsx (F1, F2, F3, F5)
  src/web-ui/src/components/ObjectTypeViews.tsx (F4)
  src/web-ui/src/components/ObjectDetailPanel.tsx (F4)
```

---

## 五、数据迁移

### 5.1 PG 迁移脚本

**迁移 1:`datasets` 表加 `kind` 列**

```sql
ALTER TABLE datasets ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'MANAGED';
-- 存量记录均为落地表,默认 MANAGED 正确
```

**迁移 2:`object_types` 表 `storage_type` 值替换**

```sql
UPDATE object_types SET storage_type = 'MANAGED' WHERE storage_type = 'PHYSICAL';
-- 迁移后 storage_type 取值域: MANAGED | VIRTUAL
```

**迁移 3:`is_view` 无需迁移**(恒 false,语义收窄即可)

### 5.2 迁移方式

- 项目已接入 **Alembic**（2026-06-29），业务表 schema 单一真相源 = ORM 模型 + `alembic/versions/` revision 链
- 本设计（`datasets.kind` 列 + `storage_type` PHYSICAL→MANAGED）已并入初始 revision `9575abae4046_initial_schema_baseline`，无需单独迁移
- 以后的 schema 变更流程：改 ORM → `alembic revision --autogenerate` → review → `alembic upgrade head`
- 历史参考：原 idempotent SQL 脚本 `scripts/migrations/20260618_dataset_kind_and_storage_type.sql` 已随 Alembic 接入删除

**验收**:
- 迁移后 `SELECT DISTINCT kind FROM datasets` 含 `MANAGED`(及未来 `VIRTUAL`)
- `SELECT DISTINCT storage_type FROM object_types` 仅 `MANAGED` / `VIRTUAL`
- 迁移可重复执行不报错

---

## 六、实施顺序与依赖

```
阶段 0 - 术语与死代码清理(无依赖,可并行)
  ├─ B4  删除 Gravitino View 死代码
  └─ B5a 后端 storage_type PHYSICAL→MANAGED (需配合迁移 2)
  (文档校准已完成,见§〇)

阶段 1 - 数据集分类与登记(B1→B2→B3 串行)
  ├─ B1  DatasetGovernance 加 kind (需配合迁移 1)
  ├─ B2  登记 Virtual Table 接口
  └─ B3  get_dataset_schema 按 kind 分流

阶段 2 - 前端类型与 guard(依赖阶段 0/1 的术语确定)
  ├─ F-types  类型对齐
  ├─ F5       VIRTUAL 写入 guard(纯前端)
  └─ F3       提交 physical_mapping + 编辑回填(依赖 F-types)

阶段 3 - 前端数据集交互(依赖 B1+B3)
  ├─ F1  向导 Step 0 统一选择(依赖 B1, B3)
  ├─ F2  Step 1 源列映射(依赖 B3, F1)
  ├─ F0  登记虚拟表入口(依赖 B2)
  └─ F6  DatasetDetail 按 kind 展示(依赖 B1, B3)

阶段 4 - 展示与关联管理
  └─ F4  对象详情数据集区块(展示部分依赖 B1;管理关联依赖后端 A1,延后)
```

**关键依赖卡点**:
- **B3 是 F1/F2 的硬依赖**:B3 不做,登记的虚拟表拉不到 schema,列映射无法工作
- **B5a 与前端 F-types 必须同步发布**:术语不一致会导致前后端 storage_type 值对不上
- **迁移脚本必须与代码同批发布**:代码读 `MANAGED` 但库里有 `PHYSICAL` 会全部查不到对象

---

## 七、验收标准

### 7.1 端到端验收场景

**场景 A:托管对象全链路**
1. 数据源 sync 一张外部表 → 产生 `kind=MANAGED` 数据集
2. 创建对象,Step 0 选 MANAGED → 列出该托管表 → 选中
3. Step 1 "从数据集生成属性" → 属性带源列映射
4. 提交 → `GET /object-types/{type}` 的 properties 含 `physical_mapping`
5. 编辑该对象 → Step 0/Step 1 回填正确
6. 详情页显示"已关联"徽章 + 数据集区块

**场景 B:虚拟对象全链路**
1. 数据源详情 explore → 选一张外部表 →「登记为虚拟表」→ 产生 `kind=VIRTUAL` 数据集
2. 创建对象,Step 0 选 VIRTUAL → 列出该虚拟表 → 选中(无"暂不关联")
3. Step 1 源列下拉来自外部表列(B3 联邦拉取)
4. Step 3 无法添加写操作(F5 guard)
5. 提交 → 对象 `storage_type=VIRTUAL`,property 有 `physical_mapping`
6. 详情页显示"虚拟 · 只读"

**场景 C:延迟关联**
1. 创建 MANAGED 对象,Step 0 勾"暂不关联" → 提交成功
2. 详情页显示"未关联"
3. (后续 A1 实现后)通过"管理关联"补绑数据集

### 7.2 全局术语验收

文档侧已完成(见§〇)。代码侧随 B4/B5a 落地后验收:

```bash
# 代码无残留旧术语(机制名"联邦查询"除外)
grep -rn "PHYSICAL" src/ | grep -v "dataset-ontology-binding\|adr-007\|migration"
grep -rn "联邦表" src/
grep -rn "VirtualTableService\|create_view\|query_view" src/ontology
# 上述均应无结果
```

### 7.3 测试验收

- 后端:`pytest` 全绿,新增 B2/B3 的单测
- 前端:`npm run build` 无类型错误,`npm run lint` 通过
- 迁移脚本可重复执行

---

## 八、未覆盖与后续

### 8.1 明确延后项

| 项 | 说明 | 前置 |
|----|------|------|
| **Action 闭环** | OutboxExecutor / CDC / WriteBackManager 接线 | 见 implementation-status.md P0 #1 |
| **后端 A1:独立管理关联 API** | `PATCH /object-types/{type}/dataset-link`,供 F4 的 `DatasetLinkDialog` 使用 | 本文档 F4 展示部分先行 |
| **Doris 索引加速接通** | `IndexSyncService` + 真实 fields + `create_index_sync_pipeline` | 依赖本文档"数据集关联"落地(索引字段来自 property 的 physical_mapping) |
| **后端 Action 写入校验** | `ActionService.execute_action` 加 `storage_type=VIRTUAL` 拦截 | 属 Action 闭环,与前端 F5 guard 互补 |
| **Foundry View 子类型** | `is_view=true` 的 Managed 派生视图 | 字段已占位,实现延后 |

### 8.2 与 Doris 索引的衔接

Doris 索引加速接通的前提是:能从 ObjectType 的 property 拿到 `indexed=True` 字段及其 `physical_mapping`(知道索引哪个数据集的哪列)。本文档落地后,property 的 `physical_mapping` 有真实数据,Doris 索引工作即可基于此推进。建议 Doris 索引作为本文档的**紧后继**工作。

### 8.3 开放问题

- **`physical_mapping` 的 `catalog_name`/`schema_name` 取值规范**:F3 实现时需明确 MANAGED 与 VIRTUAL 数据集的 catalog/schema 取值来源(从 `storage_location` 解析 or 固定值)。建议实现阶段对齐后端 `PhysicalColumnRef` 的实际消费方(ObjectQueryService / 未来的 IndexSyncService)。
- **Virtual Table 的 schema 刷新**:外部表 schema 变更(加列/删列)后,登记的 VIRTUAL 数据集如何感知。Palantir 用 Schema Drift 探测 + Refresh Schema。本期不实现,登记时拉一次 schema 缓存即可,后续可加刷新接口。

---

*关联文档: [CLAUDE.md](../CLAUDE.md) · [architecture_plan.md](../architecture/architecture_plan.md) · [data-layer-design.md](./data-layer-design.md) · [implementation-status.md](../architecture/implementation-status.md)*
