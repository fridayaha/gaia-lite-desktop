"""detail.py 取数+概要测试（stdlib unittest，独立于 make test）。"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import detail as D  # noqa: E402

ENUM_MAP = {
    "purchase_type": {"replacement": "换购", "first": "首购"},
    "intended_model": {"zhuiguang": "追光"},
}

PROFILE = {
    "main_summary": {
        "deal_level": "A", "overall_tag": "务实家用型决策者",
        "personality_summary": "务实家用型", "profile_summary": "该客户务实注重家庭",
    },
    "basic_notes": {
        "intended_model": {"value": "zhuiguang", "reasoning_summary": "多次询问追光"},
        "budget_range": {"value": "30-40万", "reasoning_summary": "预算明确"},
        "purchase_type": {"value": "replacement", "reasoning_summary": "有置换需求"},
    },
    "inferred_tags": [{"title": "家庭导向", "desc": "x"}, {"title": "价格敏感", "desc": "y"}],
    "usage_scenarios": [{"title": "日常通勤", "desc": "x"}, {"title": "周末出游", "desc": "y"}],
    "customer_overview": {
        "customer_type": "换购客户", "closing_probability": "85%",
        "business_opp_level": "A", "core_issue": "价格",
        "current_stage": "需求确认", "breakthrough_point": "金融方案",
    },
    "emotion_state": {"current_state": "理性", "brand_attitude": "认可", "sales_attitude": "信任"},
    "purchase_motivations": [{"motivation_name": "家庭代步"}, {"motivation_name": "安全"}],
    "product_preferences": [{"preference_name": "空间"}, {"preference_name": "油耗"}],
    "resistances": [{"resistance_name": "价格", "severity": "中"}],
}


class TestMaskPhone(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(D.mask_phone("13912345678"), "139****5678")

    def test_short(self):
        self.assertEqual(D.mask_phone("123"), "123")

    def test_empty(self):
        self.assertEqual(D.mask_phone(""), "")


class TestBuildBrief(unittest.TestCase):
    def test_reuses_extract_fields(self):
        brief = D.build_brief(PROFILE, ENUM_MAP, "13912345678", "客户5678")["brief"]
        self.assertEqual(brief["deal_level"], "A")
        self.assertEqual(brief["overall_tag"], "务实家用型决策者")
        self.assertEqual(brief["intended_model"], "追光")  # 枚举映射
        self.assertEqual(brief["current_stage"], "需求确认")
        self.assertEqual(brief["motivations"], "家庭代步 / 安全")

    def test_extends_customer_overview(self):
        brief = D.build_brief(PROFILE, ENUM_MAP, "13912345678")["brief"]
        self.assertEqual(brief["closing_probability"], "85%")
        self.assertEqual(brief["customer_type"], "换购客户")
        self.assertEqual(brief["business_opp_level"], "A")
        self.assertEqual(brief["core_issue"], "价格")

    def test_extends_emotion_state(self):
        brief = D.build_brief(PROFILE, ENUM_MAP, "13912345678")["brief"]
        self.assertEqual(brief["emotion_current_state"], "理性")
        self.assertEqual(brief["brand_attitude"], "认可")
        self.assertEqual(brief["sales_attitude"], "信任")

    def test_extends_tags_scenarios_summary(self):
        brief = D.build_brief(PROFILE, ENUM_MAP, "13912345678")["brief"]
        self.assertEqual(brief["inferred_tags"], ["家庭导向", "价格敏感"])
        self.assertEqual(brief["usage_scenarios"], ["日常通勤", "周末出游"])
        self.assertEqual(brief["profile_summary"], "该客户务实注重家庭")

    def test_meta_fields(self):
        out = D.build_brief(PROFILE, ENUM_MAP, "13912345678", "客户5678")
        self.assertEqual(out["phone"], "139****5678")  # 脱敏
        self.assertEqual(out["customer_name"], "客户5678")
        self.assertIn("13912345678", out["update_url"])
        self.assertIn("/customer_profile/customer/13912345678/profile", out["update_url"])
        self.assertEqual(out["topics"], D.DEEP_DIVE_TOPICS)

    def test_hints_all_present(self):
        hints = D.build_brief(PROFILE, ENUM_MAP, "13912345678")["hints"]
        for k in ("has_main_summary", "has_basic_notes", "has_customer_overview",
                  "has_emotion_state", "has_motivations", "has_preferences",
                  "has_resistances", "has_inferred_tags", "has_usage_scenarios"):
            self.assertTrue(hints[k], f"{k} should be true")

    def test_hints_missing_modules(self):
        prof = {"main_summary": {}}
        hints = D.build_brief(prof, {}, "13912345678")["hints"]
        self.assertFalse(hints["has_customer_overview"])
        self.assertFalse(hints["has_emotion_state"])
        self.assertFalse(hints["has_inferred_tags"])

    def test_empty_modules_safe(self):
        prof = {"main_summary": {}}
        brief = D.build_brief(prof, {}, "13912345678")["brief"]
        self.assertEqual(brief["closing_probability"], "")
        self.assertEqual(brief["inferred_tags"], [])
        self.assertEqual(brief["emotion_current_state"], "")
        self.assertEqual(brief["profile_summary"], "")


class TestStoreFull(unittest.TestCase):
    def test_writes_0600_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            D.CACHE_DIR = td
            obj = {"profile": PROFILE, "enum_map": ENUM_MAP, "fetched_at": "2026-07-06T10:00:00"}
            path = D.store_full("13912345678", obj)
            self.assertIsNotNone(path)
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["enum_map"], ENUM_MAP)
            self.assertEqual(loaded["profile"]["main_summary"]["deal_level"], "A")

    def test_returns_none_on_failure(self):
        D.CACHE_DIR = "/nonexistent_path_xyz_abc"
        path = D.store_full("13912345678", {"x": 1})
        self.assertIsNone(path)

    def test_writes_to_profile_private_dir(self):
        """无 CACHE_DIR 覆盖时，store_full 写到 HERMES_HOME/.skill_tmp/，0600 + 真实内容。"""
        with tempfile.TemporaryDirectory() as td:
            with patch.object(D, "CACHE_DIR", None), patch.dict(os.environ, {"HERMES_HOME": td}):
                obj = {"profile": PROFILE, "enum_map": ENUM_MAP, "fetched_at": "T"}
                path = D.store_full("13912345678", obj)
                self.assertIsNotNone(path)
                self.assertTrue(path.startswith(os.path.join(td, ".skill_tmp")))
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                self.assertEqual(loaded["enum_map"], ENUM_MAP)
                self.assertEqual(loaded["profile"]["main_summary"]["deal_level"], "A")

    def test_returns_none_without_profile_context(self):
        """无 HERMES_HOME/HOME → 降级不缓存，store_full 返回 None（绝不回退 /tmp）。"""
        with patch.object(D, "CACHE_DIR", None), patch.dict(os.environ, {}):
            os.environ.pop("HERMES_HOME", None)
            os.environ.pop("HOME", None)
            self.assertIsNone(D.store_full("13912345678", {"x": 1}))


class TestMain(unittest.TestCase):
    def _run_main(self, argv):
        old = sys.argv
        sys.argv = argv
        try:
            D.main()
        finally:
            sys.argv = old

    @patch("detail.get_api_key")
    @patch("detail.fetch_profile")
    @patch("detail.fetch_enum_map")
    def test_success(self, mock_enum, mock_fetch, mock_key):
        mock_key.return_value = "KEY"
        mock_fetch.return_value = (PROFILE, None)
        mock_enum.return_value = ENUM_MAP
        with tempfile.TemporaryDirectory() as td:
            D.CACHE_DIR = td
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                self._run_main(["detail.py", "--phone", "13912345678", "--customer-name", "客户5678"])
            data = json.loads(buf.getvalue())
            self.assertTrue(data["ok"])
            self.assertTrue(data["has_profile"])
            self.assertEqual(data["phone"], "139****5678")
            self.assertEqual(data["brief"]["deal_level"], "A")
            self.assertEqual(data["brief"]["closing_probability"], "85%")
            self.assertIn("fetched_at", data)
            self.assertTrue(data["stored_at"])  # 存盘成功
            # 存盘文件 0600 + 内容正确
            with open(data["stored_at"], encoding="utf-8") as f:
                cached = json.load(f)
            self.assertEqual(cached["enum_map"], ENUM_MAP)

    @patch("detail.get_api_key")
    @patch("detail.fetch_profile")
    @patch("detail.fetch_enum_map")
    def test_brief_bounded_under_10kb(self, mock_enum, mock_fetch, mock_key):
        mock_key.return_value = "KEY"
        mock_fetch.return_value = (PROFILE, None)
        mock_enum.return_value = ENUM_MAP
        with tempfile.TemporaryDirectory() as td:
            D.CACHE_DIR = td
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                self._run_main(["detail.py", "--phone", "13912345678"])
            text = buf.getvalue()
        self.assertLess(len(text), 10000)  # 概要有界

    @patch("detail.get_api_key")
    @patch("detail.fetch_profile")
    @patch("detail.fetch_enum_map")
    def test_full_json_not_in_stdout(self, mock_enum, mock_fetch, mock_key):
        """stdout 只返概要，basic_notes 的 reasoning_summary 不外泄。"""
        mock_key.return_value = "KEY"
        mock_fetch.return_value = (PROFILE, None)
        mock_enum.return_value = ENUM_MAP
        with tempfile.TemporaryDirectory() as td:
            D.CACHE_DIR = td
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                self._run_main(["detail.py", "--phone", "13912345678"])
            text = buf.getvalue()
        self.assertNotIn("多次询问追光", text)  # reasoning_summary 值
        self.assertNotIn("reasoning_summary", text)  # 字段名

    @patch("detail.get_api_key")
    @patch("detail.fetch_profile")
    def test_has_profile_false(self, mock_fetch, mock_key):
        mock_key.return_value = "KEY"
        mock_fetch.return_value = (None, None)  # 200 + 空
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run_main(["detail.py", "--phone", "13912345678"])
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"])
        self.assertFalse(data["has_profile"])
        self.assertEqual(data["phone"], "139****5678")

    @patch("detail.get_api_key")
    def test_auth_fail_when_sidecar_down(self, mock_key):
        mock_key.side_effect = Exception("sidecar down")
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run_main(["detail.py", "--phone", "13912345678"])
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "auth_fail")

    @patch("detail.get_api_key")
    @patch("detail.fetch_profile")
    def test_fetch_errors_propagated(self, mock_fetch, mock_key):
        mock_key.return_value = "KEY"
        for err in ("forbidden", "api_fail", "timeout"):
            mock_fetch.return_value = (None, err)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                self._run_main(["detail.py", "--phone", "13912345678"])
            data = json.loads(buf.getvalue())
            self.assertFalse(data["ok"], f"{err} should be ok=false")
            self.assertEqual(data["error"], err)


if __name__ == "__main__":
    unittest.main()
