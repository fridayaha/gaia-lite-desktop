"""SemanticRecaller — Step 2 of the TextQL pipeline (engine A: exact match).

Maps each business noun in the QueryIR to ontology api_names by exact /
substring matching against ObjectType / Property / LinkType displayName +
description. Produces a RecallResult that backfills the IR with resolved
api_names for Step 3 (schema injection) and Step 4 (tool use / SQL compile).

Phase 1 implements only engine A (exact match, deterministic, zero
hallucination). Engine B (Doris ANN vector search) + HyDE land in Phase 2
(ADR-012 §「分阶段实施计划」).

Design (ADR-012 §「Step 2」):
- Recall is role-partitioned: IR.objects → ObjectType, IR.properties →
  Property, IR.links → LinkType. Each noun is looked up in its matching
  ontology element type — not a flat full-text scan.
- Recall candidates come ONLY from defined ontology elements (guardrail:
  no hallucination). Vector recall (Phase 2) is likewise scoped to the
  ontology semantic table, never external.
- Confidence: exact displayName match = 1.0; exact description token
  match = 0.9; substring displayName = 0.7; substring description = 0.5.
  Below CONFIDENCE_THRESHOLD → needs_clarification or recall_refinement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ontology.core.schemas.ontology import ObjectType
from ontology.core.schemas.textql import (
    CandidateObjectType,
    CandidateProperty,
    QueryIR,
    RecallResult,
)

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7
_EXACT_DISPLAY = 1.0
_DESC_TOKEN = 0.9
_SUBSTR_DISPLAY = 0.7
_SUBSTR_DESC = 0.5
# Vector-recall similarity floor (cosine, since vectors are L2-normalized).
# Below this the vector hit is too weak to surface as a candidate.
_VECTOR_THRESHOLD = 0.5


@dataclass
class _OTIndex:
    """In-memory index of one ObjectType for matching."""

    api_name: str
    display_name: str
    description: str
    properties: dict[str, tuple[str, str]]  # api_name → (display_name, description)


def _score(noun: str, display_name: str, description: str) -> tuple[float, str]:
    """Score a noun against a displayName + description.

    Returns (confidence, evidence). displayName checks (exact > substring)
    take priority over description checks, so a noun matching the
    displayName wins even if it also appears in some description.
    """
    if not noun:
        return 0.0, ""
    noun_norm = noun.strip()
    if noun_norm == display_name:
        return _EXACT_DISPLAY, f"displayName 精确匹配 {display_name!r}"
    if noun_norm in display_name:
        return _SUBSTR_DISPLAY, f"displayName 包含 {noun_norm!r}"
    if description:
        # Description often lists aliases/synonyms separated by 、/, /space
        # (e.g. "离职率、Turnover Rate、Attrition Rate").
        for sep in ("、", ",", "，", " ", "/"):
            tokens = [t.strip() for t in description.split(sep) if t.strip()]
            if noun_norm in tokens:
                return _DESC_TOKEN, f"description 别名匹配 {noun_norm!r}"
        if noun_norm in description:
            return _SUBSTR_DESC, f"description 包含 {noun_norm!r}"
    return 0.0, ""


class SemanticRecaller:
    """Step 2 engine A: exact-match recall from ontology metadata.

    Optionally enhanced by engine B (vector recall) when injected with a
    vector_search callable + embedding provider: nouns that engine A matches
    below CONFIDENCE_THRESHOLD are re-searched via Doris ANN vector similarity
    (colloquial / synonym hits like "大车" → "货运车辆").
    """

    def __init__(
        self,
        object_types: list[ObjectType],
        vector_search: Any | None = None,
    ) -> None:
        self._indexes: list[_OTIndex] = [self._index_ot(ot) for ot in object_types]
        self._object_types = object_types
        # vector_search: async callable (text, top_k) -> list[dict] returning
        # Doris ANN rows {element_type, element_api_name, display_name,
        # description, similarity}. None → engine B disabled (Phase 1 mode).
        self._vector_search = vector_search

    @staticmethod
    def _index_ot(ot: ObjectType) -> _OTIndex:
        props: dict[str, tuple[str, str]] = {}
        for p in ot.properties or []:
            props[p.api_name] = (p.display_name, p.description or "")
        return _OTIndex(
            api_name=ot.api_name,
            display_name=ot.display_name,
            description=ot.description or "",
            properties=props,
        )

    async def recall(self, ir: QueryIR) -> RecallResult:
        """Map IR business nouns to ontology api_names.

        Role-partitioned: IR.objects → ObjectType, IR.properties → Property.
        Object-type recall keeps ALL matching candidates (not just top-1) so
        clarification can fire when two OTs tie at high confidence.

        Engine B (vector) enhancement: when engine A's top OT candidate is
        below CONFIDENCE_THRESHOLD for a noun, run a vector search on that
        noun and merge any new OTs it surfaces (colloquial/synonym hits).
        """
        all_ot_candidates: list[CandidateObjectType] = []
        object_api_names: set[str] = set()
        # Engine A: exact match per object noun.
        low_confidence_nouns: list[str] = []
        for obj_ref in ir.objects:
            cands = self._match_object_types(obj_ref.name)
            if cands:
                top = max(c.confidence for c in cands)
                if top < CONFIDENCE_THRESHOLD:
                    low_confidence_nouns.append(obj_ref.name)
                for cand in cands:
                    if cand.api_name not in object_api_names:
                        all_ot_candidates.append(cand)
                        object_api_names.add(cand.api_name)
            else:
                low_confidence_nouns.append(obj_ref.name)

        # Engine B: vector recall for low-confidence / unmatched nouns.
        if self._vector_search and low_confidence_nouns:
            for noun in low_confidence_nouns:
                vector_cands = await self._vector_recall_object_types(noun)
                for cand in vector_cands:
                    if cand.api_name not in object_api_names:
                        all_ot_candidates.append(cand)
                        object_api_names.add(cand.api_name)

        for prop_ref in ir.properties:
            self._match_property(prop_ref.name, all_ot_candidates)

        top_conf = max((c.confidence for c in all_ot_candidates), default=0.0)
        needs_clarification = self._needs_clarification(all_ot_candidates, top_conf)

        if all_ot_candidates and any(c.confidence < CONFIDENCE_THRESHOLD for c in all_ot_candidates):
            ir.needs_recall_refinement = True

        logger.info(
            "Recall: %d OT candidates (top conf %.2f), clarification=%s",
            len(all_ot_candidates),
            top_conf,
            needs_clarification,
        )
        return RecallResult(
            object_types=all_ot_candidates,
            needs_clarification=needs_clarification,
        )

    async def _vector_recall_object_types(self, noun: str) -> list[CandidateObjectType]:
        """Engine B: vector-search a noun → ObjectType candidates."""
        if not self._vector_search:
            return []
        try:
            rows = await self._vector_search(noun, top_k=5)
        except Exception as e:  # noqa: BLE001 — vector failure non-fatal
            logger.warning("Vector recall failed for %r: %s", noun, e)
            return []
        cands: list[CandidateObjectType] = []
        for row in rows:
            if row.get("element_type") != "OBJECT_TYPE":
                continue
            sim = float(row.get("similarity", 0.0))
            if sim < _VECTOR_THRESHOLD:
                continue
            cands.append(
                CandidateObjectType(
                    api_name=row["element_api_name"],
                    display_name=row.get("display_name", ""),
                    confidence=sim,
                    match_evidence=f"向量相似度 {sim:.3f}",
                    source="vector",
                    matched_properties=[],
                )
            )
        return cands

    def _match_object_types(self, noun: str) -> list[CandidateObjectType]:
        """Return ALL OTs matching the noun, sorted by confidence desc.

        Keeping multiple candidates enables clarification when two OTs tie
        at high confidence (ambiguous noun).
        """
        hits: list[tuple[float, str, _OTIndex]] = []
        for idx in self._indexes:
            score, evidence = _score(noun, idx.display_name, idx.description)
            if score > 0:
                hits.append((score, evidence, idx))
        if not hits:
            return []
        hits.sort(key=lambda x: x[0], reverse=True)
        return [
            CandidateObjectType(
                api_name=idx.api_name,
                display_name=idx.display_name,
                confidence=score,
                match_evidence=evidence,
                source="exact",
                matched_properties=[],
            )
            for score, evidence, idx in hits
        ]

    def _match_property(self, noun: str, ot_candidates: list[CandidateObjectType]) -> None:
        """Find the best-matching property across candidate OTs and attach it."""
        best: tuple[float, str, str, str, CandidateObjectType] | None = None
        for cand in ot_candidates:
            idx = next((i for i in self._indexes if i.api_name == cand.api_name), None)
            if idx is None:
                continue
            for prop_api, (prop_display, prop_desc) in idx.properties.items():
                score, evidence = _score(noun, prop_display, prop_desc)
                if score > 0 and (best is None or score > best[0]):
                    best = (score, evidence, prop_api, prop_display, cand)
        if best is None:
            return
        score, evidence, prop_api, prop_display, cand = best
        cand.matched_properties.append(
            CandidateProperty(
                api_name=prop_api,
                display_name=prop_display,
                object_type_api_name=cand.api_name,
                confidence=score,
                match_evidence=evidence,
                source="exact",
            )
        )

    @staticmethod
    def _needs_clarification(ot_candidates: list[CandidateObjectType], top_conf: float) -> bool:
        """Clarify when multiple OTs tie at high confidence (ambiguous)."""
        if len(ot_candidates) < 2:
            return False
        high_conf = [c for c in ot_candidates if c.confidence >= CONFIDENCE_THRESHOLD]
        if len(high_conf) < 2:
            return False
        sorted_conf = sorted((c.confidence for c in high_conf), reverse=True)
        return sorted_conf[0] - sorted_conf[1] < 0.05
