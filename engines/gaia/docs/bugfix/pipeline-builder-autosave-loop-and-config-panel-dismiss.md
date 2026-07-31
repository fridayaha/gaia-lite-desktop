# Pipeline Builder: auto-save 无限循环 + 配置面板闪退

## 日期

2026-07-16

## 症状

1. **配置面板闪退**：选中算子节点后，右侧配置面板短暂出现约 1 秒，然后自动消失，回到"点击画布上的节点以编辑配置"。
2. **auto-save 无限循环**：每 2 秒发送一次 `PATCH /api/v1/pipelines/:name`，即使管道内容没有任何变化。版本号从 v67 暴涨到 v351+，数据库和存储被无效版本淹没。

## 根因

两个根因叠加，形成恶性循环。

### 根因 1：`handlePipelineSaved` 中 `loadPipeline` 重置 `selectedNodeId`

```tsx
// 旧代码
const handlePipelineSaved = useCallback(
  (p: PipelineResponse) => {
    loadPipeline(p, serializeIR());  // ← 这里！
    markClean();
  },
  [loadPipeline, serializeIR, markClean],
);
```

`loadPipeline` 会把 `selectedNodeId` 设为 `null`：

```ts
loadPipeline: (pipeline, graph) => {
  set({
    pipeline,
    irNodes: graph.nodes,
    irEdges: graph.edges ?? [],
    selectedNodeId: null,  // ← 配置面板消失
    isDirty: false,
    pastSnapshots: [],
    futureSnapshots: [],
  });
},
```

每次 auto-save 完成后，`loadPipeline` 被调用 → `selectedNodeId: null` → 配置面板消失。

### 根因 2：`usePipelineBuilder.setState` + `markClean` 的执行顺序导致 auto-save 循环

```tsx
// 旧代码（修复前的 handlePipelineSaved）
usePipelineBuilder.setState({ pipeline: p });  // ← 先触发渲染，此时 isDirty 仍为 true
markClean();                                     // ← 后标记 clean，但已经晚了
```

执行时序：

1. `usePipelineBuilder.setState({ pipeline: p })` — Zustand store 更新 `pipeline` 字段，触发 React 重新渲染
2. 渲染期间 `isDirty` 仍为 `true`（`markClean` 还没执行）
3. `useAutoSave` 的 effect 读到 `isDirty: true`，设置 2s timer
4. `markClean()` 执行，`isDirty: false` — 但 timer 已经存在了
5. 2s 后 timer 触发 auto-save → `handlePipelineSaved` → 又回到步骤 1

在 React 18 StrictMode（开发模式）下，effects 会执行两次，加剧了这个问题。

## 修复

### 修复 1：`handlePipelineSaved` 不再调用 `loadPipeline`

`handlePipelineSaved` 只应更新 `pipeline` 对象（版本号）和标记 clean。
不需要重建整个 store 状态。

```tsx
// 新代码
const handlePipelineSaved = useCallback(
  (p: PipelineResponse) => {
    // 先 markClean 再 setState，避免中间渲染触发 auto-save timer
    markClean();
    usePipelineBuilder.setState({ pipeline: p });
  },
  [markClean],
);
```

### 修复 2：先 `markClean` 再 `setState`

确保 React 渲染前 `isDirty` 已经是 `false`，`useAutoSave` 的 effect 不会设置新 timer。

## 教训

### 通用反模式

| # | 反模式 | 为什么危险 | 预防 |
|---|--------|-----------|------|
| 1 | **在 auto-save 回调中调用"全量状态重建"函数**（如 `loadPipeline`） | `loadPipeline` 会重置 `selectedNodeId`、`isDirty`、undo/redo 历史等所有非持久字段 | auto-save 回调只能做两件事：① 更新版本号/元信息 ② `markClean`。其他任何操作都是错误的 |
| 2 | **先触发渲染再修正状态的顺序错误** | `setState` 触发同步渲染（React 18 非事件处理器上下文），中间状态的 effect 会开始不可逆的副作用（如设置 timer、发起请求） | 如果两个 setState 操作之间有依赖关系，先做"消除副作用条件"的操作（如 `markClean`），再做"触发副作用"的操作（如 `setState`） |
| 3 | **用 `loadPipeline` 做"状态刷新"** | `loadPipeline` 的语义是"从零加载一个管道"，会清空编辑上下文。它不是"更新管道元信息" | 区分两个操作：`loadPipeline` = 加载新管道；更新 metadata = 只改 `pipeline` 字段 |

### 设计原则

1. **auto-save 回调的职责边界**：只做"持久化完成后的 bookkeeping"——更新版本号 + 标记干净。不要碰画布状态、选中态、undo/redo 历史。
2. **Zustand 的 `setState` + `set` 不是原子的**：两个连续的 store 变更之间 React 可以渲染。如果需要原子性，合并为单次 `set` 调用。
3. **React 事件处理器外不批量**：`setTimeout` 回调、Promise `.then()` 回调、非 React 事件处理器中的 `setState` 都会触发同步渲染。

### 检测信号

如果你看到以下现象，说明可能存在同类问题：

- auto-save 每 N 秒持续触发，即使内容无变化
- 选中/编辑状态在 auto-save 后丢失
- 管道版本号暴涨（每 2 秒一个新版本 = 30 版本/分钟）
- Network 面板中 PATCH 请求稳定间隔 2s（与 debounce delay 一致）

## 相关文件

- `src/web-ui/src/pages/PipelineBuilderPage.tsx` — `handlePipelineSaved`
- `src/web-ui/src/hooks/useAutoSave.ts` — auto-save debounce 逻辑
- `src/web-ui/src/hooks/usePipelineBuilder.ts` — `loadPipeline`, `markClean`, `setState`
