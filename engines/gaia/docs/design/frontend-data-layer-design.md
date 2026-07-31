# Gaia 数据层前端设计 — 组件树 & 交互流

> 版本：v1.0 | 对标：Palantir Data Connection → Syncs → Dataset
> 核心原则：组件复用最大化（CLAUDE.md 第二原则）
>
> ⚠️ **已被 [frontend-data-source-dataset-split.md](./frontend-data-source-dataset-split.md) v2 演进**：
> 本文档 `/data` 单页混排设计已拆分为 `/data/sources`（数据源）+ `/data/datasets`（数据集）两个独立页面。
> 本文档保留作组件树与复用矩阵参考，路由与页面结构以 v2 为准。

---

## 一、页面路由与整体导航

```
Rail (左侧导航栏)
├── 🧠 业务定义  →  /ontology      (已有)
├── 🔗 数据对接  →  /data           (本次重点)
├── ⚡ 能力赋予  →  /actions        (已有)
└── 📊 运行洞察  →  /ops            (已有)

/data 子路由:
  /data                        DataSourceListPage      数据源列表
  /data/sources/:name           DataSourceDetailPage    数据源详情（探索/同步/状态）
  /data/syncs/:name             SyncTaskDetailPage      同步任务详情
  /data/datasets/:name          DatasetDetailPage       Dataset 详情
```

---

## 二、完整组件树

```
DataSourceListPage
├── PageHeader
│   ├── 标题 + 副标题
│   └── 操作按钮: [ + 添加数据源 ]
├── DataSourceForm (Modal)               ← 复用组件
│   ├── ConnectorTypePicker
│   └── ConnectionConfigForm
└── DataSourceCard[]                     ← 新抽象组件
    ├── DataSourceCardHeader
    │   ├── CapabilityBar                ← 复用组件 ★
    │   │   └── 根据 capabilities 渲染按钮: [探索] [同步] [CDC] [登记虚拟表]
    │   └── StatusBadge                  ← 复用组件 ★
    ├── (可展开区域)
    │   ├── ExplorerView                 ← 复用组件 ★★★（最重要）
    │   │   ├── SearchBar                ← 复用组件
    │   │   ├── SchemaTreeBrowser        ← 复用组件 ★★
    │   │   │   └── SchemaNode → TableNode → ColumnList
    │   │   │       └── ColumnList       ← 复用组件 ★★（也用于 ObjectType 属性映射）
    │   │   ├── PreviewTable             ← 复用组件 ★
    │   │   └── SyncConfigPanel (右侧栏)
    │   │       ├── SelectedTableList    ← 已选表管理
    │   │       │   └── SyncModeSelector ← 复用组件（也用于 SyncTask 编辑）
    │   │       └── 操作: [一键创建同步(3)] [清空]
    │   └── SyncTaskList
    │       └── SyncTaskCard[]           ← 新抽象组件
    │           └── SyncTaskCardHeader
    │               ├── StatusBadge
    │               ├── 模式/目标摘要
    │               └── 操作: [启动] [停止] [编辑] [删除]

DataSourceDetailPage (独立页, 路由到 /data/sources/:name)
├── Breadcrumb: 数据源 > {name}
├── DataSourceCard (无展开)
└── DataSourceTabs
    ├── Tab: 探索  → ExplorerView
    ├── Tab: 同步  → SyncTaskList (完整版, 含创建)
    ├── Tab: 状态  → SyncRunsTimeline
    └── Tab: 设置  → DataSourceEditForm

SyncTaskDetailPage (/data/syncs/:name)
├── Breadcrumb: 数据源 > {source} > {task}
├── SyncTaskHeader
│   ├── StatusBadge
│   └── 操作: [启动/停止] [立即运行] [编辑] [删除]
├── SyncTaskTabs
│   ├── Tab: 概览     → SyncTaskOverview
│   │   ├── SyncConfigSummary (模式/增量字段/事务类型/调度)
│   │   ├── TargetDatasetLink → /data/datasets/:name
│   │   └── PreviewTable (抽样 20 行)
│   ├── Tab: 运行历史 → RunHistoryTable
│   │   └── RunRow: 时间/状态/行数/耗时/错误
│   └── Tab: 配置     → SyncTaskEditForm
│       └── SyncModeSelector / ScheduleForm / AllowSchemaChanges

DatasetDetailPage (/data/datasets/:name)
├── Breadcrumb: 数据集 > {name}
├── DatasetHeader
│   ├── 名称/来源/行数估算
│   └── 操作: [浏览 Schema] [快照历史]
├── DatasetTabs
│   ├── Tab: Schema → ColumnList（表格模式，带 Iceberg 物理类型）
│   ├── Tab: 快照   → SnapshotTimeline
│   └── Tab: 血缘   → LineageGraph（P2）
```

