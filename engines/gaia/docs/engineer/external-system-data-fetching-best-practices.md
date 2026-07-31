# 外部系统数据获取与状态同步最佳实践

> 本文档沉淀"前端列表/详情页加载外部系统（SeaTunnel / Kestra / Doris / Gravitino 等）状态"时的工程原则与避坑指南。
> 起因：数据源详情页 `xiaoling`（44 个同步任务）打开时触发 54 次 SeaTunnel refresh 请求，单次 1.6~11s，浏览器连接池被打满导致页面卡死。
> 调研覆盖 React 多 tab 懒加载、并发限流、批量端点、StrictMode 双调用、实时状态刷新策略、SeaTunnel 批量能力。
>
> 最后更新：2026-07

---

## 一、核心原则

### 原则 1：详情页 mount 只加载主实体，tab 相关数据懒加载

**问题**：详情页打开时一次性预加载所有 tab 的数据（explore + sync + access + settings），即使用户只看默认 tab。

**原则**：mount 只 fetch 主实体（如数据源本身），各 tab 的相关数据在**该 tab 首次激活时**才 fetch。

**依据**：
- wayvo-ai multi-tab-detail 模式：tab 状态走 URL，每个 tab 组件用自己的 store query 自己的数据，layout 只负责传 store + row，**不在 page 层预加载所有 tab 数据**
- MUI #5636 "Lazy Load Tab Children"：只渲染访问过的 tab，未访问的不挂载（当每个 tab 组件自己 fetch 数据时尤其重要，否则首屏触发所有 tab 的请求）
- shadcn tabs 9 patterns：`React.lazy + Suspense` 把 tab panel 拆成独立 chunk，切到才加载

**实现要点**：
- 用 `visited` Set 标记已访问过的 tab，**已访问的保留挂载**（`hidden` 隐藏而非卸载），避免来回切时重复 fetch
- tab state 走 URL（`?tab=sync`），刷新可恢复，且支持深链

### 原则 2：终态即真相，只 reconcile 非终态

**问题**：打开列表就对所有项调外部系统刷新状态，包括已经是终态（FINISHED/FAILED/STOPPED）的。

**原则**：终态值（FINISHED/STOPPED/CANCELED/FAILED）一旦写入 PG 就是真相，**只有非终态（RUNNING/PENDING）才需要 reconcile 外部系统**。

**依据**：
- trigger.dev runs live updating：visible runs 在执行中 in-place 更新，新 run 出现时顶部弹「New runs created」刷新按钮（非自动全量刷新）
- paperclip LiveRunWidget：`refetchInterval: 3000` 但 `enabled: !!issueId`，且只 filter `status === queued/running` 的项
- Django SaaS 长任务一文：**Polling First, SSE for Premium**——默认轮询，只有需要 sub-second 实时才上 SSE/WebSocket

**判定「哪些状态需要 reconcile」的方法**：穷举所有写入该 status 字段的入口，确认有没有后台重试/恢复机制会让终态"复活"。如果终态写入后没有别的入口能改它，就只需要 reconcile 非终态。

### 原则 3：并发必须限流，`Promise.all(array.map(fn))` 是反模式

**问题**：对 N 个项并行调外部系统，`Promise.all(tasks.map(t => refresh(t)))` 一次性发出 N 个请求。

**原则**：浏览器对同域 HTTP/1.1 并发连接约 6 个，N 个请求会排队。必须用 `p-limit` 限制并发。

**依据**：
- p-limit recipes：支持动态并发（遇 429 降速）、shutdown 时等待在途 promise
- LinkedIn "API storms" 帖核心教训：**Parallel ≠ unlimited**，`Promise.all` 在循环里只是把并发乘上去，真正需要的是 controlled concurrency + progressive UI + error isolation
- TanStack Query infinite-queries 文档：同一 query 同时只能有一个在途 fetch，否则数据覆盖

**实现**：
```js
import pLimit from 'p-limit';
const limit = pLimit(4); // 浏览器同域建议 ≤6
const refreshed = await Promise.all(
  tasks.map(t => limit(() => refreshSyncTask(t.api_name).catch(() => t)))
);
```

### 原则 4：后端优先用外部系统的批量能力，消除 N+1

**问题**：前端对 N 个项各调一次后端 refresh，后端每次又各调一次外部系统。

**原则**：外部系统通常提供"全量列表"端点，后端应**一次拉取、本地匹配**，而非逐项查询。

**依据**：
- Request Bundle pattern (api-patterns.org)：把多个独立请求装进一个容器，服务端并行处理 + 单独返回每个结果
- AuditBuffet AB-000373：无 batch 端点 → 客户端被迫 N+1 循环，每条请求都加延迟和负载
- Codelit batch API：批量端点要支持**部分成功**（per-item status/error）+ 幂等性

