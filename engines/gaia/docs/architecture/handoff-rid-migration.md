# 交接文档：采用 Palantir RID 身份模型 + 命名统一

> **文档性质**：交接文档（handoff），供开发人员执行
> **创建日期**：2026-07-15
> **决策来源**：图推理与虚拟表联邦系列调研（4 份调研报告 + Palantir 官方规范核实）
> **状态**：✅ 代码已全部实现（PR 1-5 完成，2026-07-26）。迁移后清理（Neo4j/geotime 存量重建）见 §十 runbook
> **关联文档**：
> - [三场景模拟分析](../research/three-scenarios-ontology-graph-federation.md) §身份模型决策注（决策权威源）
> - [graph-reasoning-design.md](graph-reasoning-design.md) §4.1 身份模型说明
> - [implementation-status.md](implementation-status.md) §十 命名规范表
> - [虚拟表填充 Neo4j 可行性调研](../research/virtual-table-neo4j-projection-feasibility.md)
> - [Palantir 动态本体映射 Neo4j 方案对照分析](../research/palantir-neo4j-mapping-proposal-comparison.md)
> - [Ontop 源码分析](../research/ontop-source-analysis.md)

---

## 一、决策概述

### 1.1 决策内容

Gaia 的对象身份模型从**裸 UUID 主键**改为**Palantir Resource Identifier (RID) 规范**，同时命名从 `vid` / `object_id` **统一为 `rid`**。

**新格式**：
```
ri.ontology.main.object.{uuid}
```

**示例**：
```
ri.ontology.main.object.c61d9ab5-2919-4127-a0a1-ac64c0ce6367
```

### 1.2 为什么改（核心论据）

| 旧方案的问题 | 新方案解决 |
|---|---|
| 裸 UUID（32 字符 hex）无类型语境，跨存储寻址要查元数据才知道属于什么 | RID 自描述，从串本身能解析出 service/type |
| `vid` 借自图数据库界 "Vertex ID" 术语，但未注明全称，且与 VIRTUAL storage_type 的 V 易混淆 | `rid` 是通用概念，对齐 Palantir 官方术语 |
| `object_id` 是中间过渡命名，与 PG 字段同名但语义不够 "Palantir" | `rid` 直接采用 Palantir 规范 |
| 无跨实例区分能力 | RID 带 instance 段，多租户/多环境不冲突 |

### 1.3 Palantir RID 规范（官方源核实）