---

## 三、核心复用组件设计

### 3.1 CapabilityBar — 能力驱动按钮组

```
复用场景: DataSourceCard, DataSourceDetailPage
输入:     DataSource.capabilities
输出:     按钮组

"explore"     → [📋 探索 Schema]
"batch_sync"  → [🔄 新建同步]
"cdc"         → [⚡ CDC 同步]
"virtual_table" → [👻 登记虚拟表]
> 登记后产生 kind=VIRTUAL 的 DatasetGovernance，可被 VIRTUAL 对象绑定。见 [dataset-ontology-binding.md](./dataset-ontology-binding.md) §3.2/§4.2。
```

```tsx
// components/CapabilityBar.tsx
interface CapabilityBarProps {
  capabilities: string[];
  onExplore?: () => void;
  onCreateSync?: () => void;
  onCreateCdc?: () => void;
  onCreateVirtualTable?: () => void;
}
```

### 3.2 SchemaTreeBrowser — Schema 树浏览器（★★★ 最重要）

```
复用场景:
  1. ExplorerView（探索面板）
  2. DatasetSchemaView（Dataset 详情）
  3. ObjectType 数据映射时选择列（PropertyDef.physical_mapping）

输入:  TableInfo[] (来自 explore API 或 Dataset API)
功能:  搜索过滤、展开/折叠、勾选多表、点击表名展示列详情
输出:  选中表列表 + 选中列的回调
```

```tsx
// components/SchemaTreeBrowser.tsx
interface SchemaTreeBrowserProps {
  schemas: SchemaNode[];          // Schema → Table → Column 树
  searchable?: boolean;
  selectable?: boolean;           // 是否支持勾选（探索场景=true, Dataset查看=false）
  selectedTables?: string[];      // 已选表名（用于外部状态同步）
  onTableSelect?: (tableName: string) => void;
  onTableDeselect?: (tableName: string) => void;
  onTableClick?: (tableName: string) => void;  // 点击展开列详情
  selectedTable?: string | null;   // 当前展开的表
}
```

**视觉结构**：
```
┌─ SchemaTreeBrowser ─────────────────────┐
│ 🔍 [搜索表名_______________]             │
│                                         │
│ 📁 public              (12 张表)        │
│   ├── ☑ demo_user         6 列  33 B/s│
│   │   ├── user_id   bigint   PK        │
│   │   ├── name      varchar            │
│   │   ├── email     varchar            │
│   │   ├── phone     varchar            │
│   │   ├── region    varchar            │
│   │   └── level     varchar            │
│   ├── ☐ demo_order        9 列         │
│   ├── ☐ branches          7 列         │
│   └── ...                              │
└─────────────────────────────────────────┘
```

### 3.3 ColumnList — 列详情列表（★★）

```
复用场景:
  1. SchemaTreeBrowser 中展开表的列详情
  2. SyncTaskDetail → Schema 查看
  3. DatasetDetail → Schema Tab
  4. ObjectType 编辑 → 数据映射时查看源列
  5. SyncConfigPanel → 选择增量列

输入: ColumnInfo[]
输出: 渲染列名/类型/约束标记
```

