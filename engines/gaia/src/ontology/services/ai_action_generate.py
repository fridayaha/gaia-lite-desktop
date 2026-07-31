"""LLM-assisted ActionType scaffolding (AI-powered Action Type creation).

Turns a natural-language action description (e.g. "当工单状态为 Open 时，把
优先级改成 P0/P1/P2，并通知负责人") into a validated ``ActionTypeCreate``
draft the user can review and fine-tune in the ActionTypeEditor before saving.

This is the Gaia translation of Palantir AI FDE's "create Action Type" skill:
the LLM proposes, hard-coded rules decide — the product is always a *draft*,
never a direct Ontology mutation. The user confirms + the existing REST
endpoint ``POST /actions/definitions/{ontology}/{action_type}`` persists it.

Design (mirrors ``ai_policy_generate.py``'s verifier-guided paradigm):
  1. Deterministically load the affected ObjectType's full schema (property
     names/types/primary key) — the LLM is NEVER asked to guess property
     names. Schema is injected into the prompt (same anti-hallucination
     technique as ``/ai/scaffold``).
  2. ``stream_structured(ActionTypeDraft, ...)`` streams a progressively-
     complete draft via pydantic-ai's Tool Output mode.
  3. Each partial is sanitized by ``_sanitize_action_draft``:
       - drop parameters/rules referencing non-existent properties,
       - backfill missing required structural fields,
       - reconcile operation_kind against ontology_rules,
       - clamp data_type / risk_level / source enums to valid values.
  4. A post-stream validation pass (``_validate_draft``) runs the real
     validators (``ParameterValidator`` shape checks + simpleeval syntax
     pre-check on submission_criteria / rule expressions) and, on failure,
     feeds the errors back to the LLM for a repair round (CEGIS loop,
     up to ``_MAX_RETRIES``).
  5. The final draft is returned with ``validation_passed`` + errors +
     confidence — the caller (frontend) shows errors for user fix-up;
     nothing is persisted here.

Why a separate endpoint, not an AG-UI Agent tool?
  ActionType CRUD is a *management-plane* capability (ADR-019 red line 12).
  MCP/AG-UI are operation-plane entry points and must NOT expose ActionType
  definition. This endpoint is a management-plane AI assistant called
  directly by the ActionTypeEditor frontend — same layering as
  ``/ai/scaffold`` (object modelling) and the policy generator.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ontology.core.schemas.action import (
    ActionEffectConfig,
    ActionRule,
    ActionTypeCreate,
    ActionTypeParameter,
    OntologyRule,
    SubmissionCriterion,
    ValueSource,
)
from ontology.core.schemas.ontology import DataType
from ontology.services.ai_generate import stream_structured

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)

# Maximum LLM repair rounds (CEGIS: validate → feed error → regenerate).
_MAX_RETRIES = 3

# Valid enum domains (mirrors ActionTypeCreate / ActionEffectConfig literals).
_VALID_DATA_TYPES = {t.value for t in DataType}
_VALID_RISK_LEVELS = {"low", "medium", "high"}
_VALID_OP_KINDS = {"create", "update", "delete", "mixed"}
_VALID_EFFECT_TYPES = {"webhook", "write_back", "sub_action", "kafka_topic", "notification"}
_VALID_RULE_TYPES = {
    "CreateObject",
    "ModifyObject",
    "UpsertObject",
    "DeleteObject",
    "CreateLink",
    "DeleteLink",
}
_VALID_VALUE_SOURCES = {
    "PARAMETER",
    "OBJECT_PROPERTY",
    "STATIC_VALUE",
    "SYSTEM_CONTEXT",
    "SYSTEM_GENERATED",
    "EXPRESSION",
}
_VALID_DEFAULT_SOURCES = {
    "static",
    "current_user",
    "current_timestamp",
    "workspace_id",
    "selected_object_field",
}
_VALID_SYSTEM_CONTEXT_VALUES = {"CURRENT_USER_ID", "CURRENT_TIMESTAMP"}


# ── Draft schema (LLM output type) ───────────────────────────────────────


class _DraftParameter(BaseModel):
    """A parameter the LLM proposes. Sanitized into ActionTypeParameter."""

    api_name: str
    display_name: str = ""
    data_type: str = "STRING"
    required: bool = True
    default: Any | None = None
    description: str = ""
    default_source: str = "static"
    default_source_field: str | None = None
    readonly: bool = False
    hidden: bool = False
    pattern: str | None = None
    error_message: str | None = None
    enum_values: list[str] | None = None
    object_type_ref: str | None = None
    is_object_set: bool = False


class _DraftRule(BaseModel):
    """A constraint/derivation/validation rule."""

    type: str = "constraint"
    target: str
    expression: str
    description: str = ""


class _DraftSubmissionCriterion(BaseModel):
    expression: str
    error_message: str
    description: str = ""


class _DraftValueSource(BaseModel):
    source: str = "PARAMETER"
    value: str | None = None


class _DraftOntologyRule(BaseModel):
    type: str = "ModifyObject"
    target_parameter: str | None = None
    target_object_type: str | None = None
    properties: dict[str, _DraftValueSource] = Field(default_factory=dict)
    link_type: str | None = None
    source_parameter: str | None = None
    target_link_parameter: str | None = None
    condition: str | None = None
    on_missing: str = "raise_not_found"
    description: str = ""


class _DraftEffect(BaseModel):
    type: str = "notification"
    config: dict[str, Any] = Field(default_factory=dict)
    trigger: str = "AFTER_ONTOLOGY_CHANGE"
    condition: str | None = None


class ActionTypeDraft(BaseModel):
    """LLM-produced ActionType draft (streamed + sanitized).

    Shape mirrors ``ActionTypeCreate`` so the frontend ActionTypeEditor can
    patch it onto its draft state with minimal translation. ``confidence``
    and ``pending_confirmations`` are AI-FDE-style signals the UI shows so
    the user knows what to double-check.
    """

    api_name: str = ""
    display_name: str = ""
    description: str = ""
    affected_object_type_api_name: str = ""
    parameters: list[_DraftParameter] = Field(default_factory=list)
    rules: list[_DraftRule] = Field(default_factory=list)
    submission_criteria: list[_DraftSubmissionCriterion] = Field(default_factory=list)
    ontology_rules: list[_DraftOntologyRule] = Field(default_factory=list)
    effects: list[_DraftEffect] = Field(default_factory=list)
    risk_level: str = "low"
    operation_kind: str = "mixed"
    batch_enabled: bool = False
    confidence: float = 0.0
    pending_confirmations: list[str] = Field(default_factory=list)


class _ObjectTypeInfo(BaseModel):
    """Deterministic ObjectType schema slice injected into the prompt."""

    api_name: str
    display_name: str
    primary_key: str | None
    title_property: str | None
    storage_type: str
    properties: list[dict[str, Any]]


# ── System prompt ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是企业本体建模专家，专长是设计 Palantir Foundry 风格的 ActionType（业务动作契约）。

给定一个已存在的目标对象类型（ObjectType）的完整 schema，以及用户用自然语言描述的\
动作意图，你的任务是推导出一个完整的 ActionType 草稿，供用户在编辑器里确认/微调后保存。

# ActionType 契约要素

1. **api_name**：camelCase，首字母小写，纯 ASCII 字母数字（如 changePriority）。\
语义对应动作意图，由 display_name 推导。
2. **display_name**：中文友好名（如"修改优先级"）。
3. **description**：1-2 句业务描述。
4. **parameters**：入参列表。每个参数：
   - api_name：camelCase。
   - display_name：中文友好名。
   - data_type：必须从目标对象属性或常量中选，取值 ∈ {STRING, INTEGER, LONG, \
DECIMAL, BOOLEAN, TIMESTAMP, DATE}。金额用 DECIMAL（禁 DOUBLE/STRING）；\
时间用 TIMESTAMP；布尔用 BOOLEAN。
   - required：是否必填。
   - enum_values：当取值是固定枚举（如优先级 P0/P1/P2）时列出。
   - object_type_ref：当参数引用另一个对象时填该对象 api_name；引用当前对象本身时\
填目标对象 api_name。
   - default_source：静态默认值用 static；当前用户/时间用 current_user/\
current_timestamp。
5. **ontology_rules**：声明式对象变更规则（参数→对象变更映射）。每条规则：
   - type ∈ {CreateObject, ModifyObject, UpsertObject, DeleteObject, \
CreateLink, DeleteLink}。
   - ModifyObject/UpsertObject/DeleteObject：target_parameter 指向一个\
object_type_ref 参数（执行时取其值作主键）。
   - CreateObject：target_object_type 指向要创建的对象 api_name；必须映射主键属性\
（若目标=当前对象）。
   - properties：{属性api_name: ValueSource}。ValueSource.source ∈ {PARAMETER\
（value=参数名）, STATIC_VALUE（value=字面量）, SYSTEM_CONTEXT（value∈\
{CURRENT_USER_ID, CURRENT_TIMESTAMP}）, SYSTEM_GENERATED（value="uuid"→生成主键）, \
EXPRESSION（value=simpleeval 表达式）}。
   - **ModifyObject 的 properties 不可包含主键属性**。
   - condition：可选 simpleeval 表达式（如 "$isUrgent == true"），为真才执行。
6. **submission_criteria**：全局提交校验，每条 = {expression: simpleeval 表达式\
（如 "newPriority in ['P0','P1','P2']"）, error_message: 失败提示}。
7. **rules**：派生/约束规则（type ∈ {derivation, constraint, validation}），\
target=参数名，expression=simpleeval 表达式。复杂校验优先放 submission_criteria。
8. **effects**：副作用。type ∈ {notification, webhook, write_back, sub_action, \
kafka_topic}。简单通知用 notification（config 可空）。
9. **risk_level** ∈ {low, medium, high}：写操作/删除/外部回写→medium 或 high；\
纯状态修改→low。
10. **operation_kind** ∈ {create, update, delete, mixed}：根据 ontology_rules 推导。
11. **batch_enabled**：是否允许批量执行，默认 false。

# 关键约束

- **只能引用目标对象 schema 中存在的属性名**，不要臆造属性。
- 参数 api_name 与对象属性 api_name 是两个命名空间，可同名（机制 A：同名参数自动\
绑定同名属性）。
- simpleeval 表达式语法：支持 ==, !=, >, <, >=, <=, and, or, not, in, +, -, *, /；\
变量名直接写参数名（如 "quantity > 0 and status == 'open'"）；字符串用单引号。
- 不要包含运行时策略字段（幂等/重试/超时/回滚）——这些不在 ActionType 契约里。
- 对不确定的判断，在 pending_confirmations 里列出待用户确认的问题，并在 confidence\
（0.0-1.0）里反映整体把握。

# 输出

只返回结构化 ActionTypeDraft 对象，不要解释、不要 markdown。"""


