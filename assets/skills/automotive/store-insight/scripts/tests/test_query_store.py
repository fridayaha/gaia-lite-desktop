"""query_store.py 单元测试"""

import json
import os
import subprocess
import sys

import pytest

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "query_store.py"
)


class TestDryRun:
    """测试 --dry-run 模式（不连接数据库）。"""

    def test_dry_run_outputs_sql(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--sql", "SELECT 1;", "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["sql"] == "SELECT 1;"

    def test_no_sql_shows_help(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1


class TestGetSchema:
    """测试 get_schema 函数（mock 数据库）。"""

    def test_schema_structure(self) -> None:
        """验证 get_schema 返回正确的表结构字典。"""
        from unittest.mock import MagicMock

        # 模拟 cursor.fetchall 返回
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("employees", "id", "integer", "NO"),
            ("employees", "name", "character varying", "YES"),
            ("daily_sales", "employee_id", "integer", "NO"),
        ]

        from query_store import get_schema

        tables = get_schema(mock_cursor)
        assert "employees" in tables
        assert "daily_sales" in tables
        assert tables["employees"][0]["column"] == "id"
        assert tables["employees"][1]["column"] == "name"
        assert tables["daily_sales"][0]["column"] == "employee_id"