```tsx
// components/ColumnList.tsx
interface ColumnListProps {
  columns: ColumnInfo[];
  compact?: boolean;               // 紧凑模式（树内展示）
  selectable?: boolean;            // 是否可选择单列（增量字段选择场景）
  selectedColumn?: string | null;
  onColumnSelect?: (columnName: string) => void;
  highlightFK?: boolean;           // 高亮 FK 列
  fkTargets?: Record<string, string>;  // column → target table
}
```

**视觉**：
```
compact=true（树内）:
  user_id   bigint   PK  ──→ demo_user   ← FK 高亮
  name      varchar
  email     varchar

compact=false（独立面板）:
┌──────┬──────────┬──────┬──────┬────────┐
│ 列名  │ 类型     │ NULL │ 主键 │ 说明   │
├──────┼──────────┼──────┼──────┼────────┤
│ user_id│ bigint  │ NO   │ ✓   │        │
│ name  │ varchar  │ NO   │     │        │
└──────┴──────────┴──────┴──────┴────────┘
```

### 3.4 PreviewTable — 数据抽样预览

```
复用场景:
  1. ExplorerView → 点击表名后展示前 20 行
  2. SyncTaskDetail → 概览 Tab 预览
  3. DatasetDetail → 数据预览

输入: columns + rows (来自 sample_data API)
```

```tsx
// components/PreviewTable.tsx
interface PreviewTableProps {
  columns: string[];
  rows: Record<string, unknown>[];
  maxRows?: number;
  loading?: boolean;
}
```

### 3.5 SyncModeSelector — 同步模式选择器

```
复用场景:
  1. SyncConfigPanel → 每张选中表的同步模式配置
  2. SyncTaskEditForm → 编辑已有同步任务
  3. SyncTaskCreate (modal)

输入: 表名 + 列信息
输出: sync_mode / transaction_type / incremental_column
```

```tsx
// components/SyncModeSelector.tsx
interface SyncModeSelectorProps {
  tableName: string;
  columns: ColumnInfo[];
  value: SyncConfig;
  onChange: (config: SyncConfig) => void;
}

interface SyncConfig {
  sync_mode: 'full_snapshot' | 'incremental';
  transaction_type: 'snapshot' | 'append';
  incremental_column: string | null;
  target_dataset: string;
}
```

### 3.6 StatusBadge — 统一状态标记

```
复用场景: 所有页面（已有类似但不统一）
状态: CONNECTED / ERROR / DISCONNECTED / RUNNING / FAILED / DRAFT / STOPPED
```

### 3.7 ConfirmDialog — 分级确认弹窗

```
复用场景: 所有删除/危险操作
输入: severity(LOW/MEDIUM/HIGH) + ImpactItem[]
输出: 确认/取消

LOW     → "确定要删除吗？" + [确定] [取消]
MEDIUM  → 列出影响的资源 + [确定] [取消]
HIGH    → "请输入 {name} 确认删除" + 输入框 + [确认删除]
```

---

## 四、页面交互流设计

### 4.1 DataSourceListPage (/data)