# ── Prompt construction ──────────────────────────────────────────────────


def _build_prompt(
    natural_language: str,
    obj_type: _ObjectTypeInfo,
    existing_action_api_names: list[str],
) -> str:
    """Render the user prompt: NL intent + deterministic ObjectType schema."""
    props_lines = []
    for p in obj_type.properties:
        flags = []
        if p.get("is_primary_key"):
            flags.append("主键")
        if p.get("is_title_property"):
            flags.append("标题")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        props_lines.append(
            f"- {p['api_name']} | {p.get('data_type', 'STRING')} | "
            f"nullable={p.get('nullable', True)}{flag_str}"
            + (f" | 描述: {p['description']}" if p.get("description") else "")
        )
    return (
        f"目标对象类型：{obj_type.api_name}（{obj_type.display_name}）\n"
        f"存储类型：{obj_type.storage_type}\n"
        f"主键属性：{obj_type.primary_key or '（无）'}\n"
        f"标题属性：{obj_type.title_property or '（无）'}\n\n"
        f"对象属性 schema：\n" + "\n".join(props_lines) + "\n\n"
        f"已存在的 ActionType api_name（用于查重，不要重复）：{existing_action_api_names or '（无）'}\n\n"
        f"用户动作意图：\n{natural_language}"
    )


