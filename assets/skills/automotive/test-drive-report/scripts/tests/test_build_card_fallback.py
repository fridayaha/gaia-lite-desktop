"""build_card.py 兜底建卡库测试（stdlib unittest，独立于 make test）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build_card as bc  # noqa: E402

ITEM1 = {
    "test_drive_id": "1",
    "customer_name": "王先生",
    "customer_phone": "176****5538",
    "start_time": "2026-05-27 13:59:57",
    "end_time": "2026-05-27 16:05:52",
    "vehicle": "M817-城市科技版A",
    "vehicle_model": "M817",
    "vehicle_variant": "城市科技版A",
    "report_url": "http://r/1",
}
ITEM2 = {
    "test_drive_id": "2",
    "customer_name": "李女士",
    "customer_phone": "138****8476",
    "start_time": "2026-05-27 09:00:00",
    "end_time": "2026-05-27 10:00:00",
    "vehicle": "M9",
    "vehicle_model": "M9",
    "vehicle_variant": "旗舰版",
    "report_url": "http://r/2",
}


class TestParseHour(unittest.TestCase):
    def test_space_format(self):
        self.assertEqual(bc.parse_hour("2026-05-27 13:59:57"), 13)

    def test_iso_format(self):
        self.assertEqual(bc.parse_hour("2026-05-27T13:59:57"), 13)


class TestMaskPhone(unittest.TestCase):
    def test_long_phone(self):
        self.assertEqual(bc.mask_phone("17612345538"), "176****5538")

    def test_short_phone(self):
        self.assertEqual(bc.mask_phone("1234"), "1234")

    def test_empty(self):
        self.assertEqual(bc.mask_phone(""), "")


class TestBuildSingleCard(unittest.TestCase):
    def test_structure(self):
        card = bc.build_single_card(ITEM1)
        self.assertEqual(card["msgtype"], "template_card")
        tc = card["template_card"]
        self.assertEqual(tc["card_type"], "text_notice")
        self.assertEqual(tc["card_action"]["url"], "http://r/1")
        self.assertEqual(tc["jump_list"][0]["url"], "http://r/1")
        self.assertEqual(tc["jump_list"][0]["title"], "查看完整报告")

    def test_empty_customer_name_uses_tail(self):
        item = dict(ITEM1, customer_name="")
        card = bc.build_single_card(item)
        value = card["template_card"]["horizontal_content_list"][0]["value"]
        self.assertIn("5538", value)


class TestBuildMultiCard(unittest.TestCase):
    def test_rows(self):
        card = bc.build_multi_card([ITEM1, ITEM2])
        tc = card["template_card"]
        self.assertEqual(len(tc["horizontal_content_list"]), 2)
        self.assertEqual(tc["horizontal_content_list"][0]["url"], "http://r/1")
        self.assertEqual(tc["horizontal_content_list"][1]["url"], "http://r/2")

    def test_cross_time_slot_marks_period(self):
        # 13:59 + 09:00 → 跨时段，每行 value 带时段
        card = bc.build_multi_card([ITEM1, ITEM2])
        rows = card["template_card"]["horizontal_content_list"]
        self.assertTrue(any("上午" in r["value"] or "下午" in r["value"] for r in rows))

    def test_same_period_no_mark(self):
        items = [
            dict(ITEM1, start_time="2026-05-27 13:00:00", end_time="2026-05-27 14:00:00"),
            dict(ITEM2, start_time="2026-05-27 15:00:00", end_time="2026-05-27 16:00:00"),
        ]
        card = bc.build_multi_card(items)
        rows = card["template_card"]["horizontal_content_list"]
        self.assertFalse(any("上午" in r["value"] or "下午" in r["value"] for r in rows))


class TestBuildFallbackCard(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(bc.build_fallback_card([]))

    def test_single(self):
        card = bc.build_fallback_card([ITEM1])
        self.assertEqual(card["template_card"]["main_title"]["title"], "🚗 试驾报告")

    def test_multi(self):
        card = bc.build_fallback_card([ITEM1, ITEM2])
        self.assertIn("2 条", card["template_card"]["main_title"]["title"])

    def test_many_truncates_and_adds_desc(self):
        items = [
            dict(ITEM1, test_drive_id=str(i), report_url=f"http://r/{i}", customer_phone=f"176****{i:04d}")
            for i in range(8)
        ]
        card = bc.build_fallback_card(items)
        tc = card["template_card"]
        self.assertEqual(len(tc["horizontal_content_list"]), 6)
        self.assertIn("desc", tc["main_title"])


if __name__ == "__main__":
    unittest.main()
