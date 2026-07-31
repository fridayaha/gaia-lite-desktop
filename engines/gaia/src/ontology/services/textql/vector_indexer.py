"""VectorIndexer — Phase 2 向量化流水线 (ADR-012 §Step 2 引擎B).

Indexes ontology elements (ObjectType / Property / LinkType) into the Doris
semantic table as L2-normalized embeddings, enabling vector recall (engine B)
for colloquial / synonym queries that exact-match (engine A) misses.

Triggered by OntologyService.define/update hooks (same trigger points as
IndexSyncService for Doris object tables). For each element we embed a
concatenation of display_name + description — description carries multilingual
aliases / synonyms (e.g. "离职率、Turnover Rate、Attrition Rate"), so a single
embedding captures all the ways a user might phrase the concept.

Idempotent: upserts by (ontology, element_type, element_api_name) unique key.
Re-running on an unchanged ontology is a no-op (re-embeds + overwrites, safe).
"""

from __future__ import annotations

import logging
from typing import Any

from ontology.core.schemas.ontology import LinkTypeDef, ObjectType
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.services.textql.embedding import EmbeddingProvider, get_embedding_provider

logger = logging.getLogger(__name__)

# Element type tags stored in the semantic table.
_ET_OBJECT_TYPE = "OBJECT_TYPE"
_ET_PROPERTY = "PROPERTY"
_ET_LINK_TYPE = "LINK_TYPE"


class VectorIndexer:
    """Embeds ontology elements into the Doris semantic table."""

    def __init__(
        self,
        index: DorisIndexStore,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self._index = index
        self._provider = provider  # lazy: resolved on first index_ontology call

    def _get_provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = get_embedding_provider()
        return self._provider

    async def ensure_table(self) -> None:
        """Create the semantic table if absent (idempotent)."""
        if not await self._index.semantic_table_exists():
            await self._index.create_semantic_table(dim=self._get_provider().dim)

    async def index_ontology(
        self,
        ontology_api_name: str,
        object_types: list[ObjectType],
        link_types: list[LinkTypeDef],
    ) -> int:
        """Embed + upsert all elements of one ontology into the semantic table.

        Args:
            ontology_api_name: Owning ontology.
            object_types: All ObjectTypes (with their properties loaded).
            link_types: All LinkTypes.

        Returns:
            Number of rows upserted.
        """
        await self.ensure_table()
        provider = self._get_provider()

        # Build (text, metadata) pairs to embed in one batch.
        rows: list[dict[str, Any]] = []
        texts: list[str] = []

        for ot in object_types:
            # ObjectType: displayName + description.
            text = f"{ot.display_name} {ot.description}".strip()
            texts.append(text)
            rows.append(
                {
                    "ontology_api_name": ontology_api_name,
                    "element_type": _ET_OBJECT_TYPE,
                    "element_api_name": ot.api_name,
                    "display_name": ot.display_name,
                    "description": ot.description or "",
                }
            )
            # Each Property: "OT.displayName propertyDisplayName propertyDescription".
            for p in ot.properties or []:
                ptext = f"{ot.display_name} {p.display_name} {p.description}".strip()
                texts.append(ptext)
                rows.append(
                    {
                        "ontology_api_name": ontology_api_name,
                        "element_type": _ET_PROPERTY,
                        "element_api_name": p.api_name,
                        "display_name": p.display_name,
                        "description": f"{ot.api_name}.{p.api_name}: {p.description or ''}",
                    }
                )

        # LinkTypes: need source/target OT displayNames for context. Build a
        # OT id → displayName map so link text is meaningful.
        ot_id_to_display = {ot.id: ot.display_name for ot in object_types}
        for lt in link_types:
            src = ot_id_to_display.get(lt.source_object_type_id, lt.api_name)
            tgt = ot_id_to_display.get(lt.target_object_type_id, lt.api_name)
            text = f"{src} {lt.display_name} {tgt} {lt.description}".strip()
            texts.append(text)
            rows.append(
                {
                    "ontology_api_name": ontology_api_name,
                    "element_type": _ET_LINK_TYPE,
                    "element_api_name": lt.api_name,
                    "display_name": lt.display_name,
                    "description": f"{src}→{tgt}: {lt.description or ''}",
                }
            )

        # Batch embed (one forward pass for the whole ontology — cheap).
        embeddings = provider.embed(texts)
        for row, emb in zip(rows, embeddings):
            row["embedding"] = emb.tolist()

        await self._index.upsert_semantic_rows(rows)
        # Build the ANN index after the first upsert (idempotent). Done
        # post-insert because inline CREATE-TABLE ANN index triggers a 2GB
        # memtable-load pre-allocation that exceeds the dev container; ALTER
        # ADD INDEX uses a lower-memory path (see build_semantic_index doc).
        try:
            await self._index.build_semantic_index(dim=provider.dim)
        except Exception as e:  # noqa: BLE001 — index build failure non-fatal
            logger.warning("ANN index build failed (recall will be slow/absent): %s", e)
        logger.info(
            "VectorIndexer: indexed %d elements for ontology %s",
            len(rows),
            ontology_api_name,
        )
        return len(rows)
