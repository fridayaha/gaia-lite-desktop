# 待修复：本体「弃用 / 删除」UX 缺陷 — 静默失败 + 状态不可见

**记录时间**: 2026-06-26
**影响模块**: 前端 OntologyWorkspace / OntologySidebar，后端 v5.2 lifecycle
**状态**: ✅ 已修复 (2026-06-28)

---

## 现象

本体详情页的「弃用」「删除本体」两个高危操作存在两类问题：

1. **「删除本体」点了无反应** — 点确认后对话框关闭，既不报错也不删除，列表无变化。
2. **「弃用」之后本体从侧栏消失** — 用户找不到本体，也不知道它处于什么状态。

## 根因

### 缺陷 1：确认对话框吞掉 async 异常

`src/web-ui/src/pages/OntologyWorkspace.tsx:855-857`

```tsx
onConfirm={() => {
  confirmDelete.action();   // async 函数，未 await、未 try/catch
  setConfirmDelete(null);   // 立刻关闭对话框
}}
```

`confirmDelete.action` 是 `async`（内部 `await getOntologyImpact` + `await deleteOntology`），
但 `onConfirm` 既没 `await` 也没 `.catch()`。后果：

- 对话框点确认后立刻关闭（看起来"无反应"）
- action 内 `throw new Error('请先弃用（Deprecate）本休')` 成为未捕获的 Promise rejection，被静默吞掉
- 即使本体已 DEPRECATED、删除成功，toast 的时序也可能错乱

当本体仍为 ACTIVE 时，`getOntologyImpact` 返回 `can_delete=False`（见
`src/ontology/services/ontology_service.py:278`），前端抛错中断 ——
这正是用户最常见的路径（没先弃用就直接点删除），所以表现为"无反应"。

### 缺陷 2：typo "本休" 应为 "本体"

`src/web-ui/src/pages/OntologyWorkspace.tsx:433`

```tsx
throw new Error(impact.blocked_reason || '请先弃用（Deprecate）本休');
```

即便异常被捕获显示出来，提示文案也是错别字。

### 缺陷 3：DEPRECATED 本体被默认过滤，且无查看入口

`src/ontology/layers/metadata/postgres_meta_store.py:172,199`

```python
OntologyModel.deleted_at.is_(None),
OntologyModel.status != "DEPRECATED",
```

`list_ontologies` / `list_ontologies_with_counts` 默认同时过滤 soft-deleted 和 DEPRECATED。
前端 `OntologySidebar` 默认不带 `?include_deleted=true`，所以**弃用后本体从侧栏消失**。

而设计文档（v5.2 lifecycle）里规划的「回收站 / 非活跃本体」标签页被标记为
decision 11 延期（见 `OntologyWorkspace.tsx:487-489` 注释），尚未实现。
于是用户弃用后既看不到本体，也无任何入口查看其状态。

违反 CLAUDE.md 第四原则"空状态/各种状态都有设计过的界面，不是白屏"。

> 注意：`OntologySidebar.tsx:41-57` 其实已经写好了 DEPRECATED 的灰显 + ⚠ 渲染分支，
> 只是被后端默认过滤挡住了，前端根本拿不到这些行。

## 修复方案

采用业界"状态分层可见性"范式（GitHub Open/Closed tab、Notion archived 开关、
Palantir Foundry Non-active 视图均同此），分短期最小修复 + 中期回收站两步。

### 短期最小修复（本次实施）

1. **修 `onConfirm` 错误处理** — `OntologyWorkspace.tsx:855`
   - `await` + `try/catch`
   - 失败时 `setToast(..., 'error')` 并**保持对话框打开**（让用户看到原因，而非关掉装无事）
   - 成功才 `setConfirmDelete(null)`

2. **修 typo** — `OntologyWorkspace.tsx:433` "本休" → "本体"

3. **侧栏显示 DEPRECATED 本体**（灰显，已有渲染分支直接生效）
   - 方案 A（推荐，最小改动）：侧栏默认列出 DEPRECATED 但仍过滤 soft-deleted。
     即侧栏调 `listOntologies()` 时让后端对"侧栏默认视图"放宽到 `status != ACTIVE` 改为只过滤 `deleted_at`。
   - 方案 B（前端兜底）：前端加 `?include_deleted=true` 拉 DEPRECATED，但前端再过滤掉 soft-deleted。
     风险：语义混淆，include_deleted 名不副实。
   - 倾向 A，需在后端 `list_ontologies_with_counts` 区分"非活跃可见"与"软删除可见"两档语义。

4. **DEPRECATED 横幅** — `OntologyWorkspace.tsx` 详情页头部
   - 当 `currentOntology.status === 'DEPRECATED'` 时显示横幅
     `此本体已弃用，可恢复或彻底删除`
   - 与现有「弃用 / 删除本体」按钮联动：DEPRECATED 时隐藏「弃用」按钮，只显示「删除本体」+「恢复」

### 中期（decision 11，本次不做）

- 回收站标签页：复用列表组件，`?include_deleted=true` 拉软删除项
- 每项带「恢复 / 彻底删除 / 剩余 N 天」
- 呼应 CLAUDE.md「组件复用最大化」—— 回收站即列表容器换数据源

## 验证

- [x] ACTIVE 本体直接点「删除本体」→ 对话框不关，toast 显示"请先弃用（Deprecate）本体"
- [x] 点「弃用」→ 侧栏仍可见（灰显 + ⚠），详情页顶部出现 DEPRECATED 横幅
- [x] DEPRECATED 本体点「删除本体」→ 确认后列表移除，toast 显示"已删除，7天内可恢复"
- [x] DEPRECATED 状态下「弃用」按钮隐藏，只显示「删除本体」+「恢复」
- [x] 网络失败时对话框不关，toast 显示错误

## 实施记录 (2026-06-28)

已按短期最小修复方案实施：

1. **`onConfirm` 错误处理** (`OntologyWorkspace.tsx`): `async` + `try/catch`,
   失败 toast 错误并保持对话框打开, 成功才关闭。
2. **typo** "本休" → "本体"。
3. **侧栏显示 DEPRECATED** (方案 A): 后端 `list_ontologies` /
   `list_ontologies_with_counts` 新增 `include_deprecated` 参数 (独立于
   `include_non_active`), 形成三档可见性:
   - 默认: 仅 ACTIVE
   - `include_deprecated=true`: 含 DEPRECATED, 不含 soft-deleted (侧栏默认)
   - `include_non_active=true` (`?include_deleted=true`): 全含 (回收站)
   前端 `listOntologies(false, true)` 让侧栏拉取 DEPRECATED 本体, 已有的
   灰显渲染分支 (`opacity-60` + ⚠) 直接生效。
4. **DEPRECATED 横幅 + 恢复按钮**: 详情页头部 DEPRECATED 时显示横幅 +
   「恢复」按钮, 隐藏「弃用」按钮。`handleRestoreOntology` (原为 decision 11
   预留) 接入详情页恢复入口。

端到端验证 (浏览器): ACTIVE 删除被阻塞(对话框保持+toast)、弃用后侧栏灰显可见、
DEPRECATED 详情页横幅+恢复按钮、三档可见性 API 验证通过。

## 参考

- 业界范式详见对话讨论：GitHub Open/Closed、Linear Active/Backlog/Archived、
  Notion archived 开关、Palantir Foundry Non-active 视图、dbt Deprecated 横幅
- 设计依据：`src/ontology/core/models/ontology.py:36` v5.2 lifecycle 注释
  (`ACTIVE → DEPRECATED → soft-deleted`)
