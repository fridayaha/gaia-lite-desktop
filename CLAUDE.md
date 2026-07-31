# UnionAgents (知行) — 项目规范

## 安全约束（重要）

### 敏感信息保护
- **不得**将任何敏感信息（密码、API Key、Token、Secret、连接串、私钥等）提交到代码仓库
- 所有敏感配置必须通过环境变量、k8s Secret、或 `.env.local`（已加入 `.gitignore`）管理
- 示例文件（如 `.env.example`、`secret.yaml.example`）中只能使用**占位符值**（如 `your-api-key-here`、`change-me`）
- 仓库中已有的 `deploy/k8s/infra/secret.yaml` 仅用于本地 k3s 开发环境，不得包含真实生产凭据
- 提交前请检查是否误入了敏感信息，必要时使用 `git diff --cached` 审查

## 开发规范

### 技术栈
- 后端主语言：Python 3.11+（FastAPI + SQLAlchemy async）
- 管理台前端：Vue 3 + Element Plus + TypeScript（基于 vue-pure-admin）
- 用户交互前端：Enduser Portal（Vue 3 + Tailwind，组件复刻 hermes-webui 样式）
- 本地运行时：k3s（colima）
- 数据库：PostgreSQL 16
- 对象存储：MinIO
- 仓库：Monorepo（pnpm + Python）

### 开源软件使用原则
- 不对开源软件做侵入式修改
- 只基于开源软件已有的扩展能力进行扩展
- 做云化改造和安全加固
- 引擎容器化部署，通过其原生 HTTP API 调用

### 引擎运行约束
- Hermes 引擎通过 k3s Pod 以容器化方式运行，不在本地 Docker 中直接启动
- 本地开发使用 `colima + k3s` 提供 K8s 环境，`kubectl apply` 部署引擎资源

### 代码风格
- Python：使用 ruff 格式化，line-length=100
- Python 类型注解：必须
- 异步优先（async/await）

### 用户文案不暴露实现细节

代码设计/开发中的内部约束属于实现细节，**不应**在页面文案、字段标签、帮助文字、错误提示中向用户体现。用户可见的文案只描述用户可感知的行为，不解释内部机制。

- **页面文案**：标签/按钮/帮助文字只写「做什么」，不写「怎么做」
  - 正确：「全部规则」「订阅规则」「保存」
  - 错误：「全部规则（含未来新建）」「勾选后自动清空其他选项」「per-rule_type 幂等」「ondelete=SET NULL 保留历史」
- **错误提示**：用用户可理解的语言（如「保存失败，请重试」），不暴露堆栈/内部错误码/SQL 错误/异常类名
- **API 响应**：只返回用户/前端需要的字段，不泄露内部状态字段（如 is_internal/seed_version/internal_flag 等）
- **设计意图**：未来新建规则自动订阅、勾选互斥清空、级联删除等行为是设计约束，不需要在 UI 上告诉用户「我帮你做了这件事」——用户看到行为本身即可

### 测试规范（提交前必须执行一次，不可跳过）

开发过程中不必每改一次代码就跑全套测试——可以按需局部验证（如只跑受影响的单个测试文件、只跑 ruff/mypy 静态检查）。但**在 commit / push 远端之前**，必须一次性完成以下三项，缺一不可：

1. **补充单元测试用例**
   - 新增/修改的函数、API endpoint、核心分支必须有对应测试
   - 覆盖正常路径 + 边界 + 错误分支（如：无 manifest.json 只有 SKILL.md frontmatter、空入参、异常抛出）
   - DB 写入逻辑（create/update/clone/toggle/install 等）必须有测试，且**不能只断言 `commit.assert_awaited()`**——必须验证实际写入的字段值/SQL 行为（用真 DB 或断言 model 属性 + `flag_modified` 等变更检测，mock commit 测不出 schema 漂移和 ORM JSON 字段丢失）

2. **跑单元测试全绿**
   - `make test`（manager + gateway + hub + hermes）全部通过
   - 前端改动 `pnpm run typecheck` + `pnpm run build`（build 会校验 yaml 重复 key 等 typecheck 发现不了的问题）必须通过
   - 开发中途可只跑改动相关的测试文件快速验证，不必反复跑全量

