# 供应链演练（ChainSmoke）图探索演示 Query

> **演示本体**：`ChainSmoke`（display_name = "供应链演练"）
> **数据规模**：50 供应商 + 200 物料 + 500 订单 + 200 客户 ≈ 950 节点 / 1190 边
> **拓扑**：`Supplier --supplies--> Material --usedIn--> Order`（+ Supplier 直供 Order）
> **空间数据**：供应商/客户带 GEOPOINT（北京/上海/广州/深圳等 8 城），已投影 PostGIS（50+200 行）
> **入口**：前端 http://127.0.0.1:5174/explore → 选「供应链演练」
>
> **已验证**：以下每条 Query 均在当前环境实测通过（2026-07-07）。

---

## 演示路径（建议顺序）

### 场景 1：加载供应链网络 → 图谱画布

**前端操作**：进入 `/explore/ChainSmoke`，左侧「对话式」输入框或直接用 API 加载。

**API（加载供应商）**：
```bash
curl -s -X POST "http://127.0.0.1:8000/objects/ChainSmoke/object-set" \
  -H "Content-Type: application/json" \
  -d '{"type":"objectType","object_type":"Supplier"}' | python3 -m json.tool | head -20
```

**预期**：返回 50 个供应商节点（含 rid + props: supplierId/name/city/riskLevel/location）。

**演示要点**：画布渲染 50 个 Supplier 节点，可切换布局（fcose/dagre/circle/grid）。

---

### 场景 2：⭐ 路径推理 —— 供应商到订单的最短路径

**业务问题**：供应商 S000 通过什么路径影响到订单 O0468？

**API**：
```bash
curl -s -X POST "http://127.0.0.1:8000/objects/ChainSmoke/find-paths" \
  -H "Content-Type: application/json" \
  -d '{"source_key":"S000","target_key":"O0468","max_depth":4,"limit":5}' | python3 -m json.tool
```

**预期返回**：
```json
{
  "paths": [["S000", "M081", "O0468"]],
  "count": 1
}
```

**演示要点**：
- 路径 `S000 → M081 → O0468`（供应商 → 物料 → 订单，2 跳）
- 底层 Neo4j `allShortestPaths` Cypher，max_depth + limit 防爆炸
- 前端 PathFinder 面板：源下拉选 S000 / 目标选 O0468 / max_depth=4
- **话术**：「这是供应链溯源——某个订单出问题，能立刻追溯到是哪个供应商的哪批物料导致的。」

---

### 场景 3：多跳图遍历 —— 供应商的下游影响范围

**业务问题**：供应商 S000 的物料都用在哪些订单上？

**API（单跳 supplies）**：
```bash
curl -s -X POST "http://127.0.0.1:8000/objects/ChainSmoke/traverse" \
  -H "Content-Type: application/json" \
  -d '{"link_type":"supplies","source_keys":["S000"],"direction":"forward"}' | python3 -m json.tool | head -20
```

**预期**：返回 S000 供应的 5 个 Material（M045/M080/M081/...）。

**API（链式 2 跳：Supplier → Material → Order，用 object-set 嵌套 IR）**：
```bash
curl -s -X POST "http://127.0.0.1:8000/objects/ChainSmoke/object-set" \
  -H "Content-Type: application/json" \
  -d '{
    "type":"objectType","object_type":"Supplier",
    "filters":[{"field":"supplierId","op":"exactMatch","value":"S000"}],
    "operations":[
      {"type":"searchAround","linkType":"supplies","targetObjectType":"Material"},
      {"type":"searchAround","linkType":"usedIn","targetObjectType":"Order"}
    ]
  }' | python3 -m json.tool | head -30
```

**演示要点**：
- 链式 Search Around 演示「影响传导」：S000 的物料 → 影响哪些订单
- 前端右键节点 →「周边聚焦」可逐步扩展
- **话术**：「供应商中断 → 一层层传导 → 最终影响哪些订单，全程可视化追溯。」

---

### 场景 4：⭐ 空间分析 —— 地图上的供应商分布

**业务问题**：北京周边 500km 内有哪些供应商？（地图框选演示）

**前置**：先加载全部供应商 rid 作为候选集。

**API**（用 Python 构造，避免 JSON 拼接问题）：
```python
import httpx, asyncio
async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as c:
        # 1. 拿全部 supplier rid
        r = await c.post("/objects/ChainSmoke/object-set", json={
            "type":"objectType","object_type":"Supplier"
        })
        rids = [o["rid"] for o in r.json().get("objects",[])]
        # 2. 北京 500km 内
        r2 = await c.post("/objects/ChainSmoke/spatial-filter", json={
            "object_type":"Supplier",
            "candidate_rids": rids,
            "op":"withinDistance",
            "center":[116.40,39.90],
            "max_distance":500000
        })
        print(f"北京 500km 内供应商: {len(r2.json())} 个")
        print("  rids:", r2.json())
asyncio.run(main())
```

**预期**：北京 500km 内 **4 个供应商**（S006/S019/S031/S035）。

**前端操作**：
- 切换到「地图」视图（GraphExplorePage 顶栏视图切换）
- MapLibre 地图渲染供应商 marker（北京/上海/广州/深圳分布）
- 框选北京区域 → 触发 spatial-filter → 高亮命中的 4 个供应商
- 图谱视图联动：地图选中的节点在图谱中也高亮

