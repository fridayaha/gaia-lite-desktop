"""AG-UI AI routes — pydantic-ai Agent exposed via the AG-UI protocol.

POST /ai/agent — AG-UI RunAgentInput → SSE event stream

The Agent mounts the shared ontology toolsets (``ontology.tools``) — the
same toolsets exposed via MCP to external Agents. This route is the built-in
Web UI's entry point; read-only tool calls happen in-process (no MCP hop),
per ADR-009 decision 3. Write/action tool calls are declared
``requires_approval=True`` at the pydantic-ai layer; HITL is handled natively
by pydantic-ai + AGUIAdapter via AG-UI interrupt/resume (ADR-010, pydantic-ai
2.0+): the model's tool calls become a ``DeferredToolRequests`` output,
``AGUIAdapter`` emits ``RUN_FINISHED { outcome: { type: "interrupt" } }``,
the frontend renders a batch-approval panel and submits ``resume``, which
``AGUIAdapter.deferred_tool_results`` maps back to ``DeferredToolResults`` so
the agent re-runs and executes the approved tools. No custom confirm
endpoint, no NEED_APPROVAL marker.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pydantic_ai.ui.ag_ui import AGUIAdapter

from ontology.config.container import container
from ontology.services.ai_agent import agent, fresh_deps, pipeline_agent, fresh_pipeline_deps
from ontology.services.ai_generate import generate_text, stream_structured, stream_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/agent")
async def ag_ui_stream(request: Request) -> Response:
    """AG-UI standard endpoint.

    ``AGUIAdapter.dispatch_request`` handles the full lifecycle:
    1. Validates the ``RunAgentInput`` body (ag-ui-protocol typed).
    2. Runs the Agent, converting internal events to AG-UI standard
       events (TEXT_MESSAGE_*, TOOL_CALL_*, STATE_*, RUN_*).
    3. When the run ends with pending ``requires_approval`` tool calls,
       emits ``RUN_FINISHED { outcome: { type: "interrupt", interrupts } }``.
    4. On a resume request (``RunAgentInput.resume`` present), reads
       ``adapter.deferred_tool_results`` and re-runs the agent, executing
       the approved tools and continuing.
    5. Returns an SSE ``StreamingResponse`` (Accept: text/event-stream).

    ``manage_system_prompt='client'`` keeps prompt ownership on the
    frontend (system messages in ``messages`` are preserved, not stripped
    — the default 'server' mode would strip them with a warning, see
    ai-integration-guide §7.11).

    The request-scoped ``AppState`` (deps) carries the thread_id (extracted
    from RunAgentInput) + a ToolExecutor for the write/action tool bodies to
    reach Service instances and run through ``audit_call``. HITL is handled
    by pydantic-ai's ``requires_approval`` + AGUIAdapter, not by the executor.
    """
    # Extract thread_id + ontology + mode from the RunAgentInput body. The body is
    # also parsed by dispatch_request, but we read it once here to build the
    # per-run deps. ag-ui-protocol's RunAgentInput.thread_id is the AG-UI
    # conversation id; the ontology comes from forwardedProps (set by the
    # Web UI via HttpAgent.prepareRunAgentInput) and scopes the assistant to
    # the ontology the user has open. See docs/architecture/rfcs/AI-context-scoping.md.
    # mode='pipeline_builder' (set by the pipeline builder page) routes to
    # the pipeline agent + PipelineAppState (ADR-018 §14.5).
    thread_id = ""
    ontology = ""
    mode = ""
    try:
        body = await request.json()
        thread_id = str(body.get("thread_id") or body.get("run_id") or "")
        forwarded = body.get("forwarded_props") or body.get("forwardedProps") or {}
        if isinstance(forwarded, dict):
            ontology = str(forwarded.get("ontology") or "")
            mode = str(forwarded.get("mode") or "")
    except Exception:  # noqa: BLE001 — body parse is best-effort for scoping
        logger.debug("could not read thread_id/ontology from RunAgentInput body")

    # ── Pipeline Builder 分流（ADR-018 §14.5）──
    # 管道构建器页面在 forwardedProps 标记 mode='pipeline_builder'，走独立的
    # pipeline agent（挂 pipeline_builder toolset）+ PipelineAppState（state=
    # PipelineCanvasSnapshot）。不注入本体摘要（管道场景不需要）。
    if mode == "pipeline_builder":
        pipeline_deps = fresh_pipeline_deps(thread_id, pipeline_api_name=ontology)
        return await AGUIAdapter.dispatch_request(
            request,
            agent=pipeline_agent,
            deps=pipeline_deps,
            manage_system_prompt="client",
        )

    deps = fresh_deps(thread_id, ontology=ontology)

    # 路由层只注入「基础事实 + 本体结构摘要」，不替 Agent 做任务决策（ADR-009）。
    # 之前的 build_injected_schema 会同步跑 TextQL Step 1-3（意图解析 LLM + 语义召回），
    # 在 Agent 启动前阻塞 1-5s，且对建模/闲聊类问题是无谓浪费——TextQL 是工具，
    # 意图解析应在其内部执行，不在路由前置。现改为纯 DB 查询的轻量摘要（毫秒级）。
    # runtime_context（日期）无条件注入：LLM 解析“今日/本月”需要绝对时间，
    # 后端注入避免前端遗忘 new Date() 导致回归。
    from ontology.services.textql.orchestrator import build_ontology_summary, build_runtime_context

    deps.injected_schema = build_runtime_context() + await build_ontology_summary(container, ontology)

    return await AGUIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=deps,
        manage_system_prompt="client",
    )


# ══════════════════════════════════════════════════════
# AI generate / stream primitives (AI SDK-style)
# ══════════════════════════════════════════════════════


class GenerateRequest(BaseModel):
    """Request body for /ai/generate and /ai/stream.

    Mirrors Vercel AI SDK's ``generateText``/``streamText`` minimal surface:
    ``instructions`` (system prompt, optional) + ``prompt`` (user prompt,
    required). The backend does NOT perceive task semantics — what the
    prompt asks for and how to parse the output is the caller's concern.
    """

    instructions: str | None = None
    prompt: str


class GenerateResponse(BaseModel):
    """Response body for /ai/generate (non-streaming)."""

    text: str


@router.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    """Non-streaming text generation (AI SDK ``generateText`` equivalent).

    Runs the model to completion and returns the full text. Best for fast,
    structured-output tasks (e.g. deriving an apiName — sub-second). For
    long-form generation where incremental display matters, use /ai/stream.
    """
    text = await generate_text(request.instructions, request.prompt)
    return GenerateResponse(text=text)


@router.post("/stream")
async def stream(request: GenerateRequest) -> Response:
    """Streaming text generation (AI SDK ``streamText`` equivalent).

    Yields text deltas as a Server-Sent Events stream (``text/event-stream``),
    one ``data:`` line per delta. Best for long-form generation where
    incremental display improves perceived latency.
    """
    import json

    from starlette.responses import StreamingResponse

    async def event_source() -> AsyncIterator[bytes]:
        try:
            async for delta in stream_text(request.instructions, request.prompt):
                # SSE: each delta is a data line. Use JSON encoding so a delta
                # containing newlines stays on one SSE line.
                yield f"data: {json.dumps(delta)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001 — surface errors in-stream
            logger.warning("/ai/stream failed: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════
# BuildWith: scaffold an ObjectType from a dataset schema
# (see docs/design/buildwith-object-scaffolding.md)
# ══════════════════════════════════════════════════════


class ScaffoldColumn(BaseModel):
    """A column from the bound dataset's schema."""

    name: str
    type: str
    nullable: bool = True