3. **本地环境接口测试**
   - 后端改动：本地启动 manager（或 k3s 部署）后，用 curl 走真实 DB 对改动接口做冒烟（create/clone/list/install/开关 等），验证返回码 + DB 实际数据落库
   - 涉及 DB schema 变更（加/删列、改约束）：必须配 migration 脚本或显式 ALTER 语句，并在本地 DB + 云 DB 同步执行，不能只改 model 不动 DB
   - 前端改动：本地 vite 启动后实际操作页面验证，不只靠 typecheck

**执行频率原则**：测试在提交前跑一次即可，不需要每改一行就触发。如果你发现自己在开发中途频繁跑全量测试拖慢节奏，停下来——把改动攒到提交前统一验证。

**反模式（明确禁止）**：
- ❌ 只改代码不补测试
- ❌ 单测全用 mock 绕过真实 DB/SQL，`commit` 用 `AsyncMock` 后不断言写入内容
- ❌ model 改了字段/约束但 DB schema 不同步迁移
- ❌ 只跑 typecheck 不跑 build 就提交前端
- ❌ 本地不冒烟直接 push 远端让云上暴露问题

### 资料中心同步（提交前必须检查）

每次代码改动涉及**用户可见行为**时，**在 commit 之前**必须检查 `apps/docs/content/` 下对应的资料是否需要同步更新：

- **页面改动**（UI 布局、交互流程、字段名/标签、操作步骤）→ 同步 `guide/pages/<对应页面>.md` 的步骤说明、字段表、截图引用
- **菜单改动**（新增/删除/重命名侧边栏菜单项）→ 同步 `guide/pages/*.md` 里的"在左侧导航栏单击 X" 引用 + `apps/docs/.vitepress/config.ts` 的 sidebar
- **API 改动**（endpoint 路径、请求/响应字段、错误码、鉴权要求）→ 同步 `guide/api-usage.md` 示例和 `api-reference.md` 的 iframe 嵌入路径
- **架构/部署改动**（引擎类型、K8s 资源结构、部署流程、配置项）→ 同步 `architecture/*.md`、`deployment/*.md`、`features/*.md`
- **截图过期**：页面 UI 大改后，必要时重跑 `scripts/capture-screenshots.mjs` 重新截图，避免图文严重不一致

页面文件命名约定见 `apps/docs/content/guide/pages/`（如 `welcome.md` / `agent-definitions.md` / `monitoring.md` / `system.md` / `community.md`），改哪个页面就去同名 markdown 里校对。

**反模式（明确禁止）**：
- ❌ 改了页面交互流程但不更新 guide 里的步骤描述
- ❌ 改了菜单结构但 guide 里的"单击 X 菜单"还指向旧菜单名
- ❌ 截图和实际页面 UI 已严重不一致却不重新截图
- ❌ 新增/修改 API 但 `api-usage.md` 示例仍用旧路径/旧字段

### 版本号与发布（命名规则，必须沿用）

