"""OntologySqlCompiler — Step 4 path B (text2sql) core.

Compiles a LLM-generated "logical SQL" (ObjectType api_name as table name,
property api_name as column name) into a physical Doris or Trino dialect
SQL string, enforcing the three ontology guardrails at compile time:

  1. Table guardrail: every table must be a defined ObjectType
  2. Column guardrail: every column must be a defined Property of its
     owning ObjectType
  3. Join guardrail: every JOIN pair must be a defined LinkType

Literal values are extracted into a ``params`` list and replaced with ``?``
placeholders (parameterized binding) — injection-safe, superseding the
hand-written ``_sql_literal`` escaping in ObjectQueryService.

Design (see ADR-012 §「Step 4 路径B」 + §「技术可行性验证证据」):
- Two-pass traversal: pass 1 collects alias→ObjectType + CTE defs +
  subquery output cols; pass 2 rewrites. Otherwise column-owner resolution
  fails (SqlGlot 30.x depth-first rewrites Table.name before Column is
  visited, breaking alias lookup).
- SqlGlot 30.x args keys are ``from_`` / ``with_`` (not ``from`` / ``with``).
- Column-owner resolution three-tier fallback: alias prefix → alias_map;
  CTE/subquery alias → output-cols set (trust inner already validated);
  no prefix → single-table fallback (ambiguous multi-table → raise, let
  LLM add prefix).
- Recursive rewrite changes Table.name to physical; column resolution
  must tolerate both forms (physical-name reverse lookup).

Scope (Phase 1, ADR-012 §「分阶段实施计划」):
  Supported: single-table filter/sort/page, JOIN ≤5 tables (via LinkType),
  subqueries, multi-dim aggregation + GROUP BY + HAVING, window functions
  (ROW_NUMBER/RANK OVER), custom arithmetic, ratio (aggregation division),
  time functions (DATE_FORMAT/YEAR/MONTH).
  NOT supported (reject → fall back to atomic-tool chaining):
  CTE (WITH), complex self-join, YoY SELF JOIN, window+ratio combo, UNION.
  UPDATE/INSERT never handled — route to Action tools (ADR-012 决策三).
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Literal, Protocol

import sqlglot
from sqlglot import exp

from ontology.core.exceptions import OntologyError

logger = logging.getLogger(__name__)

Dialect = Literal["doris", "trino", "duckdb"]

# Parse LLM SQL as MySQL dialect — the most permissive common grammar,
# well-represented in LLM training data.
_READ_DIALECT = "mysql"


class OntologySchemaProvider(Protocol):
    """Read-only view of the ontology schema needed by the compiler.

    Decouples the compiler from PostgresMetaStore so it can be unit-tested
    with an in-memory provider. Real impl loads from metadata layer.
    """

    def object_types(self) -> dict[str, str]:
        """ObjectType api_name → Doris physical table name (e.g. idx_airline__order).

        This is the **Doris** name for MANAGED OTs and the three-part
        external locator (``catalog.schema.table``) for VIRTUAL OTs. The
        compiler derives the Trino name for MANAGED OTs separately (see
        ``storage_types`` + ``ontology_api_name``) since Trino sees them as
        ``iceberg.ontology.<snake_type>``.
        """

    def properties(self) -> dict[str, dict[str, str]]:
        """ObjectType api_name → { property api_name → backing_column }."""

    def links(self) -> set[tuple[str, str]]:
        """LinkType pairs as (source_ot, target_ot); bidirectional set."""

    def physical_to_object_type(self) -> dict[str, str]:
        """Reverse map: physical table name → ObjectType api_name."""

    def storage_types(self) -> dict[str, str]:
        """ObjectType api_name → storage_type ("MANAGED" | "VIRTUAL").

        Drives dialect-aware physical naming: MANAGED OTs compile to
        ``iceberg.ontology.<snake>`` on Trino (vs ``idx_<ont>__<type>`` on
        Doris), VIRTUAL OTs keep their external three-part locator in both.
        Default "MANAGED" for OTs not listed (backward compat with stubs
        that only model MANAGED tables).
        """

    def trino_table_refs(self) -> dict[str, str]:
        """ObjectType api_name → Trino physical table reference.

        MANAGED OTs → ``iceberg.ontology.<snake_type>`` (visible via the
        ``iceberg`` catalog, namespace ``ontology``); VIRTUAL OTs → their
        external ``<catalog>.<schema>.<table>`` locator. Used by the
        compiler only for the Trino dialect; the Doris dialect uses
        ``object_types()`` (the ``idx_<ont>__<type>`` name). Default: fall
        back to ``object_types()`` for OTs not listed (backward compat).
        """

    def duckdb_table_refs(self) -> dict[str, str]:
        """ObjectType api_name → DuckDB physical table reference (lite 桌面版).

        lite 版无 Iceberg/Doris，MANAGED 托管表不做（红线下砍掉）；VIRTUAL OTs →
        ``src_<ds>.<schema>.<table>``（catalog = DuckDB ATTACH 别名 ``src_<datasource
        api_name>``，见 B4 连接器 ``to_duckdb_attach``）。MANAGED OTs 在 lite 查询
        路径会被 guard 拦截，故此 map 仅覆盖 VIRTUAL。Default: 回退 ``object_types()``
        （向后兼容 stub provider）。
        """


class OntologySqlCompiler:
    """Compile logical SQL → physical Doris/Trino SQL with ontology guardrails."""

    def __init__(self, schema: OntologySchemaProvider) -> None:
        self._schema = schema
        self.params: list[Any] = []
        # alias → ObjectType api_name (pass 1 fills, pass 2 uses)
        self._alias_map: dict[str, str] = {}
        # CTE name → main ObjectType (for column-owner fallback)
        self._cte_defs: dict[str, str] = {}
        # subquery/CTE alias → output column names (trust inner validated)
        self._subquery_outputs: dict[str, set[str]] = {}

    def compile(self, logical_sql: str, dialect: Dialect) -> tuple[str, list[Any]]:
        """Compile logical SQL to (physical_sql, params).

        Raises OntologyError(code=INVALID_TABLE|INVALID_COLUMN|INVALID_JOIN|
        UNSUPPORTED_SQL|SQL_PARSE_ERROR) on guardrail / scope violations.
        """
        self.params = []
        self._alias_map = {}
        self._cte_defs = {}
        self._subquery_outputs = {}
        try:
            ast = sqlglot.parse_one(logical_sql, read=_READ_DIALECT)
        except Exception as e:  # noqa: BLE001 — surface as OntologyError
            raise OntologyError(f"SQL 解析失败: {e}", code="SQL_PARSE_ERROR") from e
        assert isinstance(ast, exp.Expression)

        # Scope guard: reject statements outside Phase 1 support.
        self._enforce_scope(ast)

        self._pass1_collect(ast)
        self._expand_stars(ast)
        out_aliases = self._collect_output_aliases(ast)
        ast = self._rewrite(ast, dialect, out_aliases)
        return ast.sql(dialect=dialect), self.params

    def involved_object_types(self, logical_sql: str) -> list[str]:
        """Return the deduplicated ObjectType api_names referenced in the SQL.

        Independent of ``compile``'s internal state (``_alias_map`` is reset
        on each ``compile`` call and the service compiles twice — Doris then
        Trino), so this parses the SQL fresh. Used by
        ``ObjectQueryService.execute_compiled_sql`` to apply access checks,
        storage routing, and column remapping across EVERY joined OT rather
        than a single caller-supplied "anchor" OT (design decision C).

        Only top-level ObjectType references are collected — CTE/subquery
        inner tables are validated recursively by ``compile`` already; their
        OTs are surfaced here too so access checks cover them.
        """
        try:
            ast = sqlglot.parse_one(logical_sql, read=_READ_DIALECT)
        except Exception as e:  # noqa: BLE001
            raise OntologyError(f"SQL 解析失败: {e}", code="SQL_PARSE_ERROR") from e
        ots: list[str] = []
        seen: set[str] = set()
        for t in ast.find_all(exp.Table):
            ot = self._resolve_object_type(t.name)
            if ot and ot not in seen:
                seen.add(ot)
                ots.append(ot)
        return ots

    # ── Scope enforcement (Phase 1 supported set) ─────────────────────────

    def _enforce_scope(self, ast: exp.Expression) -> None:
        """Reject SQL constructs outside the supported scope (fall back to tools)."""
        # UPDATE / INSERT / DELETE → Action path, not here.
        if isinstance(ast, (exp.Update, exp.Insert, exp.Delete)):
            raise OntologyError("text2sql 仅支持 SELECT；写操作请走 Action 工具", code="UNSUPPORTED_SQL")
        # UNION / INTERSECT / EXCEPT — still out of scope (Phase 3+).
        if isinstance(ast, (exp.Union, exp.Intersect, exp.Except)):
            raise OntologyError("UNION/INTERSECT/EXCEPT 暂不支持", code="UNSUPPORTED_SQL")
        # CTE (WITH) IS now supported (Phase 2): pass 1 collects CTE defs
        # and treats CTE names as virtual tables whose columns are trusted
        # (the inner SELECT is validated recursively).

    # ── Pass 1: collect alias / CTE / subquery output cols ────────────────

    def _pass1_collect(self, ast: exp.Expression) -> None:
        # ObjectType-table aliases
        for t in ast.find_all(exp.Table):
            if t.name in self._schema.object_types():
                self._alias_map[t.alias or t.name] = t.name
        # Subqueries (FROM/JOIN Subquery nodes) — collect output cols + owner
        for sq in ast.find_all(exp.Subquery):
            alias = sq.alias
            if alias:
                self._subquery_outputs[alias] = self._collect_output_cols(sq.this)
                inner_tables = [t.name for t in sq.this.find_all(exp.Table) if t.name in self._schema.object_types()]
                if len(inner_tables) == 1:
                    self._alias_map[alias] = inner_tables[0]
        # CTEs (WITH clause) — treat each CTE as a virtual table: record its
        # output cols (trusted, inner SELECT is validated recursively) and
        # its main ObjectType (for column-owner fallback when the CTE has a
        # single inner table). CTE names are skipped at Table-rewrite time.
        with_node = ast.args.get("with") or ast.args.get("with_")
        if with_node:
            ctes = with_node.expressions if hasattr(with_node, "expressions") else [with_node]
            for cte in ctes:
                if not isinstance(cte, exp.CTE):
                    continue
                cte_name = cte.alias
                if not cte_name:
                    continue
                self._subquery_outputs[cte_name] = self._collect_output_cols(cte.this)
                inner_tables = [t.name for t in cte.this.find_all(exp.Table) if t.name in self._schema.object_types()]
                if len(inner_tables) == 1:
                    self._alias_map[cte_name] = inner_tables[0]
                    self._cte_defs[cte_name] = inner_tables[0]

    def _expand_stars(self, ast: exp.Expression) -> None:
        """Expand top-level ``SELECT *`` to explicit columns in-place.

        ``SELECT *`` passed through to Doris/Trino yields bare physical
        column names; when two joined OTs share an api_name (e.g. both have
        ``id``, ``status``), the duplicate columns collide and one value is
        silently dropped (the DB result row is a dict keyed by column name).
        Expanding ``*`` here to ``<alias>.<col> AS <api>`` (with an OT prefix
        on collision) prevents the loss.

        Only a Star whose parent is a Select is expanded — ``COUNT(*)`` and
        other function-internal Stars are left alone. CTE/subquery-output
        Stars (``SELECT * FROM cte``) are trusted (inner already validated).
        """
        for select in list(ast.find_all(exp.Select)):
            new_exprs: list[exp.Expr] = []
            changed = False
            for expr in select.expressions:
                if isinstance(expr, exp.Star) and isinstance(expr.parent, exp.Select):
                    expanded = self._expand_one_star(select, expr)
                    if expanded is not None:
                        new_exprs.extend(expanded)
                        changed = True
                        continue
                new_exprs.append(expr)
            if changed:
                select.set("expressions", new_exprs)

    def _expand_one_star(self, select: exp.Select, star: exp.Star) -> list[exp.Expr] | None:
        """Expand a single ``*`` to explicit ``<alias>.<col> AS <api>`` columns.

        Returns None when the Star is over a CTE/subquery (can't enumerate
        OT properties — trust inner) or no ObjectType table is in scope; in
        those cases the Star is left for ``_rewrite`` to handle as-is.
        """
        # Collect (table_alias, ot_api) pairs from FROM + JOINs.
        ot_refs: list[tuple[str, str]] = []
        from_clause = select.args.get("from") or select.args.get("from_")
        if from_clause:
            for t in from_clause.find_all(exp.Table):
                ot = self._table_to_object_type(t.name)
                if ot and ot in self._schema.object_types():
                    ot_refs.append((t.alias or t.name, ot))
        for j in select.args.get("joins", []) or []:
            for t in j.find_all(exp.Table):
                ot = self._table_to_object_type(t.name)
                if ot and ot in self._schema.object_types():
                    ot_refs.append((t.alias or t.name, ot))
        if not ot_refs:
            return None  # CTE/subquery-only FROM — trust inner.
        # First pass: collect api_names per OT to detect collisions.
        ot_apis: dict[str, list[str]] = {}  # ot_api → [api_name, ...]
        for _, ot in ot_refs:
            if ot not in ot_apis:
                props = self._schema.properties().get(ot, {})
                ot_apis[ot] = list(props.keys())
        # An api_name collides if >1 OT has it.
        api_owners: dict[str, list[str]] = {}
        for ot, apis in ot_apis.items():
            for api in apis:
                api_owners.setdefault(api, []).append(ot)
        # Build expanded columns: <alias>.<col> AS <api_or_OT_api>.
        # Column references use the LOGICAL api_name (the compiler's
        # _rewrite phase maps them to physical columns downstream).
        columns: list[exp.Expr] = []
        for tbl_alias, ot in ot_refs:
            for api in ot_apis.get(ot, []):
                col = exp.column(api, table=tbl_alias)
                if len(api_owners.get(api, [])) > 1:
                    # Collision: disambiguate with an OT-prefixed alias so
                    # both columns survive in the result dict.
                    columns.append(exp.alias_(col, f"{ot}_{api}"))
                else:
                    # No collision: bare column (no AS alias). The downstream
                    # _map_backing_to_api_multi remaps the physical column
                    # back to the api_name. Avoiding an AS alias here keeps
                    # out_aliases clean (a bare api_name alias would pollute
                    # the CTE/output-col trust check and skip the physical
                    # rewrite of same-named WHERE columns).
                    columns.append(col)
        return columns

    def _collect_output_cols(self, select_node: exp.Expression) -> set[str]:
        """Collect a SELECT's output column ALIASES (not bare column names).

        Only ``AS alias`` outputs are recorded — bare column references
        (e.g. ``SELECT amount FROM Order``) belong to the inner ObjectType
        and are validated against it when referenced; recording them as CTE
        output cols would let an invalid inner column escape validation
        (the CTE's own ``SELECT bogus FROM Order`` would be trusted instead
        of rejected). Aliases (``SUM(amount) AS total``) ARE true derived
        outputs that must be trusted as-is.
        """
        cols: set[str] = set()
        if not isinstance(select_node, exp.Select):
            return cols
        for proj in select_node.expressions:
            if isinstance(proj, exp.Alias):
                cols.add(proj.alias)
        return cols

    def _collect_output_aliases(self, ast: exp.Expression) -> set[str]:
        """All SELECT output aliases (skip validation — they're outputs)."""
        out: set[str] = set()
        for node in ast.walk():
            if isinstance(node, exp.Alias) and node.alias:
                out.add(node.alias)
        return out

    # ── Pass 2: rewrite + validate ────────────────────────────────────────

    def _rewrite(self, node: exp.Expression, dialect: Dialect, out_aliases: set[str]) -> exp.Expression:
        # Table → physical name. CTE names are virtual tables — skip
        # rewrite (SqlGlot emits them as-is; their columns were validated
        # recursively when the CTE's inner SELECT was rewritten).
        if isinstance(node, exp.Table):
            if node.name in self._cte_defs or node.name in self._subquery_outputs:
                return node
            ot = self._resolve_object_type(node.name)
            if ot is None:
                raise OntologyError(f"未知 ObjectType: {node.name!r}", code="INVALID_TABLE")
            physical = self._physical_name(ot, dialect)
            # Preserve the logical OT name as the table alias when the table
            # has no explicit alias. Column prefixes like ``Order.amount``
            # reference the table by name; after we rename it to a physical
            # three-part name (iceberg.ontology.order), that prefix would
            # dangle. Setting alias = logical name keeps prefixes resolvable
            # without rewriting every Column node.
            had_alias = bool(node.alias)
            logical_name = node.name
            # VIRTUAL tables carry a catalog.schema.table three-part locator
            # (external source via Trino federation); MANAGED tables on Trino
            # carry iceberg.ontology.<snake>. Both are split on '.' to emit a
            # catalog-qualified exp.Table so Trino resolves them correctly.
            # MANAGED on Doris is a single idx_<ont>__<type> identifier.
            if "." in physical:
                parts = physical.split(".")
                if len(parts) == 3:
                    catalog, schema, table = parts
                    node.set("this", exp.to_identifier(table, quoted=False))
                    node.set("db", exp.to_identifier(schema, quoted=False))
                    node.set("catalog", exp.to_identifier(catalog, quoted=False))
                else:  # 2-part (catalog.table) — unusual, treat as db.table
                    schema, table = parts
                    node.set("this", exp.to_identifier(table, quoted=False))
                    node.set("db", exp.to_identifier(schema, quoted=False))
            else:
                node.set("this", exp.to_identifier(physical, quoted=False))
            if not had_alias and logical_name:
                node.set("alias", exp.to_identifier(logical_name, quoted=False))
            return node

        if isinstance(node, exp.Column):
            col_api = node.name
            # Subquery/CTE output col with table prefix: trust inner.
            if node.table and node.table in self._subquery_outputs:
                if col_api in self._subquery_outputs[node.table]:
                    return node
            # No-prefix column referencing a CTE/subquery output col, OR
            # a same-SELECT output alias (HAVING/ORDER BY referencing
            # ``SELECT ... AS x``). Trust it (``_is_cte_output_col`` is the
            # precise check; the ``in out_aliases`` broad check is kept for
            # HAVING/ORDER BY referencing same-SELECT aliases).
            if not node.table and col_api in out_aliases:
                return node
            if not node.table and self._is_cte_output_col(node):
                return node
            owner_ot = self._resolve_owner(node)
            if owner_ot is None:
                raise OntologyError(
                    f"无法解析列归属: {col_api!r}（多表查询请加表前缀）",
                    code="CANNOT_RESOLVE_COLUMN_OWNER",
                )
            props = self._schema.properties().get(owner_ot, {})
            if col_api not in props:
                raise OntologyError(
                    f"未知 Property: {col_api!r} 不属于 ObjectType {owner_ot}",
                    code="INVALID_COLUMN",
                )
            node.set("this", exp.to_identifier(props[col_api], quoted=False))
            return node

        if isinstance(node, exp.Join):
            self._validate_join(node)

        # Literal → parameterized placeholder. BUT: LIMIT/OFFSET clauses
        # in Doris (and several DBs) reject parameterized placeholders, so
        # literals whose parent is Limit/Offset are inlined as-is (they're
        # numeric by SQL grammar, so inlining is injection-safe). Also skip
        # identifier-quoted literals.
        if isinstance(node, exp.Literal) and not isinstance(node.parent, exp.Identifier):
            if isinstance(node.parent, (exp.Limit, exp.Offset)):
                return node  # inline numeric literal
            # Preserve the literal's native type so Trino/Doris bind it
            # correctly (integer/float stay numeric, not varchar). SqlGlot
            # stores Literal.this as a str; is_string distinguishes string
            # literals (quoted) from numeric/bool literals (unquoted).
            if node.is_string:
                self.params.append(node.this)
            else:
                raw = node.this
                # Coerce to int/float so the DB driver binds the right type.
                try:
                    self.params.append(int(raw))
                except (TypeError, ValueError):
                    try:
                        self.params.append(float(raw))
                    except (TypeError, ValueError):
                        self.params.append(raw)
            return exp.Placeholder()

        # Recurse into children.
        for key, child in list(node.args.items()):
            if isinstance(child, list):
                node.set(
                    key,
                    [self._rewrite(c, dialect, out_aliases) for c in child if isinstance(c, exp.Expression)],
                )
            elif isinstance(child, exp.Expression):
                node.set(key, self._rewrite(child, dialect, out_aliases))
        return node

    def _physical_name(self, ot_api: str, dialect: Dialect) -> str:
        """Resolve a ObjectType's physical table name for the target dialect.

        - Doris: ``object_types()[ot]`` — the ``idx_<ont>__<type>`` index
          table (MANAGED) or the external three-part locator (VIRTUAL; Doris
          never executes queries touching VIRTUAL — service routing ensures
          that, so the value here is irrelevant for VIRTUAL on Doris).
        - Trino: ``trino_table_refs()[ot]`` — ``iceberg.ontology.<snake>``
          for MANAGED, external three-part locator for VIRTUAL. This is what
          makes cross-catalog federation JOINs (MANAGED + VIRTUAL) runnable
          on a single Trino query.

        Falls back to ``object_types()`` when ``trino_table_refs`` is absent
        or doesn't list the OT (backward compat with minimal stubs).
        """
        if dialect == "trino":
            trino_refs = self._safe_trino_refs()
            if ot_api in trino_refs:
                return trino_refs[ot_api]
        if dialect == "duckdb":
            duckdb_refs = self._safe_duckdb_refs()
            if ot_api in duckdb_refs:
                return duckdb_refs[ot_api]
        return self._schema.object_types()[ot_api]

    def _safe_trino_refs(self) -> dict[str, str]:
        """trino_table_refs() with backward-compat fallback (attr may be absent)."""
        fn = getattr(self._schema, "trino_table_refs", None)
        if fn is None:
            return {}
        try:
            refs: Any = fn()
        except Exception:
            return {}
        return refs if isinstance(refs, dict) else {}

    def _safe_duckdb_refs(self) -> dict[str, str]:
        """duckdb_table_refs() with backward-compat fallback (attr may be absent)."""
        fn = getattr(self._schema, "duckdb_table_refs", None)
        if fn is None:
            return {}
        try:
            refs: Any = fn()
        except Exception:
            return {}
        return refs if isinstance(refs, dict) else {}

    def _resolve_object_type(self, name: str) -> str | None:
        """Resolve a table name (ObjectType api_name OR physical) → ObjectType."""
        if name in self._schema.object_types():
            return name
        return self._schema.physical_to_object_type().get(name)

    def _table_to_object_type(self, name: str) -> str | None:
        """Resolve a FROM/JOIN table name to its ObjectType, including CTE/subquery aliases.

        Broader than ``_resolve_object_type``: also recognizes CTE names and
        subquery aliases (mapped to their single inner ObjectType in pass 1).
        Used by ``_resolve_owner``'s no-prefix fallback so a column like
        ``SELECT amount FROM t`` (t is a CTE over Order) resolves to Order.
        """
        if name in self._alias_map:
            return self._alias_map[name]
        if name in self._cte_defs:
            return self._cte_defs[name]
        return self._resolve_object_type(name)

    def _is_cte_output_col(self, col: exp.Column) -> bool:
        """Check if a no-prefix column is an output col of an enclosing CTE/subquery.

        Used to trust CTE/subquery output columns referenced without a table
        prefix in the outer SELECT (e.g. ``WITH t AS (SELECT amount FROM
        Order) SELECT amount FROM t`` — the outer ``amount`` is t's output,
        already validated inside the CTE). Walks up the SELECT ancestors and
        checks each one's FROM/JOIN for CTE/subquery names whose output set
        contains the column.
        """
        col_api = col.name
        select = col.find_ancestor(exp.Select)
        while select is not None:
            from_clause = select.args.get("from") or select.args.get("from_")
            candidates: list[str] = []
            if from_clause:
                for t in from_clause.find_all(exp.Table):
                    candidates.append(t.name)
            for j in select.args.get("joins", []) or []:
                for t in j.find_all(exp.Table):
                    candidates.append(t.name)
            for cand in candidates:
                if cand in self._subquery_outputs and col_api in self._subquery_outputs[cand]:
                    return True
            select = select.find_ancestor(exp.Select)
        return False

    def _resolve_owner(self, col: exp.Column) -> str | None:
        """Resolve which ObjectType a column belongs to.

        Three-tier fallback:
        1. table prefix → alias_map / cte_defs / subquery_outputs / physical
        2. no prefix → the single ObjectType in the enclosing SELECT's FROM/JOIN
        3. ambiguous (multi-table, no prefix) → None (caller raises)
        """
        tbl = col.table
        if tbl:
            if tbl in self._alias_map:
                return self._alias_map[tbl]
            if tbl in self._cte_defs:
                return self._cte_defs[tbl]
            if tbl in self._subquery_outputs:
                # Subquery alias with a single inner ObjectType → that OT.
                return self._alias_map.get(tbl)
            phys_map = self._schema.physical_to_object_type()
            if tbl in phys_map:
                return phys_map[tbl]
            return None
        # No prefix: look at the enclosing SELECT's direct FROM/JOIN tables.
        select = col.find_ancestor(exp.Select)
        if select:
            ots: set[str] = set()
            from_clause = select.args.get("from") or select.args.get("from_")
            if from_clause:
                for t in from_clause.find_all(exp.Table):
                    ot = self._table_to_object_type(t.name)
                    if ot:
                        ots.add(ot)
            for j in select.args.get("joins", []) or []:
                for t in j.find_all(exp.Table):
                    ot = self._table_to_object_type(t.name)
                    if ot:
                        ots.add(ot)
            if len(ots) == 1:
                return next(iter(ots))
        return None

    def _validate_join(self, join: exp.Join) -> None:
        """Guardrail 3: every JOIN pair must be a defined LinkType."""
        select = join.find_ancestor(exp.Select)
        if not select:
            return
        ots: set[str] = set()
        for t in select.find_all(exp.Table):
            ot = self._resolve_object_type(t.name)
            if ot:
                ots.add(ot)
        if len(ots) < 2:
            return
        links = self._schema.links()
        for a, b in itertools.combinations(ots, 2):
            if (a, b) in links or (b, a) in links:
                return
        raise OntologyError(
            f"ObjectType 组合 {ots} 之间未定义 LinkType，禁止 JOIN",
            code="INVALID_JOIN",
        )
