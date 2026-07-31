"""EmbeddingProvider — pluggable text→vector embedding for semantic recall.

Phase 2 default impl: ONNX Runtime + paraphrase-multilingual-MiniLM-L12-v2
(384-dim, CPU, ~15ms/sentence). No torch / sentence-transformers dependency
— uses the ONNX-quantized model file directly + the `tokenizers` library for
tokenization.

Design (ADR-012 §「Step 2 引擎B」):
- Protocol-based so the recall layer is decoupled from the embedding impl.
  Future swaps (bge-m3, API-based, GPU) just implement EmbeddingProvider.
- Vectors are L2-normalized at output so Doris ANN can use inner_product
  directly as cosine similarity (Doris 4.x doesn't natively support cosine;
  normalized inner_product is the documented equivalent).
- CPU-only, no GPU assumption (per project constraint). MiniLM-L12-v2 is
  purpose-built for CPU — 6 layers, ~120M params, ~15ms/sentence on a
  commodity CPU.
- Thread-safe: a single ONNX InferenceSession is reused across calls
  (onnxruntime sessions are thread-safe for inference).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ontology.config.settings import settings

if TYPE_CHECKING:
    # numpy 仅作类型注解（`from __future__ import annotations` 使注解惰性求值）。
    # 运行时用到时在方法内局部 import，避免顶层拉 onnxruntime/numpy 重依赖使
    # lite 版无法 import 本模块（A3）。
    import numpy as np

logger = logging.getLogger(__name__)

# Default model path (relative to project root). The model dir holds the
# ONNX quantized file + tokenizer.json. Other weight formats in the dir
# (pytorch_model.bin, tf_model.h5, openvino/, etc.) are NOT loaded —
# onnxruntime only reads the one .onnx file we point it at.
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
_DEFAULT_ONNX = "onnx/model_qint8_avx512.onnx"  # quantized, CPU-optimal
EMBEDDING_DIM = 384
_MAX_SEQ_LEN = 128


class EmbeddingProvider(Protocol):
    """Pluggable text→vector embedding interface for semantic recall."""

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts → (n, EMBEDDING_DIM) L2-normalized vectors.

        Vectors MUST be L2-normalized so Doris ANN inner_product == cosine.
        """
        ...

    @property
    def dim(self) -> int:
        """Embedding dimensionality (384 for MiniLM-L12-v2)."""
        ...


class OnnxEmbeddingProvider:
    """Default EmbeddingProvider: ONNX Runtime + MiniLM-L12-v2 on CPU.

    Loads the quantized ONNX model once at construction; subsequent embed()
    calls reuse the session (thread-safe). Tokenization uses the `tokenizers`
    library (no transformers dependency).
    """

    def __init__(
        self,
        model_dir: Path | str | None = None,
        onnx_file: str = _DEFAULT_ONNX,
    ) -> None:
        model_dir = Path(model_dir) if model_dir else self._resolve_model_dir()
        onnx_path = model_dir / onnx_file
        if not onnx_path.exists():
            raise FileNotFoundError(f"ONNX 模型文件不存在: {onnx_path}. 请确认模型已下载到 models/ 目录")
        tokenizer_path = model_dir / "tokenizer.json"
        if not tokenizer_path.exists():
            raise FileNotFoundError(f"tokenizer.json 不存在: {tokenizer_path}")

        # CPU-only provider (project has no GPU).
        # onnxruntime/tokenizers 是 full 版重依赖（embedding 推理），lite 版不装；
        # 局部 import 使 `import ontology.services.textql.embedding` 在 lite 下不报错（A3）。
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_padding(length=_MAX_SEQ_LEN, pad_id=0, pad_token="[PAD]")
        self._tokenizer.enable_truncation(max_length=_MAX_SEQ_LEN)
        logger.info("OnnxEmbeddingProvider loaded: %s (dim=%d, CPU)", onnx_path.name, EMBEDDING_DIM)

    @staticmethod
    def _resolve_model_dir() -> Path:
        """Resolve the model dir from settings override or the default path."""
        override = getattr(settings, "embedding_model_dir", None) or os.getenv("EMBEDDING_MODEL_DIR")
        if override:
            return Path(override)
        return _DEFAULT_MODEL_DIR

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed texts → (n, 384) L2-normalized float32 vectors.

        Empty input returns a (0, dim) array. Mean-pools the token-level
        outputs (masked by attention_mask) then L2-normalizes — the
        standard sentence-transformers pooling for this model.
        """
        # numpy 仅在真正计算向量时需要（full 版运行时）；局部 import 避免 lite 版
        # 顶层拉 numpy（A3）。
        import numpy as np

        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        enc = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in enc], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in enc], dtype=np.int64)
        out = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]  # (batch, seq, 384)
        # Mean pooling with attention mask.
        mask = attention_mask[..., None].astype(np.float32)
        summed = (out * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        pooled = summed / counts
        # L2 normalize (cosine via inner_product on Doris).
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
        normalized: np.ndarray = (pooled / norms).astype(np.float32)
        return normalized


# Module-level lazy singleton — constructed on first use so the model loads
# only when semantic recall (engine B) is actually invoked. Tests that don't
# touch vector recall never pay the load cost.
_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Get the shared EmbeddingProvider (lazy singleton)."""
    global _provider
    if _provider is None:
        _provider = OnnxEmbeddingProvider()
    return _provider


def reset_embedding_provider(provider: EmbeddingProvider | None = None) -> None:
    """Override/reset the provider (for tests)."""
    global _provider
    _provider = provider
