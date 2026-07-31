"""Unit tests for SchemaInferenceEngine — Pipeline Builder's compile-time validation.

Covers:
  - Operator registry initialisation (12 core operators)
  - Individual operator validation rules (Filter/Select/Rename/TypeCast/Join/Aggregate/Union)
  - DAG-level validation (topological sort, cycle detection, multi-input)
  - Incremental inference (infer_node_schema)
  - Edge cases (empty pipeline, single node, unknown operator)
"""

from __future__ import annotations

import pytest

from ontology.core.schemas.pipeline_builder import (
    InputContract,
    IREdge,
    IRNode,
    JoinCondition,
    NodeConfig,
    Schema,
    SchemaField,
    SortKey,
)
from ontology.services.schema_inference_engine import (
    OperatorRegistry,
    OperatorSpec,
    SchemaInferenceEngine,
)

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def engine() -> SchemaInferenceEngine:
    return SchemaInferenceEngine()


@pytest.fixture
def empty_schema() -> Schema:
    return Schema(fields=[])


@pytest.fixture
def sample_schema() -> Schema:
    return Schema(
        fields=[
            SchemaField(name="id", data_type="STRING", nullable=False, primary_key=True),
            SchemaField(name="name", data_type="STRING", nullable=False),
            SchemaField(name="status", data_type="STRING", nullable=True),
            SchemaField(name="amount", data_type="DECIMAL", nullable=True),
            SchemaField(name="created_at", data_type="TIMESTAMP", nullable=True),
        ]
    )


# ═══════════════════════════════════════════════════════════════════
# Operator Registry
# ═══════════════════════════════════════════════════════════════════


class TestOperatorRegistry:
    """OperatorRegistry initialisation and lookup."""

    def test_14_core_operators_registered(self, engine: SchemaInferenceEngine) -> None:
        specs = engine.registry.get_all()
        types = {s.type for s in specs}
        expected = {
            "Source",
            "Sink",
            "Filter",
            "Select",
            "Rename",
            "TypeCast",
            "Join",
            "Aggregate",
            "Union",
            "Expression",
            "Deduplicate",
            "Sort",
            "QualityCheck",
            "GenericKestraTask",
        }
        assert types == expected, f"Missing operators: {expected - types}"

    def test_operator_lookup_found(self, engine: SchemaInferenceEngine) -> None:
        spec = engine.registry.get("Filter")
        assert spec is not None
        assert spec.type == "Filter"
        assert spec.category == "transform"

    def test_operator_lookup_not_found(self, engine: SchemaInferenceEngine) -> None:
        spec = engine.registry.get("NonExistent")
        assert spec is None

    def test_list_by_category(self, engine: SchemaInferenceEngine) -> None:
        transforms = engine.registry.list_by_category("transform")
        types = {s.type for s in transforms}
        assert "Filter" in types
        assert "Join" in types
        assert "Aggregate" in types
        assert "Source" not in types

    def test_custom_registry(self) -> None:
        registry = OperatorRegistry()
        assert len(registry.get_all()) == 0  # Not built yet

        spec = OperatorSpec(
            type="CustomOp",
            display_name="Custom",
            description="Test operator",
            category="transform",
            input_contract=InputContract(min_inputs=1, max_inputs=1),
        )
        registry.register(spec)
        assert registry.get("CustomOp") is spec


# ═══════════════════════════════════════════════════════════════════
# DAG-level validation
# ═══════════════════════════════════════════════════════════════════


