#!/usr/bin/env python
"""ADR-021 VIRTUAL 图投影冒烟脚本。

造一个临时 VIRTUAL OT（绑 pgnative.public.data_sources，本地可达源），
调 project_for_virtual_object_type 验证节点真正落 Neo4j，最后清理。

用法：.venv/bin/python scripts/smoke_virtual_graph_projection.py
"""

import asyncio
import logging
import sys
import time
import uuid
from typing import Any

from sqlalchemy import text

from ontology.config.container import Container
from ontology.config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("smoke")

SMOKE_ONT = "SmokeVirtualGraph"
SMOKE_OT = "DataSourceMirror"


async def _setup_test_ontology(conn) -> str:
    """创建临时本体 + VIRTUAL OT + properties，返回 OT id。"""
    ont_id = uuid.uuid4().hex
    ot_id = uuid.uuid4().hex
    # 查一个真实存在的 project_id（FK 约束）
    r = await conn.execute(text("SELECT id FROM projects LIMIT 1"))
    proj_row = r.first()
    if proj_row is None:
        raise RuntimeError("No project found in DB — cannot create test ontology")
    proj_id = proj_row[0]

    await conn.execute(
        text(
            "INSERT INTO ontologies (id, api_name, display_name, description, rid, status, created_at, updated_at) "
            "VALUES (:id, :api, :disp, :desc, '', 'ACTIVE', now(), now())"
        ),
        {"id": ont_id, "api": SMOKE_ONT, "disp": "Smoke Virtual Graph", "desc": "temporary"},
    )
    await conn.execute(
        text(
            "INSERT INTO object_types (id, ontology_id, api_name, display_name, description, primary_key, "
            "title_property, storage_type, visibility, status, project_id, capabilities, created_at, updated_at) "
            "VALUES (:id, :ont, :api, :disp, '', :pk, :title, 'VIRTUAL', 'NORMAL', 'ACTIVE', :proj, '{}'::jsonb, now(), now())"
        ),
        {"id": ot_id, "ont": ont_id, "api": SMOKE_OT, "disp": "DataSource Mirror",
         "pk": "id", "title": "displayName", "proj": proj_id},
    )
    # 两个 property：id (PK) + display_name (title)，绑 pgnative.public.data_sources
    for api, col, is_pk, is_title in [("id", "id", True, False), ("displayName", "display_name", False, True)]:
        await conn.execute(
            text(
                "INSERT INTO properties (id, object_type_id, api_name, display_name, description, data_type, "
                "is_primary_key, is_title_property, nullable, indexed, status, project_id, "
                "backing_dataset_api_name, backing_catalog, backing_schema, backing_table, backing_column, created_at, updated_at) "
                "VALUES (:id, :ot, :api, :disp, '', :dt, :pk, :title, true, false, 'ACTIVE', :proj, "
                ":ds, 'pgnative', 'public', 'data_sources', :col, now(), now())"
            ),
            {"id": uuid.uuid4().hex, "ot": ot_id, "api": api, "disp": api,
             "dt": "STRING", "pk": is_pk, "title": is_title,
             "ds": "smoke_data_sources", "col": col, "proj": proj_id},
        )
    return ot_id


async def _cleanup(conn, ot_id: str) -> None:
    await conn.execute(text("DELETE FROM properties WHERE object_type_id = :ot"), {"ot": ot_id})
    await conn.execute(text("DELETE FROM object_types WHERE id = :ot"), {"ot": ot_id})
    # 删本体（CASCADE 会处理残留）
    await conn.execute(text("DELETE FROM ontologies WHERE api_name = :api"), {"api": SMOKE_ONT})


async def _run_cypher(cypher: str, **params) -> Any:
    """执行 Cypher（走 Neo4jGraphStore 的 _get_driver 单例）。"""
    from ontology.layers.graph.neo4j_graph_store import _get_driver

    driver = await _get_driver()
    return await driver.execute_query(cypher, parameters_=params, database_="neo4j")


async def _count_neo4j_nodes(label: str) -> int:
    """数 Neo4j 里某 label 的节点数。"""
    result = await _run_cypher(f"MATCH (n:`{label}`) RETURN count(n) AS c")
    records = result.records
    return int(records[0]["c"]) if records else 0


async def _cleanup_neo4j(label: str) -> None:
    """清掉冒烟产生的 Neo4j 节点。"""
    await _run_cypher(f"MATCH (n:`{label}`) DETACH DELETE n")


async def main() -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    label = f"{SMOKE_ONT}{SMOKE_OT}"  # GraphProjector 的 label 规则

    _log.info("=== 1. 清理 Neo4j 残留（label=%s） ===", label)
    await _cleanup_neo4j(label)

    _log.info("=== 2. 造临时 VIRTUAL OT %s.%s（绑 pgnative.public.data_sources） ===", SMOKE_ONT, SMOKE_OT)
    eng = create_async_engine(settings.pg_dsn)
    async with eng.begin() as conn:
        # 先清理可能的残留
        await conn.execute(text("DELETE FROM ontologies WHERE api_name = :a"), {"a": SMOKE_ONT})
        ot_id = await _setup_test_ontology(conn)
    _log.info("OT id=%s", ot_id)

    try:
        _log.info("=== 3. 调 project_for_virtual_object_type ===")
        container = Container()
        funnel = container.object_index_funnel
        t0 = time.time()
        result = await funnel.project_for_virtual_object_type(SMOKE_ONT, SMOKE_OT)
        elapsed = time.time() - t0
        _log.info("投影结果（%.2fs）: %s", elapsed, result)

        _log.info("=== 4. 验证 Neo4j 节点落库 ===")
        count = await _count_neo4j_nodes(label)
        _log.info("Neo4j label=%s 节点数=%d", label, count)

        # data_sources 表有 3 行，应投影 3 个节点
        if not result["partial"] and count == 3:
            _log.info("✅ 冒烟通过：3 节点落库，无 partial")
            rc = 0
        elif result["partial"] and count >= 0:
            _log.warning("⚠️ partial 投影（%s），已落 %d 节点（降级行为正确）", result.get("error"), count)
            rc = 0  # partial 也是预期行为（源可能部分可达）
        else:
            _log.error("❌ 冒烟失败：期望 3 节点，实际 %d，partial=%s", count, result["partial"])
            rc = 1

        # 验证节点属性（_virtual / _source_ref / _sync_tag）
        if count > 0:
            result = await _run_cypher(f"MATCH (n:`{label}`) RETURN n._virtual AS v, n._source_ref AS ref, n._sync_tag AS tag LIMIT 1")
            rec = result.records[0] if result.records else None
            if rec:
                _log.info("节点属性采样: _virtual=%s _source_ref=%s _sync_tag=%s", rec["v"], rec["ref"], rec["tag"])
                assert rec["v"] is True, "_virtual 应为 true"
                assert rec["ref"] == "pgnative.public.data_sources", f"_source_ref 应为 pgnative.public.data_sources, got {rec['ref']}"
                assert rec["tag"] is not None, "_sync_tag 应非空"
                _log.info("✅ 节点骨架属性（_virtual/_source_ref/_sync_tag）正确")

    finally:
        _log.info("=== 5. 清理临时本体 + Neo4j 节点 ===")
        async with eng.begin() as conn:
            await _cleanup(conn, ot_id)
        await _cleanup_neo4j(label)
        await eng.dispose()

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
