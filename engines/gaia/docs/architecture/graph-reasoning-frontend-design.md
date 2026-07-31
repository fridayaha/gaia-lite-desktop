# 图关联推理二期前端设计 —— Vertex 式图探索与时空分析

> **状态**：设计稿（待评审）
> **版本**：v1.0（2026-07-02）
> **关联文档**：
> - [graph-reasoning-design.md](./graph-reasoning-design.md)（特性总设计，本期后端 M0-M7 已完成）
> - [graph-reasoning-progress.md](./graph-reasoning-progress.md)（实现进度）
> - [frontend-hci-review.md](../design/frontend-hci-review.md)（现有前端 HCI 审视）
> - [reference.md](../reference.md)（Palantir 范式源头）
>
> **设计原则对齐**：CLAUDE.md 第一原则"把复杂留给自己，把简单留给用户"——多引擎流转的全部复杂度收在前端之下，用户只感知业务语义：选中对象 → 右键扩展关系 → 看见链路 → 查证据。

---

## 〇、设计契约

二期前端是图推理特性的**用户价值落地**。后端 M0-M7 已提供完整 API（query-dataframe / traverse / exists-link / query-nl / analysis / timeseries-sync），但用户触点只有 AG-UI 对话（JSON 结果）。二期补齐可视化触点，对标 Palantir Gotham/Foundry Vertex。

| # | 契约 | 决策来源 |
|---|------|----------|
| F1 | 图探索画布是**对象实例图**（运行期对象 + 关系），区别于现有 OntologyGraph（设计期 ObjectType + LinkType schema） | Palantir Vertex 范式 + 现有组件澄清 |
| F2 | 复用 Cytoscape.js（已用于 OntologyGraph），不引入新图谱库 | 现有技术栈 + 组件复用原则 |
| F3 | 交互对标 Vertex：选中节点 → 右键 Search Around → 增量加边；侧栏 Selection/Layers/Histogram 三 tab | Palantir Vertex docs 调研 |
| F4 | 地图组件用 MapLibre GL（开源，Leaflet 的 WebGL 演进），轨迹回放走 TimescaleDB series_query | 开源选型 + 后端接口已就绪 |
| F5 | 证据链查看器是只读展开面板，复用现有 Modal/Disclosure 原语 | ADR-013 React Aria 组件复用 |
| F6 | ObjectSet IR 构建器是**可选高级入口**，默认走 NL（query-nl）/ 右键探索；IR 构建器供高级用户精确控制 | 渐进式披露原则 |
| F7 | 新 DataType（GEOTEMPORAL_SERIES/TIME_SERIES）在属性编辑器显示为"时序引用"特殊类型，不可编辑值（值在超表） | 后端 C3 流式独立链路语义 |
| F8 | 图探索画布支持 LOD（Level of Detail）：节点 >500 时折叠聚合，避免渲染卡顿 | Cytoscape 性能 + 图可视化 UX 最佳实践 |
| F9 | 所有可视化结果可"另存为证据"（调 query-dataframe 存 analysis_records） | 证据链闭环 |

---

## 一、价值定位

### 1.1 为什么需要二期前端

后端 M0-M7 让 API/Agent 能力完整，但用户触点单一（AG-UI 对话返回 JSON）。图推理的核心价值——**看见关系网络、时空轨迹、证据链**——必须可视化才能释放：

| 后端能力（已完成） | 前端缺什么（二期补） |
|---|---|
| searchAround 多跳遍历 | 画布右键扩展关系，看见链路拓扑 |
| spatial_filter PostGIS | 地图框选/圈选过滤，看见空间分布 |
| series_query TimescaleDB | 地图轨迹回放，看见移动路径 |
| analysis_records 证据链 | 展开看每跳来源 + 各步耗时 |
| query-nl 自然语言 | 对话直接驱动画布探索（NL→图） |
| traverse_link/exists_link | 画布内单跳精确操作 |

### 1.2 目标场景（对齐总设计 §1.4）

