# VIRTUAL 对象图投影——实现设计（工程落地权威源）

> **用途**：本文是 ADR-021 的工程落地指导。ADR-021 记录"为什么这样决策"，本文记录"具体怎么实现"——组件契约、数据流时序、难点决策记录、PR 拆解、测试矩阵。实现者以本文 + ADR-021 为准。
> **不是** ADR：决策变更走 ADR-021 修订；实现细节（方法签名、Cypher、分页参数）的调整在本文进行，记 changelog。
> **关联**：
> - 决策权威源：[`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md)
> - 架构基线：[`graph-reasoning-design.md`](./graph-reasoning-design.md) §6（已修订，见 §6.5）
> - 交接输入：[`handoff-virtual-graph-projection.md`](./handoff-virtual-graph-projection.md)（前身，待确认点已闭合，见附录 A）
> **日期**：2026-07-16

---

## 〇、TL;DR（实现者先读）

**目标**：让图关联推理（`search_around`/`find_paths`/`exists_link`）跨 VIRTUAL 对象不断链。把 VIRTUAL 对象的**身份骨架**投影进 Neo4j，全量属性走 Trino 联邦水合（零拷贝，永远最新）。

**已落地复用**（不要重造）：
- ✅ `core/rid.py`：`generate_virtual_rid` / `parse_virtual_rid_pk` / `is_virtual_rid` / `is_managed_rid`
- ✅ `DataFrameQueryService._hydrate`：按 rid type 段分流（MANAGED→PG/Doris，VIRTUAL→Trino 联邦）
- ✅ `_hydrate_virtual`：调 `ObjectQueryService.hydrate_by_pks`（Trino 联邦批量水合，PR 5a 优化）；PR 4 已改 partial 降级（Trino 失败标 `_partial:true` 不静默跳过，ADR-021 §2.8）
- ✅ `LinkTypeModel.foreign_key_property_api_name` + `cardinality` + `direction`
- ✅ `PropertyDefModel.backing_column` / `backing_catalog/schema/table` / `is_title_property` / `indexed`
- ✅ `ObjectTypeModel.title_property` + `primary_key`
- ✅ `ObjectQueryService.hydrate_by_pk` + `_virtual_table_ref`
- ✅ `naming.graph_label` / `graph_relationship_type`

**要新增/修改的**（本文档指导）：
- ✅ `ObjectIndexFunnel.project_for_virtual_object_type`：VIRTUAL 身份骨架投影入口（PR 1 节点部分完成，边投影归 PR 2）
- ✅ `GraphProjector.project_object` 扩展：识别 `_virtual`/`_source_ref`/`_sync_tag` 元标记 + title（PR 1 完成）
- ✅ `Neo4jGraphStore.upsert_nodes_batch` / `upsert_edges_batch`：批量写入（CALL {} IN TRANSACTIONS + UNWIND，PR 1 完成）
- ✅ `Neo4jGraphStore.cleanup_stale_virtual`：watermark 孤儿清理（PR 1 完成）
- ✅ `PostgresMetaStore.get_object_states_by_pks`：MANAGED 端 PK→rid 批量反查（PR 2 完成）
- ✅ `ObjectIndexFunnel` 注入 `TrinoQueryEngine` + `ObjectQueryService`（PR 1 完成；原 ProjectSyncService 已重命名）
- ✅ 触发链路：`register_virtual_table` 异步触发 + admin rebuild API（PR 3 完成）
- ✅ `ConflictDetector._audit_iteration` 排除 VIRTUAL（PR 4 完成，加 storage_type==MANAGED 过滤）
- ✅ `_hydrate_virtual` 返回 `_partial` 标记（PR 4 完成，Trino 失败不静默跳过）
- 🟡 `DataFrameQueryService` 注入 `AuthorizationService`（P0，独立 PR 0 前置）

**核心约束**（ADR-021 D1/D2）：Neo4j 是**派生索引**，VIRTUAL 投影是模式 C 的扩展（身份骨架复制），**不是 ETL 落地**。任何"为图方便把全量属性也塞进 Neo4j"的诱惑都要拒绝——那是被否决的纯 Palantir 方案的反面。

---

## 一、架构定位

### 1.1 在 Gaia 分层里的位置

```
Routes（HTTP 薄层）
  POST /admin/project/rebuild-for-virtual/{ont}/{ot}   （admin，手动 rebuild）
    ↓ 依赖注入
Services（业务编排）
  ProjectSyncService.project_for_virtual_object_type   （新增，旁路 Gate 1）
    ↓ 构造合成 object_state
  GraphProjector.project_object / project_link         （扩展，识别 _virtual 元标记）
    ↓ Cypher 收口
Layers
  TrinoQueryEngine.query  （数据源：拉 VIRTUAL 表的 pk/title/indexed/fk 列）
  Neo4jGraphStore.upsert_nodes_batch / cleanup_stale_virtual  （新增批量 + 清理）
  PostgresMetaStore.get_object_states_by_pks           （新增，MANAGED PK→rid 反查）
```

### 1.2 触发模式 D（graph-reasoning-design.md §6.1 新增）

| 模式 | 触发 | 数据源 | 写入目标 | 链路 |
|------|------|--------|---------|------|
| **D. VIRTUAL 联邦投影** | `register_virtual_table` 成功 / admin rebuild | Trino 联邦查外部源 | Neo4j 节点（骨架）+ 边（FK 推导） | TrinoQueryEngine.query → 合成 object_state → GraphProjector → Neo4jGraphStore |

与 A/B/C 区别：不经 Iceberg / 不经 outbox / 不经 SeaTunnel。数据源是 Trino，不是 IcebergStore。

### 1.3 一致性语义（ADR-021 D7）

VIRTUAL 节点 = best-effort + **不可对账**。不参与 ConflictDetector。全量属性实时（Trino 联邦水合），拓扑/剪枝投影态延迟（MVP 手动 rebuild，二期定时刷新）。

---

## 二、组件契约设计

### 2.1 `ProjectSyncService.project_for_virtual_object_type`（核心新增）

**位置**：`src/ontology/services/project_sync_service.py`

**依赖注入变更**：`ProjectSyncService.__init__` 新增 `engine: TrinoQueryEngine | None` + `object_query: ObjectQueryService | None`（保持可选，Neo4j/Trino 未启动时不报错）。container.py 同步注入。

**方法签名**：
```python
async def project_for_virtual_object_type(
    self,
    ontology_api_name: str,
    object_type_api_name: str,
    *,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """对单个 VIRTUAL ObjectType 执行图投影（身份骨架 + FK 边）。

    旁路 Gate 1（Gate 1 仍对 project_for_object_type 生效）。数据源是 Trino
    联邦查外部源，不是 IcebergStore.scan_latest。

    流程：
      1. 查 ObjectType 元数据（PK api_name + title api_name + indexed 列表 + backing_column）
      2. 从 ObjectQueryService._virtual_table_ref 拿 Trino table ref
      3. 游标分页从 Trino 拉数据：SELECT {pk_col}, {title_col}, {indexed_cols} FROM {table_ref}
         WHERE {pk_col} > $last_pk ORDER BY {pk_col} LIMIT $batch
      4. 逐批构造合成 object_state dict，调 GraphProjector.project_object
         （带 _virtual/_source_ref/_sync_tag 元标记）
      5. FK→边投影（见 §2.3）
      6. 孤儿清理（watermark + cleanup，见 §2.4）

    Returns:
        {"nodes": n, "edges": m, "cleaned": k, "partial": bool, "error": str|None}
    """
```

**合成 object_state 形状**（传给 `project_object`）：
```python
{
    "id": generate_virtual_rid(ont, ot, str(row[pk_col])),  # 合成 rid
    "object_type_api_name": ot,
    "properties": {
        pk_api_name: row[pk_col],
        title_api_name: row[title_col],
        **{p.api_name: row[p.col] for p in indexed_props},  # indexed 属性
    },
    "_virtual": True,
    "_source_ref": table_ref,            # 如 "mysql_orders.orders"
    "_sync_tag": sync_tag,               # 本次投影的水位标记
}
```

### 2.2 `GraphProjector.project_object` 扩展

**位置**：`src/ontology/services/graph_projector.py:48`

当前 `project_object` 只读 `ot.properties` 的 indexed 字段。扩展：检测 `object_state.get("_virtual")` 为 True 时，额外写入元标记。

**改动**（在 `props` 构造后）：
```python
# 现有：props = {"rid": rid, "api_name": ot.api_name} + indexed + visibility
if object_state.get("_virtual"):
    props["_virtual"] = True
    props["_source_ref"] = object_state.get("_source_ref", "")
    props["_sync_tag"] = object_state.get("_sync_tag", 0)
    # PK 业务值存一份（避免水合时重复解析 rid locator）
    pk_api = ot.primary_key
    props[pk_api] = flat.get(pk_api)
    # title 存一份（画布渲染节点标题，不查全量也能显示）
    title_api = ot.title_property
    if title_api:
        props[title_api] = flat.get(title_api)
```

**title 字段**：`ObjectTypeModel.title_property`（String 255，非空）已存在——handoff §4.2 "待确认点 1"已闭合。`PropertyDefModel.is_title_property`（Boolean）也已有，可交叉校验。

### 2.3 FK→边投影（最大设计挑战）

**位置**：`project_for_virtual_object_type` 内，节点投影后。

**FK 列名解析**（难点 1，已决策）：

`LinkType.foreign_key_property_api_name` 是**属性 api_name**（不是物理列名）。文档明确"存储在源或目标端属性上"（[`ontology-tool-layer.md`](ontology-tool-layer.md) §`define_link_type`）。解析物理列名按 **source 端优先 → target 端兜底**：

```python
def _resolve_fk_backing_column(
    link: LinkTypeDef, src_ot: ObjectType, tgt_ot: ObjectType
) -> tuple[str, ObjectType] | None:
    """解析 FK 属性的物理列名 + 归属的 ObjectType。

    Returns: (backing_column, owning_ot) 或 None（FK 缺失或属性未绑 backing_column）。
    """
    fk_api = link.foreign_key_property_api_name
    if not fk_api:
        return None
    # source 端优先
    for p in src_ot.properties or []:
        if p.api_name == fk_api and p.backing_column:
            return (p.backing_column, src_ot)
    # target 端兜底
    for p in tgt_ot.properties or []:
        if p.api_name == fk_api and p.backing_column:
            return (p.backing_column, tgt_ot)
    return None
```

**边投影逻辑**（三种 LinkType 形态）：

```python
# 查该 VIRTUAL ObjectType 作为 source 或 target 的所有 LinkType
links = await self._metadata.get_link_types_for_object_type(ont, ot)
for link in links:
    src_ot = await self._metadata.get_object_type_by_id(link.source_object_type_id)
    tgt_ot = await self._metadata.get_object_type_by_id(link.target_object_type_id)
    src_virtual = src_ot.storage_type == "VIRTUAL"
    tgt_virtual = tgt_ot.storage_type == "VIRTUAL"

    # 情况 1：两端都 MANAGED → 跳过（MANAGED 边由 Action Step 11 投影，不在此）
    if not src_virtual and not tgt_virtual:
        continue

    fk = self._resolve_fk_backing_column(link, src_ot, tgt_ot)
    if fk is None:
        # FK 缺失 → 边不投影，节点仍投影（降级）
        _log.warning("LinkType %s 缺 FK 元数据，边不投影", link.api_name)
        continue
    fk_col, fk_owning_ot = fk

    if src_virtual and tgt_virtual:
        # 情况 2：两端都 VIRTUAL（MVP 含，见难点 5 决策）
        await self._project_virtual_virtual_edges(ont, link, src_ot, tgt_ot, fk_col, ...)
    else:
        # 情况 3：一端 VIRTUAL 一端 MANAGED
        await self._project_virtual_managed_edges(ont, link, src_ot, tgt_ot, fk_col, fk_owning_ot, ...)
```

**情况 3：一端 VIRTUAL 一端 MANAGED**（FK 在 VIRTUAL 端，指向 MANAGED 端 PK）：
```python
# 1. 从 Trino 拉 VIRTUAL 表的 (fk, pk) 对
#    SELECT {fk_col}, {virtual_pk_col} FROM {virtual_table_ref}
# 2. 收集所有 MANAGED PK 值，批量反查 rid
managed_pks = [row[fk_col] for row in trino_rows]
managed_states = await self._metadata.get_object_states_by_pks(
    managed_ont, managed_ot, managed_pk_backing_col, managed_pks
)
pk_to_rid = {s["properties"][managed_pk_col]: s["rid"] for s in managed_states}
# 3. 构造边 (virtual_rid)-[:REL]->(managed_rid)，批量 project_link
for row in trino_rows:
    virtual_rid = generate_virtual_rid(virtual_ont, virtual_ot, row[virtual_pk_col])
    managed_rid = pk_to_rid.get(row[fk_col])
    if managed_rid:  # MANAGED 端不存在则跳过（悬空 FK）
        edges.append((virtual_rid, managed_rid))
await self._graph_projector.project_links_batch(ont, link.api_name, ..., edges)
```

**情况 2：两端都 VIRTUAL**（难点 5 决策：不走 Trino 跨 catalog JOIN，分两步拉取 + 内存 join）：
```python
# 1. 从 source VIRTUAL 表拉 (source_pk, fk) 对
# 2. 从 target VIRTUAL 表拉 (target_pk,) 集
# 3. 内存 join：fk == target_pk → (source_rid, target_rid) 边集
# 4. 批量 project_link
# 注：比情况 3 更简单（两端 rid 都合成，不需 PG 反查）
```

### 2.4 孤儿清理（watermark + cleanup，难点 2/4 决策）

**位置**：`Neo4jGraphStore.cleanup_stale_virtual`（新增）

```python
async def cleanup_stale_virtual(
    self, label: str, current_sync_tag: int
) -> int:
    """删除本次投影未触及的 VIRTUAL 节点（源里已删除的孤儿）。

    cartography 范式：MERGE first, then clean up。节点投影完成后调用。
    仅清理带 _virtual:true 且 _sync_tag <> current 的节点，绝不误删 MANAGED。

    Returns: 删除的节点数。
    """
    cypher = (
        f"MATCH (n:{label} {{_virtual: true}}) "
        f"WHERE n._sync_tag <> $current_tag "
        f"DETACH DELETE n"
    )
    # 返回删除计数
```

**调用时机**：`project_for_virtual_object_type` 末尾，节点 + 边投影完成后。**先建后删**顺序确保无窗口期断链。

**为什么 `_sync_tag` 而非 `lastupdated` 时间戳**：int 比较比字符串快；单调递增保证语义清晰（每次 rebuild tag + 1 或用 epoch int）。

### 2.5 Neo4j 批量写入（难点 6 决策）

**位置**：`Neo4jGraphStore.upsert_nodes_batch` / `upsert_edges_batch`（新增）

当前只有逐条 `upsert_node` / `upsert_edge`（`MERGE (n:Label {rid: $rid})`）。大表场景逐条慢。新增批量方法用 **`UNWIND` + `CALL {} IN TRANSACTIONS`**（Neo4j 5 原生，替代 deprecated 的 `apoc.periodic.iterate`）：

```python
async def upsert_nodes_batch(
    self, label: str, nodes: list[dict[str, Any]]
) -> int:
    """批量 upsert 节点（UNWIND + CALL {} IN TRANSACTIONS）。

    比 upsert_node 逐条 MERGE 快一个数量级。每 1000 行一个内部事务，
    避免大事务 OOM。依赖 rid 唯一约束（已有）。
    """
    if not nodes:
        return 0
    # UNWIND 展开参数，CALL {} IN TRANSACTIONS OF 1000 ROWS 分批提交
    prop_keys = [k for k in nodes[0].keys() if k != "rid"]
    set_clause = ", ".join(f"n.{k} = row.{k}" for k in prop_keys)
    cypher = (
        f"UNWIND $rows AS row "
        f"CALL {{ WITH row "
        f"  MERGE (n:{label} {{rid: row.rid}}) "
        f"  SET n.rid = row.rid, {set_clause} "
        f"}} IN TRANSACTIONS OF 1000 ROWS"
    )
    await self._run(cypher, rows=nodes)
    return len(nodes)
```

**前置约束**：`rid` 上需有唯一约束（`upsert_node` 的 MERGE on rid 依赖它）。需确认 `Neo4jGraphStore` 初始化时是否已建——若否，在首次 batch 前补 `CREATE CONSTRAINT IF NOT EXISTS FOR (n:Label) REQUIRE n.rid IS UNIQUE`。

**边批量**同理（`upsert_edges_batch`）。

### 2.6 `PostgresMetaStore.get_object_states_by_pks`（新增）

**位置**：`src/ontology/layers/metadata/postgres_meta_store.py`

现状只有 `get_object_states_by_rids` / `get_object_states_by_type`，**没有**按业务 PK 批量查的方法。新增：

```python
async def get_object_states_by_pks(
    self,
    ontology_api_name: str,
    object_type_api_name: str,
    pk_backing_column: str,  # 物理列名（properties JSONB 里的 key）
    pk_values: list[str],
) -> list[dict[str, Any]]:
    """按业务 PK 批量查 object_state（MANAGED 端 PK→rid 反查用）。

    PK 存在 object_state.properties JSONB 里（按 backing_column key），
    不是独立列。用 properties->>'<pk_col>' = ANY(:pks) 查询。

    Returns: object_state dict 列表（含 rid + properties）。
    """
    if not pk_values:
        return []
    # ObjectQueryService._validate_identifier 校验 pk_backing_column 防注入
    stmt = (
        select(ObjectStateModel)
        .join(ObjectTypeModel, ...)
        .join(OntologyModel, ...)
        .where(OntologyModel.api_name == ontology_api_name)
        .where(ObjectTypeModel.api_name == object_type_api_name)
        .where(text(f"properties->>'{pk_backing_column}' = ANY(:pks)"))
        .params(pks=pk_values)
    )
    ...
```

**索引建议**：`object_state.properties` JSONB 若无 GIN 索引，大表场景 `->>` 查询慢。建议补 `CREATE INDEX ... USING GIN (properties)`（独立优化，不阻塞 MVP，但实现者需评估现有索引）。

### 2.7 ConflictDetector 排除 VIRTUAL

**位置**：`src/ontology/services/conflict_detector.py:204` `_audit_iteration`

当前遍历 ObjectType 的查询没有排除 VIRTUAL。改动：

```python
stmt = (
    select(ObjectTypeModel, OntologyModel.api_name.label("ontology_api_name"))
    .join(OntologyModel, OntologyModel.id == ObjectTypeModel.ontology_id)
    .where(ObjectTypeModel.primary_key.is_not(None))
    .where(ObjectTypeModel.primary_key != "")
    .where(ObjectTypeModel.storage_type == "MANAGED")  # 新增：排除 VIRTUAL
)
```

理由（ADR-021 D7）：VIRTUAL 节点不可对账（外部源无 data_version），ConflictDetector 审计无基准。

### 2.8 `_hydrate_virtual` 改 partial 降级

**位置**：`src/ontology/services/object_set_executor.py:1490`

当前 `except Exception: continue`（静默跳过）。改为返回 `_partial` 标记：

```python
try:
    data = await self._object_query.hydrate_by_pk(ot_api, pk)
except Exception as exc:
    _log.warning("VIRTUAL rid 水合失败（Trino 联邦查询）：%s.%s pk=%s: %s", ont, ot, pk, exc)
    objects.append({
        "rid": rid, "api_name": ot, "props": {},
        "_partial": True, "_error": "source unavailable",
    })
    continue
```

对齐 ADR-020 best-effort 模式 + C9 包容式防线。前端/Agent 看到 `_partial:true` 显示"部分数据不可用"。

---

## 三、数据流时序

### 3.1 投影阶段（register_virtual_table 触发）

```
register_virtual_table(datasource, db, table)
  └─ create_dataset(kind=VIRTUAL) 成功
  └─ asyncio.create_task(project_for_virtual_object_type)  # 异步 best-effort
        │
        ├─ 1. 查 ObjectType 元数据（PK api + title api + indexed + backing_column）
        ├─ 2. _virtual_table_ref(ot) → "mysql_orders.orders"
        ├─ 3. sync_tag = int(time.time())
        ├─ 4. 游标分页循环：
        │     while True:
        │       rows = trino.query(
        │         f"SELECT {pk_col},{title_col},{indexed_cols} FROM {table_ref} "
        │         f"WHERE {pk_col} > $last_pk ORDER BY {pk_col} LIMIT $batch",
        │         [last_pk, batch_size])
        │       if not rows: break
        │       合成 object_state（带 _virtual/_source_ref/_sync_tag）
        │       graph_projector.project_object(ont, ot, state)  # 批量版 upsert_nodes_batch
        │       last_pk = rows[-1][pk_col]
        ├─ 5. FK→边投影（§2.3，三种 LinkType 形态）
        ├─ 6. cleanup_stale_virtual(label, sync_tag)  # 孤儿清理
        └─ 返回 {nodes, edges, cleaned, partial, error}
```

### 3.2 查询阶段（Agent 发起 searchAround）

```
Agent → DataFrameQueryService.execute(ir)
  ① _eval_static([Customer:C001]) → PG object_state 拿 rid → [rid_C001]
  ② _eval_search_around(placedOrder):
     Neo4j: MATCH (c:Customer {rid:$rid})-[:PLACED_ORDER]->(o:Order) RETURN o.rid
     Neo4j 有 Order VIRTUAL 节点（骨架）→ [rid_Order1, rid_Order2]
     可选剪枝：WHERE o.status="PAID"（status 是 indexed，在 Neo4j）
  ③ _eval_search_around(suppliedBy):
     Neo4j: MATCH (o)-[:SUPPLIED_BY]->(s:Supplier) RETURN s.rid → [rid_Supp_A]
  ④ _hydrate([rid_C001, rid_Order1, ..., rid_Supp_A]):
     分流（已落地）：
       MANAGED rid → PG object_state（MVP，未来切 Doris 主源点查）
       VIRTUAL rid → parse_virtual_rid_pk → hydrate_by_pk（Trino 联邦查外部源全量属性）
     VIRTUAL 水合失败 → 返回 {_partial: true}（§2.8，不失败整个查询）
```

### 3.3 刷新阶段（admin rebuild）

```
POST /admin/project/rebuild-for-virtual/{ont}/{ot}
  └─ project_for_virtual_object_type(ont, ot)
     （同 §3.1 投影流程，重新拉 Trino + MERGE + cleanup 孤儿）
  幂等：重复调用 = 重新投影（Neo4j MERGE 天然幂等 + cleanup 清孤儿）
```

---

## 四、难点决策记录（交接给开发人员）

> 本节记录调研过程中识别的难点 + 业界参考方案 + 最终决策。开发人员遇到设计疑问时先查本节。

### 难点 1：FK→边的物理列名解析

**问题**：`LinkType.foreign_key_property_api_name` 是属性 api_name（camelCase），不是物理列名。要查 Trino 需解析成 backing_column（snake_case）。

**业界参考**：
- Ontop VKG：FK 是优化器一等公民，有 FK 做 query containment 优化，无 FK 降级为 UNION（性能差）。支持在 Lens 手工声明 FK 约束弥补源系统未声明。
- Neo4j 官方 relational-to-graph："A join or foreign key is a relationship"。

**代码事实**：
- `ontology-tool-layer.md` §`define_link_type` 原文："外键属性 api_name(**存储在源或目标端属性上**, 用于物化邻接索引)"
- 示例 `Order -> Customer, foreign_key="customer_no"`：FK 在 source(Order) 端指向 target(Customer) PK

**决策**：FK 归属两端容错查找（source 优先 → target 兜底），见 §2.3 `_resolve_fk_backing_column`。默认语义 FK 在 source 端持向 target PK（经典 N:1）。缺失时降级（节点投影继续，边跳过）。**handoff §4.3 "待确认点"已闭合**。

### 难点 2：VIRTUAL 节点的增量同步/刷新策略 + 孤儿清理

**问题**：外部源会变（增/删/改），投影态过期，无 CDC 通道。外部源删除的对象若不清理，永远留在 Neo4j 成孤儿，图遍历返回已不存在的 rid。

**业界参考**（三方案对比）：
| 方案 | 机制 | 缺点 |
|------|------|------|
| 全量重建（drop+re-import） | 每次先删全部再投影 | 重建窗口期断链（违 C9） |
| **watermark + cleanup（cartography）** | **每次生成 sync_tag，MERGE 时写入，结束后 DELETE WHERE sync_tag <> current** | **需节点带 sync_tag 字段** |
| snapshot diff（UBS lineage） | 拉快照与现状对比算 diff | 内存持两份快照，大表不可行 |

cartography 模式细节：`update_tag`（时间戳）写进每个节点，sync 结束后 `WHERE lastupdated <> $update_tag` 清理本次没触及的。"MERGE first, then clean up" 顺序避免窗口期断链。cleanup 必须带维度（label + `_virtual`），不误删其他类型。

**决策**：采用 watermark + cleanup（cartography 范式）。VIRTUAL 节点加 `_sync_tag`（int），rebuild 后 `cleanup_stale_virtual` 删 `_sync_tag <> current` 的节点。见 §2.4。**这是 handoff 的遗漏**——handoff §4.4 只说"幂等 MERGE"没提孤儿清理，本设计补上。

**`_sync_tag` 字段的权衡**：违反 handoff P2"身份骨架最小化"清单。但 `_sync_tag` 是 cleanup 必需的治理字段，且**只写给 VIRTUAL 节点**（MANAGED 不加，MANAGED 删除由 Action DELETE 驱动）。VIRTUAL 节点多一个 int 字段是合理代价。

### 难点 3：大表全量投影的内存与 Trino 压力

**问题**：`SELECT *` 一次拉整张外部表进内存，大表 OOM。

**业界参考**：
- Trino：`LIMIT/OFFSET` 深翻性能差（要扫描跳过的行）；游标分页（`WHERE pk > $last_pk ORDER BY pk LIMIT $batch`）利用 PK 索引稳定。
- Neo4j：`CALL {} IN TRANSACTIONS` 分批提交避免 OOM。

**决策**：
- Trino 拉取用**游标分页**（按 PK 排序 + `WHERE pk > $last_pk`），弃 OFFSET。
- Neo4j 写入用 CIT 批量提交（§2.5），弃逐条 MERGE。
- 批大小 1000（参考 cartography 默认 + Neo4j 官方建议）。

**handoff §4.2 差异**：handoff 说"LIMIT/OFFSET 或游标分页"，本设计明确选游标分页（弃 OFFSET）。

### 难点 4：rid 稳定性与外部源 PK 变更

**问题**：VIRTUAL rid 由外部源 PK 合成，PK 变了 rid 就变，Neo4j 残留旧节点（孤儿）。

**业界参考**：同难点 2，cartography 的 watermark + cleanup 正好解决——旧 rid 的节点因 `_sync_tag` 不更新而被 cleanup 删除。Neo4j 无内置 orphan constraint（[`agentos.to` 调研](https://agentos.to/research/ontology/no-orphans-constraint/)）。

**决策**：由难点 2 的 cleanup 机制覆盖。新 rid 被投影（新 `_sync_tag`），旧 rid 节点 `_sync_tag` 过期被删。**已知限制**：rid 不稳定导致跨查询不能缓存 rid→属性（每次查询重新水合，文档记录）。

### 难点 5：跨 catalog 联邦 JOIN 的性能（两端 VIRTUAL 边）

**问题**：两端 VIRTUAL 边需关联两个外部 catalog 的表。Trino 跨 catalog JOIN 大表可能 OOM/极慢。

**业界参考**（Trino 官方）：
- Join pushdown：connector 支持时下推到源系统（同源两表可下推）。**跨 catalog（不同源系统）JOIN 无法下推**，Trino 必须拉回内存 join。
- CBO 自适应：broadcast（小表广播）vs partitioned（大表按 key 分区），运行时大表自动降级避免 OOM。

**决策**：**不走 Trino 跨 catalog JOIN**（风险高），改走"分两步拉取 + 内存 join"：
1. 从 source VIRTUAL 表拉 `(source_pk, fk)` 对
2. 从 target VIRTUAL 表拉 `(target_pk,)` 集
3. 内存 join（fk == target_pk）构造 `(source_rid, target_rid)` 边集
4. 批量 project_link

理由：内存 join 可控（只拉 pk/fk 列，数据量小），避免跨 catalog JOIN 不可预测性。PK 集可缓存复用（同一 OT 的 PK 在节点投影时已拉过）。同源 JOIN 下推优化留二期。

**两端 VIRTUAL 边的 MVP 决策**（原 handoff 列二期）：两端 VIRTUAL 反而比一端 VIRTUAL 更简单（两端 rid 都合成，少一步 PG 反查 rid）。MVP 一并做。**handoff §4.3/§8 PR5 差异**：原列二期，本设计提至 MVP。

### 难点 6：Neo4j 批量写入性能

**问题**：逐条 `MERGE` 大表慢。

**业界参考**（Neo4j 官方 + cartography）：
- `CALL {} IN TRANSACTIONS`（CIT）：Neo4j 5 原生，官方推荐。`apoc.periodic.iterate` 在 Neo4j 2026.04/Cypher 25 **deprecated**，官方指向 CIT。
- `UNWIND` 批量参数化：一次传 N 行，减少 Cypher 解析开销。
- `neo4j-admin import`：离线直写存储文件，仅适合初始化空库，不适合增量 rebuild。
- 大表（>10M）MERGE 锁竞争，需先建唯一约束。

**决策**：MVP 用 `UNWIND + CALL {} IN TRANSACTIONS`。Gaia 用 `neo4j:5-community`，CIT 原生支持无需 APOC。新增 `upsert_nodes_batch` / `upsert_edges_batch`（§2.5）。前置确认 rid 唯一约束已建。

---

## 五、PR 拆解（按依赖排序）

### PR 0（前置，独立）：权限注入

- `DataFrameQueryService` 注入 `AuthorizationService`
- 图遍历入口调 `check_access`（ObjectType 级，防泄露无权 OT 存在性）
- `AuthorizationService` 已实现（`authorization_service.py`），只是接线
- 单测：mock AuthorizationService，验证无权 OT 被拦截

**为什么独立**：权限是横切关注点，与 VIRTUAL 投影正交。塞进折中方案 PR 会违反单一职责。MANAGED 路径也受益。

### PR 1：节点投影 MVP

- `Neo4jGraphStore.upsert_nodes_batch`（§2.5）+ `cleanup_stale_virtual`（§2.4）
- `GraphProjector.project_object` 扩展识别 `_virtual`/`_source_ref`/`_sync_tag`（§2.2）
- `ProjectSyncService.__init__` 注入 `engine` + `object_query`（container.py 同步）
- `ProjectSyncService.project_for_virtual_object_type` 节点部分（§2.1，游标分页 + 批量 MERGE + cleanup）
- 单测：mock Trino 返回，验证 Neo4j 节点写入正确（rid/label/indexed/_virtual/_source_ref/_sync_tag）
- 集成测试：testcontainers Trino + Neo4j，端到端节点投影 + 孤儿清理

### PR 2：FK→边投影

- `_resolve_fk_backing_column`（§2.3）
- `PostgresMetaStore.get_object_states_by_pks`（§2.6）
- 一端 VIRTUAL 一端 MANAGED 边投影 + 两端 VIRTUAL 边投影（内存 join）
- `Neo4jGraphStore.upsert_edges_batch`
- 单测：三种 LinkType 形态（两端 MANAGED 跳过 / 一端 VIRTUAL / 两端 VIRTUAL）+ FK 缺失降级
- 集成测试：混合链路 searchAround 返回非空

### PR 3：触发链路 + admin API

- `register_virtual_table` 后 `asyncio.create_task` 异步触发（§3.1）
- `POST /admin/project/rebuild-for-virtual/{ont}/{ot}` 路由（§3.3）
- partial 降级：Trino 失败不阻塞 register，记日志返回 partial
- 单测：触发链路 + 幂等 rebuild

### PR 4：一致性 + 治理

- `ConflictDetector._audit_iteration` 排除 VIRTUAL（§2.7）
- `_hydrate_virtual` 改 `_partial` 标记（§2.8）
- 单测：ConflictDetector 跳过 VIRTUAL + 水合失败返回 partial

### PR 5（二期）：定时刷新 + 行级权限 + FK 自动推断

- indexed 属性定时刷新（分钟级，lifespan 后台任务）
- VIRTUAL 水合行级权限（Cedar TPE → Trino WHERE）
- `foreign_key_property_api_name` 缺失时从外部源 schema FK 自动推断回填（借鉴 Neo4j Virtual Graph 的 AI model 生成）

---

## 六、测试矩阵

### 6.1 单元测试（mock 外部依赖）

| 场景 | 覆盖点 |
|------|--------|
| 节点投影正常路径 | mock Trino 返回 → 验证 Neo4j 节点字段（rid/label/indexed/_virtual/_source_ref/_sync_tag） |
| 游标分页 | mock Trino 多批返回 → 验证 last_pk 推进 + 全量覆盖 |
| 孤儿清理 | 预置旧 `_sync_tag` 节点 → rebuild 后验证被删 + MANAGED 节点未误删 |
| FK 解析 source 端 | FK 属性在 source OT → backing_column 正确 |
| FK 解析 target 端 | FK 属性在 target OT → backing_column 正确 |
| FK 缺失降级 | `foreign_key_property_api_name` 为空 → 边不投影，节点仍投影 |
| 一端 VIRTUAL 边 | MANAGED PK→rid 批量反查 + 边构造 |
| 两端 VIRTUAL 边 | 内存 join + 双 rid 合成 |
| 悬空 FK | MANAGED 端不存在 → 边跳过不报错 |
| partial 降级 | Trino 水合失败 → 返回 `_partial:true` |
| PK 含特殊字符 | rid 合成 safe_pk 替换 + 水合解析（已知限制文档化） |
| ConflictDetector 排除 VIRTUAL | VIRTUAL OT 不进审计循环 |

### 6.2 集成测试（testcontainers）

| 场景 | 组件 |
|------|------|
| 端到端节点投影 | testcontainers Trino（mock 外部源 catalog）+ Neo4j |
| 端到端边投影 | 同上 + PG（object_state）|
| 跨 VIRTUAL searchAround | 混合链路 Customer(MANAGED)-[placedOrder]->Order(VIRTUAL)-[suppliedBy]->Supplier(MANAGED) 返回非空 |
| rebuild 幂等 | 重复调用 → 节点/边数不变 + 孤儿清理生效 |
| 大表分批 | 10万行 mock → 分批拉取 + 批量 MERGE 完成 |

### 6.3 异常路径

| 场景 | 预期 |
|------|------|
| Trino 不可用 | 投影返回 partial，不阻塞 register_virtual_table |
| Neo4j 不可用 | 投影记日志失败，register_virtual_table 仍成功 |
| 外部源表空 | 投影返回 {nodes:0}，不报错 |
| PK 列不存在于表 | 报错（元数据不一致，应暴露非静默） |
| FK 列不存在于表 | 该 LinkType 边跳过 + warning，节点仍投影 |

---

## 七、文档同步清单

动代码前/后必做（CLAUDE.md「设计意图变更先记 ADR/设计文档」）：

- [x] ADR-021 起草（`adr-021-virtual-graph-projection.md`）
- [x] `graph-reasoning-design.md` §6 修订（新增 §6.5 + 修订 §6.3/§6.4）
- [x] CLAUDE.md 红线 9 补充图投影例外 + ADR 索引表加 ADR-021
- [x] `implementation-status.md` 更新 VIRTUAL 投影状态（PR 合并后 🟡→✅；2026-07-16 三次校准：§二 ObjectIndexFunnel/GraphProjector 行 + §三 `/admin/*` 路由 + §十二 M1 ④ + §12.7 路标 #15/#16 + §十三 三次校准记录）
- [ ] handoff 文档标注已闭合的待确认点（附录 A）

---

## 附录 A：handoff 待确认点闭合状态

| handoff 待确认点 | 闭合状态 | 闭合依据 |
|------------------|:---:|---------|
| §4.2 ObjectType 是否有 title_property 标识 | ✅ 已有 | `ObjectTypeModel.title_property`（String 255，非空）+ `PropertyDefModel.is_title_property`（Boolean）|
| §4.3 FK 属性归属哪一端 | ✅ 文档明确 | `ontology-tool-layer.md`："存储在源或目标端属性上"，两端容错查找（§2.3）|
| §4.3 `foreign_key_property_api_name` 是否强制要求 | ✅ 不强制，缺失降级 | FK 缺失时边不投影，节点仍投影（降级）|
| §4.3 MANAGED 端 PK→rid 批量反查方法 | ✅ 不存在，需新增 | `PostgresMetaStore.get_object_states_by_pks`（§2.6）|
| §4.5 AuthorizationService 是否已实现 | ✅ 已实现 | `authorization_service.py`，方法 `check_access`/`check_access_batch`/`evaluate_query_scope` |
| §4.5 ObjectType 级权限 P0 归属 | ✅ 独立 PR 0 | 横切关注点，与 VIRTUAL 投影正交（§5 PR 0）|
| §4.3 两端 VIRTUAL 边是否二期 | ✅ 提至 MVP | 成本不更高（少一步 PG 反查），§4 决策 6 |
| §4.4 触发链路同步/异步/outbox | ✅ asyncio.create_task | outbox 是 Action 语义，VIRTUAL 投影不是 Action（ADR-021 D9）|
| §7.2 ADR-015 补充 vs 新建 | ✅ 新建 ADR-021 | 独立架构决策，分量足够 |

## 附录 B：代码事实核对（设计起草时已验证）

| 事实 | 位置 | 状态 |
|------|------|------|
| `generate_virtual_rid` / `parse_virtual_rid_pk` / `is_virtual_rid` | `src/ontology/core/rid.py` | ✅ 已实现 |
| `_hydrate` 按 rid type 分流 | `object_set_executor.py:1416` | ✅ 已实现 |
| `_hydrate_virtual` 调 `hydrate_by_pk`（静默跳过） | `object_set_executor.py:1490` | ✅ 待改 partial（§2.8）|
| `LinkTypeModel.foreign_key_property_api_name` + `cardinality` + `direction` | `core/models/ontology.py:169-171` | ✅ 字段已存在 |
| `PropertyDefModel.backing_column` / `is_title_property` / `indexed` | `core/models/ontology.py:124,131,125` | ✅ 字段已存在 |
| `ObjectTypeModel.title_property` / `primary_key` | `core/models/ontology.py:75,76` | ✅ 字段已存在 |
| `ObjectQueryService.hydrate_by_pk` + `_virtual_table_ref` | `object_query_service.py:381,608` | ✅ 已实现 |
| `ProjectSyncService` Gate 1 skip VIRTUAL | `project_sync_service.py:108` | ✅ 现状（新方法旁路）|
| `GraphProjector.project_object` 仅投影 indexed | `graph_projector.py:48` | ✅ 现状（待扩展 §2.2）|
| `Neo4jGraphStore.upsert_node` 逐条 MERGE | `neo4j_graph_store.py:185` | ✅ 现状（待新增 batch §2.5）|
| `ConflictDetector._audit_iteration` 未排除 VIRTUAL | `conflict_detector.py:204` | ✅ 现状（待改 §2.7）|
| `AuthorizationService.check_access` 已实现 | `authorization_service.py:101` | ✅ 已实现（待接线 PR 0）|
| `ProjectSyncService` 未注入 TrinoQueryEngine | `container.py:265` | ✅ 现状（待补注入）|
| `naming.graph_label` / `graph_relationship_type` | `core/naming.py` | ✅ 已实现（VIRTUAL 复用）|

## 附录 C：术语对照

| 术语 | 含义 | 出处 |
|------|------|------|
| 身份骨架 | rid + label + PK + title + indexed + `_virtual` + `_source_ref` + `_sync_tag` | 本文 §2.2 + ADR-021 D1 |
| 模式 C | 子集复制（主源 + 派生索引） | Neo4j polyglot persistence |
| 触发模式 D | VIRTUAL 联邦投影（Trino→Neo4j 骨架） | 本文 §1.2 + ADR-021 D3 |
| best-effort + 不可对账 | VIRTUAL 节点一致性语义 | ADR-021 D7 |
| watermark + cleanup | 孤儿清理范式（cartography） | 本文 §2.4 + 难点 2 |
| 路径 ③' | 自研查询时联邦（远期） | 三场景调研 §5.1 |
