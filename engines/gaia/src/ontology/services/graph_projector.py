"""GraphProjector — object_state/links → Neo4j 投影 (graph-reasoning-design.md §6.2).

投影器读 ObjectType 元数据决定写什么（C4 本体元数据驱动分发）：
- 仅 ``indexed`` 属性同步到图节点做剪枝（不存全量属性，全量在 Doris）
- 边仅存 weight + start_time/end_time + visibility（C1 边模型轻量）
- rid = object_state.rid（C1 稳定主键，Palantir RID 规范）

触发模式（§6.1）：
- A. Action 写入：Action 原子提交 PG 后触发投影（fail-tolerant，失败进 outbox 重试）
- B. SeaTunnel 批量接入：主流水线 success → 触发投影 sink

投影器是同源分发的派生副本（§6.3），可全量重建（rebuild_graph），
禁止反向写。一致性模型（C8）：默认秒级最终一致 + 可选 sync=true。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontology.core.naming import graph_label, graph_relationship_type
from ontology.core.property_mapping import backing_to_api
from ontology.core.schemas.graph import EdgeProps
from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore

if TYPE_CHECKING:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)


class GraphProjector:
    """object_state/links 变更 → Neo4j 投影。读 ObjectType 元数据决定写什么。

    依赖注入：
    - metadata：读 ObjectType/LinkType 元数据（C4 本体驱动分发）
    - graph_store：Neo4jGraphStore（Cypher 收口）
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        graph_store: Neo4jGraphStore,
    ) -> None:
        self._metadata = metadata
        self._graph = graph_store

    async def project_object(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        object_state: dict[str, Any],
    ) -> None:
        """投影单个对象到 Neo4j 节点。

        仅同步 indexed 属性 + rid + api_name + visibility（C1/C4）。
        object_state 形如 {"rid": ..., "properties": {...}, "object_type_api_name": ...}。
        """
        ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        label = graph_label(ontology_api_name, object_type_api_name)
        rid = str(object_state["rid"])

        # object_state 存 backing_column key（core.property_mapping）；规范化为
        # api_name 以匹配 ot.properties（indexed 属性按 api_name 读取）。已传
        # api_name 的调用方幂等：backing_to_api 只重命名已知的 backing_column 键。
        raw_props = object_state.get("properties", {}) or {}
        flat = {**backing_to_api(ot, raw_props), **{k: v for k, v in object_state.items() if k != "properties"}}

        # 仅 indexed 属性同步到图节点剪枝。全量属性在 Doris，不投影。
        props: dict[str, Any] = {"rid": rid, "api_name": object_type_api_name}
        for p in ot.properties:
            if p.indexed:
                props[p.api_name] = flat.get(p.api_name)
        # D5 (handoff-rid-funnel-closure.md): 强制投影业务 PK 值。不管 indexed
        # 与否, Neo4j 节点总有 pk_value 可反查——存量 rid 回填 (T1.5) 按 PK 从
        # Neo4j 反查 rid, 以及调试/未来按 PK 找节点都依赖此。pk_value 用
        # primary_key 的 api_name 作为节点属性键。
        pk_api = ot.primary_key
        if pk_api:
            props[pk_api] = flat.get(pk_api)
        # visibility 复用现有字段（不新增 security_marking 列，C10 克制）。
        props["visibility"] = flat.get("visibility", "NORMAL")

        # ADR-021 VIRTUAL 联邦投影：VIRTUAL 节点额外写入身份骨架元标记。
        # _virtual/_source_ref 为路径 ③'（远期查询时联邦）留探测点（P6）；
        # _sync_tag 是 cartography watermark，cleanup_stale_virtual 靠它清孤儿（§2.4）。
        # title 存一份供画布渲染节点标题（不查全量也能显示，P2 骨架字段）。
        if object_state.get("_virtual"):
            props["_virtual"] = True
            props["_source_ref"] = object_state.get("_source_ref", "")
            props["_sync_tag"] = object_state.get("_sync_tag", 0)
            title_api = ot.title_property
            if title_api:
                props[title_api] = flat.get(title_api)

        await self._graph.upsert_node(label, rid, props)

    async def project_link(
        self,
        ontology_api_name: str,
        link_type_api_name: str,
        source_object_type_api_name: str,
        source_rid: str,
        target_object_type_api_name: str,
        target_rid: str,
        edge_props: EdgeProps | None = None,
    ) -> None:
        """投影单个关系到 Neo4j 边。

        边仅存 weight + start_time/end_time + visibility（C1 轻量）。
        rel_type / source_label / target_label 由命名规范生成（本体前缀防冲突）。
        """
        rel_type = graph_relationship_type(ontology_api_name, link_type_api_name)
        source_label = graph_label(ontology_api_name, source_object_type_api_name)
        target_label = graph_label(ontology_api_name, target_object_type_api_name)
        await self._graph.upsert_edge(
            rel_type,
            source_label,
            source_rid,
            target_label,
            target_rid,
            edge_props,
        )

    async def delete_object(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        rid: str,
    ) -> None:
        """删除节点及其所有关联边（对象删除时触发）。"""
        label = graph_label(ontology_api_name, object_type_api_name)
        await self._graph.delete_node(label, rid)

    async def delete_link(
        self,
        ontology_api_name: str,
        link_type_api_name: str,
        source_object_type_api_name: str,
        source_rid: str,
        target_object_type_api_name: str,
        target_rid: str,
    ) -> None:
        """删除单条边（UNRELATE 时触发）。"""
        rel_type = graph_relationship_type(ontology_api_name, link_type_api_name)
        source_label = graph_label(ontology_api_name, source_object_type_api_name)
        target_label = graph_label(ontology_api_name, target_object_type_api_name)
        await self._graph.delete_edge(
            rel_type,
            source_label,
            source_rid,
            target_label,
            target_rid,
        )

    async def cleanup_stale_virtual(
        self, ontology_api_name: str, object_type_api_name: str, current_sync_tag: int
    ) -> int:
        """清理本次投影未触及的 VIRTUAL 孤儿节点（ADR-021 §2.4 薄包装）。

        传透到 Neo4jGraphStore.cleanup_stale_virtual。ObjectIndexFunnel 调此，
        不直接访问 GraphProjector._graph（保分层）。
        """
        label = graph_label(ontology_api_name, object_type_api_name)
        return await self._graph.cleanup_stale_virtual(label, current_sync_tag)

    async def project_links_batch(
        self,
        ontology_api_name: str,
        link_type_api_name: str,
        source_object_type_api_name: str,
        target_object_type_api_name: str,
        edges: list[tuple[str, str]],
    ) -> int:
        """批量投影边到 Neo4j（ADR-021 §2.3 薄包装，调 upsert_edges_batch）。

        VIRTUAL 边投影用。edges 是 (source_rid, target_rid) 元组列表。
        边模型轻量（C1，无 edge_props）。

        Returns: 写入的边数。
        """
        if not edges:
            return 0
        rel_type = graph_relationship_type(ontology_api_name, link_type_api_name)
        source_label = graph_label(ontology_api_name, source_object_type_api_name)
        target_label = graph_label(ontology_api_name, target_object_type_api_name)
        return await self._graph.upsert_edges_batch(
            rel_type, source_label, target_label, edges
        )

    async def rebuild_for_object_type(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        object_states: list[dict[str, Any]],
    ) -> int:
        """全量重建某 ObjectType 的图节点投影（rebuild_graph 用，§6.3）。

        从 object_state 全量重投影。返回投影节点数。
        """
        count = 0
        for os in object_states:
            try:
                await self.project_object(ontology_api_name, object_type_api_name, os)
                count += 1
            except Exception as exc:
                # 单条失败不阻塞重建（fail-tolerant，记录日志）。
                _log.warning(
                    "rebuild: failed to project object %s: %s",
                    os.get("rid"),
                    exc,
                )
        _log.info(
            "Rebuilt graph projection for %s.%s: %d nodes",
            ontology_api_name,
            object_type_api_name,
            count,
        )
        return count