class ScaffoldProperty(BaseModel):
    """AI-derived suggestion for a single property.

    ``data_type`` and ``nullable`` are intentionally absent — the frontend
    fills them deterministically from the dataset schema (type mapping +
    the column's nullable flag), so the LLM is not asked to guess them.
    """

    source_column: str = Field(description="对应的物理列名，必须与输入的 dataset 列名完全一致")
    display_name: str = Field(description="中文友好展示名，由列名语义推导，如 flight_no → 航班号")
    description: str = Field(
        default="",
        description="该属性的业务含义，一句话，供 LLM 语义理解",
    )
    searchable: bool = Field(
        default=False,
        description="是否常用于过滤/搜索。字符串/枚举类→true；主键/时间戳/二进制→false",
    )
    is_primary_key: bool = Field(
        default=False,
        description="是否为主键（唯一标识对象实例，非空唯一）。整个对象有且仅一个 true",
    )
    is_title_property: bool = Field(
        default=False,
        description="是否为标题字段（界面友好展示对象实例，通常是 name/title 类列）。"
        "整个对象最多一个 true；若无可读标题列则全 false（前端用主键兜底）",
    )


class ScaffoldResult(BaseModel):
    """Complete ObjectType structure derived from a dataset schema."""

    display_name: str = Field(description="对象类型中文展示名，由数据集名/列语义推导，如 flight_info → 航班信息")
    api_name: str = Field(description="对象类型 PascalCase apiName，首字母大写纯 ASCII 字母数字，如 FlightInfo")
    description: str = Field(description="该对象类型的业务领域描述，1-2 句，供 AI 语义理解")
    primary_key_column: str = Field(description="主键列名，必须等于某个属性的 source_column")
    title_column: str | None = Field(
        default=None,
        description="标题列名，必须等于某个属性的 source_column；无合适列时为 null",
    )
    properties: list[ScaffoldProperty] = Field(
        description="全部属性，每列一个；有且仅有一个 is_primary_key=true",
        min_length=1,
    )


