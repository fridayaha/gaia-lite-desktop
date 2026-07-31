"""Unit tests for /ai/generate and /ai/stream (AI SDK-style primitives).

Uses pydantic-ai's ``TestModel`` to avoid real LLM calls. The Agent built
by ``ai_generate`` reads ``settings.ai_model``; we monkeypatch it to a
TestModel instance so generation is deterministic and offline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from ontology.services import ai_generate


@pytest.fixture
def test_model(monkeypatch: pytest.MonkeyPatch) -> TestModel:
    """Patch settings.ai_model to a TestModel that echoes a fixed string."""
    model = TestModel(custom_output_text="delayFlight")
    # ai_generate._build_agent reads settings.ai_model at call time (it's an
    # attribute lookup on the imported instance), so patching the instance
    # attribute is enough — no need to rebind the module-level reference.
    from ontology.config.settings import settings

    monkeypatch.setattr(settings, "ai_model", model)
    return model


class TestGenerateText:
    @pytest.mark.asyncio
    async def test_generate_text_returns_output(self, test_model: TestModel) -> None:
        """generate_text returns the model's text output."""
        text = await ai_generate.generate_text(
            instructions="You are a naming expert.",
            prompt="Derive a camelCase apiName for 延误航班",
        )
        assert text == "delayFlight"

    @pytest.mark.asyncio
    async def test_generate_text_without_instructions(self, test_model: TestModel) -> None:
        """instructions=None is accepted (empty system prompt)."""
        text = await ai_generate.generate_text(None, "hello")
        assert text == "delayFlight"

    @pytest.mark.asyncio
    async def test_generate_text_propagates_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM errors surface to the caller."""

        class _FailingModel(TestModel):
            async def request(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
                raise RuntimeError("model unavailable")

        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "ai_model", _FailingModel())
        with pytest.raises(RuntimeError, match="model unavailable"):
            await ai_generate.generate_text(None, "hello")


class TestStreamText:
    @pytest.mark.asyncio
    async def test_stream_text_yields_deltas(self, test_model: TestModel) -> None:
        """stream_text yields text deltas as an async iterator."""
        deltas: list[str] = []
        async for delta in ai_generate.stream_text(None, "hello"):
            deltas.append(delta)
        # TestModel custom_output_text is emitted as one or more deltas;
        # the concatenated result equals the full output.
        assert "".join(deltas) == "delayFlight"

    @pytest.mark.asyncio
    async def test_stream_text_is_async_iterator(self, test_model: TestModel) -> None:
        """stream_text returns an AsyncIterator (AI SDK streamText equivalent)."""
        result = ai_generate.stream_text(None, "hello")
        assert isinstance(result, AsyncIterator)
        # Drain to avoid "never awaited" warnings.
        async for _ in result:
            pass
