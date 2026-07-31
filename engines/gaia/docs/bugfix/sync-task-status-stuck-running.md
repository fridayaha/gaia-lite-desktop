# 已修复：SeaTunnel finished-jobs NPE 导致 sync task 状态永远 RUNNING

**修复时间**: 2026-06-25  
**影响版本**: SeaTunnel 2.3.13  
**状态**: ✅ 已修复

---

## 现象

前端 synctask 列表永远显示"运行中"，即使 SeaTunnel job 已 FINISHED 或 FAILED。
调用 `POST /sync-tasks/{name}/refresh` 无法更新状态。

## 根因

三层 bug 叠加：

1. **SeaTunnel 2.3.13 `finished-jobs` 端点 NPE** — 当 job 的 finishedJobMetrics 为 null 时，
   `BaseService.getJobInfoJson` 抛出 NullPointerException，返回 HTTP 500 body `{"status":"fail"}`
2. **本体 `get_job_status` 对 500 直接抛异常** — `sea_tunnel_engine.py` 中 `resp.raise_for_status()` 对 500 抛 `OntologyError`
3. **`refresh_sync_status` 吞掉异常不更新状态** — catch 后保持原状态不动

因果链：job 跑完不在 running-jobs 里 → 查 finished-jobs → 500 NPE → 本体抛异常 → refresh 吞异常 → 状态永不更新。

## 修复

### 1. `sea_tunnel_engine.py` — finished-jobs 错误降级
当 finished-jobs 返回 500 且 body 为 `{"status":"fail"}` 时，
降级为 UNKNOWN 而非抛异常。这样 refresh_sync_status 能正常返回。

### 2. `datasource_service.py` — start_sync 提交后确认真实状态
`start_sync` 在提交 SeaTunnel job 后，主动查一次真实状态，
而非盲目标 RUNNING。

### 3. PG 手动更新(一次性)
`UPDATE sync_tasks SET status='STOPPED' WHERE data_source_id=(...)` 修正已卡住的历史记录。

---

## 备注
- SeaTunnel 社区已在 PR #10700 修复 finished-jobs NPE，合入目标 2.3.14
- 升级后可以将 `get_job_status` 的降级逻辑简化
