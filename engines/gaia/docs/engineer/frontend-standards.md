# Gaia 前端工程规范

> 以 CLAUDE.md 第四原则（前端开发质量守则）为基础，补充类型安全、样式体系、无障碍、测试策略四方面的强制性标准。
> 与第四原则有重叠的条目以此处更严格的版本为准。
>
> **最佳实践**（怎么做对）见 [`frontend-best-practices.md`](./frontend-best-practices.md)：Tailwind v4 暗色主题、Cytoscape+React 集成、命令式库模式、性能分包等。

## 类型安全（红线）

| # | 规范 | 说明 |
|---|------|------|
| T1 | `strict: true` 开启 | `tsconfig.app.json` 必须含 `"strict": true`（含 strictNullChecks、strictFunctionTypes 等全部子检查） |
| T2 | 禁止 `any` | 所有类型必须显式标注。极少数无法标注场景使用 `unknown` + 类型守卫 |
| T3 | API 类型集中维护 | 所有 HTTP 响应类型定义在 `src/web-ui/src/types/index.ts`，与后端 pydantic Schema 保持同步。禁止在组件内 `as` 强制转换 fetch 返回值 |
| T4 | `type-check` 独立脚本 | `package.json` 含 `"type-check": "tsc --noEmit"` 命令，CI 中独立于 build 执行 |

## 样式体系（红线）

> 2026-06 重构：已全面迁移至 **Tailwind v4.3** CSS-first 架构，采用两层 token + 亮暗主题。

| # | 规范 | 说明 |
|---|------|------|
| S1 | 禁止内联 `style={{}}` | 全项目已清零（388→0）。所有样式走 Tailwind 工具类或语义类。极少数动态值（如 Cytoscape 运行时读变量）例外 |
| S2 | 禁止硬编码颜色 | 颜色必须走语义 token。TSX 用 Tailwind 工具类（`bg-surface`/`text-accent`），CSS 用 `var(--color-*)`。禁止 HEX/RGB 字面量 |
| S3 | 两层 token 架构 | `@layer base` 定义原始色板（`:root` 暗 / `.light` 亮）→ 语义 token（`--color-surface` 等）→ `@theme inline` 注册为 Tailwind 工具类。**关键：必须用 `@theme inline`，否则亮暗无法覆盖**（v4 翻车点） |
| S4 | 亮暗主题切换 | `useTheme` hook + `ThemeToggle` 组件，`<html>` 切 `.light`/`.dark` 类。`index.html` 内联脚本防 FOUC。新颜色必须同时在 `:root` 和 `.light` 定义 |
| S5 | 条件类用 `cn()` | `src/lib/cn.ts`（clsx + tailwind-merge）。禁止模板字符串拼接 `className={`x${cond?' active':''}`}` |
| S6 | 语义类用 `@apply` | 仍保留的语义类（`btn`/`card`/`form-input`/`dialog` 等）在 `index.css` 的 `@layer components` 用 `@apply` 组合工具类，TSX 可读且样式走 token 体系 |
| S7 | 新增 token 流程 | 先在 `index.css` `@layer base` 的 `:root` + `.light` 定义 `--color-*`，再在 `@theme inline` 注册，即可生成 `bg-*`/`text-*` 工具类 |

## 无障碍 A11y（红线）

以 WCAG 2.2 AA 为合规基线（内部 B2B 工具无需 AAA）。

| # | 规范 | 说明 |
|---|------|------|
| A1 | 键盘可达 | 所有交互元素支持 Tab 导航、Enter/Space 激活（第四原则已覆盖，此处强化） |
| A2 | Dialog 焦点锁定 | 弹窗打开时焦点移入弹窗内，Tab 在弹窗内循环，Escape 关闭，`aria-modal="true"` |
| A3 | 图标按钮标注 | 无文本的纯图标按钮必须有 `aria-label`（如 `<button aria-label="删除对象">🗑</button>`） |
| A4 | 表单关联 | 每个 `<input>` 必须有对应的 `<label htmlFor="...">`，点击 label 聚焦输入框 |

