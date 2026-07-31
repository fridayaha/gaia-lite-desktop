# Gaia 演示 Query 清单（基于真实数据集）

> 本文档的所有 Query 均基于项目内真实存在的三个数据集，字段名、Link 拓扑、Action 定义全部来自实际 JSON / 脚本，可直接复制执行。
>
> **数据集来源**：
> - **Marketing**（汽车门店营销）：`tests/benchmark/marketing/data/ontology/marketing-ontology.json` — 39 ObjectType / 9 ActionType / 全 MANAGED
> - **DVP**（整车研发试验验证）：`tests/benchmark/dvp/data/ontology/dvp-ontology.json` — 24 ObjectType / 34 LinkType / 全 VIRTUAL（MySQL→Trino 联邦，80010 行）
> - **ChainSmoke**（供应链图探索）：`scripts/setup_explore_demo_large.py` — 4 ObjectType / ~950 节点 / ~1800 边 / 含 GEOPOINT + 时序
>
> **启动前置**：
> ```bash
> docker compose up -d --profile graph   # 含 Neo4j
> bash scripts/dev.sh
> python scripts/setup_explore_demo_large.py   # 灌入 ChainSmoke 图数据
> # Marketing / DVP 本体通过 benchmark setup 灌入，见 tests/benchmark/{marketing,dvp}/scripts/
> ```

---

## 数据集速查（Query 必备字段）

### Marketing（营销闭环，演示 Action 主力）

**核心 ObjectType**：
| OT | 主键 | 关键属性 | 角色 |
|----|------|---------|------|
| `Lead` 线索 | 线索ID | 32 属性（含状态/意向度/来源） | 营销链路中枢，4 条出边 |
| `User` 用户 | 用户ID | 客户名称 | 被线索/外呼/试驾/接待引用 |
| `SalesConsultant` 销售顾问 | 销售顾问ID | 销售顾问名称 | 跟进/分配/试驾销售 |
| `TestDrive` 试驾 | 试驾ID | orderStatus（0-4 状态机） | 7 条出边（销售/录音/用户/线索/路线/车/门店）|
| `LeadAllocateRecord` 线索分配记录 | 线索分配ID | operationType（2分配/3转移/4回收）| Action 写入目标 |
| `LeadFollowRecord` 线索跟进记录 | 跟进ID | followPurpose/followResult | Action 写入目标 |
| `ManualOutboundCall` 人工外呼 | 人工外呼ID | callStatus/callDuration | Action 写入目标 |

**Link 拓扑**（Lead 为例）：
```
Lead --hasLeadSource-->        LeadSource
Lead --belongsToUser-->        User
Lead --focusesOnVehicleModel--> VehicleModel
Lead --focusesOnVehicleSeries--> VehicleSeries
```

**9 个 ActionType**：`allocateLead` / `transferLead` / `reclaimLead` / `recordFollow` / `progressTestDrive` / `logManualCall` / `analyzeTestDrive` / `generateUserProfile` / `reassignTestDriveCar`

### DVP（试验验证联邦查询，演示图遍历主力）

**核心链路**（零部件 → 变化点 → 试验工况 → 试验项 → 规范）：
```
Component --hasChangePoint--> ChangePointEntity --triggersCondition--> OperCondition
OperCondition --hasFrontDetail--> FrontCollision --frontContainsTestItem--> TestItem --referencesSpec--> Spec
```

**项目链路**：
```
ProjectBase --containsVehicle--> ProjectVehicle --containsBody--> VehicleBody
VehicleBody --containsFront--> FrontStructure --frontContainsComponent--> Component
DvpDesign --splitsIntoRound--> ExperimentItemRound --schedulesTestItem--> TestItem
```

### ChainSmoke（供应链图探索，演示 ADR-015 旗舰）