**SeaTunnel 具体能力**：
- `GET /running-jobs` → 返回所有运行 job 的列表（jobId/jobName/jobStatus）
- `GET /finished-jobs` → 返回所有终态 job 的列表（含 errorMsg）
- **两者都是全量列表端点**，不是单 job 查询。查 N 个 job 的状态只需调 1~2 次，本地按 jobName 匹配，而非 N×2 次

### 原则 5：只取需要的字段，不要 over-fetch

**问题**：调用外部系统的详情端点（`/job-info/:jobId` 返回 jobDag/metrics/vertexInfoMap 等大对象），但实际只用 status + finishTime。

**原则**：列表端点返回的简要字段（jobId/jobName/jobStatus）通常已覆盖需求，不要为了"以防万一"拉完整详情。

**判定方法**：列出实际消费的字段，对照外部系统各端点的返回结构，选最小够用的那个。

### 原则 6：外部系统调用必须有超时，且超时粒度要考虑并发累积

**问题**：单个请求设了 30s 超时看似合理，但 N 个并发请求排队后，累计等待时间可达 N×单次延迟。

**原则**：
- 每个外部系统调用必须设超时（httpx `timeout=` / fetch `AbortSignal.timeout()`）
- 超时阈值要考虑**并发场景下的累积延迟**，不只是单次延迟
- 超时治不了并发风暴——并发风暴要靠限流（原则 3）和批量（原则 4）治

### 原则 7：reconcile 应后台异步，前端立即返回缓存值

**问题**：前端发 refresh 请求后一直 await 到外部系统返回，外部系统慢则前端卡住。

**原则**：后端立即返回 PG 当前值，reconcile 在后台跑（lifespan 后台任务或 outbox），前端轮询拿最新 PG 值。

**依据**：
- Django SaaS 长任务一文：**Polling First**——前端短间隔轮询 PG 值，比 SSE/WebSocket 简单、健壮、抗 deploy
- Kenodo "skip websockets"：大多数 async job UI 用静态 JSON + 轮询就够，WebSocket 是 overkill
- stale-while-revalidate 模式：先返回 stale 缓存立即可见，后台 revalidate 后更新

### 原则 8：React StrictMode dev 双调用要去重

**问题**：React 18 StrictMode 下 `useEffect` 在 dev 会双 mount → effect 跑两次 → list 请求被发两次 → N 个 refresh 整个又被触发一遍。

**原则**：
- TanStack Query / SWR 内置请求去重（同一 queryKey 在途自动 dedupe），优先用这类库
- 手写 effect 用 `AbortController` + `signal`，cleanup 时 abort 第一个请求
- 或用 `useRef` 标记 in-flight 防重入

**注意**：StrictMode 双调用只在 dev 出现，prod 不会。但并发风暴在 prod 一样会卡（N 个并发照样打满连接），所以这只是次要诱因，主因仍是原则 1~4。

---

## 二、反模式速查表

| # | 反模式 | 后果 | 正确做法 |
|---|--------|------|---------|
| 1 | 详情页 mount 预加载所有 tab 数据 | 首屏慢，未访问 tab 的请求浪费 | tab 激活时才 fetch |
| 2 | 对所有列表项全量 reconcile | N 次外部调用，连接池打满 | 只 reconcile 非终态项 |
| 3 | `Promise.all(array.map(fn))` 无限流 | 并发风暴，浏览器卡死 | `p-limit` 限流 |
| 4 | 逐项调外部系统查状态（N+1） | N 次外部调用，延迟叠加 | 后端一次拉全量列表本地匹配 |
| 5 | 调详情端点取全量字段 | over-fetch，响应体大 | 列表端点的简要字段够用 |
| 6 | 外部系统调用无超时 | 单个慢请求拖垮整链 | 必设超时，且考虑并发累积 |
| 7 | 前端 await reconcile 结果 | 外部系统慢则前端卡 | 后端后台 reconcile，前端拿缓存值 |
| 8 | 手写 effect 无去重 | StrictMode dev 双调用 | AbortController / 库去重 |

---

## 三、决策树：列表项状态如何刷新

```
列表加载
  ├─ 立即渲染 PG 存的 status（终态即真相）
  ├─ 筛出 status === RUNNING/PENDING 的项
  │    ├─ 有？→ 后端批量 reconcile（一次拉外部系统全量列表，本地匹配）
  │    │        └─ 失败？→ 保留 PG 值（best-effort，不阻塞渲染）
  │    └─ 无？→ 结束，零外部调用
  └─ 用户切到详情/单条 → 按需 reconcile 该条（如有必要）
```