| 场景 | 二期前端能力 |
|---|---|
| 情报/执法 | 选中嫌疑人 → 右键 3 跳扩展 → 看通讯/资金/同行网络 → 圈选区域找共现 |
| 金融风控 | 两账户 → 找路径 → 看壳公司网络 → 着色按风险评分 |
| 供应链 | 选中停产供应商 → 扩展受影响订单 → 地图找 300km 替代 → 时序看库存趋势 |
| 军工/态势 | 时间轴拖动 → 48h 内进入敏感区装备 → 轨迹回放 → 关联人员 |

---

## 二、架构总览

### 2.1 前端分层

```
┌─────────────────────────────────────────────────────────────────┐
│  用户触点                                                         │
│  ① 图探索画布（GraphExplorePage）  ② 对话驱动（AiSuggestPanel）  │
│  ③ 地图轨迹（MapPanel，画布内嵌）  ④ 证据链（EvidenceDrawer）    │
├─────────────────────────────────────────────────────────────────┤
│  组件层                                                           │
│  GraphCanvas / SelectionSidebar / LayersPanel / HistogramPanel   │
│  MapPanel / TrajectoryPlayer / EvidenceTimeline / ObjectSetBuilder│
├─────────────────────────────────────────────────────────────────┤
│  API 层（api/graph.ts，新增）                                      │
│  queryDataFrame / queryNL / traverseLink / existsLink /           │
│  getAnalysis / startTimeseriesSync                                │
├─────────────────────────────────────────────────────────────────┤
│  状态层（hooks/useGraphExplore）                                   │
│  节点/边增量管理 + LOD 折叠 + 撤销栈 + 选中态                      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 路由新增

```
/explore                # 图探索画布（主入口，空画布 + 搜索起手）
/explore/:ontology      # 指定本体画布（从 OntologyWorkspace "探索数据"按钮进入）
```

`Layout.tsx` 的 RAIL_ITEMS 加第 5 项"图探索"（🔍），符合米勒定律（5 < 7±2）。

### 2.3 与现有组件的关系

| 现有组件 | 二期复用方式 |
|---|---|
| `OntologyGraph.tsx`（schema 图谱） | **不动**。GraphCanvas 是新组件，对象实例图。共用 cytoscapeExtensions.ts（单例扩展注册） |
| `ObjectDetailPanel.tsx` | 复用为 SelectionSidebar 的详情区（选中节点显示属性） |
| `AssistantUiChat` / `AiSuggestPanel` | 对话结果含 query_with_dataframe 工具调用时，加"在画布打开"按钮 |
| `Modal` / `Disclosure` / `ConfirmDialog` | 证据链/IR 构建器复用 |
| `SearchBar` | 复用为画布起手搜索 |
| `StatusBadge` / `EmptyState` / `Skeleton` | 复用 |

---

## 三、图探索画布（GraphExplorePage）—— 核心

### 3.1 布局

```
┌──────────────────────────────────────────────────────────────┐
│  顶栏：本体选择 | 搜索框(起手) | 撤销/重做 | 清空 | 存证据      │
├──────────┬──────────────────────────────────┬────────────────┤
│          │                                  │  侧栏（三 tab）  │
│  图层    │                                  │  ◉ Selection    │
│  控制    │       Cytoscape 画布             │  ◯ Layers       │
│  (折叠)  │       (对象节点 + 关系边)         │  ◯ Histogram    │
│          │                                  │                │
│  - 布局  │                                  │  选中节点属性    │
│  - 着色  │                                  │  / 图层样式      │
│  - 筛选  │                                  │  / 属性分布      │
│          │                                  │                │
├──────────┴──────────────────────────────────┴────────────────┤
│  底栏：节点数 N | 边数 M | 引擎耗时 | 截断提示 | 缩放控制         │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 起手流程（空画布 → 有内容）

1. 用户进 `/explore/:ontology`，画布空，提示"搜索对象开始探索"
2. 搜索框输入：对象类型 + 过滤条件（复用 SearchBar + ObjectType 选择）
3. 调 `POST /objects/{ont}/query-dataframe` 传 `{type:"objectType", object_type:"X", filters:[...]}`
4. 返回对象集 → 画布渲染为节点（按 ObjectType 着色）
5. 用户选中节点 → 右键 → Search Around