**4 OT + Link 拓扑**：
```
Supplier --supplies--> Material --usedIn--> Order --orderedBy--> Customer
```
- 每个节点带 `riskLevel`（low/medium/high）、`location`（GEOPOINT 北京/上海/广州 8 城）、`createdAt`（时序）
- 规模：50 Supplier + 200 Material + 500 Order + 200 Customer ≈ 950 节点 / 1800 边

---

# 一、Action Query 清单（9 条，覆盖 9 种 ActionType）

> 全部基于 Marketing 本体。所有 Action 走 `POST /actions/execute`，低风险（risk_level=low）默认不审批，中高危走 HITL。
> **演示要点**：每个 Action 执行后立即用「读即所见」Query 验证 `object_state` 已写入。

### A1. 分配线索（allocateLead）— CreateObject + 系统生成主键

**业务场景**：新进线索分配给销售顾问跟进。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "allocateLead",
    "ontology": "Marketing",
    "parameters": {
      "leadId": "L-000001",
      "salesConsultantId": "SC-000001"
    }
  }'
```

**演示要点**：
- `oid` 主键 `source: SYSTEM_GENERATED, value: uuid` → 系统自动生成，无需前端传
- `operationType` `source: STATIC_VALUE, value: "2"` → 声明式规则固化
- `operationTime` `source: SYSTEM_CONTEXT, CURRENT_TIMESTAMP` → 系统上下文注入
- 执行后立即查证：`POST /objects/Marketing/load` → LeadAllocateRecord 出现 operationType=2 记录

---

### A2. 转移线索（transferLead）— 多参数 + ObjectReference

**业务场景**：销售 A 把线索转给销售 B。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "transferLead",
    "ontology": "Marketing",
    "parameters": {
      "leadId": "L-000001",
      "fromSalesConsultantId": "SC-000001",
      "toSalesConsultantId": "SC-000002"
    }
  }'
```

**演示要点**：
- 3 个参数全部是 ObjectReference（`object_type_ref` 指向 Lead / SalesConsultant / SalesConsultant）
- `operationType: "3"` 写入 LeadAllocateRecord
- 配合 A1 演示「同一目标表，不同 operationType 区分语义」

---

### A3. 推进试驾状态（progressTestDrive）— ModifyObject + enum 校验 + write_back

**业务场景**：试驾单状态机推进（待排程→待签署→待开始→进行中→已结束）。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "progressTestDrive",
    "ontology": "Marketing",
    "parameters": {
      "testDriveId": "TD-000001",
      "newStatus": "3"
    }
  }'
```

**演示要点**（最值得讲的 Action）：
- **ModifyObject**（非 CreateObject）→ 修改既有 TestDrive 的 `orderStatus`
- **enum 约束**：`rules: [{expression: "value in ['0','1','2','3','4']"}]` → 传 `"9"` 会被 ActionValidator 拒绝
- **write_back effect**：`effects: [{type: write_back, op: upsert}]` → OutboxExecutor 异步回写 Iceberg
- **错误演示**（故意传非法值）：
  ```bash
  # 传 "9" → 应返回 ValidationError，演示护栏
  "newStatus": "9"
  ```

---

### A4. 记录线索跟进（recordFollow）— 6 参数混合类型

**业务场景**：销售记录一次跟进（目的/结果/内容/下次时间）。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "recordFollow",
    "ontology": "Marketing",
    "parameters": {
      "leadId": "L-000001",
      "followerId": "SC-000001",
      "followPurpose": "确认试驾意向",
      "followResult": "用户同意本周末试驾",
      "followContent": "客户对混动版感兴趣，关注油耗",
      "nextFollowTime": "2026-07-12T10:00:00"
    }
  }'
```

**演示要点**：
- 参数类型混合：STRING × 4 + TIMESTAMP × 1 + ObjectReference × 2
- `createTime: SYSTEM_CONTEXT` 自动注入
- 执行后查 LeadFollowRecord，配合 A1/A2 演示「线索生命周期完整留痕」

---

### A5. 记录人工外呼（logManualCall）— 可选参数 + 录音关联

