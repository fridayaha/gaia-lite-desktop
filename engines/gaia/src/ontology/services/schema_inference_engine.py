"""Schema Inference Engine — Pipeline Builder's compile-time validation core.

Design (ADR-018 D3):
  - Compile-time schema inference without touching real data
  - Millisecond-level incremental recomputation (dirty downstream only)
  - Per-operator InputContract → internal inference → OutputContract
  - ERROR/WARNING/INFO three-level validation

Kestra has no schema-inference capability; this is a Gaia core moat.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ontology.core.schemas.pipeline_builder import (
    ContractViolation,
    InputContract,
    IREdge,
    IRNode,
    NodeConfig,
    Schema,
    SchemaField,
    ValidationResponse,
)

# ═══════════════════════════════════════════════════════════════════
# Operator Registration
# ═══════════════════════════════════════════════════════════════════


@dataclass
class OperatorSpec:
    """Schema-inference specification for a single operator type.

    Every transform operator registers one OperatorSpec so the engine
    can validate + infer schemas generically via the registry, without
    hard-coded switch/case branches.
    """

    type: str
    display_name: str
    description: str
    category: Literal["source", "transform", "sink", "quality", "kestra"]
    input_ports: int = 1
    output_ports: int = 1
    # Human-readable description of how output schema is derived (for /operators catalog)
    output_schema_rule: str = ""

    # Contract: input constraints (field existence, type compatibility, cardinality)
    input_contract: InputContract = field(default_factory=InputContract)

    # Inference: (input_schemas, config) → output_schema
    infer_output_schema: Callable[[list[Schema], NodeConfig], Schema] | None = None

    # Validation: (config, input_schemas) → list of violations
    validate_config: Callable[[NodeConfig, list[Schema]], list[ContractViolation]] | None = None

    # Config JSON Schema for UI form rendering
    config_schema: dict[str, Any] = field(default_factory=dict)


class OperatorRegistry:
    """Registry of all operator specs — single source of truth.

    Instead of switch/case on node type, all operators register here.
    Add a new operator = register() a new OperatorSpec.
    """

    def __init__(self) -> None:
        self._operators: dict[str, OperatorSpec] = {}
        self._built = False

    def register(self, spec: OperatorSpec) -> None:
        """Register an operator spec."""
        self._operators[spec.type] = spec

    def get(self, type_name: str) -> OperatorSpec | None:
        """Get an operator spec by type name."""
        return self._operators.get(type_name)

    def get_all(self) -> list[OperatorSpec]:
        """List all registered operators."""
        return list(self._operators.values())

    def list_by_category(self, category: str) -> list[OperatorSpec]:
        """List operators by category."""
        return [spec for spec in self._operators.values() if spec.category == category]

    def ensure_built(self) -> None:
        """Idempotent — register core operators on first call."""
        if self._built:
            return
        _register_core_operators(self)
        self._built = True


# ═══════════════════════════════════════════════════════════════════
# Inference Engine
# ═══════════════════════════════════════════════════════════════════


class SchemaInferenceEngine:
    """Core inference engine: node-level + DAG-level schema validation.

    Usage:
        engine = SchemaInferenceEngine()
        engine.registry.ensure_built()
        result = engine.validate_pipeline(nodes, edges)
    """

    def __init__(self, registry: OperatorRegistry | None = None) -> None:
        self.registry = registry or OperatorRegistry()
        self.registry.ensure_built()

    # ── Public API ──

    def validate_pipeline(
        self,
        nodes: list[IRNode],
        edges: list[IREdge | dict[str, str]] | None = None,
        sink_dataset_api_name: str | None = None,
        source_schemas: dict[str, Schema] | None = None,
    ) -> ValidationResponse:
        """Top-level validation: validate all nodes + topology.

        Returns collected violations + final inferred output schema.
        Does NOT mutate the input IR; returns a ValidationResponse.

        ``source_schemas`` (optional) maps Source node_id → pre-fetched dataset
        schema. When present, Source nodes use this schema instead of the
        registry's default (empty) inference, so downstream nodes (Join, etc.)
        can populate column dropdowns during editing.
        """
        source_schemas = source_schemas or {}
        violations: list[ContractViolation] = []
        # Build adjacency: node_id → list of upstream node_ids
        adjacency = self._build_adjacency(nodes, edges or [])

        # Topological sort
        try:
            topo_order = self._topological_sort(nodes, adjacency)
        except ValueError as e:
            violations.append(ContractViolation(node_id="__pipeline__", valid=False, level="ERROR", message=str(e)))
            return ValidationResponse(valid=False, contracts=violations)

        # Per-node inference (topological order — upstream schemas are ready)
        node_schemas: dict[str, Schema] = {}
        for node_id in topo_order:
            node = next((n for n in nodes if n.id == node_id), None)
            if node is None:
                continue
            upstream_ids = adjacency.get(node_id, [])
            upstream_schemas = [node_schemas[uid] for uid in upstream_ids if uid in node_schemas]

            operator_key = node.operator_type or node.type
            spec = self.registry.get(operator_key)
            if spec is None:
                violations.append(
                    ContractViolation(
                        node_id=node_id,
                        valid=False,
                        level="ERROR",
                        message=f"Unknown operator type: {operator_key}",
                    )
                )
                continue

            # Step 1: validate config
            if spec.validate_config:
                config_violations = spec.validate_config(node.config, upstream_schemas)
                for v in config_violations:
                    v.node_id = node_id
                violations.extend(config_violations)

            # Step 2: infer output schema
            inferred: Schema | None = None
            # Source nodes: prefer pre-fetched dataset schema (resolved by the
            # service layer from Iceberg/Gravitino) over the registry's empty
            # default. This lets downstream column dropdowns work during editing.
            if operator_key == "Source" and node_id in source_schemas:
                inferred = source_schemas[node_id]
            elif spec.infer_output_schema:
                try:
                    inferred = spec.infer_output_schema(upstream_schemas, node.config)
                except Exception as e:
                    violations.append(
                        ContractViolation(
                            node_id=node_id,
                            valid=False,
                            level="ERROR",
                            message=f"Schema inference error: {e}",
                        )
                    )

            # Step 3: input contract validation
            if len(upstream_schemas) < spec.input_contract.min_inputs:
                violations.append(
                    ContractViolation(
                        node_id=node_id,
                        valid=False,
                        level="ERROR",
                        message=(
                            f"Node requires at least {spec.input_contract.min_inputs} "
                            f"inputs, got {len(upstream_schemas)}"
                        ),
                    )
                )
            if spec.input_contract.max_inputs > 0 and len(upstream_schemas) > spec.input_contract.max_inputs:
                violations.append(
                    ContractViolation(
                        node_id=node_id,
                        valid=False,
                        level="WARNING",
                        message=(
                            f"Node expects at most {spec.input_contract.max_inputs} inputs, got {len(upstream_schemas)}"
                        ),
                    )
                )

            # Required field check
            for req_field in spec.input_contract.required_fields:
                found = any(f.name == req_field for s in upstream_schemas for f in s.fields)
                if not found:
                    violations.append(
                        ContractViolation(
                            node_id=node_id,
                            valid=False,
                            level="ERROR",
                            message=f"Required field '{req_field}' not found in upstream schema",
                        )
                    )

            # Field type compatibility check
            for field_name, allowed_types in spec.input_contract.field_type_requirements.items():
                for s in upstream_schemas:
                    for f in s.fields:
                        if f.name == field_name and f.data_type not in allowed_types:
                            violations.append(
                                ContractViolation(
                                    node_id=node_id,
                                    valid=False,
                                    level="WARNING",
                                    message=(
                                        f"Field '{field_name}' has type "
                                        f"'{f.data_type}', expected one of "
                                        f"{allowed_types}"
                                    ),
                                )
                            )

            node_schemas[node_id] = inferred or Schema(fields=[])

        # Determine overall validity
        has_errors = any(v.level == "ERROR" for v in violations)
        # Final schema = output schema of the terminal node(s) (out-degree 0).
        # topo_order[-1] is NOT reliable as the terminal — in a diamond graph
        # the last topo node may have outgoing edges. Find true sinks instead.
        final_schema = self._find_terminal_schema(nodes, adjacency, node_schemas)

        return ValidationResponse(
            valid=not has_errors,
            inferred_schema=final_schema,
            contracts=violations,
            node_schemas=node_schemas,
        )

    def infer_node_schema(self, node: IRNode, upstream_nodes: list[IRNode]) -> Schema:
        """Infer schema for a single node given its upstreams.

        Used by the incremental path: when a single node changes, only
        it and its downstream are recomputed.
        """
        operator_key = node.operator_type or node.type
        spec = self.registry.get(operator_key)
        if spec is None or spec.infer_output_schema is None:
            return Schema(fields=[])

        upstream_schemas = [u.output_schema or Schema(fields=[]) for u in upstream_nodes]
        try:
            return spec.infer_output_schema(upstream_schemas, node.config)
        except Exception:
            return Schema(fields=[])

    # ── Private helpers ──

    def _find_terminal_schema(
        self,
        nodes: list[IRNode],
        adjacency: dict[str, list[str]],
        node_schemas: dict[str, Schema],
    ) -> Schema | None:
        """Find the schema of the terminal node (out-degree 0).

        Prefers Sink nodes; falls back to any node with no downstream.
        Returns None if there are multiple terminals (ambiguous) or none.
        """
        # Build out-degree map
        out_degree: dict[str, int] = {n.id: 0 for n in nodes}
        for target_id, upstreams in adjacency.items():
            for src in upstreams:
                out_degree[src] = out_degree.get(src, 0) + 1

        terminals = [n.id for n in nodes if out_degree.get(n.id, 0) == 0]
        if not terminals:
            return None
        # Prefer Sink nodes among terminals
        sink_terminals = [nid for nid in terminals if any(n.id == nid and n.type == "Sink" for n in nodes)]
        chosen = sink_terminals[0] if sink_terminals else terminals[0]
        return node_schemas.get(chosen)

    def _build_adjacency(
        self,
        nodes: list[IRNode],
        edges: list[IREdge | dict[str, str]],
    ) -> dict[str, list[str]]:
        """Build adjacency list: node_id → list of upstream node_ids."""
        node_ids = {n.id for n in nodes}
        adjacency: dict[str, list[str]] = {}
        for edge in edges:
            if isinstance(edge, dict):
                source = edge.get("source_id", edge.get("source", ""))
                target = edge.get("target_id", edge.get("target", ""))
            else:
                source = edge.source_id
                target = edge.target_id
            if source in node_ids and target in node_ids:
                adjacency.setdefault(target, []).append(source)
        return adjacency

    def _topological_sort(
        self,
        nodes: list[IRNode],
        adjacency: dict[str, list[str]],
    ) -> list[str]:
        """Kahn's algorithm topological sort. Raises ValueError on cycle."""
        node_ids = {n.id for n in nodes}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for nid in node_ids:
            for upstream in adjacency.get(nid, []):
                in_degree[nid] = in_degree.get(nid, 0) + 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_nodes: list[str] = []

        while queue:
            nid = queue.pop(0)
            sorted_nodes.append(nid)
            # Find all nodes that have this node as upstream
            for potential_downstream, upstreams in adjacency.items():
                if nid in upstreams:
                    in_degree[potential_downstream] -= 1
                    if in_degree[potential_downstream] == 0:
                        queue.append(potential_downstream)

        if len(sorted_nodes) != len(node_ids):
            raise ValueError("Pipeline IR contains a cycle — topological sort failed")

        return sorted_nodes