class TestDAGValidation:
    """Topological sort, cycle detection, DAG-level checks."""

    def test_empty_pipeline(self, engine: SchemaInferenceEngine) -> None:
        result = engine.validate_pipeline(nodes=[], edges=[])
        assert result.valid
        assert len(result.contracts) == 0

    def test_single_source_node(self, engine: SchemaInferenceEngine) -> None:
        nodes = [IRNode(id="s1", type="Source", operator_type="Source")]
        result = engine.validate_pipeline(nodes, [])
        assert result.valid

    def test_source_uses_injected_source_schemas(self, engine: SchemaInferenceEngine) -> None:
        """Source nodes should use pre-fetched dataset schemas when provided.

        Without source_schemas, Source output is empty (registry default).
        With source_schemas, the injected schema flows to downstream nodes
        so column dropdowns populate during editing.
        """
        nodes = [IRNode(id="s1", type="Source", operator_type="Source")]
        # Without injection — empty schema
        result_no_inject = engine.validate_pipeline(nodes, [])
        assert result_no_inject.node_schemas["s1"].fields == []

        # With injection — real schema
        ds_schema = Schema(
            fields=[SchemaField(name="id", data_type="STRING"), SchemaField(name="ts", data_type="TIMESTAMP")]
        )
        result_inject = engine.validate_pipeline(nodes, [], source_schemas={"s1": ds_schema})
        injected = result_inject.node_schemas["s1"]
        assert [f.name for f in injected.fields] == ["id", "ts"]

    def test_source_schema_flows_to_join_downstream(self, engine: SchemaInferenceEngine) -> None:
        """Join config panel reads upstream columns via node_schemas.

        Reproduces the bug where Join showed "请先将两个上游节点连接到本节点"
        even with two connected Source nodes — because Source output schema
        was empty. With source_schemas injected, Join's upstream schemas
        populate and downstream inference works.
        """
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source"),
            IRNode(id="s2", type="Source", operator_type="Source"),
            IRNode(
                id="j1",
                type="Transform",
                operator_type="Join",
                config=NodeConfig(
                    join_type="INNER",
                    join_conditions=[JoinCondition(left_column="id", right_column="id")],
                ),
            ),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="j1"),
            IREdge(id="e2", source_id="s2", target_id="j1"),
        ]
        left = Schema(fields=[SchemaField(name="id", data_type="STRING"), SchemaField(name="a", data_type="STRING")])
        right = Schema(fields=[SchemaField(name="id", data_type="STRING"), SchemaField(name="b", data_type="INTEGER")])
        result = engine.validate_pipeline(nodes, edges, source_schemas={"s1": left, "s2": right})

        # Both Source schemas populated
        assert [f.name for f in result.node_schemas["s1"].fields] == ["id", "a"]
        assert [f.name for f in result.node_schemas["s2"].fields] == ["id", "b"]
        # Join output schema includes columns from both sides (join on id)
        join_fields = {f.name for f in result.node_schemas["j1"].fields}
        assert "a" in join_fields and "b" in join_fields

    def test_source_filter_sink(self, engine: SchemaInferenceEngine) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source"),
            IRNode(
                id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="status = 'active'")
            ),
            IRNode(id="snk1", type="Sink", operator_type="Sink"),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="f1"),
            IREdge(id="e2", source_id="f1", target_id="snk1"),
        ]
        result = engine.validate_pipeline(nodes, edges)
        # Source/Sink have no upstream schemas, so field references can't be validated
        # Warnings from Filter are expected (no upstream fields to check against)
        assert result.valid is True

    def test_cycle_detection(self, engine: SchemaInferenceEngine) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source"),
            IRNode(id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="1=1")),
        ]
        edges = [
            IREdge(id="e1", source_id="s1", target_id="f1"),
            IREdge(id="e2", source_id="f1", target_id="s1"),  # cycle!
        ]
        result = engine.validate_pipeline(nodes, edges)
        assert result.valid is False
        assert any(v.node_id == "__pipeline__" for v in result.contracts)

    def test_unknown_operator(self, engine: SchemaInferenceEngine) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source"),
            IRNode(id="x1", type="Transform", operator_type="NonExistent"),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="x1")]
        result = engine.validate_pipeline(nodes, edges)
        assert result.valid is False
        assert any("NonExistent" in v.message for v in result.contracts)


# ═══════════════════════════════════════════════════════════════════
# Operator validation rules
# ═══════════════════════════════════════════════════════════════════


