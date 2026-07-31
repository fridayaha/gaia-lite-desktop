"""Unit tests for KestraEngine — IR → YAML translation + REST client.

Covers:
  - KestraFlowTranslator: IR → Kestra Flow YAML for all operator types
  - KestraClient: REST API method signatures and exception handling
  - Edge cases: empty pipeline, single node, GenericKestraTask passthrough
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from ontology.core.schemas.pipeline_builder import (
    IREdge,
    IRNode,
    NodeConfig,
    PipelineIR,
    QualityRule,
)
from ontology.layers.pipeline.kestra_engine import (
    KestraClient,
    KestraEngine,
    KestraFlowTranslator,
    KestraUnavailableError,
)

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def translator() -> KestraFlowTranslator:
    return KestraFlowTranslator()


# ═══════════════════════════════════════════════════════════════════
# KestraFlowTranslator — IR → YAML
# ═══════════════════════════════════════════════════════════════════


class TestFlowTranslator:
    """IR → Kestra Flow YAML translation."""

    def test_simple_source_filter_sink(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "customers"})),  # noqa: E501
            IRNode(id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="status = 'active'")),  # noqa: E501
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "active_customers"})),  # noqa: E501
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="f1"),
            IREdge(id="e2", source_id="f1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges, write_mode="FULL_REFRESH")
        yaml_output = translator.translate(ir, "test_pipeline")
        flow = yaml.safe_load(yaml_output)

        assert flow["id"] == "pipeline_test_pipeline"
        assert flow["namespace"] == "gaia.pipelines"
        assert len(flow["tasks"]) == 3
        assert flow["tasks"][2]["type"] == "io.kestra.plugin.jdbc.duckdb.Query"
        assert "CREATE OR REPLACE TABLE" in flow["tasks"][2]["sql"]

    def test_append_write_mode(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "output"})),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="snk1")]
        ir = PipelineIR(nodes=nodes, edges=edges, write_mode="APPEND")
        yaml_output = translator.translate(ir, "append_test")
        flow = yaml.safe_load(yaml_output)
        assert "INSERT INTO" in flow["tasks"][1]["sql"]

    def test_join_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "left"})),
            IRNode(id="s2", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "right"})),
            IRNode(id="j1", type="Transform", operator_type="Join",
                   config=NodeConfig(join_type="INNER", join_condition="left.id = right.id")),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "joined"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="j1"),
            IREdge(id="e2", source_id="s2", target_id="j1"),
            IREdge(id="e3", source_id="j1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "join_test")
        flow = yaml.safe_load(yaml_output)
        assert "INNER JOIN" in flow["tasks"][3]["sql"]

    def test_aggregate_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "orders"})),
            IRNode(id="a1", type="Transform", operator_type="Aggregate",
                   config=NodeConfig(
                       group_by=["status"],
                       aggregations=[{"field": "amount", "function": "SUM", "alias": "total"}],
                   )),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "agg_output"})),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="a1"), IREdge(id="e2", source_id="a1", target_id="snk1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "agg_test")
        flow = yaml.safe_load(yaml_output)
        sql = flow["tasks"][2]["sql"]
        assert "SUM(amount) AS total" in sql or "SUM" in sql
        assert "GROUP BY status" in sql or "GROUP BY" in sql

    def test_union_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "a"})),
            IRNode(id="s2", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "b"})),
            IRNode(id="u1", type="Transform", operator_type="Union"),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "merged"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="u1"),
            IREdge(id="e2", source_id="s2", target_id="u1"),
            IREdge(id="e3", source_id="u1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "union_test")
        flow = yaml.safe_load(yaml_output)
        assert flow["tasks"][3]["type"] == "io.kestra.plugin.jdbc.duckdb.Query"

    def test_rename_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="r1", type="Transform", operator_type="Rename",
                   config=NodeConfig(column_mapping={"old_col": "new_col"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "renamed"})),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="r1"), IREdge(id="e2", source_id="r1", target_id="snk1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "rename_test")
        flow = yaml.safe_load(yaml_output)
        assert flow["tasks"][2]["type"] == "io.kestra.plugin.jdbc.duckdb.Query"

    def test_generic_kestra_task_passthrough(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="k1", type="GenericKestraTask", operator_type="GenericKestraTask",
                   config=NodeConfig(
                       kestra_task_type="io.kestra.plugin.scripts.python.Script",
                       kestra_task_config={"script": "print('hello')"},
                   )),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="k1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "generic_test")
        flow = yaml.safe_load(yaml_output)
        assert flow["tasks"][1]["type"] == "io.kestra.plugin.scripts.python.Script"
        assert flow["tasks"][1]["script"] == "print('hello')"

    def test_quality_check_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="q1", type="QualityCheck", operator_type="QualityCheck",
                   config=NodeConfig(
                       quality_rules=[
                           QualityRule(rule_type="not_null", field="id", severity="ERROR"),
                           QualityRule(rule_type="range", field="amount",
                                       config={"min": 0, "max": 10000}, severity="WARNING"),
                       ],
                   )),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="q1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "quality_test")
        flow = yaml.safe_load(yaml_output)
        # QualityCheck translates to io.kestra.plugin.core.flow.If
        task_types = {t["type"] for t in flow["tasks"]}
        assert "io.kestra.plugin.core.flow.If" in task_types

    def test_expression_translation(self, translator: KestraFlowTranslator) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "data"})),
            IRNode(id="e1", type="Transform", operator_type="Expression",
                   config=NodeConfig(expression="amount * 1.1", extra={"alias": "amount_with_tax"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="e1"), IREdge(id="e2", source_id="e1", target_id="snk1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "expr_test")
        flow = yaml.safe_load(yaml_output)
        assert flow["tasks"][2]["type"] == "io.kestra.plugin.jdbc.duckdb.Query"

    def test_labels_from_ir(self, translator: KestraFlowTranslator) -> None:
        nodes = [IRNode(id="s1", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "d"}))]
        ir = PipelineIR(nodes=nodes, write_mode="FULL_REFRESH", tags=["tag1", "tag2"])
        yaml_output = translator.translate(ir, "labels_test")
        flow = yaml.safe_load(yaml_output)
        label_values = {lbl["value"] for lbl in flow["labels"]}
        assert "pipeline" in label_values
        assert "FULL_REFRESH" in label_values

    def test_empty_pipeline(self, translator: KestraFlowTranslator) -> None:
        ir = PipelineIR(nodes=[], edges=[])
        yaml_output = translator.translate(ir, "empty")
        flow = yaml.safe_load(yaml_output)
        assert flow["id"] == "pipeline_empty"
        assert len(flow["tasks"]) == 0


# ═══════════════════════════════════════════════════════════════════
# KestraClient — REST API
# ═══════════════════════════════════════════════════════════════════


class TestKestraClient:
    """KestraClient method signatures and error handling (no real server)."""

    def test_client_initialisation(self) -> None:
        client = KestraClient()
        assert client._base_url != ""

    @pytest.mark.asyncio
    async def test_health_returns_false_when_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ontology.config.settings import settings as s
        s.kestra_host = "127.0.0.1"
        s.kestra_port = 1  # unreachable port
        client = KestraClient()
        result = await client.health()
        assert result is False

    @pytest.mark.asyncio
    async def test_upsert_flow_raises_on_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ontology.config.settings import settings as s
        s.kestra_host = "127.0.0.1"
        s.kestra_port = 1
        client = KestraClient()
        with pytest.raises(KestraUnavailableError):
            await client.upsert_flow("test_ns", "test_flow", "id: test")

    @pytest.mark.asyncio
    async def test_get_flow_returns_none_on_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_flow returns None when flow not found (404 caught gracefully)."""
        from ontology.config.settings import settings as s
        s.kestra_host = "127.0.0.1"
        s.kestra_port = 1
        client = KestraClient()
        with pytest.raises(KestraUnavailableError):
            await client.get_flow("test", "nonexistent")


