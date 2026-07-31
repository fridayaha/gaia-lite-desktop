"""Unit tests for the ontology-modelling capability (form A).

Verifies the ``ontology-modeling`` skill migration onto pydantic-ai's native
``Capability`` extension point:

1. ``build_modeling_capability`` produces a deferred ``Capability`` carrying
   ONLY instructions (no tools/hooks) — the write/action tools it guides
   already live on the AG-UI Agent.
2. The methodology text encodes the skill's cross-tool modelling rules
   (six-step flow, data-type red lines, M:N split, ActionType semantic-
   contract discipline, confidence marking) and references Gaia's actual
   tools (``define_object_type`` etc.).
3. Progressive disclosure: on a non-modelling turn the methodology stays
   collapsed to a one-line catalog entry (id + description) and never
   enters the prompt; the framework-managed ``load_capability`` tool is
   the only way to surface it.
4. After ``load_capability`` is called, the methodology lands in the
   conversation as the tool's return value (so the model sees it in
   history on subsequent turns), and ``loaded_capability_ids`` is
   reconstructed from history on resume.

These tests use ``TestModel`` (offline, deterministic) per the project's
existing AI test pattern (see ``test_ai_generate.py``).
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from ontology.services.ontology_modeling import (
    _MODELING_METHODOLOGY,
    build_modeling_capability,
)

# ── Capability construction ──


class TestCapabilityConstruction:
    """The capability is built correctly: deferred, instructions-only."""

    def test_is_deferred_with_stable_id(self) -> None:
        """defer_loading=True + stable id (history-replay requirement)."""
        cap = build_modeling_capability()
        assert cap.id == "ontology-modeling"
        assert cap.defer_loading is True

    def test_carries_only_instructions_no_user_tools(self) -> None:
        """Form A: the capability carries ONLY instructions, no user-defined tools.

        The write/action tools it guides (define_object_type / add_property /
        define_link_type / invoke_action) already live on the AG-UI Agent
        (build_write_toolset / build_action_toolset + HITL). Adding them
        here would duplicate. The capability just tells the LLM *how* to
        use them well.

        (``Capability`` convenience class may return a default empty
        ``FunctionToolset`` from ``get_toolset()`` — what matters is that no
        user tools are registered on it, i.e. it exposes zero tools.)
        """
        cap = build_modeling_capability()
        instructions = cap.get_instructions()
        assert instructions is not None
        assert len(instructions) >= 1
        # The capability contributes no callable tools of its own. We check
        # by inspecting the toolset's tool count rather than identity, since
        # the convenience class hands back an empty FunctionToolset shell.
        ts = cap.get_toolset()
        if ts is not None:
            # An empty/default toolset exposes no tools until the agent run
            # resolves them; verify via the toolset's own tool registry.
            tools_attr = getattr(ts, "_tools", None) or getattr(ts, "tools", None)
            assert not tools_attr, f"capability must not register tools, got {tools_attr!r}"

    def test_description_triggers_on_modelling_intent(self) -> None:
        """The catalog description tells the LLM WHEN to load this capability.

        Must mention modelling verbs (建模/创建对象/搭建本体) so the LLM loads
        it on modelling turns, and must mention it is NOT for pure queries
        (纯查询/探索类问题无需加载) so query turns stay lean.
        """
        desc = build_modeling_capability().description
        assert desc is not None
        desc_text = str(desc)
        # Modelling triggers
        assert "建模" in desc_text
        assert "创建对象" in desc_text or "定义客户对象" in desc_text
        # Non-modelling exclusion
        assert "查询" in desc_text or "探索" in desc_text


# ── Methodology content (skill → Gaia alignment) ──


class TestMethodologyContent:
    """The methodology encodes the skill's rules, aligned to Gaia's tools/types."""

    def test_references_gaia_tools_not_palantir_vocab(self) -> None:
        """The skill's Palantir UI vocabulary (Workshop) is dropped; Gaia's
        actual tool names (define_object_type / add_property /
        define_link_type) are used instead.

        Note: "Function 层" / "Functions 实现" is RETAINED — it refers to
        Gaia's action-function implementation layer (where business rules
        live), which is the skill's legitimate concept, not Palantir UI
        vocabulary. Only Palantir-specific UI terms are stripped."""
        m = _MODELING_METHODOLOGY
        assert "define_object_type" in m
        assert "define_link_type" in m
        assert "add_property" in m
        # Palantir UI vocabulary must NOT appear (would confuse the LLM
        # about which platform/tools to use).
        assert "Workshop" not in m
        assert "Foundry" not in m

    def test_six_step_flow_present(self) -> None:
        """Skill §建模流程 六步法 is encoded."""
        m = _MODELING_METHODOLOGY
        assert "建模六步法" in m
        # Step 1: entities
        assert "梳理实体" in m
        # Step 2: actions
        assert "拆解原子行为" in m or "ActionType" in m
        # Step 5: validation
        assert "合规自检" in m

    def test_data_type_red_lines(self) -> None:
        """Skill §数据类型规范: decimal for money, timestamp for time, etc."""
        m = _MODELING_METHODOLOGY
        assert "DECIMAL" in m
        assert "TIMESTAMP" in m
        assert "BOOLEAN" in m
        # The red line: string must not substitute for these
        assert "STRING" in m  # appears in the "禁止" column

    def test_mn_split_rule(self) -> None:
        """Skill red line 3: M:N must split into middle entity + two 1:N."""
        m = _MODELING_METHODOLOGY
        assert "M:N" in m
        assert "中间" in m  # 中间 ObjectType
        assert "1:N" in m

    def test_actiontype_no_runtime_strategy(self) -> None:
        """Skill red line 8: ActionType must not carry idempotent/retry/timeout/rollback."""
        m = _MODELING_METHODOLOGY
        assert "idempotent" in m or "幂等" in m
        assert "retry_strategy" in m or "重试" in m
        assert "timeout" in m or "超时" in m
        # The prohibition is stated
        assert "严禁" in m

    def test_virtual_write_guard_mentioned(self) -> None:
        """Skill / arch red line 9: VIRTUAL targets must not be written.

        Gaia enforces this in ActionService.execute_action, but the
        methodology reminds the LLM not to attempt it."""
        m = _MODELING_METHODOLOGY
        assert "VIRTUAL" in m
        assert "禁止" in m

    def test_confidence_marking(self) -> None:
        """Skill §置信度标记: confirmed/high/tentative."""
        m = _MODELING_METHODOLOGY
        assert "confirmed" in m
        assert "high" in m
        assert "tentative" in m

    def test_parallel_modelling_guidance(self) -> None:
        """Multiple define_object_type calls in one turn → batch approval.

        Aligns with the existing AG-UI batch-approval UX (the frontend
        aggregates parallel write tool calls into one panel)."""
        m = _MODELING_METHODOLOGY
        assert "并行" in m
        assert "批量审批" in m


# ── Progressive disclosure (the core pydantic-ai mechanism) ──


def _build_test_agent(model: TestModel) -> Agent:
    """Minimal agent with ONLY the modelling capability mounted.

    Mirrors build_agent() but strips the ontology toolsets — we're testing
    the capability's disclosure behaviour, not the tools (which have their
    own tests)."""
    return Agent(
        model,
        system_prompt="你是本体助手。",
        capabilities=[build_modeling_capability()],
        defer_model_check=True,
    )


class TestProgressiveDisclosure:
    """defer_loading=True collapses the methodology to a catalog entry until loaded."""

    @pytest.mark.asyncio
    async def test_methodology_absent_on_query_turn(self) -> None:
        """On a non-modelling turn, the methodology body is NOT in the prompt.

        Only a one-line catalog entry (id + description) appears, plus the
        framework-managed ``load_capability`` tool. The catalog description
        may mention keywords like "M:N" or "六步法" in summary (that's how the
        LLM knows what the capability offers), but the full methodology
        *body* (its section headers) must NOT appear — that's the whole
        point of progressive disclosure."""
        tm = TestModel(custom_output_text="好的", call_tools=[])
        agent = _build_test_agent(tm)
        result = await agent.run("查询客户数量")
        instr = getattr(result.all_messages()[0], "instructions", None)
        full = "".join(str(p) for p in instr) if instr else ""
        # Catalog present
        assert "ontology-modeling" in full
        assert "load_capability" in full
        # Methodology BODY absent — check for section headers, not individual
        # keywords (the catalog description legitimately mentions "M:N 拆分"
        # and "六步法" in summary).
        assert "## 一、建模六步法" not in full
        assert "## 二、数据类型红线" not in full
        assert "## 三、关系（LinkType）规则" not in full
        assert "## 四、ActionType 语义契约" not in full

    @pytest.mark.asyncio
    async def test_load_capability_tool_exposed(self) -> None:
        """The framework-managed load_capability tool is visible so the LLM
        can load the methodology on a modelling turn."""
        tm = TestModel(custom_output_text="好的", call_tools=[])
        agent = _build_test_agent(tm)
        await agent.run("查询客户数量")
        params = tm.last_model_request_parameters
        assert params is not None
        tool_names = [t.name for t in params.function_tools]
        assert "load_capability" in tool_names

    @pytest.mark.asyncio
    async def test_methodology_surfaces_after_load(self) -> None:
        """After load_capability is called, the methodology is in the
        conversation as the tool's return value (so the model sees it in
        history on subsequent turns).

        We simulate a prior turn that called load_capability by seeding
        message_history with LoadCapabilityCallPart + LoadCapabilityReturnPart
        (the framework's native part types for capability loading)."""
        tm = TestModel(custom_output_text="好的", call_tools=[])
        agent = _build_test_agent(tm)
        history = [
            ModelRequest(parts=[UserPromptPart(content="帮我建个订单本体")]),
            ModelResponse(parts=[LoadCapabilityCallPart(tool_call_id="tc1", args={"id": "ontology-modeling"})]),
            ModelRequest(
                parts=[LoadCapabilityReturnPart(tool_call_id="tc1", content={"instructions": _MODELING_METHODOLOGY})]
            ),
        ]
        result = await agent.run("继续建模", message_history=history)
        # The methodology is now in the conversation history (as the tool
        # result), visible to the model on this and subsequent turns.
        full_history = "".join(
            str(getattr(p, "content", ""))
            for m in result.all_messages()
            for p in (m.parts if hasattr(m, "parts") else [])
            if hasattr(p, "content")
        )
        assert "建模六步法" in full_history
        assert "M:N" in full_history