- **版本格式**：SemVer `MAJOR.MINOR.PATCH`（如 `0.8.0`），可选预发布后缀 `-<prerelease>`（如 `0.8.0-beta.1`）。源仓库 `git tag` 加 `v` 前缀（`v0.8.0`）；`VERSION` 文件与镜像 tag 用**裸版本号**（无 `v`）。
- **版本号源头**：仓库根 `VERSION` 文件存裸版本号（唯一真相）。打包/发版前先确认 `VERSION` 高于云上已发布版本（云上镜像 tag 见容器镜像仓库 / `kubectl get deploy -o jsonpath=...image`）。
- **改版本号**：执行 `scripts/bump-version.sh <版本号>`（如 `scripts/bump-version.sh 0.8.0`），脚本自动同步：`VERSION`、根 `pyproject.toml`、`package.json`（root + `apps/admin` + `apps/enduser`）、FastAPI `version="..."`（`services/manager|gateway|controller/app/main.py`）、`README.md`、`deploy/ci/deployment.yaml` + `deploy/ci/deploy.sh` 示例。**不要**手改单点。
- **镜像 tag**：所有服务（manager/gateway/hub/console-admin/enduser-portal/engine-hermes-v2）统一用同一裸版本号 tag，推容器镜像仓库：`<registry>/unionagents/<service>:<version>`（仓库地址见 `Makefile` 的 `ACR_REGISTRY`，真实地址由 `deploy/ci/.env.local` 的 `REGISTRY` 注入，仓库内只用占位符）。
- **commit / tag**：`chore: bump version to <version>` 提交 → `git tag v<version>` → `git push && git push --tags`。
- **云上一键部署**：`bash deploy/ci/deploy.sh <版本号>`（域名/镜像仓库/DB 凭据在 `deploy/ci/.env.local`，已 gitignore）。脚本用 `sed` 把 `deploy/ci/deployment.yaml` 里的 `${VERSION}`/`${REGISTRY}`/`${UA_MINIO_ENDPOINT}` 等占位符替换为实际值后 `kubectl apply`。
- **注意**：`deploy/ci/deployment.yaml` 若与当前架构（controller 已并入 manager）不一致，发版前必须先更新部署清单，否则会把已废弃的独立 controller 重新部署上去。

## 架构约束

### Gateway 反向依赖
- Gateway **不允许**查询 Controller 或其他服务获取 upstream 地址
- 路由信息通过请求头 `X-Agent-ID` + DNS 命名规范传递
- Controller 按约定规范创建 Pod，Gateway 按规范直接构造 URL：
  `engine-hermes-{agent_id[:8]}.{namespace}.svc.cluster.local:8642`
- 两者通过命名约定解耦，无运行时依赖

### SSE 流式与 nginx
- nginx 代理 SSE 流式请求必须设置 `proxy_buffering off;`
- `proxy_set_header Connection "upgrade"` 会干扰 SSE 流式响应
- 浏览器端用 `ReadableStream` + `TextDecoder` 逐块解析 SSE
- **不要**在 nginx 层缓冲或修改 SSE 响应内容

### iframe 禁止
- 终端门户**不使用 iframe** 嵌入 hermes-webui
- Portal Chat 页面直接渲染 Vue 3 组件，样式拷贝 hermes-webui/style.css
- 不修改 hermes-webui 源代码，仅参考其 HTML 结构和 JS 逻辑翻译为 Vue 3

### Gateway Origin 头过滤
- Gateway 转发请求到引擎前，**必须去掉 `Origin` 和 `Referer` 头**
- Hermes 引擎 API 在收到带 `Origin` 头的请求时会返回 403
- 这是 Chrome/浏览器端 SSE 请求失败 (`Failed to fetch`) 的主要原因

### 数据存档策略
- 存档时机提前到 **SUSPEND**（30min 空闲时）
- 不设定期轮询备份（大规模下不可行，1000 Pod × 5min = 200次/分钟）
- PVC 实时写（引擎自身行为，零开销）
- SUSPEND 存档 → DESTROY 仅清理 K8s 资源（数据已在 MinIO）

### 终端门户前端
- Chat 页面组件：`ChatPage` → `ChatSessionList` + `ChatMessages` + `ChatComposer` + `ChatFileBrowser`
- 聊天核心逻辑：`useChat` composable（会话管理、SSE 消息、Gateway 通信）
- 聊天会话由引擎自身管理（不入 Manager DB）
- 消息/模型 API 通过 Gateway（`X-Agent-ID` 头）转发到引擎

## Admin 前端硬规则

> 完整规范（目录约定/路由菜单/表单规则/API 类型/设计 Token/工具链待办等）见 [docs/frontend-guidelines.md](docs/frontend-guidelines.md)。下列为最常被违反、必须遵守的 do/don't。