---

## 四、SeaTunnel 状态查询的正确姿势

### 现状（错误）

`get_job_status(name)` 被设计成"单 job 查询"，但底层 SeaTunnel 是"全量列表"端点。查 N 个 job 状态时：

```
N 个 task × (查 running-jobs 全量列表 + 查 finished-jobs 全量列表)
= 2N 次 SeaTunnel 调用，每次都拉全量 job 详情
```

44 个 task → 88 次 SeaTunnel 调用。

### 正确姿势

新增批量方法，一次拉取本地匹配：

```python
async def get_jobs_status_batch(self, names: set[str]) -> dict[str, PipelineStatus]:
    """一次拉取 SeaTunnel running + finished 列表，本地按 jobName 匹配多个 task。"""
    running = await self._fetch_running_jobs()   # 1 次
    finished = await self._fetch_finished_jobs() # 1 次
    # 本地构建 jobName → status 索引，O(N) 匹配
    ...
```

2 次调用替代 2N 次。

---

## 五、SyncTask 状态机与 reconcile 边界

### 写入 `sync_tasks.status` 的入口（穷举）

| 入口 | 写入的 status | 是否需要 reconcile |
|------|--------------|-------------------|
| `create_sync_task`（ORM default） | `DRAFT` | 否（前置态，PG 即真相） |
| `start_sync` / `start_cdc_sync` / `start_timeseries_sync` | `RUNNING`（成功）/ `FAILED`（提交失败） | **是**（RUNNING 是非终态） |
| `stop_sync` | `STOPPED` | 否（终态，PG 即真相） |
| `refresh_sync_status` | 映射 SeaTunnel 状态 | —（这就是 reconcile 本身） |
| 后台任务 | ❌ 无任何后台任务碰 sync_tasks.status | — |

### 结论

- **唯一需要 reconcile 的是 `status === RUNNING` 的项**——这是唯一可能和 SeaTunnel 真实状态不同步的非终态值
- 终态值（FINISHED/STOPPED/CANCELED/FAILED）一旦写入就是真相，没有任何入口能让它"复活"回 RUNNING
- **"只刷新 RUNNING"既治标又治本**：减少 refresh 数量（44 → 通常 0~3 个），且 stale RUNNING 正是需要被 reconcile 的，RUNNING-only 精准覆盖
- 历史隐患（见 `bugfix/sync-task-status-stuck-running.md`）：过去 `get_job_status` 用错端点导致 stale RUNNING 永不更新。已修复，但若 DB 里仍有历史 stale RUNNING，RUNNING-only 刷新正好清理它们

### 边界说明

「只刷新 RUNNING」不保护"用户手动改 DB 把终态改成 RUNNING"这种非法操作——这超出系统保护范围，是合理边界。

---

## 六、待优化项清单

> 以下针对当前 `xiaoling` refresh 风暴的具体优化，按优先级排序。
> **状态：✅ 全部已实施（P0~P4）。P5 作为远期演进方向保留。**

### P0 — 前端：tab 懒加载（治本，立竿见影）✅

**位置**：`src/web-ui/src/pages/DataSourceDetail.tsx` 的 `loadAll` effect

**现状**：`loadAll` 在 mount 时同时调 `fetchSyncTasksFor` + `fetchExplore`，不管用户看哪个 tab。

**改动**：
- `fetchSyncTasksFor` 从 `loadAll` 移出
- 改到 `tab === 'sync'` 首次激活时触发（用 `visited` Set 标记已访问，保留挂载避免重复 fetch）
- 默认 `explore` tab 打开时零 refresh

**效果**：打开详情页（默认 explore tab）零 refresh 请求；切到 sync tab 才加载。

### P1 — 前端：立即渲染 PG 值 + 只 refresh RUNNING（治本）✅

**位置**：`src/web-ui/src/hooks/useDataSource.ts` 的 `fetchSyncTasksFor`

**现状**：`Promise.all(tasks.map(t => refreshSyncTask(t.api_name)))` 对所有 task 并行 refresh。

**改动**：
- `listSyncTasks` 返回后**立即 `setSyncTasks(tasks)`** 渲染 PG 存的 status
- 主路径改用 P4 的批量端点（一次 reconcile 全部）；批量端点不可用时退回单条路径
- 退回路径只对 `t.status === 'RUNNING'` 的 task 调 `refreshSyncTask`（通常 0~3 个）
- 终态项零 refresh（PG 值即真相，见上文 reconcile 边界分析）

