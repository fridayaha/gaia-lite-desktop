# UnionAgents Admin 前端开发规范

本规范是 Admin 前端（`apps/admin`，基于 vue-pure-admin 二次开发）团队开发的**单一真相源**。所有新增页面、样式、弹框、菜单、国际化必须遵循。CLAUDE.md 中的「Admin 前端硬规则」是本规范的精简版，供 AI 协作时在上下文内遵循；完整约定以本文件为准。

> 本规范范围：仅 Admin 端。enduser / landing / docs / 移动端暂不纳入。

## 1. 技术栈与目录约定

- 技术栈：Vue 3.5 + Element Plus 2.14 + TypeScript 6 + Tailwind v4 + ECharts 6 + Pinia 3 + vue-router 5 + vue-i18n 11 + Vite 8，基于 vue-pure-admin。
- 模块目录标准结构（`src/views/<模块>/`）：

  ```
  src/views/<module>/
  ├── index.vue              # 列表/主页
  ├── form.vue 或 form/      # 表单（简单用单文件，复杂用目录）
  ├── detail/index.vue       # 详情页
  ├── components/            # 模块私有组件（PascalCase 文件名）
  └── utils/
      ├── hook.tsx           # 表格配置（columns/分页/操作列 cellRenderer）
      ├── rule.ts            # 表单校验规则
      └── types.ts           # 模块类型
  ```

- 命名约定：
  - `views/` 目录用 **kebab-case**（如 `agent-instances/`、`skill-studio/`）。
  - 通用组件用 **`Re` 前缀**（vue-pure-admin 约定：`ReDialog`/`ReIcon`/`ReAuth`/`ReCol`...），位于 `src/components/`。
  - 模块私有组件放模块 `components/` 内，**PascalCase** 文件名（如 `ListCard.vue`）。
- API 模块：`src/api/manager/<module>.ts`（管理后端）、`src/api/controller/<module>.ts`（引擎控制面）。

## 2. 路由与菜单

- 路由模块文件放 `src/router/modules/<module>.ts`，导出 `satisfies RouteConfigsTable` 的配置对象。**自动导入**（`router/index.ts` 用 `import.meta.glob` eager 扫描），无需手动注册。
- 菜单顺序用 `meta.rank`（取自 `src/router/enums.ts` 常量），权限用 `meta.roles`。菜单由路由 `meta` 自动生成（`src/router/utils.ts`：按 rank 排序 → 建层级 → 按 roles 过滤），**不要**另写静态菜单配置。
- 详情/不进菜单的页面：`meta.showLink: false`。
- `meta.title` 存 i18n key（`t("menus.pureXxx")` 的返回值即 key 字符串）；`meta.icon` 用字符串形式 `"ri:stack-line"`（带冒号走在线图标）。

  ```ts
  export default {
    path: "/agent-definitions",
    redirect: "/agent-definitions/index",
    meta: { icon: "ri:stack-line", title: $t("menus.pureAgentDevelopment"),
            rank: agentDefinitions, roles: ["系统管理员", "平台管理员"] },
    children: [
      { path: "/agent-definitions/index", name: "AgentDefinitionList",
        component: () => import("@/views/agent-definitions/index.vue"),
        meta: { title: $t("menus.pureAgentDevelopment"), roles: [...] } },
      { path: "/agent-definitions/detail/:id", name: "AgentDefinitionDetail",
        component: () => import("@/views/agent-definitions/detail/index.vue"),
        meta: { title: $t("definition.detailTitle"), showLink: false, roles: [...] } }
    ]
  } satisfies RouteConfigsTable;
  ```

- 新增/重命名菜单项必须同步 `apps/docs/content/guide/pages/*.md` 里的"单击 X 菜单"引用 + `apps/docs/.vitepress/config.ts` 的 sidebar（详见 CLAUDE.md「资料中心同步」节）。

## 3. 页面布局

- 最外层统一 `<div class="main">`（与智能体管理列表页一致）。
- 顶部工具条左右分栏：创建/Action 按钮在左，筛选 + 搜索在右。

  ```html
  <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
    <div class="flex items-center gap-3 flex-wrap"><!-- 左：创建/Action 按钮 --></div>
    <div class="flex items-center gap-3 flex-wrap"><!-- 右：筛选下拉 + 搜索框 --></div>
  </div>
  ```

