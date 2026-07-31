"""Unit tests for SemanticRecaller engine B (vector recall) + VectorIndexer.

Engine B tests mock the vector_search callable (no Doris dependency).
VectorIndexer tests mock DorisIndexStore. Both validate the engine-A/B
merge logic and the indexing pipeline without a live Doris ANN table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import numpy as np

from ontology.core.schemas.ontology import ObjectType, PropertyDef
from ontology.core.schemas.textql import ObjectRef, QueryIR
from ontology.services.textql.semantic_recall import SemanticRecaller
from ontology.services.textql.vector_indexer import VectorIndexer

_NOW = datetime.now(UTC)


def _prop(api: str, display: str, desc: str = "") -> PropertyDef:
    return PropertyDef(
        id=f"p-{api}",
        object_type_id="ot-1",
        api_name=api,
        display_name=display,
        description=desc,
        data_type="STRING",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _ot(api: str, display: str, props: list[PropertyDef] | None = None) -> ObjectType:
    return ObjectType(
        id=f"ot-{api}",
        ontology_id="ont-1",
        api_name=api,
        display_name=display,
        description=f"{display}对象",
        primary_key="id",
        title_property="name",
        storage_type="MANAGED",
        properties=props or [],
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestEngineBVectorRecall:
    """Engine B fires when engine A confidence < threshold; merges vector hits."""

    @staticmethod
    def _make_vector_search(results: list[dict]) -> AsyncMock:
        async def _search(text: str, top_k: int = 10) -> list[dict]:
            return results

        return _search

    async def test_vector_recall_fires_on_low_confidence(self) -> None:
        """Noun engine A can't match → engine B surfaces vector candidates."""
        ots = [_ot("Truck", "货运车辆"), _ot("Order", "订单")]
        # "大车" doesn't exact-match any OT display_name → engine A returns [].
        # Engine B vector search returns Truck (similarity 0.63).
        vector_results = [
            {
                "element_type": "OBJECT_TYPE",
                "element_api_name": "Truck",
                "display_name": "货运车辆",
                "description": "",
                "similarity": 0.63,
            },
        ]
        recaller = SemanticRecaller(ots, vector_search=self._make_vector_search(vector_results))
        ir = QueryIR(raw_query="查大车", intent_type="query", objects=[ObjectRef(name="大车")])
        result = await recaller.recall(ir)
        # Engine B surfaced Truck.
        assert any(c.api_name == "Truck" for c in result.object_types)
        truck_cand = next(c for c in result.object_types if c.api_name == "Truck")
        assert truck_cand.source == "vector"
        assert truck_cand.confidence == 0.63

    async def test_engine_b_skipped_when_engine_a_high_confidence(self) -> None:
        """Engine A exact match (confidence 1.0) → engine B not called."""
        ots = [_ot("Order", "订单")]
        vector_called = False

        async def _search(text: str, top_k: int = 10) -> list[dict]:
            nonlocal vector_called
            vector_called = True
            return []

        recaller = SemanticRecaller(ots, vector_search=_search)
        ir = QueryIR(raw_query="查订单", intent_type="query", objects=[ObjectRef(name="订单")])
        result = await recaller.recall(ir)
        assert vector_called is False  # engine A was enough
        assert result.object_types[0].confidence == 1.0
        assert result.object_types[0].source == "exact"

    async def test_vector_below_threshold_filtered(self) -> None:
        """Vector hits below _VECTOR_THRESHOLD (0.5) are discarded."""
        ots = [_ot("Truck", "货运车辆")]
        vector_results = [
            {
                "element_type": "OBJECT_TYPE",
                "element_api_name": "Truck",
                "display_name": "货运车辆",
                "description": "",
                "similarity": 0.3,
            },
        ]
        recaller = SemanticRecaller(ots, vector_search=self._make_vector_search(vector_results))
        ir = QueryIR(raw_query="查大车", intent_type="query", objects=[ObjectRef(name="大车")])
        result = await recaller.recall(ir)
        assert len(result.object_types) == 0  # weak hit filtered

    async def test_vector_only_object_types_returned(self) -> None:
        """Non-OBJECT_TYPE vector rows (PROPERTY/LINK_TYPE) are ignored by engine B."""
        ots = [_ot("Truck", "货运车辆")]
        vector_results = [
            {
                "element_type": "PROPERTY",
                "element_api_name": "amount",
                "display_name": "金额",
                "description": "",
                "similarity": 0.9,
            },
            {
                "element_type": "OBJECT_TYPE",
                "element_api_name": "Truck",
                "display_name": "货运车辆",
                "description": "",
                "similarity": 0.6,
            },
        ]
        recaller = SemanticRecaller(ots, vector_search=self._make_vector_search(vector_results))
        ir = QueryIR(raw_query="查大车", intent_type="query", objects=[ObjectRef(name="大车")])
        result = await recaller.recall(ir)
        assert len(result.object_types) == 1
        assert result.object_types[0].api_name == "Truck"

    async def test_vector_failure_non_fatal(self) -> None:
        """Engine B exception is logged + swallowed; engine A result returned."""

        async def _failing_search(text: str, top_k: int = 10) -> list[dict]:
            raise RuntimeError("Doris down")

        ots = [_ot("Order", "订单")]
        recaller = SemanticRecaller(ots, vector_search=_failing_search)
        ir = QueryIR(raw_query="查大车", intent_type="query", objects=[ObjectRef(name="大车")])
        # Should not raise — engine B failure is non-fatal.
        result = await recaller.recall(ir)
        assert len(result.object_types) == 0

    async def test_no_vector_search_engine_b_disabled(self) -> None:
        """vector_search=None → pure engine A (Phase 1 mode)."""
        ots = [_ot("Order", "订单")]
        recaller = SemanticRecaller(ots, vector_search=None)
        ir = QueryIR(raw_query="查订单", intent_type="query", objects=[ObjectRef(name="订单")])
        result = await recaller.recall(ir)
        assert result.object_types[0].source == "exact"


