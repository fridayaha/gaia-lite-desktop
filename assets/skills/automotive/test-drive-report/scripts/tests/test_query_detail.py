"""query_detail.py 深挖切片测试（stdlib unittest，独立于 make test）。"""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import query_detail  # noqa: E402

# 复用 test_detail 的 fixture 结构（这里独立定义一份精简版，含三模块）
FIXTURE = {
    "test_drive_id": "TID1",
    "customer_phone": "17621765538",
    "sales_check": {
        "communication": {
            "result": {
                "statistics": {"score_rate": "81.0%"},
                "overall_evaluation": "沟通良好。",
                "improvement_suggestions": ["s1", "s2"],
                "groups": [{"group_name": "语言逻辑", "analysis_items": [{"item": "x"}]}],
                "highlights": [{"title": "h", "desc": "d"}],
            },
            "updated_at": "2026-05-27T16:11:29",
        },
        "knowledge": {
            "result": {
                "statistics": {"score_rate": "97.0%"},
                "overall_evaluation": "优秀。",
                "improvement_suggestions": ["k1"],
            },
            "updated_at": "2026-05-27T16:15:32",
        },
    },
    "deal_intent": {
        "result": {
            "vehicle": {"model": "M817"},
            "closerDashboard": {
                "closeLevel": "A",
                "closeProbability": 65,
                "painPoints": ["p1", "p2"],
            },
            "focusAnalysis": {
                "items": [
                    {
                        "weight": 92,
                        "details": "d",
                        "evidence": ["e"],
                        "dimension": "优惠",
                        "rationaleChain": ["r"],
                    }
                ],
                "summary": "关注成本。",
            },
            "emotionHeatmap": [{"time": "14:03", "label": "试驾启动", "interest": 50}],
            "signalsAndRisks": {
                "signalStrength": 65,
                "risks": [{"level": "high", "description": "观望"}],
            },
            "resistanceAnalysis": {
                "overallSummary": "抗拒在产品维度。",
                "dimensions": [{"dimension": "价格", "summary": "无抗拒"}],
            },
        },
        "updated_at": "2026-05-27T16:12:02",
    },
    "next_action": {
        "result": {
            "timeline": [{"time": "00:03", "type": "start", "description": "试驾启动"}],
            "salesAmmo": {"materials": [{"name": "m"}], "recommendedKit": "外放电套装"},
            "competitorCard": {"detected": False, "competitor": "未检测到竞品", "dimensions": []},
            "nextBestAction": {
                "actions": [
                    {
                        "text": "出具报价单。",
                        "priority": "urgent",
                        "motivation": "商务锁定",
                        "expectedImpact": "锁定。",
                    }
                ],
                "followUpScript": "陈总您好。",
                "aiReminder": ["改进话术"],
            },
            "customerProfile": {
                "tags": ["华为老用户"],
                "riskAlert": "持币观望",
                "winLossDrivers": {"drivers": [], "blockers": []},
            },
        },
        "updated_at": "2026-05-27T16:12:51",
    },
}


class TestLoadCache(unittest.TestCase):
    def test_missing_returns_none(self):
        with patch.object(query_detail, "CACHE_DIR", tempfile.mkdtemp()):
            obj, path = query_detail.load_cache("NONEXISTENT")
            self.assertIsNone(obj)
            self.assertIsNone(path)

    def test_corrupt_returns_none(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "tdr_detail_BAD.json"), "w") as f:
            f.write("{not valid json")
        with patch.object(query_detail, "CACHE_DIR", d):
            obj, _ = query_detail.load_cache("BAD")
            self.assertIsNone(obj)

    def test_valid_returns_obj(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "tdr_detail_TID1.json"), "w") as f:
            json.dump(FIXTURE, f)
        with patch.object(query_detail, "CACHE_DIR", d):
            obj, path = query_detail.load_cache("TID1")
            self.assertEqual(obj["test_drive_id"], "TID1")
            self.assertTrue(path.endswith("tdr_detail_TID1.json"))


class TestTopics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "tdr_detail_TID1.json"), "w") as f:
            json.dump(FIXTURE, f)
        self._patch = patch.object(query_detail, "CACHE_DIR", self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _run(self, topic, tid="TID1"):
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            sys.argv = ["query_detail.py", "--test-drive-id", tid, "--topic", topic]
            query_detail.main()
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old

    def test_focus_analysis_keeps_evidence(self):
        # 深挖切片保留 evidence/rationaleChain（概要里舍去的）
        r = self._run("focus_analysis")
        self.assertTrue(r["ok"])
        self.assertEqual(r["topic"], "focus_analysis")
        item = r["data"]["items"][0]
        self.assertIn("evidence", item)
        self.assertIn("rationaleChain", item)

    def test_timeline(self):
        r = self._run("timeline")
        self.assertEqual(len(r["data"]), 1)
        self.assertEqual(r["data"][0]["type"], "start")

    def test_sales_check_detail_has_groups(self):
        r = self._run("sales_check_detail")
        # 完整 sales_check 含 groups/analysis_items（深挖内容）
        self.assertIn("groups", r["data"]["communication"]["result"])

    def test_improvement_suggestions_aggregated(self):
        r = self._run("improvement_suggestions")
        self.assertEqual(r["data"]["communication"], ["s1", "s2"])
        self.assertEqual(r["data"]["knowledge"], ["k1"])

    def test_pain_points(self):
        r = self._run("pain_points")
        self.assertEqual(r["data"], ["p1", "p2"])

    def test_actions(self):
        r = self._run("actions")
        self.assertEqual(len(r["data"]), 1)
        self.assertEqual(r["data"][0]["priority"], "urgent")

    def test_competitor(self):
        r = self._run("competitor")
        self.assertEqual(r["data"]["competitor"], "未检测到竞品")

    def test_emotion_heatmap(self):
        r = self._run("emotion_heatmap")
        self.assertEqual(r["data"][0]["interest"], 50)

    def test_unknown_topic(self):
        r = self._run("not_a_topic")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "unknown_topic")
        self.assertIn("focus_analysis", r["available"])

    def test_help(self):
        r = self._run("help")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["available"]), 14)

    def test_cache_missing(self):
        r = self._run("focus_analysis", tid="MISSING_TID")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "cache_missing")
        self.assertIn("detail.py", r["message"])


class TestNullModule(unittest.TestCase):
    """依赖模块为 null 时返回 data=None + 未生成提示。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        null_fixture = dict(FIXTURE)
        null_fixture["deal_intent"] = None
        null_fixture["next_action"] = None
        with open(os.path.join(self.tmp, "tdr_detail_NULL.json"), "w") as f:
            json.dump(null_fixture, f)
        self._patch = patch.object(query_detail, "CACHE_DIR", self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _run(self, topic):
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            sys.argv = ["query_detail.py", "--test-drive-id", "NULL", "--topic", topic]
            query_detail.main()
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old

    def test_deal_intent_null_topic(self):
        r = self._run("focus_analysis")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["data"])
        self.assertIn("成交意愿", r["message"])

    def test_next_action_null_topic(self):
        r = self._run("actions")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["data"])
        self.assertIn("下一步建议", r["message"])

    def test_sales_check_still_works(self):
        # sales_check 仍存在 → improvement_suggestions 正常返回
        r = self._run("improvement_suggestions")
        self.assertIsNotNone(r["data"])
        self.assertIn("communication", r["data"])


if __name__ == "__main__":
    unittest.main()