```
┌─ 数据源管理 ──────────────────────────────────────┐
│ 连接外部数据系统，创建同步任务，将数据导入平台        │
│                                    [+ 添加数据源]  │
├───────────────────────────────────────────────────┤
│                                                    │
│ ┌─ 🐘 PostgreSQL Demo ──────────────────────────┐ │
│ │  ○ 已连接  demo_pg  POSTGRESQL                 │ │
│ │  postgres:5432/ontology                        │ │
│ │  [📋 探索] [🔄 新建同步] [⚡ CDC]  [测试] [删除] │ │  ← CapabilityBar
│ │                                                │ │
│ │  (展开时)                                       │ │
│ │  ┌─ ExplorerView ─────────────────────────────┐ │ │
│ │  │ SchemaTreeBrowser │ SyncConfigPanel       │ │ │
│ │  └───────────────────────────────────────────┘ │ │
│ │                                                │ │
│ │  同步任务 (2)                        [+ 添加]   │ │
│ │  ┌ sync_orders──────────────────────────────┐ │ │
│ │  │ ● 运行中  增量 → orders_raw               │ │ │
│ │  │ 上次: 14:30   [停止] [编辑] [删除]        │ │ │
│ │  └──────────────────────────────────────────┘ │ │
│ └───────────────────────────────────────────────┘ │
│                                                    │
│ ┌─ 🐬 MySQL ERP ────────────────────────────────┐ │
│ │  ○ 已连接  erp_mysql_prod  MYSQL               │ │
│ │  [📋 探索] [🔄 新建同步]  [测试] [删除]          │ │  ← 无 CDC（MySQL connector_type 判断）
│ └───────────────────────────────────────────────┘ │
│                                                    │
│ ┌─ 🪣 S3 Logs ──────────────────────────────────┐ │
│ │  ○ 已连接  s3_logs  S3                         │ │
│ │  [📋 探索] [📁 文件同步]  [测试] [删除]          │ │  ← 文件同步（S3 connector_type 判断）
│ └───────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
```

### 4.2 ExplorerView（探索面板）

