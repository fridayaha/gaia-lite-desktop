"""Ontology modelling capability — the ``ontology-modeling`` skill migrated
onto pydantic-ai's native ``Capability`` extension point (form A).

This is the Gaia translation of the ``ontology-modeling`` skill (originally a
pi skill at ``~/.pi/agent/skills/ontology-modeling/``). The skill's Palantir
methodology — six-step modelling, 13 red lines, data-type conventions,
confidence marking, ActionType semantic-contract discipline — is encoded as a
pydantic-ai ``Capability`` whose ``get_instructions()`` injects the methodology
into the AG-UI Agent's context **on demand**.

Why a Capability (form A), not a separate draft-generation endpoint (form B)?
---------------------------------------------------------------------------
The AG-UI Agent already mounts the write/action toolsets
(``define_object_type`` / ``add_property`` / ``define_link_type`` /
``invoke_action`` …) with HITL approval. What it lacked was the *modelling
discipline* that tells the LLM *when* to call which tool, *how* to pick data
types, *when* to split an M:N, and *what not* to put in an ActionType. That
discipline is exactly what the skill's methodology provides — and
pydantic-ai's ``Capability`` is the native extension point for "a bundle of
instructions (+ optional tools/hooks) the agent loads on demand" (see
https://pydantic.dev/docs/ai/core-concepts/capabilities/ — "If you already
keep your skills as Markdown files … you can wrap each one in a Capability").

``defer_loading=True`` makes this skill-style progressive disclosure: on turns
that are pure queries / graph exploration, the capability stays collapsed to a
one-line catalog entry (id + description) and the methodology never enters the
prompt. When the user asks to *model* ("帮我建个订单本体", "define a customer
object"), the LLM calls the framework-managed ``load_capability`` tool and the
methodology lands in context for the rest of the run. This keeps query turns
unpolluted (the existing ``buildOntologyQueryPrompt`` stays lean) while giving
modelling turns the full Palantir-grade discipline.

What the methodology covers (and what it deliberately does NOT):
---------------------------------------------------------------
The instructions carry the skill's *cross-tool modelling rules* — things no
single tool docstring can express:

- Six-step modelling flow (entities → actions → rules → data/security →
  validation → iteration), mapped onto Gaia's tools.
- Data-type red lines (decimal for money, timestamp for time, boolean for
  bool — never string substitutes), aligned with Gaia's ``DataType`` enum.
- M:N must split into a middle ObjectType + two 1:N links (Gaia's LinkType
  only has ONE/MANY on the target side, so M:N = two reciprocal MANY links —
  the methodology tells the LLM to introduce a junction ObjectType instead).
- ActionType is a *semantic contract* only (parameters/modifies/constraints/
  side_effects) — never embed idempotent/retry/timeout/rollback (Gaia's
  ``ActionTypeCreate`` schema already omits these fields, so this is belt-
  and-braces guidance, not a schema guard).
- Confidence marking (confirmed/high/tentative) — the LLM tags its own
  suggestions so the user knows what to double-check.

What it does NOT do (delegated elsewhere, intentionally not duplicated):
- api_name pattern validation (PascalCase/camelCase) → tool docstrings +
  pydantic ``Field(pattern=…)`` + service-layer ``_resolve_*_api_name``.
- Same-ontology uniqueness / primary-key existence → service-layer
  ``ConflictError`` / ``ValidationError`` on ``define_object_type_batch``.
- VIRTUAL write guard → ``ActionService.execute_action`` rejects
  ``storage_type=VIRTUAL`` (architecture red line 9).
- Sensitive-property Markings → Gaia has no Markings mechanism; the skill's
  "sensitive property" red line is reduced to a methodology reminder (the
  LLM should flag sensitive properties in its description), not a data model.
- Data-pipeline mapping / security policy / version rollback / contract
  testing → Gaia has its own DataSourceService / IndexSyncService / soft-
  delete + impact-report / test suite; the skill's models for these do not
  match Gaia's architecture and are not migrated.

The methodology text is the single source of truth for modelling rules; the
companion doc ``docs/architecture/ontology-modeling-spec.md`` records the
mapping from the original skill (skill rule → Gaia mechanism) for future
maintainers.
"""

from __future__ import annotations

from pydantic_ai.capabilities import Capability

from ontology.tools.state import AppState

# ── The modelling methodology (skill → Gaia-aligned instructions) ─────────
#
# This is the body of the ``ontology-modeling`` skill, rewritten to reference
# Gaia's actual tools (``define_object_type`` / ``add_property`` /
# ``define_link_type`` / ``invoke_action``) and data types (the ``DataType``
# enum: STRING/INTEGER/LONG/BOOLEAN/DECIMAL/TIMESTAMP/…), instead of
# Palantir's Functions/Workshop/Actions vocabulary.
#
# It is injected ONLY when the LLM loads the ``ontology-modeling`` capability
# (``defer_loading=True``), so query/exploration turns are not polluted.

