"""Unit tests for HITL impact preview builders + MetadataApprovalToolset enrichment.

Validates the P0 optimisation (2026-07-08): the AG-UI interrupt's metadata
carries an ``impact_summary`` (plain-language "将创建对象类型...") and
``resolved_args`` (ontology defaults applied) so the frontend
``BatchApprovalPanel`` renders the real effect instead of the raw JSON args
(which may contain ``ontology=""``).

Two layers:
1. ``impact_builder.build_impact`` — pure-function builders per tool name.
2. ``MetadataApprovalToolset.call_tool`` — raises ``ApprovalRequired`` with
   the enriched metadata (``risk_level`` + ``impact_summary`` +
   ``resolved_args``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired

from ontology.config.container import Container
from ontology.core.schemas.ontology import ObjectType
from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets.approval import MetadataApprovalToolset
from ontology.tools.toolsets.impact_builder import build_impact
from ontology.tools.toolsets.write import build_write_toolset

_NOW = datetime.now(UTC)


def _ctx(ontology: str = "Marketing") -> RunContext[AppState]:
    """Build a RunContext with the given ontology + a real executor in deps."""
    return RunContext[AppState](
        deps=AppState(ontology=ontology, executor=ToolExecutor(Container())),
        model=None,  # type: ignore[arg-type]
        usage=None,  # type: ignore[arg-type]
        prompt=None,  # type: ignore[arg-type]
        messages=[],
        tool_call_id="test",
        retry=0,
        run_step=0,
        tool_name="test",
    )


# ── impact_builder.build_impact ──


class TestBuildImpact:
    """Pure-function builders: correct summary + resolved args per tool."""

    def test_define_object_type_resolves_empty_ontology(self) -> None:
        """The core fix: LLM passes ontology="" → preview shows ctx.deps.ontology."""
        ctx = _ctx("Marketing")
        args = {
            "ontology": "",
            "api_name": "Coupon",
            "display_name": "优惠券",
            "storage_type": "MANAGED",
            "primary_key": "couponNo",
            "properties": [
                {"display_name": "券号", "data_type": "STRING", "is_primary_key": True},
                {"display_name": "面值", "data_type": "DECIMAL"},
            ],
        }
        preview = build_impact("define_object_type", args, ctx)
        assert preview is not None
        # Resolved ontology — the key assertion (was "" in the raw args).
        assert preview["resolved_args"]["ontology"] == "Marketing"
        # Summary mentions the resolved ontology + object + property count.
        assert "Marketing" in preview["impact_summary"]
        assert "优惠券" in preview["impact_summary"]
        assert "Coupon" in preview["impact_summary"]
        assert "2 个属性" in preview["impact_summary"]
        assert "Doris 建表" in preview["impact_summary"]  # MANAGED note

    def test_define_object_type_explicit_ontology_preserved(self) -> None:
        """An explicit ontology arg is preserved in resolved_args."""
        ctx = _ctx("Marketing")
        args = {
            "ontology": "OtherOntology",
            "api_name": "Order",
            "display_name": "订单",
            "storage_type": "VIRTUAL",
        }
        preview = build_impact("define_object_type", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "OtherOntology"
        # VIRTUAL → no Doris note.
        assert "Doris" not in preview["impact_summary"]

    def test_define_object_type_pk_derived_from_properties(self) -> None:
        """primary_key="" → summary says '(由属性标记推导)'."""
        ctx = _ctx("Marketing")
        args = {
            "ontology": "",
            "api_name": "Coupon",
            "display_name": "优惠券",
            "primary_key": "",
            "properties": [{"display_name": "券号", "data_type": "STRING", "is_primary_key": True}],
        }
        preview = build_impact("define_object_type", args, ctx)
        assert preview is not None
        assert "由属性标记推导" in preview["impact_summary"]

    def test_add_property(self) -> None:
        ctx = _ctx("Marketing")
        args = {"ontology": "", "object_type": "Order", "display_name": "折扣", "data_type": "DECIMAL", "indexed": True}
        preview = build_impact("add_property", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "Marketing"
        assert "Order" in preview["impact_summary"]
        assert "折扣" in preview["impact_summary"]
        assert "DECIMAL" in preview["impact_summary"]
        assert "schema 演进" in preview["impact_summary"]  # indexed note

    def test_define_link_type(self) -> None:
        ctx = _ctx("Marketing")
        args = {
            "ontology": "",
            "display_name": "属于客户",
            "source_object_type": "Order",
            "target_object_type": "Customer",
            "cardinality": "MANY",
            "direction": "OUTGOING",
        }
        preview = build_impact("define_link_type", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "Marketing"
        assert "Order" in preview["impact_summary"]
        assert "Customer" in preview["impact_summary"]
        assert "MANY" in preview["impact_summary"]

    def test_link_dataset(self) -> None:
        ctx = _ctx("Marketing")
        args = {
            "ontology": "",
            "object_type": "Order",
            "dataset_api_name": "order_raw",
            "column_mappings": [{"property": "order_no", "column": "order_id"}],
        }
        preview = build_impact("link_dataset", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "Marketing"
        assert "order_raw" in preview["impact_summary"]
        assert "1 个属性" in preview["impact_summary"]

    def test_invoke_action(self) -> None:
        ctx = _ctx("Marketing")
        args = {
            "ontology": "",
            "object_type": "Order",
            "action_type": "update_note",
            "parameters": {"order_no": "PO-001"},
        }
        preview = build_impact("invoke_action", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "Marketing"
        assert "update_note" in preview["impact_summary"]
        assert "PO-001" in preview["impact_summary"]
        # Risk note: invoke_action risk is resolved at exec time.
        assert "风险等级" in preview["impact_summary"]

    def test_validate_action(self) -> None:
        ctx = _ctx("Marketing")
        args = {"ontology": "", "object_type": "Order", "action_type": "cancel_order"}
        preview = build_impact("validate_action", args, ctx)
        assert preview is not None
        assert preview["resolved_args"]["ontology"] == "Marketing"
        assert "预校验" in preview["impact_summary"]
        assert "不执行" in preview["impact_summary"]

    def test_unknown_tool_returns_none(self) -> None:
        """Tools without a registered builder fall back to no preview."""
        ctx = _ctx("Marketing")
        assert build_impact("nonexistent_tool", {}, ctx) is None


# ── MetadataApprovalToolset integration ──


def _make_ot() -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name="Coupon",
        display_name="优惠券",
        description="",
        primary_key="couponNo",
        title_property="couponNo",
        storage_type="MANAGED",
        properties=[],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _get_tool(toolset: Any, name: str) -> Any:
    return toolset.tools[name].function


class TestApprovalEnrichment:
    """MetadataApprovalToolset attaches impact_summary + resolved_args onto the interrupt."""

    @pytest.mark.asyncio
    async def test_approval_carries_impact_summary_and_resolved_args(self) -> None:
        """When the LLM calls define_object_type(ontology="", ...), the
        ApprovalRequired.metadata carries:
          - risk_level (static, from tool declaration)
          - impact_summary (plain-language, ontology resolved to Marketing)
          - resolved_args (ontology defaults applied)

        This is what the frontend BatchApprovalPanel reads to render a
        human-readable preview instead of the raw JSON with ontology=""."""
        from unittest.mock import MagicMock

        write_ts = build_write_toolset()
        wrapped = MetadataApprovalToolset(write_ts)

        ctx = _ctx("Marketing")
        ctx.tool_call_approved = False  # type: ignore[attr-defined]

        # Build a minimal ToolsetTool stand-in carrying the static metadata
        # (risk_level=medium). The wrapper only reads tool.tool_def.metadata
        # before raising ApprovalRequired, so a mock with that attribute is
        # enough — we don't need the real tool function (it never runs on
        # the first / unapproved call).
        tool = MagicMock()
        tool.tool_def.metadata = {"risk_level": "medium"}

        with pytest.raises(ApprovalRequired) as exc_info:
            await wrapped.call_tool(
                "define_object_type",
                {
                    "ontology": "",
                    "api_name": "Coupon",
                    "display_name": "优惠券",
                    "primary_key": "couponNo",
                    "storage_type": "MANAGED",
                    "properties": [{"display_name": "券号", "data_type": "STRING", "is_primary_key": True}],
                },
                ctx,
                tool,
            )

        meta = exc_info.value.metadata
        assert meta is not None
        # Static risk_level preserved.
        assert meta["risk_level"] == "medium"
        # Impact summary attached (the P0 enrichment).
        assert "impact_summary" in meta
        assert "Marketing" in meta["impact_summary"]
        assert "优惠券" in meta["impact_summary"]
        # Resolved args attached — ontology filled from ctx.deps.ontology.
        assert "resolved_args" in meta
        assert meta["resolved_args"]["ontology"] == "Marketing"

    @pytest.mark.asyncio
    async def test_readonly_tool_without_risk_level_passes_through(self) -> None:
        """Tools without risk_level in metadata are not gated (read-only pass-through).

        impact_builder only registers builders for write/action tools; a
        read-only tool with no risk_level metadata should NOT raise
        ApprovalRequired — it executes directly."""
        from ontology.tools.toolsets.metadata import build_metadata_toolset

        executor = ToolExecutor(Container())
        meta_ts = build_metadata_toolset(executor)
        wrapped = MetadataApprovalToolset(meta_ts)

        # list_object_types has no risk_level → no approval, no impact.
        ctx = _ctx("Marketing")
        ctx.tool_call_approved = False  # type: ignore[attr-defined]

        # Should NOT raise — read-only tools pass through.
        # (It may return an error since no mock service; we just assert
        # no ApprovalRequired is raised.)
        try:
            from unittest.mock import MagicMock

            tool = MagicMock()
            tool.tool_def.metadata = None  # no risk_level → read-only
            await wrapped.call_tool("list_object_types", {"ontology": ""}, ctx, tool)
        except ApprovalRequired:
            pytest.fail("read-only tool should not require approval")
        except Exception:
            # Other errors (e.g. no DB) are fine — we only assert no approval gate.
            pass
