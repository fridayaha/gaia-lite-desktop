"""KestraEngine — IR → Kestra Flow translation + execution management.

Design (ADR-018 D1/D2):
  - Translates Pipeline IR (engine-agnostic logical DAG) to Kestra Flow YAML
  - Manages flow lifecycle via Kestra REST API (create/update/delete/trigger/kill)
  - Task routing: Transform→DuckDB, Source/Sink→DuckDB, SeaTunnel→HTTP, Trino→JDBC
  - One-way translation: IR → Kestra Flow (never reverse)
  - Does NOT modify Kestra source code — pure REST integration (red line #5)

This engine is the execution adapter layer. PipelineBuilderService calls
methods like ``deploy()`` / ``trigger_build()`` / ``cancel_build()`` here,
never directly calling Kestra REST.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import yaml

from ontology.config.settings import settings
from ontology.core import naming
from ontology.core.exceptions import OntologyError
from ontology.core.schemas.pipeline_builder import IRNode, PipelineIR
from ontology.layers.pipeline.operator_sql import (
    build_quality_check_sql,
    build_transform_sql,
)

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Domain exceptions
# ═══════════════════════════════════════════════════════════════════


class KestraUnavailableError(OntologyError):
    """Kestra server is unreachable."""


class KestraClientError(OntologyError):
    """Kestra returned a non-success response."""


# ═══════════════════════════════════════════════════════════════════
# Kestra REST Client
# ═══════════════════════════════════════════════════════════════════


class KestraClient:
    """Thin HTTP client for Kestra's REST API.

    Covers the subset needed by Pipeline Builder:
      - Flow management: GET/POST/PUT /api/v1/flows/{namespace}/{id}
      - Execution: POST /api/v1/executions/{namespace}/{id} (trigger)
      - Execution query: GET /api/v1/executions/{id}
      - Execution kill: DELETE /api/v1/executions/{id}/kill
      - Execution restart: POST /api/v1/executions/{id}/restart
      - SSE stream: GET /api/v1/executions/{id}/follow
      - Plugin discovery: GET /api/v1/plugins
    """

    def __init__(self) -> None:
        self._base_url = f"http://{settings.kestra_host}:{settings.kestra_port}/api/v1"
        self._headers: dict[str, str] = {}
        if settings.kestra_password:
            self._headers["Authorization"] = f"Basic {settings.kestra_password}"

    async def _request(self, method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        merged_headers = {**self._headers, **(headers or {})}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.request(method, url, headers=merged_headers, **kwargs)
            except httpx.RequestError as e:
                raise KestraUnavailableError(f"Kestra unreachable: {e}") from e
            if resp.status_code >= 400:
                raise KestraClientError(f"Kestra {method} {path} returned {resp.status_code}: {resp.text}")
            if resp.status_code == 204:
                return None
            return resp.json()

    async def health(self) -> bool:
        """Check if Kestra is reachable."""
        try:
            await self._request("GET", "/")
            return True
        except (KestraUnavailableError, KestraClientError):
            return False

    # ── Flow management ──

    async def upsert_flow(self, namespace: str, flow_id: str, flow_yaml: str) -> dict[str, Any]:
        """Create or update a flow. Returns the flow metadata.

        Kestra 1.3: ``POST /api/v1/main/flows`` accepts a JSON flow object
        (not a YAML string). We parse the YAML → dict and send as JSON.
        The flow's ``id`` and ``namespace`` in the body must match the
        caller-supplied ``flow_id``/``namespace``.
        """
        import yaml as _yaml

        flow_dict: dict[str, Any] = _yaml.safe_load(flow_yaml) or {}
        # Ensure id/namespace match the caller (defensive — translator sets them too).
        flow_dict["id"] = flow_id
        flow_dict["namespace"] = namespace
        path = "/main/flows"
        result = await self._request("POST", path, json=flow_dict, headers={"Content-Type": "application/json"})
        assert isinstance(result, dict)
        return result

    async def delete_flow(self, namespace: str, flow_id: str) -> None:
        """Delete a flow."""
        path = f"/main/flows/{namespace}/{flow_id}"
        try:
            await self._request("DELETE", path)
        except KestraClientError as e:
            _log.warning("Kestra delete flow failed (non-fatal): %s", e)

    async def get_flow(self, namespace: str, flow_id: str) -> dict[str, Any] | None:
        """Get flow metadata. Returns None if not found."""
        path = f"/main/flows/{namespace}/{flow_id}"
        try:
            result = await self._request("GET", path)
            assert isinstance(result, dict)
            return result
        except KestraClientError as e:
            if "404" in str(e):
                return None
            raise

    # ── Execution management ──

    async def trigger_execution(  # noqa: E501
        self, namespace: str, flow_id: str, inputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Trigger a flow execution. Returns the execution metadata.

        Kestra 1.3: ``POST /api/v1/main/executions/{namespace}/{flow_id}``.
        Inputs are sent as multipart form fields (one per input key).
        If no inputs, send an empty body.
        """
        path = f"/main/executions/{namespace}/{flow_id}"
        if inputs:
            # Kestra expects multipart/form-data for inputs.
            result = await self._request("POST", path, data=inputs)
        else:
            # No inputs — POST with no body (Kestra accepts empty).
            result = await self._request("POST", path)
        assert isinstance(result, dict)
        return result

    async def get_execution(self, execution_id: str) -> dict[str, Any]:
        """Get execution details."""
        path = f"/main/executions/{execution_id}"
        result = await self._request("GET", path)
        assert isinstance(result, dict)
        return result

    async def kill_execution(self, execution_id: str) -> None:
        """Kill a running execution."""
        path = f"/main/executions/{execution_id}/kill"
        await self._request("DELETE", path)

    async def restart_execution(self, execution_id: str) -> dict[str, Any]:
        """Restart a failed execution."""
        path = f"/main/executions/{execution_id}/restart"
        result = await self._request("POST", path)
        assert isinstance(result, dict)
        return result

    async def list_plugins(self) -> list[dict[str, Any]]:
        """List all available Kestra plugins."""
        path = "/plugins"
        result = await self._request("GET", path)
        assert isinstance(result, list)
        return result


