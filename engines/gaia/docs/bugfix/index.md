# 事故复盘

> 本节记录开发过程中的关键 bug 与修复，每条都是真金白银踩出来的教训。

- [benchmark 检测到的后端缺陷](benchmark-detected-backend-defects)
- [DB 连接泄漏与点查性能](db-connection-leak-and-point-lookup-perf)
- [eval: Doris 全量数据替换 Dataset Lookup](eval-doris-full-data-replace-dataset-lookup)
- [Gravitino 1.3.0 升级](gravitino-1.3.0-upgrade)
- [Gravitino external(...) 类型阻塞外部数据源预览](gravitino-external-type-blocks-datasource-preview)
- [HITL 批量审批 pending (pydantic-ai)](hitl-batch-approval-pending-pydantic-ai)
- [Managed Dataset 治理记录缺失](managed-dataset-governance-record-missing)
- [Object Picker 异步 ComboBox](object-picker-async-combobox)
- [数据源「浏览 Schema」连续切表崩溃（React Aria Table 竞态）](datasource-schema-browse-table-crash)
- [本体废弃/删除 UX](ontology-deprecate-delete-ux)
- [Path B: Kafka-Doris Schema 不匹配](path-b-kafka-doris-schema-mismatch)
- [SeaTunnel 索引管道 Iceberg→Doris 不可用](seatunnel-index-pipeline-iceberg-doris-unavailable)
- [SeaTunnel PG CDC timestamptz 阻塞](seatunnel-pg-cdc-timestamptz-blocker)
- [同步任务 safe_query 未给标识符加引号 → PG camelCase 列名同步失败](seatunnel-sync-safe-query-unquoted-identifier)
- [SeaTunnel Worker OOM + Doris BE 内存限制](seatunnel-worker-oom-and-doris-be-mem-limit)
- [ACTION_TYPE_VERSION 快照未持久化](action-type-version-snapshot-not-persisted)
- [同步任务状态卡在 RUNNING](sync-task-status-stuck-running)
- [Pipeline Builder: auto-save 无限循环 + 配置面板闪退](pipeline-builder-autosave-loop-and-config-panel-dismiss)
- [数据预览水平滚动条亮色主题下不可见](preview-horizontal-scrollbar-invisible-on-light-theme)