- **图标**：主用 `import X from "~icons/ri/xxx"` 编译期导入；**禁止**把图标字符串（`"ri:chat-1-line"`）传给 `IconifyIconOffline`（只接受导入的组件对象）。`el-button :icon` 用 `useRenderIcon(SomeIcon)`。菜单图标用字符串 `"ri:stack-line"`。
- **页面布局**：最外层 `<div class="main">`。顶部工具条 `w-full flex flex-wrap items-center justify-between mb-4 gap-3`，左创建/Action 按钮、右筛选+搜索。搜索框 `el-input` + `clearable` + `style="width: 260px"`，搜索图标放 `#suffix` slot 用 `v-show="searchText.length === 0"` 控制显隐。筛选下拉在前、文本搜索在后。
- **Dashboard**：`<div class="main"><div class="welcome">` 双层容器，`.welcome` 设 `max-width: 1400px; margin: 0 auto;`，左右分栏 `md:17` + `md:7`，图表卡用 `.chart-card` + `.chart-fill`。
- **弹框**：列表页增删改用命令式 `addDialog`（`@/components/ReDialog`），`beforeSure` 里 `await ruleFormRef.value.validate()` → 调 API → `done()`；`closeOnClickModal: false`、`draggable: true`。复杂多步表单用独立路由页 + `el-steps`。
- **表单**：规则抽 `utils/rule.ts`（`reactive<FormRules>` 或工厂）；`el-form` 统一 `label-position="top"` + `:rules` + `ref`，提交前必须 `validate()`；validator 文案走 i18n。
- **API**：统一用 `src/utils/http` 的 `http` 单例（自动 token + refresh），**禁止**裸 axios/fetch。命名 `getXxxApi`/`createXxxApi`/`updateXxxApi`/`deleteXxxApi`，URL 前缀 `/api/manager/` 或 `/api/controller/`。导出 `type XxxResponse`，列表结构按是否分页区分：分页 `{ items, total, page, page_size }`、非分页全量 `{ items, total }`，**禁止**列表返回裸数组。调用处泛型 `http.request<XxxResponse>(...)`。
- **错误处理**：catch 内 `console.error(prefix, err?.response?.data?.detail || err)` + `message(t("...failed"), {type:"error"})`。**完全隐藏后端 detail**——detail 仅入 console，禁止映射或展示给用户（含 `codeMap[detail] || detail || t(...)` 这类回退 raw detail 的写法）。用户侧只见固定友好文案。
- **样式**：优先级 Tailwind v4 > scoped SCSS > 全局 SCSS。颜色优先 `var(--el-*)`，主题 token 在 `src/style/theme.scss`；语义色统一 蓝`#386bf5`/绿`#00a870`/橙`#f59e0b`/红`#f56c6c`/紫`#9b59b6`。scoped 用 `<style lang="scss" scoped>`，穿透用 `:deep()`，声明顺序遵循 stylelint-config-recess-order。业务页不写全局样式。
- **ECharts**：Chart 类型在 `src/plugins/echarts.ts` 统一按需注册；`setOptions({...} as any)` 统一加 `as any` 断言。
- **路由/菜单**：模块文件放 `src/router/modules/*.ts`（自动导入），`satisfies RouteConfigsTable`；菜单由路由 `meta` 自动生成（`src/router/utils.ts`），**不写静态菜单**。`meta.rank`（`src/router/enums.ts`）定序、`meta.roles` 定权限、`meta.showLink: false` 不进菜单、`meta.title` 存 `t()` key、`meta.icon` 字符串 `"ri:xxx"`。
- **i18n**：新增用户可见文案**必须**走 i18n，**禁止**硬编码中文。文案在 `apps/admin/locales/{zh-CN,en}.yaml` 按业务模块分顶层 key，新模块加顶层 key。`$t`（`src/plugins/i18n.ts`）只是 `(key) => key` 占位、**无翻译能力**，真实翻译用 vue-i18n 的 `t`（组件内 `const { t } = useI18n()`）。改 yaml 增删 key 后**必须重启 Vite**（`import.meta.glob` 启动时缓存，HMR 不重载）。`transformI18n` 已绕过有 bug 的 `flatI18n` 缓存，不要依赖 flatI18n。

相关记忆：[[i18n-lessons]]
