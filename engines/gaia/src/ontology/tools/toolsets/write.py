"""Write-layer tools (4) — ontology modelling mutations.

Per docs/architecture/ontology-tool-layer.md Sprint 2 (ADR-010). Each tool
wraps an OntologyService write method (define_object_type / add_property /
define_link_type / link_dataset) and is gated by MEDIUM-risk HITL approval.

Design (ADR-010, "shared logic + dual exposure"):
  - ``<tool>_logic(executor, ...)`` is the single source of truth: builds
    the impact summary, defers the Service call, delegates to
    ``executor.execute_gated``. Protocol-agnostic — takes the executor
    (which carries the protocol-specific ApprovalHandler).
  - ``build_write_toolset()`` produces the AG-UI exposure: ``@ts.tool``
    wrappers that read the request-scoped executor from ``ctx.deps`` and
    forward to ``<tool>_logic``. docstrings (the LLM contract) live here.
  - MCP exposure (``protocols/mcp_server.py``) calls the same
    ``<tool>_logic`` with a MCP-scoped executor (MCPApprovalHandler).

This keeps "tool logic written once" while accommodating the two request-
context injection mechanisms (pydantic-ai RunContext vs fastmcp Context).
"""

from __future__ import annotations

from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ontology.core.exceptions import ValidationError
from ontology.core.schemas.ontology import (
    LinkTypeDefCreate,
    ObjectTypeBatchCreate,
    ObjectTypeCreate,
    PropertyDefCreate,
    PropertyInput,
)
from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets._contracts import (
    ADD_PROPERTY_DESC,
    DEFINE_LINK_TYPE_DESC,
    DEFINE_OBJECT_TYPE_DESC,
    LINK_DATASET_DESC,
)

# ── Shared logic (single source of truth, protocol-agnostic) ─────────────