- 搜索框统一：`el-input` + `clearable` + `style="width: 260px"`，搜索图标放 `#suffix` slot，用 `v-show="searchText.length === 0"` 控制显隐（有内容时露出 clear 按钮）。筛选下拉在前、文本搜索在后。

  ```html
  <el-input v-model="searchText" style="width: 260px" clearable ...>
    <template #suffix>
      <el-icon class="el-input__icon">
        <SearchLine v-show="searchText.length === 0" />
      </el-icon>
    </template>
  </el-input>
  ```

- 卡片网格页：`el-row :gutter="12"` + `el-col :xs="24" :sm="12" :md="6"` + 自定义卡片组件。
- 表格页：`PureTableBar` 包 `pure-table`，`#buttons` slot 放筛选区，默认 slot 渲染表格；表格配置（columns/分页/操作列 cellRenderer）抽到 `utils/hook.tsx`。
- 分页统一：

  ```html
  <el-pagination v-model:current-page="..." class="float-right mt-1"
    :page-sizes="[12, 24, 36, 48]" :background="true"
    layout="total, sizes, prev, pager, next, jumper" .../>
  ```

- 每页顶部建议放 `<DocsLink to="..." />` 指向对应文档。

## 4. Dashboard 布局

- 双层容器：`<div class="main"><div class="welcome">...</div></div>`。
- `.welcome` 设 `max-width: 1400px; margin: 0 auto;`。
- 管理员视图左右分栏：左侧 `md:17`（~73%），右侧 `md:7`（~27%）。
- 图表卡片用 `.chart-card` + `.chart-fill` 实现自适应高度填充（`.chart-fill { flex: 1; min-height: 170px }`）。
- 数字统计卡用 `ReNormalCountTo`（`src/components/ReCountTo`）做滚动动画。
- 等高行用 `.equal-height { display: flex; flex-wrap: wrap }`。

## 5. 弹框与表单

- 列表页增删改用命令式 `addDialog`（`@/components/ReDialog`）：`contentRenderer` 渲染表单组件，`beforeSure` 里 `await ruleFormRef.value.validate()` → 调 API → `done()` 关闭。统一 `closeOnClickModal: false`、`draggable: true`。

  ```ts
  import { addDialog } from "@/components/ReDialog";
  addDialog({
    title: t("..."),
    width: "46%",
    draggable: true,
    closeOnClickModal: false,
    contentRenderer: () => h(editForm, { ref: ruleFormRef }),
    beforeSure: async (done, { closeLoading }) => {
      await ruleFormRef.value.validate();
      await updateXxxApi(...);
      done();
    }
  });
  ```

- 复杂多步表单用独立路由页（`form.vue` + `el-steps`），不用弹框。
- 表单规则抽到 `utils/rule.ts`，导出 `reactive<FormRules>` 或工厂函数 `makeFormRules(...)`（校验依赖运行时条件时用工厂）。自定义 validator 文案走 i18n。
- `el-form` 统一 `label-position="top"` + `:rules="formRules"` + `ref="ruleFormRef"`，提交前必须 `await ruleFormRef.value.validate()`。

## 6. API 调用层

- 统一用 `src/utils/http` 的 `http` 单例（axios 封装，自动注入 `Authorization` + token 过期自动 refresh）。**禁止**裸 axios/fetch。
- API 函数命名：`getXxxApi` / `createXxxApi` / `updateXxxApi` / `deleteXxxApi`。URL 前缀 `/api/manager/`（管理后端）或 `/api/controller/`（引擎控制面）。
- 每个模块文件导出 `type XxxResponse`。列表响应结构按是否分页区分：
  - **分页列表**（后端按 page/page_size 切片）：`{ items, total, page, page_size }`，类型 `XxxListResponse`。
  - **非分页全量列表**（后端一次性返回全部，前端客户端分页）：`{ items, total }`（`total = items.length`），类型 `XxxListResponse`。**禁止**列表接口直接返回裸数组。
- 调用处用泛型 `http.request<XxxResponse>(...)`。

  ```ts
  import { http } from "@/utils/http";
  export type AgentDefinitionResponse = { id: string; name: string; ... };
  export type AgentDefinitionListResponse = { items: AgentDefinitionResponse[]; total: number; page: number; page_size: number; };
  export const getDefinitionsApi = (params?: Record<string, any>) =>
    http.request<AgentDefinitionListResponse>("get", "/api/manager/agent-definitions", { params });
  export const createDefinitionApi = (data: { name: string; ... }) =>
    http.request<AgentDefinitionResponse>("post", "/api/manager/agent-definitions", { data });
  ```

