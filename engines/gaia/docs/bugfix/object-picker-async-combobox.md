# 对象选择器服务端搜索 + react-aria async combobox 踩坑复盘

**记录时间**: 2026-07-01
**影响模块**: 前端 `ObjectPicker` / `ComboBox` / `ExecuteActionDialog`，后端 `/objects/textsql`
**状态**: ✅ 已修复 (2026-07-01)

---

## 背景：为什么做这个

Action 执行时，对象引用参数（如「分配线索」的 `leadId` / `salesConsultantId`）需要一个对象选择器。原实现 `ObjectPicker` 一次性 `loadObjects(limit:50)` + 前端过滤，存在两个严重问题：

1. **超过 50 条看不见** —— Lead/SalesConsultant 动辄上千条，用户在前 50 条里找不到，只能去别处查到主键手敲
2. **只按 title/pk 过滤** —— 用户记得"张三"但 title 可能是工号，搜不到

这是"把复杂留给用户"的反模式。参照 Palantir Foundry Object Dropdown 的最佳实践，改为**服务端搜索 combobox**：聚焦即显示候选、输入即搜索、显示 title 存 pk。

---

## 最佳实践研究结论

研究了 Palantir Foundry、Mendix、USWDS、NN/G、Baymard 关于"对象引用参数选择"的共识：

| 实践 | 说明 |
|------|------|
| 显示 title 存 pk | 用户看可读名称，底层存主键（Gaia 已做到） |
| 服务端搜索 | 大数据集不能全量加载，输入即后端模糊查（解决 50 条上限） |
| 多属性搜索 | 搜索不止匹配 title/pk，可配置搜哪些属性 |
| 悬停预览 | 选中前预览对象其他属性，辅助确认 |
| 属性预过滤 | 候选集按属性条件预缩小（如只显示"在岗"销售顾问） |
| Combobox 阈值 | >15 选项必须用 combobox（可输入过滤），≤15 用 select |

落地分 4 阶段：
- **P0 服务端搜索**（解决 50 条看不见）— 已完成
- **P1 多属性搜索 + 悬停预览** — 待做
- **P2 属性预过滤**（静态值 + 联动其他参数）— 待做
- **唯一值自动预填** — 待做

P2 联动过滤的关键决策：**参数按依赖关系拓扑排序**，被引用的参数（如 leadId）排在依赖它的参数（如 salesConsultantId）前面，用户按顺序填；被引用参数未填时，依赖它的 picker 禁用 + 提示"请先选择 XXX"。

---

## 核心踩坑：react-aria-components async combobox

### 现象

服务端搜索改完后，**首次聚焦 → 异步加载数据 → 数据到了 popover 没打开**。切换到其他输入再回来，就正常了。

### 根因

