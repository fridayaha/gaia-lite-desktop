"""search.py 取数器测试（stdlib unittest，独立于 make test）。"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import search  # noqa: E402

LIST_RESPONSE = {
    "total": 2,
    "items": [
        {"id": 49, "phone": "13912345678", "name": "客户5678", "deal_level": "B",
         "profile_sync_status": 0, "overall_tag": "尊享型"},
        {"id": 52, "phone": "13800001111", "name": "张先生", "deal_level": "A",
         "profile_sync_status": 1, "overall_tag": ""},
    ],
}

PROFILE_RESPONSE = {
    "main_summary": {"deal_level": "B", "overall_tag": "尊享型", "personality_summary": "务实"},
    "customer_overview": {"current_stage": "意向", "breakthrough_point": "价格"},
    "basic_notes": {"intended_model": {"value": "M817"}, "budget_range": {"value": "30-40万"}},
    "purchase_motivations": [{"motivation_name": "代步"}],
    "product_preferences": [{"preference_name": "空间"}],
    "resistances": [{"resistance_name": "价格"}],
}

ENUM_RESPONSE = {"configs": []}


def _mock_urlopen(response_dict, status=200):
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = json.dumps(response_dict).encode()
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


class TestSearchProfiles(unittest.TestCase):
    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_success(self, mock_get, mock_key):
        mock_get.return_value = (200, LIST_RESPONSE)
        out = search.search_profiles(customer_name_keyword="客户", api_key="KEY")
        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 2)
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["items"][0]["id"], 49)
        self.assertEqual(out["hints"]["count_category"], "multi")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_zero_hit(self, mock_get, mock_key):
        mock_get.return_value = (200, {"total": 0, "items": []})
        out = search.search_profiles(phone_keyword="0000", api_key="KEY")
        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["items"], [])
        self.assertEqual(out["hints"]["count_category"], "none")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_single_hit(self, mock_get, mock_key):
        mock_get.return_value = (200, {"total": 1, "items": [LIST_RESPONSE["items"][0]]})
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertEqual(out["hints"]["count_category"], "single")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_auth_fail_401(self, mock_get, mock_key):
        mock_get.return_value = (401, None)
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "auth_fail")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_forbidden_403(self, mock_get, mock_key):
        mock_get.return_value = (403, None)
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertEqual(out["error"], "forbidden")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_timeout(self, mock_get, mock_key):
        mock_get.return_value = (None, None)
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertEqual(out["error"], "timeout")

    @patch("search.get_api_key", side_effect=Exception("sidecar down"))
    def test_sidecar_fail(self, mock_key):
        out = search.search_profiles(customer_name_keyword="x")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "auth_fail")


class TestOutputCap(unittest.TestCase):
    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_items_capped_to_30(self, mock_get, mock_key):
        """API 返回 50 条 → items 截到 30，total 保留 50。"""
        items = [
            {"id": i, "phone": f"1390000{i:04d}", "name": f"c{i}",
             "deal_level": "B", "profile_sync_status": 0, "overall_tag": ""}
            for i in range(50)
        ]
        mock_get.return_value = (200, {"total": 50, "items": items})
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertEqual(out["total"], 50)
        self.assertEqual(len(out["items"]), search.MAX_ITEMS_OUTPUT)
        self.assertEqual(out["hints"]["returned"], search.MAX_ITEMS_OUTPUT)
        self.assertEqual(out["hints"]["count_category"], "multi")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_count_category_uses_total_not_len(self, mock_get, mock_key):
        """count_category 基于 total（真值），不基于 len(items)（可能被截）。"""
        mock_get.return_value = (200, {"total": 1, "items": [LIST_RESPONSE["items"][0]]})
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["hints"]["count_category"], "single")


class TestFetchProfileMerge(unittest.TestCase):
    """--fetch-profile + total=1 → 合并画像到搜索输出。"""

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    @patch("profile.http_get_json")
    def test_fetch_profile_single_hit_merges_fields(self, mock_prof_get, mock_get, mock_key):
        """total=1 + --fetch-profile → 输出含 fields/has_profile/update_url。"""
        # search API 返回 1 条
        search_resp = {"total": 1, "items": [LIST_RESPONSE["items"][0]]}
        mock_get.return_value = (200, search_resp)
        # profile API + enum API（profile.http_get_json 依次被 fetch_profile / fetch_enum_map 调用）
        mock_prof_get.side_effect = [(200, PROFILE_RESPONSE), (200, ENUM_RESPONSE)]

        out = search.search_profiles(
            customer_name_keyword="x", api_key="KEY"
        )
        # 模拟 main() 的合并逻辑
        search._fetch_and_merge_profile(out, "KEY")

        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 1)
        self.assertIn("fields", out)
        self.assertTrue(out["has_profile"])
        self.assertIn("update_url", out)
        self.assertEqual(out["phone"], "13912345678")
        self.assertEqual(out["customer_name"], "客户5678")
        # fields 含枚举映射后的值
        self.assertEqual(out["fields"]["deal_level"], "B")
        self.assertEqual(out["fields"]["overall_tag"], "尊享型")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_fetch_profile_multi_hit_no_merge(self, mock_get, mock_key):
        """total>1 + --fetch-profile → 不合并（只搜索结果）。"""
        mock_get.return_value = (200, LIST_RESPONSE)
        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        # main() 只在 total==1 时调 _fetch_and_merge_profile，这里模拟不调
        self.assertNotIn("fields", out)
        self.assertNotIn("has_profile", out)
        self.assertEqual(out["total"], 2)

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    @patch("profile.http_get_json")
    def test_fetch_profile_fail_keeps_search_only(self, mock_prof_get, mock_get, mock_key):
        """total=1 + --fetch-profile 但 profile API 失败 → 保持搜索结果原样。"""
        search_resp = {"total": 1, "items": [LIST_RESPONSE["items"][0]]}
        mock_get.return_value = (200, search_resp)
        mock_prof_get.return_value = (500, None)  # profile API 失败

        out = search.search_profiles(customer_name_keyword="x", api_key="KEY")
        search._fetch_and_merge_profile(out, "KEY")

        self.assertTrue(out["ok"])
        self.assertEqual(out["total"], 1)
        self.assertNotIn("fields", out)  # 画像失败，不合并
        self.assertNotIn("has_profile", out)


class TestPhoneTail(unittest.TestCase):
    """--phone-tail: API 模糊查 + 客户端 endswith 过滤。"""

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_tail_filters_out_middle_match(self, mock_get, mock_key):
        """API 返回 3 条（含中间匹配），phone_tail 过滤后只留尾号匹配的 2 条。"""
        api_items = [
            {"id": 1, "phone": "15388008001", "name": "A", "deal_level": "B",
             "profile_sync_status": 1, "overall_tag": ""},
            {"id": 2, "phone": "18872780018", "name": "B", "deal_level": "C",
             "profile_sync_status": 1, "overall_tag": ""},  # 8001 在中间
            {"id": 3, "phone": "13986098001", "name": "C", "deal_level": "A",
             "profile_sync_status": 1, "overall_tag": ""},
        ]
        mock_get.return_value = (200, {"total": 3, "items": api_items})
        out = search.search_profiles(phone_tail="8001", api_key="KEY")
        self.assertEqual(out["total"], 2)  # 过滤后 2 条
        phones = [it["phone"] for it in out["items"]]
        self.assertIn("15388008001", phones)
        self.assertIn("13986098001", phones)
        self.assertNotIn("18872780018", phones)  # 中间匹配被过滤

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_tail_zero_match(self, mock_get, mock_key):
        """API 有结果但都不以 tail 结尾 → total=0, items=[]。"""
        mock_get.return_value = (200, {"total": 1, "items": [
            {"id": 1, "phone": "18872780018", "name": "X", "deal_level": "B",
             "profile_sync_status": 1, "overall_tag": ""}]})
        out = search.search_profiles(phone_tail="9999", api_key="KEY")
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["items"], [])
        self.assertEqual(out["hints"]["count_category"], "none")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_tail_single_match(self, mock_get, mock_key):
        """尾号唯一匹配 → count_category=single。"""
        mock_get.return_value = (200, {"total": 1, "items": [
            {"id": 1, "phone": "13900008001", "name": "X", "deal_level": "B",
             "profile_sync_status": 1, "overall_tag": ""}]})
        out = search.search_profiles(phone_tail="8001", api_key="KEY")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["hints"]["count_category"], "single")

    @patch("search.get_api_key", return_value="KEY")
    @patch("search.http_get_json")
    def test_phone_keyword_still_fuzzy(self, mock_get, mock_key):
        """--phone-keyword（无 tail）保持模糊匹配，不过滤。"""
        mock_get.return_value = (200, {"total": 3, "items": [
            {"id": 1, "phone": "15388008001", "name": "A", "deal_level": "B",
             "profile_sync_status": 1, "overall_tag": ""},
            {"id": 2, "phone": "18872780018", "name": "B", "deal_level": "C",
             "profile_sync_status": 1, "overall_tag": ""},
        ]})
        out = search.search_profiles(phone_keyword="8001", api_key="KEY")
        self.assertEqual(out["total"], 2)  # 不过滤，全保留
        self.assertEqual(len(out["items"]), 2)


class TestTeeCpJson(unittest.TestCase):
    """_tee_cp_json: 写 .skill_tmp/cp.json + stdout 照常输出。"""

    def test_tee_writes_cp_json(self):
        """tee：写 .skill_tmp/cp.json，内容 = stdout。"""
        import tempfile
        out = {"ok": True, "total": 1, "items": [{"id": 1}]}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                search._tee_cp_json(out)
                cp_path = os.path.join(td, ".skill_tmp", "cp.json")
                self.assertTrue(os.path.exists(cp_path))
                with open(cp_path, encoding="utf-8") as f:
                    self.assertEqual(json.load(f), out)

    def test_tee_no_env_skips_file(self):
        """无 HERMES_HOME/HOME → 跳过写文件（不崩）。"""
        out = {"ok": True, "total": 0}
        with patch.dict(os.environ, {}, clear=True):
            search._tee_cp_json(out)  # 不应抛异常


class TestClassifyError(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(search.classify_error(401), "auth_fail")
        self.assertEqual(search.classify_error(403), "forbidden")
        self.assertEqual(search.classify_error(None), "timeout")
        self.assertEqual(search.classify_error(500), "api_fail")
        self.assertEqual(search.classify_error(422), "api_fail")


if __name__ == "__main__":
    unittest.main()
