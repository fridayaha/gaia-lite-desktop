# Gaia 前端 HCI 审视报告

> 版本：v1.0 | 审视范围：`src/web-ui` 全量前端 | 依据：人机交互（HCI）理论、尼尔森十大原则、WCAG 2.1、B 端后台交互规范
> 状态：✅ 已落地实施（见文末「实施记录」）

本文档对照 HCI 完整理论框架（认知心理学 / 行为交互 / 可用性工程 / 通用设计原则 / 落地最佳实践），逐层审视当前前端的**导航、布局、分栏、菜单、按钮、表单、弹窗、加载、图谱、异常、无障碍、一致性**，给出问题证据（文件:行）与改进方案，并在末尾给出可勾选的**前端开发校验清单**。

---

## 目录

1. [全局结论](#一全局结论)
2. [导航与布局层](#二导航与布局层米勒定律--格式塔--识别优于记忆)
3. [分栏与信息密度](#三分栏与信息密度认知负荷--格式塔邻近性)
4. [按钮与可点击组件](#四按钮与可点击组件费茨定律--反馈闭环--防错)
5. [表单与向导](#五表单与向导容错--认知负荷--米勒定律)
6. [弹窗与抽屉](#六弹窗与抽屉行动七阶段--用户可控--层级规范)
7. [加载与反馈](#七加载与反馈系统状态可见)
8. [图谱可视化交互](#八图谱可视化交互hci-第六节)
9. [无障碍 WCAG 2.1](#九无障碍-wcag-21硬性合规)
10. [一致性工程](#十一致性工程尼尔森第-4-条b-端核心)
11. [优先级排序与落地路线](#十一优先级排序与落地路线)
12. [前端开发校验清单](#十二前端开发校验清单)
13. [实施记录](#十三实施记录)

---

## 一、全局结论

当前前端在**视觉一致性、组件复用、token 体系**上扎实（两层 token、复用矩阵、分级确认弹窗、图谱工具栏），问题集中在 HCI 维度：

| 维度 | 现状 | 主要问题 |
|---|---|---|
| 导航结构 | 4 个一级 rail ✅ 符合米勒定律 | rail 纯图标无文字，违反"识别优于记忆"；titlebar 不随路由变化 |
| 分栏密度 | Workspace 三栏（280+流式+340） | 中栏无独立 max-width，信息密度过高；右栏 340px 恒占位 |
| 反馈闭环 | 有 Toast、loading、状态徽标 | 按钮无 loading 态、无骨架屏、长任务无进度、错误反馈不分级 |
| 容错 | ConfirmDialog 已分级 ✅ | 表单实时校验不足、无草稿、删除按钮与编辑按钮等权重并排（误触风险） |
| 无障碍 | 有 aria-label / aria-current | emoji 当唯一图标语义、对比度部分不足、键盘可达性未系统化 |
| 图谱交互 | 工具栏齐全 ✅ | 缺筛选联动、缺图例、右键菜单移动端不可用 |

---

## 二、导航与布局层（米勒定律 / 格式塔 / 识别优于记忆）

### 2.1 一级导航数量 ✅ 合格
`Layout.tsx` 的 `RAIL_ITEMS` 仅 4 项（业务定义/数据对接/能力赋予/运行洞察），带 ①②③④ 步骤 hint，心智模型清晰，符合米勒定律（7±2）。

### 2.2 ❌ Rail 纯图标、文字靠 hover tooltip —— 违反「识别优于记忆」
**证据** `Layout.tsx`：rail 按钮仅渲染 `{item.icon}`，文字标签藏在 `title` tooltip。
- 对新用户：🧠🔗⚡📊 含义不直白，必须 hover 才知道，增加视觉 + 记忆成本。
- emoji 跨平台渲染不一致，破坏一致性原则。

**改进**：rail 加宽到 56px，按钮常显 10px 文字标签，保留图标。✅ 已实施。

### 2.3 ❌ titlebar 不随路由变化 —— 违反「系统状态可见」
**证据** `Layout.tsx`：titlebar 中部写死"本体建模平台"，在 `/data/syncs/x` 深页仍显示本体，用户不知道自己在哪。

**改进**：titlebar 中部显示当前页路径（rail label → 子页名）。✅ 已实施。

### 2.4 ⚠️ 面包屑各页手写、不统一
**证据** `DataSourceDetail` / `SyncTaskDetail` / `DatasetDetail` 各自手写 `← 返回 / 名称`，样式不一致。

**改进**：抽取统一 `Breadcrumb` 组件，全站复用。✅ 已实施。

### 2.5 ⚠️ OntologyWorkspace 用 `-m-6` 破坏 main-content padding
**证据** `OntologyWorkspace.tsx`：`<div className="flex h-full gap-0 -m-6">` 用负 margin 抵消 `.main-content` 的 `p-6`，导致 Workspace 内边距=0，其他页=24px，全站视觉不一致。

**改进**：`AppLayout` 提供 `fullBleed` 模式，页面自行声明，不再用负 margin。✅ 已实施。

---

## 三、分栏与信息密度（认知负荷 / 格式塔邻近性）

### 3.1 ⚠️ Workspace 三栏总宽过载
固定 `280px + 流式 + 340px`。1280px 笔记本中栏仅 660px，还要塞表格/卡片/图谱 + AI 面板，认知负荷过高。

**改进**：右栏 `ObjectDetailPanel` 改为可收起抽屉（选中时滑入，关闭时不占位）；中栏加 `max-w-[1100px] mx-auto`。✅ 已实施（抽屉化 + max-width）。

### 3.2 ⚠️ DataConnections 一页混排三类对象
数据源卡片（含嵌套同步任务）+ 数据集列表全堆一页，嵌套层级 = 卡片 > 区段 > 卡片，违反格式塔封闭性/邻近性。

**改进**：数据集列表用粗分割线 + 大标题明确分区，卡片内嵌套用缩进 + 浅背景明确层级。✅ 已实施。

### 3.3 ✅ 格式塔做得好的地方
- `.card` / `.ds-card` 圆角 + border + surface 背景，封闭性到位。
- `.section-header` 带下边框 + 大写小标题，连续性 + 分组清晰。

---

## 四、按钮与可点击组件（费茨定律 / 反馈闭环 / 防错）

### 4.1 ❌ 按钮无 loading 态 —— 违反「系统状态可见」+「反馈闭环」
**证据** 全项目 `.btn` 无 loading 变体；`OntologyWorkspace` 的 `handleCreateOntology` / `handleWizardComplete` 是 async，点击后无视觉反馈直到 Toast 出现，用户会重复点击 → 重复创建。

**改进**：
- `.btn` 加 `.is-loading` 变体（`pointer-events-none` + 内嵌 spinner + 文案"处理中…"）。
- 抽 `useAsyncAction` hook，自动管理 loading + disabled。
- ✅ 已实施。

### 4.2 ❌ 删除按钮与编辑按钮等权重并排 —— 违反「防错优于纠错」
**证据** `ObjectDetailPanel.tsx` 底部：编辑、删除两个 `flex-1` 等宽并排，无空间隔离，误触概率高。

**改进**：编辑主按钮 `flex-1`，删除收成图标按钮 + 与编辑物理隔离（分隔线）。✅ 已实施。

### 4.3 ⚠️ 点击热区偏小
`.btn-xs` = `px-1.5 py-0.5`，实际点击区可能 < 24×24px，低于 Web 推荐 32×32px 下限（费茨定律）。

**改进**：`.btn-xs` / `.btn-sm` 用最小高度 + 透明扩展层扩大热区。✅ 已实施（min-height）。

### 4.4 ❌ 无快捷键 —— 违反「灵活高效（熟手）」
**改进**：全局快捷键（`/` 聚焦搜索、`n` 新建、`g o/d/a` 切 rail、Workspace `1/2/3` 切视图、图谱 `f/+/-/Delete`）。✅ 已实施（`useHotkeys` hook + 全局注册）。

### 4.5 ✅ 按钮分级语义清晰
`.btn` / `.btn-primary` / `.btn-danger` / `.btn-sm` / `.btn-xs` 分级合理，颜色语义符合格式塔相似性。`CapabilityBar` 按能力动态渲染也是好设计。

---

## 五、表单与向导（容错 / 认知负荷 / 米勒定律）

### 5.1 ⚠️ 表单实时校验不足 —— 违反「防错」+「清晰错误提示」
**证据** 新建本体表单用原生 `<input required>`，提交才校验；错误用 Toast 全局提示，不定位输入框。`CreateObjectWizard` 已有 `showErrors`（部分到位）但 api_name 无格式实时校验。

**改进**：新建本体表单加失焦实时校验 + 行内错误；api_name 加 `pattern` + 实时提示。✅ 已实施。

### 5.2 ❌ 无草稿保存 —— 违反「用户可控」+「恢复性容错」
**证据** `CreateObjectWizard` 多步向导填到一半刷新页面全部丢失。

**改进**：向导数据写 `localStorage`（按 ontology + 草稿 key），重新打开提示"检测到未完成草稿，是否恢复？"。✅ 已实施（`useDraft` hook）。

### 5.3 ✅ 向导分步设计本身符合米勒定律
把"对象属性 + 关系 + 动作"拆成多步，每步信息单元可控。

---

## 六、弹窗与抽屉（行动七阶段 / 用户可控 / 层级规范）

### 6.1 ✅ ConfirmDialog 分级（LOW/MEDIUM/HIGH + requireName）做得到位
HIGH 级要求输入名称确认，`DataConnections` 接 `analyzeImpact` 显示影响项，超过多数项目。

### 6.2 ⚠️ 弹窗层级未在 CSS 显式约定
`.dialog-overlay` z-100、`.toast` z-200、`.overlay-backdrop` z-100 同级，多层弹窗时谁盖谁靠 DOM 顺序。

**改进**：`index.css` 顶部定义 z-index token，禁用裸数字。✅ 已实施。

### 6.3 ⚠️ 弹窗关闭交互不完整
`.dialog-overlay` 点击遮罩关闭，但无 focus trap、关闭后焦点不回归。`ConfirmDialog` / `CreateObjectWizard` 已有 ESC（部分到位）。

**改进**：封装 `<Modal>` 组件统一处理 ESC、focus trap、`aria-modal`。✅ 已实施。

### 6.4 ✅ 大表单用 Wizard（多步）而非窄弹窗
符合"大表单改用多步向导"。

---

## 七、加载与反馈（系统状态可见）

### 7.1 ❌ 无骨架屏，加载态用纯文字"加载中…"
**证据** `ObjectDetailPanel` / `DataConnections` / `OntologyGraph` loading 均为纯文字，无进度预期，内容跳变造成布局抖动。

**改进**：列表/卡片用骨架屏（`.skeleton` 灰块 + shimmer），详情面板用结构化骨架。✅ 已实施（`Skeleton` + `.skeleton` 样式）。

### 7.2 ❌ 长任务无进度反馈
**证据** `OntologyWorkspace.doBatchCreate` 批量创建 N 个对象，for 循环串行 await，期间静默。

**改进**：批量操作显示进度条 `创建中 3/20…`（`useProgress` + 全局 progress bar）。✅ 已实施。

### 7.3 ⚠️ 错误反馈不分级，全用 Toast
所有错误走 `setToast('加载失败: ' + err.message)`，err.message 常是后端术语。违反"贴近用户真实世界"和"清晰错误提示"。

**改进**：抽 `formatError(err)` 统一翻译后端错误为可执行文案；网络/权限错误走专用空状态。✅ 已实施。

### 7.4 ✅ 空状态有引导
`DataConnections` / `OntologyWorkspace` 空状态有 emoji + 文案 + 引导按钮，符合"空页面附引导操作"。

---

## 八、图谱可视化交互（HCI 第六节）

### 8.1 ✅ 工具栏齐全
布局下拉、重排、放大/缩小/自适应、锁定、隐藏、导出 PNG/SVG；右键菜单、鸟瞰图、Shift 框选、hover 高亮邻域。覆盖 HCI 最佳实践 6.2/6.3/6.6。

### 8.2 ❌ 缺图例（违反格式塔相似性 + 识别优于记忆）
节点颜色按 storage_type 区分，但画布无图例说明，用户无法解读。

**改进**：画布右下角浮层图例（实体=橙、虚拟=青、关系边=灰），可折叠。✅ 已实施。

### 8.3 ⚠️ hover 高亮邻域无过渡动画
`cy.elements().style('opacity', 0.3)` 直接跳变，无 transition，视觉突兀。

**改进**：用 `cy.batch` + `animate` 实现 0.2s 渐变。✅ 已实施。

### 8.4 ⚠️ 右键菜单移动端不可用
`cxtmenu` 是右键触发，触屏无右键。

**改进**：工具栏补"编辑/聚焦"按钮兜底（已有部分），文档标注桌面优先。⚠️ 部分实施。

### 8.5 ✅ 懒加载 + 实例常驻
cytoscape 动态 import + `hidden` 显隐保留实例/缩放，符合性能最佳实践。

---

## 九、无障碍 WCAG 2.1（硬性合规）

### 9.1 ❌ emoji 作为唯一图标语义
rail、视图切换、能力按钮大量用 emoji 且 `aria-hidden="true"`，屏幕阅读器读不出含义。

**改进**：emoji 保留装饰，按钮补 `aria-label` 或可见文字。✅ 已实施。

### 9.2 ⚠️ 对比度风险
`--color-text-muted: #5e6d7e` on `--color-bg: #0c1117`，对比度约 4.0:1，低于 WCAG AA 4.5:1。小字大量用 muted，不达标。

**改进**：muted 提亮到 `#738091`（暗色）/ 调整亮色，小字号强制用 secondary。✅ 已实施。

### 9.3 ⚠️ 键盘可达性未系统化
`OntologySidebar` 用 `<div onClick>`（不可 Tab 聚焦），违反可操作性。

**改进**：可点击改 `<button>` 或加 `role="button" tabIndex={0}` + `onKeyDown`。✅ 已实施。

### 9.4 ✅ 已有的无障碍努力
`Layout.tsx` rail 有 `aria-label`、`aria-current="page"`、`aria-label="主导航"`；主题切换走 class。

---

## 十、一致性工程（尼尔森第 4 条，B 端核心）

### 10.1 ✅ 组件复用矩阵落地
`frontend-data-layer-design.md` 有完整复用矩阵，`StatusBadge`/`SearchBar`/`ColumnList`/`ConfirmDialog` 已抽取复用。

### 10.2 ⚠️ 状态标记有三套体系并存
`.status-active/.status-experimental`（object-card）+ `.sb-success/.sb-error`（status-badge）+ `.status-dot.connected`，三套语义重叠。

**改进**：统一到 `StatusBadge` + 一套语义 token，`.status-active` 标记 deprecated 别名。✅ 已实施。

### 10.3 ⚠️ 术语轻微不一致
"数据源管理"（页标题）vs "数据对接"（rail）；"新建对象" vs "添加数据源"（新建/添加混用）。

**改进**：建术语常量 `constants/terms.ts`，新增=创建业务对象，添加=接入外部资源。✅ 已实施。

### 10.4 ⚠️ OperationsDashboard 是半成品
仅 3 个指标卡 + N+1 轮询刷新历史，缺真正运维指标（同步延迟、查询 P95、成功率）。

**改进**：扩展指标维度（数据源/同步任务/对象类型/关系 + 同步状态分布），保留健康检查，去除 N+1 改并行。✅ 已实施。

---

## 十一、优先级排序与落地路线

| 优先级 | 问题 | 影响 | 状态 |
|---|---|---|---|
| **P0** | 按钮 loading 态（4.1） | 所有写操作体验 | ✅ |
| **P0** | 表单实时校验 + 错误定位（5.1/7.3） | 防错、容错 | ✅ |
| **P0** | 骨架屏替换"加载中…"（7.1） | 状态可见 | ✅ |
| **P1** | 删除按钮隔离（4.2） | 防误触 | ✅ |
| **P1** | rail 加可见文字标签（2.2） | 识别>记忆 | ✅ |
| **P1** | 统一 Modal 组件（ESC/focus trap）（6.3） | 用户可控+a11y | ✅ |
| **P1** | z-index token 规范（6.2） | 一致性 | ✅ |
| **P1** | 批量操作进度反馈（7.2） | 状态可见 | ✅ |
| **P2** | 状态样式三套合一（10.2） | 一致性 | ✅ |
| **P2** | 图谱图例 + 筛选联动（8.2/8.3） | 图谱可用性 | ✅图例 / ⚠️联动 |
| **P2** | 快捷键体系（4.4） | 熟手高效 | ✅ |
| **P2** | 对比度 + emoji a11y（9.2/9.1） | WCAG 合规 | ✅ |
| **P3** | 面包屑 + titlebar 路径（2.3/2.4） | 导航定位 | ✅ |
| **P3** | 向导草稿保存（5.2） | 容错 | ✅ |
| **P3** | OperationsDashboard 重做（10.4） | 运维价值 | ✅ |

---

## 十二、前端开发校验清单

> 上线前逐条勾选。源自尼尔森十大原则 + WCAG 2.1 + B 端最佳实践。

### A. 导航与布局
- [ ] 一级导航 ≤ 7 项；二级菜单分组折叠
- [ ] rail/菜单项有可见文字标签（不仅靠图标 hover）
- [ ] titlebar / 面包屑反映当前路由位置
- [ ] 页面内边距全站统一，禁止用负 margin hack
- [ ] 三栏布局在 1280px 宽下中栏 ≥ 600px，否则右栏可收起

### B. 按钮与可点击
- [ ] 所有写操作按钮点击后有 loading 态（`.is-loading` 或 `useAsyncAction`）
- [ ] 高危（删除）按钮与常规按钮物理隔离，不等权重并排
- [ ] Web 可点击区域最小 28×28px（`.btn-xs`/`.btn-sm` min-height）
- [ ] 按钮分级：primary / default / danger / disabled 语义清晰
- [ ] 高频操作有快捷键

### C. 表单
- [ ] 必填/格式校验失焦即时触发，错误行内定位（不弹 Toast）
- [ ] 错误文案给可执行方案（"请输入 11 位手机号"而非"参数错误"）
- [ ] 多步向导有草稿保存（`useDraft`）
- [ ] 长表单报错自动滚动到第一个错误项
- [ ] 下拉/单选替代自由文本输入，减少人为错误

### D. 弹窗与抽屉
- [ ] 弹窗支持 ESC 关闭、点击遮罩关闭、右上角关闭
- [ ] 弹窗有 focus trap，关闭后焦点回归触发按钮
- [ ] 高危操作二次确认（ConfirmDialog 分级，HIGH 输入名称）
- [ ] 多层弹窗 ≤ 2 层；z-index 用 token（`--z-*`）不裸数字
- [ ] 大表单用多步向导或右侧抽屉，不用窄弹窗

### E. 加载与反馈
- [ ] 接口请求有 loading（按钮局部 / 表格遮罩 / 骨架屏）
- [ ] 加载用骨架屏而非纯文字，避免布局抖动
- [ ] > 3s 长任务有进度条（`useProgress`）
- [ ] 错误用 `formatError` 翻译，不直接展示后端堆栈
- [ ] 空状态区分：无数据 / 无搜索结果 / 无权限 / 加载失败，附引导

### F. 图谱/可视化
- [ ] 画布有图例说明颜色/形状语义
- [ ] 工具栏：布局切换、缩放、自适应、导出
- [ ] hover 高亮邻域有过渡动画（200–300ms）
- [ ] 鸟瞰缩略图 + 框选多选
- [ ] 大数据虚拟滚动 / 视口外懒渲染

### G. 无障碍 WCAG
- [ ] 文字与背景对比度 ≥ 4.5:1（小字用 secondary 不用 muted）
- [ ] emoji/图标不作为唯一语义，补 `aria-label` 或可见文字
- [ ] 可点击元素可 Tab 聚焦（`<button>` 或 `role=button tabIndex=0`）
- [ ] 焦点有可见高亮边框
- [ ] 颜色不单独区分状态，搭配文字/图标
- [ ] 动态弹窗加 `aria-modal`，屏幕阅读器可读

### H. 一致性
- [ ] 状态标记全站用 `StatusBadge`，禁用散落 `.status-*`
- [ ] 术语全站统一（`constants/terms.ts`）
- [ ] 通用组件（Table/Form/Modal/Toast）全项目复用，不各页自定义
- [ ] z-index / 间距 / 圆角用 token，不裸数字
- [ ] 动画时长 200–300ms，无高频闪烁

---

## 十三、实施记录

> 本节记录每条改进的落地位置，便于回归对照。

| 改进项 | 落地文件 | 说明 |
|---|---|---|
| rail 文字标签 | `components/Layout.tsx` + `index.css` | rail 加宽 56px，常显文字 |
| titlebar 路径 | `components/Layout.tsx` | 中部显示当前 rail label |
| 统一 Breadcrumb | `components/Breadcrumb.tsx`（新） | 三个详情页替换手写版 |
| fullBleed 布局 | `components/Layout.tsx` + `OntologyWorkspace.tsx` | 去除 `-m-6` hack |
| 按钮 loading | `hooks/useAsyncAction.ts`（新）+ `index.css` | `.is-loading` + hook |
| 删除按钮隔离 | `components/ObjectDetailPanel.tsx` | 删除收成图标按钮 + 分隔 |
| 点击热区 | `index.css` | `.btn-xs`/`.btn-sm` min-height |
| 快捷键 | `hooks/useHotkeys.ts`（新）+ `Layout.tsx` | 全局 + Workspace + 图谱 |
| 表单实时校验 | `OntologyWorkspace.tsx` 新建本体表单 | 失焦校验 + 行内错误 |
| 向导草稿 | `hooks/useDraft.ts`（新）+ `CreateObjectWizard.tsx` | localStorage + 恢复提示 |
| 骨架屏 | `components/Skeleton.tsx`（新）+ `index.css` | `.skeleton` shimmer |
| 批量进度 | `hooks/useProgress.ts`（新）+ 全局 ProgressBar | `创建中 3/20…` |
| 错误翻译 | `lib/formatError.ts`（新） | `formatError(err)` |
| Modal 组件 | `components/Modal.tsx`（新） | ESC + focus trap + aria-modal |
| z-index token | `index.css` | `--z-*` token |
| 图谱图例 | `components/OntologyGraph.tsx` | 右下角浮层图例 |
| 图谱过渡动画 | `components/OntologyGraph.tsx` | hover 渐变 0.2s |
| 对比度 | `index.css` | muted 提亮 |
| emoji a11y | 多文件 | 补 `aria-label` |
| 键盘可达 | `OntologySidebar.tsx` | div onClick → button |
| 状态样式统一 | `index.css` + `StatusBadge.tsx` | `.status-*` deprecated |
| 术语常量 | `constants/terms.ts`（新） | 新增/添加统一 |
| OperationsDashboard | `pages/OperationsDashboard.tsx` | 扩展指标 + 并行加载 |
| Workspace 右栏抽屉 | `OntologyWorkspace.tsx` + `ObjectDetailPanel.tsx` | 选中滑入，关闭不占位 |
| DataConnections 分区 | `pages/DataConnections.tsx` | 数据集粗分割 + 嵌套缩进 |

> 文档版本 v1.0 · 最后更新 2026-06-18 · 审视依据：HCI 完整理论框架