**业务场景**：销售完成一次人工外呼，记录呼叫状态/时长/录音。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "logManualCall",
    "ontology": "Marketing",
    "parameters": {
      "leadId": "L-000001",
      "userId": "U-000001",
      "callStatus": "接通",
      "callDuration": 187,
      "recordingUrl": "s3://rustfs/recordings/call-20260707-001.wav"
    }
  }'
```

**演示要点**：
- `callDuration` 是 INTEGER（演示类型校验）
- `recordingUrl` 指向 RustFS S3（演示对象存储集成）
- ManualOutboundCall 有 3 条出边（外呼线索/外呼用户/外呼录音）→ 后续可图遍历

---

### A6. 更换试驾车（reassignTestDriveCar）— ModifyObject + ObjectReference 比较

**业务场景**：试驾车故障，临时换一辆。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "reassignTestDriveCar",
    "ontology": "Marketing",
    "parameters": {
      "testDriveId": "TD-000001",
      "newTestDriveCarId": "TDC-000002"
    }
  }'
```

**演示要点**：
- 约束规则 `value != '' and len(value) > 0`（非空校验）
- ModifyObject 修改 TestDrive.testDriveCarId
- 两个参数都是 ObjectReference，演示 P1 的参数级权限可校验「该销售是否有权操作这辆试驾车」

---

### A7. 回收线索（reclaimLead）— 单参数 CreateObject

**业务场景**：销售离职或线索失效，回收线索。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "reclaimLead",
    "ontology": "Marketing",
    "parameters": {
      "leadId": "L-000001"
    }
  }'
```

**演示要点**：单参数 + operationType=4，配合 A1/A2 演示「分配/转移/回收」三态闭环。

---

### A8. 生成试驾报告（analyzeTestDrive）— AI 产物触发

**业务场景**：试驾结束，触发 AI 生成 5 张试驾报告表。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "analyzeTestDrive",
    "ontology": "Marketing",
    "parameters": {
      "testDriveId": "TD-000001"
    }
  }'
```

**演示要点**：
- `ontology_rules: []`（空）→ 这是 AI 产物类 Action，由 AI 推导生成 TdAnalysisDetails / CompetitiveAnalysis / StrategyExecutionAudit / ScriptExecutionAnalysis / FocusResistancePoints 5 张表
- **诚实边界**：需配置 `AI_MODEL` + provider key，未配置时返回 SKIP（不影响其他 Action 演示）

---

### A9. 生成用户画像（generateUserProfile）— AI 产物 8 表

**业务场景**：为用户触发 AI 生成 8 张画像表（基础/概览/情绪/标签/用车场景/购车动机/产品偏好/抗性）。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "generateUserProfile",
    "ontology": "Marketing",
    "parameters": {
      "userId": "U-000001"
    }
  }'
```

---

### A10. ⭐ 批量分配线索（P2 Batch Action）— 杀手锏

**业务场景**：批量把 1000 条新线索分配给销售团队（演示 P2 分片调度）。

```bash
curl -X POST http://127.0.0.1:8000/actions/execute-batch \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "allocateLead",
    "ontology": "Marketing",
    "default_parameters": {
      "salesConsultantId": "SC-000001"
    },
    "items": [
      {"parameters": {"leadId": "L-001001"}},
      {"parameters": {"leadId": "L-001002"}},
      {"parameters": {"leadId": "L-001003", "salesConsultantId": "SC-000002"}},
      {"parameters": {"leadId": "L-001004"}},
      {"parameters": {"leadId": "L-001005"}}
    ],
    "shard_size": 100,
    "fail_fast": false
  }'
```

**演示要点**（P2 核心能力）：
- `default_parameters` 共享默认值，`items` 内参数胜出（L-001003 改派给 SC-000002）
- 分片调度：shard_size 默认 100 / 最大 1000 / 上限 10000 项
- **逐项原子事务**：单项失败不中断整批 → 返回 partial 结果（每项 ItemResult 含成功/失败 + 错误原因）
- **派生幂等键**：`batch_key#index` → 重试安全
- **fail_fast 选项**：true 则遇错即停，false 则跑完全部再汇总

