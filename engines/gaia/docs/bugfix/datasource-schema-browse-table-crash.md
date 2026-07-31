# 数据源「浏览 Schema」连续切表崩溃复盘（React Aria Table collection 竞态）

**记录时间**: 2026-07-24
**影响模块**: 前端 `components/ui/DataTable.tsx` / `components/ColumnList.tsx` / `components/PreviewTable.tsx` / `pages/DataSourceDetail.tsx`
**状态**: ✅ 已修复（DataTable 回退为原生 `<table>`，见 ADR-013 R1）

---

## 现象

数据源详情页「浏览 Schema」tab，连续点击两张表（中间隔几秒），页面直接崩溃：

> 组件出错
> Cell count must match column count. Found 5 cells and 0 columns.

崩溃后整个 tab 内容被 ErrorBoundary 替换，必须刷新页面才能恢复。

**反直觉的复现条件**：间隔**几秒**（慢）反而比快速连点更容易触发。快速连点（几十毫秒）常常不报错。

## 根因

React Aria Components 的 `<Table>` 用一个基于 fake DOM（`Document` 类）+ `useSyncExternalStore` 的 collection 系统。在 `TableCollection.commit()` 阶段逐行校验「cell 数 == column 数」：

```js
// react-aria-components/dist/private/Table.mjs
commit(firstKey, lastKey, isSSR = false) {
  this.updateColumns(isSSR);          // 收集 TableHeader 里的 Column
  ...
  for (let row of this.getRows()) {
    let numberOfCellsInRow = (lastCell.colIndex ?? lastCell.index) + (lastCell.colSpan ?? 1);
    if (numberOfCellsInRow !== this.columns.length && !isSSR)
      throw new Error(`Cell count must match column count. Found ${numberOfCellsInRow} cells and ${this.columns.length} columns.`);
  }
}
```

`ColumnList`（包在 `DataTable` 内）在 `activeTable` 切换时，因 `activeInfo = columnMap[activeTable]` 在新表列未加载完时为 `null`，经历 **卸载 →（等异步 fetchColumns）→ 重新挂载** 的完整周期。重新挂载时新 `<AriaTable>` 创建新 `Document`，React 在挂载 portal children 时，`Document.queueUpdate()` 可能在 `TableHeader` 的 `Column` 节点尚未全部 `addNode` 而 `TableBody` 的 `Cell` 节点已 `addNode` 的中间态就触发 `commit` → `this.columns.length === 0` 而 cells 已有 5 个 → 抛错。

**为什么慢间隔更容易触发**：慢间隔下 `ColumnList` 完整经历了「卸载 → 等异步 → 重挂载」周期，命中重挂载过程中的竞态窗口；快间隔下第二次点击打断了第一次的卸载/重挂载时序，反而错开竞态。

这是 React Aria 的长期已知缺陷，官方未修复：
- [adobe/react-spectrum#8127](https://github.com/adobe/react-spectrum/issues/8127)（条件渲染 columns/cells 崩溃）
- [adobe/react-spectrum#9937](https://github.com/adobe/react-spectrum/issues/9937)（动态 API id scoping 崩溃）
- [adobe/react-spectrum#8906](https://github.com/adobe/react-spectrum/issues/8906)（Suspense 下只渲染首列）

## 失败的修复尝试

### ❌ `key={columnsKey}` 强制重建

工作区里曾有一个补丁：用 `columns.map(c => c.id).join('::')` 作为 `<AriaTable>` 的 key，期望列结构变化时强制重建 collection。

**为何无效**：
1. `ColumnList` 的列定义是**静态的**（列名/类型/NULL/主键/说明，固定 5 列），`columnsKey` 恒定 → key 不变 → 不触发重建。
2. 更根本地，竞态发生在**重挂载过程内部**（新 Document 挂载 portal children 的中间态 commit），key 重建只是让 React 卸载旧的、挂载新的，但「新的」在挂载时仍可能命中竞态。实测慢间隔下 B 是全新挂载仍报错，证明 key 重建无法解决。

## 最终修复

`DataTable` 由 React Aria `Table` 回退为原生语义 `<table>` + `<thead>/<tbody>/<th>/<td>`，复用现有 `.data-table` CSS class。

**对外 API 完全不变**（`columns` / `rows` / `renderCell` / `rowKey` / `rowClassName` / `onRowAction` / `aria-label`），调用方（`PreviewTable` / `ColumnList` / `DatasetsPage`）零感知。`onRowAction` 保留（行点击 + Enter/Space 键盘激活）以维持 API 兼容和基本可达性。

**依据**：
1. 所有调用方均为纯只读展示，无一处使用 React Aria `Table` 的键盘行导航 / 行选择能力（`ColumnList.selectable` 已无调用方传入，`DatasetsPage` 行内点击通过 cell 内 `<button>` 实现）。回退不丢失任何实际功能。
2. `DataTable` 源码注释本就声明「只读展示表用它、嵌入表单控件的编辑表用原生 `<table>`」，回退与该定位一致。
3. 原生表无 collection 校验竞态，且性能更优（无 fake DOM + portal + `useSyncExternalStore` 开销）。

详见 [ADR-013 R1](../architecture/adr-013-react-aria-components.md#修订记录)。

## 验证

本地 k3s 环境（数据源 `xiaoling`，45 张表）实测三场景均零报错：
- 慢间隔（2s）：点 A → 等 2s → 点 B ✅
- 快间隔（50ms）：点 C → 等 50ms → 点 D ✅
- 压力测试：12 次快速切换（A→B→A→B→C→D→...）✅

页面不再进入 ErrorBoundary，当前活跃表正确渲染。

`tsc -b` + `vite build` + 368 个 vitest 用例全绿（含新增 7 个 `DataTable` 单测）。

## 教训

1. **headless 库的 collection 系统是黑盒风险**：React Aria `Table` 的 fake DOM + `useSyncExternalStore` 机制在组件卸载/重挂载时有竞态，且官方长期未修复。对「会频繁卸载重挂载」的展示型组件，原生语义元素更可靠。
2. **`key` 重建不是万能药**：`key` 变化只让 React 卸载旧实例、挂载新实例，但「挂载新实例过程内部」的竞态无法靠 key 解决。定位竞态时要区分「跨 render 的实例复用问题」和「单次挂载内部的时序问题」。
3. **复现条件可能反直觉**：本 bug 慢间隔比快间隔更容易触发。遇到「偶发崩溃」时，要系统测试不同时序（快/慢/压力），不能只测一种。
4. **决策要基于实际使用**：ADR-013 当初选 React Aria `Table` 是为「键盘导航 + 行选择」，但实际没有任何调用方用到这些能力。定期审视「为 capability X 引入的依赖，X 是否真被使用」。
