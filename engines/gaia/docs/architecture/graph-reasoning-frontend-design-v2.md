# 图关联推理前端设计 v2 —— 从 MVP 骨架到 Vertex 式决策分析能力

> **状态**：设计定稿（待评审），基于 v1 实现的差距盘点 + 业界最佳实践调研
> **版本**：v2.0（2026-07-02）
> **前置文档**：
> - [graph-reasoning-frontend-design.md](./graph-reasoning-frontend-design.md)（v1，已实现 MVP 骨架）
> - [graph-reasoning-design.md](./graph-reasoning-design.md)（后端总设计，5 目标 + 4 场景）
> - [reference-graph-reasoning.md](./reference-graph-reasoning.md)（Palantir 范式源头）
>
> **v1 已实现**：GraphExplorePage + GraphCanvas（单跳 Search Around）+ Selection/Layers/Histogram 三 tab + MapPanel + TrajectoryPlayer + PathFinder + EvidenceDrawer + NL 查询
>
> **v2 要补的**：把 v1 从"图探索 MVP 骨架"拉到"可用决策分析工具"，对齐原始诉求 5 目标（尤其目标 4 分析→行动闭环）

---

## 〇、v2 设计依据

### 0.1 v1 差距盘点（对照原始诉求 5 目标）

| 原始诉求目标 | v1 达成度 | v2 补什么 |
|------------|----------|----------|
| 1. 可推理（绑定本体+溯源） | ✅ | — |
| 2. 多模型混合 | ✅ 后端 / 🟡 前端不体现引擎 | 多步 Search Around 展示每跳引擎来源 |
| 3. 本体驱动 + NL | 🟡 NL 单次装载 | **多步 Search Around 配置面板**（Vertex 核心范式） |
| 4. 分析与行动闭环 | ❌ 完全断裂 | **选中节点触发 Action**（复用 ExecuteActionDialog） |
| 5. 全链路审计 | 🟡 单对象轨迹 | **全局时间轴**（画布级时间过滤） |

### 0.2 调研结论（业界最佳实践，附出处）

| # | 实践 | 出处 | v2 采纳 |
|---|------|------|---------|
| R1 | **Hairball/Snowstorm/Starburst 三大视觉陷阱**：不要一次塞全部数据，用过滤+聚类+渐进披露 | Cambridge Intelligence UX 博客 | LOD 阈值 + Search Around 预览数量 + 星爆节点折叠 |
| R2 | **布局按场景选择**：力导向(聚类) / 层级(流程) / 环形(网络) / 网格(批量)；力导向 > 几百节点就退化 | Cytoscape.js layouts + yFiles docs | 多布局切换 + 节点>1000 自动切 cose |
| R3 | **增量布局保心理地图**：expand-collapse 后只对新节点布局，不重排全图（PLOS One 论文） | cytoscape-expand-collapse + PLOS One | v1 已做（fcose randomize=false），v2 强化 |
| R4 | **过滤面板按类别计数 + 显隐切换**：每个节点类型显示数量 + 可一键隐藏 | Linkurious Filter Panel | Histogram tab 强化类别计数 |
| R5 | **Focus 模式**：点节点/边 dim 其他，可累加选中 | FalkorDB/GitHub issue | 选中节点高亮 + 邻居高亮，非邻居 dim |
| R6 | **时间轴是全局分析维度**：拖动滑块过滤整个画布（不是单对象回放）；Kibana Graph Timebar 即此模式 | Elastic Kibana PR #123966 + KronoGraph | **全局 TimeScrubber**（替换单对象 TrajectoryPlayer） |
| R7 | **Action 从对象触发**：选中对象→按钮/右键→Action 表单（参数预填 rid）→提交→toast 反馈→read-your-writes 刷新 | Palantir Workshop Actions + 现有 ExecuteActionDialog | **侧栏 Action 区 + 右键 Action 子菜单** |
| R8 | **配置化探索是核心差异化**：多跳 + 关系类型勾选 + 属性过滤 + 跨类型链式，可视化构建 ObjectSet IR | Palantir Vertex Search Around + NebulaGraph Visual Query + GraphDetective | **SearchAroundConfigPanel**（v2 核心） |
| R9 | **颜色语义化**：红=风险/绿=正常/蓝粉紫中性；不要每个实体一色；灰度也要可读 | Cambridge Intelligence 颜色指南 | LayersPanel 配色规则 + 默认调色板调整 |
| R10 | **canvas 测试难**：cytoscape 渲染在 canvas，RTL 无法点节点；策略=抽逻辑到 hook + mock cy 实例 | StackOverflow + Feedzai Genome 博客 | hook 测试为主，组件测试 mock cytoscape |

