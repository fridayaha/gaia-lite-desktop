"""End-to-end TextQL pipeline integration test (Phase 1).

Validates the full Step 1 → Step 4 path-B chain against the live Airline
ontology + Doris:
  1. parse_intent (LLM) → QueryIR
  2. SemanticRecaller (engine A) → RecallResult (api_name backfill)
  3. OntologySqlCompiler → physical Doris SQL
  4. ObjectQueryService.execute_compiled_sql → real rows

Run against a live backend (Doris + PG + the Airline ontology with synced
data). Skips gracefully if the environment isn't available.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

from ontology.config.container import container
from ontology.services.textql.intent_parser import parse_intent
from ontology.services.textql.schema_provider import MetaStoreSchemaProvider
from ontology.services.textql.semantic_recall import SemanticRecaller
from ontology.services.textql.sql_compiler import OntologySqlCompiler

pytestmark = pytest.mark.skipif(
    os.getenv("GAIA_TEXTQL_E2E") != "1",
    reason="Set GAIA_TEXTQL_E2E=1 to run the live TextQL E2E test (needs Doris + Airline data)",
)


@pytest_asyncio.fixture
async def airline_schema() -> MetaStoreSchemaProvider:
    provider = MetaStoreSchemaProvider(container.metadata)
    await provider.load("Airline")
    return provider


@pytest_asyncio.fixture
async def airline_object_types() -> list:
    async with container.metadata_session() as meta:
        return await meta.list_object_types("Airline")


class TestTextQLEndToEnd:
    """Full pipeline: NL → IR → recall → compile → execute."""

    async def test_simple_filter_query(self, airline_schema, airline_object_types) -> None:
        """'查询状态为 Operational 的飞机机型' → single-table filter."""
        ir = await parse_intent("查询状态为Operational的飞机机型和数量")
        assert ir.intent_type in ("query", "aggregate", "count")

        recaller = SemanticRecaller(airline_object_types)
        recall = recaller.recall(ir)
        assert len(recall.object_types) > 0, "recall should find Aircraft"
        assert any(c.api_name == "Aircraft" for c in recall.object_types)

        # LLM-generated logical SQL (simulating Step 4 path B).
        sql = "SELECT aircraftId, model, status FROM Aircraft WHERE status = 'Operational' LIMIT 5"
        compiler = OntologySqlCompiler(airline_schema)
        doris_sql, params = compiler.compile(sql, "doris")
        assert "idx_airline__aircraft" in doris_sql
        assert "Operational" in params

        svc = container.object_query_service
        rows = await svc.execute_compiled_sql("Airline", sql, compiler)
        assert len(rows) > 0
        assert all(r.get("status") == "Operational" for r in rows)
        # api_name mapping (not physical column)
        assert "aircraftId" in rows[0]

    async def test_aggregation_query(self, airline_schema) -> None:
        """'统计各状态飞机数量' → aggregation."""
        ir = await parse_intent("统计各状态的飞机数量")
        assert ir.intent_type in ("aggregate", "count")

        sql = "SELECT status, COUNT(*) AS cnt FROM Aircraft GROUP BY status"
        compiler = OntologySqlCompiler(airline_schema)
        doris_sql, _ = compiler.compile(sql, "doris")
        assert "GROUP BY" in doris_sql
        assert "COUNT(*)" in doris_sql

        svc = container.object_query_service
        rows = await svc.execute_compiled_sql("Airline", sql, compiler)
        assert len(rows) > 0
        assert all("cnt" in r for r in rows)

    async def test_guardrail_invalid_column(self, airline_schema) -> None:
        """Guardrail: unknown property rejected."""
        from ontology.core.exceptions import OntologyError

        compiler = OntologySqlCompiler(airline_schema)
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT color FROM Aircraft", "doris")
        assert exc.value.code == "INVALID_COLUMN"

    async def test_guardrail_invalid_table(self, airline_schema) -> None:
        """Guardrail: unknown ObjectType rejected."""
        from ontology.core.exceptions import OntologyError

        compiler = OntologySqlCompiler(airline_schema)
        with pytest.raises(OntologyError) as exc:
            compiler.compile("SELECT * FROM Nonexistent", "doris")
        assert exc.value.code == "INVALID_TABLE"

    async def test_guardrail_update_rejected(self, airline_schema) -> None:
        """Scope: UPDATE rejected (route to Action tools)."""
        from ontology.core.exceptions import OntologyError

        compiler = OntologySqlCompiler(airline_schema)
        with pytest.raises(OntologyError) as exc:
            compiler.compile("UPDATE Aircraft SET status = 'Retired' WHERE aircraftId = 1", "doris")
        assert exc.value.code == "UNSUPPORTED_SQL"


if __name__ == "__main__":
    # Manual run: GAIA_TEXTQL_E2E=1 python -m pytest scripts/verify_textql_e2e.py -s
    asyncio.run(lambda: None)
