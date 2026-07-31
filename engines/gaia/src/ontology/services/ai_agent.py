"""AG-UI Agent for ontology tool access via the built-in Web UI.

v4.0 (ADR-009): the Agent mounts shared ontology toolsets built from
ontology metadata — the same toolsets exposed via MCP. The built-in Web
UI is one consumer among many (MCP / REST being the others).

v4.2 (ADR-010, AG-UI native interrupt/resume): write/action tools are
declared with ``metadata={"risk_level": ...}`` and wrapped in
``MetadataApprovalToolset`` (see ``toolsets/approval.py``). When the model
calls such a tool, the wrapper raises ``ApprovalRequired(metadata=...)``;
pydantic-ai collects the pending approval into a ``DeferredToolRequests``
output; ``AGUIAdapter`` emits an AG-UI ``RUN_FINISHED { outcome: { type:
"interrupt" } }`` carrying one ``Interrupt`` per pending tool call (the
wrapper forwards the tool's ``metadata`` onto ``Interrupt.metadata``). The
frontend renders a batch approval panel; the user's decision is submitted
as AG-UI ``resume``, which ``AGUIAdapter.deferred_tool_results`` maps back
to ``DeferredToolResults``, and the agent re-runs — executing the approved
tools and continuing. No custom ``/ai/action/confirm`` endpoint, no
NEED_APPROVAL marker.

Note: tools do NOT use pydantic-ai's ``requires_approval=True`` — that sets
``ToolDefinition.kind='unapproved'``, which makes pydantic-ai collect the
deferral WITHOUT calling ``call_tool``, so the tool's static ``metadata``
never reaches ``DeferredToolRequests.metadata`` / ``Interrupt.metadata``.
The ``MetadataApprovalToolset`` wrapper avoids this by raising
``ApprovalRequired(metadata=...)`` from ``call_tool`` (which pydantic-ai
then records into ``DeferredToolRequests.metadata``).

Architecture notes (see docs/engineer/ai-integration-guide.md for the v3.0
AG-UI mechanics, still in force):
- ``manage_system_prompt='client'`` — the frontend owns the prompt.
- HITL: pydantic-ai native ``requires_approval`` + AGUIAdapter
  interrupt/resume (pydantic-ai 2.0+, PR #5441). See
  docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md §4.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic_ai import Agent, ModelSettings, RunContext
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.toolsets import CombinedToolset

from ontology.config.container import container
from ontology.config.settings import settings
from ontology.services.ontology_modeling import build_modeling_capability
from ontology.tools import (
    ToolExecutor,
    build_action_toolset,
    build_canvas_control_toolset,
    build_link_traversal_toolset,
    build_metadata_toolset,
    build_object_query_toolset,
    build_reasoning_toolset,
    build_write_toolset,
)
from ontology.tools.state import AppState
from ontology.tools.toolsets.approval import MetadataApprovalToolset
from ontology.tools.pipeline_state import PipelineAppState
from ontology.tools.toolsets.pipeline_builder import build_pipeline_builder_toolset

_log = logging.getLogger(__name__)

# Module-level executor for the read-only toolsets (no HITL, no handler).
# Write/action toolsets do NOT use this — they read a request-scoped
# executor from ctx.deps.executor (bound per AG-UI run).
_readonly_executor = ToolExecutor(container)


def _current_date_instruction(_ctx: RunContext[AppState]) -> str:
    """Inject the current date as a runtime instruction.

    A basic fact the LLM cannot infer from tool calls. Without it,
    "统计今日外呼数" / "本月销售额" made the LLM guess "今日" from the queried
    data range ("该本体数据日期范围...因此以...为今日") or fabricate a date
    literal that failed the storage-layer type check. Backend-owned (not in
    the frontend's static prompt) so a forgotten ``new Date()`` on the client
    cannot regress it. Mirrors pi's own `Current date: YYYY-MM-DD` injection —
    one line is enough; the LLM derives "今日/本月/最近N天" itself. Beijing
    time (UTC+8) because the business calendar is China-local; UTC would
    split a day across midnight.

    Implemented as an `instructions` callable (not `@system_prompt`) because
    the AG-UI path runs `manage_system_prompt='client'`, where `@system_prompt`
    decorators are dropped when the frontend sends a SystemMessage — see
    ``build_agent`` for the full rationale.
    """
    today_bj = (datetime.now(UTC) + timedelta(hours=8)).strftime("%Y-%m-%d")
    return f"Current date: {today_bj} (Beijing time, UTC+8)"


def _injected_schema_instruction(ctx: RunContext[AppState]) -> str | None:
    """Inject the TextQL schema block as a runtime instruction (ADR-012).

    The /ai/agent route runs Step 1-3 (intent parse → semantic recall →
    schema injection) before the agent and stashes the block on
    ``ctx.deps.injected_schema``. This appends it as an instruction every
    turn — the LLM sees only ontology-defined entities / properties / links
    (the "用本体驯化 LLM" guardrail). Returns None (no instruction added)
    when no ontology is scoped or recall found nothing.

    Like ``_current_date_instruction``, this uses the `instructions` channel
    (not `@system_prompt`) so it survives `manage_system_prompt='client'`.
    """
    return ctx.deps.injected_schema or None


def _canvas_state_instruction(ctx: RunContext[AppState]) -> str | None:
    """Inject the current canvas state as a runtime instruction (ADR-015).

    This is the ReAct "current state" channel for graph exploration: the
    Agent reads ``ctx.deps.state`` (a ``CanvasSnapshot``) every turn to
    decide the next action. Crucially, when ``object_count == 0`` the Agent
    sees the canvas is empty and terminates gracefully with an honest
    "no relevant data in this ontology" message instead of fabricating a
    multi-step analysis (the ADR-015 D5 "natural termination" guard — no
    if-rule needed, the LLM observes the empty state and stops).

    Returns None when the canvas is in its initial state (no query yet),
    so non-graph-exploration conversations (ontology modelling chat) are
    not polluted with canvas context.
    """
    canvas = ctx.deps.state
    # Initial state: no query run yet. Don't inject — this keeps the canvas
    # context out of ontology-modelling conversations that share the Agent.
    if not canvas.last_query_summary and canvas.object_count == 0:
        return None
    return (
        "# 图探索画布当前状态\n\n"
        f"- 视图：{canvas.view}\n"
        f"- 画布对象数：{canvas.object_count}\n"
        f"- 已展开关系：{', '.join(canvas.expanded_links) or '（无）'}\n"
        f"- 当前着色：{canvas.color_by or '（未着色）'}\n"
        f"- 上一步查询：{canvas.last_query_summary}\n\n"
        "决策规则：\n"
        "- 若 object_count 为 0，说明当前本体不包含与问题相关的对象，"
        "请如实告知用户无法分析并建议换问法或换本体，不要编造分析结论，"
        "也不要继续调用 switch_view / color_by 等画布工具。\n"
        "- 若已有对象，可继续调 query_with_dataframe / traverse_link 展开关系，"
        "或 switch_view / color_by 调整可视化。\n"
    )


def build_agent() -> Agent[AppState, str]:
    """Build the AG-UI Agent with all ontology toolsets mounted.

    Read-only toolsets (metadata / object_query / link_traversal) bind the
    module-level ``_readonly_executor``. Write/action toolsets (write /
    action) take ``RunContext[AppState]`` and read the request-scoped
    executor from ``ctx.deps.executor`` — bound per run by fresh_deps().
    Write/action toolsets are wrapped in ``MetadataApprovalToolset`` so their
    ``metadata={"risk_level": ...}`` flows onto AG-UI interrupts and pydantic-ai
    + AGUIAdapter own the HITL interrupt/resume lifecycle.
    """
    toolset = CombinedToolset(
        [
            build_metadata_toolset(_readonly_executor),
            build_object_query_toolset(_readonly_executor),
            build_link_traversal_toolset(_readonly_executor),
            # Graph-reasoning 推理线（query_with_dataframe, ObjectSet IR → Neo4j+PG）。
            build_reasoning_toolset(_readonly_executor),
            # 画布操控（ADR-015）：switch_view / color_by，写 CanvasSnapshot state。
            build_canvas_control_toolset(),
            # Wrap write/action toolsets so their static metadata (risk_level)
            # flows through DeferredToolRequests.metadata → AG-UI
            # Interrupt.metadata, which the frontend batch-approval panel reads
            # to decide per-item vs blanket-approve. The built-in
            # ApprovalRequiredToolset drops metadata; MetadataApprovalToolset
            # preserves it.
            MetadataApprovalToolset(build_write_toolset()),
            MetadataApprovalToolset(build_action_toolset()),
        ]
    )
    agent = Agent(
        settings.ai_model,
        deps_type=AppState,
        system_prompt="",
        toolsets=[toolset],
        # DeferredToolRequests must be an output type so that when a
        # write/action tool's MetadataApprovalToolset wrapper raises
        # ApprovalRequired, pydantic-ai ends the run with a
        # DeferredToolRequests output (carrying the pending approvals + their
        # metadata). AGUIAdapter then turns that into an AG-UI
        # RUN_FINISHED { outcome: { type: "interrupt" } }; the frontend
        # batch-approval panel submits resume, AGUIAdapter maps it back to
        # DeferredToolResults, and the agent re-runs executing the approved
        # tools. Without this output type pydantic-ai raises RUN_ERROR on
        # the deferral.
        output_type=[str, DeferredToolRequests],
        model_settings=ModelSettings(
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        ),
        retries=settings.ai_retries,
        defer_model_check=True,
        # Ontology-modelling skill (form A): a deferred Capability whose
        # `get_instructions()` injects the Palantir-grade modelling
        # methodology (six-step flow, data-type red lines, M:N split,
        # ActionType semantic-contract discipline, confidence marking) ONLY
        # when the LLM calls `load_capability` on a modelling turn. Stays
        # collapsed to a one-line catalog entry on query/exploration turns,
        # so `buildOntologyQueryPrompt` stays lean. The capability carries
        # ONLY instructions — the write/action tools it guides already live
        # on the Agent (build_write_toolset/build_action_toolset + HITL).
        # See services/ontology_modeling.py + docs/architecture/
        # ontology-modeling-spec.md for the skill→Gaia mapping.
        capabilities=[build_modeling_capability()],
        # Runtime context is injected via `instructions`, NOT `@system_prompt`.
        # The /ai/agent route runs with `manage_system_prompt='client'`: the
        # frontend owns the system prompt (its SystemMessage is preserved),
        # and pydantic-ai only injects the agent's `@system_prompt` decorators
        # when message_history is EMPTY (pydantic_ai._agent_graph line ~373:
        # `if not messages: parts.extend(await self._sys_parts(...))`). Since
        # the frontend always sends a SystemMessage, `@system_prompt` decorators
        # are silently dropped on the AG-UI path — putting the date or schema
        # block there means the LLM never sees them (this was the root cause of
        # the "统计今日外呼数" date-hallucination: the date decorator fired in
        # unit tests but not in the real client-mode run).
        #
        # `instructions` (InstructionPart) is resolved on EVERY ModelRequestNode
        # (pydantic_ai._agent_graph line ~906) independent of client/server
        # mode, so it reliably reaches the LLM every turn. We use it for both:
        #   - the current date (basic runtime fact),
        #   - the TextQL schema block (the "用本体驯化 LLM" guardrail).
        instructions=[
            _current_date_instruction,
            _injected_schema_instruction,
            _canvas_state_instruction,
        ],
    )

    return agent


agent = build_agent()


def fresh_deps(thread_id: str = "", ontology: str = "") -> AppState:
    """Build a request-scoped AppState for an AG-UI run.

    Constructs a ToolExecutor with no approval handler — the AG-UI path does
    not use ``execute_gated`` for approvals (pydantic-ai's
    ``requires_approval`` + AGUIAdapter interrupt/resume handles HITL). The
    executor is still needed so write/action tool bodies can reach Service
    instances via ``ctx.deps.executor.container`` and run through
    ``audit_call``.

    ``ontology`` is the ontology api_name the user has open in the Web UI
    (empty for non-UI paths). Read-only toolsets read it from
    ``ctx.deps.ontology`` to default their ``ontology`` arg, keeping the
    assistant inside the current ontology. See
    docs/architecture/rfcs/AI-context-scoping.md.
    """
    executor = ToolExecutor(container)
    return AppState(thread_id=thread_id, executor=executor, ontology=ontology)


# ══════════════════════════════════════════════════════
# Pipeline Builder Agent (ADR-018 §14.5)
# ══════════════════════════════════════════════════════
# 独立于 ontology Agent：挂 pipeline_builder toolset（8 工具），deps 为
# PipelineAppState（state=PipelineCanvasSnapshot）。前端管道构建器页面在
# RunAgentInput.forwardedProps 标记 mode='pipeline_builder'，路由层据此分流。


def build_pipeline_agent() -> Agent[PipelineAppState, str]:
    """Build the AG-UI Agent for the pipeline builder canvas.

    Mounts the pipeline_builder toolset (list_datasets / get_dataset_schema /
    add_source / add_transform / add_sink / modify_node / remove_node /
    connect). Tools write ``PipelineCanvasSnapshot`` via ``StateSnapshotEvent``;
    the frontend ``usePipelineBuilderAgent`` taps the event stream and
    re-renders the canvas.
    """
    agent = Agent(
        settings.ai_model,
        deps_type=PipelineAppState,
        system_prompt="",
        toolsets=[build_pipeline_builder_toolset()],
        output_type=str,
        model_settings=ModelSettings(
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        ),
        retries=settings.ai_retries,
        defer_model_check=True,
    )
    return agent


pipeline_agent = build_pipeline_agent()


def fresh_pipeline_deps(thread_id: str = "", pipeline_api_name: str = "") -> PipelineAppState:
    """Build a request-scoped PipelineAppState for a pipeline-builder AG-UI run."""
    executor = ToolExecutor(container)
    return PipelineAppState(
        thread_id=thread_id,
        executor=executor,
        pipeline_api_name=pipeline_api_name,
    )
