"""draw_chart.py 测试（stdlib unittest）。

用 subprocess 调脚本（stdin 喂 JSON）+ 断言 stdout + PNG 文件有效性。
不 mock PIL（真画图，验证输出有效 PNG）。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPTS_DIR, "draw_chart.py")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _run(config_dict, env=None):
    """运行 draw_chart.py，返回 (stdout, returncode)。"""
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(config_dict),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout.strip(), proc.returncode


def _is_png(path):
    """检查文件是否存在 + 是有效 PNG（magic bytes）。"""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        return f.read(8) == PNG_MAGIC


def _run_chart(chart_type, td, output="output/chart.png"):
    """在临时目录 td 下运行指定图表类型，返回 (stdout, png_path)。"""
    env = {**os.environ, "HERMES_HOME": td}
    config = {
        "chart_type": chart_type,
        "title": f"Test {chart_type}",
        "x_label": "X",
        "y_label": "Y",
        "series": [{"name": "A", "values": [10, 20, 15, 25, 30]}],
        "categories": ["a", "b", "c", "d", "e"],
        "output": output,
    }
    stdout, rc = _run(config, env=env)
    return stdout, rc, os.path.join(td, output)


class TestChartTypes(unittest.TestCase):
    """5 种图表各自生成有效 PNG + stdout 输出正确相对路径。"""

    def test_bar(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("bar", td)
            self.assertEqual(rc, 0, f"bar failed: {stdout}")
            self.assertEqual(stdout, "output/chart.png")
            self.assertTrue(_is_png(png), "bar: not a valid PNG")
            self.assertGreater(os.path.getsize(png), 100, "bar: PNG too small")

    def test_line(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("line", td)
            self.assertEqual(rc, 0, f"line failed: {stdout}")
            self.assertEqual(stdout, "output/chart.png")
            self.assertTrue(_is_png(png), "line: not a valid PNG")

    def test_pie(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("pie", td)
            self.assertEqual(rc, 0, f"pie failed: {stdout}")
            self.assertTrue(_is_png(png), "pie: not a valid PNG")

    def test_scatter(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("scatter", td)
            self.assertEqual(rc, 0, f"scatter failed: {stdout}")
            self.assertTrue(_is_png(png), "scatter: not a valid PNG")

    def test_area(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("area", td)
            self.assertEqual(rc, 0, f"area failed: {stdout}")
            self.assertTrue(_is_png(png), "area: not a valid PNG")

    def test_radar(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("radar", td)
            self.assertEqual(rc, 0, f"radar failed: {stdout}")
            self.assertEqual(stdout, "output/chart.png")
            self.assertTrue(_is_png(png), "radar: not a valid PNG")
            self.assertGreater(os.path.getsize(png), 100, "radar: PNG too small")


class TestMultiSeries(unittest.TestCase):
    """多 series 图表（bar 分组并排 + 图例）。"""

    def test_bar_multi_series(self):
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "HERMES_HOME": td}
            config = {
                "chart_type": "bar",
                "title": "Multi Series",
                "series": [
                    {"name": "Series A", "values": [10, 20, 30], "color": "#FF0000"},
                    {"name": "Series B", "values": [15, 25, 35], "color": "#00FF00"},
                ],
                "categories": ["x", "y", "z"],
                "output": "output/multi.png",
            }
            stdout, rc = _run(config, env=env)
            self.assertEqual(rc, 0)
            self.assertTrue(_is_png(os.path.join(td, "output/multi.png")))

    def test_radar_multi_series(self):
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "HERMES_HOME": td}
            config = {
                "chart_type": "radar",
                "title": "销售四维度对比",
                "series": [
                    {"name": "销售A", "values": [85, 78, 82, 90]},
                    {"name": "销售B", "values": [70, 88, 75, 80]},
                ],
                "categories": ["沟通表达", "需求挖掘", "异议处理", "促单成交"],
                "output": "output/radar_multi.png",
            }
            stdout, rc = _run(config, env=env)
            self.assertEqual(rc, 0, f"radar multi failed: {stdout}")
            self.assertTrue(_is_png(os.path.join(td, "output/radar_multi.png")))


class TestCustomColors(unittest.TestCase):
    """自定义颜色 + 默认色板。"""

    def test_custom_color(self):
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "HERMES_HOME": td}
            config = {
                "chart_type": "line",
                "series": [{"name": "A", "values": [1, 2, 3], "color": "#ABCDEF"}],
                "categories": ["x", "y", "z"],
                "output": "output/custom.png",
            }
            stdout, rc = _run(config, env=env)
            self.assertEqual(rc, 0)
            self.assertTrue(_is_png(os.path.join(td, "output/custom.png")))


class TestOutputPath(unittest.TestCase):
    """输出路径 + 目录自动创建。"""

    def test_nested_output_dir(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("bar", td, output="deep/nested/dir/chart.png")
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "deep/nested/dir/chart.png")
            self.assertTrue(_is_png(png), "nested dir: PNG not found")

    def test_stdout_is_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, _ = _run_chart("bar", td, output="output/test_123.png")
            self.assertEqual(stdout, "output/test_123.png")
            self.assertFalse(stdout.startswith("/"), "stdout should be relative")


class TestErrors(unittest.TestCase):
    """错误分支：无效输入 → {"error":"..."}。"""

    def test_invalid_chart_type(self):
        stdout, rc = _run({"chart_type": "unknown", "series": [{"values": [1]}], "output": "o.png"})
        self.assertIn("error", stdout)
        self.assertIn("unsupported", stdout)

    def test_missing_chart_type(self):
        stdout, rc = _run({"series": [{"values": [1]}], "output": "o.png"})
        self.assertIn("error", stdout)

    def test_missing_series(self):
        stdout, rc = _run({"chart_type": "bar", "output": "o.png"})
        self.assertIn("error", stdout)

    def test_empty_series(self):
        stdout, rc = _run({"chart_type": "bar", "series": [], "output": "o.png"})
        self.assertIn("error", stdout)

    def test_missing_output(self):
        stdout, rc = _run({"chart_type": "bar", "series": [{"values": [1]}]})
        self.assertIn("error", stdout)

    def test_empty_output(self):
        stdout, rc = _run({"chart_type": "bar", "series": [{"values": [1]}], "output": ""})
        self.assertIn("error", stdout)

    def test_path_traversal(self):
        stdout, rc = _run(
            {"chart_type": "bar", "series": [{"values": [1]}], "output": "../evil.png"}
        )
        self.assertIn("error", stdout)

    def test_absolute_path(self):
        stdout, rc = _run(
            {"chart_type": "bar", "series": [{"values": [1]}], "output": "/tmp/evil.png"}
        )
        self.assertIn("error", stdout)

    def test_invalid_json(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            input="not json",
            capture_output=True,
            text=True,
        )
        self.assertIn("error", proc.stdout)


class TestFileFormat(unittest.TestCase):
    """生成的文件是有效 PNG（magic bytes + 非空）。"""

    def test_png_file_size_reasonable(self):
        with tempfile.TemporaryDirectory() as td:
            stdout, rc, png = _run_chart("bar", td)
            size = os.path.getsize(png)
            self.assertGreater(size, 500, f"PNG too small: {size} bytes")
            self.assertLess(size, 500_000, f"PNG too large: {size} bytes")


if __name__ == "__main__":
    unittest.main()
