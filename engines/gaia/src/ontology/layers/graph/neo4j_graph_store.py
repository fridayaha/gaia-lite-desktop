"""Neo4jGraphStore — Graph Layer (graph-reasoning-design.md §4).

所有 Cypher 收口于此（C1 迁移口子"Cypher 收口"）。Service/工具层只调方法，
不直接写 Cypher。迁移到 NebulaGraph 时只改这一个类。

设计要点：
- **rid 稳定主键**（C1）：Neo4j 节点用 ``object_state.id``（UUID hex）作 ``rid``
  属性 + 唯一约束，不用 Neo4j 内部 id。NebulaGraph 强制 rid，数据 1:1 搬移。
- **强 schema 建模**（C1）：标签/边类型先 CREATE 再写入，不用 schema-less。
- **边模型轻量**（C1）：边仅存 weight + start_time/end_time + visibility。
- **多跳用原生 Cypher**（C9）：``MATCH (n)-[*1..3]->(m)`` + LIMIT，不用 APOC
  ``path.expand``（Neo4j issue #56：不被内存追踪器检测，可能 OOM）。
- **防线**（C9）：图遍历边对数上限 100 万（MVP 下调自 Palantir 官方 1000 万，
  handoff-rid-funnel-closure.md D4），超限截断 + truncated。
- **driver 单例**：AsyncGraphDatabase.driver() 创建一次，lifespan 关闭（R4）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontology.config.settings import settings
from ontology.core.exceptions import GraphUnavailableError
from ontology.core.naming import graph_label, graph_relationship_type
from ontology.core.schemas.graph import (
    EdgeProps,
    EdgeTriple,
    GraphTraversalResult,
    NodeFilter,
    TraversalDirection,
)

if TYPE_CHECKING:
    from neo4j import AsyncDriver

_log = logging.getLogger(__name__)

# ── Neo4j driver 单例（模块级，lifespan 关闭） ──
# R4: driver 创建一次复用，不每次新建。Neo4j 官方性能建议。
_driver: AsyncDriver | None = None


async def _get_driver() -> AsyncDriver:
    """Lazy-init the shared Neo4j AsyncDriver.

    Created once on first use; closed via :func:`close_driver` at application
    shutdown (main.py lifespan). A single driver pools connections; creating
    one per request is an anti-pattern per Neo4j performance docs.
    """
    global _driver
    if _driver is None:
        try:
            from neo4j import AsyncGraphDatabase

            _driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            # 验证连接（失败立即抛 GraphUnavailableError，不延迟到首次查询）。
            await _driver.verify_connectivity()
        except Exception as exc:
            _driver = None
            raise GraphUnavailableError(f"Neo4j unreachable at {settings.neo4j_uri}: {exc}") from exc
    return _driver


async def close_driver() -> None:
    """Close the shared Neo4j driver. Call at app shutdown (main.py lifespan)."""
    global _driver
    if _driver is not None:
        try:
            await _driver.close()
        except Exception:
            pass
        _driver = None


def _escape_string_literal(value: str) -> str:
    """转义 Cypher 字符串字面量（防注入）。

    Cypher 字符串用单引号包裹，内部单引号需转义为 \\'。所有用户可控的
    字符串值（api_name、属性值）经此函数处理后内联进 Cypher。属性值优先
    用参数化（$param），但 schema 名（标签/关系类型）不能用参数化，必须
    内联——此时经命名规范 + 此转义双重保护。
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _render_node_filter(node_alias: str, node_filter: NodeFilter | None) -> str:
    """渲染节点 WHERE 谓词（下推剪枝）。

    仅支持 indexed 属性的等值/范围/in 判断。字段名经白名单校验由调用方
    （DataFrameQueryService 从 ObjectType 元数据推导）保证，此处只做 Cypher
    安全渲染。值用参数化（$param），字段名内联（已校验）。
    ``node_alias`` 是 Cypher 中目标节点的变量名（search_around 中为 ``m``）。
    """
    if node_filter is None:
        return ""
    f = node_filter
    field = f.field
    if f.op == "eq":
        return f"{node_alias}.{field} = $nf_value"
    if f.op == "neq":
        return f"{node_alias}.{field} <> $nf_value"
    if f.op == "gt":
        return f"{node_alias}.{field} > $nf_value"
    if f.op == "gte":
        return f"{node_alias}.{field} >= $nf_value"
    if f.op == "lt":
        return f"{node_alias}.{field} < $nf_value"
    if f.op == "lte":
        return f"{node_alias}.{field} <= $nf_value"
    if f.op == "in":
        return f"{node_alias}.{field} IN $nf_values"
    return ""