---

## 一、v2 三大核心交互（设计重点）

### 1.1 多步 Search Around 配置面板（SearchAroundConfigPanel）

**为什么是核心**：Vertex/材料里每个场景的核心操作都是"右键→配置跳数/关系类型/属性过滤→展开"。v1 只有单跳直接展开，无法完成"3跳内关联""仅展开高风险供应商"等目标场景。

**位置**：侧栏新增第四 tab「探索」，或右键菜单「🔍 配置探索…」打开浮层。选**侧栏 tab**（与选中/图层/分布并列），因为多步配置需要持续可见、可编辑、可重跑。

**交互流程**（对齐 Vertex 多步 Search Around）：

```
┌─ 探索 tab（侧栏）──────────────────────┐
│ 起始对象集                              │
│  [当前选中: S001 (Supplier)]  [重选]    │
│                                          │
│ ┌─ Link 1 ──────────────────────────┐  │
│ │ 关系: supplies ▼   方向: 下游 ▼    │  │
│ │ 跳数: 1 ────●──── 3                │  │
│ │ 过滤: status = unfulfilled  [+]    │  │
│ │ 预览: 命中 12 个 Order              │  │
│ │              [展开到画布]           │  │
│ └────────────────────────────────────┘  │
│           [+ 添加下一跳]                 │
│ ┌─ Link 2（链式，以上一跳结果为起点）─┐  │
│ │ 关系: produces ▼  方向: 下游 ▼      │  │
│ │ ...                                 │  │
│ └────────────────────────────────────┘  │
│                                          │
│ [全部展开]  [保存为模板]                 │
└──────────────────────────────────────────┘
```

**关键设计点**：
1. **起始对象集**：默认当前选中节点，可"重选"改为框选的多节点
2. **每跳配置卡片**：关系类型下拉（从 linkTypes 取）+ 方向（上下游）+ 跳数滑块 + 属性过滤（+号加条件）
3. **预览数量**：配置后点「预览」调 `traverse_link`（带 target_filter）显示命中数，不直接展开——避免星爆（R1）
4. **链式**：Link 2 的起始集 = Link 1 的结果集，可视化构建嵌套 `searchAround` IR
5. **全部展开**：构建完整 ObjectSet IR 调 `queryDataFrame`，增量加到画布
6. **保存为模板**：localStorage 存 IR + 名称（v2 轻量，不入库）

**与后端对接**：
- 单跳预览：`POST /traverse`（已有，传 target_filter）
- 多跳链式：`POST /query-dataframe` 传嵌套 `searchAround` IR（后端 ObjectSetIR 已支持，C7）
- 证据链：每次「全部展开」生成 analysis_record

**防星爆（R1）**：预览数量 > 50 时提示"结果较多，建议加过滤"；> 200 强制要求加过滤才能展开。

### 1.2 分析→行动闭环（ActionTrigger）

**为什么是核心**：原始诉求目标 4"采取行动"完全断裂。后端 Action 系统已成熟，前端 ObjectDetailPanel 已集成 ExecuteActionDialog，图探索侧栏只需复用。

**位置**：侧栏 Selection tab 底部（选中节点详情下方），+ 右键菜单 Action 子项。

**交互流程**（对齐 Palantir Workshop Actions，R7）：