_MODELING_METHODOLOGY = """\
# 本体建模方法论（Ontology Modeling）

你正在协助用户进行本体建模。当前本体已由上下文限定（不要尝试访问其他本体）。
建模动作通过调用工具完成：`define_object_type`（建对象+属性）、`add_property`（加属性）、\
`define_link_type`（建关系）。这些写操作需用户审批（会进入批量审批面板，你无需自行处理审批，\
照常调用即可，审批通过后自动执行）。**禁止凭空捏造"已创建/已生效"等结果**——以工具返回为准。

## 一、建模六步法

1. **梳理实体与术语**：识别业务实体（ObjectType）。每个实体必须有独立业务身份，禁止把\
数据库表/中间表/报文结构当 ObjectType。用 `display_name`（中文友好名）统一术语，`api_name`\
用 PascalCase 英文（如 `ProductionOrder`），由你从 display_name 推导（中文名需翻译为英文）。
2. **拆解原子行为**：识别 ActionType，声明 parameters（入参）、modifies（影响的对象）、\
constraints（语义前置条件，仅命名，逻辑在 Function 层）、side_effects（成功后的事件/通知）。\
动作的执行顺序/编排不在本体层描述。
3. **业务规则下沉**：复杂计算、联动校验、风控规则不属于本体定义层——在本体里只用 constraints\
标注规则名，具体逻辑由 Functions 实现。不要把业务规则堆在对话里。
4. **数据与安全**：MANAGED 类型会落地 Iceberg+Doris（建表+索引同步自动触发）；VIRTUAL 类型\
仅可读（Trino 联邦），**禁止对 VIRTUAL 目标定义写入动作**。敏感属性（证件号/手机号/金额等）\
在 description 中显式标注"敏感"。
5. **合规自检**（提交前在脑中过一遍）：
   - 是否有重复实体、循环依赖、类型冲突？
   - 每个对象是否有且仅一个主键（非空、唯一）？
   - M:N 是否已拆分为中间实体 + 两组 1:N？
   - ActionType 是否混入了运行时策略（幂等/重试/超时/回滚）？——禁止。
6. **迭代**：先建核心实体，再补关系和动作。一次建模可并行调用多个 `define_object_type`\
（会在一个批量审批面板里聚合，用户一次确认）。

## 二、数据类型红线

| 业务场景 | 正确类型 | 禁止 |
|----------|----------|------|
| 金额/单价/费用 | `DECIMAL` | `DOUBLE`/`STRING`（精度丢失） |
| 业务时间 | `TIMESTAMP` | `STRING`（无法比较/计算） |
| 布尔状态 | `BOOLEAN` | `0/1`/`STRING` |
| 主键 | `STRING`（非空唯一） | 自增数值 |
| 数量/序号 | `INTEGER`/`LONG` | `STRING` |
| 固定分类/状态 | `STRING` + 在 description 列枚举值 | 散落数字 |

## 三、关系（LinkType）规则

- 基数只有 `ONE`/`MANY`（目标端计数）。1:1 用 ONE，1:N 用 MANY。
- **M:N 严禁直接建**：必须引入中间 ObjectType（命名如 `EmployeePostRel`），\
含自身主键 + 两端外键 + 关联属性，然后建两组 1:N（中间实体→两端各一个 MANY）。
- 关系命名用小驼峰动词短语（如 `contains`、`belongsTo`），正反向语义互逆，\
禁止用 `has`/`rel`/`link` 等模糊词。
- 禁止循环依赖链路（A→B→C→A）。

## 四、ActionType 语义契约

ActionType 只定义"做什么"，**严禁**包含：`idempotent`、`atomic`、`retry_strategy`、\
`rollback_action`、`timeout_seconds`。这些运行时策略属于 Function 层，不在本体定义。

契约要素：
- `parameters`：入参（名+类型+是否必填）。
- `modifies`：声明影响哪些 ObjectType（create/update/delete）。
- `constraints`：语义前置条件，**只列条件名**（如 `sufficient_stock`），不写实现。
- `side_effects`：成功后产生的事件/通知（如 `emit OrderSubmitted`）。

## 五、置信度标记

在描述你的建模建议时，对不确定的判断标注置信度：
- `confirmed`：用户明确提供或已确认。
- `high`：基于行业最佳实践推断，极大概率正确。
- `tentative`：存在多种选择，需用户确认（在文字中明确提出二选一）。

对于 `tentative` 项，必须在回复中显式列出待确认问题，不要自行假设。

## 六、并行建模

当用户要求一次性建模多个对象（如"帮我建整个订单系统的本体"），**在同一个回复里并行调用\
所有 `define_object_type`**（不要逐轮创建）。并行调用会聚合到一个批量审批面板，用户可一次\
确认全部，体验远好于逐个审批。审批执行后，一并汇总结果。
"""


def build_modeling_capability() -> Capability[AppState]:
    """Build the ontology-modelling capability (form A).

    Returns a ``Capability`` with ``defer_loading=True``: the methodology
    stays collapsed to a catalog entry (id + description) until the LLM calls
    the framework-managed ``load_capability`` tool on a modelling turn. This
    is the skill-style progressive-disclosure pattern documented at
    https://pydantic.dev/docs/ai/core-concepts/capabilities/#on-demand-capabilities
    ("If you've used Anthropic's Agent Skills, this is the same idea
    generalised").

    The capability carries ONLY instructions — no tools, no hooks, no model
    settings. The write/action tools it guides already live on the Agent
    (``build_write_toolset`` / ``build_action_toolset``, wrapped in
    ``MetadataApprovalToolset`` for HITL). Adding them again here would
    duplicate; the methodology just tells the LLM *how* to use them well.
    """
    return Capability[AppState](
        id="ontology-modeling",
        description=(
            "本体建模方法论。当用户要求从业务描述建模、创建对象类型/关系/动作、"
            '搭建本体（如"帮我建个订单本体""定义客户对象""设计工单系统"）时加载。'
            "提供 Palantir 级建模规范：六步法、数据类型红线、M:N 拆分、ActionType 语义契约、"
            "置信度标记。加载后指导你更好地调用 define_object_type / add_property / "
            "define_link_type 等工具。纯查询/探索类问题无需加载。"
        ),
        instructions=_MODELING_METHODOLOGY,
        defer_loading=True,
    )