async def define_object_type_logic(
    executor: ToolExecutor,
    ontology: str,
    api_name: str,
    display_name: str,
    primary_key: str | None = None,
    title_property: str | None = None,
    storage_type: str = "MANAGED",
    description: str = "",
    properties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new object type. Medium-risk HITL gated.

    v6: api_name is PascalCase, caller-supplied (the LLM derives it from
    display_name; for Chinese names the frontend uses LLM translation via
    /ai/generate). primary_key / title_property are resolved from property
    is_primary_key / is_title_property flags (Q2) when omitted. Property /
    Link api_names are still derived by the service (camelCase).
    """
    svc = executor.container.ontology_service

    async def _do() -> dict[str, Any]:
        if properties:
            # Batch create: ObjectType + properties in one transaction.
            # Adapt the tool's loose dict shape ({display_name, data_type,
            # is_primary_key?, is_title_property?, indexed?}) to PropertyInput.
            # api_name is derived by the service.
            prop_inputs = [
                PropertyInput(
                    display_name=p.get("display_name", p.get("api_name", "")),
                    data_type=p["data_type"],
                    searchable=p.get("indexed", False),
                    is_primary_key=p.get("is_primary_key"),
                    is_title_property=p.get("is_title_property"),
                )
                for p in properties
            ]
            batch_data = ObjectTypeBatchCreate(
                api_name=api_name,
                display_name=display_name,
                description=description,
                primary_key=primary_key,
                title_property=title_property,
                storage_type=storage_type,
                properties=prop_inputs,
            )
            ot = await svc.define_object_type_batch(ontology, batch_data)
        else:
            # Single-type create requires an explicit primary_key (no
            # properties to derive it from). The tool requires it here.
            if not primary_key:
                raise ValidationError(
                    "define_object_type requires primary_key when no properties are given "
                    "(otherwise set is_primary_key=true on a property)"
                )
            simple_data = ObjectTypeCreate(
                api_name=api_name,
                display_name=display_name,
                description=description,
                primary_key=primary_key,
                title_property=title_property,
                storage_type=storage_type,  # type: ignore[arg-type]
            )
            ot = await svc.define_object_type(ontology, simple_data)
        return {
            "api_name": ot.api_name,
            "id": ot.id,
            "display_name": ot.display_name,
            "storage_type": ot.storage_type,
            "properties_created": len(properties) if properties else 0,
            "status": "created",
        }

    prop_count = len(properties) if properties else 1
    impact = (
        f"将创建对象类型 {display_name} ({api_name}),主键 {primary_key or '(由属性标记推导)'},"
        f"storage_type={storage_type},含 {prop_count} 个属性。"
        f"{'MANAGED 类型会触发 Doris 建表 + 索引同步 pipeline。' if storage_type == 'MANAGED' else ''}"
    )
    return cast(
        "dict[str, Any]",
        await executor.execute_write(
            "define_object_type",
            {"ontology": ontology, "api_name": api_name, "display_name": display_name},
            "medium",
            impact,
            _do,
        ),
    )


async def add_property_logic(
    executor: ToolExecutor,
    ontology: str,
    object_type: str,
    display_name: str,
    data_type: str,
    indexed: bool = False,
    nullable: bool = True,
    description: str = "",
) -> dict[str, Any]:
    """Add a property to an existing object type. Medium-risk HITL gated.

    v6: api_name is derived by the service from display_name (camelCase).
    """
    svc = executor.container.ontology_service

    async def _do() -> dict[str, Any]:
        prop = PropertyDefCreate(
            display_name=display_name,
            description=description,
            data_type=data_type,  # type: ignore[arg-type]
            indexed=indexed,
            nullable=nullable,
        )
        result = await svc.add_property_to_object_type(ontology, object_type, prop)
        return {"api_name": result.api_name, "object_type": object_type, "status": "added"}

    impact = (
        f"将为对象类型 {object_type} 添加属性 {display_name},"
        f"类型 {data_type},indexed={indexed}。"
        f"{'会触发 Doris 索引表 schema 演进。' if indexed else ''}"
    )
    return cast(
        "dict[str, Any]",
        await executor.execute_write(
            "add_property",
            {"ontology": ontology, "object_type": object_type, "display_name": display_name},
            "medium",
            impact,
            _do,
        ),
    )


async def define_link_type_logic(
    executor: ToolExecutor,
    ontology: str,
    display_name: str,
    source_object_type: str,
    target_object_type: str,
    cardinality: str = "MANY",
    direction: str = "OUTGOING",
    foreign_key_property: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Define a relationship type between two object types. Medium-risk HITL gated.

    v6: api_name is derived by the service from display_name (camelCase).
    """
    svc = executor.container.ontology_service

    async def _do() -> dict[str, Any]:
        # Resolve api_names -> UUIDs (define_link_type takes IDs).
        src = await svc.get_object_type(ontology, source_object_type)
        tgt = await svc.get_object_type(ontology, target_object_type)
        data = LinkTypeDefCreate(
            display_name=display_name,
            description=description,
            source_object_type_id=src.id,
            target_object_type_id=tgt.id,
            foreign_key_property_api_name=foreign_key_property,
            cardinality=cardinality,  # type: ignore[arg-type]
            direction=direction,  # type: ignore[arg-type]
        )
        lt = await svc.define_link_type(ontology, data)
        return {"api_name": lt.api_name, "status": "created"}

    impact = (
        f"将创建关系 {display_name}: "
        f"{source_object_type} -> {target_object_type}, "
        f"cardinality={cardinality}, direction={direction}."
    )
    return cast(
        "dict[str, Any]",
        await executor.execute_write(
            "define_link_type",
            {
                "ontology": ontology,
                "display_name": display_name,
                "source": source_object_type,
                "target": target_object_type,
            },
            "medium",
            impact,
            _do,
        ),
    )


async def link_dataset_logic(
    executor: ToolExecutor,
    ontology: str,
    object_type: str,
    dataset_api_name: str,
    column_mappings: list[dict[str, str]],
) -> dict[str, Any]:
    """Bind an object type's properties to physical dataset columns. Medium-risk HITL gated."""
    svc = executor.container.ontology_service

    async def _do() -> dict[str, Any]:
        ot = await svc.link_dataset(ontology, object_type, dataset_api_name, column_mappings)
        return {
            "object_type": ot.api_name,
            "dataset_api_name": dataset_api_name,
            "mapped_properties": len(column_mappings),
            "status": "linked",
        }

    impact = f"将绑定对象类型 {object_type} -> 数据集 {dataset_api_name},映射 {len(column_mappings)} 个属性到物理列。"
    return cast(
        "dict[str, Any]",
        await executor.execute_write(
            "link_dataset",
            {"ontology": ontology, "object_type": object_type, "dataset": dataset_api_name},
            "medium",
            impact,
            _do,
        ),
    )


# ── AG-UI exposure (pydantic-ai toolset, reads executor from ctx.deps) ───


def build_write_toolset() -> FunctionToolset[AppState]:
    """Build the AG-UI write-layer toolset.

    Each ``@ts.tool`` wrapper reads the request-scoped executor from
    ``ctx.deps.executor`` and forwards to the shared ``<tool>_logic``.
    docstrings (the LLM contract) live on these wrappers.
    """
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool(
        description=DEFINE_OBJECT_TYPE_DESC,
        metadata={"risk_level": "medium"},
    )
    async def define_object_type(
        ctx: RunContext[AppState],
        /,
        ontology: str,
        api_name: str,
        display_name: str,
        primary_key: str | None = None,
        title_property: str | None = None,
        storage_type: str = "MANAGED",
        description: str = "",
        properties: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new object type. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "write tools require a request-scoped executor"}}
        # Fall back to the ontology open in the Web UI (ctx.deps.ontology)
        # when the LLM omits it — same defaulting the read-only tools do.
        # Without this, the LLM frequently passes ontology="" and the user
        # approves a request that then fails with NotFoundError.
        ontology = ontology or ctx.deps.ontology
        return await define_object_type_logic(
            executor,
            ontology,
            api_name,
            display_name,
            primary_key,
            title_property,
            storage_type,
            description,
            properties,
        )

    @ts.tool(
        description=ADD_PROPERTY_DESC,
        metadata={"risk_level": "medium"},
    )
    async def add_property(
        ctx: RunContext[AppState],
        /,
        ontology: str,
        object_type: str,
        display_name: str,
        data_type: str,
        indexed: bool = False,
        nullable: bool = True,
        description: str = "",
    ) -> dict[str, Any]:
        """Add a property to an object type. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "write tools require a request-scoped executor"}}
        ontology = ontology or ctx.deps.ontology
        return await add_property_logic(
            executor,
            ontology,
            object_type,
            display_name,
            data_type,
            indexed,
            nullable,
            description,
        )

    @ts.tool(
        description=DEFINE_LINK_TYPE_DESC,
        metadata={"risk_level": "medium"},
    )
    async def define_link_type(
        ctx: RunContext[AppState],
        /,
        ontology: str,
        display_name: str,
        source_object_type: str,
        target_object_type: str,
        cardinality: str = "MANY",
        direction: str = "OUTGOING",
        foreign_key_property: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Define a relationship type. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "write tools require a request-scoped executor"}}
        ontology = ontology or ctx.deps.ontology
        return await define_link_type_logic(
            executor,
            ontology,
            display_name,
            source_object_type,
            target_object_type,
            cardinality,
            direction,
            foreign_key_property,
            description,
        )

    @ts.tool(
        description=LINK_DATASET_DESC,
        metadata={"risk_level": "medium"},
    )
    async def link_dataset(
        ctx: RunContext[AppState],
        /,
        ontology: str,
        object_type: str,
        dataset_api_name: str,
        column_mappings: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Bind an object type's properties to physical dataset columns. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "write tools require a request-scoped executor"}}
        ontology = ontology or ctx.deps.ontology
        return await link_dataset_logic(
            executor,
            ontology,
            object_type,
            dataset_api_name,
            column_mappings,
        )

    return ts