```
┌─ Selection tab ──────────────────────┐
│ Supplier · S001                        │
│ rid: S001                              │
│ name: Acme                             │
│ supplierId: S001                       │
│ ─────────────────────                  │
│ Search Around                          │
│  🔗 supplies (MANY)                    │
│  🔍 路径推理                           │
│ ─────────────────────                  │
│ ⚡ 可执行操作          ← v2 新增        │
│  ▸ 标记为风险供应商    ← Modify Action  │
│  ▸ 下发核查工单        ← Create Action  │
│  ▸ 调整供货优先级      ← Modify Action  │
└────────────────────────────────────────┘
```

点操作 → 弹 `ExecuteActionDialog`（已有组件，传 `initialParameters={rid: rid}`）→ 填参数 → 提交 → toast 反馈 → read-your-writes 刷新节点属性。

**关键设计点**：
1. **操作列表来源**：`listActionTypes(ontology)` 过滤 `affected_object_type == 选中节点 api_name`
2. **参数预填**：rid 自动填（用户不可见），其他参数表单输入
3. **提交后刷新**：`onApplied` 回调重新查 object_state 更新节点 props（read-your-writes）
4. **右键入口**：右键菜单加「⚡ 操作」子菜单（展开操作列表），与侧栏等价
5. **风险标记快捷**：常见 Modify Action（如标记风险）可一键执行无需弹窗（参数已预填）

**与后端对接**：
- 列操作：`GET /actions/definitions/{ontology}`（已有）
- 执行：`POST /actions/{ontology}/{objectType}/{actionApiName}/execute`（已有）
- 刷新：执行成功后重新查 object_state

### 1.3 全局时间轴（TimeScrubber）

**为什么是核心**：v1 的 TrajectoryPlayer 只回放单对象轨迹，不是全局分析维度。材料场景四"48h内进入敏感区域"的核心交互是拖动时间轴过滤整个画布。

**位置**：画布底部新增时间轴条（替代原底栏的统计信息，统计信息移到顶栏）。

**交互流程**（对齐 Kibana Graph Timebar + KronoGraph，R6）：

```
┌─ 画布 ────────────────────────────────────┐
│                                            │
│        ◯ ── ◯ ── ◯                        │
│         \      /                           │
│          ◯                                │
│                                            │
├─ 时间轴 ────────────────────────────────────┤
│ 2026-07-01          ═══════════          2026-07-02 │
│                     ◄──选中 48h──►                │
│  [仅显示该时段活跃实体 ☑]                      │
└────────────────────────────────────────────┘
```

**关键设计点**：
1. **时间范围**：从画布节点的时序属性自动推算时间范围，或用户手动选
2. **拖动选窗**：左右滑块定时间窗，画布只显示该窗内有活动的节点（节点有 timeseries 属性且窗内有数据）
3. **仅活跃实体**：勾选后，窗内无时序数据的节点变灰
4. **轨迹叠加**：切到地图视图时，时间窗内的轨迹点叠加显示
5. **回放**：点播放按钮，时间窗自动滑动，画布节点状态/地图轨迹同步变化

**与后端对接**：
- 取时间范围：`POST /series-query` 查所有节点 series 的 min/max timestamp
- 窗口过滤：前端按 timestamp 过滤节点（有时序属性的）
- 地图轨迹：`POST /series-query` 传 time_range

**降级**：无时序属性的对象不参与时间过滤，保持原样显示。

---

## 二、配套交互改进

### 2.1 多布局切换（R2）

顶栏加布局下拉：力导向(fcose) / 层级(dagre) / 环形(circle) / 网格(grid)。

| 布局 | 适用场景 | cytoscape 扩展 |
|------|---------|---------------|
| fcose（默认） | 关系网络聚类 | 已用 |
| dagre 层级 | 流程链路（供应商→物料→订单） | cytoscape-dagre |
| circle 环形 | 网络拓扑 | 内置 |
| grid 网格 | 批量同类节点 | 内置 |