# ── Sanitization (anti-hallucination, idempotent) ────────────────────────


def _clamp(value: str, valid: set[str], default: str) -> str:
    return value if value in valid else default


def _sanitize_param(p: _DraftParameter, valid_prop_names: set[str]) -> _DraftParameter:
    """Clamp enums + drop object_type_ref pointing at non-existent objects.

    ``object_type_ref`` may legitimately reference *another* ObjectType (not
    the affected one), which we can't fully validate without loading all OTs.
    We keep it as-is (the frontend resolves it); we only clamp the scalar
    enums.
    """
    return _DraftParameter(
        api_name=p.api_name,
        display_name=p.display_name or p.api_name,
        data_type=_clamp(p.data_type.upper(), _VALID_DATA_TYPES, "STRING"),
        required=p.required,
        default=p.default,
        description=p.description,
        default_source=_clamp(p.default_source, _VALID_DEFAULT_SOURCES, "static"),
        default_source_field=p.default_source_field,
        readonly=p.readonly,
        hidden=p.hidden,
        pattern=p.pattern,
        error_message=p.error_message,
        enum_values=p.enum_values,
        object_type_ref=p.object_type_ref,
        is_object_set=p.is_object_set,
    )


def _sanitize_value_source(vs: _DraftValueSource) -> _DraftValueSource:
    source = _clamp(vs.source, _VALID_VALUE_SOURCES, "PARAMETER")
    value = vs.value
    # SYSTEM_CONTEXT value must be a known constant; else drop it.
    if source == "SYSTEM_CONTEXT" and value and value not in _VALID_SYSTEM_CONTEXT_VALUES:
        value = None
    return _DraftValueSource(source=source, value=value)