**返回结构**（重点讲）：
```json
{
  "total": 5, "succeeded": 4, "failed": 1,
  "items": [
    {"index": 0, "status": "success", "rid": "..."},
    {"index": 2, "status": "failed", "error": "leadId not found"}
  ]
}
```

---

# 二、图关联分析 Query 清单（ADR-015 旗舰）

> 分两组：**ChainSmoke**（供应链图探索，演示 ADR-015 全套）+ **DVP**（试验验证多跳遍历，演示联邦查询）。
> 三种入口：REST 直调 / AG-UI Agent NL / MCP 工具。

## 2.1 ChainSmoke 供应链图探索（10 条）

### G1. Search Around 单跳：找某供应商的所有订单

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {
      "type": "objectType",
      "ontology": "ChainSmoke",
      "objectType": "Supplier"
    },
    "operations": [
      {
        "type": "searchAround",
        "linkType": "supplies",
        "direction": "OUTGOING",
        "targetObjectType": "Order"
      }
    ]
  }'
```

**AG-UI NL**（`/ai/agent`）：
> "找出供应商 S001 供应的所有订单"

**演示要点**：底层 Neo4j `MATCH (s:Supplier {supplierId:'S001'})-[:supplies]->(o:Order) RETURN o`，Neo4j 不可用时降级 PG `object_links`。

---

### G2. 多步 Search Around：供应商 → 物料 → 订单 → 客户（链式影响传导）

**REST**（嵌套 IR）：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {
      "type": "objectType",
      "ontology": "ChainSmoke",
      "objectType": "Supplier"
    },
    "operations": [
      {"type": "searchAround", "linkType": "supplies", "targetObjectType": "Material"},
      {"type": "searchAround", "linkType": "usedIn", "targetObjectType": "Order"},
      {"type": "searchAround", "linkType": "orderedBy", "targetObjectType": "Customer"}
    ]
  }'
```

**AG-UI NL**：
> "分析供应商 S001 的下游影响，最终影响到哪些客户？"

**演示要点**：
- 3 跳链式 Search Around，演示「影响传导」（供应商中断 → 哪些客户受影响）
- useSearchAroundConfig 链式嵌套 IR 构建 + 预览防星爆
- 这是 ADR-015 Phase 2f 的核心能力

---

### G3. ⭐ 路径推理：找供应商到客户的最短路径

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/find-paths \
  -H "Content-Type: application/json" \
  -d '{
    "source_rid": "S001",
    "target_rid": "C0001",
    "max_depth": 4,
    "limit": 10
  }'
```

**前端**：PathFinder 面板，源下拉选 S001 / 目标下拉选 C0001 / max_depth=4

**AG-UI NL**：
> "供应商 S001 和客户 C0001 之间有什么关联路径？"

**演示要点**：
- 底层 Neo4j `allShortestPaths` Cypher（max_depth + limit 防爆炸）
- 第 22 个工具 `find_paths`
- 返回路径序列：`S001 -[:supplies]-> M001 -[:usedIn]-> O001 -[:orderedBy]-> C0001`

---

### G4. 关系存在性：某供应商是否间接供应某客户

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/exists-link \
  -H "Content-Type: application/json" \
  -d '{
    "source_rid": "S001",
    "target_rid": "C0001",
    "mode": "ANY"
  }'
```

**演示要点**：
- ANY 模式：任意路径存在即 true
- SINGLE_TARGET 模式：精确到目标对象
- 比 G3 轻，适合快速判定

---

### G5. 过滤 + 图遍历：高风险供应商的下游订单

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {
      "type": "objectType",
      "ontology": "ChainSmoke",
      "objectType": "Supplier"
    },
    "operations": [
      {"type": "filter", "where": {"op": "equal", "field": "riskLevel", "value": "high"}},
      {"type": "searchAround", "linkType": "supplies", "targetObjectType": "Material"},
      {"type": "searchAround", "linkType": "usedIn", "targetObjectType": "Order"}
    ]
  }'