**来源**：
- 开源 spec：[palantir/resource-identifier](https://github.com/palantir/resource-identifier)
- 官方文档：[Functions on objects · Object identifiers](https://palantir.com/docs/foundry/functions/object-identifiers/)

**格式**：
```
ri.<service>.<instance>.<type>.<locator>
```

四段，点分隔，各段 regex（来自开源 spec）：
| 段 | 含义 | regex | Gaia 取值 |
|---|---|---|---|
| `service` | 服务/应用命名空间 | `[a-z][a-z0-9\-]*` | `ontology` |
| `instance` | 部署实例（可空） | `([a-z0-9][a-z0-9\-]*)?` | `main`（单实例部署） |
| `type` | 资源类型 | `[a-z][a-z0-9\-]*` | `object`（对象实例） |
| `locator` | 定位串 | `[a-zA-Z0-9\-\._]+` | UUID（系统生成） |

**关键事实（交叉验证）**：
1. **Object RID 在创建时由系统分配**（"Ontology objects have a RID assigned to them when they are created, either from indexing a backing dataset or as part of an Action"）
2. **locator 是 UUID，不是 primary key**——社区实际例子 `ri.phonograph2-objects.main.object.48971f8a-fdff-4157-9cf4-aa3e98163be4`（标准 UUID 格式）
3. **新创建未持久化的对象 RID = undefined**（"Newly created objects always have a `rid` value of `undefined`"）
4. **应用层判等用 `(typeId, primaryKey)`，不用 RID**（官方推荐，因为 RID 可能 undefined）
5. **primary key 是独立的业务属性**，用户提供，**不是** RID 的 locator

### 1.4 核心设计原则：身份正交分离

这是本决策的**灵魂**，必须理解：

```
RID（系统身份）           primary key（业务身份）
─────────────────         ─────────────────────
系统自动分配                用户提供
格式 ri.ontology.main.object.{uuid}   业务值（如 employeeId=1001）
稳定不变（即使 pk 改了也不变）        可变
应用层透明                  应用层判等用
跨服务寻址                  业务语义

      正交分离：互不依赖，各管各的
```

**为什么 locator 不用 primary key**：
- 如果用 primary key 当 locator，primary key 一改 RID 就得变
- "RID 分配后不变"的稳定性保证就没了
- Palantir 明确把两者分离：RID 给系统/跨服务用，primary key 给业务层用

**应用层判等**：
```python
# ✅ 正确：用 (typeId, primaryKey)
def is_equal(o1, o2):
    return o1.type_id == o2.type_id and o1.primary_key == o2.primary_key

# ❌ 错误：用 rid（新创建对象 rid 可能 undefined）
def is_equal(o1, o2):
    return o1.rid == o2.rid
```

---

## 二、命名统一规则

### 2.1 全局命名变更

| 旧名 | 新名 | 说明 |
|---|---|---|
| `vid` | `rid` | 废弃（Vertex ID 缩写，无全称，与 VIRTUAL 的 V 混淆） |
| `object_id` | `rid` | 废弃（中间过渡命名） |
| `object_ids` | `rids` | 复数 |
| `source_object_id` | `source_rid` | object_links 字段 |
| `target_object_id` | `target_rid` | object_links 字段 |
| `source_vid` | `source_rid` | 方法参数 |
| `target_vid` | `target_vid` | 方法参数 |
| `candidate_vids` | `candidate_rids` | 方法参数 |
| `get_object_states_by_ids` | `get_object_states_by_rids` | 方法名 |
| `get_object_ids_by_type` | `get_rids_by_type` | 方法名 |
| `get_object_ids_by_pk` | `get_rids_by_pk` | 方法名 |
| `_resolve_vids_by_pk` | `_resolve_rids_by_pk` | 方法名 |
| `vid_batch_size` | `rid_batch_size` | settings 配置 |

### 2.2 与 `ontology.rid` 的关系

**`rid` 是通用概念，不区分**。

| 实体 | 字段名 | RID 格式 | 说明 |
|---|---|---|---|
| Ontology | `ontology.rid` | `ri.ontology.main.ontology.{uuid}` | 本体资源（已有字段 `String(255)`） |
| Object | `object_state.rid` | `ri.ontology.main.object.{uuid}` | 对象实例（本次改） |
| ObjectType | （未来）`object_type.rid` | `ri.ontology.main.object-type.{api_name}` | 对象类型资源 |
| LinkType | （未来）`link_type.rid` | `ri.ontology.main.link-type.{api_name}` | 链接类型资源 |

同名不冲突，靠 `type` 段区分。这是 Palantir 的做法（所有资源的 RID 字段都叫 `rid`）。

**现状**：`OntologyModel.rid` 当前是 `String(255), default=""`（多为空串，未真正使用 Palantir RID 格式）。本次**不改 ontology.rid**，只改 object 相关。ontology.rid 未来若启用，格式应为 `ri.ontology.main.ontology.{uuid}`。

### 2.3 不改的部分

| 项 | 为什么不改 |
|---|---|
| **Iceberg 表的 PK** | Iceberg 表 PK 是业务主键列（`backing_column`，如 `flight_id`），与 Palantir backing dataset 一致。RID 是 ontology 层身份，不在存储层。见 `iceberg_store.py:460` 注释："⚠️ PK 是业务主键的 backing_column (如 flight_id), 不是 object_id" |
| **元数据表的主键**（ontologies/object_types/link_types 等） | 这些表用 `id`（UUID hex）作主键，不是 object 实例身份。本次只改 object_state/object_links 的 object 身份。元数据表保持裸 UUID。 |
| **`ontology.rid`** | 已存在字段，语义不同（Ontology 资源），本次不动 |

---

## 三、VIRTUAL 对象的合成 RID

### 3.1 问题背景

VIRTUAL 对象（虚拟表，外部数据源的联邦代理）**不落地**（无 object_state，无 Iceberg 表），因此**没有系统分配的 RID**。但图遍历需要跨 VIRTUAL 节点寻址，必须有 rid。

### 3.2 合成方案

```
ri.ontology.main.virtual-object.{ontology_api_name}.{object_type_api_name}.{pk_value}
```

**示例**：
```
ri.ontology.main.virtual-object.supplychain.Order.ORD001
```

### 3.3 设计要点

| 要点 | 说明 |
|---|---|
| `type` 段用 `virtual-object` | 与 MANAGED 的 `object` 区分，水合时按 type 分流 |
| locator 嵌入 ont/ot/pk | 可逆解析，水合时能从 rid 解析出 PK 查 Trino |
| 符合 RID 规范外壳 | 不是 `virtual:xxx` 这种非规范格式，保持体系一致 |
| **不是系统身份** | VIRTUAL rid 是合成的，不保证稳定（外部源 PK 改了就变）。这是 VIRTUAL 的固有特性，与 MANAGED 的"rid 稳定不变"不同 |

### 3.4 水合分流逻辑

```python
from ontology.core.rid import parse_rid, is_managed_rid, is_virtual_rid

managed_rids = [r for r in rids if is_managed_rid(r)]      # type == "object"
virtual_rids = [r for r in rids if is_virtual_rid(r)]      # type == "virtual-object"

# MANAGED → Doris 主源点查（降级 Trino iceberg）
managed_objs = await object_query_service.load_by_ids(managed_rids)

# VIRTUAL → 解析出 PK → Trino 跨 catalog 联邦查外部源
virtual_by_ot = group_by_ot(virtual_rids)  # {("ont","Order"): [pk1, pk2]}
for (ont, ot), pks in virtual_by_ot.items():
    objs = await trino_query_external(ont, ot, pks)
```

---

## 四、当前代码现状（影响面清单）

### 4.1 主键生成点（唯一）

**文件**：`src/ontology/services/action_service.py`

```python
# Line 629（CREATE_OBJECT mutation）
obj_id = mutation.get("object_id", str(uuid.uuid4()))  # 当前：裸 UUID

# Line 1251（另一个生成点）
object_id = uuid.uuid4().hex  # 当前：32 字符 hex
```

**改后**：
```python
from ontology.core.rid import generate_object_rid
rid = generate_object_rid()  # ri.ontology.main.object.{uuid}
```

### 4.2 PG Schema（字段名 + 长度）

**文件**：`src/ontology/core/models/ontology.py`

| 表 | 字段 | 当前 | 改后 |
|---|---|---|---|
| `object_state` | `object_id` | `String(64), primary_key=True` | `rid: String(128), primary_key=True` |
| `object_links` | `source_object_id` | `String(64), index=True` | `source_rid: String(128), index=True` |
| `object_links` | `target_object_id` | `String(64), index=True` | `target_rid: String(128), index=True` |

**长度计算**：`ri.ontology.main.object.` (25字符) + UUID (36字符含连字符) = 61 字符。`String(128)` 留足余量（VIRTUAL rid 更长：`ri.ontology.main.virtual-object.supplychain.Order.ORD001` ≈ 70+ 字符）。

### 4.3 Neo4j Cypher（节点属性 + 约束）

**文件**：`src/ontology/layers/graph/neo4j_graph_store.py`（40 处）

当前节点用 `vid` 属性：
```cypher
CREATE CONSTRAINT {label}_vid_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.vid IS UNIQUE
MERGE (n:{label} {vid: $vid})
MATCH (s:{source_label} {vid: $source_vid}), (t:{target_label} {vid: $target_vid})
```

改后：
```cypher
CREATE CONSTRAINT {label}_rid_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.rid IS UNIQUE
MERGE (n:{label} {rid: $rid})
MATCH (s:{source_label} {rid: $source_rid}), (t:{target_label} {rid: $target_rid})
```

**Neo4j 存量数据**：清空重建。Neo4j 是派生副本，可全量 `rebuild_graph`。无生产数据。

### 4.4 Doris idx 表

**文件**：`src/ontology/layers/index/doris_index_store.py`

Doris idx 表（`idx_{ont}__{type}`）的主键列当前存 object_id（UUID hex）。改后存 rid（完整 RID 串）。列类型要确认是否够长（Doris VARCHAR 长度）。

### 4.5 全栈代码分布（精确统计）

#### Python 代码（src/ontology/）

| 文件 | 命中数 | 主要内容 |
|---|---|---|
| `services/action_service.py` | 88 | object_id 生成 + mutation 处理 |
| `layers/metadata/postgres_meta_store.py` | 73 | object_state/object_links CRUD + 方法名 |
| `services/object_set_executor.py` | 43 | IR 求值 + 水合 + `_resolve_vids_by_pk` |
| `services/object_query_service.py` | 10 | 查询路由 |
| `core/schemas/action.py` | 8 | Action schema |
| `services/project_sync_service.py` | 5 | 投影同步 |
| `layers/index/doris_index_store.py` | 5 | Doris 点查 |
| `core/models/ontology.py` | 5 | ORM 模型 |
| `services/outbox_executor.py` | 4 | 投影触发 |
| `services/authorization_service.py` | 3 | 权限校验 |
| `services/action_auth.py` | 3 | Action 权限 |
| `tools/toolsets/link_traversal.py` | 2 | 链接遍历工具 |
| `services/sync_flush_scheduler.py` | 1 | 同步刷新 |
| `services/action_validator.py` | 1 | Action 校验 |
| `layers/dataset/iceberg_store.py` | 1 | 注释（不改，见 2.3） |
| `core/schemas/query.py` | 1 | 查询 schema |
| `layers/graph/neo4j_graph_store.py` | 40（vid） | Cypher + 方法签名 |
| `layers/geotime/geotime_store.py` | 17（vid） | 时空存储 |
| `services/geotime_projector.py` | 8（vid） | 时空投影 |
| `services/graph_projector.py` | 7（vid） | 图投影 |
| `tools/toolsets/reasoning.py` | 8（vid） | 推理工具 |
| `tools/toolsets/_contracts.py` | 5（vid） | 工具契约 |
| `routes/query/__init__.py` | 5（vid） | 查询路由 |
| `core/schemas/canvas.py` | 4（vid） | 画布 schema |
| `core/schemas/graph.py` | 3（vid） | 图 schema |
| `services/pipeline_builder_service.py` | 2（vid） | 管道构建 |
| `core/schemas/object_set.py` | 2（vid） | ObjectSet IR |
| `core/naming.py` | 2（vid） | 命名规范 |
| `config/settings.py` | 2（vid） | `vid_batch_size` 配置 |
| `services/ontology_service.py` | 1（vid） | 本体服务 |

**Python 合计**：约 280 处（object_id + vid 变体）

#### 前端代码（src/web-ui/）

| 文件 | 命中数 |
|---|---|
| `hooks/useGraphExplore.ts` | 29 |
| `components/MapPanel.tsx` | 19 |
| `pages/GraphExplorePage.tsx` | 15 |
| `hooks/__tests__/useGraphExplore.test.ts` | 12 |
| `components/GraphCanvas.tsx` | 8 |
| `components/PathFinder.tsx` | 7 |
| `types/index.ts` | 4 |
| `components/EvidenceDrawer.tsx` | 4 |
| `hooks/__tests__/useSearchAroundConfig.test.ts` | 3 |
| `components/ExecuteActionDialog.tsx` | 2 |
| `api/graph.ts` | 2 |
| `types/canvas.ts` | 1 |
| `hooks/useActionTrigger.ts` | 1 |
| `components/__tests__/PathFinder.test.tsx` | 1 |
| `components/__tests__/EvidenceDrawer.test.tsx` | 1 |
| `components/TimeScrubber.tsx` | 1 |

**前端合计**：约 110 处

#### 测试代码（tests/）

| 文件 | 命中数 |
|---|---|
| `unit/services/test_object_set_executor.py` | 80 |
| `unit/services/test_batch_action.py` | 45 |
| `unit/layers/test_neo4j_graph_store.py` | 29（vid） |
| `unit/tools/test_canvas_state.py` | 28（vid） |
| `unit/services/test_resolve_vids_by_pk.py` | 16（文件名也要改） |
| `unit/tools/test_link_traversal.py` | 15 |
| `unit/services/test_action_service.py` | 15 |
| `unit/core/test_action_schemas.py` | 15 |
| `benchmark/marketing/harness/read_harness.py` | 14 |
| `unit/services/test_projection_wiring.py` | 13 |
| `unit/services/test_action_sync_outbox.py` | 12 |
| `unit/layers/test_postgres_meta_store_datasource.py` | 12 |
| `unit/services/test_project_sync_service.py` | 10 |
| `unit/layers/test_postgres_meta_store.py` | 9 |
| `integration/test_action_routes.py` | 9 |
| `unit/tools/test_query_with_dataframe_logic.py` | 7 |
| `unit/services/test_outbox_executor.py` | 7 |
| `unit/services/test_graph_projector.py` | 7 |
| 其他（< 5 处） | 约 30 |

**测试合计**：约 350 处

### 4.6 settings.py 配置

**文件**：`src/ontology/config/settings.py`

```python
# Line 163-166（当前）
# Ibis filter 单步 vid 集上限
# 候选 vid 集分批大小
vid_batch_size: int = 5_000
```

改后：`rid_batch_size`

---

## 五、PR 拆解（执行计划）

### 总原则

- 按**依赖顺序**执行：工具 → schema → 生成点 → 消费方 → 前端
- 每个 PR **独立可测可回滚**
- 提交前必须跑：`ruff check` + `mypy` + `pytest`（受影响文件）+ 本地冒烟
- schema 变更走 **Alembic migration**（红线：禁止手写 SQL 脚本改业务表）

---

### PR 1：RID 生成器（基础设施，不改调用方）

**目标**：提供 RID 生成/解析工具函数，独立可测，不接线。

**新增文件**：`src/ontology/core/rid.py`

```python
"""Palantir Resource Identifier (RID) 规范实现。

RID 格式：ri.<service>.<instance>.<type>.<locator>
Gaia 对象 RID：ri.ontology.main.object.{uuid}

规范来源：https://github.com/palantir/resource-identifier
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from functools import lru_cache

# Gaia RID 常量
SERVICE = "ontology"
INSTANCE = "main"
OBJECT_TYPE = "object"
VIRTUAL_OBJECT_TYPE = "virtual-object"

# RID 各段 regex（来自 Palantir spec）
_SERVICE_RE = r"[a-z][a-z0-9\-]*"
_INSTANCE_RE = r"([a-z0-9][a-z0-9\-]*)?"
_TYPE_RE = r"[a-z][a-z0-9\-]*"
_LOCATOR_RE = r"[a-zA-Z0-9\-\._]+"

# 完整 RID regex
_RID_PATTERN = re.compile(
    rf"^ri\.(?P<service>{_SERVICE_RE})\."
    rf"(?P<instance>{_INSTANCE_RE})\."
    rf"(?P<type>{_TYPE_RE})\."
    rf"(?P<locator>{_LOCATOR_RE})$"
)


@dataclass(frozen=True)
class ResourceId:
    """解析后的 RID 四段。"""
    service: str
    instance: str  # 可为空串
    type: str
    locator: str

    @property
    def is_object(self) -> bool:
        """是否为 MANAGED 对象 RID（type == 'object'）。"""
        return self.type == OBJECT_TYPE

    @property
    def is_virtual_object(self) -> bool:
        """是否为 VIRTUAL 对象合成 RID（type == 'virtual-object'）。"""
        return self.type == VIRTUAL_OBJECT_TYPE


def generate_object_rid() -> str:
    """生成 MANAGED 对象 RID。

    格式：ri.ontology.main.object.{uuid}
    UUID 带连字符（36 字符），对齐 Palantir 实际格式。
    """
    return f"ri.{SERVICE}.{INSTANCE}.{OBJECT_TYPE}.{uuid.uuid4()}"


def generate_virtual_rid(
    ontology_api_name: str,
    object_type_api_name: str,
    pk_value: str,
) -> str:
    """合成 VIRTUAL 对象 RID。

    格式：ri.ontology.main.virtual-object.{ont}.{ot}.{pk}
    locator 嵌入 ont/ot/pk 以便水合时解析回 PK 查 Trino。

    注意：VIRTUAL rid 是合成的，不保证稳定（外部源 PK 改了就变）。
    这是 VIRTUAL 的固有特性，与 MANAGED 的"rid 稳定不变"不同。
    """
    # pk_value 可能含特殊字符，做基本清理（locator regex 允许字母数字-._）
    safe_pk = re.sub(r"[^a-zA-Z0-9\-\.]", "_", str(pk_value))
    return (
        f"ri.{SERVICE}.{INSTANCE}.{VIRTUAL_OBJECT_TYPE}."
        f"{ontology_api_name}.{object_type_api_name}.{safe_pk}"
    )


def parse_rid(rid: str) -> ResourceId:
    """解析 RID 字符串为四段。

    Raises:
        ValueError: 如果 rid 不符合 RID 规范。
    """
    match = _RID_PATTERN.match(rid)
    if not match:
        raise ValueError(f"Invalid RID format: {rid!r}")
    return ResourceId(
        service=match.group("service"),
        instance=match.group("instance") or "",
        type=match.group("type"),
        locator=match.group("locator"),
    )


def is_managed_rid(rid: str) -> bool:
    """是否为 MANAGED 对象 RID。"""
    try:
        return parse_rid(rid).is_object
    except ValueError:
        return False


def is_virtual_rid(rid: str) -> bool:
    """是否为 VIRTUAL 对象合成 RID。"""
    try:
        return parse_rid(rid).is_virtual_object
    except ValueError:
        return False


def parse_virtual_rid_pk(rid: str) -> tuple[str, str, str]:
    """从 VIRTUAL rid 解析出 (ontology, object_type, pk_value)。

    Raises:
        ValueError: 如果不是 VIRTUAL rid 或格式不符。
    """
    parsed = parse_rid(rid)
    if not parsed.is_virtual_object:
        raise ValueError(f"Not a virtual object RID: {rid!r}")
    parts = parsed.locator.split(".", 2)
    if len(parts) != 3:
        raise ValueError(f"Cannot parse ont/ot/pk from locator: {parsed.locator!r}")
    return parts[0], parts[1], parts[2]
```

**新增测试**：`tests/unit/core/test_rid.py`

```python
"""RID 生成器测试。"""
import re
import pytest
from ontology.core.rid import (
    generate_object_rid,
    generate_virtual_rid,
    parse_rid,
    is_managed_rid,
    is_virtual_rid,
    parse_virtual_rid_pk,
    ResourceId,
)


class TestGenerateObjectRid:
    def test_format(self):
        rid = generate_object_rid()
        assert rid.startswith("ri.ontology.main.object.")
        # locator 是 UUID（36 字符含连字符）
        locator = rid.rsplit(".", 1)[1]
        assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", locator)

    def test_unique(self):
        rids = {generate_object_rid() for _ in range(1000)}
        assert len(rids) == 1000

    def test_length_under_128(self):
        rid = generate_object_rid()
        assert len(rid) < 128  # 约 61 字符


class TestGenerateVirtualRid:
    def test_format(self):
        rid = generate_virtual_rid("supplychain", "Order", "ORD001")
        assert rid == "ri.ontology.main.virtual-object.supplychain.Order.ORD001"

    def test_pk_with_special_chars(self):
        # 特殊字符替换为下划线
        rid = generate_virtual_rid("ont", "OT", "pk with space/slash")
        assert " " not in rid and "/" not in rid

    def test_length_under_128(self):
        rid = generate_virtual_rid("supplychain", "Order", "ORD001")
        assert len(rid) < 128


class TestParseRid:
    def test_parse_managed(self):
        rid = generate_object_rid()
        parsed = parse_rid(rid)
        assert parsed.service == "ontology"
        assert parsed.instance == "main"
        assert parsed.type == "object"
        assert parsed.is_object is True
        assert parsed.is_virtual_object is False

    def test_parse_virtual(self):
        rid = "ri.ontology.main.virtual-object.supplychain.Order.ORD001"
        parsed = parse_rid(rid)
        assert parsed.type == "virtual-object"
        assert parsed.is_object is False
        assert parsed.is_virtual_object is True

    def test_parse_ontology_rid(self):
        # ontology 资源的 RID（与 object 同名不冲突）
        parsed = parse_rid("ri.ontology.main.ontology.abc123")
        assert parsed.type == "ontology"

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_rid("not-a-rid")
        with pytest.raises(ValueError):
            parse_rid("ri.Ontology.Main.Object.xxx")  # 大写不合法
        with pytest.raises(ValueError):
            parse_rid("")

    def test_empty_instance(self):
        # instance 段可为空
        parsed = parse_rid("ri.ontology..object.xxx")
        assert parsed.instance == ""


class TestHelpers:
    def test_is_managed_rid(self):
        assert is_managed_rid(generate_object_rid()) is True
        assert is_managed_rid("ri.ontology.main.virtual-object.ont.ot.pk") is False
        assert is_managed_rid("invalid") is False

    def test_is_virtual_rid(self):
        assert is_virtual_rid("ri.ontology.main.virtual-object.ont.ot.pk") is True
        assert is_virtual_rid(generate_object_rid()) is False

    def test_parse_virtual_rid_pk(self):
        ont, ot, pk = parse_virtual_rid_pk("ri.ontology.main.virtual-object.supplychain.Order.ORD001")
        assert (ont, ot, pk) == ("supplychain", "Order", "ORD001")

    def test_parse_virtual_rid_pk_invalid(self):
        with pytest.raises(ValueError):
            parse_virtual_rid_pk(generate_object_rid())  # MANAGED rid
```

**验证**：
```bash
.venv/bin/python -m pytest tests/unit/core/test_rid.py -v
.venv/bin/ruff check src/ontology/core/rid.py
.venv/bin/mypy --explicit-package-bases src/ontology/core/rid.py
```

---

### PR 2：PG Schema Migration + ORM Model

**目标**：改字段名 + 扩长度，提供 Alembic migration。

**改动文件**：

#### 2.1 `src/ontology/core/models/ontology.py`

```python
# ObjectStateModel（line ~482）
class ObjectStateModel(Base):
    __tablename__ = "object_state"

    # 旧：object_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rid: Mapped[str] = mapped_column(String(128), primary_key=True)  # Palantir RID
    # ... 其余不变

# ObjectLinkModel（line ~525）
class ObjectLinkModel(Base):
    __tablename__ = "object_links"
    # 旧：source_object_id / target_object_id
    source_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # UniqueConstraint 也要改字段名
    __table_args__ = (
        UniqueConstraint(
            "link_type_api_name",
            "source_rid",
            "target_rid",
            name="uq_object_links_...",
        ),
    )
```

#### 2.2 Alembic migration

```bash
.venv/bin/alembic revision --autogenerate -m "adopt palantir rid for object_state and object_links"
```

**人工 review migration**（autogenerate 不完美）：
- 确认生成的是 `ALTER TABLE object_state RENAME COLUMN object_id TO rid`
- 确认 `ALTER TABLE object_state ALTER COLUMN rid TYPE VARCHAR(128)`
- 同理 object_links 的 source_object_id → source_rid, target_object_id → target_rid
- 确认 UniqueConstraint 重建

**因存量可清空**，也可以用更简单的 DROP + CREATE（如果不需要保留数据）：
```python
# migration 修订版（若选择清空重建）
def upgrade():
    op.drop_table("object_links")
    op.drop_table("object_state")
    # 然后让 create_all 重建（或显式 CREATE）

def downgrade():
    pass  # 不可逆（清空了）
```

**建议**：用 RENAME + ALTER（保留表结构，只改列），即使清空数据也用 `DELETE FROM` 而非 DROP，这样 migration 逻辑更清晰。

#### 2.3 `src/ontology/layers/metadata/postgres_meta_store.py`（73 处）

所有方法签名 + SQL 的字段名：
- `get_object_state(rid: str)` （旧 `object_id`）
- `get_object_states_by_rids(rids: list[str])` （旧方法名）
- `upsert_object_state(..., rid: str, ...)` 
- `get_rids_by_type(...)` （旧 `get_object_ids_by_type`）
- `get_rids_by_pk(...)` （旧 `get_object_ids_by_pk`）
- `get_rids_by_interface(...)` （旧 `get_object_ids_by_interface`）
- SQL 里 `object_id` → `rid`、`source_object_id` → `source_rid`、`target_object_id` → `target_rid`

**验证**：
```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic check  # 确认无漂移
.venv/bin/python -m pytest tests/unit/layers/test_postgres_meta_store.py -v
# 本地冒烟
.venv/bin/python -c "
from ontology.core.rid import generate_object_rid
rid = generate_object_rid()
print(f'Generated: {rid}')
# TODO: 写入 PG 验证
"
```

---

### PR 3：Action 层 + outbox（生成点切换）

**目标**：把 object_id 生成点改为 RID 生成器，action/outbox/projection 全链路改名。

**改动文件**：

#### 3.1 `src/ontology/services/action_service.py`（88 处）

**核心改动**（生成点）：
```python
# Line 629（旧）
obj_id = mutation.get("object_id", str(uuid.uuid4()))
# 改后
from ontology.core.rid import generate_object_rid
rid = mutation.get("rid") or generate_object_rid()

# Line 1251（旧）
object_id = uuid.uuid4().hex
# 改后
rid = generate_object_rid()
```

其余 86 处：`object_id` → `rid`、`target_object_id` → `target_rid`、`source_object_id` → `source_rid`（参数名、变量名、dict key）。

#### 3.2 `src/ontology/services/outbox_executor.py`（4 处 + vid 5 处）

投影触发调用改名：`source_vid` → `source_rid` 等。

#### 3.3 `src/ontology/services/graph_projector.py`（vid 7 处）

```python
# 旧
async def project_object(self, ontology, object_type, object_state):
    vid = object_state["id"]
    props = {"vid": vid, "api_name": ot.api_name}
    await self.graph_store.upsert_node(vid, ot.api_name, props)

# 改后
async def project_object(self, ontology, object_type, object_state):
    rid = object_state["rid"]  # 注意：object_state dict 的 key 也要改
    props = {"rid": rid, "api_name": ot.api_name}
    await self.graph_store.upsert_node(rid, ot.api_name, props)
```

#### 3.4 `src/ontology/services/geotime_projector.py`（vid 8 处）

同 graph_projector。

**验证**：
```bash
.venv/bin/python -m pytest tests/unit/services/test_action_service.py tests/unit/services/test_projection_wiring.py tests/unit/services/test_graph_projector.py tests/unit/services/test_geotime_projector.py -v
# 本地冒烟：创建对象 → 查 PG 确认 rid 格式
curl -X POST http://localhost:8000/actions/execute -d '...'
docker exec gaia-postgres psql -U ontology -d ontology -c "SELECT rid FROM object_state LIMIT 5;"
# 应看到 ri.ontology.main.object.{uuid} 格式
```

---

### PR 4：查询层 + tools + Neo4j + Doris（最大）

**目标**：查询编排层、图存储、工具层全部改名 + 水合架构修正。

#### 4.1 `src/ontology/layers/graph/neo4j_graph_store.py`（vid 40 处）

所有 Cypher 和方法签名：
- `vid` 属性 → `rid`
- `{label}_vid_unique` 约束名 → `{label}_rid_unique`
- `upsert_node(vid)` → `upsert_node(rid)`
- `source_vid` / `target_vid` → `source_rid` / `target_rid`
- `search_around(source_vids)` → `search_around(source_rids)`
- `delete_node(vid)` → `delete_node(rid)`

**Neo4j 存量**：清空重建
```bash
# 清空（无生产数据）
docker exec gaia-neo4j cypher-shell -u neo4j -p password "MATCH (n) DETACH DELETE n"
# 重建（重新投影）
# 通过 rebuild_graph API 或脚本
```

#### 4.2 `src/ontology/layers/geotime/geotime_store.py`（vid 17 处）

同 neo4j_graph_store，`vid` → `rid`。

#### 4.3 `src/ontology/services/object_set_executor.py`（object_id 43 处 + vid）

**核心改动**：
- `_resolve_vids_by_pk` → `_resolve_rids_by_pk`
- `_eval_static` / `_hydrate` / `_eval_search_around` 全链路改名
- `vids` 变量 → `rids`

**水合架构修正（重要，不只改名）**：

当前 `_hydrate` 走 PG `get_object_states_by_rids`（MVP 实现），应改为走 Doris 主源（ADR-001）：

```python
# 当前（MVP，未对齐架构）
async def _hydrate(self, rids: list[str]) -> list[dict]:
    states = await self.metadata.get_object_states_by_rids(rids)  # 走 PG
    return states

# 改后（对齐 ADR-001）
async def _hydrate(self, rids: list[str]) -> list[dict]:
    # 分流：MANAGED → Doris 主源；VIRTUAL → Trino 联邦
    managed_rids = [r for r in rids if is_managed_rid(r)]
    virtual_rids = [r for r in rids if is_virtual_rid(r)]

    result = []
    # MANAGED：Doris 主源点查（降级 Trino Iceberg）
    if managed_rids:
        objs = await self.object_query_service.load_by_ids(managed_rids)
        result.extend(objs)
    # VIRTUAL：解析 PK → Trino 跨 catalog 联邦查外部源
    if virtual_rids:
        for rid in virtual_rids:
            ont, ot, pk = parse_virtual_rid_pk(rid)
            objs = await self._hydrate_virtual_from_trino(ont, ot, [pk])
            result.extend(objs)
    return result
```

**注意**：水合架构修正是**独立工作**，可以拆成 PR 4a（纯改名）+ PR 4b（水合走 Doris）。如果工作量太大，先做 4a 改名，4b 单独跟进。

#### 4.4 `src/ontology/tools/toolsets/link_traversal.py`（vid 16 处）

```python
# 旧
source_vids = await svc._resolve_vids_by_pk(ontology, source_ot.api_name, source_keys)
# 改后
source_rids = await svc._resolve_rids_by_pk(ontology, source_ot.api_name, source_keys)
```

#### 4.5 `src/ontology/tools/toolsets/reasoning.py`（vid 8 处）、`_contracts.py`（vid 5 处）

参数名 + 契约定义改名。

#### 4.6 `src/ontology/routes/query/__init__.py`（vid 5 处）

路由参数改名。

#### 4.7 `src/ontology/layers/index/doris_index_store.py`（5 处）

- `load_by_ids(object_ids)` → `load_by_ids(rids)`
- 确认 Doris 表的主键列长度足够（RID 比 UUID 长）

#### 4.8 `src/ontology/config/settings.py`（2 处）

```python
# 旧
vid_batch_size: int = 5_000
# 改后
rid_batch_size: int = 5_000
```

注释里的 `vid` 也改 `rid`。

#### 4.9 测试文件改名

```bash
git mv tests/unit/services/test_resolve_vids_by_pk.py tests/unit/services/test_resolve_rids_by_pk.py
```

测试类名/函数名里的 `vid` → `rid`。

**验证**：
```bash
.venv/bin/python -m pytest tests/unit/ -v  # 全量单元测试
.venv/bin/ruff check src/
.venv/bin/mypy --explicit-package-bases src/
# 端到端
make k8s-all
# 图探索冒烟
curl http://localhost:8000/objects/{ont}/query-dataframe -d '...'
```

---

### PR 5：前端

**目标**：前端 TypeScript 代码全部 `vid` / `object_id` → `rid`。

**改动文件**（16 个）：
- `hooks/useGraphExplore.ts`（29 处）
- `components/MapPanel.tsx`（19 处）
- `pages/GraphExplorePage.tsx`（15 处）
- `hooks/__tests__/useGraphExplore.test.ts`（12 处）
- `components/GraphCanvas.tsx`（8 处）
- `components/PathFinder.tsx`（7 处）
- `types/index.ts`（4 处）
- `components/EvidenceDrawer.tsx`（4 处）
- `hooks/__tests__/useSearchAroundConfig.test.ts`（3 处）
- `components/ExecuteActionDialog.tsx`（2 处）
- `api/graph.ts`（2 处）
- `types/canvas.ts`（1 处）
- `hooks/useActionTrigger.ts`（1 处）
- `components/__tests__/PathFinder.test.tsx`（1 处）
- `components/__tests__/EvidenceDrawer.test.tsx`（1 处）
- `components/TimeScrubber.tsx`（1 处）

**改动模式**：
```typescript
// 旧
interface GraphObject { vid: string; api_name: string; props: Record<string, any>; }
const nodes: Map<vid, GraphNode>;
async function searchAround(vid: string, ...)

// 改后
interface GraphObject { rid: string; api_name: string; props: Record<string, any>; }
const nodes: Map<rid, GraphNode>;
async function searchAround(rid: string, ...)
```

**验证**：
```bash
cd src/web-ui
pnpm run typecheck
pnpm run build  # 必须跑 build，typecheck 发现不了 yaml 重复 key 等问题
# 手动操作图探索页面验证
```

---

## 六、执行检查清单

### 提交前必做（每个 PR）

#### 代码质量
- [ ] `ruff check src/` 零错误
- [ ] `ruff format --check src/` 零错误
- [ ] `mypy --explicit-package-bases src/` 零错误
- [ ] 无 `vid` / `object_id` 残留（除决策注历史说明）

#### 测试
- [ ] 受影响的单元测试全绿
- [ ] 新增 RID 生成器测试（PR 1）
- [ ] 异常路径覆盖（无效 RID 格式、VIRTUAL 解析失败等）

#### Schema（PR 2）
- [ ] Alembic migration 已生成并人工 review
- [ ] `alembic upgrade head` 成功
- [ ] `alembic check` 无漂移
- [ ] 本地 PG 冒烟：写入对象 → 查 `SELECT rid FROM object_state` 确认格式

#### 本地冒烟（PR 3+）
- [ ] 创建对象 → PG `object_state.rid` 为 `ri.ontology.main.object.{uuid}` 格式
- [ ] Neo4j 节点 `rid` 属性正确（清空重建后）
- [ ] 图探索端到端：query-dataframe → searchAround → 水合返回全量属性

#### 前端（PR 5）
- [ ] `pnpm run typecheck` 通过
- [ ] `pnpm run build` 通过
- [ ] 手动操作图探索页面

### 资料中心同步（CLAUDE.md 要求）

- [ ] 检查 `apps/docs/content/` 是否需要同步更新（本次为架构层变更，主要影响 `architecture/` 和 `api-reference/`）
- [ ] 截图是否需要重跑（图探索页面如果有 rid 显示变化）

---

## 七、风险与注意事项

### 7.1 高风险点

| 风险 | 影响 | 缓解 |
|---|---|---|
| **PG schema migration 失败** | object_state/object_links 表损坏 | 存量可清空，最坏 DROP + CREATE；migration 先在本地验证 |
| **Neo4j 存量数据 rid 格式不一致** | 图遍历找不到节点 | 清空重建（`MATCH (n) DETACH DELETE n` + `rebuild_graph`） |
| **Doris idx 表主键列长度不够** | RID 写入截断 | 确认列类型 VARCHAR 长度 ≥ 128 |
| **水合架构修正（PR 4b）范围大** | 查询路径行为变化 | 拆成独立 PR，先改名（4a）再修水合（4b） |

### 7.2 不要改的部分

| 项 | 原因 |
|---|---|
| Iceberg 表 PK | 用业务主键列（backing_column），与 Palantir backing dataset 一致 |
| 元数据表主键（ontologies/object_types 等） | 用裸 UUID，不是 object 身份 |
| `ontology.rid` | 已存在字段，语义不同，本次不动 |
| Ontop 调研文档里的 IRI 术语 | 那是 RDF 范式，与 RID 无关 |

### 7.3 常见陷阱

1. **`object_state` dict 的 key**：代码里很多地方用 `object_state["id"]` 或 `object_state["object_id"]` 取值。改字段名后，dict key 也要同步改成 `object_state["rid"]`。**注意 PG 返回的 row dict 的 key 跟字段名一致**。

2. **Cypher 字符串拼接**：Neo4j Cypher 里 `{vid: $vid}` 是属性名 + 参数名。属性名改 `rid`，参数名也改 `rid`，注意 Cypher 里 `$rid` 不要和其他参数冲突。

3. **`is_managed_rid` 性能**：图遍历可能对大量 rid 调用 `is_managed_rid`（每个都 parse_rid）。如果性能敏感，可以改成 `rid.startswith("ri.ontology.main.object.")` 前缀判断（更快但不严格）。MVP 先用 parse_rid 保证正确性。

4. **VIRTUAL rid 的 pk_value 特殊字符**：`generate_virtual_rid` 对 pk_value 做了字符清理（非 `[a-zA-Z0-9\-\.]` 替换为 `_`）。如果业务 PK 含这些字符，解析回 PK 时会失真。**注意**：这可能导致 `parse_virtual_rid_pk` 返回的 pk 与原始 pk 不一致。如果业务 PK 含特殊字符，需要额外编码（如 base64）。MVP 假设 PK 是字母数字。

5. **测试里的硬编码 UUID**：测试代码里有很多 `uuid.uuid4().hex` 或硬编码 UUID 字符串作为 object_id。改后要么用 `generate_object_rid()`，要么硬编码完整 RID 字符串。

---

## 八、参考资料

### Palantir 官方
- [Resource Identifier 开源 spec](https://github.com/palantir/resource-identifier) — RID 格式定义
- [Functions on objects · Object identifiers](https://palantir.com/docs/foundry/functions/object-identifiers/) — 对象身份三层模型（RID / typeId+primaryKey / 业务主键）
- [Get Object API](https://palantir.com/docs/foundry/api/ontology-resources/objects/get-object/) — API 用 primaryKey 定位，不用 RID

### Gaia 内部调研
- [三场景模拟分析](../research/three-scenarios-ontology-graph-federation.md) — 身份模型决策权威源 + VIRTUAL 合成 rid 方案
- [虚拟表填充 Neo4j 可行性调研](../research/virtual-table-neo4j-projection-feasibility.md) — VIRTUAL 投影路径选型
- [Palantir 动态本体映射 Neo4j 方案对照分析](../research/palantir-neo4j-mapping-proposal-comparison.md) — 方案对照 + 决策三角
- [Ontop 源码分析](../research/ontop-source-analysis.md) — VKG 引擎实现参考

### Gaia 架构文档
- [graph-reasoning-design.md](graph-reasoning-design.md) §4.1 — 身份模型说明 + C1 决策
- [implementation-status.md](implementation-status.md) §十 — 命名规范表（已更新）
- [icd-01-postgres-meta-store.md](icd-01-postgres-meta-store.md) — PostgresMetaStore 接口契约
- [ADR-001](adr-001-doris-as-online-read-source.md) — Doris 在线读主源（水合架构依据）

### 项目规范
- `CLAUDE.md`（engines/gaia/）— 开发规范、测试规范、schema 变更走 Alembic
- `CLAUDE.md`（根）— 提交前测试、资料中心同步

---

## 九、PR 执行顺序速查

```
PR 1: RID 生成器（新增 core/rid.py，不接线）
  ↓ 依赖
PR 2: PG schema migration + ORM model（改字段名 + 扩长度）
  ↓ 依赖
PR 3: Action 层 + outbox（生成点切换 uuid→RID）
  ↓ 依赖
PR 4: 查询层 + tools + Neo4j + Doris（消费方改名 + 水合架构修正）
  ↓ 依赖
PR 5: 前端（TypeScript 改名）
```

**每个 PR 独立可测可回滚。PR 4 可拆 4a（改名）+ 4b（水合走 Doris）。**

**预计工作量**：
- PR 1：0.5 天（工具 + 测试）
- PR 2：1 天（migration + ORM + meta_store）
- PR 3：1.5 天（action_service 88 处 + 投影）
- PR 4：3 天（最大，object_set_executor + Neo4j + tools + 水合修正）
- PR 5：1.5 天（前端 16 文件）
- **合计**：约 7.5 天

---

## 十、迁移后清理 Runbook（代码合并后执行）

> PR 1-5 代码已全部完成。以下为部署时需执行的存量数据清理步骤，
> 因涉及外部组件（Neo4j/PostGIS）运行状态，不在代码 PR 范围内。

### 10.1 Neo4j 存量图重建（必做）

**原因**：PR 4a 把 Neo4j 节点/边的属性名 `vid`→`rid`、约束名 `_vid_unique`→`_rid_unique`。
存量节点的 `vid` 属性在新代码里读不到（新代码查 `n.rid`），且旧约束名
`_vid_unique` 会与新代码创建的 `_rid_unique` 共存（重复约束）。

**前置**：Neo4j 容器运行中（`docker compose --profile graph up -d neo4j`）。

```bash
# 1. 清空存量图（派生副本，可全量重建）
docker exec gaia-neo4j cypher-shell -u neo4j -p <password> \
  "MATCH (n) DETACH DELETE n"

# 2. 删除旧约束（属性名已变，旧约束失效）
docker exec gaia-neo4j cypher-shell -u neo4j -p <password> \
  "SHOW CONSTRAINTS"  # 记下 _vid_unique 的名字
docker exec gaia-neo4j cypher-shell -u neo4j -p <password> \
  "DROP CONSTRAINT <旧_vid_unique约束名>"

# 3. 全量重建（逐 ObjectType）
#    通过 API 或脚本调 GraphProjector.rebuild_for_object_type(ont, ot)
#    或重启服务后触发 OutboxExecutor 重放（若 outbox 有存量 INDEX effect）
```

**验证**：`MATCH (n) RETURN n.rid LIMIT 5` 返回 RID 格式值（非空）。

### 10.2 PostGIS geotime 表重建（必做）

**原因**：PR 4a 把 `create_geo_table` 的列定义从 `vid VARCHAR(32)` 改为
`rid VARCHAR(128)`。存量表是旧 DDL 创建的，`CREATE TABLE IF NOT EXISTS`
不会改已有表结构——存量表仍是 `vid VARCHAR(32)`，新代码 upsert 写 `rid`
列会报 `column "rid" does not exist`。

**影响范围**：所有 `geo_*` / `timeseries_*` 业务表（PostGIS 系统视图
`geography_columns`/`geometry_columns` 不动）。

```bash
# 1. 查看存量 geotime 表
psql -U ontology -d ontology -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_name LIKE 'geo_%' OR table_name LIKE 'timeseries_%'
   ORDER BY table_name"

# 2. 逐表 DROP（派生投影，可重建）
#    注意：geotime_projector 无 rebuild 方法，需手动 drop 表后逐对象重投影
psql -U ontology -d ontology -c "DROP TABLE IF EXISTS geo_chain_smoke__customer CASCADE;"
# ... 对每个 geo_*/timeseries_* 表执行

# 3. 重建：重启服务后，访问该对象类型的图探索/空间过滤会触发
#    GeoTimeProjector.project_object 重新建表 + 投影
#    或写脚本批量调 project_object（需从 object_state 读空间属性）
```

**验证**：`SELECT column_name, character_maximum_length FROM
information_schema.columns WHERE table_name='geo_xxx' AND
column_name='rid'` 返回 `rid, 128`。

### 10.3 Doris idx 表（待评估，独立架构工作）

**现状**：PR 4a 仅改了 `doris_index_store.load_by_ids` 参数名
`object_ids`→`rids`（语义不变，仍按主键列点查）。Doris idx 表主键列
存**业务 PK**（IndexFieldExtractor 推导），不含 rid 列。

**handoff §3.4 期望**：MANAGED rid → Doris 主源点查。但当前 Doris 表
无 rid 列，无法直接按 rid 点查。PR 4b 水合 MANAGED 仍走 PG object_state
（MVP），VIRTUAL 走 Trino 联邦。

**未来工作**（独立架构 PR，不在本次迁移范围）：
1. IndexFieldExtractor 增加 rid 列（STORED_ONLY VARCHAR(128)）
2. IndexSyncService 同步时写 rid（从 object_state.rid 取）
3. DataFrameQueryService._hydrate_managed 切换为
   `object_query_service.load_by_ids(rids)` 走 Doris 主源
4. 存量 Doris 表需重建（加 rid 列 + 回填）

### 10.4 资料中心同步检查

本次为架构层变更，主要影响：
- `apps/docs/content/architecture/` — 若有提及 object_id/vid 的架构说明需同步
- `apps/docs/content/api-reference/` — API 响应字段 object_id→rid（BatchAction）
- 截图：图探索页面若显示 rid（当前 UI 不直接展示 rid 字符串，无需重跑截图）

**检查方式**：
```bash
grep -rn "object_id\|\bvid\b" apps/docs/content/ --include="*.md" \
  | grep -vE "evidence|provided|video|avid"
```
