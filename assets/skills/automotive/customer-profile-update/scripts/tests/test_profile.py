"""profile.py 取数+映射测试（stdlib unittest，独立于 make test）。"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import profile as P  # noqa: E402

ENUM_MAP = {
    "purchase_type": {"replacement": "换购", "loan": "贷款"},
    "driver_license_status": {"yes": "已持照", "no": "未持照"},
    "intended_model": {"zhuiguang": "追光"},
}

PROFILE = {
    "main_summary": {"deal_level": "A", "overall_tag": "务实家用型决策者", "personality_summary": "务实家用型"},
    "customer_overview": {"current_stage": "需求确认", "breakthrough_point": "金融方案与质保"},
    "basic_notes": {
        "intended_model": {"value": "zhuiguang"},
        "budget_range": {"value": "30-40万"},
        "purchase_type": {"value": "replacement||loan"},
        "driver_license_status": {"value": '{"key":"yes","input_value":""}'},
    },
    "purchase_motivations": [{"motivation_name": "家庭代步"}, {"motivation_name": "安全"}],
    "product_preferences": [{"preference_name": "空间"}, {"preference_name": "油耗"}],
    "resistances": [{"resistance_name": "价格"}, {"resistance_name": "品牌力"}],
}


class TestParseNote(unittest.TestCase):
    def test_enum_mapping(self):
        self.assertEqual(P.parse_note(PROFILE["basic_notes"], "intended_model", ENUM_MAP), "追光")

    def test_json_string_field(self):
        # driver_license_status 值是 JSON 字符串 → 取 key=yes → 枚举映射"已持照"
        v = P.parse_note(PROFILE["basic_notes"], "driver_license_status", ENUM_MAP)
        self.assertEqual(v, "已持照")

    def test_multi_value_split(self):
        # purchase_type=replacement||loan → 枚举映射在 || 拆分前对整体不命中，
        # 所以 || 拆分后是 "replacement / loan"（逐项未映射）。验证拆分行为。
        v = P.parse_note(PROFILE["basic_notes"], "purchase_type", ENUM_MAP)
        self.assertEqual(v, "replacement / loan")

    def test_plain_value(self):
        self.assertEqual(P.parse_note(PROFILE["basic_notes"], "budget_range", ENUM_MAP), "30-40万")

    def test_missing_key(self):
        self.assertEqual(P.parse_note({}, "nope", ENUM_MAP), "")


class TestExtractFields(unittest.TestCase):
    def test_full_extraction(self):
        f = P.extract_fields(PROFILE, ENUM_MAP)
        self.assertEqual(f["deal_level"], "A")
        self.assertEqual(f["overall_tag"], "务实家用型决策者")
        self.assertEqual(f["intended_model"], "追光")
        self.assertEqual(f["current_stage"], "需求确认")
        self.assertEqual(f["breakthrough_point"], "金融方案与质保")
        self.assertEqual(f["motivations"], "家庭代步 / 安全")
        self.assertEqual(f["preferences"], "空间 / 油耗")
        self.assertEqual(f["resistances"], "价格 / 品牌力")

    def test_empty_arrays(self):
        prof = {"main_summary": {}, "customer_overview": {}, "basic_notes": {}}
        f = P.extract_fields(prof, {})
        self.assertEqual(f["motivations"], "")
        self.assertEqual(f["preferences"], "")


class TestBuildOutput(unittest.TestCase):
    def test_with_profile(self):
        out = P.build_output("13912345678", "客户5678", PROFILE, ENUM_MAP)
        self.assertTrue(out["ok"])
        self.assertTrue(out["has_profile"])
        self.assertEqual(out["phone"], "13912345678")
        self.assertIn("13912345678", out["update_url"])
        self.assertEqual(out["fields"]["intended_model"], "追光")

    def test_without_profile(self):
        out = P.build_output("13912345678", "客户5678", None, {})
        self.assertTrue(out["ok"])
        self.assertFalse(out["has_profile"])
        self.assertEqual(out["fields"], {})
        self.assertIn("13912345678", out["update_url"])


class TestFetchProfile(unittest.TestCase):
    @patch("profile.http_get_json")
    def test_success(self, mock_get):
        mock_get.return_value = (200, PROFILE)
        prof, err = P.fetch_profile("13912345678", "KEY")
        self.assertIsNone(err)
        self.assertEqual(prof["main_summary"]["deal_level"], "A")

    @patch("profile.http_get_json")
    def test_empty_profile(self, mock_get):
        mock_get.return_value = (200, {})
        prof, err = P.fetch_profile("13912345678", "KEY")
        self.assertIsNone(err)
        self.assertIsNone(prof)

    @patch("profile.http_get_json")
    def test_auth_fail(self, mock_get):
        mock_get.return_value = (401, None)
        prof, err = P.fetch_profile("13912345678", "KEY")
        self.assertEqual(err, "auth_fail")


if __name__ == "__main__":
    unittest.main()
