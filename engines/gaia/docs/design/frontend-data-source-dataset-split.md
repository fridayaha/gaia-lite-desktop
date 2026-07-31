# Gaia 数据层前端设计 v2 — 数据源 / 数据集页面拆分

> 版本：v2.0 | 日期：2026-06-28
> 前序：[frontend-data-layer-design.md](./frontend-data-layer-design.md) v1.0（`/data` 单页混排）
> 对标：Palantir Foundry — Data Connections（数据连接）与 Projects & Files（数据集）分属两个独立应用
> 驱动原则：CLAUDE.md 第一原则（把复杂留给自己，把简单留给用户）、第二原则（组件复用）、第三原则（先走通再完美）、第四原则（质量守则）

---

## 一、背景与问题

### 1.1 v1 现状

`/data` 单页（`DataConnections.tsx`）混合渲染两类治理对象：

```
/data
├── 数据源卡片列表（DataSourceCard[] + 内嵌 SyncTaskCard[]）
└── 数据集分区（紧凑行列表，格式塔封闭性分区）
```

详情层：`/data/sources/:name`、`/data/datasets/:name`、`/data/syncs/:name` 已独立。

### 1.2 为什么要拆

参考 Palantir Foundry 官方设计：**Data Connections 与 Dataset 是两个独立应用、两套微服务、两类管控维度**：

| 维度 | 数据源（Data Connection） | 数据集（Dataset） |
|------|--------------------------|-------------------|
| 心智 | "平台能否连通外部系统" | "平台内已落地的数据长什么样" |
| 管控对象 | Connector / Credential / Runtime / Sync | Storage / Transaction / Schema / Lineage / Retention |
| 生命周期 | 连通性测试、同步规则、凭证轮换 | 版本快照、Schema 漂移、冷热归档、行列权限 |
| 用户角色 | 接入工程师（DBA / 数据工程师） | 数据消费方（分析师 / 本体建模者） |

混排的代价：
1. 用户无法判断"我该在哪管理这个对象"，认知负担高（违反第一原则）
2. 数据源列表被数据集分区挤压，扫描效率低
3. 数据集未来加版本/权限/TTL 后，单页会膨胀失控

### 1.3 拆分目标

将 `/data` 单页拆为**侧边栏二级导航下两个独立页面**，物理隔离 + 业务跳转闭环，后端零改动。

---

## 二、信息架构

### 2.1 侧边栏二级导航

Rail「数据」由单条目改为**可展开分组**（`Disclosure` 原语承载，记忆展开态）：

```
🔗 数据
   ├─ 数据源      /data/sources
   └─ 数据集      /data/datasets
```

- 默认展开（数据是核心工作区）
- active 高亮：当前所在子页加 `.active`；父项「数据」常亮
- 折叠态下父项点击在两个子页间切换（或落到默认 `/data/sources`）

### 2.2 路由表

| 路径 | 页面组件 | 状态 | 说明 |
|------|---------|------|------|
| `/data` | — | **删除** | 开发期无外部用户，不做重定向，直接移除 |
| `/data/sources` | 🆕 `DataSourcesPage` | 新建 | 数据源列表 |
| `/data/sources/:name` | `DataSourceDetail` | 沿用 | 路径不变 |
| `/data/datasets` | 🆕 `DatasetsPage` | 新建 | 数据集列表 |
| `/data/datasets/:name` | `DatasetDetail` | 沿用 | 路径不变 |
| `/data/syncs/:name` | `SyncTaskDetail` | 沿用 | 路径不变（Sync 属 Source 侧） |

> 决策记录：`/data` 不保留聚合页、不做重定向（开发期，无外部用户依赖）。

### 2.3 两页交叉互通（对齐 Palantir「业务跳转闭环」）

两页物理隔离，通过业务跳转闭环：

| 起点 | 终点 | 实现 |
|------|------|------|
| 数据源详情 · 创建同步 | 指定目标数据集 | `SyncModeSelector` 的「目标」字段：文本输入 → 升级为 `ComboBox` 数据集选择器（P1） |
| 数据源详情 · SyncTaskCard | 目标数据集详情 | ✅ 已实现：`target_dataset_api_name` 可点击跳 `/data/datasets/:name` |
| 数据集详情 · 概览 | 来源数据源 | ✅ 已有跳转，面包屑 `to` 从 `/data` 改 `/data/sources` |
| 数据集详情 · 概览 | 加工来源数据集 | ✅ 已有跳转 `/data/datasets/:name` |
| 数据集列表 · 来源列 | 数据源 / 上游数据集 | 🆕 行内可点击链接跳转 |
| 数据集详情 · 血缘 | 上游 Source/Sync | ⏸ 占位，待 Gravitino lineage 接通 |

