"""TextQLOrchestrator — wires Step 1-3 into a single pre-agent pipeline.

Called by /ai/agent before the AG-UI Agent runs. Takes the user's latest
message + the scoped ontology, runs:
  Step 1: parse_intent (LLM) → QueryIR
  Step 2: SemanticRecaller (engine A) → RecallResult
  Step 3: SchemaInjector → schema block string

Returns the schema block to stash on AppState.injected_schema (the agent's
dynamic system_prompt decorator appends it to the LLM context).

Design (ADR-012 §「Step 1-3」 + 决策二):
- Deterministic retrieval context: runs on every user message (per reference
  material). Failures are non-fatal — if any step errors, we log + return an
  empty schema block so the agent still runs (LLM can fall back to calling
  list_object_types / describe_object_type tools itself).
- LLM self-manages multi-step iteration (决策二): if recall is incomplete
  (needs_recall_refinement), the IR flag is set but we still inject what we
  have — the LLM can iteratively call metadata tools to补充.
- Token budget: SchemaInjector caps injected ObjectTypes (MAX_INJECT).

This orchestrator is the bridge between the TextQL pipeline and the AG-UI
Agent. It does NOT run Step 4 (tool use) — that's the Agent's job.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ontology.core.models.defaults import utcnow
from ontology.services.textql.intent_parser import parse_intent
from ontology.services.textql.schema_injector import SchemaInjector
from ontology.services.textql.semantic_recall import SemanticRecaller

if TYPE_CHECKING:
    from ontology.config.container import Container

logger = logging.getLogger(__name__)


def _cardinality_label(cardinality: str, direction: str) -> str:
    """Render a human-readable cardinality phrase for a LinkType.

    LinkTypeModel stores ``cardinality`` (ONE|MANY — how many targets one
    source relates to) + ``direction`` (OUTGOING|INCOMING — whether the FK
    lives on the source or target side). The combination yields the
    business-facing relation label (one-to-many / many-to-one / one-to-one).
    Palantir AIP prompts surface this phrase directly because the raw enum
    values are ambiguous to an LLM ("MANY" alone does not say which side is
    many).
    """
    if cardinality == "ONE":
        return "一对一"
    # MANY: direction disambiguates. OUTGOING = FK on source (source is the
    # "many" side → many-to-one); INCOMING = FK on target (source is the
    # "one" side → one-to-many).
    if direction == "OUTGOING":
        return "多对一"
    return "一对多"


# 业务时区：本体查询面向中国市场门店运营场景，“今日/本月/最近N天” 等相对时间
# 一律按北京时间（UTC+8）日历日换算，避免 UTC 跨日错位（UTC 16:00 = 次日 00:00 北京）。
# 服务端仍以 UTC 为内部基准，这里同时给出两个时间，LLM 用北京时间理解业务语义。
_BIZ_TZ_OFFSET = timedelta(hours=8)


def build_runtime_context() -> str:
    """Render the always-on runtime context block (basic facts for the LLM).

    Backend-owned source of runtime facts the AG-UI Agent must know but cannot
    infer from tool calls alone. Currently this is the **current time** — so
    when a user asks "统计今日呼出数" / "本月销售额", the LLM resolves "今日" /
    "本月" to an absolute date instead of guessing from data or fabricating a
    date literal that fails the storage-layer type check.

    Unconditional: injected on EVERY /ai/agent run, even when no ontology is
    scoped. Pinned here (backend) so the frontend static system prompt never
    needs to know the date — a forgotten ``new Date()`` on the client cannot
    regress this.

    Returns:
        A markdown block string (UTC + Beijing time + date-resolution rules).
    """
    now_utc = utcnow()
    now_bj = now_utc + _BIZ_TZ_OFFSET
    today_bj = now_bj.strftime("%Y-%m-%d")
    tomorrow_bj = (now_bj + timedelta(days=1)).strftime("%Y-%m-%d")
    month_start = now_bj.replace(day=1).strftime("%Y-%m-%d")
    next_month = (now_bj.replace(day=28) + timedelta(days=4)).replace(day=1)
    next_month_start = next_month.strftime("%Y-%m-%d")
    return (
        "# 运行时上下文（基础事实）\n\n"
        f"当前时间（UTC）：{now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"当前时间（北京时间 UTC+8）：{now_bj.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"今日（北京时间日历日）：{today_bj}\n"
        f"明日：{tomorrow_bj}　本月起：{month_start}　次月起：{next_month_start}\n\n"
        "时间换算规则（用户提到相对时间时按此换算成绝对值再调工具）：\n"
        '- "今日/今天" → 北京时间当日 [今日 00:00:00, 次日 00:00:00)。'
        "对 TIMESTAMP 列用 gte + lt 两个条件表达，不要用 eq（一个时刻匹配不到一整天）。\n"
        '- "昨日" → [昨日 00:00:00, 今日 00:00:00)。\n'
        '- "本月" → [本月起 00:00:00, 次月起 00:00:00)。\n'
        '- "最近N天" → [今日-N 00:00:00, 今日 00:00:00) 或含今日视语义而定。\n'
        '- 传给 filter 的 value 用 "YYYY-MM-DDTHH:MM:SS" 格式字符串，'
        'storage 层会做类型转换；不要传 "today" / "今" / "6-30" 等非标准字面量。\n'
    )


async def build_ontology_summary(container: Container, ontology: str) -> str:
    """Render a lightweight ontology structure summary for the Agent system prompt.

    This replaces the previous build_injected_schema (which ran the full TextQL
    Step 1-3 pipeline — intent parse LLM + semantic recall — on every /ai/agent
    request, blocking Agent start by 1-5s). Per ADR-009 the routing layer should
    NOT make task decisions for the Agent (TextQL is a tool, not a pre-route);
    intent parsing belongs inside the query tool, not before the Agent runs.

    This summary is a pure DB read (milliseconds, no LLM). It gives the Agent
    enough context to pick the right tool and write valid SQL / drive the canvas
    WITHOUT blind metadata exploration (which caused the repeated
    list_object_types / describe_object_type / list_link_types round-trips that
    slowed graph-explore ReAct loops — each redundant call is one extra LLM
    turn). Concretely it injects, per Palantir OQL best practice:
      - ObjectType list with property api_name + data_type + pk/title markers
        (the LLM needs types to pick gte/lt ranges for timestamps, and to know
        which property is the display title for canvas nodes).
      - LinkType list with source→target OT api_names, the FK property, and
        cardinality semantics (one-to-one / one-to-many / many-to-one) so the
        Agent can call traverse_link with the right link_type + direction in a
        single turn instead of discovering them via describe_link_type.
      - Action type list with api_name + display_name + one-line description
        (ADR-020): keeps the built-in Agent's awareness of available actions
        on par with the MCP ``describe_ontology`` payload (ADR-019 capability
        parity). Full parameter schemas are NOT injected — call
        validate_action / describe_action_type on demand.

    Since ADR-020 this renders from the shared ``assemble_ontology_metadata``
    aggregate (single source of truth shared with the ``describe_ontology``
    tool/endpoint), so the text-injected view and the structured endpoint can
    never drift apart.

    Args:
        container: DI container (for ontology_service access).
        ontology: The ontology api_name the user has open (empty -> no summary).

    Returns:
        A markdown summary block (empty when no ontology is scoped or no OTs).
    """
    if not ontology:
        return ""
    try:
        meta = await container.ontology_service.assemble_ontology_metadata(ontology)
    except Exception as e:  # noqa: BLE001 — never block the agent
        logger.warning("Failed to build ontology summary: %s", e)
        return ""
    if not meta.object_types:
        return ""

    # id → api_name index so LinkType source/target UUIDs render as business
    # names the LLM can use directly. ObjectTypeFullMetadata carries the OT id
    # (ADR-020) precisely to close this loop without a second metadata call.
    ot_name_by_id: dict[str, str] = {ot.id: name for name, ot in meta.object_types.items()}

    lines: list[str] = [
        "# 本体结构摘要（查询约束）",
        "",
        "你只能查询以下已定义的 ObjectType 及其 Property。表名用 ObjectType api_name，字段名用 property api_name。",
        "属性已标注数据类型与主键/标题标记，可直接据此写 SQL。",
        "除非需要约束/关系等更细节，不必再调 describe_object_type。",
        "",
        "## 对象类型（ObjectType）",
        "",
    ]
    # Map OT api_name → its action api_names (built in assemble, attached per OT).
    for ot_name, ot in meta.object_types.items():
        title_tag = f" [title={ot.title_property}]" if ot.title_property else ""
        lines.append(f"### {ot.api_name} ({ot.display_name})[pk={ot.primary_key}]{title_tag}")
        if ot.description:
            lines.append(f"  {ot.description}")
        props = ot.properties or []
        if not props:
            lines.append("  （无属性）")
        else:
            for p in props:
                markers: list[str] = []
                if p.is_primary_key:
                    markers.append("pk")
                if p.is_title_property:
                    markers.append("title")
                if not p.nullable:
                    markers.append("not null")
                tag = f" ({', '.join(markers)})" if markers else ""
                lines.append(f"  - {p.api_name}: {p.data_type}{tag}")
        lines.append("")

    if meta.link_types:
        lines.append("## 关联关系（LinkType）")
        lines.append("")
        lines.append("展开关系时用 traverse_link 工具，link_type 填下表的 api_name。方向已标注（源→目标）。")
        lines.append("")
        for lt in meta.link_types.values():
            src = ot_name_by_id.get(lt.source_object_type_id, lt.source_object_type_id)
            dst = ot_name_by_id.get(lt.target_object_type_id, lt.target_object_type_id)
            card = _cardinality_label(lt.cardinality, lt.direction)
            fk = f" via {lt.foreign_key_property_api_name}" if lt.foreign_key_property_api_name else ""
            desc = f" — {lt.description}" if lt.description else ""
            lines.append(f"- {lt.api_name} ({lt.display_name}): {src} → {dst}{fk}, {card}{desc}")
        lines.append("")

    if meta.action_types:
        lines.append("## 可用动作（ActionType）")
        lines.append("")
        lines.append(
            "执行动作用 invoke_action（需确认）或先 validate_action 预检。下面仅列出概要，完整参数见对应工具。"
        )
        lines.append("")
        for at in meta.action_types.values():
            target = f" (作用于 {at.affected_object_type_api_name})" if at.affected_object_type_api_name else ""
            risk = f" [risk={at.risk_level}]" if at.risk_level != "low" else ""
            desc = f" — {at.description}" if at.description else ""
            lines.append(f"- {at.api_name} ({at.display_name}){target}{risk}{desc}")
        lines.append("")

    lines.append(
        "需要字段约束 / 关系方向细节 / Action 完整参数时再调对应工具（describe_object_type / "
        "describe_link_type / validate_action），不要为了写基础查询而反复探索上述已给信息。"
    )
    return "\n".join(lines)


async def build_injected_schema(container: Container, ontology: str, user_message: str) -> str:
    """Run Step 1-3 and return the schema block for LLM context injection.

    Args:
        container: DI container (for metadata access).
        ontology: The ontology api_name the user has open (empty → no injection).
        user_message: The user's latest natural-language message.

    Returns:
        A markdown schema block string (empty on any failure or when no
        ontology is scoped — the agent runs without injection in that case).
    """
    if not ontology or not user_message.strip():
        return ""

    import time as _time

    _t0 = _time.perf_counter()
    try:
        # Load the ontology's ObjectTypes + LinkTypes once (shared by recall + injection).
        async with container.metadata_session() as meta:
            object_types = await meta.list_object_types(ontology)
            link_types = await meta.get_link_types(ontology)
        _t1 = _time.perf_counter()

        if not object_types:
            logger.debug("No ObjectTypes in ontology %s; skipping injection", ontology)
            return ""

        # Step 1: intent parse (LLM). Non-fatal on failure.
        try:
            ir = await parse_intent(user_message)
        except Exception as e:  # noqa: BLE001 — LLM failure should not block the agent
            _t2 = _time.perf_counter()
            logger.warning("Step 1 intent parse failed (%.2fs), injecting full schema: %s", _t2 - _t1, e)
            # Fall back: inject the full ontology schema without recall narrowing.
            injector = SchemaInjector(link_types)
            ot_id_to_api = {ot.id: ot.api_name for ot in object_types}
            from ontology.core.schemas.textql import RecallResult

            empty_recall = RecallResult(object_types=[])
            return injector.build_context_block(object_types, empty_recall, ot_id_to_api)
        _t2 = _time.perf_counter()

        # Step 2: semantic recall (engine A exact-match + engine B vector).
        # Engine B is wired only if the semantic table exists + embedding
        # provider loads; failures fall back to engine A only (non-fatal).
        vector_search = await _maybe_vector_search(container, ontology)
        recaller = SemanticRecaller(object_types, vector_search=vector_search)
        recall = await recaller.recall(ir)
        _t3 = _time.perf_counter()

        # Step 3: schema injection.
        ot_id_to_api = {ot.id: ot.api_name for ot in object_types}
        injector = SchemaInjector(link_types)
        block = injector.build_context_block(object_types, recall, ot_id_to_api)
        _t4 = _time.perf_counter()

        logger.info(
            "TextQL Step 1-3 done for ontology=%s: intent=%s, recall=%d OTs, block=%d chars | "
            "timings: load=%.2fs intent=%.2fs recall=%.2fs inject=%.2fs total=%.2fs",
            ontology,
            ir.intent_type,
            len(recall.object_types),
            len(block),
            _t1 - _t0,
            _t2 - _t1,
            _t3 - _t2,
            _t4 - _t3,
            _t4 - _t0,
        )
        return block
    except Exception as e:  # noqa: BLE001 — never block the agent
        logger.warning("TextQL pipeline failed, skipping schema injection: %s", e)
        return ""


async def _maybe_vector_search(container: Container, ontology: str) -> Any:
    """Build a vector_search callable for engine B, or None if unavailable.

    Returns an async callable (text, top_k) -> list[dict] that embeds the
    query + runs Doris ANN vector_search. Returns None (engine B disabled)
    if: the semantic table doesn't exist, the embedding model isn't loaded,
    or Doris is unreachable — all non-fatal, engine A still runs alone.
    """
    try:
        index = container.index
        if not await index.semantic_table_exists():
            return None
        from ontology.services.textql.embedding import get_embedding_provider

        provider = get_embedding_provider()

        async def _search(text: str, top_k: int = 10) -> list[dict[str, object]]:
            emb = provider.embed([text])[0].tolist()
            return await index.vector_search(emb, ontology, top_k=top_k)

        return _search
    except Exception as e:  # noqa: BLE001 — engine B is best-effort
        logger.debug("Vector search unavailable (engine B disabled): %s", e)
        return None