# ═══════════════════════════════════════════════════════════════════
# IR → Kestra Flow Translator
# ═══════════════════════════════════════════════════════════════════


class KestraFlowTranslator:
    """Translates Pipeline IR → Kestra Flow YAML.

    Translation rules (pipeline-builder-design.md §8.3):
      - One Pipeline → One Kestra Flow
      - One IR node → one or more Kestra Tasks
      - Transform nodes → DuckDB SQL queries (CTE-chained in MVP, D6)
      - Source/Sink nodes → DuckDB read/write Iceberg
      - GenericKestraTask → passthrough (raw Kestra plugin config)
      - QualityCheck → Kestra If Task + DuckDB validation SQL
    """

    def __init__(self) -> None:
        self._namespace = settings.kestra_namespace_prefix
        self._trino_jdbc_url = f"jdbc:trino://{settings.trino_host}:{settings.trino_port}"
        self._iceberg_rest_uri = settings.iceberg_rest_uri
        self._iceberg_warehouse = settings.iceberg_warehouse

    def translate(self, ir: PipelineIR, pipeline_api_name: str, project_api_name: str = "pipelines") -> str:
        """Translate Pipeline IR → Kestra Flow YAML string.

        Args:
            ir: The pipeline IR to translate.
            pipeline_api_name: Used to derive flow ID (pipeline_{api_name}).
            project_api_name: Used for Kestra namespace (gaia.{project}).

        Returns:
            A complete Kestra Flow YAML document.
        """
        flow_id = naming.kestra_flow_id(pipeline_api_name)
        namespace = naming.kestra_namespace(project_api_name)

        # Build adjacency from edges: node_id → list of direct upstream node_ids
        upstream_map = self._build_upstream_map(ir.nodes, ir.edges)

        # Topological sort
        topo_order = self._topological_sort(ir.nodes, upstream_map)

        # Translate each node to Kestra tasks
        tasks: list[dict[str, Any]] = []
        cte_chain: list[str] = []
        node_outputs: dict[str, str] = {}  # node_id → CTE alias

        for node_id in topo_order:
            node = next((n for n in ir.nodes if n.id == node_id), None)
            if node is None:
                continue

            # Resolve direct upstreams (preserving edge order for multi-input nodes like Join)
            direct_upstream_ids = upstream_map.get(node_id, [])

            if node.type == "Source":
                task = self._translate_source(node, cte_chain, node_outputs)
                tasks.append(task)
            elif node.type == "Transform":
                task = self._translate_transform(node, direct_upstream_ids, cte_chain, node_outputs)
                tasks.append(task)
            elif node.type == "Sink":
                task = self._translate_sink(node, ir.write_mode, cte_chain, node_outputs, upstream_map)
                tasks.append(task)
            elif node.type == "QualityCheck":
                task = self._translate_quality_check(node, direct_upstream_ids, cte_chain, node_outputs)
                tasks.append(task)
            elif node.type == "GenericKestraTask":
                task = self._translate_generic(node)
                tasks.append(task)

        # Build the final YAML
        flow = {
            "id": flow_id,
            "namespace": namespace,
            "revision": 1,
            "description": f"Pipeline: {pipeline_api_name}",
            "labels": self._build_labels(ir),
            "tasks": tasks,
        }

        result: str = yaml.dump(flow, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return result

    def _build_upstream_map(self, nodes: list[IRNode], edges: list) -> dict[str, list[str]]:
        """Build adjacency: node_id → list of direct upstream node_ids (edge order preserved)."""
        node_ids = {n.id for n in nodes}
        upstream: dict[str, list[str]] = {}
        for edge in edges:
            source = getattr(edge, "source_id", None) or (edge.get("source_id") if isinstance(edge, dict) else None)
            target = getattr(edge, "target_id", None) or (edge.get("target_id") if isinstance(edge, dict) else None)
            if source and target and source in node_ids and target in node_ids:
                upstream.setdefault(target, []).append(source)
        return upstream

    # ── Node translators ──

    def _translate_source(self, node: IRNode, cte_chain: list[str], node_outputs: dict[str, str]) -> dict[str, Any]:
        """Source node → DuckDB query reading from Iceberg."""
        dataset_name = node.config.extra.get("dataset", "") if node.config.extra else ""
        if not dataset_name:
            # Empty dataset is a config error — emit a no-op placeholder;
            # SchemaInferenceEngine flags this as ERROR separately.
            dataset_name = "_unconfigured_source"
        # Validate dataset name (prevents SQL injection via node config)
        naming.validate_identifier(dataset_name)
        table_ref = f"iceberg.{dataset_name}"
        alias = f"src_{node.id}"
        naming.validate_identifier(alias)

        cte_entry = f"{alias} AS (SELECT * FROM {table_ref})"
        cte_chain.append(cte_entry)
        node_outputs[node.id] = alias

        # Source is a no-op metadata task in the Kestra flow — the actual
        # data reading is embedded in the CTE chain.
        return {
            "id": node.id,
            "type": "io.kestra.plugin.core.log.Log",
            "message": f"Source: {dataset_name} (embedded in CTE chain)",
        }

    def _translate_transform(
        self,
        node: IRNode,
        direct_upstream_ids: list[str],
        cte_chain: list[str],
        node_outputs: dict[str, str],
    ) -> dict[str, Any]:
        """Transform node → DuckDB SQL in CTE chain.

        MVP uses CTE chaining (scheme A from design §8.3): all transforms
        are concatenated into one DuckDB query. Phase 2 may switch to
        intermediate Iceberg tables for long pipelines.
        """
        operator = node.operator_type or "Filter"

        if not direct_upstream_ids:
            # No upstream — skip; this is an error state that should have
            # been caught by SchemaInferenceEngine validation.
            return {
                "id": node.id,
                "type": "io.kestra.plugin.core.log.Log",
                "message": f"Transform {node.id} has no upstream (skipped)",
            }

        # Build the upstream references for the CTE chain (resolve aliases)
        upstream_aliases = [node_outputs.get(uid, "") for uid in direct_upstream_ids]
        if not all(upstream_aliases):
            return {
                "id": node.id,
                "type": "io.kestra.plugin.core.log.Log",
                "message": f"Transform {node.id}: one or more upstream outputs not found",
            }

        node_alias = f"tfm_{node.id}"
        sql = build_transform_sql(operator, node.config, upstream_aliases, node_alias)
        cte_chain.append(sql)
        node_outputs[node.id] = node_alias

        return {
            "id": node.id,
            "type": "io.kestra.plugin.core.log.Log",
            "message": f"Transform: {operator} (embedded in CTE chain)",
        }

    def _translate_sink(
        self,
        node: IRNode,
        write_mode: str,
        cte_chain: list[str],
        node_outputs: dict[str, str],
        upstream_map: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Sink node → Final DuckDB query that writes to Iceberg.

        The final CTE entry is used as the write source. We resolve the
        sink's direct upstream alias (not cte_chain[-1], which may belong
        to a sibling branch in a multi-sink pipeline).
        """
        dataset_name = node.config.extra.get("dataset", "") if node.config.extra else ""
        if not dataset_name:
            # Fallback: use default from pipeline config (set by PipelineBuilderService)
            dataset_name = "output"
        # Validate dataset name (prevents SQL injection)
        naming.validate_identifier(dataset_name)

        # Resolve the sink's direct upstream alias
        direct_upstream_ids = upstream_map.get(node.id, [])
        upstream_alias = node_outputs.get(direct_upstream_ids[0], "") if direct_upstream_ids else ""
        if not upstream_alias and cte_chain:
            # Fallback to last CTE entry's alias (single-sink pipelines)
            upstream_alias = cte_chain[-1].split(" AS ", 1)[0].strip()
        if upstream_alias:
            naming.validate_identifier(upstream_alias)

        cte_body = ",\n  ".join(cte_chain) if cte_chain else "dummy AS (SELECT 1 AS dummy)"

        # The final SQL: either CREATE OR REPLACE TABLE (full refresh) or INSERT INTO (append)
        if write_mode.upper() == "APPEND":
            final_sql = f"WITH {cte_body}\nINSERT INTO iceberg.{dataset_name}\nSELECT * FROM {upstream_alias}"
        else:
            final_sql = (
                f"WITH {cte_body}\nCREATE OR REPLACE TABLE iceberg.{dataset_name} AS\nSELECT * FROM {upstream_alias}"
            )

        return {
            "id": node.id,
            "type": "io.kestra.plugin.jdbc.duckdb.Query",
            "sql": final_sql,
        }

    def _translate_quality_check(
        self,
        node: IRNode,
        direct_upstream_ids: list[str],
        cte_chain: list[str],
        node_outputs: dict[str, str],
    ) -> dict[str, Any]:
        """QualityCheck node → Kestra If Task + DuckDB validation SQL.

        Builds a DuckDB query that returns the count of violating rows.
        If count > 0 and severity is ERROR, the If task fails the execution.
        """
        upstream_id = direct_upstream_ids[0] if direct_upstream_ids else ""
        upstream_alias = node_outputs.get(upstream_id, "") if upstream_id else ""

        if not upstream_alias:
            return {
                "id": node.id,
                "type": "io.kestra.plugin.core.log.Log",
                "message": "QualityCheck skipped (no upstream)",
            }

        rules = node.config.quality_rules or []
        if not rules:
            return {
                "id": node.id,
                "type": "io.kestra.plugin.core.log.Log",
                "message": "QualityCheck: no rules configured",
            }

        check_sql = build_quality_check_sql(rules, upstream_alias)
        if check_sql is None:
            return {
                "id": node.id,
                "type": "io.kestra.plugin.core.log.Log",
                "message": "QualityCheck: no valid rules produced",
            }

        # Determine if this check should fail the build (ERROR severity)
        has_error = any((r.severity if hasattr(r, "severity") else r.get("severity")) == "ERROR" for r in rules)

        # QualityCheck → io.kestra.plugin.core.flow.If + DuckDB validation SQL.
        # The If task checks the violation count; on failure (ERROR severity)
        # it logs and fails the execution via a FAIL task.
        validation_task_id = f"{node.id}_check"
        fail_task_id = f"{node.id}_fail"
        pass_task_id = f"{node.id}_pass"

        return {
            "id": node.id,
            "type": "io.kestra.plugin.core.flow.If",
            "condition": f"{{{{ outputs.{validation_task_id}.rows[0].total_violations == 0 }}}}",
            "then": [
                {
                    "id": pass_task_id,
                    "type": "io.kestra.plugin.core.log.Log",
                    "message": f"Quality check passed ({len(rules)} rules)",
                }
            ],
            "else": [
                {
                    "id": fail_task_id,
                    "type": "io.kestra.plugin.core.log.Log",
                    "level": "ERROR" if has_error else "WARN",
                    "message": (
                        f"Quality check FAILED: "
                        f"{{{{ outputs.{validation_task_id}.rows[0].total_violations }}}} violations"
                    ),
                },
            ]
            + (
                [
                    {
                        # On ERROR severity, fail the execution explicitly
                        "id": f"{node.id}_abort",
                        "type": "io.kestra.plugin.core.flow.Fail",
                        "message": f"Quality check failed with ERROR severity ({len(rules)} rules)",
                    }
                ]
                if has_error
                else []
            ),
            # The validation query runs as a sub-task before the If evaluates
            "validationTask": {
                "id": validation_task_id,
                "type": "io.kestra.plugin.jdbc.duckdb.Query",
                "sql": check_sql,
            },
        }

    def _translate_generic(self, node: IRNode) -> dict[str, Any]:
        """GenericKestraTask node → passthrough Kestra plugin task config."""
        task_type = node.config.kestra_task_type or "io.kestra.plugin.core.log.Log"
        task_config = node.config.kestra_task_config or {}

        task: dict[str, Any] = {
            "id": node.id,
            "type": task_type,
        }
        task.update(task_config)
        return task

    # ── Helpers ──

    def _build_labels(self, ir: PipelineIR) -> list[dict[str, str]]:
        """Build Kestra labels from IR metadata."""
        labels = [
            {"key": "gaia.resource", "value": "pipeline"},
            {"key": "gaia.write_mode", "value": ir.write_mode},
        ]
        if ir.tags:
            for tag in ir.tags[:5]:  # Limit to 5 tags
                labels.append({"key": "gaia.tag", "value": tag})
        return labels

    def _topological_sort(self, nodes: list[IRNode], upstream_map: dict[str, list[str]]) -> list[str]:
        """Kahn's algorithm for topological sort of IR nodes.

        Raises ValueError if the graph contains a cycle.
        """
        node_ids = [n.id for n in nodes]
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        # Count in-degree: number of direct upstreams per node
        for nid in node_ids:
            in_degree[nid] = len(upstream_map.get(nid, []))

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_nodes: list[str] = []

        while queue:
            nid = queue.pop(0)
            sorted_nodes.append(nid)
            # Decrement in-degree for all nodes that have nid as upstream
            for potential_downstream, upstreams in upstream_map.items():
                if nid in upstreams:
                    in_degree[potential_downstream] -= 1
                    if in_degree[potential_downstream] == 0 and potential_downstream not in sorted_nodes:
                        queue.append(potential_downstream)

        if len(sorted_nodes) != len(node_ids):
            raise ValueError("Pipeline IR contains a cycle — topological sort failed")

        return sorted_nodes


# ═══════════════════════════════════════════════════════════════════
# KestraEngine (facade)
# ═══════════════════════════════════════════════════════════════════


class KestraEngine:
    """Facade for Pipeline Builder ↔ Kestra integration.

    Encapsulates the two sub-components:
      - KestraClient (REST API communication)
      - KestraFlowTranslator (IR → Flow YAML translation)

    PipelineBuilderService calls methods here rather than interacting
    with Kestra directly.
    """

    def __init__(
        self,
        client: KestraClient | None = None,
        translator: KestraFlowTranslator | None = None,
    ) -> None:
        self.client = client or KestraClient()
        self.translator = translator or KestraFlowTranslator()

    async def health(self) -> bool:
        """Check Kestra connectivity."""
        return await self.client.health()

    async def deploy(
        self,
        ir: PipelineIR,
        pipeline_api_name: str,
        project_api_name: str = "pipelines",
        namespace: str = "gaia.pipelines",
    ) -> dict[str, Any]:
        """Deploy a pipeline: translate IR → Kestra Flow YAML → upsert.

        Returns the Kestra flow metadata.
        """
        flow_yaml = self.translator.translate(ir, pipeline_api_name, project_api_name)
        flow_id = naming.kestra_flow_id(pipeline_api_name)
        return await self.client.upsert_flow(namespace, flow_id, flow_yaml)

    async def undeploy(self, pipeline_api_name: str, namespace: str = "gaia.pipelines") -> None:
        """Remove a pipeline flow from Kestra."""
        flow_id = naming.kestra_flow_id(pipeline_api_name)
        await self.client.delete_flow(namespace, flow_id)

    async def trigger_build(
        self,
        pipeline_api_name: str,
        namespace: str = "gaia.pipelines",
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger a pipeline execution in Kestra.

        Returns the Kestra execution metadata.
        """
        flow_id = naming.kestra_flow_id(pipeline_api_name)
        return await self.client.trigger_execution(namespace, flow_id, inputs)

    async def get_build_status(self, kestra_execution_id: str) -> dict[str, Any]:
        """Get execution status from Kestra.

        Returns the Kestra execution metadata with state, taskRunList, etc.
        """
        return await self.client.get_execution(kestra_execution_id)

    async def cancel_build(self, kestra_execution_id: str) -> None:
        """Cancel a running execution."""
        await self.client.kill_execution(kestra_execution_id)

    async def restart_build(self, kestra_execution_id: str) -> dict[str, Any]:
        """Restart a failed execution."""
        return await self.client.restart_execution(kestra_execution_id)

    async def list_plugins(self) -> list[dict[str, Any]]:
        """List available Kestra plugins (for operator catalog)."""
        return await self.client.list_plugins()
