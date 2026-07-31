# 本体删除治理与数据层命名空间隔离 - 设计方案

> **版本**: v5.2（最终：物理隔离 + 删除治理，遵循架构约束）
> **参考**: Palantir Foundry OSv2 本体删除操作
> **状态**: 实施中
> **评审决策**（§十五）：
> - ✅ 5: 以 Dataset 为中心隔离物理资源（Palantir 远期方案落地）
> - ✅ 6: 依赖检测只提醒不阻断
> - ✅ 7: MCP tools 排除非正常状态（本体/对象/关系/动作）
> - ✅ 8: 恢复接口做，但恢复时不自动 re-provision
> - ✅ 9: 子资源全量 soft-delete（Ontology/ObjectType/LinkType/ActionType/PropertyDef）
> - ✅ 10: **Dataset 完全独立**——不随本体删除：`datasets` 记录不 soft-delete、不加 `deleted_at`；Iceberg 物理表不 drop。删除本体只清理 Doris idx 表 + SeaTunnel INDEX pipeline。
>   - **⚠️ 2026-07 去 SeaTunnel 化后订正**：SeaTunnel INDEX pipeline 已删除（T1.10），删除本体现在只清理 Doris idx 表（+ 图/时空投影按 capabilities 清理）。本文档其他提及「SeaTunnel 同步管道 / INDEX pipeline / `sync_<type>` pipeline」处均为历史描述，对应能力已由 ObjectIndexFunnel（Doris 写入）+ OutboxExecutor（Action 写入）取代，不再走 SeaTunnel。
> - ✅ 11: `MAIN`→`SYNC`、`INDEX_SYNC`→`INDEX` 枚举改名；~~`ACTION_CDC`/`PG_TO_KAFKA`/`KAFKA_TO_DORIS` 保持不变~~ **2026-07-08/10 已全部删除**（去 SeaTunnel 化）
> - ✅ 12: SYNC pipeline 名 `sync_{dataset_api_name}` 纳入本次改名（DatasourceService/SyncTask 体系）
> - ✅ 13: MCP `list_object_types`/`describe_object_type` 仅排除 `DEPRECATED` + `deleted_at`，不排除 `EXPERIMENTAL`
> **关联文档**:
> - [implementation-status.md §七-bis](../architecture/implementation-status.md) —— 已记录验证结论与遗留
> - [data-layer-design.md](./data-layer-design.md) —— 数据层架构
> - [index-acceleration-design.md](../architecture/index-acceleration-design.md) —— Doris 索引加速
> - [reference.md](../reference.md) —— 第 26-30 迭代对齐
> - [CLAUDE.md](../../CLAUDE.md) —— 项目编码规范与红线

---

## 目录

