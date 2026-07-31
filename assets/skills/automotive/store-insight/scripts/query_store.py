#!/usr/bin/env python3
"""
门店销售数据查询工具 — query_store.py
连接 PostgreSQL 执行 SQL，输出 JSON 到 stdout。
密码从环境变量读取，不硬编码。
连接强制只读模式，防止 LLM 生成的 SQL 修改数据。
"""
# /// script
# dependencies = ["psycopg2-binary"]
# ///

import argparse
import json
import os
import sys
from typing import Any

import psycopg2
import psycopg2.extras

DB_USER = os.environ.get("DB_USER", "unionagents")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "store_insight")

# 查询超时（秒），防止恶劣 SQL 挂死连接
QUERY_TIMEOUT_SECONDS = 30


def get_connection() -> psycopg2.extensions.connection:
    """建立只读连接，设置查询超时。"""
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        options=f"-c statement_timeout={QUERY_TIMEOUT_SECONDS * 1000}",
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def get_schema(cursor: psycopg2.extensions.cursor) -> dict[str, list[dict[str, str]]]:
    """获取所有表结构。"""
    cursor.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
        """
    )
    tables: dict[str, list[dict[str, str]]] = {}
    for tbl, col, typ, nul in cursor.fetchall():
        tables.setdefault(tbl, []).append(
            {"column": col, "type": typ, "nullable": nul}
        )
    return tables


def execute_query(sql: str, output_path: str | None = None) -> None:
    """执行 SQL 查询并输出 JSON 结果。"""
    try:
        conn = get_connection()
        cur = conn.cursor()

        # 二次保险：SET 只读 + 超时
        cur.execute("SET transaction_read_only = on;")
        cur.execute(
            f"SET statement_timeout = '{QUERY_TIMEOUT_SECONDS * 1000}ms';"
        )

        cur.execute(sql)

        if cur.description:
            cols = [desc[0] for desc in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            result: dict[str, Any] = {
                "columns": cols,
                "rows": rows,
                "row_count": len(rows),
            }
        else:
            result = {"affected_rows": cur.rowcount}

        cur.close()
        conn.close()

        output = json.dumps(result, ensure_ascii=False, indent=2, default=str)

        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)

        print(output)

    except psycopg2.errors.QueryCanceled:
        print(
            json.dumps(
                {
                    "error": f"查询超时（{QUERY_TIMEOUT_SECONDS}s），请简化查询或缩小时间范围",
                    "sql": sql,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except psycopg2.OperationalError as e:
        print(
            json.dumps(
                {"error": f"数据库连接失败: {e}", "sql": sql},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(
            json.dumps({"error": str(e), "sql": sql}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="门店销售数据查询工具")
    parser.add_argument("--sql", type=str, help="要执行的 SQL 语句（只读）")
    parser.add_argument("--output", type=str, help="保存结果到 JSON 文件")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印 SQL 不执行"
    )
    parser.add_argument("--schema", action="store_true", help="打印表结构")
    args = parser.parse_args()

    if args.schema:
        conn = get_connection()
        cur = conn.cursor()
        tables = get_schema(cur)
        cur.close()
        conn.close()
        print(json.dumps(tables, ensure_ascii=False, indent=2))
        return

    if not args.sql:
        parser.print_help()
        sys.exit(1)

    if args.dry_run:
        print(
            json.dumps(
                {"dry_run": True, "sql": args.sql}, ensure_ascii=False, indent=2
            )
        )
        return

    execute_query(args.sql, args.output)


if __name__ == "__main__":
    main()