class TestFilterValidation:
    """Filter: expression validation, field reference checks."""

    def test_filter_with_expression_passes(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="status = 'active'")
        )
        # With upstream schema, Filter should validate field references
        result = engine.infer_node_schema(
            node, [IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)]
        )  # noqa: E501
        assert result == sample_schema  # Filter preserves schema

    def test_filter_without_expression_fails(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(id="f1", type="Transform", operator_type="Filter")
        spec = engine.registry.get("Filter")
        assert spec is not None
        assert spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[])])
        assert any("expression" in v.message.lower() for v in violations)

    def test_filter_references_missing_field(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(
            id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="nonexistent = 1")
        )
        spec = engine.registry.get("Filter")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[SchemaField(name="id", data_type="STRING")])])
        assert any("nonexistent" in v.message for v in violations)


class TestSelectValidation:
    """Select: column projection."""

    def test_select_keeps_only_specified_columns(self, engine: SchemaInferenceEngine) -> None:
        upstream = Schema(
            fields=[
                SchemaField(name="a", data_type="STRING"),
                SchemaField(name="b", data_type="INTEGER"),
                SchemaField(name="c", data_type="DECIMAL"),
            ]
        )
        node = IRNode(id="sel1", type="Transform", operator_type="Select", config=NodeConfig(columns=["a", "c"]))
        result = engine.infer_node_schema(
            node, [IRNode(id="s1", type="Source", operator_type="Source", output_schema=upstream)]
        )  # noqa: E501
        assert len(result.fields) == 2
        assert result.fields[0].name == "a"
        assert result.fields[1].name == "c"

    def test_select_missing_column_error(self, engine: SchemaInferenceEngine) -> None:
        upstream = Schema(fields=[SchemaField(name="a", data_type="STRING")])
        node = IRNode(id="sel1", type="Transform", operator_type="Select", config=NodeConfig(columns=["nonexistent"]))
        spec = engine.registry.get("Select")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [upstream])
        assert any("nonexistent" in v.message for v in violations)

    def test_select_no_columns_is_passthrough(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(id="sel1", type="Transform", operator_type="Select")
        result = engine.infer_node_schema(
            node, [IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)]
        )  # noqa: E501
        assert len(result.fields) == len(sample_schema.fields)


class TestRenameValidation:
    """Rename: column renaming."""

    def test_rename_applies_mapping(self, engine: SchemaInferenceEngine) -> None:
        upstream = Schema(
            fields=[
                SchemaField(name="old_name", data_type="STRING"),
                SchemaField(name="keep", data_type="INTEGER"),
            ]
        )
        node = IRNode(
            id="r1",
            type="Transform",
            operator_type="Rename",
            config=NodeConfig(column_mapping={"old_name": "new_name"}),
        )
        result = engine.infer_node_schema(
            node, [IRNode(id="s1", type="Source", operator_type="Source", output_schema=upstream)]
        )  # noqa: E501
        names = {f.name for f in result.fields}
        assert "new_name" in names
        assert "old_name" not in names
        assert "keep" in names

    def test_rename_nonexistent_column_error(self, engine: SchemaInferenceEngine) -> None:
        upstream = Schema(fields=[SchemaField(name="a", data_type="STRING")])
        node = IRNode(
            id="r1", type="Transform", operator_type="Rename", config=NodeConfig(column_mapping={"nonexistent": "b"})
        )
        spec = engine.registry.get("Rename")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [upstream])
        assert any("nonexistent" in v.message for v in violations)


class TestJoinValidation:
    """Join: multi-input, condition, type checks."""

    def test_join_without_condition_fails(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(id="j1", type="Transform", operator_type="Join", config=NodeConfig(join_type="INNER"))
        spec = engine.registry.get("Join")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[]), Schema(fields=[])])
        assert any("condition" in v.message.lower() for v in violations)

    def test_join_merges_schemas(self, engine: SchemaInferenceEngine) -> None:
        left = Schema(fields=[SchemaField(name="id", data_type="STRING"), SchemaField(name="name", data_type="STRING")])  # noqa: E501
        right = Schema(
            fields=[SchemaField(name="order_id", data_type="STRING"), SchemaField(name="amount", data_type="DECIMAL")]
        )  # noqa: E501
        node = IRNode(
            id="j1",
            type="Transform",
            operator_type="Join",
            config=NodeConfig(join_type="INNER", join_condition="id = order_id"),
        )
        result = engine.infer_node_schema(
            node,
            [
                IRNode(id="s1", type="Source", output_schema=left),
                IRNode(id="s2", type="Source", output_schema=right),
            ],
        )
        assert len(result.fields) == 4
        names = {f.name for f in result.fields}
        assert "id" in names and "name" in names and "order_id" in names and "amount" in names

    def test_join_requires_two_inputs(self, engine: SchemaInferenceEngine) -> None:
        IRNode(
            id="j1", type="Transform", operator_type="Join", config=NodeConfig(join_type="INNER", join_condition="1=1")
        )
        spec = engine.registry.get("Join")
        assert spec is not None
        assert spec.input_contract.min_inputs == 2