class Neo4jGraphStore:
    """Graph Layer。所有 Cypher 收口于此，上层只调方法（C1）。

    生命周期：driver 由模块级单例管理（:func:`_get_driver`），store 实例
    本身轻量无状态，可由 container 按需构造。
    """

    async def _run(self, cypher: str, **params: Any) -> Any:
        """执行 Cypher（自动加 database_ 参数，R4 性能建议）。

        所有 Cypher 经此入口，便于统一加超时/日志/指标。
        """
        driver = await _get_driver()
        try:
            # execute_query 是 Neo4j 5.x 推荐的简化 API（替代 session.run）。
            # database_ 显式指定目标库（避免 driver 额外请求探测默认库）。
            result = await driver.execute_query(cypher, parameters_=params, database_="neo4j")
            return result
        except Exception as exc:
            _log.warning("Neo4j query failed: %s | cypher: %s", exc, cypher[:200])
            if "unreachable" in str(exc).lower() or "connection" in str(exc).lower():
                raise GraphUnavailableError(f"Neo4j query failed: {exc}") from exc
            raise

    # ── Schema（define_object_type / define_link_type 触发） ──

    async def create_label(self, ontology_api_name: str, object_type_api_name: str) -> str:
        """创建节点标签（CREATE CONSTRAINT 强 schema，C1）。

        为 rid 建唯一约束（rid = object_state.rid，C1 稳定主键）。
        返回生成的标签名（供投影器复用）。
        """
        label = graph_label(ontology_api_name, object_type_api_name)
        # 唯一约束确保 rid 唯一（upsert 语义基础）。IF NOT EXISTS 幂等。
        cypher = f"CREATE CONSTRAINT {label}_rid_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.rid IS UNIQUE"
        await self._run(cypher)
        _log.info("Created Neo4j label constraint: %s", label)
        return label

    async def create_relationship_type(
        self, ontology_api_name: str, link_type_api_name: str, temporal: bool = False
    ) -> str:
        """创建关系类型（强 schema 占位，C1）。

        Neo4j 关系类型本身无需预声明，但为强 schema 迁移铺路，此处记录
        元数据（temporal 标记）。MVP 仅日志，不强制预声明（Neo4j 限制）。
        返回生成的关系类型名。
        """
        rel_type = graph_relationship_type(ontology_api_name, link_type_api_name)
        _log.info("Registered Neo4j relationship type: %s (temporal=%s)", rel_type, temporal)
        return rel_type

    async def create_indexed_property_index(
        self, ontology_api_name: str, object_type_api_name: str, prop_api_name: str
    ) -> None:
        """为 indexed 属性建 B-tree 索引（图遍历剪枝）。

        indexed 属性同步到节点做剪枝（C4）。Neo4j 5.x 用 RANGE 索引（B-tree 后继）。
        """
        label = graph_label(ontology_api_name, object_type_api_name)
        idx_name = f"{label}_{prop_api_name}_idx"
        cypher = f"CREATE INDEX {idx_name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop_api_name})"
        await self._run(cypher)
        _log.info("Created Neo4j index: %s.%s", label, prop_api_name)

    # ── 写入（GraphProjector 调用） ──

    async def upsert_node(self, label: str, rid: str, props: dict[str, Any]) -> None:
        """Upsert 节点（MERGE on rid，C1 稳定主键）。

        props 含 rid + api_name + indexed 属性 + visibility。不存全量属性
        （全量在 Doris，水合时借 ObjectQueryService.load_by_ids）。
        """
        if not props:
            props = {"rid": rid}
        elif "rid" not in props:
            props = {**props, "rid": rid}
        # MERGE on rid 幂等 upsert。SET 更新所有属性。
        prop_setters = ", ".join(f"n.{k} = ${k}" for k in props)
        cypher = f"MERGE (n:{label} {{rid: $rid}}) SET {prop_setters}"
        # props 已含 rid，不再重复传 rid 关键字。
        await self._run(cypher, **props)

    async def upsert_edge(
        self,
        rel_type: str,
        source_label: str,
        source_rid: str,
        target_label: str,
        target_rid: str,
        edge_props: EdgeProps | None = None,
    ) -> None:
        """Upsert 边（MERGE on 端点 + 类型，C1 边模型轻量）。

        边仅存 weight + start_time/end_time + visibility（C1）。
        """
        props: dict[str, Any] = {}
        if edge_props is not None:
            if edge_props.weight is not None:
                props["weight"] = edge_props.weight
            if edge_props.start_time is not None:
                props["start_time"] = edge_props.start_time
            if edge_props.end_time is not None:
                props["end_time"] = edge_props.end_time
            if edge_props.visibility is not None:
                props["visibility"] = edge_props.visibility

        # MERGE 端点节点（按 rid），再 MERGE 关系。SET 关系属性。
        prop_setters = ", ".join(f"r.{k} = ${k}" for k in props) if props else ""
        set_clause = f" SET {prop_setters}" if prop_setters else ""
        cypher = (
            f"MATCH (s:{source_label} {{rid: $source_rid}}), "
            f"(t:{target_label} {{rid: $target_rid}}) "
            f"MERGE (s)-[r:{rel_type}]->(t){set_clause}"
        )
        await self._run(
            cypher,
            source_rid=source_rid,
            target_rid=target_rid,
            **props,
        )

    async def delete_node(self, label: str, rid: str) -> None:
        """删除节点及其所有关联边（DETACH DELETE）。"""
        cypher = f"MATCH (n:{label} {{rid: $rid}}) DETACH DELETE n"
        await self._run(cypher, rid=rid)

    async def delete_edge(
        self,
        rel_type: str,
        source_label: str,
        source_rid: str,
        target_label: str,
        target_rid: str,
    ) -> None:
        """删除指定边。"""
        cypher = (
            f"MATCH (s:{source_label} {{rid: $source_rid}})"
            f"-[r:{rel_type}]->"
            f"(t:{target_label} {{rid: $target_rid}}) "
            f"DELETE r"
        )
        await self._run(cypher, source_rid=source_rid, target_rid=target_rid)

    # ── 批量写入（VIRTUAL 联邦投影，ADR-021） ──

    async def upsert_nodes_batch(self, label: str, nodes: list[dict[str, Any]]) -> int:
        """批量 upsert 节点（UNWIND + CALL {} IN TRANSACTIONS，ADR-021 §2.5）。

        比 :meth:`upsert_node` 逐条 MERGE 快一个数量级。每 1000 行一个内部
        事务，避免大事务 OOM。依赖 rid 唯一约束（:meth:`create_label` 已建）。

        Args:
            label: 节点标签。
            nodes: 节点属性 dict 列表，每个 dict 必须含 ``rid`` 键。

        Returns: 写入的节点数。
        """
        if not nodes:
            return 0
        # UNWIND 展开参数，CALL {} IN TRANSACTIONS 分批提交（Neo4j 5 原生，
        # 替代 deprecated 的 apoc.periodic.iterate，难点 6 决策）。
        # 所有节点假设同 schema（keys 一致），取首个节点的 keys 构造 SET 子句。
        prop_keys = [k for k in nodes[0].keys() if k != "rid"]
        set_clause = ", ".join(f"n.{k} = row.{k}" for k in prop_keys)
        # SET n.rid 显式写出（保证 rid 总被设，即使 prop_keys 为空）
        full_set = "SET n.rid = row.rid" + (f", {set_clause}" if set_clause else "")
        cypher = (
            f"UNWIND $rows AS row "
            f"CALL {{ WITH row "
            f"  MERGE (n:{label} {{rid: row.rid}}) "
            f"  {full_set} "
            f"}} IN TRANSACTIONS OF 1000 ROWS"
        )
        await self._run(cypher, rows=nodes)
        return len(nodes)

    async def upsert_edges_batch(
        self,
        rel_type: str,
        source_label: str,
        target_label: str,
        edges: list[tuple[str, str]],
    ) -> int:
        """批量 upsert 边（UNWIND + CALL {} IN TRANSACTIONS，ADR-021 §2.5）。

        边仅建关系（无 edge_props，C1 边模型轻量）。端点节点需已存在
        （MERGE 端点由节点投影负责，此处只 MATCH + MERGE 关系）。

        Args:
            rel_type: 关系类型。
            source_label / target_label: 端点标签。
            edges: (source_rid, target_rid) 元组列表。

        Returns: 写入的边数。
        """
        if not edges:
            return 0
        rows = [{"s": s, "t": t} for s, t in edges]
        cypher = (
            f"UNWIND $rows AS row "
            f"CALL {{ WITH row "
            f"  MATCH (s:{source_label} {{rid: row.s}}), "
            f"        (t:{target_label} {{rid: row.t}}) "
            f"  MERGE (s)-[r:{rel_type}]->(t) "
            f"}} IN TRANSACTIONS OF 1000 ROWS"
        )
        await self._run(cypher, rows=rows)
        return len(edges)

    async def cleanup_stale_virtual(self, label: str, current_sync_tag: int) -> int:
        """删除本次投影未触及的 VIRTUAL 节点（源里已删除的孤儿，ADR-021 §2.4）。

        cartography 范式：MERGE first, then clean up。节点投影完成后调用。
        仅清理带 ``_virtual: true`` 且 ``_sync_tag <> current`` 的节点，
        **绝不误删 MANAGED 节点**（MANAGED 无 _virtual 标记）。

        Args:
            label: 节点标签。
            current_sync_tag: 本次投影的水位标记（int）。

        Returns: 删除的节点数。
        """
        # DETACH DELETE 连带删除孤儿节点的边。WHERE 限定 _virtual 防误删 MANAGED。
        cypher = (
            f"MATCH (n:{label} {{_virtual: true}}) "
            f"WHERE n._sync_tag <> $current_tag "
            f"DETACH DELETE n "
            f"RETURN count(*) AS deleted"
        )
        result = await self._run(cypher, current_tag=current_sync_tag)
        # execute_query 返回 EagerResult，records 在 .records
        deleted = 0
        if result and hasattr(result, "records") and result.records:
            rec = result.records[0]
            deleted = rec["deleted"] if "deleted" in rec.keys() else 0
        _log.info("cleanup_stale_virtual(%s): deleted %d orphan nodes", label, deleted)
        return deleted

    # ── 查询（DataFrameQueryService 的 searchAround 步骤调用） ──

    async def search_around(
        self,
        label: str,
        source_rids: list[str],
        hops: tuple[int, int],
        rel_types: list[str] | None = None,
        direction: TraversalDirection = "both",
        node_filter: NodeFilter | None = None,
        limit: int | None = None,
    ) -> GraphTraversalResult:
        """多跳遍历（C9：原生 Cypher，不用 APOC path.expand）。

        Args:
            label: 目标节点标签（图遍历返回此标签的节点 rid）。
            source_rids: 起始 rid 集（= object_state.id）。
            hops: (min, max) 跳数，默认 (1, 3)。
            rel_types: 限定关系类型；None=任意关系。
            direction: out/in/both。Neo4j 原生双向。
            node_filter: 下推 WHERE 谓词（剪枝）。
            limit: 结果上限——作用于去重 (start, m) 边对数（方向 B 精细化,
                handoff-rid-funnel-closure.md T1.7），不是终点 rid 数。C9 默认 100 万。

        Returns:
            GraphTraversalResult: rids 为去重终点 rid 集（保序）; edges 为去重
            (start→target) 边三元组; matched_count 为边对数; truncated 表示
            边对数达上限。
        """
        if not source_rids:
            return GraphTraversalResult()

        effective_limit = limit or settings.graph_traversal_result_limit
        min_hops, max_hops = hops

        # 构造关系模式：[*min..max] + 方向 + 类型。
        if rel_types:
            rel_pattern = "|".join(f":{rt}" for rt in rel_types)
        else:
            rel_pattern = ""
        # 方向：out -> , in <- , both -。
        if direction == "out":
            rel = f"-[{rel_pattern}*{min_hops}..{max_hops}]->"
        elif direction == "in":
            rel = f"<-[{rel_pattern}*{min_hops}..{max_hops}]-"
        else:
            rel = f"-[{rel_pattern}*{min_hops}..{max_hops}]-"

        # 起始节点用 UNWIND $rids 批量匹配（避免超大 IN）。
        where = ""
        params: dict[str, Any] = {"rids": source_rids, "limit": effective_limit}
        nf_clause = _render_node_filter("m", node_filter)
        clauses = []
        if nf_clause:
            clauses.append(nf_clause)
            if node_filter and node_filter.op == "in":
                params["nf_values"] = node_filter.values or []
            elif node_filter:
                params["nf_value"] = node_filter.value
        if clauses:
            where = " WHERE " + " AND ".join(clauses)

        cypher = (
            f"UNWIND $rids AS src_rid "
            f"MATCH (start {{rid: src_rid}}){rel}(m:{label}){where} "
            f"WITH DISTINCT start, m LIMIT $limit "
            f"RETURN m.rid AS rid, start.rid AS src_rid"
        )
        result = await self._run(cypher, **params)

        # 收集 start→target 配对（去重），用于画布渲染探索轨迹箭头（ADR-015）。
        # rel_type 取本次遍历的关系类型；多 rel_types 时无法区分具体边类型,
        # 用第一个（前端按 link_type 渲染，工具层会用 IR.link 覆盖）。
        edge_pairs: set[tuple[str, str]] = set()
        primary_rel = rel_types[0] if rel_types else ""
        # rids 去重保序（方向 B 精细化, handoff-rid-funnel-closure.md T1.7）：
        # 同一个 m 可能被多个 start 命中, 原实现会重复 append 导致下游水合
        # 重复取数。这里用 dict.fromkeys 保序去重。
        deduped_rids: list[str] = []
        seen_rids: set[str] = set()
        # 注意：Neo4j Record 的 __contains__ 只认 index 不认 key（"src_rid" in
        # record 恒为 False），必须用 record.get(key) 取值。
        for record in result.records:
            rid = record["rid"]
            src_rid = record.get("src_rid")
            if rid is not None:
                rid_s = str(rid)
                if rid_s not in seen_rids:
                    seen_rids.add(rid_s)
                    deduped_rids.append(rid_s)
            if src_rid is not None and rid is not None:
                edge_pairs.add((str(src_rid), str(rid)))

        # LIMIT 语义（方向 B 精细化）：LIMIT 作用于去重 (start, m) 边对数
        # （Cypher `WITH DISTINCT start, m LIMIT $limit`），不是终点 rid 数。
        # matched_count = 边对数; truncated 基于边对数判定。下游去重 rid 集
        # 自然 ≤ 边对数, 不单独截断（防线二拆两行, graph-reasoning-design.md §8.2）。
        matched_edges = len(edge_pairs)
        truncated = matched_edges >= effective_limit
        edges = [
            EdgeTriple(
                source_rid=src,
                target_rid=tgt,
                rel_type=primary_rel,
                direction=direction,
            )
            for src, tgt in edge_pairs
        ]
        return GraphTraversalResult(
            rids=deduped_rids,
            edges=edges,
            truncated=truncated,
            hops=max_hops,
            matched_count=matched_edges,
        )

    async def find_paths(
        self,
        source_rid: str,
        target_rid: str,
        rel_types: list[str] | None = None,
        max_depth: int = 5,
        limit: int = 10,
    ) -> list[list[str]]:
        """最短路径推理（Phase 2d 路径推理）。

        用 Neo4j ``allShortestPaths`` 找源→目标的所有最短路径，返回 rid 序列。
        限制 max_depth（默认 5）避免爆炸，limit 控制返回路径数。

        Args:
            source_rid: 起点 rid。
            target_rid: 终点 rid。
            rel_types: 限定关系类型；None=任意。
            max_depth: 最大跳数（默认 5）。
            limit: 返回路径上限（默认 10）。

        Returns:
            路径列表，每条路径是 rid 序列 [source, ..., target]。
        """
        if rel_types:
            rel_pattern = ":" + "|".join(rel_types)
        else:
            rel_pattern = ""
        cypher = (
            "MATCH (start {rid: $src}), (end {rid: $tgt}) "
            f"MATCH p = allShortestPaths((start)-[{rel_pattern}*1..{max_depth}]-(end)) "
            "RETURN [n IN nodes(p) | n.rid] AS path "
            "LIMIT $limit"
        )
        result = await self._run(cypher, src=source_rid, tgt=target_rid, limit=limit)
        paths: list[list[str]] = []
        for record in result.records:
            raw = record["path"]
            if raw:
                paths.append([str(v) for v in raw if v is not None])
        return paths

    async def exists_link(
        self,
        rel_type: str,
        source_label: str,
        source_rid: str,
        target_label: str,
        target_rid: str | None = None,
        direction: TraversalDirection = "out",
    ) -> bool:
        """检查关系是否存在（exists_link 工具底层）。

        target_rid=None → ANY_TARGET 模式（源是否有至少一条此类型关系）。
        target_rid 给定 → SINGLE_TARGET 模式（源是否关联此特定目标）。
        """
        if direction == "out":
            rel = f"-[r:{rel_type}]->"
        elif direction == "in":
            rel = f"<-[r:{rel_type}]-"
        else:
            rel = f"-[r:{rel_type}]-"

        if target_rid is None:
            cypher = f"MATCH (s:{source_label} {{rid: $source_rid}}){rel}() RETURN count(r) > 0 AS exists LIMIT 1"
            result = await self._run(cypher, source_rid=source_rid)
        else:
            cypher = (
                f"MATCH (s:{source_label} {{rid: $source_rid}})"
                f"{rel}"
                f"(t:{target_label} {{rid: $target_rid}}) "
                f"RETURN count(r) > 0 AS exists LIMIT 1"
            )
            result = await self._run(cypher, source_rid=source_rid, target_rid=target_rid)

        if result.records:
            return bool(result.records[0]["exists"])
        return False

    async def count_nodes(self, label: str) -> int:
        """统计某标签节点数（rebuild/对账用）。"""
        cypher = f"MATCH (n:{label}) RETURN count(n) AS cnt"
        result = await self._run(cypher)
        if result.records:
            return int(result.records[0]["cnt"])
        return 0
