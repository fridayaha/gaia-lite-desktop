# 待修复：托管对象定义后 PG `datasets` 治理记录缺失，前端看不到 dataset

**发现时间**: 2026-06-26  
**状态**: 🟢 已修复 (2026-06-26)  
**影响范围**: 前端「数据对接」页数据集列表 + `link_dataset` 绑定流程

---

## 现象

前端「数据对接」(`/data`)页面的 `数据集 (N)` 区块永远不显示,即使已定义多个 MANAGED ObjectType。

前端入口和后端 API 都齐全,但 `GET /datasets` 永远返回空数组:

```tsx
// DataConnections.tsx:221
{datasets.length > 0 && (
  <div>
    <h4>数据集 ({datasets.length})</h4>
    {datasets.map((ds) => ( /* ... */ ))}
  </div>
)}
```

`length === 0` 时整个区块不渲染 → 用户以为功能没实现。

## 根因

**`define_object_type` / `define_object_type_batch` 创建 MANAGED 对象时,只注册了 Gravitino 物理资产和 Doris 索引表,漏写了 PG `datasets` 治理记录。**

### 链路实查

**1. 前端入口存在(非未接线)**

- 路由 `/data/datasets/:name` → `DatasetDetail.tsx`(`App.tsx:50`)
- `DataConnections.tsx:221` 有 `数据集 ({datasets.length})` 列表区,点击跳详情
- API client `listDatasets()` → `GET /datasets`(`client.ts:452`)

**2. 后端 API 齐全**

- `routes/datasource.py:352` `GET /datasets` → `service.list_datasets()` → `metadata.list_datasets()`
- `metadata.list_datasets()`(`postgres_meta_store.py:1602`)查 PG 表 `datasets`(`DatasetGovernanceModel`,`__tablename__ = "datasets"`)

**3. PG `datasets` 表只有两条写入路径,都与"定义托管对象"无关**

| 写入入口 | 触发场景 | 路由 |
|---------|---------|------|
| `datasource_service.register_virtual_table` | 手动登记**虚拟表**(kind=VIRTUAL) | `POST /datasources/{name}/virtual-tables` |
| `datasource_service.register_dataset` | 通用登记(几乎无人调) | `POST /datasources/datasets` |

**4. 关键缺口** — `ontology_service.py` 两个 define 方法的 MANAGED 分支只做两件事:

```python
# define_object_type (L420-455) / define_object_type_batch (L590-625)
if data.storage_type == "MANAGED":
    await self._catalog.register_dataset(...)   # ✅ Gravitino 物理资产
    await self._provision_index(...)            # ✅ Doris 索引表
    # ❌ 缺:await self._metadata.create_dataset(DatasetGovernanceCreate(kind="MANAGED", ...))
```

用 awk 切出这两个方法全文搜 `create_dataset` / `DatasetGovernance` 均为空 —— 确认托管对象定义流程压根不写 PG `datasets` 治理表。

## 影响

1. **前端不可见**:所有 MANAGED ObjectType 的 dataset 卡片在「数据对接」页永不出现
2. **`link_dataset` (A1 绑定) 失败**:`link_dataset`(`ontology_service.py:852`)第一步 `get_dataset(dataset_api_name)` 会因记录不存在抛 `NotFoundError`,MANAGED 对象无法绑定 dataset
3. **虚拟表是唯一可见路径**:只有手动通过 `RegisterVirtualTableDialog` 登记的 VIRTUAL 表能出现在前端

## 修复方向

在 `define_object_type` 和 `define_object_type_batch` 的 `if data.storage_type == "MANAGED":` 分支,Gravitino 注册成功后补一次治理记录写入:

```python
await self._metadata.create_dataset(
    DatasetGovernanceCreate(
        api_name=ot_api_name,
        display_name=data.display_name,
        storage_location=f"s3://ontology-warehouse/{ot_api_name}",
        kind="MANAGED",
        is_view=False,
    )
)
```

**幂等性**:`metadata.create_dataset` 的查重逻辑在 api_name 已存在时返回现有记录而非抛 409(见 `register_virtual_table` docstring 注释),所以重复调用安全。