class TestAggregateValidation:
    """Aggregate: grouping, aggregation functions."""

    def test_aggregate_reduces_fields(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="a1",
            type="Transform",
            operator_type="Aggregate",
            config=NodeConfig(
                group_by=["status"],
                aggregations=[{"field": "amount", "function": "SUM", "alias": "total"}],
            ),
        )
        result = engine.infer_node_schema(node, [IRNode(id="s1", type="Source", output_schema=sample_schema)])
        names = {f.name for f in result.fields}
        assert "status" in names  # group_by field preserved
        assert "total" in names  # aggregation result added
        assert "id" not in names  # non-group non-aggregate fields removed

    def test_aggregate_missing_group_by_field(self, engine: SchemaInferenceEngine) -> None:
        upstream = Schema(fields=[SchemaField(name="id", data_type="STRING")])
        node = IRNode(id="a1", type="Transform", operator_type="Aggregate", config=NodeConfig(group_by=["nonexistent"]))
        spec = engine.registry.get("Aggregate")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [upstream])
        assert any("nonexistent" in v.message for v in violations)

    def test_aggregate_function_types(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="a1",
            type="Transform",
            operator_type="Aggregate",
            config=NodeConfig(
                group_by=["status"],
                aggregations=[
                    {"field": "amount", "function": "SUM", "alias": "total"},
                    {"field": "id", "function": "COUNT", "alias": "cnt"},
                    {"field": "amount", "function": "AVG", "alias": "avg"},
                ],
            ),
        )
        result = engine.infer_node_schema(node, [IRNode(id="s1", type="Source", output_schema=sample_schema)])
        total = next(f for f in result.fields if f.name == "total")
        cnt = next(f for f in result.fields if f.name == "cnt")
        avg = next(f for f in result.fields if f.name == "avg")
        assert total.data_type == "DECIMAL"
        assert cnt.data_type == "BIGINT"
        assert avg.data_type == "DECIMAL"


class TestUnionValidation:
    """Union: schema merging."""

    def test_union_merges_all_fields(self, engine: SchemaInferenceEngine) -> None:
        left = Schema(fields=[SchemaField(name="a", data_type="STRING")])
        right = Schema(fields=[SchemaField(name="b", data_type="INTEGER")])
        node = IRNode(id="u1", type="Transform", operator_type="Union")
        result = engine.infer_node_schema(
            node,
            [
                IRNode(id="s1", type="Source", output_schema=left),
                IRNode(id="s2", type="Source", output_schema=right),
            ],
        )
        assert len(result.fields) == 2
        names = {f.name for f in result.fields}
        assert names == {"a", "b"}

    def test_union_type_conflict_resolved_to_string(self, engine: SchemaInferenceEngine) -> None:
        s1 = Schema(fields=[SchemaField(name="x", data_type="STRING")])
        s2 = Schema(fields=[SchemaField(name="x", data_type="INTEGER")])
        node = IRNode(id="u1", type="Transform", operator_type="Union")
        result = engine.infer_node_schema(
            node,
            [
                IRNode(id="s1", type="Source", output_schema=s1),
                IRNode(id="s2", type="Source", output_schema=s2),
            ],
        )
        assert result.fields[0].data_type == "STRING"