def _sanitize_ontology_rule(
    rule: _DraftOntologyRule, valid_param_names: set[str], valid_ot_names: set[str]
) -> _DraftOntologyRule | None:
    """Sanitize one ontology rule. Return None to drop entirely.

    Drops rules whose target_parameter references a non-existent parameter
    (hallucination), or whose CreateObject target_object_type is unknown.
    Keeps Modify/Upsert/Delete with dangling target_parameter (frontend will
    flag it) so the user sees what the LLM tried to do.
    """
    rtype = _clamp(rule.type, _VALID_RULE_TYPES, "ModifyObject")

    # target_parameter must reference a real parameter (for Modify/Upsert/Delete).
    target_param = rule.target_parameter
    needs_real_param = rtype in ("ModifyObject", "UpsertObject", "DeleteObject")
    if needs_real_param and target_param and target_param not in valid_param_names:
        # Drop: hallucinated parameter reference.
        return None

    # CreateObject target_object_type must be a known ObjectType.
    target_ot = rule.target_object_type
    if rtype == "CreateObject" and target_ot and target_ot not in valid_ot_names:
        return None

    cleaned_props: dict[str, _DraftValueSource] = {}
    for prop_name, vs in rule.properties.items():
        if not prop_name or prop_name == "__empty__":
            continue
        cleaned_props[prop_name] = _sanitize_value_source(vs)

    return _DraftOntologyRule(
        type=rtype,
        target_parameter=target_param,
        target_object_type=target_ot,
        properties=cleaned_props,
        link_type=rule.link_type,
        source_parameter=rule.source_parameter,
        target_link_parameter=rule.target_link_parameter,
        condition=rule.condition,
        on_missing=_clamp(rule.on_missing, {"raise_not_found", "create"}, "raise_not_found"),
        description=rule.description,
    )