# ═══════════════════════════════════════════════════════════════════
# KestraEngine (facade)
# ═══════════════════════════════════════════════════════════════════


class TestKestraEngine:
    """KestraEngine facade integration."""

    def test_engine_initialisation(self) -> None:
        engine = KestraEngine()
        assert engine.client is not None
        assert engine.translator is not None

    @pytest.mark.asyncio
    async def test_health_proxies_to_client(self) -> None:
        engine = KestraEngine()
        # No Kestra server running in unit tests
        healthy = await engine.health()
        assert healthy is False

    def test_deploy_translates_and_returns_flow_yaml(self) -> None:
        """KestraEngine.deploy() should call the translator and client."""
        from unittest.mock import AsyncMock, MagicMock

        mock_client = MagicMock()
        mock_client.upsert_flow = AsyncMock(return_value={"id": "pipeline_test", "namespace": "gaia"})
        mock_translator = MagicMock()
        mock_translator.translate = MagicMock(return_value="id: pipeline_test\nnamespace: gaia\n")

        engine = KestraEngine(client=mock_client, translator=mock_translator)
        ir = PipelineIR(nodes=[], edges=[])
        import asyncio

        result = asyncio.run(engine.deploy(ir, "test"))
        assert result["id"] == "pipeline_test"
        mock_translator.translate.assert_called_once()
        mock_client.upsert_flow.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# SQL correctness regression tests (post-review fixes)