class TestTypeCastValidation:
    """TypeCast: type conversion."""

    def test_typecast_requires_target_type(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(id="t1", type="Transform", operator_type="TypeCast")
        spec = engine.registry.get("TypeCast")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[])])
        assert any("target_type" in v.message.lower() for v in violations)


class TestQualityCheckValidation:
    """QualityCheck: rule validation."""

    def test_quality_no_rules_warns(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(id="q1", type="QualityCheck", operator_type="QualityCheck")
        spec = engine.registry.get("QualityCheck")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[])])
        assert any(v.level == "WARNING" for v in violations)

    def test_quality_field_not_found_warns(self, engine: SchemaInferenceEngine) -> None:
        from ontology.core.schemas.pipeline_builder import QualityRule

        node = IRNode(
            id="q1",
            type="QualityCheck",
            operator_type="QualityCheck",
            config=NodeConfig(
                quality_rules=[
                    QualityRule(rule_type="not_null", field="nonexistent"),
                ],
            ),
        )
        spec = engine.registry.get("QualityCheck")
        assert spec is not None and spec.validate_config is not None
        violations = spec.validate_config(node.config, [Schema(fields=[SchemaField(name="id", data_type="STRING")])])
        assert any("nonexistent" in v.message for v in violations)


class TestGenericKestraTask:
    """GenericKestraTask: passthrough, no schema inference."""

    def test_generic_task_no_schema_inference(self, engine: SchemaInferenceEngine) -> None:
        node = IRNode(
            id="k1",
            type="GenericKestraTask",
            operator_type="GenericKestraTask",
            config=NodeConfig(kestra_task_type="io.kestra.plugin.scripts.python.Script"),
        )
        result = engine.infer_node_schema(
            node,
            [
                IRNode(
                    id="s1",
                    type="Source",
                    output_schema=Schema(fields=[SchemaField(name="x", data_type="STRING")]),
                )
            ],
        )
        assert len(result.fields) == 0  # GenericKestraTask has no schema inference


# ═══════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Empty pipelines, single nodes, missing configs."""

    def test_no_edges_valid(self, engine: SchemaInferenceEngine) -> None:
        nodes = [IRNode(id="s1", type="Source", operator_type="Source")]
        result = engine.validate_pipeline(nodes)
        assert result.valid

    def test_many_nodes_no_cycle(self, engine: SchemaInferenceEngine) -> None:
        nodes = [
            IRNode(
                id=f"n{i}",
                type="Source" if i == 0 else "Transform",
                operator_type="Source" if i == 0 else "Filter",
                config=NodeConfig(expression="1=1") if i > 0 else NodeConfig(),
            )
            for i in range(10)
        ]
        edges = [IREdge(id=f"e{i}", source_id=f"n{i}", target_id=f"n{i + 1}") for i in range(9)]
        result = engine.validate_pipeline(nodes, edges)
        # Source nodes have no upstream, so Filter warnings about field refs are expected
        assert result.valid is True

    def test_output_schema_in_final_response(self, engine: SchemaInferenceEngine) -> None:
        nodes = [
            IRNode(id="s1", type="Source", operator_type="Source"),
            IRNode(id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="1=1")),
        ]
        edges = [IREdge(id="e1", source_id="s1", target_id="f1")]
        result = engine.validate_pipeline(nodes, edges)
        assert result.inferred_schema is not None


# ═══════════════════════════════════════════════════════════════════
# Regression tests for review fixes
# ═══════════════════════════════════════════════════════════════════


class TestTypeCastInferenceFix:
    """TypeCast must actually change the target column's type (regression)."""

    def test_typecast_changes_column_type(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        """TypeCast with column + target_type must update the field's data_type."""
        node = IRNode(
            id="tc1",
            type="Transform",
            operator_type="TypeCast",
            config=NodeConfig(target_type="INTEGER", extra={"column": "amount"}),
        )
        upstream = IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)
        result = engine.infer_node_schema(node, [upstream])
        # Find the 'amount' field — should be INTEGER now
        amount_field = next((f for f in result.fields if f.name == "amount"), None)
        assert amount_field is not None, "amount field should exist in output"
        assert amount_field.data_type == "INTEGER", (
            f"TypeCast should change amount to INTEGER, got {amount_field.data_type}"
        )

    def test_typecast_without_column_passes_through(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        """TypeCast without column should pass through unchanged (with WARNING)."""
        node = IRNode(
            id="tc2",
            type="Transform",
            operator_type="TypeCast",
            config=NodeConfig(target_type="INTEGER"),  # no column
        )
        upstream = IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)
        result = engine.infer_node_schema(node, [upstream])
        # All fields should retain original types
        for f in result.fields:
            original = next((of for of in sample_schema.fields if of.name == f.name), None)
            if original:
                assert f.data_type == original.data_type


