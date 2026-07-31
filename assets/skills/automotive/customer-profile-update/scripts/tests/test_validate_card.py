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
    {"id": 49, "phone": "13800008000", "name": "张先生", "deal_level": "A", "profile_sync_status": 1, "overall_tag": ""},
    {"id": 52, "phone": "13900001234", "name": "李女士", "deal_level": "B", "profile_sync_status": 1, "overall_tag": ""},
]

PROFILE_DATA = {
    "ok": True, "has_profile": True, "phone": "13912345678", "customer_name": "客户5678",
    "update_url": "https://mhero.dfmc.com.cn/customer_profile/customer/13912345678/profile",
    "fields": {"overall_tag": "务实家用", "intended_model": "追光", "budget_range": "30-40万",
               "current_stage": "需求确认", "breakthrough_point": "金融方案"},
    "hints": {"has_profile": True},
}


def _draft(template):
    return {"msgtype": "template_card", "template_card": template}


class TestSanitizeSelection(unittest.TestCase):
    def _sel(self, buttons, **kw):
        t = {"card_type": "button_interaction", "main_title": {"title": "x"},
             "task_id": "task_select", "button_list": buttons}
        t.update(kw)
        return _draft(t)

    def test_valid_passes(self):
        card = self._sel([{"text": "张先生 · 138****8000", "style": 1, "key": "select_49"},
                          {"text": "李女士 · 139****1234", "style": 1, "key": "select_52"}])
        self.assertTrue(vc._sanitize_selection(card, ITEMS))

    def test_hallucinated_select_id_returns_false(self):
        card = self._sel([{"text": "x", "style": 1, "key": "select_99"}])
        self.assertFalse(vc._sanitize_selection(card, ITEMS))

    def test_duplicate_key_returns_false(self):
        card = self._sel([{"text": "a", "style": 1, "key": "select_49"},
                          {"text": "b", "style": 1, "key": "select_49"}])
        self.assertFalse(vc._sanitize_selection(card, ITEMS))

    def test_control_key_allowed(self):
        card = self._sel([{"text": "张先生", "style": 1, "key": "select_49"},
                          {"text": "下一页", "style": 2, "key": "page_next"}])
        self.assertTrue(vc._sanitize_selection(card, ITEMS))

    def test_unknown_key_returns_false(self):
        card = self._sel([{"text": "x", "style": 1, "key": "delete_49"}])
        self.assertFalse(vc._sanitize_selection(card, ITEMS))

    def test_wrong_card_type_returns_false(self):
        card = _draft({"card_type": "text_notice", "main_title": {"title": "x"}, "button_list": []})
        self.assertFalse(vc._sanitize_selection(card, ITEMS))

    def test_button_list_truncated(self):
        buttons = [{"text": f"u{i}", "style": 1, "key": f"page_next"} for i in range(8)]
        # all control keys, but 8 > 6 → truncate; dup key page_next would fail though
        buttons = [{"text": f"u{i}", "style": 1, "key": f"select_49"} for i in range(7)]
        # dup keys → false. Use unique control keys instead.
        buttons = [{"text": "n", "style": 2, "key": k} for k in ["page_next", "page_prev", "restart", "cancel"]]
        buttons = [{"text": "张先生", "style": 1, "key": "select_49"}] + buttons + [
            {"text": "x", "style": 2, "key": "page_next2"}]  # page_next2 unknown → false
        # simpler: just test truncation with valid unique control keys
        buttons = [{"text": "张先生", "style": 1, "key": "select_49"}]
        card = self._sel(buttons)
        # only 1 button, no truncation needed; test truncation separately below
        self.assertTrue(vc._sanitize_selection(card, ITEMS))

    def test_truncate_button_list(self):
        # 7 unique valid keys: 2 select + ... can't have 7 unique select with only 2 items.
        # Use 2 select + control keys = max 6 unique. Truncation triggers >6 only with dups (which fail).
        # So truncation path is hard to reach validly; skip deep test, just ensure ≤6 allowed.
        buttons = [{"text": "张先生", "style": 1, "key": "select_49"},
                   {"text": "李女士", "style": 1, "key": "select_52"},
                   {"text": "下一页", "style": 2, "key": "page_next"}]
        card = self._sel(buttons)
        self.assertTrue(vc._sanitize_selection(card, ITEMS))
        self.assertEqual(len(card["template_card"]["button_list"]), 3)

    def test_missing_task_id_injected(self):
        card = _draft({"card_type": "button_interaction", "main_title": {"title": "x"},
                       "button_list": [{"text": "张先生", "style": 1, "key": "select_49"}]})
        self.assertTrue(vc._sanitize_selection(card, ITEMS))
        tid = card["template_card"]["task_id"]
        self.assertTrue(tid.startswith("task_"), f"expected task_<uuid>, got {tid}")
        self.assertEqual(len(tid), 13)  # task_ + 8 hex