### 测试(TDD)

- `test_define_object_type_creates_dataset_governance`:定义 MANAGED 对象后,`metadata.get_dataset(api_name)` 返回 `kind=="MANAGED"` 记录
- `test_define_object_type_batch_creates_dataset_governance`:批量定义同理
- `test_define_object_type_idempotent_dataset`:重复定义同一 api_name 不报错,返回同一记录
- `test_define_object_type_virtual_skips_dataset`:VIRTUAL 对象不写 MANAGED 治理记录

## 相关文件

- `src/ontology/services/ontology_service.py` — `define_object_type` (~L405-455) / `define_object_type_batch` (~L560-625)
- `src/ontology/services/datasource_service.py` — `register_dataset` / `register_virtual_table` / `list_datasets` (参考实现)
- `src/ontology/layers/metadata/postgres_meta_store.py` — `create_dataset` (L1561) / `list_datasets` (L1602)
- `src/ontology/core/models/datasource.py` — `DatasetGovernanceModel` (`__tablename__ = "datasets"`)
- `src/web-ui/src/pages/DataConnections.tsx:221` — 前端列表渲染条件
- `src/web-ui/src/pages/DatasetDetail.tsx` — 详情页

## 关联文档

- `docs/design/dataset-ontology-binding.md` §3.2 / §4.6 — dataset 治理记录与 ObjectType 绑定规范
- `docs/architecture/implementation-status.md` — Dataset 层标记 ✅,但治理记录写入链路有此缺口

---

## 修复记录 (2026-06-26)

### 实际实现

在 `define_object_type` / `define_object_type_batch` 的 `if storage_type == "MANAGED":` 分支补写治理记录 + 属性 backing_mapping 回填,并修正了原修复方向中的一个命名冲突。

**关键修正:dataset api_name 必须全小写,不能等于 ObjectType 的 PascalCase api_name。**

`DatasetGovernanceCreate.api_name` 的 pattern 是 `PROPERTY_API_NAME_PATTERN = ^[a-z][a-zA-Z0-9]{0,99}$`(首词小写),而 ObjectType api_name 是 PascalCase(`^[A-Z]...`)。原修复方向写的 `api_name=ot_api_name`(`Flight`)会触发 422 校验失败。

更重要的是 **Iceberg 物理表名 == dataset api_name,而 Iceberg 表名在本部署中必须全小写**:`sea_tunnel_engine._build_sync_pipeline`（~~`create_index_pipeline` 已于 T1.10 删除，但其小写化命名教训仍有效~~）都用 `dataset_api_name.lower()` / `object_type_api_name.lower()` 读写 Iceberg 表,因为 Trino 的 iceberg REST client 在查找时会小写化标识符,而 REST server 保留声明时的大小写 —— 大小写混合的表名(如 `flightStatusLog`)在 Trino 里会 `NoSuchTableException` 不可达(见 `sea_tunnel_engine.py` 代码注释的 live-test 结论)。

因此新增 `naming.managed_dataset_api_name(ot_api_name)` 把 PascalCase **全小写**(`Flight`→`flight`、`FlightStatusLog`→`flightstatuslog`),作为该 ObjectType 自有托管数据集的 api_name = Iceberg 物理表名。这样 PG 治理记录、属性 `backing_mapping`、Iceberg 表、SeaTunnel 管道四者标识符完全一致。

> **二次修正(2026-06-26 Schema 页验证发现)**:首版修复用了 camelCase(`FlightStatusLog`→`flightStatusLog`),单词条 `Flight`→`flight` 碰巧与 Iceberg 小写一致能工作,但多词条 `flightStatusLog` 与 SeaTunnel 管道期望的小写 `flightstatuslog` 不一致 → Iceberg 表 `flightStatusLog` 在 Trino 不可达 → `GET /api/datasets/{name}/schema` 返回空(表加载失败)。改为全小写后多词条对象 Schema 页正常返回列定义。

### 改动文件

