"""query_detail.py 深挖切片测试（stdlib unittest，独立于 make test）。"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import query_detail as Q  # noqa: E402

ENUM_MAP = {
    "purchase_type": {"replacement": "换购"},
    "intended_model": {"zhuiguang": "追光"},
}

PROFILE = {
    "main_summary": {"deal_level": "A", "overall_tag": "务实", "profile_summary": "总结"},
    "basic_notes": {
        "intended_model": {"value": "zhuiguang", "reasoning_summary": "多次询问"},
        "budget_range": {"value": "30-40万", "reasoning_summary": "预算明确"},
        "purchase_type": {"value": "replacement", "reasoning_summary": "置换"},
    },
    "customer_overview": {"closing_probability": "85%", "customer_type": "换购"},
    "emotion_state": {
        "current_state": "理性",
        "radar_data": {"items": [{"score": 80, "dimension": "信任"}], "max_score": 100},
    },
    "purchase_motivations": [{"motivation_name": "家庭代步", "reasoning_detail": {"summary": "s"}}],
    "product_preferences": [{"preference_name": "空间"}],
    "resistances": [{"resistance_name": "价格", "severity": "中"}],
    "inferred_tags": [{"title": "家庭导向", "desc": "x"}],
    "usage_scenarios": [{"title": "通勤", "desc": "x"}],
}

CACHE = {"profile": PROFILE, "enum_map": ENUM_MAP, "fetched_at": "2026-07-06T10:00:00"}


def _run(topic, cache=CACHE, phone="13912345678"):
    with tempfile.TemporaryDirectory() as td:
        Q.CACHE_DIR = td
        path = os.path.join(td, f"cp_detail_{phone}.json")
        with open(path, "w") as f:
            json.dump(cache, f)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", phone, "--topic", topic]
            try:
                Q.main()
            finally:
                sys.argv = old
    return json.loads(buf.getvalue())


class TestLoadCache(unittest.TestCase):
    def test_missing(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)

    def test_valid(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                json.dump(CACHE, f)
            obj, _ = Q.load_cache("13912345678")
        self.assertEqual(obj["enum_map"], ENUM_MAP)

    def test_expired_mtime(self):
        """mtime 超 TTL 视为过期 → None。"""
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                json.dump(CACHE, f)
            old = time.time() - 660  # 11min 前
            os.utime(path, (old, old))
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)

    def test_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                f.write("{not json")
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)


class TestTopics(unittest.TestCase):
    def test_basic_notes_detail_mapped(self):
        """basic_notes 主题：parse_note 现映射 value + reasoning_summary。"""
        data = _run("basic_notes_detail")
        self.assertTrue(data["ok"])
        bn = data["data"]
        self.assertEqual(bn["intended_model"]["value"], "追光")  # 枚举映射
        self.assertEqual(bn["purchase_type"]["value"], "换购")  # 枚举映射
        self.assertEqual(bn["budget_range"]["value"], "30-40万")  # 直接值
        self.assertEqual(bn["intended_model"]["reasoning_summary"], "多次询问")

    def test_customer_overview_detail(self):
        data = _run("customer_overview_detail")
        self.assertEqual(data["data"]["closing_probability"], "85%")

    def test_emotion_detail(self):
        data = _run("emotion_detail")
        self.assertEqual(data["data"]["current_state"], "理性")
        self.assertEqual(data["data"]["radar_data"]["items"][0]["dimension"], "信任")

    def test_motivations_detail(self):
        data = _run("motivations_detail")
        self.assertEqual(data["data"][0]["motivation_name"], "家庭代步")

    def test_preferences_detail(self):
        data = _run("preferences_detail")
        self.assertEqual(data["data"][0]["preference_name"], "空间")

    def test_resistances_detail(self):
        data = _run("resistances_detail")
        self.assertEqual(data["data"][0]["resistance_name"], "价格")
        self.assertEqual(data["data"][0]["severity"], "中")

    def test_inferred_tags(self):
        data = _run("inferred_tags")
        self.assertEqual(data["data"][0]["title"], "家庭导向")

    def test_usage_scenarios(self):
        data = _run("usage_scenarios")
        self.assertEqual(data["data"][0]["title"], "通勤")

    def test_personality(self):
        data = _run("personality")
        self.assertEqual(data["data"]["deal_level"], "A")
        self.assertEqual(data["data"]["profile_summary"], "总结")


class TestNullModule(unittest.TestCase):
    def test_null_module_returns_data_none_with_message(self):
        """依赖模块缺失 → data=null + 未生成提示。"""
        cache = {"profile": {"main_summary": {}}, "enum_map": {}, "fetched_at": "..."}  # 无 emotion_state
        data = _run("emotion_detail", cache=cache)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["data"])
        self.assertIn("情绪状态", data["message"])


class TestErrors(unittest.TestCase):
    def test_cache_missing(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                old = sys.argv
                sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "emotion_detail"]
                try:
                    Q.main()
                finally:
                    sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "cache_missing")

    def test_unknown_topic(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "nope"]
            try:
                Q.main()
            finally:
                sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "unknown_topic")
        self.assertIn("available", data)

    def test_help_lists_all_topics(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "help"]
            try:
                Q.main()
            finally:
                sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["available"]), 9)


if __name__ == "__main__":
    unittest.main()
