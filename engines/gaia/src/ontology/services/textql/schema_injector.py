"""SchemaInjector — Step 3 of the TextQL pipeline.

Deterministically injects the full schema of candidate ObjectTypes into the
LLM context before tool use (Step 4). This is the "用本体驯化 LLM" guardrail
from the reference material — the LLM sees only ontology-defined entities,
properties, and links, so it cannot hallucinate fields.

Design (ADR-012 §「Step 3」):
- Triggered on every user message (deterministic retrieval context, per
  reference material). Runs after Step 2 recall narrows candidates.
- Renders six categories per ObjectType: base info, Properties (with
  types + constraints), LinkTypes, data-type constraints, business
  constraints, relationships. Mirrors reference material §「Schema 注入」.
- Caps injected ObjectTypes (MAX_INJECT) to keep token budget bounded.
- The three guardrails (entity / field / relationship) are enforced
  downstream by the SQL compiler's whitelist + the tool signatures, but
  the injected schema is what makes the LLM stay inside the lines.
"""

from __future__ import annotations

import logging

from ontology.core.schemas.ontology import LinkTypeDef, ObjectType
from ontology.core.schemas.textql import RecallResult

logger = logging.getLogger(__name__)

# Cap injected ObjectTypes to bound token usage (reference material: 1-25,
# default 5). We use 8 as a balance for multi-object queries.
MAX_INJECT_OBJECT_TYPES = 8


class SchemaInjector:
    """Renders candidate ObjectType schemas as a context block for the LLM."""

    def __init__(self, link_types: list[LinkTypeDef] | None = None) -> None:
        # link_types passed in so we can list per-OT relationships without
        # another DB round-trip (caller already loaded them for recall).
        self._link_types = link_types or []
        # Index: ObjectType api_name → list of (link_api, other_ot_api, cardinality)
        self._links_by_ot: dict[str, list[tuple[str, str, str]]] = {}
        self._index_links()

    def _index_links(self) -> None:
        """Build a per-OT link index. LinkTypeDef uses source/target UUIDs,
        which the caller must have resolved to api_names before constructing
        LinkTypeDef — but the schema model carries api_name + the UUIDs.
        We rely on the caller passing LinkTypeDef with resolved names via
        a lightweight wrapper; for the schema model as-is, we index by
        the link's display_name and cardinality, deferring OT resolution to
        the caller (see build_from_object_types)."""
        # The LinkTypeDef schema has source_object_type_id/target_object_type_id
        # (UUIDs), not api_names. For Phase 1 we render links generically;
        # precise per-OT link listing requires a UUID→api_name map the caller
        # provides. See build_context_block usage in routes/ai.py.
        pass

    def build_context_block(
        self,
        object_types: list[ObjectType],
        recall: RecallResult,
        ot_id_to_api: dict[str, str] | None = None,
    ) -> str:
        """Render the schema block for the top candidate ObjectTypes.

        Args:
            object_types: All ObjectTypes in the ontology (caller loads once).
            recall: Step 2 result — candidates ordered by confidence.
            ot_id_to_api: Optional UUID → api_name map for resolving LinkType
                source/target to api_names (enables per-OT link listing).

        Returns:
            A markdown-formatted schema block to prepend to the LLM context.
        """
        # Order: recall candidates first (by confidence), then fill up to
        # MAX_INJECT_OBJECT_TYPES with other OTs the user might reference.
        ot_by_api = {ot.api_name: ot for ot in object_types}
        ordered: list[ObjectType] = []
        seen: set[str] = set()
        for cand in recall.object_types:
            ot = ot_by_api.get(cand.api_name)
            if ot and ot.api_name not in seen:
                ordered.append(ot)
                seen.add(ot.api_name)
        for ot in object_types:
            if len(ordered) >= MAX_INJECT_OBJECT_TYPES:
                break
            if ot.api_name not in seen:
                ordered.append(ot)
                seen.add(ot.api_name)

        blocks = [self._render_object_type(ot, ot_id_to_api) for ot in ordered]
        header = (
            "# 本体 Schema（查询约束）\n\n"
            "你只能查询以下已定义的 ObjectType 及其 Property。禁止编造未列出的对象、字段或关系。\n"
            "字段名用 api_name（PascalCase/camelCase），表名用 ObjectType api_name。\n\n"
        )
        return header + "\n\n---\n\n".join(blocks)

    def _render_object_type(self, ot: ObjectType, ot_id_to_api: dict[str, str] | None) -> str:
        """Render one ObjectType's full schema (six categories)."""
        lines = [
            f"## ObjectType: {ot.api_name} (displayName: {ot.display_name})",
            f"description: {ot.description}" if ot.description else "description: (无)",
            f"primary_key: {ot.primary_key}  title_property: {ot.title_property}",
            "",
            "### Properties (只能用以下字段，禁止编造):",
        ]
        for p in ot.properties or []:
            constraints: list[str] = []
            if not p.nullable:
                constraints.append("required")
            if p.is_primary_key:
                constraints.append("PK")
            if p.is_title_property:
                constraints.append("title")
            constraint_str = f" [{', '.join(constraints)}]" if constraints else ""
            desc = f"  # {p.description}" if p.description else ""
            lines.append(f"- {p.api_name} ({p.data_type}{constraint_str}, displayName={p.display_name}){desc}")
        if not ot.properties:
            lines.append("- (无属性)")

        # Links involving this OT (if UUID→api_name map provided).
        if ot_id_to_api:
            my_links = self._links_for_ot(ot.id, ot_id_to_api)
            if my_links:
                lines.append("")
                lines.append("### Links (跨对象查询只能走以下关系):")
                for link_api, other_api, cardinality in my_links:
                    lines.append(f"- {link_api} → {other_api} ({cardinality})")
        return "\n".join(lines)

    def _links_for_ot(self, ot_id: str, ot_id_to_api: dict[str, str]) -> list[tuple[str, str, str]]:
        """List links involving one ObjectType, resolved to api_names."""
        result: list[tuple[str, str, str]] = []
        for lt in self._link_types:
            if lt.source_object_type_id == ot_id:
                other = ot_id_to_api.get(lt.target_object_type_id)
                if other:
                    result.append((lt.api_name, other, lt.cardinality))
            elif lt.target_object_type_id == ot_id:
                other = ot_id_to_api.get(lt.source_object_type_id)
                if other:
                    result.append((lt.api_name, other, lt.cardinality))
        return result