### 3.3 右键 Search Around（对标 Vertex 核心交互）

选中节点右键弹出 cxtmenu（复用 OntologyGraph 已有的 cxtmenu 模式）：

```
┌─────────────────────────┐
│  🔍 Search Around...     │ → 子菜单：列出该节点 ObjectType 的所有 LinkType
│    ├ supplies (→ Order)  │   点击 → 调 traverse_link(direction=forward)
│    ├ assignedTo (→ User) │   返回目标对象 → 增量加到画布（不重渲染全图）
│    └ ...                  │
│  🗺 在地图查看            │ → 切换地图视图（若有空间属性）
│  📊 查看时序              │ → 打开 TrajectoryPlayer（若有时序属性）
│  📋 复制 rid       │
│  🗑 从画布移除            │
└─────────────────────────┘
```

**增量加边**（不重渲染全图，性能关键）：
- 新节点 diff 后 `cy.add()`，已存在节点跳过
- 新边同理，避免布局重排抖动（可选"自动重排"开关）
- 节点数 >500 触发 LOD 折叠（同类型节点聚合成超级节点，显示数量）

### 3.4 侧栏三 tab（对标 Vertex）

**Selection tab**（选中节点/边）：
- 复用 `ObjectDetailPanel`：显示对象全量属性（rid + api_name + props）
- 底部"操作区"：Search Around 快捷按钮 + "在地图查看" + "查时序"

**Layers tab**（图层样式）：
- 按 ObjectType 着色（默认）+ 自定义着色（按属性值，如 status=ACTIVE 绿/INACTIVE 红）
- 节点大小（按度数 / 按属性值）
- 边粗细（按 weight）
- 保存样式（localStorage，二期不入库）

**Histogram tab**（属性分布筛选）：
- 选 ObjectType → 选属性 → 显示直方图（值分布）
- 框选区间 → "Filter to"（保留）/ "Filter out"（排除）
- 顶栏显示已应用筛选 chip，点 × 移除（对标 Vertex）

### 3.5 性能与防线（对齐后端 C9）

| 场景 | 前端处理 |
|---|---|
| 节点 >500 | LOD 折叠（同类型聚合超级节点） |
| 节点 >2000 | 警告 + 建议缩小范围（不强制截断，后端已截断） |
| 截断（truncated=true） | 底栏显示"结果截断，已显示前 N 个"，"加载更多"按钮（cursor 续取） |
| 大图布局 | 默认 fcose（现有），>1000 节点切 cose（更快） |
| 增量加边 | diff + cy.add()，不重布局 |

---

## 四、地图组件（MapPanel）—— 空间可视化

### 4.1 选型：MapLibre GL JS

- 开源、WebGL、矢量瓦片、性能优于 Leaflet
- 不依赖商业地图服务（可用开源瓦片源，如 OpenStreetMap raster）
- 与 React 集成：react-map-gl（MapLibre 适配）

### 4.2 嵌入方式

MapPanel 是**画布的可切换视图**（不是独立页）：
- 顶栏"视图切换"：图谱 | 地图 | 分屏
- 图谱视图：Cytoscape 画布
- 地图视图：MapLibre 画布，对象按 GEOPOINT 渲染为 marker
- 分屏：左右各半（图谱左 + 地图右），选中联动

### 4.3 交互

- **框选过滤**：地图上画矩形 → 调 `spatial_filter(withinBoundingBox)` → 命中对象高亮（图谱视图同步高亮）
- **圈选过滤**：点选中心 + 拖拽半径 → `withinDistance`
- **多边形过滤**：画多边形 → `withinPolygon`
- **着色**：与图谱 Layers tab 联动（同属性着色规则）

### 4.4 轨迹回放（TrajectoryPlayer）

选中含 `GEOTEMPORAL_SERIES` 属性的对象 → "查看时序" → 底部弹出播放器：