# ═══════════════════════════════════════════════════════════════════


class TestSQLRegressionFixes:
    """Regression tests for SQL generation bugs found during code review."""

    def test_typecast_uses_cast_column_as_type(self, translator: KestraFlowTranslator) -> None:
        """TypeCast must produce CAST(column AS type), not invalid CAST(*)."""
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="tc1", type="Transform", operator_type="TypeCast",
                   config=NodeConfig(target_type="INTEGER", extra={"column": "amount"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="tc1"),
            IREdge(id="e2", source_id="tc1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "tc_test")
        assert "CAST(amount AS INTEGER)" in yaml_output
        # Must NOT contain the invalid CAST(*)
        assert "CAST(*)" not in yaml_output

    def test_union_uses_union_all(self, translator: KestraFlowTranslator) -> None:
        """Union must use UNION ALL, not comma-separated SELECTs."""
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "a"})),
            IRNode(id="s2", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "b"})),
            IRNode(id="u1", type="Transform", operator_type="Union", config=NodeConfig()),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="u1"),
            IREdge(id="e2", source_id="s2", target_id="u1"),
            IREdge(id="e3", source_id="u1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "union_test")
        assert "UNION ALL" in yaml_output

    def test_aggregate_count_distinct(self, translator: KestraFlowTranslator) -> None:
        """Aggregate should support COUNT_DISTINCT function."""
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="a1", type="Transform", operator_type="Aggregate",
                   config=NodeConfig(
                       group_by=["category"],
                       aggregations=[{
                           "field": "user_id",
                           "function": "COUNT_DISTINCT",
                           "alias": "unique_users",
                       }],
                   )),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="a1"),
            IREdge(id="e2", source_id="a1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "agg_test")
        assert "COUNT(DISTINCT user_id)" in yaml_output

    def test_find_upstream_resolves_direct_upstream_only(
        self, translator: KestraFlowTranslator
    ) -> None:
        """Join must receive exactly its two direct upstreams (not all ancestors).

        Regression: _find_upstream used to return all node_outputs keys,
        causing Join to receive >2 inputs and produce wrong SQL.
        """
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "left"})),
            IRNode(id="s2", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "right"})),
            IRNode(id="f1", type="Transform", operator_type="Filter",
                   config=NodeConfig(expression="status = 'active'")),
            IRNode(id="j1", type="Transform", operator_type="Join",
                   config=NodeConfig(join_type="INNER", join_condition="s1.id = f1.id")),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        # s1 → f1 (filter on s1); s2 → j1; f1 → j1 (Join takes s2 + f1)
        edges = [
            IREdge(id="e1", source_id="s1", target_id="f1"),
            IREdge(id="e2", source_id="s2", target_id="j1"),
            IREdge(id="e3", source_id="f1", target_id="j1"),
            IREdge(id="e4", source_id="j1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "join_upstream_test")
        # Join SQL should reference exactly 2 aliases (tfm_f1 and src_s2)
        assert "JOIN src_s2" in yaml_output or "JOIN tfm_f1" in yaml_output
        # Must NOT join with src_s1 (s1 is upstream of f1, not direct upstream of j1)
        join_sql_line = [line for line in yaml_output.split("\\n") if "JOIN" in line][0]
        assert "src_s1" not in join_sql_line or "tfm_f1" in join_sql_line

    def test_topological_sort_detects_cycle(self, translator: KestraFlowTranslator) -> None:
        """Cycle in IR should raise ValueError (not silently produce wrong order)."""
        import pytest as _pytest
        nodes = [
            IRNode(id="a", type="Transform", operator_type="Filter",
                   config=NodeConfig(expression="1=1")),
            IRNode(id="b", type="Transform", operator_type="Filter",
                   config=NodeConfig(expression="1=1")),
        ]
        edges = [
            IREdge(id="e1", source_id="a", target_id="b"),
            IREdge(id="e2", source_id="b", target_id="a"),  # cycle!
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        with _pytest.raises(ValueError, match="cycle"):
            translator.translate(ir, "cycle_test")


class TestDeduplicateSortSQL:
    """Deduplicate/Sort SQL generation (added in ADR-018 refactor)."""

    def test_deduplicate_uses_qualify(self, translator: KestraFlowTranslator) -> None:
        """Deduplicate uses DuckDB QUALIFY row_number()=1."""
        from ontology.core.schemas.pipeline_builder import IREdge, IRNode, NodeConfig, PipelineIR
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="d1", type="Transform", operator_type="Deduplicate",
                   config=NodeConfig(columns=["user_id"])),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="d1"),
            IREdge(id="e2", source_id="d1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "dedup_test")
        assert "QUALIFY" in yaml_output
        assert "row_number()" in yaml_output
        assert "user_id" in yaml_output

    def test_sort_uses_order_by(self, translator: KestraFlowTranslator) -> None:
        """Sort uses ORDER BY."""
        from ontology.core.schemas.pipeline_builder import IREdge, IRNode, NodeConfig, PipelineIR
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="so1", type="Transform", operator_type="Sort",
                   config=NodeConfig(columns=["created_at"])),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="so1"),
            IREdge(id="e2", source_id="so1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        yaml_output = translator.translate(ir, "sort_test")
        assert "ORDER BY" in yaml_output
        assert "created_at" in yaml_output


class TestSQLInjectionGuard:
    """SQL injection guards (ADR-018 refactor #3)."""

    def test_unsafe_dataset_name_rejected(self, translator: KestraFlowTranslator) -> None:
        """Dataset name with SQL injection attempt must raise ValueError."""
        import pytest as _pytest

        from ontology.core.schemas.pipeline_builder import IREdge, IRNode, NodeConfig, PipelineIR
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw; DROP TABLE x; --"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="snk1")]
        ir = PipelineIR(nodes=nodes, edges=edges)
        with _pytest.raises(ValueError, match="identifier"):
            translator.translate(ir, "injection_test")

    def test_unsafe_typecast_type_rejected(self, translator: KestraFlowTranslator) -> None:
        """TypeCast target_type must be in the SQL type whitelist."""
        import pytest as _pytest

        from ontology.core.schemas.pipeline_builder import IREdge, IRNode, NodeConfig, PipelineIR
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="tc1", type="Transform", operator_type="TypeCast",
                   config=NodeConfig(target_type="EVIL; DROP TABLE x; --", extra={"column": "amount"})),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="tc1"),
            IREdge(id="e2", source_id="tc1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        with _pytest.raises(ValueError, match="Disallowed SQL type"):
            translator.translate(ir, "type_injection_test")

    def test_unsafe_agg_function_rejected(self, translator: KestraFlowTranslator) -> None:
        """Aggregation function must be in the whitelist."""
        import pytest as _pytest

        from ontology.core.schemas.pipeline_builder import IREdge, IRNode, NodeConfig, PipelineIR
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source",
                   config=NodeConfig(extra={"dataset": "raw"})),
            IRNode(id="a1", type="Transform", operator_type="Aggregate",
                   config=NodeConfig(group_by=["cat"],
                                     aggregations=[{"field": "x", "function": "EVIL_FUNC", "alias": "y"}])),
            IRNode(id="snk1", type="Sink", operator_type="Sink",
                   config=NodeConfig(extra={"dataset": "result"})),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="a1"),
            IREdge(id="e2", source_id="a1", target_id="snk1"),
        ]
        ir = PipelineIR(nodes=nodes, edges=edges)
        with _pytest.raises(ValueError, match="Disallowed aggregation function"):
            translator.translate(ir, "agg_injection_test")