---

## 三、数据源页设计（`/data/sources` → `DataSourcesPage`）

### 3.1 心智定位

**"管理平台与外部系统的连通"** — 只关心：连得上吗？同步规则是什么？凭证安全吗？**不关心落地数据长什么样**。

### 3.2 页面结构

```
┌─ PageHeader ─────────────────────────────────────────────┐
│  数据源管理              [🔍 搜索] [连接器类型▾] [+ 添加]  │
│  连接外部数据系统，创建同步任务，将数据导入平台             │
├─ ListFilterBar（搜索 + 连接器 chips + 状态 + 密度切换）───┤
├─ 列表区 ─────────────────────────────────────────────────┤
│  ┌─ DataSourceCard ───────────────────────────────────┐  │
│  │ 📡 display_name          [CONNECTED] [mysql]       │  │
│  │    api_name · Gravitino: xxx                       │  │
│  │    同步任务 3 · 最近运行 2h前                        │  │
│  │    [浏览Schema] [创建同步] [测试连接] [⋯]           │  │
│  └────────────────────────────────────────────────────┘  │
│  ...                                                     │
├─ EmptyState（空）─ 📡 暂无数据源 ────────────────────────┤
├─ ErrorState（错）─ ⚠ 加载失败 [重试] ───────────────────┤
└─ LoadingState（载）─ SkeletonList ───────────────────────┘
```

### 3.3 筛选维度（`ListFilterBar`）

- **搜索**：display_name + api_name + connector_type（防抖 200ms）
- **连接器类型** chips：mysql / postgresql / s3 / kafka / ...（多选，从 `CAPABILITY_MAP` 推导可选集 + 实际数据补充）
- **状态**：CONNECTED / DISCONNECTED / ERROR（单选下拉）
- **密度切换**：舒适 / 紧凑（影响卡片内边距）

### 3.4 卡片操作

复用现有 `DataSourceCard`（已支持 `expanded`）。列表态操作按钮（不必进详情即可触发）：
- 浏览 Schema → 跳详情 `explore` Tab
- 创建同步 → 跳详情 `sync` Tab
- 测试连接 → 列表内 inline 调 `testConnection`，按钮态 loading + 结果 toast
- ⋯ 菜单 → 编辑 / 删除（删除走 `ConfirmDialog` + `analyzeImpact`，高危输名称）

### 3.5 数据加载

- `listDataSources()` 一次拉取
- 每个数据源的同步任务概览：并行 `listSyncTasks(ds.api_name)`，失败兜底为空数组（沿用 v1 逻辑）
- 不在此页加载数据集（已拆走）

### 3.6 不做的事（第三原则）

- 不加 Runtime/Agent 选择（项目当前 Direct 模式为主，无 Agent 架构）
- 不加网络策略/Egress/IP 白名单配置（基础设施层）
- 不加凭证独立管理 UI（凭证随数据源配置，`Credential` API 已有但前端暂不单独入口）

---

## 四、数据集页设计（`/data/datasets` → `DatasetsPage`）

### 4.1 心智定位

**"管理平台内已落地的数据"** — 关心：数据在哪？Schema 是什么？多少行？来源是什么？是不是虚拟表？

### 4.2 页面结构

数据集天然适合**表格视图**（治理维度多、信息密度高），用 `DataTable` 原语：

```
┌─ PageHeader ─────────────────────────────────────────────┐
│  数据集管理              [🔍 搜索] [类型▾] [来源▾] [登记] │
│  平台内已落地数据的治理元数据：存储、Schema、来源、血缘     │
├─ ListFilterBar ──────────────────────────────────────────┤
├─ DataTable ──────────────────────────────────────────────┤
│  名称            类型       来源            行数    更新  │
│  📦 erp_order    托管表     sync:erp_db    128k   2h前 →│
│  📦 erp_inv      托管表     sync:erp_db    1.2M   1h前 →│
│  🔗 ext_crm      虚拟表    ds:crm          —      —   →│
│  📦 order_clean  加工       transform      125k   3h前 →│
├─ EmptyState ─ 📦 暂无数据集 ─────────────────────────────┤
└──────────────────────────────────────────────────────────┘
```