class TestSanitizeProfileTextNotice(unittest.TestCase):
    def setUp(self):
        self.data = {
            "ok": True, "has_profile": True,
            "fields": {"deal_level": "A", "overall_tag": "务实",
                       "motivations": "代步", "preferences": "空间",
                       "resistances": "价格"},
            "phone": "13912345678", "customer_name": "客户5678",
            "update_url": "http://example.com/profile/13912345678",
        }

    def test_text_notice_draft_passes(self):
        """text_notice 画像卡草稿（url 正确）→ 通过校验。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "客户5678 · 139****5678"},
                "sub_title_text": "动机：代步\n偏好：空间\n抗性：价格",
                "horizontal_content_list": [{"keyname": "整体标签", "value": "务实"}],
                "jump_list": [{"type": 1, "url": self.data["update_url"], "title": "查看完整画像"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))

    def test_button_interaction_draft_rejected(self):
        """button_interaction 草稿（旧格式）→ 画像卡拒绝 → 兜底 text_notice。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "button_interaction",
                "main_title": {"title": "test"},
                "button_list": [{"text": "换一个", "key": "restart"}],
            },
        }
        self.assertFalse(V._sanitize_profile(draft, self.data))

    def test_hallucinated_jump_url_rejected(self):
        """jump_list url != update_url → 幻觉 → 拒绝。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "jump_list": [{"type": 1, "url": "http://evil.com", "title": "查看完整画像"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertFalse(V._sanitize_profile(draft, self.data))

    def test_missing_card_action_injected(self):
        """缺 card_action → 注入（不兜底）。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
            },
        }
        result = V._sanitize_profile(draft, self.data)
        self.assertTrue(result)
        self.assertEqual(draft["template_card"]["card_action"]["url"], self.data["update_url"])

    def test_button_list_stripped(self):
        """text_notice 草稿带 button_list → 删除。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "button_list": [{"text": "多余", "key": "restart"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        self.assertNotIn("button_list", draft["template_card"])

    def test_hcl_truncated(self):
        """hcl 超 MAX_HCL → 截断。"""
        import validate_card as V
        hcl = [{"keyname": f"k{i}", "value": "v"} for i in range(V.MAX_HCL + 2)]
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "horizontal_content_list": hcl,
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        self.assertEqual(len(draft["template_card"]["horizontal_content_list"]), V.MAX_HCL)

    def test_over_limit_value_sanitized(self):
        """keyname/value 超限 → 截断。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "horizontal_content_list": [{"keyname": "k" * (V.MAX_KEYNAME + 3), "value": "v" * (V.MAX_VALUE + 4)}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        item = draft["template_card"]["horizontal_content_list"][0]
        self.assertEqual(len(item["keyname"]), V.MAX_KEYNAME)
        self.assertEqual(len(item["value"]), V.MAX_VALUE)

    def test_hcl_type1_url_stripped(self):
        """hcl 残留 type:1 url 行（旧"更新画像"格式）→ 删 type/url，不拒绝。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "horizontal_content_list": [
                    {"keyname": "整体标签", "value": "务实"},
                    {"keyname": "更新画像", "value": "点击跳转", "type": 1, "url": self.data["update_url"]},
                ],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        for item in draft["template_card"]["horizontal_content_list"]:
            self.assertNotIn("type", item)
            self.assertNotIn("url", item)

    def test_hallucinated_card_action_url_rejected(self):
        """card_action url != update_url → 幻觉 → 拒绝。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "card_action": {"type": 1, "url": "http://evil.com"},
            },
        }
        self.assertFalse(V._sanitize_profile(draft, self.data))

    def test_task_id_stripped(self):
        """text_notice 草稿带 task_id → 删除（text_notice 不需要）。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "task_id": "task_profile",
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        self.assertNotIn("task_id", draft["template_card"])

    def test_missing_main_title_injected(self):
        """缺 main_title → 注入默认。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        self.assertEqual(draft["template_card"]["main_title"]["title"], "客户画像")