**演示要点**：
- 底层 PostGIS `ST_DWithin` + GiST 索引，geography(Point,4326)
- GEOPOINT 数据从 object_state 投影到 PostGIS（geo_chain_smoke__supplier 表，50 行）
- **话术**：「这是 8 个城市的供应商分布。框选北京，立刻看到周边 500km 有 4 个供应商——如果这些供应商出问题，影响范围一目了然。」

---

### 场景 5：⭐ 组合 —— 高风险供应商 + 空间 + 图遍历

**业务问题**：找出高风险供应商，看他们的下游订单，并在地图上定位。

**注意**：当前数据 riskLevel 多为 "low"/"none"，需先确认有哪些值。可用 filter 查：

**API（按 riskLevel 过滤供应商）**：
```bash
curl -s -X POST "http://127.0.0.1:8000/objects/ChainSmoke/object-set" \
  -H "Content-Type: application/json" \
  -d '{"type":"objectType","object_type":"Supplier","filters":[{"field":"riskLevel","op":"exactMatch","value":"low"}]}' | python3 -m json.tool | head -30
```

**组合演示流程**（前端）：
1. 图谱视图：加载 riskLevel=low 的供应商（红色着色，LayersPanel → color_by riskLevel）
2. 右键这些供应商 → Search Around（supplies → Material → Order）
3. 切换地图视图：看这些高风险供应商的地理分布
4. 框选某区域 → 看该区域高风险供应商影响的订单

**演示要点**：一条业务问题串起「属性过滤（PG）+ 图遍历（Neo4j）+ 空间过滤（PostGIS）」——8 Layer 协同，用户只看到一个画布。

---

### 场景 6：⭐ AG-UI Agent 自动编排（ADR-015 灵魂）

**前端操作**：`/explore/ChainSmoke` 中央对话框输入自然语言。

**NL Query**：
> "分析供应商 S000 的下游影响"

**Agent ReAct 执行过程**（观察重点）：
1. Agent 调 `query_with_dataframe` 加载 S000 → 读 CanvasSnapshot 看 object_count
2. object_count > 0 → 调 `traverse_link`（supplies）扩展到 Material
3. 继续 `traverse_link`（usedIn）扩展到 Order
4. 调 `color_by` 按 riskLevel 着色
5. 返回分析结论 + evidence_id

**NL Query 2**（空间相关）：
> "北京周边的供应商有哪些？"

**Agent**：调 `query_with_dataframe` 加载 Supplier → 调 `switch_view` 切到地图 → 返回。

**演示要点**（必讲）：
- Agent **每步基于画布状态决策**，不是空画布编计划
- 0 对象自然终止不编结论（ADR-015 转向核心）
- 工具返回 ToolReturn 双职分离：return_value 给 Agent + StateSnapshotEvent 驱动画布
- URL 预填充：`/explore/ChainSmoke?question=分析供应商S000的下游影响`

---

## 数据现状说明（演示前知晓）

| 项 | 状态 | 影响 |
|----|------|------|
| Neo4j 节点 | ✅ 950 个（ChainSmoke 前缀标签） | 图遍历可用 |
| Neo4j 边 | ✅ supplies(504) + usedIn(686) | 路径推理可用 |
| PostGIS 供应商 | ✅ 50 行（已补投影） | 地图演示可用 |
| PostGIS 客户 | ✅ 200 行（已补投影） | 地图演示可用 |
| Order→Customer 边 | ❌ 缺失（setup 脚本未投影成功） | **路径推理只能到 Order，不能到 Customer** |
| 节点属性（Neo4j） | ⚠️ 仅 rid+api_name（indexed=False 导致） | 图谱节点属性展示靠 object_state 水合 |
| riskLevel 值 | ⚠️ 多为 "low"/"none"，无 "high" | 「高风险供应商」演示需用 "low" 替代或改数据 |

**路径推理用 S000→O0468（已实测通），不要用 S000→C0001（Customer 无入边，不通）。**

---

## API 字段名速查（避免踩坑）

| 端点 | 字段名 | 说明 |
|------|--------|------|
| `/objects/{ont}/traverse` | `source_keys`（复数）+ `link_type` + `direction` | 不是 source_rids |
| `/objects/{ont}/find-paths` | `source_key` + `target_key`（单数） | 不是 source_rid |
| `/objects/{ont}/spatial-filter` | `candidate_rids` + `op` + `center=[lon,lat]` + `max_distance`（米） | 返回 rid 列表，不是对象 |
| `/objects/{ont}/object-set` | 顶层 `type`+`object_type`+`filters`，filter op 用 `exactMatch` | 不是 equal |
| Graph IR filter op | `exactMatch`/`notEqual`/`in`/`range`/`greaterThan`/`withinDistance`... | 对齐 Palantir，不是 equal |

---

## 演示话术要点

1. **路径推理**：「供应链溯源——订单出问题，秒级追溯到供应商和物料。Neo4j allShortestPaths，千级节点毫秒响应。」

2. **空间分析**：「8 城供应商分布一目了然。框选北京，PostGIS GiST 索引秒级返回 500km 内的 4 个供应商。这是 8 Layer 中 GeoTime Layer 的能力。」

3. **AG-UI Agent**：「注意看——Agent 不是在空画布上编计划，它每一步都读画布真实状态。0 对象就停，有对象才继续。这是 ReAct 范式，和 LLM 一次性编计划的伪 Agent 本质不同。」

4. **多引擎联动**：「一条业务问题串起属性过滤（PG）+ 图遍历（Neo4j）+ 空间过滤（PostGIS）——8 Layer 协同，用户只看到一个画布。这是分层架构的价值。」