class ScaffoldRequest(BaseModel):
    """Request body for /ai/scaffold."""

    dataset_api_name: str
    dataset_display_name: str = ""
    storage_type: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    columns: list[ScaffoldColumn]


_SCAFFOLD_INSTRUCTIONS = (
    "你是企业数据建模专家。给定一个数据集的列 schema（列名、类型、是否可空），"
    "推导出一个对象类型（ObjectType）的完整结构，供用户在建模向导中确认/微调。\n\n"
    "推导要求：\n"
    "1. display_name：从数据集名和列语义推导中文展示名（如 flight_info → 航班信息）。\n"
    "2. api_name：PascalCase，首字母大写，纯 ASCII 字母数字，≤99 字符，语义对应 display_name。\n"
    "3. description：1-2 句业务领域描述。\n"
    "4. properties：每个列生成一个属性。\n"
    "   - display_name：列名的中文友好名（如 flight_no → 航班号，created_at → 创建时间）。\n"
    "   - description：该列的业务含义，一句话。\n"
    "   - searchable：字符串/枚举类用于过滤的列→true；主键/时间戳/数值度量/二进制→false。\n"
    "   - source_column：必须与输入列名完全一致，不要改写。\n"
    "5. primary_key_column：选唯一标识对象实例的列。优先非空的 id 类列；避免可空列。"
    "必须等于某个属性的 source_column。\n"
    "6. title_column：选界面友好展示的列（通常是 name/title/label 类字符串列）。"
    "无合适列时返回 null。必须等于某个属性的 source_column 或为 null。\n\n"
    "约束：\n"
    "- 不要输出 data_type、nullable、source_column 之外的字段（类型由系统映射，nullable 由 schema 提供）。\n"
    "- primary_key_column 必须存在且唯一；title_column 可为 null。\n"
    "- 全部属性中，主键列对应的 is_primary_key=true，其余 false；"
    "title 列对应的 is_title_property=true。\n"
    "- 只返回结构化结果，不要解释、不要 markdown。\n\n"
    "示例：\n"
    "输入：\n"
    "数据集名：customer\n"
    "列 schema：\n"
    "- customer_id | bigint | nullable=false\n"
    "- name | varchar | nullable=false\n"
    "- email | varchar | nullable=true\n"
    "- phone | varchar | nullable=true\n"
    "- created_at | timestamp | nullable=true\n\n"
    "输出：\n"
    "{\n"
    '  "display_name": "客户",\n'
    '  "api_name": "Customer",\n'
    '  "description": "客户信息，记录客户基本联系方式。",\n'
    '  "primary_key_column": "customer_id",\n'
    '  "title_column": "name",\n'
    '  "properties": [\n'
    '    {"source_column": "customer_id", "display_name": "客户ID", '
    '"description": "客户唯一标识", "searchable": false, "is_primary_key": true, '
    '"is_title_property": false},\n'
    '    {"source_column": "name", "display_name": "姓名", '
    '"description": "客户姓名", "searchable": true, "is_primary_key": false, '
    '"is_title_property": true},\n'
    '    {"source_column": "email", "display_name": "邮箱", '
    '"description": "客户邮箱地址", "searchable": true, "is_primary_key": false, '
    '"is_title_property": false},\n'
    '    {"source_column": "phone", "display_name": "电话", '
    '"description": "客户联系电话", "searchable": true, "is_primary_key": false, '
    '"is_title_property": false},\n'
    '    {"source_column": "created_at", "display_name": "创建时间", '
    '"description": "客户记录创建时间", "searchable": false, "is_primary_key": false, '
    '"is_title_property": false}\n'
    "  ]\n"
    "}"
)