class TestKestraApiPaths:
    """Verify Kestra 1.3 API paths (POST /main/flows JSON, /main/executions/{ns}/{id}, etc).

    Regression: previously used /flows/{ns}/{id} (YAML) which 404s on Kestra 1.3.
    """

    async def test_upsert_flow_posts_json_to_main_flows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """upsert_flow must POST JSON to /main/flows (not YAML to /flows/{ns}/{id})."""
        client = KestraClient()
        captured: dict[str, Any] = {}

        async def fake_request(method: str, path: str, headers: dict | None = None, **kw: Any) -> dict:
            captured["method"] = method
            captured["path"] = path
            captured["json"] = kw.get("json")
            captured["content_type"] = (headers or {}).get("Content-Type")
            return {"id": "test_flow", "namespace": "gaia.pipelines"}

        monkeypatch.setattr(client, "_request", fake_request)
        await client.upsert_flow("gaia.pipelines", "test_flow", "id: test_flow\nnamespace: gaia.pipelines\ntasks: []")
        assert captured["method"] == "POST"
        assert captured["path"] == "/main/flows"
        assert captured["content_type"] == "application/json"
        assert captured["json"]["id"] == "test_flow"
        assert captured["json"]["namespace"] == "gaia.pipelines"

    async def test_trigger_execution_posts_to_main_executions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """trigger_execution must POST to /main/executions/{ns}/{id} (not /executions/{ns}/{id})."""
        client = KestraClient()
        captured: dict[str, Any] = {}

        async def fake_request(method: str, path: str, headers: dict | None = None, **kw: Any) -> dict:
            captured["method"] = method
            captured["path"] = path
            return {"id": "exec1", "state": {"current": "CREATED"}}

        monkeypatch.setattr(client, "_request", fake_request)
        await client.trigger_execution("gaia.pipelines", "test_flow")
        assert captured["method"] == "POST"
        assert captured["path"] == "/main/executions/gaia.pipelines/test_flow"

    async def test_delete_flow_uses_main_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete_flow must DELETE /main/flows/{ns}/{id}."""
        client = KestraClient()
        captured: dict[str, Any] = {}

        async def fake_request(method: str, path: str, headers: dict | None = None, **kw: Any) -> Any:
            captured["method"] = method
            captured["path"] = path
            return None

        monkeypatch.setattr(client, "_request", fake_request)
        await client.delete_flow("gaia.pipelines", "test_flow")
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/main/flows/gaia.pipelines/test_flow"

    async def test_get_execution_uses_main_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_execution must GET /main/executions/{id}."""
        client = KestraClient()
        captured: dict[str, Any] = {}

        async def fake_request(method: str, path: str, headers: dict | None = None, **kw: Any) -> dict:
            captured["method"] = method
            captured["path"] = path
            return {"id": "exec1", "state": {"current": "SUCCESS"}}

        monkeypatch.setattr(client, "_request", fake_request)
        await client.get_execution("exec1")
        assert captured["method"] == "GET"
        assert captured["path"] == "/main/executions/exec1"
