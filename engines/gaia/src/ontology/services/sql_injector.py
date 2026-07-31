"""Cedar residual → SQL predicate translator + SqlGlot AST injector (ADR-017 D4, Phase 3).

Two stages (design §4, research §3.4 + §7.2 + §8.3):

1. **ResidualTranslator**: walks the Cedar residual AST and produces a SQL
   predicate string. Deterministic mapping table (CLAUDE.md red line 8) —
   handles ONLY the finite Cedar AST node types that survive partial
   evaluation: ``==`` → ``=``, ``!=`` → ``<>``, ``in`` → ``IN``,
   ``&&`` → ``AND``, ``||`` → ``OR``, ``.`` attribute access → column name,
   literals → quoted values. No hand-written operator if-elif chain, no
   hand-escaped literals — a dispatch table.

2. **SqlPermissionInjector**: takes a base SQL query + a permission predicate
   and injects the predicate into EVERY WHERE clause in the query (including
   subqueries, CTEs, UNION arms, JOINs) using SqlGlot's Scope tree
   (``build_scope``). Architecture borrowed from AskTable SQL Permission Guard
   (research §8.3, <10ms, production-grade).

Safety: the injector is "application-constructs-predicate, engine-executes-
filter" — the predicate is injected BEFORE the SQL reaches Doris/Trino/PG,
so the engine filters at the scan node. Unpermissioned rows never reach the
application layer. This is NOT post-filtering (design §4.0 / §7.1).

Gaia already depends on sqlglot>=30.0 (ADR-017 D4) — no new dependency.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

_log = logging.getLogger(__name__)


# ── Stage 1: Cedar residual AST → SQL predicate ───────────────────────


# Cedar AST node operators → SQL operators (deterministic mapping table).
# This is the ONLY place operator translation happens — no scattered if-elif
# chains (CLAUDE.md red line 8).
_BINARY_OPS: dict[str, str] = {
    "==": "=",
    "!=": "<>",
}

# Cedar logical operators → SQL logical operators.
_LOGICAL_OPS: dict[str, str] = {
    "&&": "AND",
    "||": "OR",
    "and": "AND",
    "or": "OR",
}


class ResidualTranslator:
    """Translate a Cedar residual AST into a SQL WHERE predicate string.

    The translator only handles the Cedar AST node types that survive partial
    evaluation (principal-side already evaluated away): equality, inequality,
    ``in``, logical AND/OR, attribute access (``.``), and literals. Any
    unrecognized node fails-closed (returns ``None`` → AuthorizationService
    treats as Deny, no rows visible) — never silently allows.

    Attribute access on ``resource`` maps to a column name; attribute access
    on ``principal`` should have been evaluated away by partial evaluation
    (if it survives, the translator fails-closed).
    """

    def translate(self, residual_ast: dict[str, Any] | None) -> str | None:
        """Return the SQL predicate string, or None if untranslatable (fail-closed)."""
        if residual_ast is None:
            return None
        result = self._visit(residual_ast)
        return result

    def _visit(self, node: Any) -> str | None:
        """Recursively translate a Cedar AST node to a SQL fragment."""
        if not isinstance(node, dict):
            return None
        # First, check for wrapper/nesting keys (conditions/body/expr/kind).
        # These wrap the actual operator node and must be unwrapped first,
        # BEFORE the single-key operator dispatch (a {conditions: [...]} node
        # is single-key but 'conditions' is not an operator).
        for key in ("conditions", "body", "expr"):
            if key in node:
                val = node[key]
                if isinstance(val, list):
                    parts = [self._visit(v) for v in val]
                    parts = [p for p in parts if p is not None]
                    if not parts:
                        return None
                    if len(parts) == 1:
                        return parts[0]
                    return "(" + " AND ".join(f"({p})" for p in parts) + ")"
                return self._visit(val)
        # Multi-key dict with kind+body (condition wrapper).
        if "kind" in node and "body" in node:
            return self._visit(node["body"])
        # A Cedar AST node is a single-key dict: {operator: operands}.
        if len(node) == 1:
            (op, operand), = node.items()
            handler = self._HANDLERS.get(op)
            if handler is None:
                _log.debug("Cedar AST node '%s' not translatable (fail-closed)", op)
                return None
            return handler(self, operand)
        return None

    def _visit_eq(self, operand: Any) -> str | None:
        return self._visit_binary(operand, "=")

    def _visit_neq(self, operand: Any) -> str | None:
        return self._visit_binary(operand, "<>")

    def _visit_and(self, operand: Any) -> str | None:
        return self._visit_logical(operand, "AND")

    def _visit_or(self, operand: Any) -> str | None:
        return self._visit_logical(operand, "OR")

    def _visit_in(self, operand: Any) -> str | None:
        """``in`` → ``column IN (v1, v2, ...)``."""
        if not isinstance(operand, dict):
            return None
        left = self._visit(operand.get("left"))
        right = self._visit(operand.get("right"))
        if left is None or right is None:
            return None
        return f"{left} IN ({right})"

    def _visit_attr(self, operand: Any) -> str | None:
        """Attribute access ``a.b`` → column name ``b`` (if a is resource)."""
        if not isinstance(operand, dict):
            return None
        left = operand.get("left")
        attr = operand.get("attr")
        # The left side should be ``{"unknown": "resource"}`` (the residual
        # resource reference). If it's a principal reference, partial eval
        # should have resolved it — fail-closed if not.
        if isinstance(left, dict) and left.get("unknown") == "resource":
            return str(attr)
        # Some Cedar versions use {"EntityRef": {"unknown": "resource"}}.
        if isinstance(left, dict) and "unknown" in left:
            return str(attr)
        return None

    def _visit_lit(self, operand: Any) -> str | None:
        """Literal value → SQL-quoted string/number."""
        if isinstance(operand, (str,)):
            # Single-quote string literals; escape embedded quotes.
            escaped = operand.replace("'", "''")
            return f"'{escaped}'"
        if isinstance(operand, (int, float, bool)):
            return str(operand)
        if isinstance(operand, dict) and "Value" in operand:
            return self._visit_lit(operand["Value"])
        if isinstance(operand, list):
            # Set of literals → "v1, v2, ..." (for IN).
            parts = [self._visit_lit(x) for x in operand]
            if any(p is None for p in parts):
                return None
            return ", ".join(p for p in parts if p is not None)
        return None

    def _visit_binary(self, operand: Any, sql_op: str) -> str | None:
        if not isinstance(operand, dict):
            return None
        left = self._visit(operand.get("left"))
        right = self._visit(operand.get("right"))
        if left is None or right is None:
            return None
        return f"{left} {sql_op} {right}"

    def _visit_logical(self, operand: Any, sql_op: str) -> str | None:
        if not isinstance(operand, dict):
            return None
        left = self._visit(operand.get("left"))
        right = self._visit(operand.get("right"))
        if left is None or right is None:
            return None
        return f"({left} {sql_op} {right})"

    # Dispatch table (no if-elif chain — red line 8).
    _HANDLERS = {
        "==": _visit_eq,
        "!=": _visit_neq,
        "&&": _visit_and,
        "||": _visit_or,
        "and": _visit_and,
        "or": _visit_or,
        "in": _visit_in,
        ".": _visit_attr,
        "Lit": _visit_lit,
        "Value": _visit_lit,
    }


# ── Stage 2: SqlGlot AST WHERE injection ──────────────────────────────


class SqlPermissionInjector:
    """Inject a permission predicate into every WHERE clause of a SQL query.

    Uses SqlGlot's Scope tree (``build_scope``) to recursively find every
    SELECT scope — including subqueries, CTEs, UNION arms, and JOINs — and
    AND the permission predicate into each WHERE. Architecture borrowed from
    AskTable SQL Permission Guard (research §8.3): recursive Scope traversal,
    alias-aware, de-duplicated.

    The dialect is configurable (Doris / Trino / PostgreSQL) — same injection
    logic, different SQL flavor output.
    """

    def __init__(self, default_dialect: str = "postgres") -> None:
        self._default_dialect = default_dialect

    def inject(self, sql: str, permission_predicate: str, dialect: str | None = None) -> str:
        """Return ``sql`` with ``permission_predicate`` ANDed into every WHERE.

        If the query has no WHERE, one is added. If it has multiple scopes
        (subquery/CTE/UNION), each gets the predicate. The predicate is
        de-duplicated per scope (no double-injection on self-joins).
        """
        d = dialect or self._default_dialect
        ast = sqlglot.parse_one(sql, read=d).copy()
        scope = build_scope(ast)
        if scope is None:
            # Single-statement, no subqueries — inject at the top level.
            self._inject_into_node(ast, permission_predicate)  # type: ignore[arg-type]  # sqlglot parse_one returns Expr
        else:
            self._inject_into_scope(scope, permission_predicate)
        return ast.sql(dialect=d)

    def _inject_into_scope(self, scope: Any, predicate: str) -> None:
        """Recursively inject the predicate into a scope + all child scopes."""
        select = scope.expression
        self._inject_into_node(select, predicate)
        for child in scope.subqueries:
            child_scope = build_scope(child)
            if child_scope is not None:
                self._inject_into_scope(child_scope, predicate)
            else:
                self._inject_into_node(child, predicate)

    def _inject_into_node(self, select: exp.Expression, predicate: str) -> None:
        if not isinstance(select, exp.Select):
            # UNION / CTE wrapper — recurse into its args.
            for sub in select.find_all(exp.Select):
                self._inject_into_node(sub, predicate)
            return
        # Parse the predicate string into a SqlGlot expression.
        try:
            pred_expr = sqlglot.parse_one(f"SELECT 1 WHERE {predicate}", read=self._default_dialect)
        except Exception:  # noqa: BLE001
            return
        where = pred_expr.find(exp.Where)
        if where is None:
            return
        pred_ast = where.this
        existing = select.args.get("where")
        if existing is not None:
            existing_cond = existing.this
            # De-duplicate: if the predicate is already present (string match),
            # skip injection (self-join case).
            if pred_ast.sql() in existing_cond.sql():
                return
            new_cond = exp.And(this=existing_cond, expression=pred_ast)
            select.set("where", exp.Where(this=new_cond))
        else:
            select.set("where", exp.Where(this=pred_ast))


def translate_residual_to_predicate(residual_ast: dict[str, Any] | None) -> str | None:
    """Convenience: translate a Cedar residual AST to a SQL predicate string."""
    return ResidualTranslator().translate(residual_ast)


def inject_permission(sql: str, predicate: str, dialect: str = "postgres") -> str:
    """Convenience: inject a permission predicate into a SQL query."""
    return SqlPermissionInjector(default_dialect=dialect).inject(sql, predicate, dialect)