def _sanitize_effect(e: _DraftEffect) -> _DraftEffect:
    return _DraftEffect(
        type=_clamp(e.type, _VALID_EFFECT_TYPES, "notification"),
        config=e.config,
        trigger=_clamp(e.trigger, {"BEFORE_ONTOLOGY_CHANGE", "AFTER_ONTOLOGY_CHANGE"}, "AFTER_ONTOLOGY_CHANGE"),
        condition=e.condition,
    )


def _sanitize_draft(
    draft: ActionTypeDraft,
    obj_type: _ObjectTypeInfo,
) -> ActionTypeDraft:
    """Validate/repair an LLM-produced draft against the real ObjectType schema.

    Guards against hallucination (same technique as ``_sanitize_scaffold_result``):
      - clamp all enum fields to valid domains;
      - drop ontology_rules referencing non-existent parameters / object types;
      - reconcile operation_kind with the actual ontology_rules present;
      - default affected_object_type_api_name to the real target.
    """
    valid_prop_names = {p["api_name"] for p in obj_type.properties}
    # The affected OT is always a valid object-type target; the LLM may also
    # reference other OTs via object_type_ref (kept as-is, frontend resolves).
    valid_ot_names = {obj_type.api_name}
    valid_param_names = {p.api_name for p in draft.parameters}

    sanitized_params = [_sanitize_param(p, valid_prop_names) for p in draft.parameters]
    # Rebuild param name set after sanitization (api_name unchanged, but be safe).
    valid_param_names = {p.api_name for p in sanitized_params if p.api_name}

    sanitized_rules: list[_DraftOntologyRule] = []
    for r in draft.ontology_rules:
        cleaned = _sanitize_ontology_rule(r, valid_param_names, valid_ot_names)
        if cleaned is not None:
            sanitized_rules.append(cleaned)

    # Reconcile operation_kind with the sanitized ontology_rules.
    rule_types = {r.type for r in sanitized_rules}
    if rule_types:
        has_create = "CreateObject" in rule_types
        has_modify = bool(rule_types & {"ModifyObject", "UpsertObject"})
        has_delete = "DeleteObject" in rule_types
        active_kinds = [k for k in (has_create, has_modify, has_delete) if k]
        if len(active_kinds) == 1:
            inferred = "create" if has_create else "update" if has_modify else "delete"
        else:
            inferred = "mixed"
        operation_kind = inferred
    else:
        operation_kind = _clamp(draft.operation_kind, _VALID_OP_KINDS, "mixed")

    return ActionTypeDraft(
        api_name=draft.api_name,
        display_name=draft.display_name,
        description=draft.description,
        affected_object_type_api_name=obj_type.api_name,
        parameters=sanitized_params,
        rules=[
            _DraftRule(
                type=_clamp(r.type, {"constraint", "derivation", "validation"}, "constraint"),
                target=r.target,
                expression=r.expression,
                description=r.description,
            )
            for r in draft.rules
        ],
        submission_criteria=[
            _DraftSubmissionCriterion(
                expression=c.expression,
                error_message=c.error_message or "校验失败",
                description=c.description,
            )
            for c in draft.submission_criteria
        ],
        ontology_rules=sanitized_rules,
        effects=[_sanitize_effect(e) for e in draft.effects],
        risk_level=_clamp(draft.risk_level, _VALID_RISK_LEVELS, "low"),
        operation_kind=operation_kind,
        batch_enabled=draft.batch_enabled,
        confidence=max(0.0, min(1.0, draft.confidence)),
        pending_confirmations=draft.pending_confirmations,
    )


# ── Validation (verifier-guided loop) ────────────────────────────────────


