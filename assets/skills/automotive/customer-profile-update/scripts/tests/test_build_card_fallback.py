"""build_card.py 兜底建卡库测试（stdlib unittest，独立于 make test）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build_card as bc  # noqa: E402


def _items(n):
    return [{"id": i, "phone": f"138000{i:04d}", "name": f"客户{i}"} for i in range(1, n + 1)]


class TestMaskPhone(unittest.TestCase):
    def test_long(self):
        self.assertEqual(bc.mask_phone("13912345678"), "139****5678")

    def test_short(self):
        self.assertEqual(bc.mask_phone("1234"), "1234")

    def test_empty(self):
        self.assertEqual(bc.mask_phone(""), "")


class TestPageLayout(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(bc.page_layout(0, 1), (0, 0, False, False, 1))

    def test_le5_single_page(self):
        self.assertEqual(bc.page_layout(3, 1), (0, 3, False, False, 1))

    def test_8_two_pages(self):
        self.assertEqual(bc.page_layout(8, 1), (0, 5, False, True, 2))
        self.assertEqual(bc.page_layout(8, 2), (5, 3, True, False, 2))

    def test_13_three_pages(self):
        self.assertEqual(bc.page_layout(13, 1), (0, 5, False, True, 3))
        self.assertEqual(bc.page_layout(13, 2), (5, 4, True, True, 3))
        self.assertEqual(bc.page_layout(13, 3), (9, 4, True, False, 3))

    def test_page_clamped(self):
        # page 超出范围被夹紧
        start, count, has_prev, has_next, total = bc.page_layout(8, 99)
        self.assertEqual(total, 2)


class TestBuildSelectionCard(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(bc.build_selection_card([], 1))

    def test_le5_no_nav(self):
        card = bc.build_selection_card(_items(3), 1)
        bl = card["template_card"]["button_list"]
        self.assertEqual(len(bl), 3)
        self.assertTrue(all(b["key"].startswith("select_") for b in bl))

    def test_page1_has_next(self):
        card = bc.build_selection_card(_items(8), 1)
        bl = card["template_card"]["button_list"]
        self.assertEqual(len(bl), 6)  # 5 select + next
        self.assertEqual(bl[-1]["key"], "page_next")
        self.assertIn("第 1/2 页", card["template_card"]["main_title"]["title"])

    def test_middle_page_prev_next(self):
        card = bc.build_selection_card(_items(13), 2)
        bl = card["template_card"]["button_list"]
        self.assertEqual(len(bl), 6)  # 4 select + prev + next
        keys = [b["key"] for b in bl]
        self.assertIn("page_prev", keys)
        self.assertIn("page_next", keys)

    def test_last_page_prev_only(self):
        card = bc.build_selection_card(_items(8), 2)
        bl = card["template_card"]["button_list"]
        keys = [b["key"] for b in bl]
        self.assertIn("page_prev", keys)
        self.assertNotIn("page_next", keys)

    def test_button_text_uses_name_and_masked_phone(self):
        card = bc.build_selection_card([{"id": 1, "phone": "13912345678", "name": "张先生"}], 1)
        text = card["template_card"]["button_list"][0]["text"]
        self.assertIn("张先生", text)
        self.assertIn("139****5678", text)

    def test_empty_name_uses_phone_tail(self):
        card = bc.build_selection_card([{"id": 1, "phone": "13912345678", "name": ""}], 1)
        text = card["template_card"]["button_list"][0]["text"]
        self.assertIn("5678", text)


class TestBuildProfileCard(unittest.TestCase):
    DATA = {
        "phone": "13912345678", "customer_name": "客户5678",
        "update_url": "http://x/customer/13912345678/profile",
        "fields": {
            "deal_level": "A", "overall_tag": "务实家用", "personality_summary": "务实家用型决策者偏长",
            "intended_model": "追光", "budget_range": "30-40万", "current_stage": "需求确认",
            "breakthrough_point": "金融方案", "motivations": "家庭代步", "preferences": "空间", "resistances": "价格",
        },
    }

    def test_structure(self):
        card = bc.build_profile_card(self.DATA)
        tc = card["template_card"]
        self.assertEqual(tc["card_type"], "text_notice")
        self.assertNotIn("task_id", tc)
        self.assertNotIn("button_list", tc)
        self.assertIn("139****5678", tc["main_title"]["title"])
        self.assertIn("A", tc["main_title"]["desc"])
        # 5 字段，无「更新画像」hcl 行
        self.assertEqual(len(tc["horizontal_content_list"]), 5)
        for item in tc["horizontal_content_list"]:
            self.assertNotIn("url", item)
            self.assertNotIn("type", item)
        # jump_list「查看完整画像」
        self.assertEqual(tc["jump_list"][0]["title"], "查看完整画像")
        self.assertEqual(tc["jump_list"][0]["url"], self.DATA["update_url"])
        # card_action 跳转
        self.assertEqual(tc["card_action"]["type"], 1)
        self.assertEqual(tc["card_action"]["url"], self.DATA["update_url"])
        # sub_title_text 用 \n 换行
        self.assertIn("\n", tc["sub_title_text"])

    def test_empty_fields_skipped(self):
        data = {"phone": "13912345678", "customer_name": "x", "update_url": "http://x",
                "fields": {"overall_tag": "", "intended_model": "追光", "budget_range": "",
                           "current_stage": "", "breakthrough_point": ""}}
        card = bc.build_profile_card(data)
        hcl = card["template_card"]["horizontal_content_list"]
        # 只有 intended_model 非空 = 1（无「更新画像」hcl 行）
        self.assertEqual(len(hcl), 1)
        self.assertEqual(hcl[0]["keyname"], "意向车型")


class TestBuildErrorCard(unittest.TestCase):
    def test_not_found(self):
        card = bc.build_error_card("not_found")
        tc = card["template_card"]
        self.assertIn("未找到", tc["main_title"]["title"])
        keys = [b["key"] for b in tc["button_list"]]
        self.assertIn("restart", keys)
        self.assertIn("cancel", keys)


if __name__ == "__main__":
    unittest.main()