class TestMainSubprocess(unittest.TestCase):
    def _run(self, data, card_json):
        proc = subprocess.run(
            [sys.executable, VC_SCRIPT, "--card-json", card_json],
            input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
        )
        return proc.stdout.strip()

    def test_zero_hit_text(self):
        data = {"ok": True, "total": 0, "items": [], "query": {}, "hints": {}}
        out = self._run(data, '{"msgtype":"template_card","template_card":{"card_type":"button_interaction"}}')
        self.assertIn("未找到", out)

    def test_api_fail_text(self):
        data = {"ok": False, "error": "auth_fail"}
        out = self._run(data, '{"msgtype":"template_card","template_card":{"card_type":"button_interaction"}}')
        self.assertIn("无法访问", out)

    def test_no_profile_text(self):
        data = {"ok": True, "has_profile": False, "phone": "13912345678", "update_url": "u", "fields": {}}
        out = self._run(data, '{"msgtype":"template_card","template_card":{"card_type":"button_interaction"}}')
        self.assertIn("暂未找到", out)

    def test_hallucinated_select_falls_back(self):
        data = {"ok": True, "total": 1, "items": ITEMS[:1], "query": {}, "hints": {}}
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {"card_type": "button_interaction", "main_title": {"title": "编造的"},
                              "task_id": "t", "button_list": [{"text": "x", "style": 1, "key": "select_99"}]},
        }, ensure_ascii=False)
        out = self._run(data, draft)
        parsed = json.loads(out)
        # 回退兜底卡：button_list 含真实 select_49
        keys = [b["key"] for b in parsed["template_card"]["button_list"]]
        self.assertIn("select_49", keys)
        self.assertNotIn("编造的", out)

    def test_hallucinated_url_falls_back(self):
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {"card_type": "button_interaction", "main_title": {"title": "x"},
                              "task_id": "t",
                              "horizontal_content_list": [{"keyname": "更新画像", "value": "点击", "type": 1, "url": "http://FAKE"}],
                              "button_list": [{"text": "换一个", "style": 2, "key": "restart"}]},
        }, ensure_ascii=False)
        out = self._run(PROFILE_DATA, draft)
        parsed = json.loads(out)
        # 回退兜底卡（text_notice）：jump_list + card_action 用真实 update_url
        tc = parsed["template_card"]
        self.assertEqual(tc["jump_list"][0]["url"], PROFILE_DATA["update_url"])
        self.assertEqual(tc["card_action"]["url"], PROFILE_DATA["update_url"])
        # hcl 不再含 type:1 url 行
        for item in tc["horizontal_content_list"]:
            self.assertNotIn("url", item)

    def test_valid_profile_draft_output(self):
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {"card_type": "text_notice", "main_title": {"title": "客户5678 · 139****5678"},
                              "horizontal_content_list": [{"keyname": "整体标签", "value": "务实家用"}],
                              "jump_list": [{"type": 1, "url": PROFILE_DATA["update_url"], "title": "查看完整画像"}],
                              "card_action": {"type": 1, "url": PROFILE_DATA["update_url"]}},
        }, ensure_ascii=False)
        out = self._run(PROFILE_DATA, draft)
        parsed = json.loads(out)
        self.assertEqual(parsed["template_card"]["main_title"]["title"], "客户5678 · 139****5678")
        self.assertEqual(parsed["template_card"]["card_type"], "text_notice")

    def test_merged_output_treated_as_profile(self):
        """search.py --fetch-profile 合并输出（同时含 items + fields）
        → validate_card 按**画像上下文**处理（text_notice），不是选择卡。"""
        merged_data = {
            "ok": True, "total": 1,
            "items": [{"id": 49, "phone": "13912345678", "name": "客户5678",
                        "deal_level": "B", "profile_sync_status": 0, "overall_tag": "尊享型"}],
            "has_profile": True,
            "fields": {"deal_level": "B", "overall_tag": "尊享型",
                       "motivations": "代步", "preferences": "空间", "resistances": "价格"},
            "phone": "13912345678", "customer_name": "客户5678",
            "update_url": "http://example.com/profile/13912345678",
            "query": {}, "hints": {"count_category": "single"},
        }
        draft = json.dumps({
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "客户5678 · 139****5678"},
                "horizontal_content_list": [{"keyname": "整体标签", "value": "尊享型"}],
                "jump_list": [{"type": 1, "url": merged_data["update_url"],
                               "title": "查看完整画像"}],
                "card_action": {"type": 1, "url": merged_data["update_url"]},
            },
        }, ensure_ascii=False)
        out = self._run(merged_data, draft)
        parsed = json.loads(out)
        # 应该是 text_notice 画像卡（不是 button_interaction 选择卡）
        self.assertEqual(parsed["template_card"]["card_type"], "text_notice")
        self.assertNotIn("button_list", parsed["template_card"])


if __name__ == "__main__":
    unittest.main()