```
┌──────────────────────────────────────────────────────────────┐
│  ◀  ▶  ⏸    [████████░░░░░░] 2026-07-01 10:00 → 11:30       │
│  速度: 1x ▼   位置: 116.4, 39.9   速度: 60km/h   状态: ACTIVE │
└──────────────────────────────────────────────────────────────┘
```

- 调 `series_query(series_ids=[rid], time_range)` 取轨迹点
- 地图上画轨迹线 + 当前点 marker（随播放移动）
- 时间轴拖拽 scrubbing（对标 Vertex 时间选择面板）
- 多对象轨迹叠加（不同色）

---

## 五、证据链查看器（EvidenceDrawer）

### 5.1 触发

- 图谱/地图操作"存证据"后，toast 提示"已保存证据 chain_xxx，点击查看"
- 画布顶栏"证据历史"按钮 → 列表（近期 analysis_records）
- AG-UI 对话结果含 evidence_id 时，结果显示"查看证据链"链接

### 5.2 展示（Drawer 抽屉，复用 React Aria ModalOverlay）

```
┌──────────────────────────────────────────────┐
│  证据链 chain_xxx                    [关闭]   │
├──────────────────────────────────────────────┤
│  查询意图（NL）：供应商S001关联的所有订单       │
│  ObjectSet IR：searchAround(supplies, S001)   │
│  执行时间：2026-07-02 14:30                   │
├──────────────────────────────────────────────┤
│  执行轨迹（时间线）                            │
│  ● static [S001]           0.005s  1 个对象   │
│  ● searchAround (Neo4j)    3.8s    2 个对象   │
│  ○ hydrate (PG)            0.1s    2 个对象   │
├──────────────────────────────────────────────┤
│  命中对象（3）                                 │
│  ▸ Order O1  status=unfulfilled               │
│  ▸ Order O2  status=fulfilled                 │
│  ▸ Supplier S001                              │
├──────────────────────────────────────────────┤
│  引擎：postgres, neo4j   截断：否              │
└──────────────────────────────────────────────┘
```

- 调 `GET /objects/{ont}/analysis/{id}` 取证据
- 时间线用 React Aria Disclosure 展开各步
- "在画布打开"按钮 → 把命中对象加载到 GraphCanvas

---

## 六、ObjectSet IR 构建器（高级入口，可选）

### 6.1 定位

默认用户走 NL（query-nl）或右键探索。IR 构建器供**高级用户/Agent 调试**精确构造查询：

### 6.2 形态

Modal 弹窗，树形构造 IR：

```
┌──────────────────────────────────────────────┐
│  ObjectSet IR 构建器                [执行]   │
├──────────────────────────────────────────────┤
│  类型: ◉ searchAround  ○ filter  ○ objectType│
│  link: [supplies ▼]                          │
│  hops: [1] - [3]   direction: [both ▼]       │
│  子集:                                        │
│  ┌──────────────────────────────────────┐    │
│  │  类型: ◉ static  ○ filter            │    │
│  │  objects: [S001]                     │    │
│  └──────────────────────────────────────┘    │
├──────────────────────────────────────────────┤
│  预览 JSON:                                   │
│  {"type":"searchAround","link":"supplies",...}│
└──────────────────────────────────────────────┘
```

- 嵌套 ≤3 层（对齐后端校验）
- link/object_type 下拉从本体元数据加载
- 实时生成 JSON 预览
- "执行" → `POST /objects/{ont}/query-dataframe` → 结果进画布

---

## 七、新 DataType 适配

### 7.1 types/index.ts 补充

```typescript
export type DataType =
  | 'STRING' | 'INTEGER' | ... 
  | 'GEOPOINT' | 'GEOSHAPE'        // 已有
  | 'GEOTEMPORAL_SERIES'            // 新增
  | 'TIME_SERIES';                  // 新增
```

### 7.2 属性编辑器行为

| DataType | 编辑器 | 说明 |
|---|---|---|
| GEOPOINT | 坐标输入 [lon, lat] + 小地图选点 | 静态空间属性 |
| GEOSHAPE | WKT 文本框 + 地图绘制工具 | 静态空间属性 |
| GEOTEMPORAL_SERIES | 只读"时序引用"标签 + "查看轨迹"按钮 | 值在超表，不可编辑 |
| TIME_SERIES | 只读"时序引用"标签 + "查看时序"按钮 | 值在超表，不可编辑 |