class TestFinalSchemaSinkDetection:
    """Final schema should be the Sink's output, not topo_order[-1] (regression)."""

    def test_final_schema_is_from_sink_not_last_topo(
        self, engine: SchemaInferenceEngine, sample_schema: Schema
    ) -> None:
        """final_schema should come from the Sink (terminal) node.

        Regression: validate_pipeline used to return topo_order[-1]'s schema.
        In a chain Source→Filter→Sink, topo_order[-1] is Sink (correct),
        but in a diamond it could be wrong. Here we verify the Sink is chosen.
        """
        # Mock the Source's infer_output_schema to return sample_schema
        # (in production this comes from Dataset metadata)
        source_spec = engine.registry.get("Source")
        original_source_infer = source_spec.infer_output_schema
        source_spec.infer_output_schema = lambda schemas, config: sample_schema
        try:
            source = IRNode(
                id="src",
                type="Source",
                operator_type="Source",
                config=NodeConfig(extra={"dataset": "raw"}),
            )
            filter_node = IRNode(
                id="f1",
                type="Transform",
                operator_type="Filter",
                config=NodeConfig(expression="status = 'active'"),
            )
            sink = IRNode(
                id="snk",
                type="Sink",
                operator_type="Sink",
                config=NodeConfig(extra={"dataset": "out"}),
            )
            nodes = [source, filter_node, sink]
            edges = [
                IREdge(id="e1", source_id="src", target_id="f1"),
                IREdge(id="e2", source_id="f1", target_id="snk"),
            ]
            result = engine.validate_pipeline(nodes=nodes, edges=edges)
            assert result.inferred_schema is not None, "final_schema should not be None"
            field_names = {f.name for f in result.inferred_schema.fields}
            assert "id" in field_names, "id field should be in final schema (from Sink)"
        finally:
            source_spec.infer_output_schema = original_source_infer


class TestOperatorSchemaRule:
    """Each registered operator should have a non-empty output_schema_rule (for catalog)."""

    def test_all_core_operators_have_schema_rule(self, engine: SchemaInferenceEngine) -> None:
        """output_schema_rule field should be populated for all core operators."""
        for spec in engine.registry.get_all():
            assert spec.output_schema_rule, f"Operator '{spec.type}' has empty output_schema_rule"


