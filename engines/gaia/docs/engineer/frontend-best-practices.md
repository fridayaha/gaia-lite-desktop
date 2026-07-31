# Gaia 前端最佳实践

> 本文档沉淀前端开发中积累的经验与最佳实践，作为开发者的独立参考手册。
> 与 [`frontend-standards.md`](./frontend-standards.md)（红线规范）互补：本文讲"怎么做对"，红线讲"不能做什么"。
>
> 最后更新：2026-06

---

## 一、Tailwind v4 暗色主题（最大坑点）

### 1.1 `@theme` vs `@theme inline` —— 决定暗色能否覆盖

这是 Tailwind v4 最易翻车的点，必须牢记：

| 写法 | 工具类编译结果 | 暗色覆盖是否生效 |
|------|--------------|----------------|
| `@theme { --color-surface: #fff }` | `.bg-surface { background: #fff }`（值内联） | ❌ 不生效 |
| `@theme inline { --color-surface: var(--color-surface) }` | `.bg-surface { background: var(--color-surface) }`（引用变量） | ✅ 生效 |

**规则**：所有可切换主题的 token 一律用 `@theme inline`。只有"绝不随主题变"的固定值（如 `--radius-*`、`--font-*`）才用 `@theme`。

### 1.2 两层 token 架构

```
原始色板（@layer base :root/.light）
  └─ 语义 token（--color-surface 等）
       └─ @theme inline 注册为 Tailwind 工具类（bg-surface 等）
```

- `:root` 默认暗色，`.light` 覆盖亮色值
- 语义 token 是切换的"开关"，组件只消费语义 token（通过工具类）
- 旧变量名（`--bg`/`--surface`）保留为 `var(--color-*)` 别名，迁移期兼容

### 1.3 FOUC（主题闪烁）防护

`index.html` 内联**同步脚本**在 React 加载前设 `<html>` 类：

```html
<script>
  (function () {
    var stored = localStorage.getItem('gaia-theme');
    var dark = stored ? stored === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.classList.toggle('dark', dark);
    document.documentElement.classList.toggle('light', !dark);
  })();
</script>
```

`useTheme` hook 只管运行时切换 + 持久化，不负责首屏防闪烁。

### 1.4 组件语义类用 `@apply`

保留语义类名（`btn`/`card`/`form-input`）以便 TSX 可读，但样式走 Tailwind token 体系：

```css
@layer components {
  .btn {
    @apply cursor-pointer rounded-md border border-border bg-surface px-3.5 py-1.5 text-sm font-medium text-text transition-all;
  }
}
```

---

## 二、Cytoscape + React 集成

### 2.1 核心原则：实例只创建一次，用 CSS 显隐切视图

**❌ 错误做法**（曾导致布局丢失、时序竞态、HMR 不稳）：

把 cytoscape 生命周期绑在 `viewMode` 上，切视图时 `cy.destroy()` + 重建。

**✅ 正确做法**：

```tsx
// 父组件：三个视图都用 hidden 显隐，保证图谱组件常驻不卸载
<div className={cn(viewMode !== 'canvas' && 'hidden')}>
  <OntologyGraph ... />
</div>
```

```tsx
// OntologyGraph：实例只在挂载时创建，卸载时 destroy
useEffect(() => {
  (async () => {
    const cytoscape = (await import('cytoscape')).default;
    const cy = cytoscape({ container, ... });
    cyRef.current = cy;
  })();
  return () => { cyRef.current?.destroy(); };
}, []); // 空依赖，仅挂载时跑
```

官方维护者明确建议：**复用实例，隐藏容器而非销毁**。这样实例/布局/缩放/拖拽位置全保留，切回瞬间恢复。

### 2.2 数据增量同步

数据变化时 diff 增删节点/边，**已有节点位置不动**：

```ts
function syncElements(cy, objectTypes, links, isFirstSync) {
  const existingIds = new Set(cy.nodes().map(n => n.id()));
  const hasNewNodes = objectTypes.some(ot => !existingIds.has(ot.id));
  // 删除不再存在的、新增缺失的、更新已有的...
  // 布局只在首次或出现新节点时跑，用户拖拽/缩放被保留
  if (isFirstSync || hasNewNodes) runLayout(cy);
}
```

### 2.3 异步初始化的时序处理

动态 import 导致 cytoscape 实例创建是异步的，数据同步 useEffect 可能在实例就绪前跑：

**解法**：创建 effect 内就绪后手动触发首次同步；后续数据变化由独立 effect 处理（实例已存在直接同步）。

### 2.4 主题感知

Cytoscape style API 不接受 `var()`，需运行时读解析后的颜色值：