### 7.3 CreateObjectWizard 适配

- Step 1 数据集选择：时序属性需要数据源是 Kafka（流式链路）
- Step 2 属性映射：GEOTEMPORAL_SERIES 映射到 Kafka topic 的 series_id 字段
- Step 3 完成时提示"时序数据需通过 timeseries-sync 接入"

---

## 八、API 层（api/graph.ts，新增）

```typescript
// 推理线
export async function queryDataFrame(ontology: string, ir: ObjectSetIR): Promise<ReasoningResult>;
export async function queryNL(ontology: string, question: string): Promise<ReasoningResult>;
export async function traverseLink(ontology: string, req: TraverseReq): Promise<TraverseResult>;
export async function existsLink(ontology: string, req: ExistsReq): Promise<ExistsResult>;
export async function getAnalysis(ontology: string, id: string): Promise<AnalysisRecord>;
export async function startTimeseriesSync(ds: string, req: TimeseriesSyncReq): Promise<SyncTask>;

// 类型
interface ObjectSetIR { type: 'objectType'|'static'|'filter'|'searchAround'; ... }
interface ReasoningResult { objects: GraphObject[]; truncated: boolean; next_cursor?: string; stats: {...}; evidence_id?: string; }
interface GraphObject { rid: string; api_name: string; props: Record<string, any>; }
```

---

## 九、状态管理（hooks/useGraphExplore）

```typescript
function useGraphExplore(ontology: string) {
  // 画布元素（增量管理）
  const nodes: Map<rid, GraphNode>;
  const edges: Map<edgeKey, GraphEdge>;
  // 选中态
  const selectedVid: string | null;
  // 撤销栈（Search Around 可撤销）
  const undoStack: HistoryEntry[];
  // LOD 折叠态
  const collapsedGroups: Set<objectType>;
  // 图层样式
  const layerStyle: { colorBy: 'type'|'property'; colorProp?: string; ... };
  // 操作
  async function searchAround(rid: string, linkType: string, direction: 'out'|'in'|'both');
  async function loadStartSet(objectType: string, filters?: Filter[]);
  async function spatialFilter(vids: string[], spatial: SpatialFilter);
  function undo(); function redo();
  function saveAsEvidence(ir: ObjectSetIR);
}
```

---

## 十、分期实施路线

### 10.1 Phase 2a（MVP 可用）—— 图探索核心

| 任务 | 优先级 | 依赖 |
|---|---|---|
| api/graph.ts + types 补充 | P0 | 后端 API 已就绪 |
| GraphExplorePage + GraphCanvas（Cytoscape 对象图） | P0 | cytoscapeExtensions 复用 |
| 起手搜索 + 右键 Search Around（traverse_link） | P0 | api/graph.ts |
| SelectionSidebar（复用 ObjectDetailPanel） | P0 | 现有组件 |
| LOD 折叠 + 截断提示 | P1 | C9 防线 |
| EvidenceDrawer（GET analysis） | P1 | 后端 M6 |
| Layout RAIL_ITEMS 加"图探索" | P0 | |

### 10.2 Phase 2b（空间时空）

| 任务 | 优先级 | 依赖 |
|---|---|---|
| MapPanel（MapLibre）+ marker 渲染 | P1 | GEOPOINT 数据 |
| 框选/圈选/多边形空间过滤 | P1 | spatial_filter API |
| 图谱/地图分屏联动 | P2 | |
| TrajectoryPlayer（轨迹回放） | P2 | series_query API |
| 时间轴 scrubbing | P2 | |

### 10.3 Phase 2c（高级）

| 任务 | 优先级 | 依赖 |
|---|---|---|
| ObjectSetBuilder（IR 构建器） | P2 | 后端 query-dataframe |
| Layers tab（着色/大小自定义） | P2 | |
| Histogram tab（属性分布筛选） | P2 | |
| 对话结果"在画布打开"联动 | P2 | AG-UI toolset |
| 新 DataType 属性编辑器适配 | P1 | types 补充 |

