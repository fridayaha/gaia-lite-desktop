"""SyncFlushScheduler — ARCHIVE outbox 微批调度 + outbox 清理。

action-sync-outbox-design.md §5/§8.6: Action 写 PG object_state 时同步追加
ARCHIVE effect outbox 记录, 本调度器微批消费, 把 object_state 变更归档到
Iceberg 业务表 (MERGE INTO, 按业务 PK 覆盖)。

两条后台循环:

1. ``run_flush_loop`` (60s tick): 按 ontology 分桶, 双触发:
   - count >= FLUSH_COUNT_THRESHOLD (1000) → 立即 flush
   - 距上次 flush >= FLUSH_TIME_THRESHOLD (5min) → flush
   tick 间隔 60s 远小于 5min 窗口, 保证时间触发不会迟于 6min。

2. ``run_cleanup_loop`` (1h): 清理 7 天前 COMPLETED/FAILED 记录。
   DLQ 不自动删 (人工审查)。PENDING 不删 (等消费/重试)。

设计要点 (design §3.2/§3.3/§3.6):
- 调度按 ontology 分桶 (一个 Action 涉及多 ObjectType 的变更尽量同批 flush,
  保证事务完整性); 物理写入按 ObjectType 分写 (Doris/Iceberg 跨表无法原子
  commit, "尽量同时" 是能做到的最好一致性)。
- MERGE INTO 的 PK 是业务主键 backing_column, 不是 object_id (design §3.3)。
- PG READ COMMITTED 保证 flusher 只看到已 commit 的 outbox 行 (Action 事务内
  outbox 对 flusher 不可见, 不会读到半成品)。
- claim 用 FOR UPDATE SKIP LOCKED, 多实例 HA 自动分片 (design §3.7)。

Lifecycle: started by main.py lifespan as background tasks, cancelled on
shutdown. Best-effort: per-tick errors are swallowed so the loop survives.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ontology.core.exceptions import OntologyError
from ontology.core.models.defaults import utcnow
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

if TYPE_CHECKING:
    # IcebergStore 仅类型注解；移入 TYPE_CHECKING 避免 lite 版拉 pyiceberg 重依赖（A3）。
    from ontology.config.container import Container
    from ontology.layers.dataset.iceberg_store import IcebergStore

_log = logging.getLogger("ontology.sync_flush_scheduler")

# ── 微批阈值 (design §5.1) ──
FLUSH_TICK_INTERVAL = 60.0  # tick 间隔: 1min (远小于 5min 窗口, 保证时间触发不迟于 6min)
FLUSH_COUNT_THRESHOLD = 1000  # 攒满 1000 条立即 flush
FLUSH_TIME_THRESHOLD = 300.0  # 5min 窗口 (距上次 flush 超过 5min 触发)

# ── 清理 (design §4.3) ──
CLEANUP_INTERVAL = 3600.0  # 1h
CLEANUP_RETENTION_DAYS = 7  # COMPLETED/FAILED 保留 7 天


class SyncFlushScheduler:
    """ARCHIVE outbox 微批归档到 Iceberg + outbox 清理。

    注入 IcebergStore (写 Iceberg 业务表) + metadata_factory (查 ObjectType
    配置 + claim/mark outbox)。metadata_factory 每次返回独立 session 的
    PostgresMetaStore (后台循环不能复用请求级 session)。
    """

    def __init__(
        self,
        dataset: IcebergStore,
        metadata_factory: Callable[[], PostgresMetaStore],
        *,
        flush_tick_interval: float = FLUSH_TICK_INTERVAL,
        flush_count_threshold: int = FLUSH_COUNT_THRESHOLD,
        flush_time_threshold: float = FLUSH_TIME_THRESHOLD,
        cleanup_interval: float = CLEANUP_INTERVAL,
        cleanup_retention_days: int = CLEANUP_RETENTION_DAYS,
    ) -> None:
        self._dataset = dataset
        self._metadata_factory = metadata_factory
        self._flush_tick_interval = flush_tick_interval
        self._flush_count_threshold = flush_count_threshold
        self._flush_time_threshold = flush_time_threshold
        self._cleanup_interval = cleanup_interval
        self._cleanup_retention_days = cleanup_retention_days
        # 上次 flush 时间 (按 ontology api_name)。首次 tick 立即 flush (初始化为很久以前)。
        self._last_flush_at: dict[str, datetime] = {}
        # ObjectType → pk_columns 缓存 (design §8.6)。ObjectType define/update
        # 时失效由 invalidate_pk_cache 显式触发 (本期未接, 简单进程内缓存)。
        self._pk_cache: dict[tuple[str, str], list[str]] = {}

    # ═════════════════════════════════════════════════════════════════
    # 后台循环 (lifespan 启动)
    # ═════════════════════════════════════════════════════════════════

    async def run_flush_loop(self, container: Container | None = None) -> None:
        """ARCHIVE flush 主循环: 每 tick 检查各 ontology 是否达到双触发阈值。

        container 参数仅为与其他 scheduler 签名一致 (run_backfill_loop(container)),
        本调度器不需要 container (依赖已在构造时注入)。
        """
        _log.info(
            "SyncFlushScheduler flush loop started (tick=%.0fs, count≥%d or ≥%.0fs)",
            self._flush_tick_interval,
            self._flush_count_threshold,
            self._flush_time_threshold,
        )
        while True:
            try:
                await self._flush_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — loop must survive per-iter errors
                _log.exception("SyncFlushScheduler flush loop error")
            await asyncio.sleep(self._flush_tick_interval)

    async def run_cleanup_loop(self, container: Container | None = None) -> None:
        """outbox 清理循环: 每 1h 删除 7 天前 COMPLETED/FAILED 记录。"""
        _log.info(
            "SyncFlushScheduler cleanup loop started (interval=%.0fs, retention=%dd)",
            self._cleanup_interval,
            self._cleanup_retention_days,
        )
        while True:
            try:
                await self._cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — loop must survive per-iter errors
                _log.exception("SyncFlushScheduler cleanup loop error")
            await asyncio.sleep(self._cleanup_interval)

    # ═════════════════════════════════════════════════════════════════
    # flush tick
    # ═════════════════════════════════════════════════════════════════

    async def _flush_tick(self) -> int:
        """单次 tick: 检查所有 ontology 的 ARCHIVE PENDING 数, 触发 flush。

        Returns 本次 tick 触发 flush 的 ontology 数。
        """
        meta = self._metadata_factory()
        try:
            counts = await meta.count_pending_by_ontology("ARCHIVE")
        finally:
            await meta.close()

        now = utcnow()
        triggered = 0
        for ontology, count in counts:
            if ontology is None:
                # target_ontology 为空 (历史/异常记录) 跳过, 不归档。
                continue
            last = self._last_flush_at.get(ontology)
            age = (now - last).total_seconds() if last is not None else float("inf")
            if count >= self._flush_count_threshold or age >= self._flush_time_threshold:
                try:
                    await self._flush_ontology(ontology)
                    self._last_flush_at[ontology] = utcnow()
                    triggered += 1
                except Exception:  # noqa: BLE001
                    _log.exception("SyncFlushScheduler: flush ontology %s failed", ontology)
        return triggered

    async def _flush_ontology(self, ontology: str) -> None:
        """认领一个 ontology 的 ARCHIVE 批次 → 按 ObjectType 拆分 → 各自 MERGE。

        design §3.2/§8.6:
        1. claim_pending_by_ontology (FOR UPDATE SKIP LOCKED, 多实例 HA 安全)
        2. 按 object_type_api_name 拆分批次
        3. 每个 ObjectType: 查 primary_key→backing_column → 按 mutation_type 分流
           - CREATE/UPDATE: rows=[properties] → IcebergStore.merge(delete=False)
           - DELETE:        rows=[{pk_col: pk_val}] → IcebergStore.merge(delete=True)
        4. 全部成功 → mark_outbox_batch_completed; 任一失败 → retry_outbox_batch

        ⚠️ 事务边界: claim + mark 在**同一个 session** 内完成。FOR UPDATE SKIP
        LOCKED 的行锁持有到最终 commit, 保证多实例 HA 下被 claim 的记录不会被
        另一实例重复 claim (design §3.7)。Iceberg MERGE 在 session 外执行 (不持 PG
        锁), 失败的记录由 retry_outbox_batch 回退 PENDING。单实例下行锁无额外开销。
        """
        meta = self._metadata_factory()
        try:
            records = await meta.claim_pending_by_ontology("ARCHIVE", ontology, batch_size=self._flush_count_threshold)
            if not records:
                # 释放 claim SELECT 开启的事务 (避免 idle-in-transaction)。
                await meta.commit_transaction()
                return

            _log.info(
                "SyncFlushScheduler: flushing %d ARCHIVE records for ontology %s",
                len(records),
                ontology,
            )

            # 按 ObjectType 拆分 (design §3.2 物理写入维度)。
            by_type: dict[str, list[dict[str, Any]]] = {}
            for rec in records:
                ot_api = (rec.get("payload", {}) or {}).get("object_type_api_name", "")
                if ot_api:
                    by_type.setdefault(ot_api, []).append(rec)

            failed_ids: list[str] = []
            for ot_api, type_records in by_type.items():
                try:
                    await self._flush_object_type(ontology, ot_api, type_records)
                except Exception as exc:  # noqa: BLE001
                    _log.exception(
                        "SyncFlushScheduler: flush %s/%s failed (%d records will retry): %s",
                        ontology,
                        ot_api,
                        len(type_records),
                        exc,
                    )
                    failed_ids.extend(r["id"] for r in type_records)
                    # 单 type 失败不影响同批其他 type (design §3.2 各自独立写)。

            # 成功的标记 COMPLETED, 失败的回退 PENDING 等待下个 tick 重试。
            # 在同一 session (行锁仍持有) 提交, 释放所有行锁。
            success_ids = [r["id"] for r in records if r["id"] not in failed_ids]
            if success_ids:
                await meta.mark_outbox_batch_completed(success_ids)
            if failed_ids:
                await meta.retry_outbox_batch(failed_ids, "ARCHIVE flush failed (see logs)")
        finally:
            await meta.close()

    async def _flush_object_type(
        self, ontology_api_name: str, object_type_api_name: str, records: list[dict[str, Any]]
    ) -> None:
        """一个 ObjectType 的 ARCHIVE 批次 → MERGE INTO Iceberg 业务表。

        design §3.3: MERGE 的 PK 是业务 primary_key 的 backing_column。
        CREATE/UPDATE 写全量列; DELETE 只需 PK 列。
        """
        pk_columns = await self._resolve_pk_columns(ontology_api_name, object_type_api_name)
        if not pk_columns:
            raise OntologyError(f"ObjectType {object_type_api_name} has no primary_key; cannot MERGE (design §10)")

        upsert_rows: list[dict[str, Any]] = []
        delete_rows: list[dict[str, Any]] = []
        for rec in records:
            payload = rec.get("payload", {}) or {}
            mut_type = payload.get("mutation_type", "")
            props = payload.get("properties", {}) or {}
            if mut_type == "DELETE_OBJECT":
                # 只需 PK 列。
                pk_row = {pk: props.get(pk) for pk in pk_columns}
                # 任一 PK 值缺失 → 无法定位行, skip (幂等)。
                if all(v is not None for v in pk_row.values()):
                    delete_rows.append(pk_row)
            elif mut_type in ("CREATE_OBJECT", "UPDATE_OBJECT", "UPDATE_PROPERTY"):
                if props:
                    upsert_rows.append(dict(props))
            # 未知 mutation_type 跳过 (_create_sync_outbox_records 已过滤)。

        table = self._iceberg_table_name(object_type_api_name)
        if upsert_rows:
            await self._dataset.merge(table, upsert_rows, pk_columns, delete=False)
            _log.info(
                "SyncFlushScheduler: MERGE upsert %d rows → %s (%s/%s)",
                len(upsert_rows),
                table,
                ontology_api_name,
                object_type_api_name,
            )
        if delete_rows:
            await self._dataset.merge(table, delete_rows, pk_columns, delete=True)
            _log.info(
                "SyncFlushScheduler: MERGE delete %d rows → %s (%s/%s)",
                len(delete_rows),
                table,
                ontology_api_name,
                object_type_api_name,
            )

    # ── 配置查询 (带缓存, design §8.6) ──

    async def _resolve_pk_columns(self, ontology_api_name: str, object_type_api_name: str) -> list[str]:
        """查 ObjectType.primary_key api_name → PropertyDef backing_column。

        返回 PK 列名列表 (单列 PK 为 1 元素)。缓存避免每条记录查一次。
        """
        key = (ontology_api_name, object_type_api_name)
        cached = self._pk_cache.get(key)
        if cached is not None:
            return cached
        meta = self._metadata_factory()
        try:
            ot = await meta.get_object_type(ontology_api_name, object_type_api_name)
        finally:
            await meta.close()
        pk_api = ot.primary_key
        pk_columns: list[str] = []
        if pk_api:
            # 找 primary_key 属性的 backing_column (默认回退 api_name)。
            for prop in ot.properties:
                if prop.api_name == pk_api:
                    col = prop.backing_mapping.backing_column if prop.backing_mapping else pk_api
                    pk_columns.append(col)
                    break
            if not pk_columns:
                # primary_key 指向的属性不存在 (配置异常) → 回退 api_name。
                pk_columns.append(pk_api)
        self._pk_cache[key] = pk_columns
        return pk_columns

    def invalidate_pk_cache(self, ontology_api_name: str, object_type_api_name: str) -> None:
        """ObjectType define/update 时调用, 失效 PK 缓存。"""
        self._pk_cache.pop((ontology_api_name, object_type_api_name), None)

    @staticmethod
    def _iceberg_table_name(object_type_api_name: str) -> str:
        """ObjectType → Iceberg 业务表名 (ontology schema, snake_case)。

        design §3.3: 复用业务 Iceberg 表 (ontology.<snake_type>)。命名走
        core/naming 的 managed_dataset_api_name (snake_case, 保词界), 与
        SeaTunnel sink 建表 / IndexSyncService.sync_now 一致 (后者同样用
        ``ontology.{dataset_api_name or _to_snake(ot)}`` 引用业务表)。
        """
        from ontology.core.naming import managed_dataset_api_name

        return f"ontology.{managed_dataset_api_name(object_type_api_name)}"

    # ═════════════════════════════════════════════════════════════════
    # cleanup
    # ═════════════════════════════════════════════════════════════════

    async def _cleanup_once(self) -> int:
        """删除 7 天前 COMPLETED/FAILED outbox 记录。Returns 删除行数。"""
        meta = self._metadata_factory()
        try:
            deleted = await meta.delete_old_completed_outbox(self._cleanup_retention_days)
        finally:
            await meta.close()
        if deleted > 0:
            _log.info("SyncFlushScheduler cleanup: deleted %d old outbox records", deleted)
        return deleted


__all__ = ["SyncFlushScheduler"]