- [一、问题陈述](#一问题陈述)
- [二、现状与根因](#二现状与根因)
- [三、设计目标与分层规划](#三设计目标与分层规划)
- [四、命名规范设计（本次实施）](#四命名规范设计本次实施)
- [五、Ontology + 子资源生命周期状态机（本次实施）](#五ontology--子资源生命周期状态机本次实施)
- [六、删除前置校验与依赖检测（本次实施）](#六删除前置校验与依赖检测本次实施)
- [七、Soft-delete 与冷却窗口（本次实施）](#七soft-delete-与冷却窗口本次实施)
- [八、MCP Tools 状态过滤（本次实施）](#八mcp-tools-状态过滤本次实施)
- [九、四层物理清理时序](#九四层物理清理时序)
- [十、改动范围与影响面](#十改动范围与影响面)
- [十一、数据迁移策略](#十一数据迁移策略)
- [十二、失败语义与补偿](#十二失败语义与补偿)
- [十三、测试策略](#十三测试策略)
- [十四、实施计划与风险](#十四实施计划与风险)
- [十五、评审决策汇总](#十五评审决策汇总)
- [附录A：Palantir 参考模型摘要](#附录a-palantir-参考模型摘要)
- [附录B：本次实施 vs 远期对齐](#附录b-本次实施-vs-远期对齐)

---

## 一、问题陈述

### 1.1 跨本体物理资源冲突（误删/互盖）

ObjectType 创建时直接在 Gravitino/Iceberg 注册物理表（table name = object_type_api_name），同时建 Doris 索引表（`idx_<type>`）和 SeaTunnel 同步管道（`sync_<type>`）。三者均不含本体维度。

`ObjectType.api_name` 仅在单本体内唯一（`UniqueConstraint("ontology_id", "api_name")`），跨本体可重名；`Ontology.api_name` 才是全局唯一（`unique=True`）。

**实测冲突面**：`asset`（20 本体共享）、`device`（19）、`sensor`（2）。

后果：删除任一本体 → 误删其他本体的物理资源（Doris 表/Iceberg 表/SeaTunnel pipeline）。

### 1.2 物理资源清理缺失

`delete_object_type` / `delete_ontology` 只清 Doris + SeaTunnel，不 drop Iceberg 表。Dataset 是独立元数据，**设计上不随本体/ObjectType 删除而清理**（见决策10）：删除本体只清理 Doris idx 表 + SeaTunnel INDEX pipeline，Iceberg 物理表与 `datasets` 记录均保留。

### 1.3 删除治理缺失

- Ontology 无 status / deleted_at → 无法 Deprecate 前置、无法 soft-delete
- 子资源（ObjectType/LinkType/ActionType/PropertyDef）无 deleted_at → 父资源 soft-delete 后子资源硬删，恢复失败
- MCP tools 无状态过滤 → 外部 Agent 可能引用已弃用/已删除资源
- 无删除前影响评估 → 用户不知道删本体会影响什么

### 1.4 根因：以 ObjectType 为物理资源锚点，而非 Dataset

当前架构的核心问题：ObjectType **自己创建并拥有**物理表（Iceberg/Doris/SeaTunnel），Dataset 只是可选的属性→列映射。

而 Palantir 的模型是：**Dataset 是独立的物理资源**，ObjectType 映射到一个或多个 Dataset。Ontology 是语义层，不拥有数据。隔离在 Dataset 层级（文件系统 RID/路径），不依赖表名。

**本方案的核心改造**：物理资源名加 ontology 前缀（Doris/SeaTunnel/S3），Iceberg 表名依赖 Dataset api_name 的 UNIQUE 约束。Dataset 保持独立，不感知 Ontology。

---

## 二、现状与改造方向

### 2.1 Dataset 当前角色（改造前）

Gaia 已有 Dataset（`DatasetGovernanceModel` / `datasets` 表），但当前仅作为**可选的属性→物理列映射**，不参与物理资源生命周期：

- ObjectType 创建时不创建 Dataset（`define_object_type_batch` 直接调 Gravitino REST 建 Iceberg 表，不写 `datasets` 表）
- `DatasetGovernanceModel` 无 `ontology_id` 字段——Dataset 是全局平台级的，不归属任何本体
- Dataset API 是独立的：`POST /api/datasets` → 创建；`DELETE /api/datasets/{name}` → 删除
- `link_dataset` 是独立的后续操作（前端 DatasetLinkDialog），在 ObjectType 创建后手动调用
- 物理资源隔离完全依赖表名（Iceberg/Doris/SeaTunnel 都用 `object_type_api_name`）

**这导致**：删除本体的物理清理需要遍历 ObjectType 逐一清理 Iceberg/Doris/SeaTunnel，且 Dataset 残留。

### 2.2 改造方向：Dataset 成为 ObjectType 的强制性物理后端

对齐 Palantir 模型：

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| ObjectType 创建 | 直接调 Gravitino REST 建 Iceberg 表 | 先创建 DatasetGovernance 记录 → Dataset 负责 Gravitino/Iceberg 注册 |
| Dataset 归属 | 无归属（独立平台资产） | **不变**——Dataset 不感知 Ontology/ObjectType，保持独立 |
| ObjectType→物理表的关系 | ObjectType 创建时直接建 Iceberg 表，表名 = object_type_api_name | ObjectType 创建时不直接建表。改为：先创建 Dataset（独立记录，api_name 用户定义，不加 ontology 前缀）→ Gravitino 注册 Iceberg 表（**name = dataset.api_name**）→ PropertyDef.physical_mapping.table_name 指向该物理表名 |
| Dataset 独立性 | api_name 无 ontology 信息（独立平台资产） | **不变**——Dataset api_name 保持独立，不编码 ontology/object_type 信息 |
| 物理资源命名 | `object_type_api_name`（冲突） | Doris/SeaTunnel/S3 = `{ontology}__{type}`（隔离）；Iceberg 表名 = dataset.api_name（Dataset 自身 UNIQUE 保证隔离） |
| 物理资源清理 | 遍历 ObjectType 逐个清理 | 遍历 ObjectType → 清其关联的物理表（Iceberg/Doris/SeaTunnel）。Dataset 独立管理，可选是否随 Ontology 删除 |
| 存储隔离 | 表名级别（冲突） | 物理资源注册层（Gravitino REST name + Doris 表名 + SeaTunnel pipeline 名）— 在此处加 ontology 前缀 |
| S3 路径 | `s3://ontology-warehouse/{type}` | `s3://ontology-warehouse/{ontology}/{type}/` |

### 2.3 现有对象类型与 Dataset 的迁移

当前有 ObjectType 但无对应 Dataset 记录。改造时：

- 为每个已存在的 MANAGED ObjectType **自动创建对应的 DatasetGovernance 记录**（migration 脚本，一次性）
- Dataset api_name = `{ontology_api_name}__{object_type_api_name}`
- 对于已有 `physical_dataset_api_name` 的 PropertyDef（通过 link_dataset 绑定过），保留现有映射关系
- Doris idx 表 / SeaTunnel INDEX pipeline 维持现有清理逻辑（`_deprovision_index`），**仅在本体删除时触发**；Dataset 记录与 Iceberg 表不动（决策10）

**迁移成本**：当前所有表均为空壳（§1.5 已核实），无需数据搬迁。

### 2.4 各模型 soft-delete 支持现状

| 模型 | status 列 | deleted_at 列 | 状态枚举 |
|------|----------|--------------|---------|
| Ontology | ❌ | ❌ | — |
| ObjectType | ✅ | ❌ | ACTIVE / ENDORSED / EXPERIMENTAL / DEPRECATED |
| ActionType | ✅ | ❌ | ACTIVE / DEPRECATED |
| LinkType | ❌ | ❌ | — |
| PropertyDef | ❌ | ❌ | — |
| Dataset | ❌ | ❌ | —（**本次不加**，决策10：Dataset 独立） |

### 2.5 数据分布（迁移成本零）

- Gravitino `table_meta` 无未删除记录
- Doris 9 张 `idx_*` 表全部 0 行
- `object_state` / `object_links` 无实例数据

**无真实业务数据需迁移。**

---

## 三、设计目标与分层规划

### 3.1 本次实施

**A. 架构层：隔离规范**：
- **A1 物理隔离**：Doris idx 表名 + SeaTunnel pipeline 名 + S3 路径含 ontology 前缀。Iceberg 表名 = Dataset api_name（用户自定义，全局 UNIQUE），不编码 ontology。
- **A2 Dataset 保持独立**：遵循原则 1（单向引用），`DatasetGovernanceModel` 不加 `ontology_id`/`object_type_id`
- **A3 物理清理补齐**：遍历 ObjectType 清其关联的 Doris idx 表 + SeaTunnel INDEX pipeline；**不清 Iceberg 表/不动 Dataset**（决策10）
- **A4 内部枚举统一**：`MAIN`→`SYNC`, `INDEX_SYNC`→`INDEX`

**B. 删除治理层**（同 v3.0）：
- **B1 生命周期状态机**：Ontology + ObjectType + LinkType + ActionType + PropertyDef 全量支持 `status` + `deleted_at`
- **B2 Deprecate 前置**：Ontology ACTIVE 不可直接删
- **B3 前置依赖检测**：Impact API 列出级联影响（Dataset 数量、Doris/Iceberg/SeaTunnel 资源）
- **B4 Soft-delete 冷却窗口**：删除设 `deleted_at`，7 天冷却，POST `/restore` 恢复
- **B5 MCP 状态过滤**：所有 list/describe 排除非正常状态
- **B6 前端区分**：sidebar 区分状态；详情页展示状态标签 + 操作按钮

**C. 不实施**：变更分支、血缘扫描阻断、后台 reaper、审计入库、恢复时自动 re-provision

---

## 四、物理隔离方案（遵循架构约束）

### 4.1 架构约束引用

来自 `dataset-ontology-binding.md` 和 `architecture_plan.md` 的强制约束：

| 约束 | 来源 | 含义 |
|------|------|------|
| 原则 1：单向引用 | dataset-ontology-binding.md §2.2 | Dataset 侧不得有 `ontology_id` / `object_type_id` 反向引用 |
| 原则 2：先登记后绑定 | 同上 | 本体对象只能绑定已登记的数据集，不能凭空创建 Iceberg 表 |
| 各层红线 | architecture_plan.md §十 | Dataset(Iceberg) 存全量明细；Pipeline 做数据采集/同步，不做元数据管理 |

### 4.2 隔离策略

**Iceberg 表名 = Dataset 的 api_name**（不编码 ontology/object_type 信息）。Dataset api_name 靠 `datasets` 表 UNIQUE 约束保证全局唯一——不同本体的 ObjectType 只要绑定不同 Dataset，物理隔离自然成立。

| 实体 | 命名 | 是否含 ontology 信息 | 隔离机制 |
|------|------|--------------------|---------|
| **Dataset api_name** | 用户自定义（如 `order_raw`），全局 UNIQUE | ❌ | UNIQUE 约束 |
| **Iceberg 表名**（Gravitino REST） | `{dataset_api_name}` | ❌ | 依赖 Dataset UNIQUE |
| **Doris idx 表** | `idx_{ontology}__{type}` | ✅ | 表名内嵌 |
| **SeaTunnel pipeline** | 见 §4.3 | 见 §4.3 | 见 §4.3 |
| **S3/RustFS 路径** | `s3://ontology-warehouse/{ontology}/{type}/` | ✅ | 目录层级 |

### 4.3 SeaTunnel Pipeline 两类命名

| Pipeline | 数据流 | pipeline 名 | 内部枚举（`PipelineDef.type`） | 含 ontology？ |
|----------|--------|------------|-------------------------------|-------------|
| SYNC | 数据源 → Iceberg | `sync_{dataset_api_name}` | `SYNC` | ❌（Dataset 全局唯一） |
| INDEX | Iceberg → Doris | `index_{ontology}__{type}` | `INDEX` | ✅（Doris idx 表需按 ontology 隔离） |

> **依据**：`architecture_plan.md` §4.5 定义两类 pipeline；§9.2 要求物理隔离。MAIN pipeline 消费者是 Dataset（已全局唯一），INDEX_SYNC 消费者是 Doris idx 表（ObjectType 维度，需 ontology 前缀防冲突）。

### 4.4 命名生成模块

```python
# src/ontology/core/naming.py（新增）

def sync_pipeline(dataset_api_name: str) -> str:
    """SYNC pipeline 名：sync_{dataset}（数据源→Iceberg，Dataset 全局唯一）"""
    return f"sync_{dataset_api_name}"

def doris_index_table(ontology: str, object_type: str) -> str:
    """Doris 索引表名（需 ontology 前缀防冲突）"""
    return f"idx_{ontology}__{object_type}"

def index_pipeline(ontology: str, object_type: str) -> str:
    """INDEX pipeline 名：index_{ontology}__{type}（Iceberg→Doris，需 ontology 前缀）"""
    return f"index_{ontology}__{object_type}"

def iceberg_s3_location(ontology: str, object_type: str) -> str:
    """RustFS S3 存储路径"""
    return f"s3://ontology-warehouse/{ontology}/{object_type}"
```

> **注意**：不包含 `iceberg_table_name` 或 `dataset_api_name(ontology, type)` —— Iceberg 表名直接用 Dataset 的 api_name（用户定义，全局 UNIQUE），不编码 ontology/object_type 信息。

> **枚举一致性**：`PipelineDef.type` 的字面量同步改为 `Literal["SYNC", "INDEX", "ACTION_CDC", "PG_TO_KAFKA", "KAFKA_TO_DORIS"]`（仅 `MAIN`→`SYNC`、`INDEX_SYNC`→`INDEX` 改名，其余三个保持不变）。同时修正以下代码中的常量/注释：`PIPELINE_MAIN_TEMPLATE` → `PIPELINE_SYNC_TEMPLATE`；`create_main_pipeline` → 并入已有 `create_sync_pipeline`（SYNC 类，数据源→Iceberg）；`create_index_sync_pipeline` → `create_index_pipeline`（INDEX 类，Iceberg→Doris）；`update_sync_pipeline` → `update_index_pipeline`；`IndexSyncService`/`DatasourceService`/`OntologyService` 中的注释（`MAIN`/`INDEX_SYNC` → `SYNC`/`INDEX`）。`sea_tunnel_engine.py` 内 5 处硬编码 `type="MAIN"`/`type="INDEX_SYNC"` 同步改名。

### 4.5 ObjectType 创建流程（先登记 Dataset，后注册物理表）

```
define_object_type_batch(ontology_api_name, data):
  │
  ├─ 1. PG 元数据（现有，不变）
  │   ├─ INSERT object_types
  │   ├─ INSERT properties
  │   └─ INSERT links
  │
  ├─ 2. 创建/绑定 Dataset（先登记后绑定）
  │   ├─ 若用户提供了已有 Dataset api_name → 校验存在 + kind=MANAGED
  │   ├─ 若用户未提供 → 自动创建新 Dataset：
  │   │     INSERT datasets (
  │   │       api_name = data.dataset_api_name or auto_generate(),  # 用户命名或自动生成
  │   │       kind = "MANAGED",
  │   │       storage_location = naming.iceberg_s3_location(ont, type)
  │   │     )
  │   │     ⚠️ 不加 ontology_id / object_type_id（违反单向引用原则）
  │   └─ ObjectType 的 PropertyDef.physical_mapping.table_name = dataset.api_name
  │
  ├─ 3. Gravitino Iceberg 注册（表名 = Dataset api_name）
  │   └─ GravitinoRegistry.register_dataset(
  │       schema = "ontology",
  │       name = dataset.api_name,           # 不编码 ontology/type
  │       location = dataset.storage_location, # s3://.../{ont}/{type}/
  │       columns = [...]
  │     )
  │
  └─ 4. Doris 索引表 + INDEX_SYNC pipeline（需 ontology 前缀）
      └─ IndexSyncService.provision(
          ontology_api_name, object_type_api_name
        ) → 使用 naming.doris_index_table / naming.index_sync_pipeline
```

### 4.6 Dataset 与 ObjectType 的绑定关系

遵循"原则 2：先登记后绑定"：
- ObjectType 创建时需指定一个已存在的 Dataset api_name（MANAGED），或自动创建一个新 Dataset
- 一个 Dataset 可被多个 ObjectType 引用（如多本体共享同一数据集），但不推荐
- Dataset 不感知 ObjectType/Ontology（原则 1：单向引用）
- VIRTUAL ObjectType 绑定 VIRTUAL Dataset（外部表代理）

### 4.7 标识符安全性

- 拼接后名通过 `_validate_identifier`（`[A-Za-z_][A-Za-z0-9_]*`）✅
- api_name 在上游 schema 已校验，无注入风险
- 表名长度 ≤ 255 ✅

---

## 五、Ontology + 子资源生命周期状态机

### 5.1 状态模型（全部子资源统一）

| 模型 | 新增字段 | 状态枚举 | 默认 |
|------|---------|---------|------|
| Ontology | `status VARCHAR(20)`, `deleted_at TIMESTAMPTZ` | `ACTIVE`, `DEPRECATED` | `ACTIVE` |
| ObjectType | `deleted_at TIMESTAMPTZ` | 已有 status（ACTIVE/ENDORSED/EXPERIMENTAL/DEPRECATED） | 已有 |
| ActionType | `deleted_at TIMESTAMPTZ` | 已有（ACTIVE/DEPRECATED） | 已有 |
| LinkType | `status VARCHAR(20)`, `deleted_at TIMESTAMPTZ` | `ACTIVE`, `DEPRECATED` | `ACTIVE` |
| PropertyDef | `status VARCHAR(20)`, `deleted_at TIMESTAMPTZ` | `ACTIVE`, `DEPRECATED` | `ACTIVE` |

> **Dataset 不参与 soft-delete**（决策10）：`datasets` 表不加 `deleted_at`/`status`，删除本体时不标记、不删除 Dataset 记录。
>
> **PropertyDef** 的 DEPRECATED 用于标记已删除/弃用的属性。属性不支持独立 soft-delete（随 ObjectType 级联）。

### 5.2 状态流转

```
[ACTIVE] ──deprecate──> [DEPRECATED] ──delete──> [soft-deleted: deleted_at IS NOT NULL]
                                                       │
                                        ┌──────────────┘
                                        │ 冷却期满（7天）+ 清理脚本
                                        ▼
                                  [物理删除: PG 行清除]
```

### 5.3 Ontology soft-delete 时子资源级联逻辑

```python
async def delete_ontology(api_name: str):
    # 1. 校验
    onto = get_ontology(api_name)
    if onto.status == "ACTIVE":
        raise ConflictError("请先弃用（Deprecate）本体")

    # 2. 级联标记子资源
    now = datetime.now(UTC)
    # ObjectType
    UPDATE object_types SET deleted_at = :now WHERE ontology_id = :onto.id
    # LinkType
    UPDATE link_types SET deleted_at = :now WHERE ontology_id = :onto.id
    # ActionType
    UPDATE action_types SET deleted_at = :now WHERE ontology_id = :onto.id

    # 3. 标记本体自身
    onto.deleted_at = now

    # 4. 物理资源清理（best-effort，见 §八）
    #    仅清理索引层资源：Doris idx 表 + SeaTunnel INDEX pipeline
    #    Iceberg 物理表 / Dataset 记录不动（决策10）
    for ot in object_types:
        deprovision_index(onto.api_name, ot.api_name)
```

**恢复（POST /restore）**：反向操作，本体 + 所有子资源 `SET deleted_at = NULL`。status 保持 DEPRECATED（恢复后需手动切回 ACTIVE）。

### 5.4 前端状态展示

| 状态 | Sidebar | 详情页头部 |
|------|---------|----------|
| ACTIVE | 正常显示 | 正常 + "弃用"按钮 |
| DEPRECATED | 显示，badge 黄色/灰色 + 弃用图标 | 显示"已弃用"标签 + "删除" / "恢复为活跃"按钮 |
| soft-deleted | **不显示**（需 `include_deleted=true`） | 不可访问（404） |

### 5.5 路由变更

| 方法 | 路径 | 变更 |
|------|------|------|
| PATCH | `/ontologies/{api_name}` | 允许更新 `status` 字段 |
| DELETE | `/ontologies/{api_name}` | 软删除（设 `deleted_at`），拒绝 ACTIVE |
| **POST** | `/ontologies/{api_name}/restore` | **新增**，恢复软删除的本体及全部子资源 |
| GET | `/ontologies` | 默认 `WHERE deleted_at IS NULL`；`?include_deleted=true` 返回全部 |
| **GET** | `/ontologies/{api_name}/impact` | **新增**，级联影响报告（§六） |

---

## 六、删除前置校验与依赖检测

### 6.1 流程

用户点击删除 → `GET /ontologies/{api_name}/impact` → 前端 ConfirmDialog 展示影响 + 状态提示 → 用户输入 api_name 确认 → `DELETE /ontologies/{api_name}`

### 6.2 Impact API

```python
# GET /ontologies/{api_name}/impact → ImpactReport

class ImpactItem(BaseModel):
    resource_type: str       # "object_type" | "action_type" | "link_type" | ...
    count: int
    label: str

class ImpactReport(BaseModel):
    api_name: str
    status: str              # ACTIVE | DEPRECATED
    impacts: list[ImpactItem]
    can_delete: bool         # False when status == "ACTIVE"
    blocked_reason: str | None
```

**扫描范围**（PG 实时查询，不依赖外部服务）：

| 资源 | 示例 label |
|------|-----------|
| ObjectType | "11 个对象类型（含 63 个属性）" |
| ActionType | "3 个动作定义" |
| LinkType | "27 个关系类型" |
| 对象实例 | "152 条对象实例数据" |
| 关系实例 | "89 条关系实例数据" |
| Doris 索引表 | "11 张 Doris 索引表" |
| Iceberg 物理表 | "11 个 Iceberg 数据集" |
| SeaTunnel pipeline | "11 条同步管道" |

### 6.3 校验规则

- `status == "ACTIVE"` → `can_delete = False`, `blocked_reason = "本体状态为 ACTIVE，请先弃用（Deprecate）"`
- `status == "DEPRECATED"` → `can_delete = True`，列出全部 `impacts`
- **依赖存在不阻断**（只提醒），远期可加管理员配置

### 6.4 前端 ConfirmDialog

分三部分：
1. **状态提示**：ACTIVE → 确认按钮置灰 + "请先弃用"。DEPRECATED → 提示冷却期可恢复。
2. **影响清单**：结构化 `impacts` 列表（替换当前 `[unknown]` 字符串数组）。
3. **输入确认**：沿用 HIGH 级 type-to-confirm。

Toast：`本体已删除，7天内可恢复。`

---

## 七、Soft-delete 与冷却窗口

### 7.1 配置

```python
# src/ontology/config/settings.py
class Settings:
    soft_delete_retention_days: int = 7
```

### 7.2 删除执行

1. PG `deleted_at` 设置（本体 + 全部子资源）——**事务内**，失败全部回滚。
2. 物理资源清理（Doris idx 表 + SeaTunnel INDEX pipeline）——在事务提交后执行（best-effort，失败不阻断）。**不清理 Iceberg/Dataset**（决策10）。

### 7.3 恢复执行

`POST /ontologies/{api_name}/restore`：
1. 验证 `deleted_at IS NOT NULL`（否则 404）。
2. 设置本体 + 全部子资源 `deleted_at = NULL`（事务内）。
3. 物理资源**不自动重建**（已 drop）。前端提示"物理资源已清理，需重新同步"。

### 7.4 物理删除（冷却期满后）

`scripts/cleanup_soft_deleted_ontologies.py`（人工执行）：
- 查询 `deleted_at < NOW() - INTERVAL '7 days'`
- 物理 PG DELETE（CASCADE 子资源）
- dry-run 模式默认，`--execute` 才真删

---

## 八、MCP Tools 状态过滤

### 8.1 规则

所有 MCP tool 默认排除以下状态的资源：
- `status = 'DEPRECATED'`
- `deleted_at IS NOT NULL`

> **不排除 `EXPERIMENTAL`**（决策13）：ObjectType 的 `EXPERIMENTAL` 语义为"实验性但可用"，对 Agent 仍可查可用，故 MCP `list_object_types`/`describe_object_type` 不排除。`InterfaceType` 本就 EXPERIMENTAL 且不在 MCP 暴露，不涉及。

### 8.2 影响的 tools

| Tool | 过滤逻辑 |
|------|---------|
| `list_ontologies` | `WHERE deleted_at IS NULL AND status != 'DEPRECATED'` |
| `list_object_types` | `WHERE deleted_at IS NULL AND status != 'DEPRECATED'`（不排除 EXPERIMENTAL，决策13） |
| `describe_object_type` | 404 若 `deleted_at IS NOT NULL OR status = 'DEPRECATED'`（不排除 EXPERIMENTAL） |
| `list_link_types` | `WHERE deleted_at IS NULL AND status != 'DEPRECATED'` |
| `describe_link_type` | 404 若 `deleted_at IS NOT NULL` |
| `filter_object` / `get_object` / `aggregate_objects` | 上游 ontology/object_type 为正常状态方可查询 |

### 8.3 实现方式

在 `OntologyService` 的 list/get 方法层面加参数 `include_non_active: bool = False`。AG-UI/REST 路径默认 `False`（排除非正常），MCP 路径也默认 `False`。需要时显式传 `True`（如 Web UI 的"已删除"列表、管理员页面）。

---

## 九、四层物理清理时序

### 9.1 清理流程（按 ObjectType 遍历清理索引层资源）

Delete Ontology 时，遍历该本体下所有 MANAGED ObjectType，**仅清理索引层资源**（Doris idx 表 + SeaTunnel INDEX pipeline）。**Iceberg 物理表与 `datasets` 记录不动**（决策10：Dataset 完全独立）。

```
DELETE /ontologies/{api_name}
  ├─ 1. 前置校验（status + impact）                               —— 同步
  ├─ 2. PG 事务：soft-delete 标记                                 —— 同步
  │     ├─ SET deleted_at on ontology
  │     ├─ SET deleted_at on object_types (WHERE ontology_id)
  │     ├─ SET deleted_at on link_types (WHERE ontology_id)
  │     └─ SET deleted_at on action_types (WHERE ontology_id)
  │     （datasets 不动——决策10）
  ├─ 3. 物理资源清理（事务提交后，best-effort，遍历 ObjectType） —— 准同步
  │     for each MANAGED object_type:
  │       ├─ SeaTunnelEngine.stop(index_pipeline({ont}, {type}))   # index_{ont}__{type}
  │       └─ DorisIndexStore.drop_index_table({ont}, {type})       # idx_{ont}__{type}
  │     （不 drop Iceberg 表、不动 datasets 记录）
  └─ 4. 返回 204
```

物理删除（冷却期满后）：清理脚本物理 DELETE PG 行（CASCADE 子资源）。**不清理 Iceberg/Dataset**（从未动过）。

> **恢复语义**（§七.3）：恢复仅清空本体 + 子资源的 `deleted_at`。Doris idx 表 + INDEX pipeline 已被 drop，**不自动重建**（决策8），需用户手动 re-provision。Iceberg 物理表/Dataset 未受影响，恢复后 ObjectType 可直接读回原数据。

---

## 十、改动范围与影响面

### 10.1 文件清单

| 文件 | 改动 |
|------|------|
| `core/naming.py` | **新增**：Dataset 中心化的统一命名生成 |
| `core/models/datasource.py` | **不加 `deleted_at`**（决策10：Dataset 独立）；现有字段不变 |
| `core/models/ontology.py` | Ontology/LinkType 加 `status` + `deleted_at`；ObjectType/ActionType/PropertyDef 加 `deleted_at` |
| `core/schemas/ontology.py` | schema 加字段 + `ImpactReport`/`ImpactItem` |
| `core/schemas/datasource.py` | **不加 `deleted_at`**（决策10）；不加 `ontology_id`/`object_type_id` |
| `config/settings.py` | 加 `soft_delete_retention_days` |
| `layers/index/doris_index_store.py` | 所有方法签名加 `ontology_api_name`（通过 naming 模块生成表名） |
| `layers/pipeline/sea_tunnel_engine.py` | 方法签名加 `ontology_api_name`（INDEX 类）；`create_main_pipeline`并入`create_sync_pipeline`、`create_index_sync_pipeline`→`create_index_pipeline`、`update_sync_pipeline`→`update_index_pipeline`；模板常量改名；SYNC pipeline 名改为 `sync_{dataset_api_name}`（含 DatasourceService 调用点） |
| `layers/catalog/gravitino_registry.py` | `register_dataset` 调用方传新 name + S3 path（通过 naming 模块） |
| `layers/metadata/postgres_meta_store.py` | list 方法加 `include_non_active` 过滤 |
| `layers/dataset/iceberg_store.py` | **无需新增 `drop_table_if_exists`**（决策10：不 drop Iceberg 表） |
| `services/index_sync_service.py` | provision/rebuild/deprovision/backfill/sync_now 加 `ontology_api_name`；修正 deprovision 停错 pipeline 名的 bug（`sync_{type}`→`index_{ont}__{type}`） |
| `services/datasource_service.py` | SYNC pipeline 名改用 `naming.sync_pipeline(dataset_api_name)`；`sync_tasks.pipeline_name` 值对齐 |
| `services/ontology_service.py` | ① define_object_type 不直接建 Iceberg 表，改为创建 Dataset ② delete 软删+级联子资源（**不动 Dataset**） ③ restore ④ impact ⑤ 补物理清理（仅 Doris idx + INDEX pipeline） |
| `services/object_query_service.py` | 透传 ontology_api_name 到 Doris 调用 |
| `routes/ontology/__init__.py` | DELETE/PATCH/GET 软删除；新增 restore + impact |
| `protocols/mcp_server.py` | 无需改（底层过滤生效） |
| `tools/toolsets/metadata.py` | 无需改 |
| `pages/OntologyWorkspace.tsx` | 详情页头加 status 标签 + Deprecate/Delete/Restore；ConfirmDialog 展示 impact |
| `components/OntologySidebar.tsx` | 过滤 deleted，区分状态 |
| `api/client.ts` | 新增 restore/impact/deprecate API |
| `docs/design/ontology-namespace-isolation-and-cleanup.md` | 本文档 |
| `docs/architecture/implementation-status.md` | 更新路标 #9 |

### 10.2 Migration

```sql
-- Ontology
ALTER TABLE ontologies ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE ontologies ADD COLUMN deleted_at TIMESTAMPTZ;

-- ObjectType / ActionType / LinkType / PropertyDef
ALTER TABLE object_types ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE action_types ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE link_types ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE link_types ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE properties ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE properties ADD COLUMN deleted_at TIMESTAMPTZ;

-- Dataset：不加任何列（决策10：Dataset 完全独立，不参与 soft-delete）
```

### 10.3 关键陷阱

`OntologyService.define_object_type*` 内部变量 `api_name` = object_type 的 api_name，**不是** ontology。传给 IndexSyncService 必须用方法首参 `ontology_api_name`：

```python
# 旧（错——传了 type 名当 ontology 用）
self._index_sync.provision(api_name, ...)

# 新（正确）
self._index_sync.provision(ontology_api_name, api_name, ...)
```

---

## 十一、数据迁移策略

**零迁移成本**（§2.3 已核实）。

旧 `idx_<type>` / `sync_<type>` / `ontology.<type>` 空壳资源已由一次性脚本清理完成（原 `scripts/cleanup_legacy_index_resources.py`，迁移完成后已移除，保留于 git 历史）。

**不设兼容期**（无真实数据）。

---

## 十二、失败语义与补偿

| 阶段 | 失败行为 |
|------|---------|
| PG soft-delete 标记 | 回滚整个事务，返回 500 |
| 物理资源清理（Doris idx 表 + INDEX pipeline） | best-effort，`_log.warning` 含完整上下文，不阻断 PG 操作。**不清理 Iceberg/Dataset**（决策10） |
| 冷却期满物理删除 | 清理脚本逐条处理，失败跳过 + 记录，可重试 |

补偿表 + reaper 归入路标 #4（远期）。

---

## 十三、测试策略

### 13.1 新增测试

1. **跨本体隔离**：两本体各建 `asset` → 确认 Doris 独立表 `idx_ont1__asset` / `idx_ont2__asset` → 删本体1 → 本体2 表完好
2. **状态机**：ACTIVE 拒删（409）→ Deprecate 后允许 → 恢复后仍 DEPRECATED
3. **子资源级联**：本体删除 → 所有 ObjectType/LinkType/ActionType.deleted_at 同步设置 → 恢复后全部清空
4. **Impact API**：各种资源数量在报告中正确
5. **MCP 过滤**：`list_ontologies` 不返回 DEPRECATED/已删除本体
6. **物理清理**：软删除后确认 drop 了 Doris idx 表 + stop 了 INDEX pipeline；**确认 Iceberg 表 / `datasets` 记录仍在**（决策10）
7. **命名单测**：`core/naming.py` 输出规范

---

## 十四、实施计划与风险

### 14.1 PR 拆分

| 阶段 | 内容 | 风险 |
|------|------|------|
| P1 | `core/naming.py` + Migration | 低 |
| P2 | 改 `DorisIndexStore` + `IndexSyncService` 签名 | 中 |
| P3 | 改 `SeaTunnelEngine` + `GravitinoRegistry` + `OntologyService` 调用点 | 中 |
| P4 | 改 `ObjectQueryService` 透传 | 低 |
| P5 | 软删除 + Deprecate 前置 + 子资源级联 + Impact + Restore + MCP 过滤 | 中 |
| P6 | Iceberg 清理 + 前端交互 | 中 |
| P7 | 清理脚本 + 文档 | 低 |

### 14.2 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 子资源级联遗漏某个 FK 关系 | 中 | PG 外键约束 + 集成测试全覆盖 |
| 签名穿透遗漏调用点 | 中 | mypy + 全量测试 + grep |
| Iceberg drop 误删 | 高 | dry-run 脚本先验证 |

---

## 十五、评审决策汇总

| # | 问题 | 决策 |
|---|------|------|
| 1 | ❓ 实施范围 | ✅ 远期方案落地：Dataset 为 ObjectType 强制性物理后端 |
| 2 | ✅ Deprecate 前置 | 本次做 |
| 3 | ✅ 依赖检测 | 级联快照提醒（不阻断） |
| 5 | ✅ 物理资源隔离 | Doris/SeaTunnel/S3 含 ontology 前缀；Iceberg 表名 = Dataset api_name（不编码 ontology） |
| 6 | ✅ 依赖检测阻断 | 只提醒 |
| 7 | ✅ MCP 状态过滤 | 所有 list/describe 排除非正常状态 |
| 8 | ✅ 恢复 + re-provision | 恢复接口做；不自动重建物理表 |
| 9 | ✅ 子资源 soft-delete | ObjectType/LinkType/ActionType/PropertyDef 全量（**Dataset 独立，不参与**，决策10） |
| 10 | ✅ Dataset 独立性 | `datasets` 记录不 soft-delete、不加 `deleted_at`；Iceberg 物理表不 drop。删除本体只清 Doris idx 表 + INDEX pipeline |
| 11 | ✅ 枚举改名 | `MAIN`→`SYNC`、`INDEX_SYNC`→`INDEX`；其余三个保持不变 |
| 12 | ✅ SYNC pipeline 改名 | `sync_{dataset_api_name}` 纳入本次（DatasourceService/SyncTask 体系） |
| 13 | ✅ MCP EXPERIMENTAL | `list_object_types`/`describe_object_type` 不排除 EXPERIMENTAL |
| 14 | ✅ PR 拆分 | P1-7 按此实施 |
| 15 | ✅ 前端已删除列表 | API 支持 `include_deleted`，前端 tab 后续 |

---

## 附录A：Palantir 参考模型摘要

### A.1 前置校验

- 下游依赖全量扫描（9 类资源）
- 状态强校验（active 禁止直接删除）
- 级联自动标记（LinkType 同步待删）

### A.2 暂存待删（Staging）

- 变更分支隔离，主分支不受影响
- 可随时撤回
- 提交 Proposal 审批后合入

### A.3 四层清理时序

1. **同步（秒级）**：元数据永久删除
2. **准同步（分钟级）**：OSv2 存储分片入口注销，Funnel 写入关闭
3. **异步（小时级）**：物化数据集 + 合并数据集 Mark
4. **后台（天级）**：索引缓存 Sweep（冷却期 7 天可恢复）

---

## 附录B：本次实施能力清单

| 能力 | 状态 | 备注 |
|------|------|------|
| 物理隔离规范 | ✅ 本次 | Doris/SeaTunnel/S3 含 ontology 前缀；Iceberg 表名 = Dataset api_name |
| Iceberg 清理 | ❌ 不清理 | 决策10：Iceberg 物理表与 Dataset 记录不随本体删除 |
| Deprecate 前置 | ✅ 本次 | |
| 子资源全量 soft-delete | ✅ 本次 | ObjectType/LinkType/ActionType/PropertyDef（**Dataset 不参与**） |
| MCP 状态过滤 | ✅ 本次 | |
| Impact 提醒 + ConfirmDialog | ✅ 本次 | |
| Soft-delete + 恢复接口 | ✅ 本次 | |
| 物理清理脚本（冷却期满） | ✅ 本次 | |
| 变更分支隔离 | ❌ | BranchModel 已有，后续 |
| 血缘扫描阻断 | ❌ | 后续 |
| 后台 timed reaper | ❌ | 后续 |
| 审计日志入库 | ❌ | 路标 #4 |
| 补偿表 | ❌ | 路标 #4 |
| 恢复时自动 re-provision | ❌ | 后续 |
| 前端"已删除"tab | ❌ | API 支持，UI 后续 |
