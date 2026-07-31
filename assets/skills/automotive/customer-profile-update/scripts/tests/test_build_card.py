"""build_card.py 测试 — text_notice 画像卡（stdlib unittest，独立于 make test）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from build_card import build_profile_card  # noqa: E402


class TestBuildProfileCardTextNotice(unittest.TestCase):
    def setUp(self):
        self.data = {
            "fields": {
                "deal_level": "A", "overall_tag": "务实家用型",
                "personality_summary": "务实注重家庭",
                "intended_model": "追光", "budget_range": "30-40万",
                "current_stage": "需求确认", "breakthrough_point": "金融方案",
                "motivations": "家庭代步+安全", "preferences": "空间/油耗",
                "resistances": "价格/品牌力",
            },
            "phone": "13912345678", "customer_name": "客户5678",
            "update_url": "http://example.com/profile/13912345678",
        }

    def test_card_type_text_notice(self):
        card = build_profile_card(self.data)
        self.assertEqual(card["template_card"]["card_type"], "text_notice")

    def test_no_button_list_no_task_id(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertNotIn("button_list", tc)
        self.assertNotIn("task_id", tc)

    def test_jump_list_view_profile(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertIn("jump_list", tc)
        self.assertEqual(tc["jump_list"][0]["title"], "查看完整画像")
        self.assertEqual(tc["jump_list"][0]["url"], self.data["update_url"])

    def test_card_action_required(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertEqual(tc["card_action"]["type"], 1)
        self.assertEqual(tc["card_action"]["url"], self.data["update_url"])

    def test_sub_title_newline(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertIn("\n", tc["sub_title_text"])
        self.assertIn("动机：", tc["sub_title_text"])
        self.assertIn("偏好：", tc["sub_title_text"])
        self.assertIn("抗性：", tc["sub_title_text"])

    def test_no_update_profile_hcl_row(self):
        tc = build_profile_card(self.data)["template_card"]
        for item in tc["horizontal_content_list"]:
            self.assertNotIn("url", item)
            self.assertNotIn("type", item)

    def test_no_update_url_placeholder_card_action(self):
        data = dict(self.data, update_url="")
        tc = build_profile_card(data)["template_card"]
        self.assertIn("card_action", tc)  # 必填，缺 url 用占位
        self.assertNotIn("jump_list", tc)  # 无 url 不加 jump_list


if __name__ == "__main__":
    unittest.main()