| 文件 | 改动 |
|------|------|
| `src/ontology/core/naming.py` | 新增 `managed_dataset_api_name(ot_api_name)` + 导出 |
| `src/ontology/services/ontology_service.py` | 新增 `_managed_backing_ref` / `_register_managed_dataset_governance` 辅助方法;`define_object_type` / `define_object_type_batch` 的 MANAGED 分支调用后者写治理记录;**两个 `register_dataset` 调用点改用 `managed_dataset_api_name(ot_api_name)`(全小写)作为 Iceberg 表名**(原先传 PascalCase `ot_api_name` 会导致 Iceberg 表名与 dataset api_name / backing_mapping 不一致 → Trino 不可达);批量路径在 ORM 构造期对未显式提供 `backing_mapping` 的属性自动回填(dataset=全小写, catalog=iceberg, schema=iceberg_namespace, table=全小写, column=property api_name),显式 mapping 原样保留 |
| `tests/unit/core/test_naming.py` | `TestManagedDatasetApiName` 覆盖推导 + pattern 合规 + 非法输入 |
| `tests/unit/services/test_ontology_service.py` | `test_define_managed_idempotent_dataset_governance` + VIRTUAL/MANAGED 治理记录断言 |
| `tests/unit/services/test_ontology_service_batch.py` | `test_define_batch_managed_creates_dataset_governance` / `_backfills_property_backing_mapping` / `_preserves_explicit_backing_mapping` / `_virtual_skips_dataset_governance_and_backfill` / `_idempotent_dataset` |

### 与用户问题(「ObjectType-dataset 关联未回填 `backing_mapping.dataset_api_name`」)的关系

两者同源:定义 MANAGED 对象时既漏了治理记录(本 bug 文档),也漏了把属性自动绑定到该对象自有托管数据集(用户标题)。本次一并修复——治理记录写入后,属性 backing_mapping 也回填到同一个 dataset_api_name,这样:

1. `GET /datasets` 能列出所有 MANAGED 对象的数据集(前端「数据对接」页不再空)
2. `link_dataset` 第一步 `get_dataset(dataset_api_name)` 不再抛 `NotFoundError`
3. 对象详情/向导编辑回填时 `properties[].backing_mapping.dataset_api_name` 有值,前端徽章能正确显示「已关联」

### 幂等性

`metadata.create_dataset` 在 api_name 已存在时返回现有记录(不抛 409),所以重复 define 安全。属性 backing_mapping 回填发生在 ORM 构造期(单事务原子提交),重复 define 会走 `ConflictError` 路径在回填之前就被拦住。

### 注意:与 sync-task 链路的并存

benchmark 脚本(`benchmark/scripts/02_setup_pipeline.py`)走的是另一条 MANAGED dataset 产生路径:外部表 → sync task → Iceberg 托管表,sync task 写治理记录时 dataset api_name = `"airline" + ot_api`(带前缀,如 `airlineFlight`),但 SeaTunnel SYNC 管道实际写入的 Iceberg 表名是 `dataset_api_name.lower()` = `airlineflight`(全小写)。本修复针对的是**不经 sync task 直接 define_object_type** 的场景(对象自带托管数据集),推导出全小写 dataset api_name,与 SeaTunnel 管道的小写约定一致。两条路径互不干扰(前缀不同),且 `link_dataset` 显式指定 `dataset_api_name`,用户可按需绑定任一 dataset。

### Schema 页验证(2026-06-26)

多词对象 `FlightStatusLog`(PascalCase)经修复后:PG 治理记录 `flightstatuslog`(全小写)→ Iceberg 物理表 `flightstatuslog`(全小写)→ `GET /api/datasets/flightstatuslog/schema` 返回 200 + 列定义(`logId`/`flightCode`/`status`)→ Trino `SELECT count(*) FROM iceberg.ontology.flightstatuslog` 可达(返回 0)→ 前端「数据集详情 → Schema」页正确渲染物理列表格。修复前(首版 camelCase / 更早的 PascalCase)均因 Iceberg 表名大小写不一致导致 Schema 页「加载失败: Dataset not found」。