## 7. 图标使用

- 主用 `~icons/ri/xxx` 编译期导入（unplugin-icons），零运行时请求。

  ```ts
  import AddFill from "~icons/ri/add-circle-line";
  import SearchLine from "~icons/ri/search-line";
  import More from "~icons/ep/more-filled";   // ep = element-plus 图标集
  ```

- **禁止**把图标字符串（如 `"ri:chat-1-line"`）直接传给 `IconifyIconOffline`，该组件只接受从 `~icons/` 导入的组件对象。
- `el-button :icon` 用 `useRenderIcon(SomeIcon)` 适配器（`@/components/ReIcon/src/hooks`）。
- 菜单图标用字符串 `"ri:stack-line"`（带冒号，sidebar 渲染时走 IconifyIconOnline）。
- JSX 中用 `IconifyIconOffline` 时需显式 `import { IconifyIconOffline } from "@/components/ReIcon"`，且 `width`/`height`/`color` 用 `{...({ width: "18", height: "18" } as any)}` 传递。

## 8. 样式与设计 Token

- 优先级：**Tailwind v4 > scoped SCSS > 全局 SCSS**。布局/间距/颜色优先 Tailwind 工具类。
- 设计 Token：
  - 颜色优先用 Element Plus 变量：`var(--el-text-color-primary)`、`var(--el-color-primary)`、`var(--el-fill-color-light)`。
  - 主题色 token 在 `src/style/theme.scss`，按 `html[data-theme="light|default|saucePurple|..."]` 切换。
  - 语义色（状态徽章等）统一：蓝 `#386bf5`（agent/主操作）、绿 `#00a870`（在线/成功）、橙 `#f59e0b`（草稿/警告）、红 `#f56c6c`（下线/错误）、紫 `#9b59b6`。
- scoped 样式用 `<style lang="scss" scoped>`，需穿透用 `:deep()`；声明顺序遵循 stylelint-config-recess-order（admin 已配 stylelint）。
- Tailwind important 用 `!` 后缀（如 `class="w-32!"`）。
- 全局样式入口 `src/style/index.scss`（`@use` theme/transition/element-plus/sidebar/dark）。**业务页不写全局样式**。

## 9. ECharts 图表

- Chart 类型在 `src/plugins/echarts.ts` 统一按需注册（`use([...])`），挂到 `app.config.globalProperties.$echarts`。
- 用 `@pureadmin/utils` 的 `useECharts(ref, { theme: isDark.value ? "dark" : "light" })`。
- `setOptions({...} as any)` **统一加 `as any` 断言**（ECharts option 类型复杂且按需注册类型不完整）。或函数返回类型标 `any`：`function lineChartOptions(...): any { return { ... } }`。
- 图表容器用 `class="chart-fill"` 或固定高度，配合 `useECharts` 自动 resize。

## 10. 国际化（i18n）

- 文案文件：`apps/admin/locales/zh-CN.yaml` + `en.yaml`（扁平 YAML，按业务模块分顶层 key）。已有顶层 key：`menus`/`buttons`/`common`/`login`/`definition`/`instance`/`welcome`/`community`/`system`/`operationLog` 等。**新增模块加一个顶层 key**。
- `import.meta.glob` 在 **Vite 启动时**缓存 YAML 内容。修改 `locales/*.yaml`（新增/删除 key）后**必须重启 Vite**（`Ctrl+C` + `pnpm dev`），HMR 不重载 glob 结果。
- `$t`（`src/plugins/i18n.ts`）只是 `(key) => key` 占位，仅供 i18n Ally IDE 插件提示和路由 `meta` 存 key，**没有实际翻译能力**。真实翻译用 vue-i18n 的 `t`。
- 组件内用法：`const { t } = useI18n()` → `t("common.action.edit")`，支持占位 `t("welcome.stat.publishRate", { rate: 50 })`；`te(key)` 判断 key 是否存在。
- `.ts`/`.tsx` 中：`import { i18n } from "@/plugins/i18n"; const t = i18n.global.t as ...`。
- `transformI18n(message)`（`i18n.ts`）已绕过有 bug 的 `flatI18n`/`getObjectKeys` 缓存（该缓存只收集父级 key 不收集完整路径），检测到点号 key 直接调 `i18n.global.t()`。**不要依赖 flatI18n**。
- **新增用户可见文案必须走 i18n，禁止硬编码中文字符串。**

## 11. 用户文案规范