节点 > 1000 自动切 cose（更快，R2）。

### 2.2 Focus 模式 + 邻居高亮（R5）

选中节点时：节点+直接邻居高亮，二度外节点 dim（opacity 0.2）。点空白恢复。Cytoscape 用 `eles.difference(neighborhood)` 设 style。

### 2.3 事件徽章（材料三·3）

节点右上角彩色小圆点，表示关联事件（告警/异常）。从节点 props 的特定字段（如 `alert_count > 0`）渲染。hover 显示事件详情 tooltip。

### 2.4 Search Around 预览数量（R1 防星爆）

右键 Search Around 菜单项旁显示该 link 的目标对象数量（如"🔗 supplies (12)"），调 `traverse_link` 预查询 count。数量大时菜单项标红警示。

---

## 三、布局与信息架构调整

### 3.1 顶栏（调整）

```
[本体▼] [对象类型▼] [属性过滤] [加载对象] | [NL提问] [💬问] | [布局▼] [布局] [⚙]
                                              统计: 节点4 边2 证据 abc123
```
统计信息从底栏移到顶栏右侧（给时间轴腾位置）。

### 3.2 侧栏四 tab（v1 三 tab + 探索）

选中 / 图层 / 分布 / **探索**（v2 新增 SearchAroundConfigPanel）

### 3.3 底栏 → 时间轴（v2 改造）

原底栏统计移走，底部改为全局 TimeScrubber（无时序数据时折叠为一行提示）。

---

## 四、技术实现要点

### 4.1 SearchAroundConfigPanel 状态管理

IR 构建是核心，状态独立于 useGraphExplore（探索配置不应被画布操作污染）：

```typescript
// hooks/useSearchAroundConfig.ts
interface SearchAroundStep {
  linkType: string;
  direction: 'forward' | 'reverse';
  maxHops: number;
  filters: GraphFilter[];  // 目标对象属性过滤
  previewCount?: number;   // 预览命中数
}
interface SearchAroundConfig {
  startVids: string[];     // 起始对象集
  steps: SearchAroundStep[]; // 链式跳
}
```

构建 ObjectSet IR：
```typescript
// steps 链式构建嵌套 searchAround IR
function buildIR(config: SearchAroundConfig): ObjectSetIR {
  let ir: ObjectSetIR = { type: 'static', objects: config.startVids };
  for (const step of config.steps) {
    ir = {
      type: 'searchAround',
      object_set: ir,
      link: step.linkType,
      direction: step.direction === 'forward' ? 'out' : 'in',
      hops: [1, step.maxHops],
      filters: step.filters,
    };
  }
  return ir;
}
```

### 4.2 ActionTrigger 复用

```typescript
// 侧栏 Selection tab
const [execAction, setExecAction] = useState<ActionTypeRecord | null>(null);
const applicableActions = actionTypes.filter(a => a.affected_object_type === selectedNode.api_name);

// 渲染操作列表
{applicableActions.map(a => (
  <button onClick={() => setExecAction(a)}>▸ {a.display_name}</button>
))}

// 复用现有组件
{execAction && (
  <ExecuteActionDialog
    open
    onClose={() => setExecAction(null)}
    ontology={ontology}
    objectType={selectedNode.api_name}
    action={execAction}
    initialParameters={{ rid: selectedNode.rid }}  // 预填
    onApplied={(result) => {
      // read-your-writes：重新查 object_state 刷新节点
      refreshNode(selectedNode.rid);
      setExecAction(null);
    }}
  />
)}
```

### 4.3 TimeScrubber 实现

```typescript
// hooks/useTimeFilter.ts
interface TimeFilter {
  range: [Date, Date] | null;  // 选中的时间窗
  activeOnly: boolean;          // 仅显示活跃实体
}
// 节点过滤：node 有 timeseries 属性且 range 内有数据 → 显示；否则 dim
```

### 4.4 多布局

