"""AI generate/stream primitives — AI SDK-style LLM calls without task awareness.

Exposes two functions mirroring Vercel AI SDK's ``generateText`` / ``streamText``:

- :func:`generate_text`: non-streaming. Returns the complete text. Use for
  fast, structured-output tasks (e.g. deriving an apiName — sub-second).
- :func:`stream_text`: streaming. Yields text deltas. Use for long-form
  generation where incremental display matters.

Both take ``instructions`` (system prompt, optional) + ``prompt`` (user
prompt, required) — the same minimal surface as AI SDK. The backend does
NOT perceive task semantics: what the prompt asks for, how to parse the
output, is entirely the caller's concern. This keeps the endpoint a
general LLM primitive (contrast with ``/ai/agent``, the AG-UI streaming
assistant that mounts 13 ontology tools and owns HITL).

Implementation: a lightweight ``pydantic_ai.Agent`` is constructed
per-request with ``system_prompt=instructions``. No toolset is mounted
(these calls don't use tools), no ``output_type`` (output is plain text),
no conversation state. Model inference (``infer_model``) is cached by
pydantic-ai for repeated ``settings.ai_model`` strings, so per-request
construction is cheap.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent

from ontology.config.settings import settings

_log = logging.getLogger(__name__)


def _build_agent(instructions: str | None) -> Agent[None, str]:
    """Construct a per-request lightweight Agent bound to ``instructions``.

    ``system_prompt`` is fixed at Agent construction time, so a fresh Agent
    is built per call (cheap — model inference is cached). No tools, no
    structured output type, no deps.
    """
    return Agent(
        settings.ai_model,
        system_prompt=instructions or "",
        # Plain text output: the backend does not parse task-specific schemas.
        output_type=str,
        retries=settings.ai_retries,
        defer_model_check=True,
    )


async def generate_text(instructions: str | None, prompt: str) -> str:
    """Non-streaming text generation (AI SDK ``generateText`` equivalent).

    Runs the model to completion and returns the full text. Best for fast,
    deterministic-shape outputs (e.g. camelCase apiName derivation).
    """
    agent = _build_agent(instructions)
    try:
        result = await agent.run(prompt)
        return result.output
    except Exception as e:  # noqa: BLE001 — surface LLM errors to the caller
        _log.warning("generate_text failed: %s", e)
        raise


async def stream_text(instructions: str | None, prompt: str) -> AsyncIterator[str]:
    """Streaming text generation (AI SDK ``streamText`` equivalent).

    Yields text deltas as they arrive. Best for long-form generation where
    incremental display improves perceived latency.
    """
    agent = _build_agent(instructions)
    try:
        async with agent.run_stream(prompt) as result:
            async for delta in result.stream_text():
                yield delta
    except Exception as e:  # noqa: BLE001 — surface LLM errors in the stream
        _log.warning("stream_text failed: %s", e)
        raise


StructuredT = TypeVar("StructuredT", bound=BaseModel)


# noqa: UP047 — PEP 695 generic syntax unsupported by pre-commit's pinned mypy
async def stream_structured(  # noqa: UP047
    output_type: type[StructuredT],
    instructions: str,
    prompt: str,
) -> AsyncIterator[StructuredT]:
    """Structured streaming output — AI SDK ``streamObject`` equivalent.

    Forces the model to return data matching ``output_type`` (a Pydantic
    model) via pydantic-ai's default Tool Output mode (tool calling), and
    streams partial objects as they arrive. Pydantic validates each partial;
    ``ModelRetry`` auto-retries on schema violation.

    Reusable across any structured-streaming task (object scaffolding,
    relationship inference, semantic enrichment, …). The caller owns the
    schema and prompt; this function owns only the structured-streaming
    mechanism.

    Args:
        output_type: Pydantic model class defining the result schema.
        instructions: System prompt (task description + schema constraints).
        prompt: User prompt (concrete input data).

    Yields:
        Partial ``output_type`` instances — progressively more complete as
        the model streams. The final yield is the complete object.
    """
    agent = Agent(
        settings.ai_model,
        system_prompt=instructions,
        output_type=output_type,
        retries=settings.ai_retries,
        defer_model_check=True,
    )
    try:
        async with agent.run_stream(prompt) as result:
            # debounce_by=0.3: batch partial emissions within 300ms so the
            # frontend isn't flooded with a frame per token (pydantic-ai's
            # default None emits hundreds of frames for a small object).
            # Fewer, more complete frames = less React re-render churn and a
            # stabler progressive-fill UX.
            async for partial in result.stream_output(debounce_by=0.3):
                yield partial
    except Exception as e:  # noqa: BLE001 — surface LLM errors to the caller
        _log.warning("stream_structured failed: %s", e)
        raise