```

**AG-UI NL**：
> "高风险供应商影响的订单有哪些？"

**演示要点**：
- filter 分流：属性过滤走 PG，图遍历走 Neo4j，一条 IR 串起来
- EvidenceChain 证据累积（每个中间结果集都有 evidence）

---

### G6. 空间过滤：北京周边 500km 内的供应商

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/spatial-filter \
  -H "Content-Type: application/json" \
  -d '{
    "objectType": "Supplier",
    "center": {"lon": 116.40, "lat": 39.90},
    "radius_km": 500,
    "field": "location"
  }'
```

**前端**：MapLibre 地图，北京中心点框选

**演示要点**：
- 底层 PostGIS `ST_DWithin` + GiST 索引
- GEOPOINT 数据自动投影到 PostGIS（GeoTimeProjector）
- 演示 8 Layer 中 GeoTime Layer 的能力

---

### G7. 时序查询：最近 24h 新增的订单

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/series-query \
  -H "Content-Type: application/json" \
  -d '{
    "objectType": "Order",
    "field": "createdAt",
    "timeRange": {"from": "now-24h", "to": "now"},
    "interval": "1h"
  }'
```

**前端**：TimeScrubber 双滑块 + 预设 24h

**演示要点**：
- 底层 TimescaleDB 超表 + time_bucket
- 演示时空多维分析（空间 + 时序 + 图三者联动）

---

### G8. 集合运算：高风险供应商 ∩ 北京周边供应商

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {
      "type": "intersect",
      "left": {
        "type": "filter",
        "objectSet": {"type": "objectType", "ontology": "ChainSmoke", "objectType": "Supplier"},
        "where": {"op": "equal", "field": "riskLevel", "value": "high"}
      },
      "right": {
        "type": "filter",
        "objectSet": {"type": "objectType", "ontology": "ChainSmoke", "objectType": "Supplier"},
        "where": {"op": "within", "field": "location", "center": {"lon": 116.40, "lat": 39.90}, "radius_km": 500}
      }
    }
  }'
```

**演示要点**：
- 对齐 Palantir ObjectSet `intersect` type
- Ibis union/intersect/difference 下推
- 「既高风险又在北京周边」→ 优先风控

---

### G9. 聚合：各风险等级的供应商数量

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/ChainSmoke/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type": "objectType", "ontology": "ChainSmoke", "objectType": "Supplier"},
    "operations": [
      {"type": "aggregate", "groupBy": ["riskLevel"], "aggregations": [{"op": "count"}]}
    ]
  }'
```

**前端**：HistogramPanel 属性分布筛选

**演示要点**：对齐 Palantir `aggregate` type，group_by + count/sum/avg/min/max。

---

### G10. ⭐ AG-UI ReAct Agent 自动编排（ADR-015 灵魂，最重要）

**入口**：前端 `/explore` → 中央对话框输入

**NL（场景卡片其一）**：
> "分析供应链中断风险"

**演示要点**（必讲）：
- Agent ReAct 多步执行（不是一次性编计划）：
  1. 调 `query_with_dataframe` 加载 Supplier → 读 CanvasSnapshot 看 object_count
  2. object_count > 0 → 调 `traverse_link`（supplies）扩展到 Material/Order
  3. 调 `color_by` 按 riskLevel 着色（高风险红）
  4. 调 `switch_view` 切到图谱视图
  5. 返回分析结论 + evidence_id
- **关键修复点**：如果第 1 步返回 0 对象，Agent **自然终止不编结论**（这是 ADR-015 从 explore-plan 转向 ReAct 的核心原因）
- 工具返回 ToolReturn 双职分离：return_value 给 Agent 推理 + StateSnapshotEvent 驱动画布
- URL 预填充：`/explore/ChainSmoke?question=分析供应链中断风险`

**话术**：「注意看——Agent 不是在空画布上瞎编 5 步计划，它每一步都读画布真实状态。0 个对象就停，有对象才继续。这就是 ReAct 范式，和 LLM 一次性编计划的伪 Agent 本质不同。」

---

## 2.2 DVP 试验验证多跳遍历（5 条，演示联邦查询）

> DVP 全 VIRTUAL，走 Trino 跨 catalog 联邦 JOIN MySQL（80010 行，21 表）。演示「虚拟对象也能图遍历」。

### G11. 零部件 → 变化点 → 试验工况（3 跳影响分析）

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/DVP/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type": "objectType", "ontology": "DVP", "objectType": "Component"},
    "operations": [
      {"type": "searchAround", "linkType": "hasChangePoint", "targetObjectType": "ChangePointEntity"},
      {"type": "searchAround", "linkType": "triggersCondition", "targetObjectType": "OperCondition"}
    ]
  }'
```