def _build_scaffold_prompt(req: ScaffoldRequest) -> str:
    """Render the user prompt from the request's dataset info."""
    lines = [
        f"数据集名：{req.dataset_api_name}",
        f"数据集展示名：{req.dataset_display_name or req.dataset_api_name}",
        f"存储类型：{req.storage_type}",
        "",
        "列 schema：",
    ]
    for col in req.columns:
        nullable_str = "nullable=false" if not col.nullable else "nullable=true"
        lines.append(f"- {col.name} | {col.type} | {nullable_str}")
    return "\n".join(lines)


def _sanitize_scaffold_result(result: ScaffoldResult, input_columns: list[ScaffoldColumn]) -> ScaffoldResult:
    """Validate/repair an LLM-produced ScaffoldResult against the input schema.

    Guards against hallucination:
    - drop properties whose ``source_column`` is not in the input columns;
    - backfill missing columns as deterministic skeleton properties;
    - if ``primary_key_column`` / ``title_column`` don't match a surviving
      property's ``source_column``, clear them (frontend falls back to
      user-selected PK / PK-as-title).
    """
    input_names = {c.name for c in input_columns}

    # 1. Drop hallucinated properties (source_column not in input).
    kept = [p for p in result.properties if p.source_column in input_names]
    seen = {p.source_column for p in kept}

    # 2. Backfill missing columns as deterministic skeletons.
    for col in input_columns:
        if col.name in seen:
            continue
        kept.append(
            ScaffoldProperty(
                source_column=col.name,
                display_name=col.name,
                description="",
                searchable=False,
                is_primary_key=False,
                is_title_property=False,
            )
        )
        seen.add(col.name)

    # 3. Validate key columns reference surviving properties.
    kept_names = {p.source_column for p in kept}
    primary_key_column = result.primary_key_column if result.primary_key_column in kept_names else ""
    title_column = (
        result.title_column if result.title_column is not None and result.title_column in kept_names else None
    )

    # 4. Reconcile per-property flags with the (possibly repaired) key columns.
    for p in kept:
        p.is_primary_key = p.source_column == primary_key_column
        p.is_title_property = title_column is not None and p.source_column == title_column

    return ScaffoldResult(
        display_name=result.display_name,
        api_name=result.api_name,
        description=result.description,
        primary_key_column=primary_key_column,
        title_column=title_column,
        properties=kept,
    )


