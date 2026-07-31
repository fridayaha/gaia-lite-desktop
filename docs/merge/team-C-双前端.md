# C 团队任务书 — 双前端（Admin + Enduser）

> 依赖 A（manager API）+ B（gateway/hub API）。A/B 落地前按 `01-接口契约.md` mock 并行。
> 工作目录：Repo1 `develop` 分支。基座来源：Repo2 `apps/`。

## 职责边界
- Admin Console（apps/admin）：V3 三层页面 + LiteLLM UI + dashboard + 系统管理 + hub 管理页
- Enduser Portal（apps/enduser）：Repo2 基座 + 嫁接 Repo1 端体验细化

不做：后端（A/B）。所有 API 按契约消费。

## 任务清单

### Admin（apps/admin，Vue3 + Element Plus + TS，端口 8848）

#### C1. 基座 + V3 三层页面（W2）
- Repo2 admin 拷入；页面：`/agent-definitions`、`/agent-definitions/detail/:id`、`/agent-instances`、`/agent-instances/detail/:id`、`/resource-pools`（F-ADM-001~005）。
- 实例详情 5 Tab（概览/实例/监控/记忆/技能）；双层状态（Manager 业务态 + Controller 部署态）；15s 轮询部署状态；Pod 重建跟踪。
- **验收**：建定义→发布→建池→建实例→上线→deploy SSE 进度可见。

#### C2. LiteLLM 管理 UI + dashboard（W2~3）
- `/litellm/models`、`/litellm/keys`、`/litellm/spend`（F-ADM-010~012）：多供应商、Key 智能关联、预算/速率、用量 ECharts（折线/饼/柱/柱）。
- `/welcome` 多角色 dashboard（F-ADM-020）：`<div class="main"><div class="welcome">` 双层 max-width 1400px；管理员 md:17/md:7 分栏；`.chart-card`+`.chart-fill`；ECharts `as any`。
- **验收**：三角色（平台管理员/组管理员/普通用户）视角正确；图表自适应。

#### C3. 系统管理 + i18n（W3）
- `/system/user`、`/system/role`、`/system/user-group`（F-ADM-030~032）：权限树 + 搜索 + 联动；弹窗式成员编辑。
- i18n（F-ADM-040）：**改 `locales/*.yaml` 后必须重启 Vite**（`import.meta.glob` 启动缓存）；`$t` 仅占位符，真实翻译在 `transformI18n`；`flatI18n` 缓存有 bug 已绕过。
- **验收**：中英双语切换生效；权限树配置保存正确。

#### C4. 知识库页面 — ❌ 本次不做（已决策）
- Repo1/Repo2 知识库均无后端。用户决策：**本次不做，合并后保留 Repo2 占位页**（`views/knowledge/index.vue` placeholder）。C 无需处理。

#### C5. hub 管理页（W4）
- Repo1 `apps/admin/src/views/hub/{index,detail}.vue` 迁入；对接 B 的 `/api/hub`。
- **验收**：hub_item 列表/详情/审批/扫描结果展示。

#### C6. 样式约束（贯穿）
- CLAUDE.md：`<div class="main">` 最外层；按钮左筛选右；搜索框 `width:260px` + suffix 图标 `v-show`；筛选下拉在前搜索在后。
- 图标：**禁止**字符串传 `IconifyIconOffline`，必须 `import Chat1Line from "~icons/ri/chat-1-line"`；JSX 用 `{...({width:"18"} as any)}`。
- Dashboard 图表 `as any`；Chart 类型在 `src/plugins/echarts.ts` 统一注册。

### Enduser（apps/enduser，Vue3 + Tailwind + Pinia，端口 3000）

#### C7. 基座（W2~3）
- Repo2 enduser 拷入；rail 导航 9 面板；JWT 认证 + 路由守卫（F-END-001~005）；可访问实例列表；自动部署 + SSE 进度；503 健康监控。
- 会话管理（F-END-010~012）：多会话、智能标题、LocalStorage 持久化、JSON 导入导出。
- 消息（F-END-020~022）：ReadableStream+TextDecoder 解析 SSE、AbortController、Markdown 渲染、工具调用追踪。
- 工作区（F-END-030~032）：文件浏览器、多工作区、附件上传。
- 设置（F-END-070~074）：主题/字体、面板记忆、导出导入、滚动优化、移动端。
- 网络（F-END-060~062）：`api/client.ts` 统一 HTTP + 401 跳转；gateway 代理 base `/api/gateway` + `X-Agent-ID`/`X-Engine-Type`/`X-Session-ID`。

#### C8. 嫁接 Repo1 端体验细化（W4）
- **ApprovalCard** + 审批工作流（Repo1 `apps/enduser/src/components/chat/` 下，需在 Repo1 定位 ApprovalCard 组件）。
- **resumePendingHermesRuns**：`apps/enduser/src/composables/useChat.ts` 的 `PENDING_RUNS_KEY` localStorage(5min TTL) + 中断 SSE 自动恢复。
- **连接恢复横幅**：`ChatPage.vue` 的 `navigator.onLine` 离线检测 + 不完整会话重连选择 + 引擎健康监控。
- **工具追踪打磨**：`ToolCard.vue` 实时进度 + 耗时 + 可折叠 + 状态图标。
- **部署进度**：`DeployProgress.vue` 多步骤 + 百分比 + 重试。
- **输入框**：`ChatComposer.vue` 自适应高度 + 附件预览 + 快捷键 + 模型下拉。
- 迁到 Repo2 enduser 组件树，注意样式/props 对齐。
- **验收**：审批操作触发 ApprovalCard；中断后刷新自动恢复 pending run；离线/重连横幅正确；工具卡片实时更新。

## 交付物
- Admin：V3 三层 + LiteLLM + dashboard + 系统管理 + hub 页，样式合规
- Enduser：Repo2 基座 + Repo1 审批/恢复/工具追踪/部署/输入打磨

## 关键依赖文件
- Repo2 admin（基座）：`apps/admin/src/views/{agent-definitions,agent-instances,resource-pools,litellm,knowledge,system,welcome}/`
- Repo1 hub 页（迁 C5）：`apps/admin/src/views/hub/{index,detail}.vue`
- Repo2 enduser（基座）：`apps/enduser/src/`
- Repo1 enduser 细化（迁 C8）：`apps/enduser/src/composables/useChat.ts`、`apps/enduser/src/components/chat/{ChatPage,ToolCard,ChatComposer,ChatMessages,ChatSessionList,ChatFileBrowser}.vue`、`apps/enduser/src/components/DeployProgress.vue`
- 契约：`docs/merge/01-接口契约.md` §2(manager)、§4(gateway)、§5(hub)
- 样式约束：`CLAUDE.md`「Admin 前端样式约束」「国际化注意事项」