class TestDeduplicateSortInference:
    """Deduplicate/Sort preserve schema (rows change, fields unchanged)."""

    def test_deduplicate_preserves_schema(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="dedup1",
            type="Transform",
            operator_type="Deduplicate",
            config=NodeConfig(columns=["id"]),
        )
        upstream = IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)
        result = engine.infer_node_schema(node, [upstream])
        assert {f.name for f in result.fields} == {f.name for f in sample_schema.fields}

    def test_sort_preserves_schema(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="sort1",
            type="Transform",
            operator_type="Sort",
            config=NodeConfig(columns=["created_at"]),
        )
        upstream = IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)
        result = engine.infer_node_schema(node, [upstream])
        assert {f.name for f in result.fields} == {f.name for f in sample_schema.fields}

    def test_deduplicate_unknown_key_error(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        node = IRNode(
            id="dedup1",
            type="Transform",
            operator_type="Deduplicate",
            config=NodeConfig(columns=["nonexistent"]),
        )
        upstream = IRNode(id="s1", type="Source", operator_type="Source", output_schema=sample_schema)
        result = engine.validate_pipeline(
            nodes=[upstream, node],
            edges=[{"source_id": "s1", "target_id": "dedup1"}],
        )
        assert any("nonexistent" in v.message and v.level == "ERROR" for v in result.contracts)


class TestNodeSchemasExposure:
    """validate_pipeline 必须返回每个节点的输出 Schema（供前端列下拉）。"""

    def test_node_schemas_contains_all_nodes(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        """node_schemas 应包含 DAG 中每个节点的输出 Schema。"""
        source_spec = engine.registry.get("Source")
        original = source_spec.infer_output_schema
        source_spec.infer_output_schema = lambda schemas, config: sample_schema
        try:
            source = IRNode(
                id="src", type="Source", operator_type="Source", config=NodeConfig(extra={"dataset": "raw"})
            )
            flt = IRNode(
                id="f1", type="Transform", operator_type="Filter", config=NodeConfig(expression="status = 'active'")
            )
            sink = IRNode(id="snk", type="Sink", operator_type="Sink", config=NodeConfig(extra={"dataset": "out"}))
            result = engine.validate_pipeline(
                nodes=[source, flt, sink],
                edges=[
                    IREdge(id="e1", source_id="src", target_id="f1"),
                    IREdge(id="e2", source_id="f1", target_id="snk"),
                ],
            )
            assert set(result.node_schemas.keys()) == {"src", "f1", "snk"}
            # Source 的 schema 应该是 sample_schema
            src_names = {f.name for f in result.node_schemas["src"].fields}
            assert "id" in src_names
        finally:
            source_spec.infer_output_schema = original

    def test_structured_join_conditions_validated(self, engine: SchemaInferenceEngine) -> None:
        """结构化 join_conditions 的列名应被校验是否在上游 schema 中。"""
        left_schema = Schema(fields=[SchemaField(name="id", data_type="STRING")])
        right_schema = Schema(fields=[SchemaField(name="customer_id", data_type="STRING")])
        source_spec = engine.registry.get("Source")
        original = source_spec.infer_output_schema
        source_spec.infer_output_schema = lambda schemas, config: schemas[0] if schemas else Schema(fields=[])
        try:
            left = IRNode(
                id="sl",
                type="Source",
                operator_type="Source",
                output_schema=left_schema,
                config=NodeConfig(extra={"dataset": "l"}),
            )
            right = IRNode(
                id="sr",
                type="Source",
                operator_type="Source",
                output_schema=right_schema,
                config=NodeConfig(extra={"dataset": "r"}),
            )
            join = IRNode(
                id="j1",
                type="Transform",
                operator_type="Join",
                config=NodeConfig(
                    join_type="INNER",
                    join_conditions=[JoinCondition(left_column="id", right_column="nonexistent")],
                ),
            )
            result = engine.validate_pipeline(
                nodes=[left, right, join],
                edges=[
                    IREdge(id="e1", source_id="sl", target_id="j1"),
                    IREdge(id="e2", source_id="sr", target_id="j1"),
                ],
            )
            assert any("nonexistent" in v.message and v.level == "ERROR" for v in result.contracts)
        finally:
            source_spec.infer_output_schema = original

    def test_structured_sort_keys_validated(self, engine: SchemaInferenceEngine, sample_schema: Schema) -> None:
        """结构化 sort_keys 的列名应被校验。"""
        source_spec = engine.registry.get("Source")
        original = source_spec.infer_output_schema
        source_spec.infer_output_schema = lambda schemas, config: sample_schema
        try:
            source = IRNode(
                id="src",
                type="Source",
                operator_type="Source",
                output_schema=sample_schema,
                config=NodeConfig(extra={"dataset": "raw"}),
            )
            sort_node = IRNode(
                id="so1",
                type="Transform",
                operator_type="Sort",
                config=NodeConfig(sort_keys=[SortKey(column="nonexistent", direction="ASC")]),
            )
            result = engine.validate_pipeline(
                nodes=[source, sort_node],
                edges=[IREdge(id="e1", source_id="src", target_id="so1")],
            )
            assert any("nonexistent" in v.message and v.level == "ERROR" for v in result.contracts)
        finally:
            source_spec.infer_output_schema = original