def _simpleeval_syntax_check(expression: str, names: dict[str, Any]) -> tuple[bool, str]:
    """Check a simpleeval expression parses + evaluates against sample names.

    Returns (passed, error_message). Uses simpleeval's EvalWithCompoundTypes
    so ``and``/``or``/``in`` work. ``names`` provides a sample namespace so
    name-lookup errors surface as syntax/reference errors (not NameErrors).
    """
    if not expression or not expression.strip():
        return False, "表达式为空"
    try:
        from simpleeval import EvalWithCompoundTypes

        evaluator = EvalWithCompoundTypes(names=names)
        evaluator.eval(expression)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — any failure = invalid expression
        return False, f"{type(exc).__name__}: {exc}"


def _validate_draft(
    draft: ActionTypeDraft, obj_type: _ObjectTypeInfo
) -> list[str]:
    """Run real validators against the draft. Returns list of error strings.

    Empty list = draft is safe to hand to the user. Non-empty = feed back to
    the LLM for a repair round (CEGIS). Checks:
      - api_name pattern (camelCase) + non-empty display_name;
      - each parameter api_name non-empty + unique;
      - simpleeval syntax on every rule / submission_criteria / ontology_rule.condition;
      - ontology_rules structural consistency (Modify needs target_parameter,
        CreateObject needs target_object_type, etc.).
    """
    errors: list[str] = []
    import re

    api_name_pattern = re.compile(r"^[a-z][a-zA-Z0-9]*$")

    if not draft.api_name:
        errors.append("api_name 不能为空")
    elif not api_name_pattern.match(draft.api_name):
        errors.append(f"api_name '{draft.api_name}' 不符合 camelCase 规范（首字母小写，纯字母数字）")

    if not draft.display_name.strip():
        errors.append("display_name 不能为空")

    # Parameter uniqueness + non-empty api_name.
    seen_params: set[str] = set()
    for i, p in enumerate(draft.parameters):
        if not p.api_name:
            errors.append(f"parameters[{i}].api_name 不能为空")
            continue
        if p.api_name in seen_params:
            errors.append(f"parameters[{i}].api_name '{p.api_name}' 重复")
        seen_params.add(p.api_name)

    # Build a sample namespace for simpleeval checks: all parameter names +
    # a few system constants. Values are typed stubs matching common usage.
    sample_names: dict[str, Any] = {name: "sample" for name in seen_params}
    sample_names.update(
        {
            "CURRENT_USER_ID": "user1",
            "CURRENT_TIMESTAMP": "2024-01-01T00:00:00Z",
            "true": True,
            "false": False,
            "status": "open",
            "quantity": 1,
        }
    )

    # Rule expressions.
    for i, rule in enumerate(draft.rules):
        ok, msg = _simpleeval_syntax_check(rule.expression, sample_names)
        if not ok:
            errors.append(f"rules[{i}].expression 无效: {msg}")

    # Submission criteria.
    for i, c in enumerate(draft.submission_criteria):
        ok, msg = _simpleeval_syntax_check(c.expression, sample_names)
        if not ok:
            errors.append(f"submission_criteria[{i}].expression 无效: {msg}")
        if not c.error_message.strip():
            errors.append(f"submission_criteria[{i}].error_message 不能为空")

    # Ontology rules structural checks.
    pk_api_name = obj_type.primary_key
    for i, orule in enumerate(draft.ontology_rules):
        ctx = f"ontology_rules[{i}]"
        if orule.type in ("ModifyObject", "UpsertObject", "DeleteObject") and not orule.target_parameter:
            errors.append(f"{ctx}: {orule.type} 必须指定 target_parameter")
        if orule.type == "CreateObject" and not orule.target_object_type:
            errors.append(f"{ctx}: CreateObject 必须指定 target_object_type")
        if orule.type in ("ModifyObject", "UpsertObject") and not orule.properties:
            errors.append(f"{ctx}: {orule.type} 至少需要一条属性映射")
        # Modify must not touch primary key.
        if orule.type == "ModifyObject" and pk_api_name and pk_api_name in orule.properties:
            errors.append(f"{ctx}: ModifyObject 不可修改主键 '{pk_api_name}'")
        if orule.type in ("CreateLink", "DeleteLink") and not orule.link_type:
            errors.append(f"{ctx}: {orule.type} 必须指定 link_type")
        # Condition syntax.
        if orule.condition:
            ok, msg = _simpleeval_syntax_check(orule.condition, sample_names)
            if not ok:
                errors.append(f"{ctx}.condition 无效: {msg}")

    return errors


