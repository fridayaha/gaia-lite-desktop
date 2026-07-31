"""validate_card.py 校验器测试（stdlib unittest，独立于 make test）。"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import validate_card as vc  # noqa: E402

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VC_SCRIPT = os.path.join(SCRIPTS_DIR, "validate_card.py")

ITEMS = [
    {
        "test_drive_id": "1",
        "customer_name": "王先生",
        "customer_phone": "176****5538",
        "start_time": "2026-05-27 13:59:57",
        "end_time": "2026-05-27 16:05:52",
        "vehicle": "M817-城市科技版A",
        "vehicle_model": "M817",
        "vehicle_variant": "城市科技版A",
        "report_url": "http://r/1",
    },
    {
        "test_drive_id": "2",
        "customer_name": "李女士",
        "customer_phone": "138****8476",
        "start_time": "2026-05-27 09:00:00",
        "end_time": "2026-05-27 10:00:00",
        "vehicle": "M9",
        "vehicle_model": "M9",
        "vehicle_variant": "旗舰版",
        "report_url": "http://r/2",
    },
]


def _draft(template):
    return {"msgtype": "template_card", "template_card": template}


class TestSanitize(unittest.TestCase):
    def test_valid_draft_passes(self):
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "horizontal_content_list": [
                {"keyname": "a", "value": "b", "type": 1, "url": "http://r/1"}
            ],
            "card_action": {"type": 1, "url": "http://r/1"},
        })
        self.assertTrue(vc._sanitize(card, ITEMS))
        self.assertEqual(card["template_card"]["card_type"], "text_notice")

    def test_hallucinated_url_returns_false(self):
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "horizontal_content_list": [
                {"keyname": "a", "value": "b", "type": 1, "url": "http://r/FAKE"}
            ],
            "card_action": {"type": 1, "url": "http://r/1"},
        })
        self.assertFalse(vc._sanitize(card, ITEMS))

    def test_hallucinated_card_action_url_returns_false(self):
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "card_action": {"type": 1, "url": "http://r/FAKE"},
        })
        self.assertFalse(vc._sanitize(card, ITEMS))

    def test_wrong_card_type_returns_false(self):
        card = _draft({"card_type": "news_notice", "main_title": {"title": "x"},
                        "card_action": {"type": 1, "url": "http://r/1"}})
        self.assertFalse(vc._sanitize(card, ITEMS))

    def test_over_limit_sanitized(self):
        hcl = [
            {"keyname": "12345678", "value": "v" * 30, "type": 1, "url": "http://r/1"}
            for _ in range(8)
        ]
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "horizontal_content_list": hcl,
            "card_action": {"type": 1, "url": "http://r/1"},
        })
        self.assertTrue(vc._sanitize(card, ITEMS))
        tc = card["template_card"]
        self.assertEqual(len(tc["horizontal_content_list"]), vc.MAX_HCL)
        self.assertEqual(len(tc["horizontal_content_list"][0]["keyname"]), vc.MAX_KEYNAME)
        self.assertEqual(len(tc["horizontal_content_list"][0]["value"]), vc.MAX_VALUE)

    def test_missing_card_action_injected(self):
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "horizontal_content_list": [],
        })
        self.assertTrue(vc._sanitize(card, ITEMS))
        self.assertEqual(card["template_card"]["card_action"]["url"], "http://r/1")

    def test_missing_main_title_injected(self):
        card = _draft({
            "card_type": "text_notice",
            "card_action": {"type": 1, "url": "http://r/1"},
        })
        self.assertTrue(vc._sanitize(card, ITEMS))
        self.assertIn("main_title", card["template_card"])

    def test_jump_list_truncated(self):
        jl = [{"type": 1, "url": "http://r/1", "title": f"t{i}"} for i in range(5)]
        card = _draft({
            "card_type": "text_notice",
            "main_title": {"title": "x"},
            "jump_list": jl,
            "card_action": {"type": 1, "url": "http://r/1"},
        })
        self.assertTrue(vc._sanitize(card, ITEMS))
        self.assertEqual(len(card["template_card"]["jump_list"]), vc.MAX_JUMP)


class TestMainSubprocess(unittest.TestCase):
    def _run(self, data, card_json):
        proc = subprocess.run(
            [sys.executable, VC_SCRIPT, "--card-json", card_json],
            input=json.dumps(data, ensure_ascii=False),
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def test_zero_items_outputs_text(self):
        data = {"ok": True, "code": 0, "total": 0, "items": [], "hints": {}, "query": {}}
        out = self._run(data, '{"msgtype":"template_card","template_card":{"card_type":"text_notice"}}')
        self.assertIn("没找到", out)

    def test_api_fail_outputs_fail_text(self):
        data = {"ok": False, "error": "api_fail"}
        out = self._run(data, '{"msgtype":"template_card","template_card":{"card_type":"text_notice"}}')
        self.assertIn("查询失败", out)

    def test_parse_error_falls_back(self):
        data = {"ok": True, "code": 0, "total": 2, "items": ITEMS, "hints": {}, "query": {}}
        out = self._run(data, "NOT-JSON")
        self.assertIn("template_card", out)
        self.assertIn("text_notice", out)

    def test_non_template_card_falls_back(self):
        data = {"ok": True, "code": 0, "total": 2, "items": ITEMS, "hints": {}, "query": {}}
        out = self._run(data, '{"msgtype":"text"}')
        self.assertIn("text_notice", out)

    def test_valid_draft_output(self):
        data = {"ok": True, "code": 0, "total": 1, "items": ITEMS[:1], "hints": {}, "query": {}}
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "🚗 王先生的试驾"},
                "horizontal_content_list": [
                    {"keyname": "车型", "value": "M817", "type": 1, "url": "http://r/1"}
                ],
                "card_action": {"type": 1, "url": "http://r/1"},
            },
        }, ensure_ascii=False)
        out = self._run(data, draft)
        parsed = json.loads(out)
        self.assertEqual(parsed["template_card"]["main_title"]["title"], "🚗 王先生的试驾")

    def test_hallucinated_draft_falls_back_to_real_data(self):
        data = {"ok": True, "code": 0, "total": 1, "items": ITEMS[:1], "hints": {}, "query": {}}
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "编造的"},
                "horizontal_content_list": [
                    {"keyname": "x", "value": "y", "type": 1, "url": "http://r/FAKE"}
                ],
                "card_action": {"type": 1, "url": "http://r/FAKE"},
            },
        }, ensure_ascii=False)
        out = self._run(data, draft)
        parsed = json.loads(out)
        # 回退到兜底卡：url 必须是真实 http://r/1
        self.assertEqual(parsed["template_card"]["card_action"]["url"], "http://r/1")
        self.assertNotIn("编造的", out)


if __name__ == "__main__":
    unittest.main()
