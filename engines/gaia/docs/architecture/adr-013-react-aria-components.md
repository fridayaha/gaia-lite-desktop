# ADR-013：前端采用 React Aria Components 作为 headless 行为层

> **状态**：已采纳（Accepted）
> **日期**：2026-06-28
> **决策者**：前端架构
> **关联**：CLAUDE.md 第二原则（组件复用最大化）、第四原则（前端质量守则）、`docs/frontend-standards.md`

## 背景

Gaia 前端基于 **React 19 + Tailwind CSS v4**，此前**未引入任何 UI 组件库**——所有交互组件（input、modal、toast、table、accordion）均为手写原生 HTML + 受控 state。

### 触发问题

在「新建本体 / 新建对象」向导中，displayName 输入框采用受控 `<input>` + 每次 `onChange` 同步 `setState`（并实时推导 apiName 触发额外重渲染）。**中文 IME 输入法 composition 期间，React 受控回写 value 会打断输入**，导致用户无法正常输入中文。这是 React 受控输入的经典痛点（[react#3926](https://github.com/react/react/issues/3926)）。

### 行业现状（2025-2026）

带 CSS 的组件库（Ant Design / Blueprint / Mantine）已不是首选，**headless / unstyled 组件库**成为主流——逻辑（状态、键盘、ARIA、IME、国际化）归库，样式归自己（Tailwind）。shadcn/ui 2026-01 起官方声明"Choose between Radix or Base UI"——它是样式层，底层 headless 库需自行选择。当前项目未装任何底层，input 行为是裸的。

## 决策

**采用 [React Aria Components](https://react-spectrum.adobe.com/)（`react-aria-components`）作为前端 headless 行为层。**

### 为什么是 React Aria（而非 Radix / Base UI / Headless UI）

React Aria 作为后续 Phase（Modal/Table/Select 等）的 headless 行为层。**但经源码核实，React Aria 1.19 的普通 `TextField` 并不处理 IME composition**（`useTextField` 的 onChange 在每次 input 事件触发，不检查 `isComposing`；IME 处理仅在 `useFormattedTextField` 数字/日期格式化路径）——见 adobe/react-spectrum#8506。因此 Phase 1 的 IME 修复改用专门库 **foxact/use-composition-input**。

| 维度 | React Aria | Radix / Base UI | Headless UI |
|------|-----------|----------------|-------------|
| IME composition 处理 | ❌ 普通 TextField 不处理 | ⚠️ 原生 | ⚠️ 原生 |
| 国际化（i18n） | ✅ 一等公民（30+ 语言、13 日历、RTL） | ⚠️ 需自行 | ⚠️ 无 |
| 可访问性（a11y） | ✅ Adobe 生产级，ARIA 自动管理 | ✅ 强 | ✅ 强 |
| 与 Tailwind v4 契合 | ✅ 完全无样式 | ✅ 无样式 | ✅ 无样式 |
| 组件覆盖广度 | ✅ 全套（表单/集合/浮层/布局/日期） | ✅ 全 | ❌ 少 |

### Phase 1 IME 修复方案（composition 期间切非受控）

**根因**：React 受控 `<input value={state} onChange={setState}>` 在 CJK IME composition 期间，React 19 可能触发 onChange，父组件 setState 导致重渲染，回写 controlled value（旧值，因为 composition 未提交）清空 DOM，关闭 IME 候选窗——用户看到"值进不去"（facebook/react#8683）。

**已试方案及否决**：
- ❌ React Aria `TextField`：源码核实不处理 IME composition（adobe/react-spectrum#8506）
- ❌ foxact `useCompositionInput`：明确只支持**非受控** input，受控模式下 value 回写旧值清空
- ❌ shadow state（内部状态跟踪 live value）：useEffect 时序问题导致父旧值同步回 shadow 清空
- ❌ 原生受控 + composingRef guard 副作用：React 19 composition 期间仍可能触发 onChange 回写

**最终方案（composition 期间切非受控）**：wrapper 用 `composing` state 跟踪 IME 状态，`value={composing ? undefined : value}`——composition 期间 `value=undefined` 使 input **非受控**（React 不回写，DOM 自然显示用户输入），compositionend 后 `setComposing(false)` + `onChange(finalValue)` flush 到父组件，下次渲染 `composing=false` → `value=新state` 受控。这是 React 官方支持的受控/非受控切换方式。

**封装**：`src/components/ui/TextField.tsx` 的 `<TextInput>` / `<TextAreaInput>`，对外保持原生 input API（value/onChange/placeholder），调用点零感知。

| 阶段 | 范围 | 目的 |
|------|------|------|
| **Phase 1（已完成）** | 引入 `react-aria-components`，封装项目级 `<TextInput>` / `<TextArea>`（套现有 `form-input` CSS class），替换**有实时推导/受控 setState 的输入框**（新建本体/对象 displayName、apiName、description，属性 displayName、description） | 修复 IME 中文输入 + ARIA |
| **Phase 1 扩展（已完成）** | 将所有受控 `<input className="form-input">` / `<textarea>` 替换为 IME-safe `TextInput` / `TextAreaInput`（DataSourceForm / EffectConfigForm / ValueSourceInput / ParameterList / RuleCard / ActionTypeEditor / ObjectTypeViews / SyncModeSelector / ActionPreviewPanel / ActionParameterField / ConfirmDialog / RegisterVirtualTableDialog / DatasetLinkDialog）。`TextField.tsx` 重构为透传完整原生 props（pattern/title/autoFocus/maxLength 等）。 | 全表单中文输入不中断 |
| **Phase 2（已完成）** | `Modal` 基于 React Aria `ModalOverlay` + `Modal` + `Dialog` 重写（焦点陷阱 + 焦点回归 / iOS body 滚动锁定 `usePreventScroll` / 外部 `aria-hidden` + `inert` / ESC + 遮罩点击 `isDismissable`）；`RegisterVirtualTableDialog` / `DatasetLinkDialog` 由手写 `.dialog-overlay` 迁移至统一 `Modal`。对外 API 不变（新增 `panelClassName` 自定义宽度）。`Disclosure` accordion 见 Phase 5 备注。 | 浮层标准化 |
| **Phase 3（已完成）** | `ConfirmDialog` 改用统一 `Modal` 底座 + `role="alertdialog"`（HIGH 级名称确认输入改为 IME-safe `TextInput`）。`Toast` 维持现有 `useToast` + `ToastView`（已具备正确 `aria-live`/`role`）；全局 `ToastQueue` + `ToastProvider` 重构为体验增强，待真实多 Toast 排队需求触发再引入。 | 反馈/确认标准化 |
| **Phase 4（已完成）** | `Select`：封装项目级 `<Select>` / `<SelectOption>`（React Aria `Select`+`Button`+`Popover`+`ListBox`，套 `form-select` 样式 + 列表项 hover/selected/disabled 样式），替换全部 36 处原生 `<select>`（含动态选项/条件渲染/块式 onChange）；同步更新 `ActionParameterField` / `ExecuteActionDialog` 测试（`getByRole('combobox')` → listbox 触发按钮断言）。`Table`：封装 `<DataTable>`（React Aria `Table`+`Column`/`Row`/`Cell`，键盘行导航 + row action），迁移只读展示表（`PreviewTable`、`ColumnList`、CreateObjectWizard 关系列表）；嵌入表单控件的编辑表（属性编辑表、DatasetLinkDialog 列映射表）保留原生 `<table>`（React Aria `Cell`+`Collection` 与内联受控控件组合复杂，且原生表已具备语义 a11y）。 | 集合/选择增强 |
| **Phase 5（已完成）** | 封装四个 React Aria headless 原语供项目复用：`<ComboBox>`（可搜索单选 + 自定义列表项）、`<DatePicker>`（ISO 字符串适配 React Aria `DateValue`）、`<Disclosure>`（可折叠面板 + ARIA expanded）。各配 smoke 测试。**未迁移的存量场景及依据**：(1) `ObjectPicker` 保留手写——其「可自由输入主键 + 前端过滤选择」的混合行为是刻意设计，React Aria `ComboBox` 的 selectedKey 模型不允许自由输入未列出的值；(2) `SchemaTreeBrowser` 保留手写 div 树——含懒加载列、登记虚拟表按钮、多选表等自定义流程，React Aria `Tree` 的 `Collection` 模型难以承载；(3) `ActionParameterField` 的 DATE/TIMESTAMP 保留原生 `input[type=date]`/`datetime-local`——ISO 字符串值 + 轻量，React Aria `DateField` 用 `DateSegment` 会破坏测试且无额外收益。`DatePicker`/`ComboBox`/`Disclosure` 原语就绪，后续新场景（如同步调度日期、数据集 ComboBox 搜索）可直接采用。 | 高级交互原语 |

每个 Phase 独立小迭代，不破坏现有 Tailwind 样式，遵循第三原则"先走通再完美"。

### ui/ 原语清单（ADR-013 产出）

`src/components/ui/` 下沉淀了项目级 headless 包装原语，供全项目复用：

| 原语 | 底层 | 用途 | 状态 |
|------|------|------|------|
| `TextInput` / `TextAreaInput` | 原生 input + IME composition 切非受控 | 表单文本输入（IME-safe） | ✅ 全项目采用 |
| `Select` / `SelectOption` | React Aria `Select`+`Popover`+`ListBox` | 单选下拉 | ✅ 全项目采用（36 处） |
| `DataTable` | 原生 `<table>`（2026-07 回退自 React Aria `Table`，见下方「修订记录 R1」） | 只读展示表 | ✅ 展示表采用 |
| `ComboBox` | React Aria `ComboBox`+`Input`+`ListBox` | 可搜索单选 | ✅ 原语就绪 |
| `DatePicker` | React Aria `DatePicker`+`Calendar`（ISO 字符串适配） | 日期选择 | ✅ 原语就绪 |
| `Disclosure` | React Aria `Disclosure`+`DisclosurePanel` | 可折叠面板 | ✅ 原语就绪 |
| `Modal` | React Aria `ModalOverlay`+`Modal`+`Dialog` | 模态框 | ✅ 全项目采用 |

各原语均配单元测试（`__tests__/`），覆盖渲染、选择、禁用等关键路径。

### 不采用方案

- **Ant Design / Blueprint / Mantine**：自带 CSS 体系，与 Tailwind v4 冲突，改动巨大，违背现有设计系统
- **自封装 composition hook**：重新发明 React Aria 已做好的事（且漏掉 ARIA/validation/i18n），违背"组件复用最大化"原则

## 影响

- **新增依赖**：`react-aria-components`（+ 传递依赖 `@react-aria/*` / `@react-stately/*`，tree-shakeable）
- **样式**：无影响——React Aria 完全无样式，复用现有 `form-input` / `form-select` / `card` 等 CSS class
- **类型**：无影响——React Aria 提供完整 TS 类型
- **测试**：React Aria 提供 `@react-aria/test-utils` 模拟交互，后续测试更可靠
- **bundle 体积**：tree-shakeable，按需引入，Phase 1 仅 TextField 增量很小

## 验证

Phase 1 完成后验证：
1. 新建本体 displayName 输入"我的业务"中文 → IME 不被打断，正常出词
2. apiName 实时推导仍生效（composition 结束后同步）
3. 现有 Tailwind 样式不变（`form-input` class 仍生效）
4. ARIA 标签关联正确（label ↔ input）

## 附录：React 受控 input + 中文 IME 输入问题的解决经验

本节沉淀 Phase 1 调试过程中踩过的所有坑，供后续遇到同类问题时参考。这是一个看似简单实则极其隐蔽的问题——**多轮方案都失败**，最终才找到根因。

### 问题现象

新建本体/对象的「显示名称」输入框，用中文输入法打字时：
- 能看到 IME 候选窗，但**最终值进不去**（提交时 `display_name: ""`，后端 422）
- 描述字段（textarea）反而正常

### 根本原因（最终定位）

React 受控 `<input value={state} onChange={setState}>` 的致命矛盾：

1. CJK IME 输入时，浏览器触发 `compositionstart` → 多个 `input` 事件（中间态拼音）→ `compositionend`（提交最终中文）
2. React 19 **可能在 composition 期间就触发 `onChange`**（React 文档称 "fires immediately when value changes"）
3. onChange 触发父组件 `setState` → 重渲染 → **回写 `value` prop（此时 state 还是 composition 未提交的旧值）到 DOM**
4. DOM 被旧值覆盖 → IME 候选窗关闭 → 用户看到"值进不去"

关键矛盾：**受控 input 的 `value` 必须等于 state，但 composition 期间 state 还没更新到最终值，回写就清空了用户正在输入的内容**。

参考：[facebook/react#8683](https://github.com/facebook/react/issues/8683)（React 长期未解的 issue）、[facebook/react#3926](https://github.com/facebook/react/issues/3926)。

### 失败方案及原因（按尝试顺序）

#### ❌ 方案 1：React Aria Components 的 TextField

**假设**：React Aria 标榜 "composition event support"、"internationalization out of the box"，以为它处理了 IME。

**现实**：读源码 `node_modules/react-aria/dist/private/textfield/useTextField.js` 发现，普通 TextField 的 onChange 直接 `setValue`，**不检查 `isComposing`**。IME 处理只在 `useFormattedTextField`（数字/日期格式化路径）。[adobe/react-spectrum#8506](https://github.com/adobe/react-spectrum/issues/8506) 是 feature request，**未实现**。

**教训**：不要相信文档的营销话术，**读源码核实**。"supports composition events" ≠ "blocks onChange during composition"。

#### ❌ 方案 2：foxact/use-composition-input

**假设**：专门处理 IME 的轻量库（SukkaW 出品），逻辑正确（跟踪 composition 状态，期间不调 cb）。

**现实**：读文档明确写着 **"Track value of uncontrolled `<input />`"**——**只支持非受控**。在受控模式下，composition 期间不调 cb → 父 state 不更新 → 重渲染回写旧值清空 DOM。正是用户看到的"值进不去"。

**教训**：库的适用前提（受控/非受控）必须核实，不能想当然套用。

#### ❌ 方案 3：shadow state（内部状态跟踪 live value）

**假设**：wrapper 内部维护 shadow state 跟踪 DOM live value（含 composition 中间态），input 的 `value` 绑 shadow 而非父 prop，`handleChange` 无条件 `setShadow(next)`，父 `onChange` 仅非 composition 时触发。

**现实**：用 `useEffect` 同步父 prop value 到 shadow 时，**时序问题**导致父旧值（空）覆盖 shadow——提交 payload `display_name: ""`。这是用户报告 422 的直接根因。

**教训**：`useEffect` 同步外部 prop 到内部 state 有时序陷阱，多个 setState 批处理时容易产生非预期覆盖。**受控/非受控混用极易引入难调的 bug**。

#### ❌ 方案 4：原生受控 + composingRef guard 副作用

**假设**：React 16+ 自己在 composition 期间不触发 onChange，只需 guard 父组件的额外 setState（setApiName）即可。

**现实**：React 19 的行为不再可靠——可能在 composition 期间触发 onChange 回写。guard 副作用挡不住受控 value 本身的回写。

**教训**：不能依赖 "React 在 composition 期间不触发 onChange" 这个假设，React 版本间行为有差异。

### ✅ 最终方案：composition 期间切非受控

```tsx
function TextInput({ value, onChange, ...rest }) {
  const [composing, setComposing] = useState(false);
  return (
    <input
      {...rest}
      value={composing ? undefined : (value ?? '')}  // 关键
      onChange={(e) => onChange?.(e.target.value)}
      onCompositionStart={() => setComposing(true)}
      onCompositionEnd={(e) => {
        setComposing(false);
        onChange?.(e.currentTarget.value);  // flush 到父组件
      }}
    />
  );
}
```

**机制**：
- composition 期间 `value=undefined` → input **非受控** → React 不回写 → DOM 自然显示用户输入 → IME 候选窗不打断
- compositionend 后 `setComposing(false)` + `onChange(finalValue)` → 父 setState → 下次渲染 `composing=false` → `value=新state` 受控 → 值正确

这是 React 官方支持的受控/非受控切换方式（`value={undefined}` 即非受控，见 [React 文档](https://react.dev/reference/react-dom/components/input)）。本质和 Blueprint 的 `AsyncControllableInput`（[PR#4262](https://github.com/palantir/blueprint/pull/4262)）思路一致——"IME 期间临时放弃受控"。

### 关键经验教训

1. **读源码，别信营销**：React Aria 的 "composition event support" 实际未实现 IME block。文档措辞需用源码核实。
2. **核实库的适用前提**：foxact 只支持非受控，受控模式下直接失效。
3. **受控/非受控混用是雷区**：shadow state 方案的 `useEffect` 同步时序问题极难调试，能避则避。
4. **不要依赖未文档化的 React 内部行为**："composition 期间不触发 onChange" 在 React 版本间不稳定。
5. **`value={undefined}` 是官方逃生舱**：动态切换受控/非受控是 React 支持的模式，比自造 shadow state 可靠。
6. **「能输入但值不进 state」是受控回写的典型症状**：区别于「完全打不出字」（可能是 disabled/readonly/pattern 拦截）。

### 调试方法论（CDP 的局限）

本次调试的额外困难：**Chrome DevTools Protocol（CDP）无法可靠模拟真实 IME**。

- CDP 的 `Input.insertText`（`browser_fill`）一次性提交文本，**不触发 composition 事件链**，只能验证非 composition 路径
- CDP 的 `dispatchEvent(new CompositionEvent(...))` 派发的合成事件 `isTrusted=false`，**React 的合成事件系统不接收**，onCompositionStart/End 的 handler 不触发
- 因此 CDP 测试通过 ≠ 真实浏览器 IME 正常；CDP 测试失败也可能是假阳性

**可靠验证方式**：
1. `browser_fill`（insertText）验证非 composition 路径的 onChange/state 更新
2. 拦截 `window.fetch` 验证提交 payload
3. **最终必须用真实浏览器 + 真实中文输入法验收**（CDP 无法替代）

### 问题诊断决策树

遇到「中文输入异常」时按此排查：

```
输入框完全打不出字？
├─ 是 → 检查 disabled/readonly/pattern 属性，或 CSS pointer-events
└─ 否（能打但有问题）
   ├─ 能看到候选窗但值进不去？
   │  └─ 受控 input 回写问题 → 用 composition 期间切非受控方案
   ├─ 打一半被清空？
   │  └─ 父组件重渲染回写旧值 → 同上，或 guard onChange 里的额外 setState
   ├─ 候选窗闪退？
   │  └─ 重渲染导致 input 重新挂载（检查 key prop 是否用 index 且数组重建）
   └─ 英文正常中文异常？
      └─ 100% 是 IME composition 问题，按上述方案处理
```

## 修订记录

### R1（2026-07）：`DataTable` 由 React Aria `Table` 回退为原生 `<table>`

**背景**：数据源详情页「浏览 Schema」连续点击两张表（间隔数秒）时，页面崩溃报错：

> `Cell count must match column count. Found 5 cells and 0 columns.`

**根因**：React Aria Components 的 `Table` 用一个基于 fake DOM（`Document`）+ `useSyncExternalStore` 的 collection 系统，在 `commit()` 阶段逐行校验「cell 数 == column 数」。当 `ColumnList`（包裹在 `DataTable` 内）因 `activeTable` 切换导致 `activeInfo` 在 `null ↔ 有值` 之间过渡而**卸载→重新挂载**时，新 `<AriaTable>` 创建新 `Document`，React 在挂载 portal children 时，`queueUpdate` 可能在 `TableHeader` 的 `Column` 节点尚未全部 `addNode` 而 `TableBody` 的 `Cell` 节点已 `addNode` 的中间态就触发 `commit`，导致 `this.columns.length === 0` 而 cells 已有 5 个 → 抛错。

这是 React Aria 的长期已知缺陷（[adobe/react-spectrum#8127](https://github.com/adobe/react-spectrum/issues/8127) / [#9937](https://github.com/adobe/react-spectrum/issues/9937) / [#8906](https://github.com/adobe/react-spectrum/issues/8906)），**官方未修复**。先前的 `key={columnsKey}` 补丁无效——对静态列定义（`ColumnList` 列固定为 5 列）key 恒定不触发重建，且竞态发生在重挂载过程中而非列结构变化时。

**决策**：`DataTable` 改用原生语义 `<table>` + `<thead>/<tbody>/<th>/<td>`，复用现有 `.data-table` CSS class（与 `DatasetDetail`/`SyncTaskDetail` 等已在用的原生表完全一致）。**对外 API 完全不变**（`columns` / `rows` / `renderCell` / `rowKey` / `rowClassName` / `onRowAction` / `aria-label`），调用方（`PreviewTable` / `ColumnList` / `DatasetsPage`）零感知。

**依据**：
1. 所有调用方均为**纯只读展示**，无一处使用 React Aria `Table` 的键盘行导航 / 行选择能力（`ColumnList.selectable` 已无调用方传入，`DatasetsPage` 行内点击通过 cell 内 `<button>` 实现）。回退不丢失任何实际功能。
2. `DataTable` 源码注释本就声明「只读展示表用它、嵌入表单控件的编辑表用原生 `<table>`」，回退与该定位一致。
3. RAC `Table` 的 collection 缺陷是长期风险，原生表无此问题且性能更优（无 fake DOM + portal + `useSyncExternalStore` 开销）。
4. 保留 `onRowAction`（行点击 + Enter/Space 键盘激活）以维持 API 兼容，满足基本可达性。

**验证**：本地 k3s 环境实测，慢间隔（2s）/ 快间隔（50ms）/ 12 次快速切换三场景均零报错，页面不再进入 ErrorBoundary；`tsc -b` + `vite build` + 368 个 vitest 用例全绿（含新增 7 个 `DataTable` 单测）。

**影响范围**：仅 `src/components/ui/DataTable.tsx`；其余 React Aria 原语（`TextInput` / `Select` / `ComboBox` / `DatePicker` / `Disclosure` / `Modal`）不受影响，继续采用 React Aria Components。

## 参考

- [React Aria Components 文档](https://react-spectrum.adobe.com/react-aria/components.html)
- [useTextField 源码](https://react-aria.adobe.com/TextField/useTextField.html)（核实：普通 TextField 不处理 IME）
- [foxact/use-composition-input](https://foxact.skk.moe/use-composition-input)（只支持非受控）
- [Blueprint AsyncControllableInput PR#4262](https://github.com/palantir/blueprint/pull/4262)（IME 期间临时非受控的原始思路）
- [facebook/react#8683](https://github.com/facebook/react/issues/8683)（React 受控 input + IME 长期 issue）
- [facebook/react#3926](https://github.com/facebook/react/issues/3926)（早期 composition 事件问题）
- [adobe/react-spectrum#8506](https://github.com/adobe/react-spectrum/issues/8506)（React Aria TextField IME feature request，未实现）
- [sinchang/react IME 输入总结](https://wiki.sinchang.me/til/react/ime-input)
- [shadcn/ui 2026-01 Base UI changelog](https://ui.shadcn.com/docs/changelog/2026-01-base-ui)
