# 供应链演练 · 图探索对话 Query 清单

> **入口**：前端 http://127.0.0.1:5174/explore → 选「供应链演练」
> **对话方式**：在中央对话框（ExploreLanding）或顶部对话框（AssistantUiChat）输入以下自然语言
> **原理**：AG-UI ReAct Agent 每轮读画布状态决策，调 `query_with_dataframe` / `traverse_link` / `switch_view` / `color_by` 等工具驱动画布。Agent 收到的 schema 注入包含 OT 列表（Supplier/Material/Order/Customer + 属性），link_type 需它自己调 list_link_types 探索。
>
> **重要**：以下 Query 用业务语言，**不要**在对话里硬编码 link_type/api_name（如「traverse_link supplies」）——那样失去 NL 演示意义。让 Agent 自己理解业务意图。

---

## 场景 1：加载供应链网络（建立画布）

**对话输入**：
```
查看所有供应商
```

**预期 Agent 行为**：
1. 读 schema 摘要 → 知道有 `Supplier` OT
2. 调 `query_with_dataframe`（objectType=Supplier，无 filter）
3. 画布渲染 50 个供应商节点
4. 返回：「已加载 50 个供应商」

**验证点**：画布出现节点，`object_count: 50`，视图=graph。

---

## 场景 2：⭐ 路径推理（供应链溯源）

**对话输入**（在场景 1 之后）：
```
供应商 S000 的物料最终用在了哪些订单上？
```

**预期 Agent 行为**：
1. 读画布状态（已有 50 个供应商）→ 决定探索 S000 的下游
2. 调 `list_link_types` → 发现 `supplies` / `usedIn` 关系
3. 调 `traverse_link`（link_type=supplies, source_keys=[S000]）→ 拿到 M045/M080/M081 等 5 个物料
4. 调 `traverse_link`（link_type=usedIn, source_keys=[5 个物料]）→ 拿到订单
5. 画布扩展显示 S000 → Material → Order 链路
6. 返回：「S000 供应的物料用于 N 个订单」

**验证点**：画布出现 S000 → M*** → O*** 的链路，证据链 evidence_id 生成。

---

## 场景 3：⭐ 空间分析（地图分布）

**对话输入**：
```
在地图上查看供应商的地理分布
```

**预期 Agent 行为**：
1. 读画布状态（已有供应商）
2. 调 `switch_view`（view=map）
3. 画布切换到 MapLibre 地图视图
4. 供应商 marker 按 location（北京/上海/广州/深圳 8 城）渲染
5. 返回：「已切换到地图视图，显示 50 个供应商的地理分布」

**验证点**：视图切到 map，地图上出现 marker 聚类（8 个城市）。

---

## 场景 4：⭐ 空间 + 图遍历组合（区域影响分析）

**对话输入**：
```
北京周边的供应商供应了哪些物料？
```

**预期 Agent 行为**：
1. 调 `query_with_dataframe` 加载 Supplier（或读已有画布）
2. 调 `spatial_filter`（北京 500km，候选=供应商 rid）→ 4 个供应商（S006/S019/S031/S035）
3. 调 `traverse_link`（supplies, source_keys=[4 个供应商]）→ 它们的物料
4. 画布高亮这 4 个供应商 + 关联物料
5. 返回：「北京周边 4 个供应商共供应 N 个物料」

**验证点**：画布聚焦到 4 个供应商 + 物料，地图/图谱联动。

---

## 场景 5：⭐ 着色分析（风险可视化）

**对话输入**：
```
按风险等级给供应商着色
```

**预期 Agent 行为**：
1. 读画布状态（已有供应商）
2. 调 `color_by`（field=riskLevel）
3. 画布节点按 riskLevel 着色：low=绿 / none=灰
4. 返回：「已按 riskLevel 着色」

**验证点**：节点变色，图例显示 riskLevel 分级。

---

## 场景 6：AG-UI 自动编排（ADR-015 灵魂）

**对话输入**（独立演示，从空画布开始）：
```
分析供应商 S000 对供应链下游的影响
```

**预期 Agent ReAct 多步执行**：
1. 调 `query_with_dataframe`（filter supplierId=S000）→ 加载 S000
2. 读 CanvasSnapshot → object_count=1
3. 调 `traverse_link`（supplies）→ 扩展到 5 个物料
4. 读 state → object_count=6
5. 调 `traverse_link`（usedIn）→ 扩展到订单
6. 读 state → object_count 增长
7. 调 `color_by`（riskLevel）→ 着色
8. 返回分析结论 + evidence_id

**验证点**（必讲）：
- Agent 每步读画布状态决策，**不是空画布编计划**
- object_count 递增可见
- 工具调用序列在对话流中可见（ToolCallPart 渲染）

---

## 场景 7：空状态自然终止（ADR-015 D5 守卫）

**对话输入**（故意问不存在的数据）：
```
分析所有 high 风险的供应商
```

**预期 Agent 行为**：
1. 调 `query_with_dataframe`（filter riskLevel=high）
2. 返回 0 个对象（数据里没有 high，只有 low/none）
3. 读 CanvasSnapshot → object_count=0
4. **自然终止**：「当前本体中没有 high 风险的供应商，无法分析」（不编造结论）

**验证点**（必讲）：
- 0 对象时 Agent **不编造**「重点关注XX」的假结论
- 这是 ADR-015 从 explore-plan 转向 ReAct 的核心修复点
- 对比旧架构：explore-plan 会硬凑 5 步计划空转到底还编结论

---

## 演示节奏建议

| 步骤 | Query | 时长 | 重点 |
|------|-------|------|------|
| 1 | 「查看所有供应商」 | 30s | 建立画布 |
| 2 | 「供应商 S000 的物料用在了哪些订单上？」 | 1min | 路径推理 ⭐ |
| 3 | 「在地图上查看供应商的地理分布」 | 30s | 空间分析 ⭐ |
| 4 | 「按风险等级给供应商着色」 | 20s | 着色分析 ⭐ |
| 5 | 「分析供应商 S000 对供应链下游的影响」 | 1.5min | AG-UI 自动编排 ⭐⭐ |
| 6 | 「分析所有 high 风险的供应商」 | 30s | 空状态守卫 ⭐ |

## 话术要点

1. **每步看对话流**：「注意对话框里的工具调用——Agent 每调一个工具，你都能看到它在干什么，不是黑盒。」

2. **看画布状态变化**：「object_count 从 0 → 1 → 6 → N，Agent 每步都读这个数字决定下一步。这是 ReAct 范式。」

3. **空状态守卫是亮点**：「问 high 风险供应商，没有数据——Agent 直接说没有，不编造。这看起来简单，但旧架构（explore-plan）会硬编 5 步计划还给出假结论。我们发现错了就改，这是工程诚实。」

4. **地图 + 图谱联动**：「切到地图看地理分布，切回图谱看关系网络——同一份数据，两个视角，Agent 自动切换。」

## ⚠️ 已知限制

- **Agent 依赖 LLM 能力**：DeepSeek V4 一般能理解业务意图，但偶尔可能调错工具。演示前建议先跑一遍场景 1-2 确认稳定。
- **link_type 探索**：Agent 需先调 list_link_types 才知道 supplies/usedIn，多一次工具调用。如果 Agent 卡住，可改用更直白的 query：「查看 S000 供应的所有物料」。
- **地图 marker 依赖 location 数据**：已补投影 50 供应商 + 200 客户到 PostGIS，但如果 Agent 加载的是 Order（无 GEOPOINT），地图无 marker。