```ts
function getThemeColors() {
  return {
    surface: getComputedStyle(document.documentElement).getPropertyValue('--color-surface').trim(),
    // ...
  };
}

// theme 变化时重建 stylesheet
useEffect(() => {
  cyRef.current?.style(buildCyStyles());
}, [theme]);
```

### 2.5 扩展懒加载注册

```ts
let extensionsRegistered = false; // 模块级标志防重复注册

useEffect(() => {
  (async () => {
    const cytoscape = (await import('cytoscape')).default;
    if (!extensionsRegistered) {
      const cxtmenu = (await import('cytoscape-cxtmenu')).default;
      const navigator = (await import('cytoscape-navigator')).default;
      cytoscape.use(cxtmenu);
      cytoscape.use(navigator);
      extensionsRegistered = true;
    }
    // ...
  })();
}, []);
```

- cleanup 时 `menu.destroy()`，navigator 随 `cy.destroy()` 自动清理
- 扩展无类型声明 → 加 `src/types/cytoscape-extensions.d.ts`

### 2.6 稳定回调 ref 避免重绑

cytoscape 事件处理器在实例创建时绑定，若依赖 props 回调，回调变化会导致闭包过期：

```ts
const onSelectObjectRef = useRef(onSelectObject);
useEffect(() => { onSelectObjectRef.current = onSelectObject; }, [onSelectObject]);

// cytoscape 事件内用 ref
cy.on('tap', 'node', (evt) => {
  onSelectObjectRef.current?.(evt.target.id());
});
```

---

## 三、命令式库的 React 集成模式

适用于 Cytoscape、CodeMirror、Monaco、Leaflet 等命令式图形库：

1. **ref 存实例**：`const cyRef = useRef<Core | null>(null)`
2. **useEffect 创建 + cleanup 销毁**：空依赖，仅挂载/卸载时跑
3. **稳定回调 ref**：外部回调通过 ref 传入命令式事件处理器，避免重绑
4. **数据变化用独立 effect 增量同步**：不重建实例
5. **容器尺寸**：确保容器有明确宽高（`h-full w-full`），否则渲染异常
6. **CSS 显隐而非条件渲染**：切视图时用 `hidden` 类，保留实例状态

---

## 四、性能分包

### 4.1 重组件动态导入

Cytoscape 等大依赖用 `await import()`，仅在需要时加载：

```ts
const cytoscape = (await import('cytoscape')).default;
```

**收益**（本项目实测）：首屏 JS 811KB → 376KB（gzip 250KB → 112KB，-55%）

### 4.2 路由级分包（可选）

页面用 `React.lazy` + `Suspense`，访问路由时才加载。当首屏已够小（<150KB gzip）时收益递减，按需采用。

---

## 五、工作方法论

### 5.1 先研究再动手

打补丁前先问"是不是架构错了"。研究官方/社区最佳实践比闷头试错高效。

**反面案例**：Cytoscape 图谱不显示，连续打补丁（保存/恢复位置、cyReady、布局参数……）越改越乱。后查最佳实践发现根因是"实例绑 viewMode 反复 destroy/重建"，抽独立组件 + CSS 显隐一次性解决。

### 5.2 交互式选型优于文字描述

布局/样式选型，建临时预览页用真实数据对比，让用户直观选择，比文字描述高效。**选型定稿后删除预览页，不留技术债**。

### 5.3 数据问题的健壮性处理

发现"边引用不存在的节点"等问题：
1. 先验证数据（curl + 脚本分析），确认是后端残留还是前端 bug
2. 前端加校验跳过坏数据 + 显示提示
3. 记录后端问题作为后续待办，不阻塞前端

### 5.4 性能问题量化

分包前后用 build 输出的 chunk 体积量化对比，用数字说话而非"感觉快了"。

### 5.5 不盲目"修复"预期行为

如 React StrictMode 开发期双请求是压力测试，非 bug。验证 cleanup 写对后确认生产无影响即可，不"修复"。

---

## 六、Pre-commit Hook 协作

- Prettier/ESLint 失败时，先 `npx prettier --write` / `npx eslint --fix` 修复再提交
- 多余的 `eslint-disable` 会触发 "Unused eslint-disable" warning，定期用 `eslint --fix` 清理
- pre-commit 会 stash unstaged 文件，commit 后恢复——**别在 commit 中混入无关文件**，用 `git add <具体路径>` 精确暂存

---

## 附：相关文档

- [`frontend-standards.md`](./frontend-standards.md) —— 前端红线规范（类型安全、样式体系、A11y）
- [`../architecture/`](../architecture/) —— 架构设计文档
- [`../web-ui/ontology-manager.md`](../web-ui/ontology-manager.md) —— 本体管理器设计