**推荐库（阶段2引入）**：`@radix-ui/react-dialog` 替代手工实现的弹窗，免费获得焦点锁定+Escape关闭+无障碍树映射。

## 测试策略（阶段2启动）

当前前端无测试。按以下优先级逐步建立：

| 优先级 | 类型 | 工具 | 覆盖内容 |
|--------|------|------|----------|
| 1 | E2E（关键流程） | Playwright | 创建本体→创建对象→图谱交互→AI批量导入 |
| 2 | E2E（数据连接） | Playwright | 创建数据源→测试连接→探索表→创建同步任务 |
| 3 | 自定义 Hook 单元测试 | vitest | 复杂状态机、需要 mock API 的 hooks |
| — | 纯渲染组件单元测试 | 不需要 | 第四原则"组件覆盖四种状态"已在 Code Review 中检查 |
| — | Storybook 视觉回归 | 暂缓 | 组件库稳定后再引入 |

**Playwright 定位器规范**：
- 强制使用无障碍定位器：`page.getByRole('button', { name: '创建' })`、`page.getByLabelText('API 名称')`
- 禁止 CSS class 选择器（`.btn-primary`）、XPath、`data-testid`（除非无可达语义定位器）
- 设计动机：强制使用无障碍定位器 → 倒逼组件具备正确的 ARIA 语义 → 一举两得

## 验证协议

提交前检查链（第四原则已含 `npm run build`，此处补充独立步骤）：

```bash
npm run lint          # ESLint 零错误（已有）
npm run type-check    # tsc --noEmit 零错误（需新增脚本）
npm run build         # Vite 构建成功（已有，oxc 校验）
```

未来加入：

```bash
npm run test:e2e      # Playwright 关键流程
```

AI 助手在声明前端任务"完成"前，必须确认以上三步全部通过。任一步失败 → 自修复后才能提交。

## 设计 Token 参考

> 2026-06 重构：两层架构。原始色板在 `@layer base`（`:root` 暗 / `.light` 亮），
> 语义 token 经 `@theme inline` 注册为 Tailwind 工具类（`bg-*`/`text-*`/`border-*`/`rounded-*`/`font-*`）。

| 类别 | 语义 token（暗 / 亮） | Tailwind 工具类示例 |
|------|----------------------|--------------------|
| 背景 | `--color-bg`(#0c1117 / #f7f6f3)、`--color-sidebar`、`--color-surface` | `bg-bg` `bg-sidebar` `bg-surface` |
| 边框 | `--color-border`、`--color-border-light` | `border-border` `border-border-light` |
| 文字 | `--color-text`、`--color-text-secondary`、`--color-text-muted` | `text-text` `text-text-secondary` `text-text-muted` |
| 强调 | `--color-accent`、`--color-accent-hover`、`--color-accent-text` | `bg-accent` `text-accent` `border-accent` |
| 次要强调 | `--color-teal`、`--color-teal-hover` | `text-teal` `bg-teal` |
| 语义色 | `--color-success`、`--color-warning`、`--color-error` | `text-success` `bg-error` `border-warning` |
| 滚动条 | `--scrollbar-thumb`/`-thumb-hover`/`-track`（中性灰，非 accent） | CSS `var(--scrollbar-*)`（不注册为工具类，仅 `index.css` 滚动条规则消费） |
| 圆角 | `--radius-sm/md/lg/pill` | `rounded-sm` `rounded-md` `rounded-lg` `rounded-pill` |
| 字体 | `--font-ui`、`--font-mono` | `font-ui` `font-mono` |
| 动画 | `--animate-fade-in/blink/pulse` | `animate-fade-in` `animate-blink` `animate-pulse` |

**透明度变体**用 `color-mix` 或 Tailwind 透明度修饰符：
- `bg-accent/10`、`bg-white/5`、`bg-white/[0.04]`
- 复杂场景用 arbitrary：`bg-[var(--accent-bg)]`、`bg-[color-mix(in_srgb,var(--color-success)_15%,transparent)]`

**旧变量名别名**（`--bg`/`--surface`/`--accent` 等）仍在 `:root` 保留指向 `var(--color-*)`，迁移期兼容，新代码应直接用 `--color-*`。
