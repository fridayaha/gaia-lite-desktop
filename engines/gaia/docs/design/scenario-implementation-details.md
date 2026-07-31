# Scenario 工程实现细节：存储、查询、性能、可靠性

> **范围**：把 Scenario 的数据写入/读取**在 Gaia 具体技术栈里的工程实现**讲透——每个引擎承担什么角色、索引怎么建、查询计划长什么样、事务/并发/故障如何处理、性能边界在哪。
> **前置**：[`scenario-and-decision-exhaust-design.md`](./scenario-and-decision-exhaust-design.md) 的 §1.2（what-if 逻辑）和 §2-§6（数据模型/Service/API）。本文是那些设计的**工程落地细节**补充，回答"具体怎么实现、性能行不行、挂了怎么办"。
> **日期**：2026-07-06

---

## 目录
- [一、各引擎在 Scenario 下的角色（首要澄清）](#一各引擎在-scenario-下的角色首要澄清)
- [二、写入路径的工程实现](#二写入路径的工程实现)
- [三、读取路径的工程实现](#三读取路径的工程实现)
- [四、索引设计与查询计划](#四索引设计与查询计划)
- [五、事务与并发控制](#五事务与并发控制)
- [六、可靠性与故障恢复](#六可靠性与故障恢复)
- [七、性能边界与容量规划](#七性能边界与容量规划)
- [八、对底层存储和查询引擎的要求清单](#八对底层存储和查询引擎的要求清单)

---

## 一、各引擎在 Scenario 下的角色（首要澄清）

> 这是理解后续一切的前提。Scenario **不是**把数据写到所有引擎，而是**只写 PG，只读 PG**。Doris/Iceberg/Trino/Neo4j 在 Scenario 路径中完全不参与。

### 1.1 Gaia 现有读写链路（main 分支，回顾）

```
Action 写入（main）:
  ActionService.execute_action
    → Step 8:  PG object_state（OCC upsert）        ← 同步，read-your-writes 源
    → Step 9:  PG execution_log + outbox            ← 同事务
    → Step 10: PG commit
    → Step 11: OutboxExecutor INDEX effect → Doris upsert (≤1s)   ← 异步，近实时（去 SeaTunnel 化，不经 Kafka/SeaTunnel）
    → Step 12: Neo4j 投影                            ← 异步，best-effort

查询（main）:
  ObjectQueryService
    → MANAGED: Doris 主（load_by_filter/execute_sql）  ← 在线索引，快
              ↓ Doris 不可用
              Trino 扫 Iceberg（降级，慢但完整）
    → VIRTUAL: Trino 联邦（外部数据源，无 Doris）
```

**关键点**：main 的查询主源是 **Doris**（在线索引），PG object_state 只是 Action 的同步写目标 + read-your-writes 兜底，**不是查询主源**。

### 1.2 Scenario 的读写链路（新）

```
Action 写入（Scenario）:
  ActionService.execute_action(scenario_id=X)
    → Step 0:  校验 Scenario 状态（ACTIVE）
    → Step 8:  PG object_state（scenario_id=X 的 overlay 行）  ← 只写 PG
    → Step 9:  PG execution_log（scenario_id=X）+ outbox       ← 只写 PG
    → Step 10: PG commit
    → Step 11: 跳过（Scenario 不触发 CDC，不写 Doris/Iceberg）
    → Step 12: 跳过（Scenario 不投影 Neo4j）

查询（Scenario）:
  ObjectQueryService.filter_objects(scenario_id=X)
    → PG object_state（base + overlay 合并查询）  ← 只读 PG
    → 不走 Doris / Trino / Iceberg
```

**为什么 Scenario 只用 PG？** 三个原因：

1. **隔离性**：Scenario 是"假设世界"，不能污染 Doris/Iceberg（那是 main 的生产数据）。CDC 链路是 main 专用的，Scenario 数据进 Kafka 会污染 Doris。
2. **即时性**：Doris 的数据靠 CDC 异步同步（秒级延迟），而 Scenario 要求"Action 执行完立刻能看到效果"（what-if 的核心体验）。PG 是同步写，read-your-writes 天然满足。
3. **规模可控**：Scenario 限制 30000 edits / 10000 对象查询（对齐 Palantir），这个量级 PG 单表轻松处理，不需要 Doris 的分布式能力。

### 1.3 各引擎要求矩阵

| 引擎 | Scenario 写入 | Scenario 查询 | Scenario 要求 |
|------|:---:|:---:|------|
| **PostgreSQL** | ✅ 唯一写入点 | ✅ 唯一查询点 | 复合主键 + JSONB GIN 索引 + 事务 + 行级锁 |
| **Doris** | ❌ 不参与 | ❌ 不参与 | 无新增要求（main 路径不变） |
| **Iceberg** | ❌ 不参与 | ❌ 不参与（决策回放除外，用 snapshot） | 无新增要求 |
| **Trino** | ❌ 不参与 | ❌ 不参与 | 无新增要求 |
| **Neo4j** | ❌ 不参与 | ❌ 不参与 | 无新增要求 |
| **Kafka** | ❌ 不参与（Scenario 不进 CDC） | ❌ | 无新增要求 |

> **结论**：Scenario 的全部工程要求集中在 **PostgreSQL** 一个引擎上。这是设计的简化点，也是性能/可靠性的关键约束点。

---

## 二、写入路径的工程实现

### 2.1 overlay 写入的 SQL（精确到语句）

#### CREATE_OBJECT in Scenario

```sql
-- Scenario 内新建对象（base 没有该 rid）
INSERT INTO object_state (rid, scenario_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at)
VALUES (:rid, :scenario_id, :ot_api, :ontology_id, 1, :properties, :modified_by, now(), now())
ON CONFLICT (rid, scenario_id) DO NOTHING
-- ON CONFLICT 保证并发同对象 CREATE 不报错（返回 rowcount=0 → 调用方判 conflict）
```

**要点**：
- 复合主键 `(rid, scenario_id)` 让同一对象在 main（scenario_id=NULL）和不同 Scenario 各有独立行，互不冲突
- `ON CONFLICT DO NOTHING` 是 OCC 的 CREATE 语义（对齐现有 `upsert_object_state` 的 expected_version=0 路径）
- `ON CONFLICT` 要求复合主键或唯一约束存在——这是 §八 对 PG 的硬要求

#### UPDATE_PROPERTY in Scenario（累积叠加，最复杂）

```sql
-- 1. 读 base 行的 version（OCC 校验基底）
SELECT version, properties FROM object_state
WHERE rid = :rid AND scenario_id IS NULL;

-- 2. 读当前 overlay 行（若存在，用于累积合并）
SELECT version, properties FROM object_state
WHERE rid = :rid AND scenario_id = :scenario_id;

-- 3a. 首次 UPDATE（无 overlay 行）：以 base properties 为基底合并，INSERT overlay
INSERT INTO object_state (rid, scenario_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at)
VALUES (:rid, :scenario_id, :ot_api, :ontology_id, 1,
        :base_properties || :changed_properties,  -- PG JSONB merge（|| 合并键）
        :modified_by, now(), now());

-- 3b. 后续 UPDATE（有 overlay 行）：以 overlay properties 为基底合并，UPDATE overlay
UPDATE object_state
SET properties = properties || :changed_properties,  -- JSONB || 在现有 overlay 上覆盖
    version = version + 1,
    modified_by = :modified_by,
    updated_at = now()
WHERE rid = :rid AND scenario_id = :scenario_id
  AND version = :current_overlay_version;  -- overlay 行 OCC（防并发同 Scenario 内冲突）
```

**关键工程细节**：

1. **JSONB `||` 合并操作符**：PG 的 `jsonb || jsonb` 做顶层键合并（后者覆盖前者）。这正是 overlay 累积需要的——`existing_overlay_props || new_changes` 把新改动覆盖到 overlay 上。**注意**：`||` 是浅合并，嵌套对象会被整体替换而非深度合并。若 properties 有嵌套结构需深度合并，要用 `jsonb_set` 逐键更新（MVP 用浅合并，文档标注限制）。

2. **两层 OCC**：
   - **base 层 OCC**：Step 1 读 base version，与 `expected_version` 比对。不匹配 → 返回 0（ConflictError）。这是"用户基于的 base 数据是否过期"的校验。
   - **overlay 层 OCC**：Step 3b 的 `WHERE version = :current_overlay_version` 防止同 Scenario 内并发 Action 冲突。不匹配 → 返回 0（ConflictError，"该对象在你这次 Action 之前被同 Scenario 的另一个 Action 改了"）。

3. **非原子性风险**：Step 1-3 是三条独立 SQL，中间有读窗口。并发下可能出现：
   - Action A 读 base v5，Action B 同时读 base v5
   - 两者都校验通过，都写 overlay
   - 这是**可接受的**——两个 Action 基于同一 base 状态做不同假设，overlay 层 OCC 保证只有一个成功（后者的 WHERE version 匹配失败）
   - 但若两个 Action 改**同一对象同一属性**，后者会整体覆盖前者（last-write-wins）。这是 Scenario 的合理语义（用户串行操作时不会触发，并发 Scenario 编辑不常见）

4. **性能**：单对象 UPDATE = 2 次 SELECT + 1 次 INSERT/UPDATE，全部走索引（复合 PK + scenario_id 索引），单次 < 1ms。50 个 Action × 平均 10 对象 = 500 次写入 < 500ms，可接受。

#### DELETE_OBJECT in Scenario（软删除）

```sql
-- Scenario 内删除 = overlay 行标记 __deleted（不是真删）
INSERT INTO object_state (rid, scenario_id, ..., version, properties, ...)
VALUES (:rid, :scenario_id, ..., 1,
        jsonb_build_object('__deleted', true, '__deleted_at', to_char(now()::timestamptz, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
        ...)
ON CONFLICT (rid, scenario_id) DO UPDATE
SET properties = object_state.properties || jsonb_build_object('__deleted', true, '__deleted_at', ...),
    version = object_state.version + 1, updated_at = now();
```

**为什么软删除**：查询时要能区分"base 有但 Scenario 删了"（显示为删除）vs"base 没有"（不存在）。若真删 overlay 行，查询时无法区分。软删除标记让 §3.2 的查询能正确过滤。

### 2.2 RELATE/UNRELATE in Scenario

```sql
-- RELATE: 直接 INSERT（同 base 语义，但带 scenario_id）
INSERT INTO object_links (id, ontology_id, scenario_id, link_type_api_name, source_rid, target_rid, created_at)
VALUES (:id, :ontology_id, :scenario_id, :lt_api, :src, :tgt, now())
ON CONFLICT (link_type_api_name, source_rid, target_rid, scenario_id) DO NOTHING;

-- UNRELATE: Scenario 内用 overlay 标记（同 DELETE 软删除逻辑）
-- 方案 A（推荐 MVP）：直接删除该 scenario 的 overlay link 行
DELETE FROM object_links
WHERE scenario_id = :scenario_id AND link_type_api_name = :lt_api
  AND source_rid = :src AND target_rid = :tgt;
-- 查询时 base 的 link 仍存在（Scenario 没删 base），表现正确

-- 方案 B（若要支持"Scenario 内 UNRELATE 后又能 RELATE 回来"）：软删除标记
-- 需 object_links 加 __deleted 列，复杂度高，MVP 不做
```

**方案 A 的语义边界**：UNRELATE 删 overlay 行后，查询 base+overlay 合并，该 link 显示为 base 的（因为 overlay 没了）。这其实是**错误的**——用户想"在 Scenario 内取消这个关系"，但删 overlay 后查询会显示 base 的关系还在。

**修正**：UNRELATE 必须用软删除（方案 B），否则语义不对。MVP 决策：`object_links` 加 `is_deleted: bool = false` 列，UNRELATE 置 true，查询过滤 `is_deleted=false`。

### 2.3 写入事务边界

```python
# ActionService.execute_action（scenario_id 模式）的事务结构
async with self._metadata.transaction():  # PG 事务单元
    # Step 8: 写 object_state overlay（可能多个对象）
    for mutation in mutations:
        await self._metadata.upsert_object_state_scenario(...)
    # Step 9: 写 execution_log + outbox
    execution = await self._metadata.create_execution_log(scenario_id=ctx.scenario_id, ...)
    # Step 9.5（决策捕获）: 写 __Decision 对象（若启用）
    # Step 10: commit（transaction 上下文器自动 commit）
```

**事务保证**：所有 overlay 写入 + execution_log + outbox + Decision 对象在同一 PG 事务。要么全成功，要么全回滚。这复用了现有 main 路径的 `transaction()` 上下文器（`postgres_meta_store.py:2069`），零改造。

---

## 三、读取路径的工程实现

### 3.1 单对象读取（base + overlay 合并）

```sql
-- get_object_state_with_overlay(rid, scenario_id)
SELECT
  COALESCE(s.properties, b.properties) AS properties,
  COALESCE(s.version, b.version) AS version,
  CASE
    WHEN s.rid IS NOT NULL AND (s.properties->>'__deleted')::bool = true THEN 'DELETED'
    WHEN s.rid IS NOT NULL AND b.rid IS NULL THEN 'CREATED'
    WHEN s.rid IS NOT NULL THEN 'UPDATED'
    ELSE 'BASE'
  END AS source
FROM object_state b
LEFT JOIN object_state s
  ON s.rid = b.rid
  AND s.scenario_id = :scenario_id
WHERE b.rid = :rid
  AND b.scenario_id IS NULL
  AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false)

UNION ALL

-- Scenario 内新建的对象（base 没有）
SELECT s.properties, s.version, 'CREATED' AS source
FROM object_state s
WHERE s.scenario_id = :scenario_id
  AND s.rid = :rid
  AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false)
  AND NOT EXISTS (
    SELECT 1 FROM object_state b
    WHERE b.rid = s.rid AND b.scenario_id IS NULL
  );
```

**执行计划**（EXPLAIN 预期）：
- 第一部分：base 行走主键索引 `(rid, scenario_id)` 定位（scenario_id IS NULL），LEFT JOIN scenario 行走同一索引。**两行精确查找，< 1ms**。
- UNION ALL 第二部分：scenario 行走索引，NOT EXISTS 走索引反查。**< 1ms**。

### 3.2 批量对象读取 + filter（Scenario 查询主路径）

这是 what-if 的核心查询——"在 Scenario A 里，筛选所有 DELAYED 航班"。

```sql
-- list_objects_with_overlay(ontology_id, object_type_api_name, scenario_id, filters, limit, offset)
WITH base_objs AS (
  -- base 行（scenario_id IS NULL）+ overlay 合并
  SELECT
    b.rid,
    COALESCE(s.properties, b.properties) AS properties,
    CASE
      WHEN s.rid IS NOT NULL AND (s.properties->>'__deleted')::bool = true THEN 'DELETED'
      WHEN s.rid IS NOT NULL THEN 'UPDATED'
      ELSE 'BASE'
    END AS source
  FROM object_state b
  LEFT JOIN object_state s
    ON s.rid = b.rid AND s.scenario_id = :scenario_id
  WHERE b.ontology_id = :ontology_id
    AND b.object_type_api_name = :ot_api
    AND b.scenario_id IS NULL
),
scenario_created AS (
  -- Scenario 内新建的对象
  SELECT s.rid, s.properties, 'CREATED' AS source
  FROM object_state s
  WHERE s.scenario_id = :scenario_id
    AND s.ontology_id = :ontology_id
    AND s.object_type_api_name = :ot_api
    AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false)
    AND NOT EXISTS (SELECT 1 FROM object_state b WHERE b.rid = s.rid AND b.scenario_id IS NULL)
),
merged AS (
  SELECT * FROM base_objs WHERE source != 'DELETED'  -- 过滤 Scenario 删除的
  UNION ALL
  SELECT * FROM scenario_created
)
SELECT rid, properties, source FROM merged
WHERE properties @> :filter_jsonb   -- GIN 索引加速的包含查询
ORDER BY rid
LIMIT :limit OFFSET :offset;
```

**关键工程点**：

1. **CTE（WITH）拆分**：base+overlay 合并 vs scenario 新建，两个子查询 UNION ALL，逻辑清晰且 PG 优化器能各自优化。

2. **filter 的 JSONB 表达**：`properties @> :filter_jsonb` 用 GIN 索引。但 Gaia 现有 filter 支持 eq/neq/gt/lt/in/contains 等多操作符，`@>` 只能做等值包含。**这是 Scenario filter 的核心工程难点**，见 §4.2 详述。

3. **过滤 DELETED**：`WHERE source != 'DELETED'` 在 CTE 外层过滤。注意 Scenario 删除的对象不参与查询结果（对齐 Palantir）。

4. **分页**：`LIMIT :limit OFFSET :offset`。**注意**：OFFSET 大了性能差（深翻页）。MVP 用 OFFSET，后续可改 cursor 分页（基于 `rid` 排序的 keyset pagination）。

### 3.3 多 Scenario 并排对比

```sql
-- compare_objects_across_scenarios(ontology_id, ot_api, [scn_a, scn_b], filters, limit)
WITH base AS (
  SELECT rid, properties FROM object_state
  WHERE ontology_id = :ontology_id AND object_type_api_name = :ot_api AND scenario_id IS NULL
),
scn_a AS (
  SELECT rid, properties FROM object_state WHERE scenario_id = :scn_a
    AND (properties->>'__deleted' IS NULL OR (properties->>'__deleted')::bool = false)
),
scn_b AS (
  SELECT rid, properties FROM object_state WHERE scenario_id = :scn_b
    AND (properties->>'__deleted' IS NULL OR (properties->>'__deleted')::bool = false)
)
SELECT
  b.rid,
  b.properties AS base_props,
  a.properties AS scn_a_props,
  c.properties AS scn_b_props
FROM base b
LEFT JOIN scn_a a ON a.rid = b.rid
LEFT JOIN scn_b c ON c.rid = b.rid
WHERE b.properties @> :filter_jsonb
  -- 至少一个 Scenario 有改动才返回（可选优化，减少返回行）
  AND (a.rid IS NOT NULL OR c.rid IS NOT NULL)
LIMIT :limit;
```

**注意**：此查询只返回 base 存在的对象。Scenario 新建的对象（base 没有）需单独 UNION（同 §3.2 的 scenario_created 逻辑）。MVP 可先不支持"Scenario 新建对象的并排对比"（边界场景），文档标注。

### 3.4 聚合查询 in Scenario（SUM/COUNT/AVG）

```sql
-- aggregate_in_scenario: 在 base+overlay 合并视图上聚合
WITH merged AS (
  -- 复用 §3.2 的 merged CTE
  ...
)
SELECT
  COUNT(*) AS cnt,
  SUM((properties->>'cost')::numeric) AS total_cost,
  AVG((properties->>'cost')::numeric) AS avg_cost
FROM merged
WHERE properties @> :filter_jsonb;
```

**性能**：聚合需扫描所有匹配行，无法用索引规避。10000 对象的聚合 < 100ms（PG JSONB 解析 + numeric 求和，单核够用）。这是 Palantir 限制 Scenario 查询 ≤10000 对象的原因。

---

## 四、索引设计与查询计划

### 4.1 必需的索引（migration 必建）

```sql
-- 1. 复合主键（§2.1 已述，migration 建）
ALTER TABLE object_state DROP CONSTRAINT object_state_pkey;
ALTER TABLE object_state ADD PRIMARY KEY (rid, scenario_id);

-- 2. 按 object_type + scenario 查询的索引（§3.2 批量查询主路径）
CREATE INDEX ix_object_state_type_scenario
  ON object_state (object_type_api_name, scenario_id)
  WHERE scenario_id IS NOT NULL;  -- 部分索引：只索引 Scenario 行（base 行走主键）
-- 注：base 行的按类型查询走 ix_object_state_type_scenario 的 base 部分（另建）
CREATE INDEX ix_object_state_type_base
  ON object_state (object_type_api_name)
  WHERE scenario_id IS NULL;  -- base 行专用

-- 3. JSONB GIN 索引（filter 性能关键！）
-- 方案 A：全 GIN（支持 @> ? ?| ?& 操作符，索引大）
CREATE INDEX ix_object_state_properties_gin
  ON object_state USING GIN (properties jsonb_path_ops);
-- jsonb_path_ops 比默认 jsonb_ops 小 30%，只支持 @> 但 Scenario filter 够用

-- 方案 B（更优）：表达式索引（针对高频 filter 字段）
-- 若用户常按 status/filter，单独建表达式索引
CREATE INDEX ix_object_state_status
  ON object_state ((properties->>'status'))
  WHERE scenario_id IS NOT NULL;

-- 4. object_links 的 scenario 索引
CREATE INDEX ix_object_links_scenario
  ON object_links (scenario_id, link_type_api_name, source_rid)
  WHERE scenario_id IS NOT NULL;

-- 5. branches 表
CREATE UNIQUE INDEX uq_branches_ontology_name ON branches (ontology_id, name);
CREATE INDEX ix_branches_ontology_main ON branches (ontology_id) WHERE is_main = true;
```

### 4.2 filter 操作符到 JSONB 查询的映射（核心工程难点）

Gaia 现有 `_filter_dict_to_sql` 支持 eq/neq/gt/lt/gte/lte/in/notIn/contains/startsWith/endsWith/isNull/isNotNull/and/or/not。Scenario 查询走 PG，需把这些操作符映射到 PG JSONB 操作。

**映射表**（假设 filter `{"status": {"eq": "DELAYED"}}` 作用于 `properties`）：

| 操作符 | PG JSONB 表达 | GIN 可加速？ |
|--------|--------------|:---:|
| eq | `properties @> '{"status":"DELAYED"}'` | ✅ |
| neq | `NOT (properties @> '{"status":"DELAYED"}')` 或 `properties->>'status' != 'DELAYED'` | ❌（表达式索引可） |
| gt/lt/gte/lte | `(properties->>'cost')::numeric > 1000` | ❌（表达式索引可） |
| in | `properties->>'status' IN ('DELAYED','CANCELLED')` | ❌（表达式索引可） |
| notIn | `properties->>'status' NOT IN (...)` | ❌ |
| contains | `properties->>'name' LIKE '%keyword%'` | ❌（pg_trgm 可加速） |
| startsWith | `properties->>'name' LIKE 'prefix%'` | ❌（表达式索引 range scan 可） |
| isNull | `properties->>'field' IS NULL` 或 `NOT properties ? 'field'` | ✅（`?` 用 GIN） |
| and/or/not | SQL 的 AND/OR/NOT 组合 | 视子操作符 |

**工程决策**：

1. **eq/isNull 优先用 `@>`/`?`**（GIN 加速），其余用 `->>` 提取（可能全表扫）
2. **高频 filter 字段建表达式索引**（如 `(properties->>'status')`）——但这需要知道用户常 filter 哪些字段。MVP 不自动建，提供 admin API 让用户按需建
3. **复合 filter**（如 `status=DELAYED AND cost>1000`）：`@>` 加速 status 部分，cost 部分扫过滤后的行。若 status 过滤后行数少，性能可接受
4. **fallback**：若无合适索引，PG 顺序扫 10000 行约 50-100ms（可接受，在 Palantir 限制内）

**实现**：新建 `_filter_dict_to_pg_jsonb(filters: dict) -> tuple[str, list]`，产出 `(where_sql, params)`。复用现有 `_OP_COMPARE` 映射表的思路，但目标方言是 PG JSONB 而非 Doris SQL。

### 4.3 查询计划分析（EXPLAIN）

预期 `list_objects_with_overlay` 的查询计划：

```
Limit (cost=xxx rows=1000)
  ->  Sort (cost=xxx)
        ->  Hash Join (cost=xxx)                    -- base LEFT JOIN scenario
              ->  Seq Scan on object_state b        -- base 行（scenario_id IS NULL）
                    Filter: object_type_api_name = 'Flight' AND scenario_id IS NULL
                    -- 若有 ix_object_state_type_base，走 Index Scan
              ->  Hash (cost=xxx)
                    ->  Bitmap Heap Scan on object_state s  -- scenario 行
                          Recheck Cond: scenario_id = 'scn_001'
                          Filter: object_type_api_name = 'Flight'
                          ->  Bitmap Index Scan on ix_object_state_type_scenario
                                Index Cond: (scenario_id, object_type_api_name) = ('scn_001', 'Flight')
```

**性能预期**（10000 base 对象 + 50 scenario overlay）：
- base 扫描：走 `ix_object_state_type_base`，10000 行 Index Scan ~10ms
- scenario 扫描：走 `ix_object_state_type_scenario`，50 行 Bitmap Index Scan ~1ms
- Hash Join + filter + Sort + Limit：~5ms
- **总计 < 20ms**，满足交互体验

---

## 五、事务与并发控制

### 5.1 事务隔离级别

Gaia 的 PG 用默认的 **Read Committed**（SQLAlchemy 默认）。

**对 Scenario 的影响**：
- Read Committed 下，同一事务内的 SELECT 看到最新已提交数据（无 statement-level snapshot）
- Scenario 内连续 Action 是**独立事务**（每个 execute_action 一个事务），所以 Action 2 能看到 Action 1 的 overlay（已提交）
- 这正是"累积叠加"语义需要的行为

**潜在问题**：若需要"一个 Scenario 的所有 Action 原子提交"（全成功或全回滚），需用单事务包裹多 Action。但 Palantir 的 Scenario 是"逐个 Action 累积"，不要求原子批量。MVP 不做多 Action 原子事务。

### 5.2 并发冲突场景

| 场景 | 机制 | 结果 |
|------|------|------|
| 两个用户同时改同一 Scenario 同一对象 | overlay 行 OCC（`WHERE version = X`） | 后者 ConflictError 409，提示重试 |
| 两个用户同时改不同 Scenario 同一 base 对象 | 各自写各自 overlay，互不影响 | 都成功（正确，假设世界隔离） |
| Scenario Action 执行中 base 被改 | base 行 OCC 校验（expected_version vs base.version） | 若 base version 变了，ConflictError（"你基于的 base 过期了"） |
| 同一用户串行改同 Scenario 同对象 | 第 2 次 Action 读到第 1 次的 overlay（已提交） | 累积正确 |
| Scenario apply 到 main 时 base 已变 | apply 重放每个 Action 到 main，main 的 OCC 校验 | 逐个 Action 可能冲突，返回冲突列表 |

### 5.3 行级锁

PG 的 UPDATE 自动加行级锁（FOR UPDATE 隐式）。Scenario 写 overlay 行时锁该行，并发同对象的 Action 2 阻塞直到 Action 1 提交。这是正确行为，但要注意：

**死锁风险**：若 Action 1 改对象 A→B，Action 2 改对象 B→A（相反顺序），可能死锁。PG 检测到死锁会 abort 一个事务。**缓解**：ActionService 内部对 mutations 按 rid 排序后写入，保证加锁顺序一致。

### 5.4 apply_scenario 的并发安全

```python
async def apply_scenario(self, scenario_id, *, dry_run=False):
    scenario = await get_branch(scenario_id)
    # 读 scenario 的所有 execution_log（按时间顺序）
    logs = await list_execution_logs(scenario_id, order_by="created_at ASC")
    results = []
    for log in logs:
        # 重放每个 Action 到 main（scenario_id=None）
        try:
            await self._action_service.execute_action(
                object_type_api_name=log.object_type_api_name,
                action_api_name=log.action_type_api_name,
                request=ActionExecutionRequest(
                    parameters=log.parameters,
                    idempotency_key=f"apply_{log.id}",  # 幂等键防重复
                ),
                context=ActionContext(scenario_id=None, ...),
            )
            results.append({"action_id": log.id, "status": "applied"})
        except ConflictError as e:
            results.append({"action_id": log.id, "status": "conflict", "error": str(e)})
    # 全部成功 → status=APPLIED；有冲突 → status=PARTIALLY_APPLIED（用户决定）
```

**要点**：
- 用唯一 idempotency_key（`apply_{log.id}`）防止 apply 重试时重复执行
- 逐个 Action 重放，不是批量原子——因为 base 可能已被别人改，逐个 OCC 更现实
- 冲突不中断，收集所有结果返回，让用户决定强制/跳过

---

## 六、可靠性与故障恢复

### 6.1 故障场景与恢复

| 故障 | 影响 | 恢复 |
|------|------|------|
| **PG 宕机** | Scenario 读写全不可用 | PG 重启后自动恢复（数据已持久化）；main 路径也受影响（共享 PG） |
| **Action 写 overlay 后 PG crash（未 commit）** | overlay 未写入，Scenario 状态不变 | 事务原子性保证：未 commit 的写入丢失，Scenario 保持一致 |
| **Action 写 overlay 后 PG crash（已 commit，未返回响应）** | overlay 已写入，但客户端不知道 | 客户端用 idempotency_key 重试，`ON CONFLICT` 保证不重复写 |
| **apply_scenario 中途中断** | 部分 Action 已重放到 main，部分未执行 | idempotency_key 防重复；用户可重新 apply（已应用的 Action 幂等跳过） |
| **Scenario discard 时 crash** | overlay 部分删除 | CASCADE 删除是原子的（`DELETE FROM branches WHERE id=X` 级联删 object_state） |

### 6.2 一致性保证

**Scenario 内部一致性**（强）：
- 所有 overlay 写入在 PG 单事务内，原子提交
- read-your-writes：Action 提交后立即可见（同事务内读自己写，跨事务读已提交）
- 无最终一致性问题（不涉及 Doris/Iceberg 异步同步）

**Scenario vs base 一致性**（ eventual）：
- base 改了，Scenario 的 overlay 仍基于旧 base —— 这是**设计如此**（假设世界冻结在创建时刻）
- 但 Scenario 查询时 `COALESCE(s.properties, b.properties)` 会读到**当前 base** —— 这意味着 Scenario 内未改的属性会跟随 base 变化
- **这是合理且符合直觉的**：用户改了 F-001 的飞机，没改 F-001 的成本，那 Scenario 内 F-001 的成本显示当前 base 的成本
- **潜在陷阱**：若 base 的成本被别人改了，Scenario 内的成本会"跳变"。若要冻结，需在创建 Scenario 时快照 base——但这违背 overlay 轻量语义。MVP 接受"未改属性跟随 base"的行为，文档标注

### 6.3 与 ConflictDetector 的关系

现有 `ConflictDetector`（`services/conflict_detector.py`）审计 PG object_state vs Iceberg 的版本一致性。**Scenario 行不参与此审计**（它本就不该同步到 Iceberg）。

**改造**：`ConflictDetector.run_audit_once` 的 `get_object_states_by_type` 查询需加 `WHERE scenario_id IS NULL`，只审计 base 行。否则 Scenario overlay 行的 version 会与 Iceberg 不匹配（误报）。

### 6.4 outbox 在 Scenario 下的行为

- `WEBHOOK_WRITEBACK`：Scenario 禁用（D8 决策），不产生 outbox 记录
- `WEBHOOK_SIDE_EFFECT`：**Scenario 禁用**（延伸 D8）——side effect 也不应在假设世界触发
- `WRITE_BACK`（SQL 反写源系统）：**Scenario 禁用**
- `SUB_ACTION`/`KAFKA_TOPIC`：**Scenario 禁用**

**实现**：Step 9 创建 outbox 时，若 `ctx.scenario_id is not None`，跳过所有 effect 创建（或创建但标记 `status=SKIPPED`）。这保证 Scenario 内的 Action 不会有任何外部副作用。

### 6.5 数据增长与清理

- 废弃 Scenario：`discard` 删除 overlay（CASCADE），释放空间
- APPLIED Scenario：保留 overlay 一段时间（审计），定时任务清理 30 天前的 APPLIED Scenario
- 长期活跃 Scenario：限制 30000 edits，超限强制 freeze 或报错

---

## 七、性能边界与容量规划

### 7.1 性能基线（估算，需实测）

| 操作 | 数据量 | 预期延迟 | 瓶颈 |
|------|--------|---------|------|
| 单对象 overlay 写入 | - | < 2ms | 2 SELECT + 1 INSERT/UPDATE（索引查找） |
| 单对象 overlay 读取 | - | < 1ms | 复合 PK 索引 |
| 批量 filter（Scenario） | 10000 base + 50 overlay | < 20ms | base Index Scan + Hash Join |
| 批量 filter（无索引） | 10000 base | 50-100ms | Seq Scan + JSONB 解析 |
| 聚合 SUM（Scenario） | 10000 对象 | < 100ms | 全行扫描 + numeric 求和 |
| 多 Scenario 并排对比 | 2 Scenario × 50 overlay | < 10ms | 两个 LEFT JOIN |
| apply_scenario | 50 Action | 50 × < 5ms = < 250ms | 逐个 Action 重放 |

### 7.2 Palantir 限制的工程依据

| Palantir 限制 | Gaia 对应 | 工程依据 |
|--------------|----------|---------|
| 单 Scenario ≤30000 edits | 同 | PG 单表 30000 行 overlay，查询 < 500ms |
| 单 Scenario ≤50 Actions | 同 | 50 个 Action 串行执行 < 1s，apply < 250ms |
| Scenario 查询 ≤10000 对象 | 同 | PG 10000 行扫描 + JSONB 解析 < 100ms |
| 加载对象 ≤10000（`.all()`） | 同 | 避免内存爆 + 查询超时 |

### 7.3 容量规划

**单本体 Scenario 数量**：
- 每 Scenario 平均 50 overlay 行 × 100 个 Scenario = 5000 行
- base 行 10000 × 1（main）= 10000 行
- object_state 总 15000 行，PG 轻松

**多本体**：
- 10 本体 × 15000 = 150000 行，仍轻松
- GIN 索引大小：约表大小的 30%，150000 行 × 1KB/行 × 30% ≈ 45MB，可接受

**扩容信号**：
- 单 Scenario 查询 > 500ms → 考虑加表达式索引
- object_state > 100万行 → 考虑分区（按 ontology_id 或 scenario_id hash partition）

---

## 八、对底层存储和查询引擎的要求清单

### 8.1 PostgreSQL 要求（Scenario 唯一依赖引擎）

| # | 要求 | 用途 | 必须？ |
|---|------|------|:---:|
| 1 | **复合主键支持** `(rid, scenario_id)` | overlay 行与 base 行共存 | ✅ 必须 |
| 2 | **JSONB 类型 + `||` 合并操作符** | overlay 累积叠加 | ✅ 必须 |
| 3 | **JSONB GIN 索引**（`jsonb_path_ops`） | filter 性能（@> 操作符） | ✅ 必须 |
| 4 | **JSONB 表达式索引** `(properties->>'field')` | 高频 filter 字段加速 | 🟡 可选（按需） |
| 5 | **部分索引** `WHERE scenario_id IS [NOT] NULL` | 分离 base/overlay 索引，减小体积 | ✅ 必须 |
| 6 | **ON CONFLICT 子句** | OCC 的 CREATE 语义 | ✅ 必须 |
| 7 | **事务 + 行级锁** | 原子性 + 并发控制 | ✅ 必须 |
| 8 | **CASCADE 外键** | discard Scenario 自动清理 overlay | ✅ 必须 |
| 9 | **CTE（WITH）** | 复杂合并查询可读性 | 🟡 推荐（PG 12+） |
| 10 | **窗口函数**（可选） | cursor 分页优化 | 🟡 可选 |
| 11 | **PG 14+**（`||` JSONB 合并稳定） | JSONB 操作符成熟 | ✅ 必须（Gaia 用 PG 16，满足） |

### 8.2 Doris 要求（Scenario 不依赖，但 main 路径不变）

**无新增要求**。Doris 继续作为 main 的在线读主源，Scenario 不触达。现有 `execute_sql`/`load_by_filter`/`upsert` 接口不变。

### 8.3 Iceberg 要求（Scenario 不依赖，决策回放除外）

| # | 要求 | 用途 | 必须？ |
|---|------|------|:---:|
| 1 | **snapshot_id 时间旅行** `load_by_ids_as_of(snapshot_id)` | 决策回放（读决策时刻的数据） | 🟡 决策捕获用，Scenario 不用 |

### 8.4 Trino / Neo4j / Kafka 要求

**无新增要求**。Scenario 不触达这些引擎。

### 8.5 连接池与会话管理

Scenario 查询全走 PG，复用现有 `PostgresMetaStore` 的 AsyncSession。**注意**：
- Scenario 的批量查询（10000 对象）可能占用连接较久（100ms），需确保连接池够大（现有 `aiomysql` pool for Doris 是独立的，PG 用 SQLAlchemy async engine 自带池）
- 长查询不要在请求线程阻塞——现有 async 模式已保证

---

## 九、实现优先级（工程视角）

按"工程依赖 + 风险"排序：

| 优先级 | 工程项 | 风险 | 依据 |
|:---:|------|:---:|------|
| P0 | 复合主键 migration + 数据回填 | 🔴 高 | 破坏性变更，必须先做且充分测试 |
| P0 | JSONB GIN 索引 migration | 🟡 中 | 无 GIN 则 filter 全表扫，不可用 |
| P0 | `upsert_object_state_scenario`（含两层 OCC） | 🔴 高 | 写入正确性核心，累积语义易错 |
| P0 | `list_objects_with_overlay`（合并查询） | 🔴 高 | 读取正确性核心，LEFT JOIN + UNION |
| P1 | `_filter_dict_to_pg_jsonb`（filter 映射） | 🟡 中 | 复用现有操作符映射，新方言 |
| P1 | Scenario CRUD（create/freeze/discard） | 🟢 低 | 标准 CRUD |
| P1 | ActionService Step 0/8/11/12 改造 | 🟡 中 | 插入式改造，不重构 |
| P2 | `apply_scenario`（重放） | 🟡 中 | OCC 冲突处理 |
| P2 | 多 Scenario 并排对比查询 | 🟢 低 | 多 LEFT JOIN |
| P2 | ConflictDetector 加 `scenario_id IS NULL` 过滤 | 🟢 低 | 防误报 |
| P3 | 表达式索引 admin API | 🟢 低 | 性能优化，按需 |

---

## 十、参考

### Gaia 内部代码（实现参照）
- `src/ontology/layers/metadata/postgres_meta_store.py:1171` — `upsert_object_state`（OCC 原型）
- `src/ontology/layers/metadata/postgres_meta_store.py:2069` — `transaction()` 上下文器
- `src/ontology/services/action_service.py:385` — `execute_action` Step 1-12
- `src/ontology/services/object_query_service.py:488` — `_resolve_query_target`（路由）
- `src/ontology/services/conflict_detector.py:99` — 一致性审计（Scenario 需过滤）
- `src/ontology/layers/index/doris_index_store.py` — Doris 连接池（Scenario 不用，参照）

### Palantir 官方
- Object backend overview: https://palantir.com/docs/foundry/object-backend/overview/
- Object indexing: https://palantir.com/docs/foundry/object-indexing/overview/
- OSv2 breaking changes: https://palantir.com/docs/foundry/object-backend/object-storage-v2-breaking-changes/
- Scenario core concepts: https://palantir.com/docs/foundry/workshop/scenarios-concepts/

### PostgreSQL JSONB 索引
- GIN indexes: https://www.postgresql.org/docs/18/gin.html
- JSONB indexing patterns: https://www.crunchydata.com/blog/indexing-jsonb-in-postgres
- jsonb_path_ops vs jsonb_ops: https://dev.to/polliog/postgresql-jsonb-gin-indexes-why-your-queries-are-slow-and-how-to-fix-them-12a0