**AG-UI NL**：
> "零部件 C001 的变更会触发哪些试验工况？"

**演示要点**：VIRTUAL 多跳遍历走 Trino 联邦 JOIN，不走 Neo4j（VIRTUAL 不投影图）。

---

### G12. 项目 → 车辆 → 车身 → 前部结构 → 零部件（5 跳结构分解）

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/DVP/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type": "objectType", "ontology": "DVP", "objectType": "ProjectBase"},
    "operations": [
      {"type": "searchAround", "linkType": "containsVehicle", "targetObjectType": "ProjectVehicle"},
      {"type": "searchAround", "linkType": "containsBody", "targetObjectType": "VehicleBody"},
      {"type": "searchAround", "linkType": "containsFront", "targetObjectType": "FrontStructure"},
      {"type": "searchAround", "linkType": "frontContainsComponent", "targetObjectType": "Component"}
    ]
  }'
```

**AG-UI NL**：
> "项目 P001 包含哪些零部件？"

**演示要点**：5 跳结构树分解，演示深层遍历 + Trino JOIN 性能。

---

### G13. DVP 计划 → 轮次 → 试验项 → 规范（试验计划展开）

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/DVP/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type": "objectType", "ontology": "DVP", "objectType": "DvpDesign"},
    "operations": [
      {"type": "searchAround", "linkType": "splitsIntoRound", "targetObjectType": "ExperimentItemRound"},
      {"type": "searchAround", "linkType": "schedulesTestItem", "targetObjectType": "TestItem"},
      {"type": "searchAround", "linkType": "referencesSpec", "targetObjectType": "Spec"}
    ]
  }'
```

**AG-UI NL**：
> "DVP 计划 DVP001 展开到试验项和规范"

---

### G14. 试验项 → 验证目标维度 → 项目目标（反向追溯）

**REST**：
```bash
curl -X POST http://127.0.0.1:8000/objects/DVP/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type": "objectType", "ontology": "DVP", "objectType": "TestItem"},
    "operations": [
      {"type": "searchAround", "linkType": "verifiesTarget", "targetObjectType": "LmsTargetDimension"},
      {"type": "searchAround", "linkType": "aggregatesTo", "targetObjectType": "ProjectTarget"}
    ]
  }'
```

**AG-UI NL**：
> "试验项 TI-000001 验证的是哪个项目目标？"

**演示要点**：反向追溯（试验项 → 目标），演示图遍历双向能力。

---

### G15. TextQL 联邦 JOIN：销售外呼统计（4 表 JOIN）

**入口**：`/ai/agent` NL → Agent 调 `query_with_sql` 工具

**NL**：
> "统计每个销售顾问的人工外呼数量和平均通话时长"

**底层 SQL**（SqlGlot 编译，跨 catalog 联邦）：
```sql
SELECT sc.销售顾问名称 AS salesConsultantName,
       COUNT(*) AS callCount,
       AVG(m.callDuration) AS avgDuration
FROM iceberg.marketing.manual_outbound_call m
JOIN iceberg.marketing.sales_consultant sc ON m.followerId = sc.销售顾问ID
GROUP BY sc.销售顾问名称
ORDER BY callCount DESC
```