前端改动同样遵循 CLAUDE.md「用户文案不暴露实现细节」节：页面文案/错误提示/API 响应只描述用户可感知的行为，不暴露内部机制（堆栈/SQL/异常类名/is_internal 等内部字段）。错误提示用「保存失败，请重试」而非内部错误码。

**错误处理统一句**（catch 内）：

```ts
} catch (err: any) {
  console.error("xxx failed:", err?.response?.data?.detail || err);
  message(t("xxx.msg.failed"), { type: "error" });
}
```

- **完全隐藏后端 detail**：`err?.response?.data?.detail` 仅写入 `console.error`，**禁止**映射到用户提示、**禁止**把 detail 字符串展示给用户（含 `codeMap[detail] || detail || t(...)` 这类回退到 raw detail 的写法）。
- 用户侧只见固定友好文案（如「保存失败，请重试」）。需要按错误码走不同**业务逻辑**（如重试、跳转）时可在 catch 内判断 detail，但**展示**侧统一走 i18n 文案。

## 12. 提交前检查

前端改动遵循 CLAUDE.md「测试规范」节：
- `pnpm run typecheck` + `pnpm run build` 必须通过（build 会校验 yaml 重复 key 等 typecheck 发现不了的问题）。
- 涉及 DB schema / API 变更需配 migration 并同步本地 + 云 DB。
- 本地 vite 启动后实际操作页面验证，不只靠 typecheck。

## 13. 资料中心同步

涉及用户可见行为时遵循 CLAUDE.md「资料中心同步」节：改页面/菜单/API 必须同步 `apps/docs/content/guide/pages/*.md` + `.vitepress/config.ts` sidebar；UI 大改后必要时重跑 `scripts/capture-screenshots.mjs` 重新截图。

## 14. 工具链待办（本轮不落地，列为后续 issue）

以下为已知缺口，待后续单独推进，不阻塞本规范采纳：

- enduser / landing / docs 补齐 ESLint + Prettier + Editorconfig（参照 admin 的 `eslint.config.js` / `.prettierrc.js` / `.editorconfig`）。
- admin 加 import 排序插件（`eslint-plugin-import` 或 `eslint-plugin-perfectionist`），统一 import 顺序（当前无强制）。
- 对齐 commitlint scope 列表与 `docs/contributing.md` 的 type 表（contributing 文档 6 个 type vs `apps/admin/commitlint.config.js` 实际允许 14 个）。
- commitlint 强制从 admin 目录推广到仓库根（当前仅 admin 的 husky `commit-msg` 钩子触发，enduser/landing/docs/根目录提交不受约束）。
- 组件命名统一评估（enduser 当前用 PascalCase 文件名，是否对齐 admin 的 kebab-case 目录 + `Re` 前缀约定）。
- 共享依赖版本漂移治理（marked/codemirror/mermaid 在 admin/enduser/`@ua/chat` 三处分别声明版本，存在漂移风险）。

## 关键文件速查

| 用途 | 路径 |
|------|------|
| 路由入口 | `src/router/index.ts` |
| 菜单生成 | `src/router/utils.ts` |
| 菜单 rank 常量 | `src/router/enums.ts` |
| 路由模块 | `src/router/modules/*.ts` |
| i18n 插件 | `src/plugins/i18n.ts` |
| ECharts 注册 | `src/plugins/echarts.ts` |
| HTTP 封装 | `src/utils/http/index.ts` |
| 弹框 | `src/components/ReDialog/index.ts` + `index.vue` |
| 图标适配器 | `src/components/ReIcon/src/hooks.ts` |
| 全局样式入口 | `src/style/index.scss` |
| Tailwind 入口 | `src/style/tailwind.css` |
| 主题 token | `src/style/theme.scss` |
| 布局内容区 | `src/layout/components/lay-content/index.vue` |
| i18n 文案 | `apps/admin/locales/{zh-CN,en}.yaml` |
| 列表页(卡片) | `src/views/agent-instances/index.vue` |
| 列表页(表格) | `src/views/system/user/index.vue` |
| 表格 hook 范式 | `src/views/system/user/utils/hook.tsx` |
| 表单规则范式 | `src/views/system/user/utils/rule.ts` |
| Dashboard | `src/views/welcome/index.vue` |
| ECharts 页 | `src/views/monitoring/usage/index.vue` |
| API 模块范式 | `src/api/manager/agentDefinitions.ts` |

> 路径均相对 `apps/admin/`（除 `apps/admin/locales/` 本身为绝对前缀）。