# ── ObjectType schema loading (deterministic) ───────────────────────────


async def _load_object_type_info(
    metadata: PostgresMetaStore,
    ontology_api_name: str,
    object_type_api_name: str,
) -> _ObjectTypeInfo:
    """Deterministically load the affected ObjectType's full schema.

    This is the anti-hallucination backbone: the LLM is given the real
    property names/types/primary key, so it never has to guess.
    """
    obj_type = await metadata.get_object_type_by_api_name(
        (await metadata.get_ontology(ontology_api_name)).id,
        object_type_api_name,
    )
    props = await metadata.get_properties(obj_type.id)
    prop_dicts: list[dict[str, Any]] = []
    for p in props:
        prop_dicts.append(
            {
                "api_name": p.api_name,
                "data_type": str(p.data_type),
                "nullable": p.nullable,
                "is_primary_key": p.is_primary_key,
                "is_title_property": getattr(p, "is_title_property", False),
                "description": p.description or "",
            }
        )
    return _ObjectTypeInfo(
        api_name=obj_type.api_name,
        display_name=obj_type.display_name,
        primary_key=obj_type.primary_key,
        title_property=getattr(obj_type, "title_property", None),
        storage_type=str(getattr(obj_type, "storage_type", "MANAGED")),
        properties=prop_dicts,
    )


# ── Public API ───────────────────────────────────────────────────────────


async def stream_action_type_draft(
    obj_type: _ObjectTypeInfo,
    natural_language: str,
    existing_action_api_names: list[str] | None = None,
) -> AsyncIterator[ActionTypeDraft]:
    """Stream a sanitized ActionTypeDraft from a natural-language description.

    Yields progressively-more-complete ``ActionTypeDraft`` partials (SSE-
    friendly). Each partial is sanitized against the real ObjectType schema
    before emission. After the stream completes, a final validation pass runs
    and — on failure — a repair round is attempted (the repaired draft is
    yielded as the final frame).

    The draft is NEVER persisted here. The caller (frontend ActionTypeEditor)
    must POST it to ``/actions/definitions/{ontology}/{action_type}`` to save.

    Args:
        obj_type: The affected ObjectType's schema, deterministically loaded
            by the caller (route layer) via ``_load_object_type_info``. Kept
            out of this function so the DB session can be closed before the
            (long-running) LLM stream starts.
        natural_language: The user's NL action intent.
        existing_action_api_names: Already-defined ActionType api_names, for
            uniqueness de-dup in the prompt (advisory).
    """
    prompt = _build_prompt(
        natural_language,
        obj_type,
        existing_action_api_names or [],
    )

    last_errors: list[str] = []
    final_draft: ActionTypeDraft | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        repair_hint = ""
        if last_errors:
            repair_hint = (
                "\n\n你上一轮产出的草稿校验失败，错误如下：\n- "
                + "\n- ".join(last_errors)
                + "\n请修正这些问题后重新生成完整的 ActionTypeDraft。"
            )

        attempt_draft: ActionTypeDraft | None = None
        try:
            async for partial in stream_structured(
                ActionTypeDraft, _SYSTEM_PROMPT, prompt + repair_hint
            ):
                sanitized = _sanitize_draft(partial, obj_type)
                attempt_draft = sanitized
                yield sanitized
        except Exception as exc:  # noqa: BLE001 — LLM stream failure
            _log.warning("ActionType draft stream attempt %d failed: %s", attempt, exc)
            last_errors = [f"llm_error: {exc}"]
            continue

        if attempt_draft is None:
            last_errors = ["empty_draft"]
            continue

        # Validate the final (most complete) partial of this attempt.
        errors = _validate_draft(attempt_draft, obj_type)
        if not errors:
            final_draft = attempt_draft
            last_errors = []
            break
        last_errors = errors
        _log.info("ActionType draft validation attempt %d failed: %s", attempt, errors)

    # If all retries exhausted with errors, yield a final frame carrying the
    # errors so the frontend can surface them. The last sanitized draft is
    # still useful (user can fix manually), so we yield it with a sentinel.
    if final_draft is None and last_errors:
        # Re-emit the last attempt's draft (if any) tagged with validation
        # errors via pending_confirmations, so the UI shows what to fix.
        # attempt_draft may be None if every attempt's stream failed.
        if attempt_draft is not None:
            tagged = attempt_draft.model_copy(
                update={
                    "pending_confirmations": list(attempt_draft.pending_confirmations)
                    + [f"⚠️ 校验未通过（已重试{_MAX_RETRIES}轮）: " + "; ".join(last_errors)]
                }
            )
            yield tagged