**演示要点**：
- 已 live 验证：4 表 JOIN（销售外呼统计）真实跑通（implementation-status §TextQL）
- SqlGlot 编译 + `SELECT *` 多表消歧 + 方言感知物理名
- **诚实边界**：benchmark 显示多跳 JOIN 的 NL→SQL 准确率仍低，演示用这个已验证用例，不要现场换新问法

---

# 三、组合演示：Action + 图分析闭环（3 条，杀手锏）

> 把 Action 写入和图分析串起来，演示「写 → 投影 → 图遍历」完整闭环。这是 A2 优势的最佳体现。

### C1. 分配线索 → 图遍历查看销售的所有线索

```bash
# Step 1: 执行 Action（写 object_state + 投影 Neo4j）
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{"action_type":"allocateLead","ontology":"Marketing",
       "parameters":{"leadId":"L-001001","salesConsultantId":"SC-000001"}}'

# Step 2: 图遍历——该销售的所有线索（Action 投影后立即可见图）
curl -X POST http://127.0.0.1:8000/objects/Marketing/object-set \
  -H "Content-Type: application/json" \
  -d '{
    "objectSet": {"type":"objectType","ontology":"Marketing","objectType":"SalesConsultant"},
    "operations": [
      {"type":"searchAround","linkType":"followedBySalesConsultant","targetObjectType":"LeadFollowRecord"},
      {"type":"searchAround","linkType":"followsUpLead","targetObjectType":"Lead"}
    ]
  }'
```

**演示要点**：
- OutboxExecutor INDEX effect 侧调 graph_projector.project_object（CREATE 投影，capabilities 门控，fail-tolerant）
- ActionService Step 11 commit 后 RELATE→project_link（边投影，capabilities 门控）
- 写入后 Neo4j 立即有节点/边，图遍历「读即所见」（需 ObjectType 启用 graph_indexing_enabled）
- 演示 Action 写 PG → outbox 消费 → 投影 Neo4j → 图查询的完整链路

---

### C2. 推进试驾状态 → 时序回放试驾轨迹

```bash
# Step 1: 推进试驾状态（待开始 → 进行中）
curl -X POST http://127.0.0.1:8000/actions/execute \
  -H "Content-Type: application/json" \
  -d '{"action_type":"progressTestDrive","ontology":"Marketing",
       "parameters":{"testDriveId":"TD-000001","newStatus":"3"}}'

# Step 2: 时序查询该试驾的状态变更历史
curl -X POST http://127.0.0.1:8000/objects/Marketing/series-query \
  -H "Content-Type: application/json" \
  -d '{"objectType":"TestDrive","field":"orderStatus",
       "timeRange":{"from":"now-7d","to":"now"},"interval":"1h"}'
```

**演示要点**：Action 写 + 时序回放，演示时空 + Action 联动。

---

### C3. ⭐ 批量分配 + 图分析：1000 条线索分配后的销售负载分析

```bash
# Step 1: 批量分配（P2 Batch Action）
curl -X POST http://127.0.0.1:8000/actions/execute-batch \
  -H "Content-Type: application/json" \
  -d '{"action_type":"allocateLead","ontology":"Marketing",
       "default_parameters":{"salesConsultantId":"SC-000001"},
       "items":[{"parameters":{"leadId":"L-001001"}}, ...1000 items...],
       "shard_size":100,"fail_fast":false}'

# Step 2: AG-UI Agent 分析销售负载
# NL: "分析销售顾问 SC-000001 当前负责的线索数量和分布"
# Agent 自动: query_with_dataframe(LeadAllocateRecord filter) → color_by(按 operationType) → 返回结论 + evidence_id
```

