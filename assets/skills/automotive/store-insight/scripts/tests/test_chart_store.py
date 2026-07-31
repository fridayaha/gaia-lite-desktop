"""chart_store.py 单元测试"""

import json
import os
import tempfile

import pytest

# 被测模块
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..")
)
from chart_store import (
    _extract_values,
    _find_numeric_column,
    auto_detect_chart_type,
)


# ── _extract_values ──


class TestExtractValues:
    def test_normal_numbers(self) -> None:
        data = {
            "rows": [{"val": 10}, {"val": 20}, {"val": 30}],
            "columns": ["val"],
        }
        assert _extract_values(data, "val") == [10.0, 20.0, 30.0]

    def test_none_and_empty(self) -> None:
        data = {
            "rows": [{"val": None}, {"val": ""}, {"val": 5}],
            "columns": ["val"],
        }
        assert _extract_values(data, "val") == [0.0, 0.0, 5.0]

    def test_invalid_values_become_zero(self) -> None:
        data = {
            "rows": [{"val": "abc"}, {"val": "3.14"}],
            "columns": ["val"],
        }
        assert _extract_values(data, "val") == [0.0, 3.14]

    def test_empty_rows(self) -> None:
        data = {"rows": [], "columns": ["val"]}
        assert _extract_values(data, "val") == []


# ── _find_numeric_column ──


class TestFindNumericColumn:
    def test_finds_second_numeric(self) -> None:
        data = {
            "rows": [{"name": "张三", "sales": 10}],
            "columns": ["name", "sales"],
        }
        assert _find_numeric_column(data, skip_first=True) == "sales"

    def test_all_string_fallback(self) -> None:
        data = {
            "rows": [{"a": "x", "b": "y"}],
            "columns": ["a", "b"],
        }
        # 第二列也是字符串，返回第二列名作为 fallback
        assert _find_numeric_column(data, skip_first=True) == "b"

    def test_empty_rows(self) -> None:
        data = {"rows": [], "columns": ["name", "sales"]}
        assert _find_numeric_column(data, skip_first=True) == "value"


# ── auto_detect_chart_type ──


class TestAutoDetectChartType:
    def test_few_rows_two_cols_is_bar(self) -> None:
        data = {
            "rows": [{"name": "A", "val": 1}, {"name": "B", "val": 2}],
            "columns": ["name", "val"],
        }
        assert auto_detect_chart_type(data) == "bar"

    def test_many_rows_is_line(self) -> None:
        data = {
            "rows": [{"date": f"2026-07-{i:02d}", "val": i} for i in range(1, 21)],
            "columns": ["date", "val"],
        }
        # 20 rows > 15，不满足 bar 条件，走 line
        assert auto_detect_chart_type(data) == "line"

    def test_few_rows_three_cols_is_bar(self) -> None:
        data = {
            "rows": [{"a": 1, "b": 2, "c": 3}],
            "columns": ["a", "b", "c"],
        }
        assert auto_detect_chart_type(data) == "bar"


# ── 图表生成集成测试（验证文件输出）──


class TestChartOutput:
    def test_bar_chart_creates_png(self) -> None:
        from chart_store import make_bar

        data = {
            "columns": ["name", "sales"],
            "rows": [
                {"name": "张三", "sales": 30},
                {"name": "李四", "sales": 20},
                {"name": "王五", "sales": 10},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outpath = os.path.join(tmpdir, "test_bar.png")
            make_bar(data, "测试柱状图", outpath)
            assert os.path.exists(outpath)
            assert os.path.getsize(outpath) > 0

    def test_line_chart_creates_png(self) -> None:
        from chart_store import make_line

        data = {
            "columns": ["date", "sales", "test_drives"],
            "rows": [
                {"date": "2026-07-01", "sales": 5, "test_drives": 10},
                {"date": "2026-07-02", "sales": 8, "test_drives": 12},
                {"date": "2026-07-03", "sales": 3, "test_drives": 7},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outpath = os.path.join(tmpdir, "test_line.png")
            make_line(data, "测试折线图", outpath)
            assert os.path.exists(outpath)
            assert os.path.getsize(outpath) > 0

    def test_pie_chart_creates_png(self) -> None:
        from chart_store import make_pie

        data = {
            "columns": ["role", "count"],
            "rows": [
                {"role": "销售顾问", "count": 5},
                {"role": "试驾专员", "count": 3},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outpath = os.path.join(tmpdir, "test_pie.png")
            make_pie(data, "测试饼图", outpath)
            assert os.path.exists(outpath)
            assert os.path.getsize(outpath) > 0

    def test_scatter_chart_creates_png(self) -> None:
        from chart_store import make_scatter

        data = {
            "columns": ["visitors", "sales", "name"],
            "rows": [
                {"visitors": 100, "sales": 10, "name": "张三"},
                {"visitors": 200, "sales": 25, "name": "李四"},
                {"visitors": 150, "sales": 18, "name": "王五"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outpath = os.path.join(tmpdir, "test_scatter.png")
            make_scatter(data, "测试散点图", outpath)
            assert os.path.exists(outpath)
            assert os.path.getsize(outpath) > 0

    def test_empty_data_bar_exits(self) -> None:
        from chart_store import make_bar

        data = {"columns": ["name", "sales"], "rows": []}
        with pytest.raises(SystemExit):
            make_bar(data, "空数据", "/tmp/should_not_exist.png")
