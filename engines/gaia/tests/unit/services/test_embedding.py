"""Unit tests for OnnxEmbeddingProvider (CPU inference, no torch).

Validates the embedding pipeline end-to-end against the locally-downloaded
ONNX model: tokenization → ONNX forward → mean pooling → L2 normalize.
Skips if the model isn't present (CI without model assets).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ontology.services.textql.embedding import EMBEDDING_DIM, OnnxEmbeddingProvider

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "paraphrase-multilingual-MiniLM-L12-v2"

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "onnx" / "model_qint8_avx512.onnx").exists(),
    reason="ONNX model not downloaded; run the model download step first",
)


class TestOnnxEmbeddingProvider:
    def test_dim_is_384(self) -> None:
        provider = OnnxEmbeddingProvider()
        assert provider.dim == EMBEDDING_DIM == 384

    def test_embed_single_text(self) -> None:
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed(["货运车辆"])
        assert vecs.shape == (1, 384)
        # L2-normalized → norm ≈ 1.0
        assert abs(np.linalg.norm(vecs[0]) - 1.0) < 1e-4

    def test_embed_batch(self) -> None:
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed(["货运车辆", "卡车", "客户订单"])
        assert vecs.shape == (3, 384)
        # All rows normalized.
        norms = np.linalg.norm(vecs, axis=1)
        assert all(abs(n - 1.0) < 1e-4 for n in norms)

    def test_embed_empty_list(self) -> None:
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed([])
        assert vecs.shape == (0, EMBEDDING_DIM)

    def test_semantic_similarity_synonyms_high(self) -> None:
        """Synonyms (卡车 vs Truck) should have high cosine similarity."""
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed(["卡车", "Truck"])
        sim = float(vecs[0] @ vecs[1])
        assert sim > 0.7, f"synonym similarity too low: {sim}"

    def test_semantic_similarity_unrelated_low(self) -> None:
        """Unrelated terms (货运车辆 vs 客户订单) should have low similarity."""
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed(["货运车辆", "客户订单"])
        sim = float(vecs[0] @ vecs[1])
        assert sim < 0.5, f"unrelated similarity too high: {sim}"

    def test_colloquial_match(self) -> None:
        """Colloquial '大车' should still match '货运车辆' reasonably well
        (the core value of engine B — engine A's exact match misses this)."""
        provider = OnnxEmbeddingProvider()
        vecs = provider.embed(["大车", "货运车辆"])
        sim = float(vecs[0] @ vecs[1])
        assert sim > 0.4, f"colloquial match too weak: {sim}"