```
┌─ ExplorerView ──────────────────────────────────────────────┐
│                                                              │
│  ┌─ SchemaTreeBrowser ───┬── SyncConfigPanel ────────────┐ │
│  │                        │                               │ │
│  │ 🔍 [搜索表]            │  已选表 (3)     [一键创建同步] │ │
│  │                        │                               │ │
│  │ 📁 public              │  ┌ demo_user ──────────────┐ │ │
│  │  ├ ☑ demo_user        │  │ ○ 全量快照  [▾]          │ │ │
│  │  │  ├ user_id  bigint  │  │ ○ 增量/updated_at [▾]   │ │ │
│  │  │  ├ name    varchar  │  │ 目标: users_raw         │ │ │
│  │  │  └ ...              │  └─────────────────────────┘ │ │
│  │  ├ ☑ demo_order        │                               │ │
│  │  │  ├ order_id bigint  │  ┌ demo_order ────────────┐ │ │
│  │  │  ├ user_id  bigint  │  │ ○ 增量/updated_at [▾]  │ │ │
│  │  │  │   → FK: demo_user│  │ 目标: orders_raw        │ │ │
│  │  │  └ ...              │  └─────────────────────────┘ │ │
│  │  ├ ☐ branches          │                               │ │
│  │  └ ...                 │  ┌ branches ──────────────┐ │ │
│  │                        │  │ ○ 全量快照             │ │ │
│  │ ────────────────────── │  │ 目标: branches_raw      │ │ │
│  │  选中: demo_order      │  └─────────────────────────┘ │ │
│  │                        │                               │ │
│  │  ┌ PreviewTable ─────┐ │  [清除选择]                   │ │
│  │  │ order_id │ user_id│ │                               │ │
│  │  │ 1        │ 47     │ │                               │ │
│  │  │ 2        │ 148    │ │                               │ │
│  │  │ ...      │ ...    │ │                               │ │
│  │  │ (前 20 行)         │ │                               │ │
│  │  └────────────────────┘ │                               │ │
│  └─────────────────────────┴───────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 五、组件复用矩阵

| 复用组件 | DataSourceList | DataSourceDetail | ExplorerView | SyncConfig | SyncTaskDetail | DatasetDetail | ObjectType 编辑 |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **DataSourceCard** | ✅ 列表 | ✅ 头部 | — | — | — | — | — |
| **DataSourceForm** | ✅ Modal | — | — | — | — | — | — |
| **CapabilityBar** | ✅ 卡片内 | ✅ 顶部 | — | — | — | — | — |
| **SchemaTreeBrowser** | ✅ 展开 | ✅ Tab | ✅ 左侧 | — | — | — | ✅ P2 选列 |
| **ColumnList** | ✅ 树内 | ✅ 树内 | ✅ 树内 | — | ✅ 详情 | ✅ Schema | ✅ 映射视图 |
| **PreviewTable** | ✅ 展开 | ✅ Tab | ✅ 底部 | — | ✅ 概览 | ✅ 预览 | — |
| **SyncModeSelector** | — | — | ✅ 右侧 | ✅ | ✅ 编辑 | — | — |
| **SyncConfigPanel** | ✅ 右侧 | ✅ Tab | ✅ 右侧 | — | — | — | — |
| **SyncTaskCard** | ✅ 列表 | ✅ Tab | — | — | ✅ 头部 | — | — |
| **StatusBadge** | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ |
| **ConfirmDialog** | ✅ 删除 | ✅ 删除 | — | ✅ | ✅ | — | ✅ |
| **SearchBar** | — | ✅ | ✅ | — | — | — | ✅ |

---

## 六、与现有组件的关系

| 现有组件 | 新设计中的位置 | 变更 |
|---------|-------------|------|
| `DataSourceForm.tsx` | 保持不变，已符合 Modal 模式 | 无 |
| `ObjectTypeCard` | 参考其 card 模式 → DataSourceCard / SyncTaskCard | 模式复用，不修改 |
| `CardView` | 不直接使用，但参考其 grid 布局 | 无 |
| `TableView` | 不直接适用（探索是树形），但 ColumnList 表格模式参考 | 参考模式 |
| `CreateObjectWizard` | SyncConfigPanel 参考其多步骤/右侧面板布局 | 参考模式 |
| `ConfirmDialog` | 需要增强为分级确认 | 修改现有 |
| `AiSuggestPanel` | 探索面板的「智能推断」按钮参考 | 无 |

---

## 七、文件结构

```
src/web-ui/src/
├── components/
│   ├── DataSourceForm.tsx          ← 已有
│   ├── CapabilityBar.tsx           ← 新增
│   ├── SchemaTreeBrowser.tsx       ← 新增 ★★★
│   ├── ColumnList.tsx              ← 新增 ★★
│   ├── PreviewTable.tsx            ← 新增 ★
│   ├── SyncModeSelector.tsx        ← 新增
│   ├── SyncConfigPanel.tsx         ← 新增
│   ├── StatusBadge.tsx             ← 新增（统一已有分散的状态样式）
│   ├── ConfirmDialog.tsx           ← 增强（分级确认）
│   ├── SearchBar.tsx               ← 新增（从 TableView 抽出）
│   ├── DataSourceCard.tsx          ← 新增
│   └── SyncTaskCard.tsx            ← 新增
├── pages/
│   ├── DataConnections.tsx         ← 重写（使用新组件）
│   ├── DataSourceDetail.tsx        ← 新增
│   ├── SyncTaskDetail.tsx          ← 新增
│   └── DatasetDetail.tsx           ← 新增
├── types/
│   └── index.ts                    ← 补充新 Schema 类型
└── api/
    └── client.ts                   ← 补充新 API 方法
```

---

## 八、实现优先级

### Sprint 1：基础组件 + DataSourceListPage 重构（P0）

1. `StatusBadge` — 统一所有页面的状态样式
2. `CapabilityBar` — 驱动 DataSourceCard 的操作按钮
3. `DataSourceCard` — 抽取当前 DataConnections 中的卡片逻辑
4. `ConfirmDialog` — 增强为分级确认
5. 重写 `DataConnections` — 使用新组件

### Sprint 2：探索面板（P0）

6. `SearchBar` — 从 TableView 抽取
7. `ColumnList` — 列详情展示
8. `SchemaTreeBrowser` — Schema 树浏览器
9. `PreviewTable` — 数据抽样预览
10. `ExplorerView` — 组装探索面板

### Sprint 3：同步创建（P0）

11. `SyncModeSelector` — 同步模式选择
12. `SyncConfigPanel` — 已选表 + 批量创建
13. `SyncTaskCard` — 同步任务卡片
14. 集成到 DataConnections 的 DataSourceCard 展开区

### Sprint 4：独立详情页（P1）

15. `DataSourceDetailPage`
16. `SyncTaskDetailPage`
17. `DatasetDetailPage`