def draft_to_create(draft: ActionTypeDraft) -> ActionTypeCreate:
    """Convert a finalized ActionTypeDraft into an ActionTypeCreate payload.

    Called by the frontend (via the REST define endpoint) once the user has
    reviewed + confirmed the draft. Provided here as a convenience so the
    shape translation lives next to the draft definition.
    """
    return ActionTypeCreate(
        api_name=draft.api_name,
        display_name=draft.display_name,
        description=draft.description,
        affected_object_type_api_name=draft.affected_object_type_api_name,
        parameters=[
            ActionTypeParameter(
                api_name=p.api_name,
                display_name=p.display_name,
                data_type=DataType(p.data_type),
                required=p.required,
                default=p.default,
                description=p.description,
                default_source=p.default_source,  # type: ignore[arg-type]
                default_source_field=p.default_source_field,
                readonly=p.readonly,
                hidden=p.hidden,
                pattern=p.pattern,
                error_message=p.error_message,
                enum_values=p.enum_values,
                object_type_ref=p.object_type_ref,
                is_object_set=p.is_object_set,
            )
            for p in draft.parameters
        ],
        rules=[
            ActionRule(
                type=r.type,  # type: ignore[arg-type]
                target=r.target,
                expression=r.expression,
                description=r.description,
            )
            for r in draft.rules
        ],
        submission_criteria=[
            SubmissionCriterion(
                expression=c.expression,
                error_message=c.error_message,
                description=c.description,
            )
            for c in draft.submission_criteria
        ],
        ontology_rules=[
            OntologyRule(
                type=r.type,  # type: ignore[arg-type]
                target_parameter=r.target_parameter,
                target_object_type=r.target_object_type,
                target_path=None,
                properties={
                    k: ValueSource(source=v.source, value=v.value)  # type: ignore[arg-type]
                    for k, v in r.properties.items()
                },
                link_type=r.link_type,
                source_parameter=r.source_parameter,
                target_link_parameter=r.target_link_parameter,
                condition=r.condition,
                on_missing=r.on_missing,  # type: ignore[arg-type]
                description=r.description,
            )
            for r in draft.ontology_rules
        ],
        effects=[
            ActionEffectConfig(
                type=e.type,  # type: ignore[arg-type]
                config=e.config,
                trigger=e.trigger,  # type: ignore[arg-type]
                condition=e.condition,
            )
            for e in draft.effects
        ],
        risk_level=draft.risk_level,  # type: ignore[arg-type]
        operation_kind=draft.operation_kind,  # type: ignore[arg-type]
        batch_enabled=draft.batch_enabled,
    )