### 4.3 列定义

| 列 | 字段 | 渲染 | 可点击跳转 |
|----|------|------|-----------|
| 名称 | `display_name \|\| api_name` | 图标 + 名称 + api_name 小字 | → `/data/datasets/:name` |
| 类型 | `kind` + `is_view` | `StatusBadge`：托管表 / 虚拟表 | — |
| 来源 | `data_source_api_name` / `source_dataset_api_name` | 统一渲染为链接 | → 数据源页 / 上游数据集详情 |
| 行数 | `row_count_estimate` | `toLocaleString()`，null 显示 `—` | — |
| 更新 | `updated_at` | 相对时间 | — |
| 操作 | — | 删除（高危确认） | — |

### 4.4 筛选维度

- **搜索**：display_name + api_name
- **类型**：托管表(MANAGED) / 虚拟表(VIRTUAL)（单选，本项目核心二分）
- **来源**：同步产出 / 加工(transform) / 手动登记 / 虚拟表（按 `source_dataset_api_name` / `data_source_api_name` / `kind` 推导分组）

### 4.5 新建入口

- **登记数据集**：`DatasetGovernanceCreate` 表单（手动登记 MANAGED，主要场景是 Sync 自动创建，手动为辅）
- 复用现有 `registerDataset` API

### 4.6 不做的事（第三原则）

- 不加分支/版本/快照列表视图（`DatasetDetail` 已有快照 Tab）
- 不加 TTL/冷热归档/行列权限 UI（后端未实现）
- 不加 Transform 加工脚本管理（当前无 Transform 服务）

---

## 五、通用组件抽取（第二原则落地）

### 5.1 🆕 `ListFilterBar`

跨两页复用的筛选栏。Props：

```ts
interface ListFilterBarProps {
  searchValue: string;
  onSearchChange: (v: string) => void;
  searchPlaceholder?: string;
  /** chips 多选筛选 */
  chips?: { label: string; value: string; count?: number }[];
  selectedChips: string[];
  onChipsChange: (vals: string[]) => void;
  /** 单选下拉筛选 */
  selects?: { label: string; value: string; options: { label: string; value: string }[]; onChange: (v: string) => void }[];
  density?: 'comfortable' | 'compact';
  onDensityChange?: (d: 'comfortable' | 'compact') => void;
}
```

- 防抖搜索内聚在组件内（200ms）
- chips 用现有按钮样式 + `.active`
- 密度切换影响下游列表（通过 context 或 callback 传递）

### 5.2 🆕 `EmptyState`

抽取通用空状态（当前各页内联）：

```ts
interface EmptyStateProps {
  icon?: string;        // emoji 或 SVG
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}
```

### 5.3 复用矩阵

| 组件 | 数据源页 | 数据集页 | 详情页 | 改动 |
|------|---------|---------|--------|------|
| `DataSourceCard` | 列表 | — | 详情头部 | 无（已支持 expanded） |
| `SyncTaskCard` | 列表概览 | — | 详情 | 无（目标数据集跳转已有） |
| `DataTable` | — | 列表 | — | 🆕 用法接入 |
| `StatusBadge` | 状态 | 类型 | — | 无 |
| `ListFilterBar` 🆕 | 筛选 | 筛选 | — | 新建 |
| `EmptyState` 🆕 | 空 | 空 | — | 新建 |
| `ConfirmDialog` + `analyzeImpact` | 删除 | 删除 | — | 无 |
| `Breadcrumb` | — | — | 详情 | `to` 路径更新 |
| `DataSourceForm` | 新建 | — | — | 无 |
| `ComboBox` (ui) | — | — | Sync 选择器 | P1 接入 |

---

## 六、交叉互通增强（P1：数据集选择器）

### 6.1 现状

`SyncModeSelector` 的「目标」字段是纯 `TextInput`，用户手输 `target_dataset`（默认 `${tableName}_raw`）。问题：
- 不知道已有哪些数据集，容易拼错
- 无法选已有数据集做增量

### 6.2 P1 改造

「目标」字段升级为 `ComboBox`（已有 ui 原语）：

```
目标: [▼ erp_order_raw        ]  [➕ 新建 erp_order_raw]
       ├ erp_order_raw (已有)
       ├ erp_inventory_raw (已有)
       └ (输入新名 → 标记为"将创建")
```