# ═══════════════════════════════════════════════════════════════════
# Core operator inference rules
# ═══════════════════════════════════════════════════════════════════


def _register_core_operators(registry: OperatorRegistry) -> None:
    """Register all core operators with inference rules."""

    # ── Source ──
    def _infer_source(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Source nodes declare their schema via dataset metadata;
        # at inference time, schema may be empty (unknown until validated).
        return Schema(fields=[])

    registry.register(
        OperatorSpec(
            type="Source",
            display_name="数据源",
            description="读取 Dataset（Iceberg 托管表）",
            category="source",
            output_schema_rule="Schema 来自 Dataset 元数据（运行时解析）",
            input_ports=0,
            output_ports=1,
            input_contract=InputContract(min_inputs=0, max_inputs=0),
            infer_output_schema=_infer_source,
            validate_config=None,
            config_schema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset api_name"},
                },
                "required": ["dataset"],
            },
        )
    )

    # ── Sink ──
    def _infer_sink(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Sink passes through upstream schema
        return input_schemas[0] if input_schemas else Schema(fields=[])

    registry.register(
        OperatorSpec(
            type="Sink",
            display_name="输出",
            description="输出到 Dataset（Iceberg 新 snapshot）",
            category="sink",
            output_schema_rule="同上游 Schema（透传）",
            input_ports=1,
            output_ports=0,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_sink,
            validate_config=None,
            config_schema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "输出 Dataset api_name"},
                    "write_mode": {"type": "string", "enum": ["FULL_REFRESH", "APPEND"]},
                },
            },
        )
    )

    # ── Filter ──
    def _infer_filter(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Filter does NOT change the schema — same fields, same types, row count changes
        return input_schemas[0] if input_schemas else Schema(fields=[])

    def _validate_filter(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        expr = config.expression
        if not expr:
            violations.append(
                ContractViolation(
                    valid=False,
                    level="ERROR",
                    message="Filter requires a condition expression",
                )
            )
        elif input_schemas:
            # Simple field reference check for common patterns like "field = value"
            import re

            field_refs = re.findall(r"([a-z_][a-z0-9_.]*)", expr)
            known_fields = {f.name for s in input_schemas for f in s.fields}
            for ref in field_refs:
                # Skip SQL keywords and literals
                if ref.upper() in ("AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE") or ref.startswith("'"):
                    continue
                if ref not in known_fields:
                    violations.append(
                        ContractViolation(
                            valid=False,
                            level="WARNING",
                            message=f"Field '{ref}' referenced in filter may not exist in upstream schema",
                        )
                    )
        return violations

    registry.register(
        OperatorSpec(
            type="Filter",
            display_name="过滤",
            description="按条件过滤行",
            category="transform",
            output_schema_rule="同上游 Schema（行数减少，字段不变）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_filter,
            validate_config=_validate_filter,
            config_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "过滤条件（WHERE 子句）"},
                },
                "required": ["expression"],
            },
        )
    )

    # ── Select (column projection) ──
    def _infer_select(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        upstream = input_schemas[0]
        columns = config.columns
        if columns:
            selected = [f for f in upstream.fields if f.name in columns]
            return Schema(fields=selected)
        return upstream  # No columns specified = passthrough

    def _validate_select(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        columns = config.columns or []
        if not input_schemas:
            return violations
        known = {f.name for f in input_schemas[0].fields}
        for col in columns:
            if col not in known:
                violations.append(
                    ContractViolation(
                        valid=False,
                        level="ERROR",
                        message=f"Column '{col}' does not exist in upstream schema",
                    )
                )
        return violations

    registry.register(
        OperatorSpec(
            type="Select",
            display_name="选择列",
            description="选择/投影指定列",
            category="transform",
            output_schema_rule="仅保留指定列（列裁剪）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_select,
            validate_config=_validate_select,
            config_schema={
                "type": "object",
                "properties": {
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "保留的列名（空=全部）",
                    },
                },
            },
        )
    )

    # ── Rename ──
    def _infer_rename(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        upstream = input_schemas[0]
        mapping = config.column_mapping or {}
        new_fields = []
        for f in upstream.fields:
            new_name = mapping.get(f.name, f.name)
            new_fields.append(SchemaField(name=new_name, data_type=f.data_type, nullable=f.nullable))
        return Schema(fields=new_fields)

    def _validate_rename(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        mapping = config.column_mapping or {}
        if not input_schemas:
            return violations
        known = {f.name for f in input_schemas[0].fields}
        for old_name in mapping:
            if old_name not in known:
                violations.append(
                    ContractViolation(
                        valid=False,
                        level="ERROR",
                        message=f"Column '{old_name}' to rename does not exist in upstream schema",
                    )
                )
        return violations

    registry.register(
        OperatorSpec(
            type="Rename",
            display_name="重命名",
            description="重命名列",
            category="transform",
            output_schema_rule="字段名重命名，类型不变",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_rename,
            validate_config=_validate_rename,
            config_schema={
                "type": "object",
                "properties": {
                    "column_mapping": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "列名映射 {旧名: 新名}",
                    },
                },
            },
        )
    )

    # ── TypeCast ──
    def _infer_typecast(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        upstream = input_schemas[0]
        target_type = config.target_type
        if not target_type:
            return upstream
        # TypeCast operates on a single column (config.extra.column) if specified
        column = config.extra.get("column") if config.extra else None
        new_fields = []
        for f in upstream.fields:
            if column and f.name == column:
                new_fields.append(SchemaField(name=f.name, data_type=target_type, nullable=f.nullable))
            else:
                new_fields.append(SchemaField(name=f.name, data_type=f.data_type, nullable=f.nullable))
        return Schema(fields=new_fields)

    def _validate_typecast(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        target = config.target_type
        if not target:
            violations.append(ContractViolation(valid=False, level="ERROR", message="TypeCast requires a target_type"))
        return violations

    registry.register(
        OperatorSpec(
            type="TypeCast",
            display_name="类型转换",
            description="转换列数据类型",
            category="transform",
            output_schema_rule="指定列类型转换为目标类型",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_typecast,
            validate_config=_validate_typecast,
            config_schema={
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "description": "目标数据类型"},
                    "column": {"type": "string", "description": "要转换的列名（空=所有列）"},
                },
            },
        )
    )

    # ── Join ──
    def _infer_join(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if len(input_schemas) < 2:
            return Schema(fields=[])
        left, right = input_schemas[0], input_schemas[1]
        # Merge fields: left + right (deduplicate names with prefix)
        left_names = {f.name for f in left.fields}
        merged = list(left.fields)
        for f in right.fields:
            if f.name in left_names:
                merged.append(
                    SchemaField(
                        name=f"{f.name}_right",
                        data_type=f.data_type,
                        nullable=f.nullable or config.join_type in ("LEFT", "FULL"),
                    )
                )
            else:
                merged.append(f)
        return Schema(fields=merged)

    def _validate_join(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        # 至少要有一个关联条件（结构化 join_conditions 或旧字符串 join_condition）
        has_structured = bool(config.join_conditions)
        has_legacy = bool(config.join_condition)
        if not has_structured and not has_legacy:
            violations.append(
                ContractViolation(valid=False, level="ERROR", message="Join requires at least one join condition")
            )
        if not config.join_type:
            violations.append(ContractViolation(valid=False, level="ERROR", message="Join requires a join_type"))
        elif config.join_type not in ("INNER", "LEFT", "RIGHT", "FULL"):
            violations.append(
                ContractViolation(valid=False, level="ERROR", message=f"Unsupported join type: {config.join_type}")
            )
        # 校验结构化条件的列名在上游 schema 中存在
        if has_structured and len(input_schemas) >= 2:
            left_names = {f.name for f in input_schemas[0].fields}
            right_names = {f.name for f in input_schemas[1].fields}
            for jc in config.join_conditions or []:
                if jc.left_column not in left_names:
                    violations.append(
                        ContractViolation(
                            valid=False,
                            level="ERROR",
                            message=f"Join left column '{jc.left_column}' not found in left input schema",
                        )
                    )
                if jc.right_column not in right_names:
                    violations.append(
                        ContractViolation(
                            valid=False,
                            level="ERROR",
                            message=f"Join right column '{jc.right_column}' not found in right input schema",
                        )
                    )
        return violations

    registry.register(
        OperatorSpec(
            type="Join",
            display_name="关联",
            description="多表关联（Inner/Left/Right/Full Join）",
            category="transform",
            output_schema_rule="合并两表字段（重名加 _right 后缀，Left/Full Join 右表可空）",
            input_ports=2,
            output_ports=1,
            input_contract=InputContract(min_inputs=2, max_inputs=2),
            infer_output_schema=_infer_join,
            validate_config=_validate_join,
            config_schema={
                "type": "object",
                "properties": {
                    "join_type": {
                        "type": "string",
                        "enum": ["INNER", "LEFT", "RIGHT", "FULL"],
                        "description": "关联类型",
                    },
                    "join_conditions": {
                        "type": "array",
                        "description": "结构化关联条件（等值连接，优先于 join_condition）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "left_column": {"type": "string"},
                                "right_column": {"type": "string"},
                            },
                        },
                    },
                    "join_condition": {"type": "string", "description": "关联条件（ON 子句，向后兼容）"},
                },
                "required": ["join_type"],
            },
        )
    )

    # ── Aggregate ──
    def _infer_aggregate(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        upstream = input_schemas[0]
        new_fields: list[SchemaField] = []

        # Group-by fields keep their types
        group_by = config.group_by or []
        gb_names = set(group_by)
        for f in upstream.fields:
            if f.name in gb_names:
                new_fields.append(f)

        # Aggregation result fields (type depends on function)
        # Aggregation function → result type mapping. None = preserve field type.
        agg_function_types: dict[str, str | None] = {
            "SUM": "DECIMAL",
            "COUNT": "BIGINT",
            "AVG": "DECIMAL",
            "MIN": None,  # Preserve field type
            "MAX": None,
            "COUNT_DISTINCT": "BIGINT",
        }
        aggs = config.aggregations or []
        for agg in aggs:
            field = agg.get("field", "")
            function = agg.get("function", "COUNT").upper()
            alias = agg.get("alias", f"{function}_{field}")
            # Determine result type
            if function in ("MIN", "MAX"):
                upstream_field = next((f for f in upstream.fields if f.name == field), None)
                agg_type = upstream_field.data_type if upstream_field else "STRING"
            else:
                agg_type = agg_function_types.get(function, "STRING") or "STRING"
            new_fields.append(SchemaField(name=alias, data_type=agg_type, nullable=True))

        return Schema(fields=new_fields)

    def _validate_aggregate(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        if not input_schemas:
            return violations
        known = {f.name for f in input_schemas[0].fields}
        for gb_field in config.group_by or []:
            if gb_field not in known:
                violations.append(
                    ContractViolation(
                        valid=False,
                        level="ERROR",
                        message=f"Group-by field '{gb_field}' not found in upstream schema",
                    )
                )
        for agg in config.aggregations or []:
            agg_field = agg.get("field", "")
            if agg_field and agg_field not in known:
                violations.append(
                    ContractViolation(
                        valid=False,
                        level="WARNING",
                        message=f"Aggregation field '{agg_field}' not found in upstream schema",
                    )
                )
        if not config.group_by and not config.aggregations:
            violations.append(
                ContractViolation(
                    valid=False,
                    level="WARNING",
                    message="Aggregate has no group_by or aggregation configured",
                )
            )
        return violations

    registry.register(
        OperatorSpec(
            type="Aggregate",
            display_name="聚合",
            description="分组聚合（SUM/COUNT/AVG/MIN/MAX）",
            category="transform",
            output_schema_rule="group_by 字段 + 聚合结果字段（SUM→DECIMAL, COUNT→BIGINT 等）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_aggregate,
            validate_config=_validate_aggregate,
            config_schema={
                "type": "object",
                "properties": {
                    "group_by": {"type": "array", "items": {"type": "string"}},
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "function": {
                                    "type": "string",
                                    "enum": ["SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"],
                                },
                                "alias": {"type": "string"},
                            },
                        },
                    },
                },
            },
        )
    )

    # ── Union ──
    def _infer_union(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        # Union merges all fields from all inputs
        all_fields: dict[str, SchemaField] = {}
        for schema in input_schemas:
            for f in schema.fields:
                if f.name not in all_fields:
                    all_fields[f.name] = f
                elif all_fields[f.name].data_type != f.data_type:
                    # Type conflict — mark as STRING (least common denominator)
                    all_fields[f.name] = SchemaField(name=f.name, data_type="STRING", nullable=True)
        return Schema(fields=list(all_fields.values()))

    def _validate_union(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        if len(input_schemas) < 2:
            violations.append(
                ContractViolation(
                    valid=False,
                    level="WARNING",
                    message="Union with <2 inputs is a no-op",
                )
            )
        return violations

    registry.register(
        OperatorSpec(
            type="Union",
            display_name="合并",
            description="纵向合并多个数据集",
            category="transform",
            output_schema_rule="合并所有输入字段（类型冲突降级为 STRING）",
            input_ports=2,
            output_ports=1,
            input_contract=InputContract(min_inputs=2, max_inputs=0),  # 0 = unlimited
            infer_output_schema=_infer_union,
            validate_config=_validate_union,
            config_schema={
                "type": "object",
                "properties": {},
            },
        )
    )

    # ── Expression (calculated column) ──
    def _infer_expression(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        if not input_schemas:
            return Schema(fields=[])
        upstream = input_schemas[0]
        # Expression adds a computed field but keeps all existing fields
        new_fields = list(upstream.fields)
        # The result type is unknown at compile time — default STRING
        if config.expression:
            new_fields.append(SchemaField(name="_expr_result", data_type="STRING", nullable=True))
        return Schema(fields=new_fields)

    registry.register(
        OperatorSpec(
            type="Expression",
            display_name="计算列",
            description="通过表达式添加计算列",
            category="transform",
            output_schema_rule="上游字段 + 计算列（结果类型默认 STRING）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_expression,
            config_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "计算表达式"},
                    "alias": {"type": "string", "description": "输出列名"},
                },
                "required": ["expression"],
            },
        )
    )

    # ── Deduplicate ──
    def _infer_deduplicate(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Deduplicate preserves schema (rows reduced, fields unchanged)
        return input_schemas[0] if input_schemas else Schema(fields=[])

    def _validate_deduplicate(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        keys = config.columns or []
        if not keys:
            violations.append(
                ContractViolation(valid=False, level="WARNING", message="Deduplicate has no key columns configured")
            )
        if input_schemas:
            known = {f.name for f in input_schemas[0].fields}
            for k in keys:
                if k not in known:
                    violations.append(
                        ContractViolation(
                            valid=False, level="ERROR", message=f"Deduplicate key '{k}' not found in upstream schema"
                        )
                    )
        return violations

    registry.register(
        OperatorSpec(
            type="Deduplicate",
            display_name="去重",
            description="按指定列去重",
            category="transform",
            output_schema_rule="同上游 Schema（行数减少，字段不变）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_deduplicate,
            validate_config=_validate_deduplicate,
            config_schema={
                "type": "object",
                "properties": {
                    "columns": {"type": "array", "items": {"type": "string"}, "description": "去重键列名"},
                },
            },
        )
    )

    # ── Sort ──
    def _infer_sort(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Sort preserves schema (row order changed, fields unchanged)
        return input_schemas[0] if input_schemas else Schema(fields=[])

    def _validate_sort(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        # 优先 sort_keys（结构化），回退 columns（旧）
        sort_keys = config.sort_keys or []
        legacy_keys = config.columns or []
        has_keys = bool(sort_keys) or bool(legacy_keys)
        if not has_keys:
            violations.append(
                ContractViolation(valid=False, level="WARNING", message="Sort has no sort keys configured")
            )
        if input_schemas:
            known = {f.name for f in input_schemas[0].fields}
            for sk in sort_keys:
                if sk.column not in known:
                    violations.append(
                        ContractViolation(
                            valid=False, level="ERROR", message=f"Sort key '{sk.column}' not found in upstream schema"
                        )
                    )
            for k in legacy_keys:
                if k not in known:
                    violations.append(
                        ContractViolation(
                            valid=False, level="ERROR", message=f"Sort key '{k}' not found in upstream schema"
                        )
                    )
        return violations

    registry.register(
        OperatorSpec(
            type="Sort",
            display_name="排序",
            description="按指定列排序",
            category="transform",
            output_schema_rule="同上游 Schema（行顺序变化，字段不变）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_sort,
            validate_config=_validate_sort,
            config_schema={
                "type": "object",
                "properties": {
                    "sort_keys": {
                        "type": "array",
                        "description": "排序键（优先于 columns），每个含 column + direction",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "direction": {"type": "string", "enum": ["ASC", "DESC"]},
                            },
                        },
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "排序列名（向后兼容，默认 ASC）",
                    },
                },
            },
        )
    )

    # ── QualityCheck ──
    def _infer_quality(input_schemas: list[Schema], config: NodeConfig) -> Schema:
        # Quality check passes through the upstream schema
        return input_schemas[0] if input_schemas else Schema(fields=[])

    def _validate_quality(config: NodeConfig, input_schemas: list[Schema]) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        rules = config.quality_rules or []
        if not rules:
            violations.append(
                ContractViolation(
                    valid=False,
                    level="WARNING",
                    message="QualityCheck has no rules configured",
                )
            )
        if input_schemas:
            known = {f.name for f in input_schemas[0].fields}
            for rule in rules:
                if rule.field and rule.field not in known:
                    violations.append(
                        ContractViolation(
                            valid=False,
                            level="WARNING",
                            message=f"Quality rule field '{rule.field}' not found in upstream schema",
                        )
                    )
        return violations

    registry.register(
        OperatorSpec(
            type="QualityCheck",
            display_name="质量校验",
            description="数据质量规则检查",
            category="quality",
            output_schema_rule="同上游 Schema（透传，仅校验不转换）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=1, max_inputs=1),
            infer_output_schema=_infer_quality,
            validate_config=_validate_quality,
            config_schema={
                "type": "object",
                "properties": {
                    "quality_rules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "rule_type": {
                                    "type": "string",
                                    "enum": ["not_null", "unique", "range", "regex", "expression"],
                                },
                                "field": {"type": "string"},
                                "config": {"type": "object"},
                                "severity": {"type": "string", "enum": ["ERROR", "WARNING", "SPLIT"]},
                                "message": {"type": "string"},
                            },
                        },
                    },
                },
            },
        )
    )

    # ── GenericKestraTask (passthrough — no schema inference) ──
    registry.register(
        OperatorSpec(
            type="GenericKestraTask",
            display_name="Kestra 任务",
            description="透传 Kestra 插件任务（Schema 需用户声明）",
            category="kestra",
            output_schema_rule="用户声明（不做自动推演）",
            input_ports=1,
            output_ports=1,
            input_contract=InputContract(min_inputs=0, max_inputs=0),
            config_schema={
                "type": "object",
                "properties": {
                    "kestra_task_type": {"type": "string", "description": "Kestra 插件全限定名"},
                    "kestra_task_config": {"type": "object", "description": "原始 Kestra 插件配置"},
                },
            },
        )
    )