### 10.4 Phase 2d（二期任务，总设计 §12.2 剩余）

| 任务 | 说明 |
|---|---|
| 全链路血缘审计 | lineage 体系 + 审计包导出（后端 + 前端） |
| 实体对齐 | 手动合并 + 自动对齐(ML) |
| find_paths（路径推理） | 最短路径/全路径/权重（后端 + 前端） |
| ObjectSet 集合运算/KNN | union/intersect/subtract/nearestNeighbors |

---

## 十一、验收指标

### 11.1 功能验收

| # | 场景 | 验收 |
|---|---|---|
| F1 | 起手搜索 | 选 ObjectType + filter → 画布显示对象节点 |
| F2 | Search Around | 右键节点 → 选 link → 增量加边，不重排 |
| F3 | 选中详情 | 点节点 → 侧栏显示全量属性 |
| F4 | 证据链 | "存证据" → EvidenceDrawer 显示 IR + 各步耗时 + 命中对象 |
| F5 | 地图视图 | 切地图 → GEOPOINT 对象渲染为 marker |
| F6 | 空间过滤 | 地图框选 → 命中对象高亮（图谱同步） |
| F7 | 轨迹回放 | 选时序对象 → 播放器 → 轨迹线随时间移动 |
| F8 | NL 驱动 | 对话问"X关联的所有Y" → 画布加载结果 |
| F9 | LOD 折叠 | >500 节点 → 同类型聚合超级节点 |
| F10 | 截断续取 | truncated → "加载更多" → cursor 续取 |

### 11.2 性能验收

| 指标 | 目标 |
|---|---|
| 画布首屏（100 节点） | < 1s |
| Search Around 增量加边（50 新节点） | < 500ms（不含网络） |
| 1000 节点交互流畅度 | > 30fps（LOD 折叠后） |
| 地图 marker 渲染（500 点） | < 1s |

### 11.3 可靠性

| 场景 | 处理 |
|---|---|
| Neo4j 宕机 | 画布报错提示"图引擎不可用"，对话 SQL 线不受影响 |
| 空结果 | EmptyState "未找到对象" + 建议调整过滤 |
| 截断 | 底栏提示 + 加载更多 |

---

## 十二、风险与反模式

| 风险 | 缓解 |
|---|---|
| Cytoscape 大图性能 | LOD 折叠 + fcose→cose 切换 + 增量加边 |
| MapLibre 瓦片源（国内访问 OSM 慢） | 可配置瓦片源（默认 OSM raster，支持自建） |
| 轨迹回放大数据量 | series_query 分页 + 抽样（后端 limit） |
| IR 构建器复杂度 | 渐进式披露，默认折叠，高级用户展开 |
| 图谱/地图状态同步 | 单一 useGraphExplore 状态源，视图订阅 |

**反模式审查**：
- [ ] 不重造图谱轮子（复用 Cytoscape + OntologyGraph 模式）
- [ ] 不把 IR 构建器作为主入口（NL/右键探索为主）
- [ ] 不全量重渲染画布（增量 diff + cy.add）
- [ ] 不在前端做复杂过滤逻辑（下推后端）
- [ ] 不存 token/密码（地图瓦片 token 走后端代理）

---

## 附录：Palantir Vertex 交互对照

| Vertex 交互 | Gaia 二期对应 |
|---|---|
| Search Around（右键扩展） | 右键 cxtmenu → traverse_link |
| Selection tab（属性） | SelectionSidebar（复用 ObjectDetailPanel） |
| Layers tab（样式） | LayersPanel（着色/大小） |
| Histogram tab（筛选） | HistogramPanel（属性分布） |
| 时间选择面板（scrubbing） | TrajectoryPlayer 时间轴 |
| Events badge | 节点 badge（status/risk 着色） |
| Filter to/out | Histogram 框选 + 顶栏 chip |
| Saved styles | localStorage（二期不入库） |
