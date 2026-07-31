"""Unit tests for runtime-context injection (current date + schema block).

Background: the ontology assistant (AG-UI Agent) used to hallucinate the
current date — when a user asked "统计今日外呼数", the LLM had no "today" in
its context, so it guessed a date from the queried data range ("该本体数据
日期范围 ... 因此以 ... 为今日") or fabricated a date literal that failed the
storage-layer type check (概率性工具报错).

Root cause (discovered 2026-06): runtime context was first added via
``@agent.system_prompt`` decorators. But the AG-UI path runs
``manage_system_prompt='client'``: the frontend owns the system prompt
(its SystemMessage is preserved), and pydantic-ai only injects the agent's
``@system_prompt`` decorators when ``message_history`` is EMPTY
(``pydantic_ai._agent_graph`` line ~373: ``if not messages: parts.extend(
await self._sys_parts(...))``). Since the frontend always sends a
SystemMessage, the decorators were silently dropped on the real AG-UI path
— they fired in unit tests (which pass no history) but not in production,
so the date hallucination persisted.

Fix: inject runtime context via the agent's ``instructions`` parameter
(``InstructionPart``), which pydantic-ai resolves on EVERY
``ModelRequestNode`` (line ~906) independent of client/server mode. This
channel reliably reaches the LLM every turn. Used for both the current
date (basic runtime fact) and the TextQL schema block (the "用本体驯化 LLM"
guardrail) — the latter was ALSO silently dropped under client mode before
this fix.

The date mirrors pi's own ``Current date: YYYY-MM-DD`` injection: one line
is enough; the LLM derives "今日/本月/最近N天" itself (CLAUDE.md red line 7:
don't pile business logic into the prompt).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ag_ui.core import RunAgentInput, SystemMessage, UserMessage
from pydantic_ai.messages import InstructionPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from ontology.services.ai_agent import agent, build_agent
from ontology.tools.state import AppState


def _today_bj() -> str:
    return (datetime.now(UTC) + timedelta(hours=8)).strftime("%Y-%m-%d")


def test_build_agent_registers_instructions_not_system_prompt_decorators() -> None:
    """Runtime context uses `instructions`, NOT `@system_prompt` decorators.

    Regression guard: `@system_prompt` decorators are dropped under
    `manage_system_prompt='client'` (the AG-UI path) when the frontend sends
    a SystemMessage. If anyone moves the date/schema back to a decorator,
    this test fails and flags the silent-drop regression.
    """
    a = build_agent()
    # instructions channel is populated...
    instr_names = {getattr(f, "__name__", str(f)) for f in a._instructions}
    assert "_current_date_instruction" in instr_names, f"date instruction missing; got {instr_names}"
    assert "_injected_schema_instruction" in instr_names, f"schema instruction missing; got {instr_names}"
    # ...and NO system_prompt decorators are registered (they'd be dropped on
    # the AG-UI path anyway).
    assert a._system_prompt_functions == [], (
        f"unexpected @system_prompt decorators {a._system_prompt_functions} — these are dropped "
        "under manage_system_prompt='client'; use `instructions` instead"
    )


@pytest.mark.asyncio
async def test_instructions_resolve_to_current_date_and_schema() -> None:
    """Resolving instructions yields today's date (and schema when present).

    Uses `agent.system_prompt_parts` is NOT appropriate here (it only covers
    the system_prompt channel, not instructions). Instead we drive a real
    (TestModel) run with NO message_history — the simplest path that still
    resolves instructions — and read `instruction_parts` off the recorded
    request parameters.
    """
    a = build_agent()
    tm = TestModel(call_tools=[], custom_output_text="done")
    deps = AppState(ontology="marketing")
    deps.injected_schema = "# 本体 Schema\n## ObjectType: CallRecord"

    try:
        await a.run("ping", deps=deps, model=tm)
    except Exception:  # noqa: BLE001 — TestModel output shape isn't the point
        pass

    parts = tm.last_model_request_parameters.instruction_parts
    contents = " ".join(p.content for p in parts)
    today = _today_bj()
    assert today in contents, f"today {today} missing from instructions: {contents!r}"
    assert "Current date:" in contents
    assert "Beijing time" in contents
    assert "ObjectType: CallRecord" in contents  # schema block also injected


@pytest.mark.asyncio
async def test_date_injected_under_client_mode_with_frontend_system_message() -> None:
    """THE core regression test: date reaches the LLM under client mode.

    This is the exact production scenario that used to fail: the frontend
    sends a SystemMessage (its static `buildOntologyQueryPrompt`), the route
    runs `manage_system_prompt='client'`. Under the old `@system_prompt`
    approach the date decorator was dropped (history non-empty → decorators
    skipped). Under the `instructions` approach the date is injected as an
    InstructionPart on every ModelRequestNode regardless of mode.
    """
    tm = TestModel(call_tools=[], custom_output_text="done")
    msgs = [
        SystemMessage(id="s1", content="You are an ontology assistant (frontend static prompt, no date)"),
        UserMessage(id="u1", content="统计今日外呼数"),
    ]
    run_input = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        messages=msgs,
        state={},
        tools=[],
        context=[],
        forwardedProps={"ontology": "Marketing"},
    )
    deps = AppState(ontology="Marketing")
    deps.injected_schema = ""  # no schema scoped — date must still inject
    adapter = AGUIAdapter(agent, run_input, manage_system_prompt="client")

    try:
        async for _ev in adapter.run_stream_native(deps=deps, model=tm):
            pass
    except Exception:  # noqa: BLE001 — streaming quirk not under test
        pass

    parts: list[InstructionPart] = tm.last_model_request_parameters.instruction_parts
    contents = " ".join(p.content for p in parts)
    today = _today_bj()
    assert today in contents, (
        f"date {today} NOT injected under client mode — the silent-drop regression is back.\n"
        f"instruction_parts: {[p.content for p in parts]!r}"
    )


@pytest.mark.asyncio
async def test_schema_block_injected_under_client_mode() -> None:
    """The TextQL schema guardrail also survives client mode.

    Before this fix the schema-block `@system_prompt` decorator was dropped
    on the AG-UI path too — meaning the "用本体驯化 LLM" guardrail was silently
    inactive in production. This test pins that it now reaches the LLM.
    """
    tm = TestModel(call_tools=[], custom_output_text="done")
    msgs = [
        SystemMessage(id="s1", content="frontend prompt"),
        UserMessage(id="u1", content="list orders"),
    ]
    run_input = RunAgentInput(
        thread_id="t1", run_id="r1", messages=msgs, state={}, tools=[], context=[], forwardedProps={"ontology": "M"}
    )
    deps = AppState(ontology="M")
    deps.injected_schema = "# 本体 Schema（查询约束）\n## ObjectType: Order"
    adapter = AGUIAdapter(agent, run_input, manage_system_prompt="client")

    try:
        async for _ev in adapter.run_stream_native(deps=deps, model=tm):
            pass
    except Exception:  # noqa: BLE001
        pass

    parts = tm.last_model_request_parameters.instruction_parts
    contents = " ".join(p.content for p in parts)
    assert "ObjectType: Order" in contents, f"schema block dropped under client mode: {contents!r}"