cytoscape 布局扩展按需动态 import（避免首屏加载全部）：
```typescript
const layout = await import('cytoscape-dagre');
cy.use(layout.default);
cy.layout({ name: 'dagre', rankDir: 'LR' }).run();
```

---

## 五、测试策略（R10）

canvas 渲染无法用 RTL 点节点，策略分层：

| 层 | 工具 | 覆盖 |
|----|------|------|
| **逻辑层（hook）** | vitest + RTL renderHook | useSearchAroundConfig（IR 构建）、useTimeFilter（过滤逻辑）、useGraphExplore（已有） |
| **组件层（纯 UI）** | vitest + RTL render | SearchAroundConfigPanel（表单交互+IR 产出）、ActionTrigger（操作列表+对话框触发）、TimeScrubber（滑块+过滤） |
| **画布交互（cytoscape）** | mock cytoscape 实例 | GraphCanvas 右键菜单/布局切换（mock cy.layout/cy.on） |
| **端到端** | 浏览器手动 | 真实数据全链路（ChainSmoke 场景） |

hook 测试是重点——把 IR 构建、过滤逻辑、Action 参数预填都放 hook，组件只渲染。

---

## 六、分期实施（v2）

### Phase 2e：分析→行动闭环（P0，目标 4）
- [ ] hooks/useActionTrigger.ts（列操作 + 预填参数 + 刷新）
- [ ] 侧栏 Selection tab 加「⚡ 可执行操作」区
- [ ] 右键菜单加「⚡ 操作」子菜单
- [ ] 复用 ExecuteActionDialog + read-your-writes 刷新
- [ ] hook 测试 + 组件测试

### Phase 2f：多步 Search Around 配置面板（P0，目标 3）
- [ ] hooks/useSearchAroundConfig.ts（链式 IR 构建）
- [ ] components/SearchAroundConfigPanel.tsx（步骤卡片 + 预览 + 链式）
- [ ] 侧栏加「探索」tab
- [ ] 预览数量 + 防星爆提示
- [ ] hook 测试（IR 构建）+ 组件测试

### Phase 2g：全局时间轴（P1，目标 5）
- [ ] hooks/useTimeFilter.ts
- [ ] components/TimeScrubber.tsx（滑块 + 活跃过滤 + 回放）
- [ ] 底栏改造 + 统计移顶栏
- [ ] 与 MapPanel 轨迹联动
- [ ] hook 测试 + 组件测试

### Phase 2h：配套改进（P1-P2）
- [ ] 多布局切换（dagre/circle/grid）
- [ ] Focus 模式 + 邻居高亮
- [ ] 事件徽章
- [ ] Search Around 预览数量

---

## 七、验收（对齐原始诉求 5 目标）

| 目标 | v2 验收点 |
|------|----------|
| 1. 可推理 | 选中节点→看属性→溯源（v1 已达） |
| 2. 多模型混合 | 多步 Search Around 每跳显示引擎来源（Neo4j/PostGIS） |
| 3. 本体驱动+NL | SearchAroundConfigPanel 可视化构建 IR + NL 入口保留 |
| **4. 分析→行动闭环** | 选中风险节点→触发处置 Action→toast 反馈→节点状态刷新 |
| 5. 全链路审计 | 全局时间轴回放分析过程 + 证据链快照 |

### 场景验收（4 个目标场景）

| 场景 | v2 能否完成 |
|------|------------|
| 嫌疑人3跳关联 | ✅ SearchAroundConfigPanel 配 3 跳 + 关系类型勾选 |
| 两账户间接汇款 | ✅ PathFinder 多跳路径 + 中间节点类型过滤 |
| 供应商停产影响+替代 | ✅ 多步 Search Around 下游传导 + MapPanel 空间筛选 + What-If(二期) |
| 48h敏感区域装备 | ✅ TimeScrubber 选 48h + MapPanel 空间过滤 + 关联人员展开 |

场景三的 What-If 仿真属 P3（依赖后端 Model Mesh），v2 不做，标注二期。