**效果**：列表瞬间渲染；refresh 数从 N 降到批量 1 次（或退回路径 0~3 次）。

### P2 — 前端：并发限流（兜底）✅

**位置**：`src/web-ui/src/hooks/useDataSource.ts`

**现状**：`Promise.all` 无限流。

**改动**：引入 `p-limit`（v7，纯 ESM，~1KB），对退回路径的 RUNNING 项 refresh 限并发 4。

**效果**：即便有多个 RUNNING 也不会打爆连接池。

### P3 — 前端：StrictMode 双调用去重（次要）✅

**位置**：`src/web-ui/src/pages/DataSourceDetail.tsx` 的 effect + `useDataSource.ts`

**改动**：
- `loadAll` effect 加 `AbortController` + `signal`，cleanup 时 abort（应对 StrictMode dev 双 mount）
- `fetchSyncTasksFor` 用 `inflightRef` 防重入（在途请求复用而非重发）

**效果**：消除 dev 下重复请求。**注意**：prod 无此问题，但 dev 体验改善值得做。

### P4 — 后端：批量 reconcile 端点（根治 N+1）✅

**位置**：`src/ontology/layers/pipeline/sea_tunnel_engine.py` + `services/datasource_service.py` + `routes/datasource.py`

**改动**：
- `sea_tunnel_engine.py` 重构：抽出 `_fetch_running_jobs` / `_fetch_finished_jobs` / `_status_from_job` 可复用方法；新增 `get_jobs_status_batch(names)` — 一次拉 running+finished 列表本地匹配，2 次调用替代 2N 次；SeaTunnel 不可达时全部降级为 UNKNOWN（不抛异常）
- `datasource_service.py` 新增 `refresh_all_sync_status(ds_api_name)` — 批量 reconcile 该 ds 下所有 task，UNKNOWN 跳过、终态写 last_run_at、per-task 失败不中断批次
- `routes/datasource.py` 新增 `POST /datasources/{ds}/sync-tasks/refresh-batch` 端点
- 前端 `fetchSyncTasksFor` 主路径改调批量端点

**效果**：88 次 SeaTunnel 调用 → 2 次。已 live 冒烟验证（SeaTunnel 不可达时正确降级，不误更新 PG）。

### P5 — 后端：reconcile 异步化（架构演进，远期）⏳

**位置**：`src/ontology/main.py` lifespan + `services/datasource_service.py`

**现状**：`refresh_all_sync_status` 仍同步 await SeaTunnel，前端等结果。

**改动**（待实施）：
- 新增后台 reconciler（仿 `PipelineBuildReconciler`）：定期扫描 `status='RUNNING'` 的 sync_task，批量 reconcile SeaTunnel，更新 PG
- refresh 端点改为立即返回 PG 当前值，触发后台 reconcile（非阻塞）
- 前端轮询 PG 值（stale-while-revalidate）

**效果**：前端零等待外部系统。作为 P0~P4 落地后的演进方向。

---

## 七、参考资料

- [wayvo-ai multi-tab-detail pattern](https://www.wayvo.dev/docs/patterns/multi-tab-detail)
- [MUI #5636 Lazy Load Tab Children](https://github.com/mui-org/material-ui/issues/5636)
- [shadcn tabs 9 patterns](https://dev.to/vaibhavg/shadcn-tabs-react-guide-real-patterns-use-cases-and-performance-tips-15kp)
- [p-limit recipes](https://github.com/sindresorhus/p-limit/blob/HEAD/recipes.md)
- [LinkedIn: API storms in frontend](https://www.linkedin.com/posts/ajit-pradhan_frontendengineering-reactquery-concurrency-activity-7406706332084469760-w1Gk)
- [Request Bundle pattern (api-patterns.org)](https://api-patterns.org/patterns/quality/dataTransferParsimony/RequestBundle)
- [AuditBuffet AB-000373 batch endpoints](https://auditbuffet.com/patterns/ab-000373)
- [SeaTunnel RESTful API V2](https://seatunnel.apache.org/docs/engines/zeta/rest-api-v2)
- [Django SaaS: Polling First, SSE for Premium](https://aisaastemplate.com/blog/long-running-jobs-django-saas-polling-websockets-sse/)
- [Kenodo: skip websockets](https://kenodo.com/blog/skip-websockets-static-file-polling)
- [TanStack Query infinite-queries](https://tanstack.com/query/latest/docs/framework/react/guides/infinite-queries.md)
- [React #24455 StrictMode double effect](https://github.com/facebook/react/issues/24455)
- 项目内：[`bugfix/sync-task-status-stuck-running.md`](../bugfix/sync-task-status-stuck-running.md)（历史 stale RUNNING 隐患）