- 数据源：`listDatasets()`（数据源详情页加载时缓存）
- 选已有 → `target_dataset` = 选中 api_name，标记为"追加到已有"
- 输入新名 → 标记为"将创建"，`createSyncTasks` 内部会 `registerDataset`
- `useDataSource.createSyncTasks` 已支持自动 register，无需后端改动

### 6.3 接入点

- `SyncModeSelector.tsx`：`target_dataset` 字段从 `TextInput` 换 `ComboBox`
- `SyncConfigPanel.tsx` / `ExplorerView.tsx`：透传 `existingDatasets` prop
- `DataSourceDetail.tsx`：加载时 `listDatasets()` 并下传

---

## 七、导航组件改造（`Layout.tsx`）

### 7.1 Rail 分组

`RAIL_ITEMS` 的 `data` 项改为带子项结构：

```ts
interface RailItem {
  id: string;
  label: string;
  shortLabel: string;
  icon: string;
  hint: string;
  path: string;
  children?: RailItem[];  // 🆕
}
```

`data` 项：
```ts
{
  id: 'data', label: '数据对接', shortLabel: '数据', icon: '🔗',
  path: '/data/sources',  // 默认落点改为 sources
  children: [
    { id: 'data-sources', label: '数据源', shortLabel: '源', icon: '📡', hint: '外部系统连接', path: '/data/sources' },
    { id: 'data-datasets', label: '数据集', shortLabel: '集', icon: '📦', hint: '已落地数据治理', path: '/data/datasets' },
  ],
}
```

### 7.2 渲染

- 折叠态（Rail 收起）：父项点击 → 落 `path`（`/data/sources`）
- 展开态：父项下方展开子项（`Disclosure` 原语），子项点击导航
- active 判断：`currentPanel` 仍按一级匹配；子项 active 按 `pathname === child.path || pathname.startsWith(child.path + '/')`
- 默认展开数据分组（核心工作区）

### 7.3 面包屑 `resolveCrumb`

更新 `/data/sources` → `['数据对接', '数据源']`，`/data/datasets` → `['数据对接', '数据集']`（列表页也显示面包屑，原来是空）。

---

## 八、迁移影响

### 8.1 后端

**零改动**。所有 API（`/datasources`、`/datasets`、`/sync-tasks`、`/credentials`、`/impact-analysis`）已完整。

### 8.2 前端改动清单

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `App.tsx` | 新增 `/data/sources`、`/data/datasets` 路由；移除 `/data` | P0 |
| `Layout.tsx` | Rail `data` 改分组；`resolveCrumb` 更新 | P0 |
| 🆕 `pages/DataSourcesPage.tsx` | 从 `DataConnections.tsx` 抽数据源部分 + `ListFilterBar` | P0 |
| 🆕 `pages/DatasetsPage.tsx` | 从 `DataConnections.tsx` 抽数据集部分 + `DataTable` + `ListFilterBar` | P0 |
| 🆕 `components/ListFilterBar.tsx` | 通用筛选栏 | P0 |
| 🆕 `components/EmptyState.tsx` | 通用空状态 | P0 |
| `pages/DatasetDetail.tsx` | 面包屑 `to` 改 `/data/datasets` | P0 |
| `pages/DataSourceDetail.tsx` | 面包屑/返回路径对齐 | P0 |
| `pages/SyncTaskDetail.tsx` | 面包屑对齐 | P0 |
| `components/SyncModeSelector.tsx` | 「目标」字段 `TextInput` → `ComboBox` | P1 |
| `components/SyncConfigPanel.tsx` | 透传 `existingDatasets` | P1 |
| `pages/DataSourceDetail.tsx` | 加载 `listDatasets()` 下传 | P1 |
| 删除 `pages/DataConnections.tsx` | 内容已拆分 | P0 |

### 8.3 风险与缓解

| 风险 | 缓解 |
|------|------|
| `DataTable` 列定义与现有样式体系对齐 | 复用 `data-table` class + `StatusBadge` |
| `ComboBox` 数据集选择器异步加载态 | 选择器内部 loading/empty 状态（第四原则） |
| Rail 分组展开态记忆 | localStorage 持久化展开状态 |
| `DataConnections.tsx` 删除后测试失效 | 迁移测试到 `DataSourcesPage` / `DatasetsPage` |