**演示要点**（终极闭环）：
- P2 批量写入（1000 项分片）→ 投影 → AG-UI Agent 自动分析
- 一条 NL 串起「批量 Action + 图分析 + 证据链」
- 证据链：`GET /objects/Marketing/analysis/{evidence_id}` 可回溯 Agent 推理过程

---

# 四、演示话术要点

## Action 演示话术

1. **声明式规则**：「看 allocateLead 的 ontology_rules——CreateObject + SYSTEM_GENERATED uuid + STATIC_VALUE operationType=2。这不是代码写死的，是配置驱动的声明式规则。换一种分配策略，改规则不改代码。」

2. **三层权限**：「invoke_action 走 ActionAuthorizer：执行权限（这个销售能不能调 allocateLead）+ 行级权限（能不能操作这个 leadId）+ 参数级权限（能不能分配给这个 salesConsultantId）。AI 想越权？三层都得过。」

3. **HITL 闸门**：「progressTestDrive 是 low risk 自动执行；如果设成 medium，前端 ApprovalDialog 弹窗列影响；设成 high，要输入名称确认。企业级 AI 不敢全自动，必须有人类闸门。」

4. **P2 批量**：「1000 条线索不是循环调 1000 次 API——那是反模式（中间失败留残数据）。我们一个 batch endpoint 单次提交，后端一个事务分片调度，逐项原子，单项失败不中断整批。」

## 图分析演示话术

1. **ADR-015 转向**：「我们最初让 LLM 在空画布编 5 步计划硬执行——0 对象还空转到底编假结论。转向 ReAct：每步读画布状态，0 对象就停。同时废了关键词路由（硬编码枚举不全，违反我们自己的红线 8）。发现错了就改，不硬撑。」

2. **三层正交**：「`/objects/*` 只吃结构化 ObjectSet IR，不吃自然语言——这是对标 Palantir 的范式正确性。NL 走 `/ai/agent`，LLM 调 query_with_dataframe 工具。两层正交，Ontology API 不依赖 LLM 可靠性。」

3. **证据链**：「Agent 每步推理都存 AnalysisRecord，拿到 evidence_id 可回溯。不是黑盒答完就完，是可审计的。」

4. **多引擎联动**：「一条 IR 串起属性过滤（PG）+ 图遍历（Neo4j）+ 空间（PostGIS）+ 时序（TimescaleDB）——8 Layer 协同，用户只看到一个 Query。」

---

# 五、演示前数据准备 Checklist

```bash
# 1. 启动基础设施（含 Neo4j）
docker compose up -d --profile graph

# 2. 启动前后端
bash scripts/dev.sh

# 3. 灌入 ChainSmoke 图数据（小规模 + 大规模二选一）
python scripts/setup_explore_demo.py           # 4 节点冒烟
python scripts/setup_explore_demo_large.py     # 950 节点完整演示

# 4. 灌入 Marketing 本体 + 数据（benchmark setup）
cd tests/benchmark/marketing && bash scripts/setup.sh

# 5. 灌入 DVP 本体 + 数据（VIRTUAL，需 MySQL 容器）
cd tests/benchmark/dvp && bash scripts/setup.sh

# 6. 配置 AI_MODEL（AG-UI Agent / TextQL / AI 产物 Action 依赖）
echo 'AI_MODEL=deepseek:deepseek-chat' >> .env
echo 'DEEPSEEK_API_KEY=sk-...' >> .env

# 7. 验证全链路
python scripts/verify_e2e_full.py
```

**诚实边界（演示前确认）**：
- [ ] `AI_MODEL` 已配置 → 否则 G10 / A8 / A9 / G15 无法演示
- [ ] Neo4j profile 已起 → 否则 G1-G10 降级走 PG（演示效果差）
- [ ] Doris BE 内存 ≥3g → 否则大表同步 OOM
- [ ] DVP 的 MySQL 容器已起 → 否则 G11-G15 无法演示
- [ ] Marketing benchmark 数据已灌 → 否则 A1-A10 找不到 leadId