@router.post("/scaffold")
async def scaffold(req: ScaffoldRequest) -> Response:
    """Scaffold an ObjectType structure from a dataset schema (BuildWith).

    Streams partial ``ScaffoldResult`` JSON objects as SSE
    (``text/event-stream``), progressively more complete. Each partial is
    sanitized against the input schema before emission so the frontend never
    receives hallucinated columns or dangling key references. Sanitization is
    idempotent and cheap, so running it on every partial (including the final
    complete one) is safe and keeps intermediate states consistent.

    See ``docs/design/buildwith-object-scaffolding.md``.
    """
    import json

    from starlette.responses import StreamingResponse

    prompt = _build_scaffold_prompt(req)

    async def event_source() -> AsyncIterator[bytes]:
        try:
            async for partial in stream_structured(ScaffoldResult, _SCAFFOLD_INSTRUCTIONS, prompt):
                sanitized = _sanitize_scaffold_result(partial, req.columns)
                yield f"data: {sanitized.model_dump_json()}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001 — surface errors in-stream
            logger.warning("/ai/scaffold failed: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════
# AI-powered ActionType scaffolding
# (management-plane AI assistant, NOT an AG-UI tool; red line 12: ActionType
# CRUD is management-plane, not operation-plane)
# ═══════════════════════════════════════════════════════


class ActionTypeScaffoldRequest(BaseModel):
    """Request body for /ai/action-type/scaffold."""

    ontology: str = Field(description="Ontology api_name")
    affected_object_type: str = Field(description="目标对象类型 api_name（action 归属，已存在）")
    natural_language: str = Field(description="用自然语言描述动作意图")


@router.post("/action-type/scaffold")
async def scaffold_action_type(req: ActionTypeScaffoldRequest) -> Response:
    """Scaffold an ActionType draft from a natural-language description.

    Streams partial ``ActionTypeDraft`` JSON objects as SSE
    (``text/event-stream``), progressively more complete. Each partial is
    sanitized against the real affected ObjectType schema before emission
    (anti-hallucination: the LLM is given the real property names, never
    asked to guess). After the stream completes, a validation pass runs and
    — on failure — a CEGIS repair round is attempted; the repaired draft is
    yielded as the final frame.

    The draft is NEVER persisted here. The frontend ActionTypeEditor must
    POST the finalized draft to ``/actions/definitions/{ontology}/{action}``
    to save. This endpoint is a management-plane AI assistant (red line 12),
    NOT exposed as an AG-UI/MCP tool.
    """
    import json

    from starlette.responses import StreamingResponse

    from ontology.services.ai_action_generate import (
        _load_object_type_info,
        stream_action_type_draft,
    )

    # Load the affected ObjectType schema + existing action names BEFORE the
    # SSE stream starts, so the DB session closes before the (long-running)
    # LLM call. The schema is passed into the stream as a plain pydantic obj,
    # decoupling the LLM loop from the DB session lifecycle.
    try:
        async with container.metadata_session() as metadata:
            obj_type = await _load_object_type_info(
                metadata, req.ontology, req.affected_object_type
            )
            try:
                existing = await metadata.list_action_types(req.ontology)
                existing_names = [a.api_name for a in existing]
            except Exception:  # noqa: BLE001 — best-effort; de-dup is advisory
                existing_names = []
    except Exception as e:  # noqa: BLE001 — ObjectType not found etc.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e)) from e

    async def event_source() -> AsyncIterator[bytes]:
        try:
            async for partial in stream_action_type_draft(
                obj_type=obj_type,
                natural_language=req.natural_language,
                existing_action_api_names=existing_names,
            ):
                yield f"data: {partial.model_dump_json()}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001 — surface errors in-stream
            logger.warning("/ai/action-type/scaffold failed: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