这是 **react-aria-components 的已知缺陷**，多个 GitHub issue 确认：
- [#5234 Async Combobox not rendering initial result](https://github.com/adobe/react-spectrum/issues/5234)
- [#5690 menu doesn't re-open when async items arrive after empty response](https://github.com/adobe/react-spectrum/issues/5690)
- [#9820 useComboBox + useAsyncList does not show results after empty list](https://github.com/adobe/react-spectrum/issues/9820)

**触发条件**：用**静态 children** 渲染 options（`options.map(o => <ListBoxItem>)`）时，items 异步到达后 popover 不打开。React Aria 内部把静态 children 当成"集合不变"，async 更新时它的 open/close 状态机出问题。

### 正确解法：动态集合模式

React Aria 官方文档的 async 示例用的是**动态集合**：`items` prop（受控）+ **render-function children**（`{(item) => <ListBoxItem>}`），而非静态 children。

```tsx
// ❌ 错误：静态 children（async items 到达后 popover 不开）
<ComboBox items={items}>
  {options.map(o => <ListBoxItem key={o.id}>{o.label}</ListBoxItem>)}
</ComboBox>

// ✅ 正确：动态集合 + render function
<ComboBox items={items}>
  {(item) => <ListBoxItem id={item.id} textValue={item.label}>{item.label}</ListBoxItem>}
</ComboBox>
```

动态集合让 React Aria 正确管理 items 的增删，async 更新时 popover 行为正常。

### react-aria-components vs React Spectrum 的 API 差异

踩坑过程中误用了 React Spectrum（`@react-spectrum/components`）的 prop，它们在 react-aria-components 里**不存在**：

| prop | React Spectrum | react-aria-components |
|------|:---:|:---:|
| `isOpen`（受控开关） | ✅ | ❌（只有 `onOpenChange` 报告状态，不能受控） |
| `loadingState` | ✅ | ❌（用 `renderEmptyState` 自己渲染加载态） |
| `menuTrigger` | ✅ | ✅ |
| `onOpenChange` | ✅ | ✅ |
| `items`（动态集合） | ✅ | ✅ |
| `allowsEmptyCollection` | ✅ | ✅ |

react-aria-components 的 ComboBox **不能受控开关 popover**。只能用 `menuTrigger="focus"`（聚焦即开）+ `onOpenChange`（观察开关）+ `allowsEmptyCollection`（空集合也显示）+ 动态 `items`。不要试图用 `isOpen` 受控——它不认。

### 正确的 async combobox 配方

```tsx
<AriaComboBox
  items={items}                    // 受控动态集合（关键！）
  allowsEmptyCollection            // 空集合时也显示 popover（显示 renderEmptyState）
  menuTrigger="focus"              // 聚焦即开
  onOpenChange={(open) => ...}     // 观察开关，驱动搜索
  defaultFilter={() => true}       // 服务端已搜，禁用前端再过滤
  selectedKey={value}
  onSelectionChange={(k) => ...}
>
  {({ isInvalid }) => (
    <>
      <Input ... />
      <Button>▾</Button>
      <Popover>
        <ListBox
          renderEmptyState={() => loading ? '搜索中…' : '无匹配项'}
        >
          {(item) => (            // render function（关键！非静态 map）
            <ListBoxItem id={item.id} textValue={item.label}>
              {item.label}
            </ListBoxItem>
          )}
        </ListBox>
      </Popover>
    </>
  )}
</AriaComboBox>
```

---

## 其他踩坑

### 1. onInputChange 不要实时提交中间文本

最初 `onInputChange` 里把用户输入的文本实时 `onChange(text)` 提交给父组件。在 `allowsCustomValue` 模式下，父组件更新 `value`（selectedKey），React Aria 用 selectedKey 重置 input 文本，**把用户正在打的字清空了**。

解法：`onInputChange` 只更新本地搜索词，**不提交**。自定义值的提交放到 `onBlur`（用户离开输入框时若文本不匹配任何选项，才当主键提交）。

### 2. comboKey remount 导致双 picker 抢焦点

多对象参数的表单（如 allocateLead 有 leadId + salesConsultantId 两个 picker），曾用 `key={options.length}` 在搜索结果回来时 remount ComboBox。remount 会丢焦点到另一个 picker，表现为"两个 picker 互相抢焦点"。

解法：**不要 remount**。动态集合模式下 React Aria 会原地更新 listbox items，remount 是多余的。

### 3. CDP 自动化无法验证 React Aria popover

Chrome DevTools Protocol 的 `input.focus()` / `element.click()` 不触发 React Aria 的 `onOpenChange`（React Aria 的 open 逻辑依赖真实用户交互的 focus 事件链）。CDP 自动化里 popover 始终关闭，无法验证。

**教训**：React Aria 组件的交互验证必须用真实浏览器手动测，CDP 只能验证数据流（network 请求、state 变化），不能验证 popover 开关。不要在 CDP 上耗费时间调试 popover 打开问题。

---

## 后续路标

- **P1 多属性搜索**：`ActionTypeParameter` 加 `search_properties: list[str]`，ObjectPicker 搜索时拼进 WHERE OR 子句；建模侧 ParameterList 加"搜索属性"多选
- **P1 悬停预览**：下拉项聚焦时展开显示对象其他属性（mini preview card），搜索接口返回多列
- **P2 属性预过滤**：`ActionTypeParameter` 加 `object_filter`（静态值 + param_ref 联动），ExecuteActionDialog 按依赖拓扑排序参数，被引用参数未填时禁用依赖它的 picker
- **唯一值自动预填**：搜索返回 1 条时自动选中

---

## 关联代码

- `src/web-ui/src/components/ObjectPicker.tsx` — 服务端搜索 combobox
- `src/web-ui/src/components/ui/ComboBox.tsx` — React Aria 动态集合封装
- `src/web-ui/src/api/client.ts` — `searchObjects`（构造 WHERE LIKE 走 textsql）
- `src/ontology/routes/query/__init__.py` — `/objects/textsql`（复用，零改动）

## 关联文档

- [CLAUDE.md](../../CLAUDE.md) §「第一原则：把复杂留给自己，把简单留给用户」
- [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) §4.6（对象↔数据集绑定）