class TestVectorIndexer:
    """VectorIndexer embeds ontology elements + upserts to Doris (mocked)."""

    async def test_index_ontology_builds_rows(self) -> None:
        """index_ontology embeds OT + Property + Link elements and upserts."""
        from ontology.core.schemas.ontology import LinkTypeDef

        ot = _ot("Order", "订单", [_prop("amount", "金额", "订单金额")])
        lt = LinkTypeDef(
            id="lt-1",
            ontology_id="ont-1",
            api_name="hasCustomer",
            display_name="属于客户",
            description="订单关联客户",
            source_object_type_id="ot-Order",
            target_object_type_id="ot-Customer",
            cardinality="MANY",
            direction="OUTGOING",
            created_at=_NOW,
            updated_at=_NOW,
        )
        # Mock index: semantic_table_exists → False (trigger create), upsert captures rows.
        mock_index = AsyncMock()
        mock_index.semantic_table_exists = AsyncMock(return_value=False)
        mock_index.create_semantic_table = AsyncMock()
        mock_index.upsert_semantic_rows = AsyncMock()

        # Mock embedding provider: return deterministic vectors.
        class FakeProvider:
            dim = 384

            def embed(self, texts: list[str]) -> np.ndarray:
                return np.ones((len(texts), 384), dtype=np.float32)

        indexer = VectorIndexer(mock_index, provider=FakeProvider())
        n = await indexer.index_ontology("Airline", [ot], [lt])
        # 1 OT + 1 Property + 1 Link = 3 rows.
        assert n == 3
        mock_index.create_semantic_table.assert_awaited_once()
        mock_index.upsert_semantic_rows.assert_awaited_once()
        rows = mock_index.upsert_semantic_rows.call_args[0][0]
        assert len(rows) == 3
        assert {r["element_type"] for r in rows} == {"OBJECT_TYPE", "PROPERTY", "LINK_TYPE"}
        assert all(len(r["embedding"]) == 384 for r in rows)

    async def test_index_ontology_empty(self) -> None:
        """Empty ontology → no rows, table still ensured."""
        mock_index = AsyncMock()
        mock_index.semantic_table_exists = AsyncMock(return_value=True)
        mock_index.upsert_semantic_rows = AsyncMock()

        class FakeProvider:
            dim = 384

            def embed(self, texts: list[str]) -> np.ndarray:
                return np.zeros((0, 384), dtype=np.float32)

        indexer = VectorIndexer(mock_index, provider=FakeProvider())
        n = await indexer.index_ontology("Airline", [], [])
        assert n == 0
        mock_index.upsert_semantic_rows.assert_awaited_once()