---

## 九、质量守则检查（第四原则）

每页交付前必须通过：

- [ ] 四种状态：loading（Skeleton）/ empty（EmptyState）/ error（重试）/ 正常
- [ ] 网络请求失败有可读反馈（toast + 内联错误），不静默
- [ ] 任何操作 100ms 内有反馈（按钮 loading / 禁用）
- [ ] 键盘可达：Tab 导航、Enter 确认、Esc 关闭弹窗
- [ ] 大列表性能：数据集列表行数 > 200 时虚拟滚动（`DataTable` 是否内置待确认，否则预留）
- [ ] 删除分级确认：LOW 弹窗 / MEDIUM 列影响 / HIGH 输名称（沿用 `ConfirmDialog`）
- [ ] 用户输入转义（React 默认安全，无 `dangerouslySetInnerHTML`）
- [ ] 提交前 `npm run build` 通过（tsc + oxc）

---

## 十、交付范围（本次一并实施）

按用户要求，P0 + P1 一并实施：

**P0 — 骨架拆分**
1. `App.tsx` 路由 + `Layout.tsx` Rail 分组
2. `DataSourcesPage` + `DatasetsPage`
3. `ListFilterBar` + `EmptyState` 通用组件
4. 面包屑/路径对齐
5. 删除 `DataConnections.tsx`

**P1 — 交叉互通增强**
6. `SyncModeSelector` 目标字段 → 数据集选择器（datalist）
7. `DataSourceDetail` 加载并下传已有数据集

**不做**：分支/版本/TTL/血缘图/Transform（依赖后端，远期）。

---

## 十一、迭代修订（2026-06-28 评审反馈）

### 11.1 Rail 父子层级区分度（问题 1）

**问题**：48px 窄 Rail 中，父项（业务/数据/能力/运营）与子项（数据源/数据集）
仅靠尺寸略小区分，无缩进、无连接线、无容器，用户无法感知父子从属关系。

**依据**（NNGroup《Vertical Navigation》+ Material/Salt 设计系统）：
- 缩进是最强的层级信号
- 连接线/浅背景容器包裹子项组
- 图标与字号递减（父 16px/9px → 子 13px/8px）
- active 指示器父子区分
- 父项展开时保持浅高亮，表明「当前组已展开」

**方案**（`Layout.tsx` + `index.css`）：
- 子项左侧加连接线（`::before` 竒线，从父项延伸）
- 子项组用浅背景容器包裹（`rail-subitems` 加背景/边框）
- 父项展开态：`.rail-btn` 加 `.group-expanded` 浅高亮
- active 指示器区分：父用左侧 3px 粗条；子用左侧 2px 细条 + 圆点
- 父项展开箭头旋转 + 变色

### 11.2 「登记数据集」语义重定位（问题 2）

**问题根因**：后端 `register_dataset` 仅在 PG 写治理元数据记录，
**不创建 Iceberg 物理表、不落地数据**。而 Palantir 的「手动创建数据集」
是上传文件落地存储（Compass 应用 manually-upload-data）。本项目当前
无文件上传落地能力，故「登记数据集」表单会产出**空壳数据集**
（查 schema 为空、无数据可查），违背第一原则（给用户看似可用实则无用的东西）。

**语义错位表现**：
1. `storage_location` 字段对用户无意义（MANAGED 类型由系统在 Sync 时自动生成，
   手填会与系统不一致）
2. 登记后 Iceberg 表不存在，`get_dataset_schema` 返回空，无业务价值
3. 与「数据集主要由 Sync 自动创建」的真实架构脱节

**方案**：移除「登记数据集」按钮与弹窗（第三原则：先走通，不为不存在的场景造 UI）。
- 空状态改为引导式：「通过数据源同步生成数据集」+ 跳转按钮到 `/data/sources`
- 列表页顶部不再有创建按钮（数据集是 Sync 的产出，不是手动创建的对象）
- P2 预留：待文件上传落地能力 / 虚拟表独立入口成熟后补回（虚拟表登记已存在于
  数据源详情页 `RegisterVirtualTableDialog`，是正确的位置）

**保留场景**：「登记已有 Iceberg 表为治理对象」（手动建过的 Iceberg 表纳入治理）
是真实需求，但需后端支持「按表名挂载元数据且不重复建表」，列为 P2 后端任务，
前端不提前造 UI。
