"""DVP golden-truth producer: run expected_sql against MySQL (DESIGN.md §5.2).

Read path: connect to the source MySQL (fixture-seeded dvp_benchmark db),
run each L*.sql with bound params, return rows. This is the "physical
direct-connect" oracle (DESIGN.md §2.2: read path has NO direct-connect
restriction; expected derived by物理 SQL, not hand-written).

The L*.sql files use ``:param`` placeholders → converted to MySQL ``%s``
positional placeholders, bound in order of appearance (parameter binding,
no string concat — anti-injection).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import aiomysql

log = logging.getLogger("golden_truth")

EXPECTED_SQL_DIR = Path(__file__).resolve().parents[1] / "data" / "expected_sql"

# DVP MySQL source (marketing-mysql container / localhost mapping, dvp_benchmark db).
DEFAULT_MYSQL = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "marketing123",
    "db": "dvp_benchmark",
    "charset": "utf8mb4",
}

_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


def _convert_sql(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Convert ``:name`` placeholders to positional ``%s`` for aiomysql.

    Preserves order of first appearance. Raises if a referenced param is
    missing (fail-loud). Escapes literal '%' (not part of %s) as %% for
    PyMySQL's format-string parameterization.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _PARAM_RE.finditer(sql):
        name = m.group(1)
        if name not in seen_set:
            seen_set.add(name)
            seen.append(name)
    missing = [n for n in seen if n not in params]
    if missing:
        raise ValueError(f"missing params for SQL: {missing}")
    conv_sql = _PARAM_RE.sub("%s", sql)
    # Escape literal '%' not followed by 's' (avoid PyMySQL format errors).
    conv_sql = re.sub(r"%(?!s)", "%%", conv_sql)
    return conv_sql, [params[n] for n in seen]


def load_sql(case_id: str) -> str:
    """Read the expected_sql file for a case id (e.g. 'L1', 'L12')."""
    p = EXPECTED_SQL_DIR / f"{case_id}.sql"
    if not p.exists():
        raise FileNotFoundError(f"expected SQL not found: {p}")
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines).strip()
    return cleaned.rstrip(";").strip()


async def run_expected(
    case_id: str,
    params: dict[str, Any],
    mysql: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a case's expected SQL against MySQL, return rows as dicts.

    DESIGN.md §5.2: 黄金真值 derivation, read-only on MySQL.
    For Tier2/Tier3 cases whose backing concept doesn't apply to VIRTUAL
    (e.g. L14 time-travel has no snapshot), returns empty list so the case
    XFAILs cleanly rather than ERRORing.
    """
    sql = load_sql(case_id)
    conv_sql, positional = _convert_sql(sql, params)
    cfg = {**DEFAULT_MYSQL, **(mysql or {})}
    conn = await aiomysql.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
        db=cfg["db"], autocommit=True, charset=cfg.get("charset", "utf8mb4"),
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(conv_sql, positional)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    except aiomysql.MySQLError as e:
        # 1146 = table doesn't exist. Return empty so Tier2/3 cases XFAIL.
        if getattr(e, "args", [None])[0] == 1146:
            log.warning("expected SQL %s: table missing (%s) → returning []", case_id, str(e)[:80])
            return []
        raise
    finally:
        conn.close()
