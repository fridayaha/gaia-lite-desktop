"""detail.py 详情取数 + 概要提取测试（stdlib unittest，独立于 make test）。"""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import detail  # noqa: E402

# 真实 API 响应结构的精简 fixture（含三模块 + 一个 sales_check 维度 null）
FULL_RESULT = {
    "test_drive_id": "1610926234373631604",
    "audio_filename": "12281777879575992.mp3",
    "customer_phone": "17621765538",
    "sales_check": {
        "communication": {
            "result": {
                "groups": [],
                "highlights": [{"title": "h", "desc": "d"}],
                "statistics": {"score_rate": "81.0%", "total_score": 81},
                "dimension_name": "沟通表达",
                "overall_evaluation": "本次试驾中销售整体沟通表现良好，介绍车辆功能逻辑清晰。",
                "improvement_suggestions": ["加大情绪起伏", "突出产品优势"],
            },
            "updated_at": "2026-05-27T16:11:29",
        },
        "knowledge": {
            "result": {
                "statistics": {"score_rate": "97.0%"},
                "dimension_name": "专业知识",
                "overall_evaluation": "专业知识整体表现优秀。",
                "improvement_suggestions": ["加强驾驶模式识别原理学习"],
                "groups": [{"group_name": "产品知识", "analysis_items": [{"item": "x"}]}],
            },
            "updated_at": "2026-05-27T16:15:32",
        },
        "deal_guide": None,  # 该维度未生成
        "process": {
            "result": {
                "statistics": {"score_rate": "18.0%"},
                "dimension_name": "流程执行",
                "overall_evaluation": "流程执行合规性较差。",
                "improvement_suggestions": ["严格落实试驾前必做动作"],
            },
            "updated_at": "2026-05-27T16:17:50",
        },
    },
    "deal_intent": {
        "result": {
            "vehicle": {"model": "M817"},
            "closerDashboard": {
                "stage": "客户处于对比评估阶段。",
                "aiInsight": "现有问界M7的家庭用户。",
                "closeLevel": "A",
                "painPoints": ["空悬配置性价比", "7月改款不确定性"],
                "closeProbability": 65,
            },
            "focusAnalysis": {
                "items": [
                    {
                        "weight": 92,
                        "details": "购置税补贴、置换补贴、落地价构成",
                        "evidence": ["evidence_should_not_be_in_brief"],
                        "dimension": "优惠补贴政策",
                        "rationaleChain": ["r1", "r2"],
                    }
                ],
                "summary": "客户核心关注购车成本合理性。",
            },
            "emotionHeatmap": [{"time": "14:03", "label": "试驾启动", "interest": 50}],
            "signalsAndRisks": {
                "risks": [{"level": "high", "description": "客户担忧7月改款持币观望"}],
                "signalStrength": 65,
                "explicitSignals": ["颜色偏好：银色跟绿色都行"],
                "implicitSignals": ["反复确认价格"],
            },
            "resistanceAnalysis": {
                "dimensions": [{"items": [], "summary": "未表现出明显价格抗拒。", "dimension": "价格"}],
                "overallSummary": "整体抗拒点集中在产品维度。",
            },
        },
        "updated_at": "2026-05-27T16:12:02",
    },
    "next_action": {
        "result": {
            "timeline": [{"time": "00:03", "type": "start", "description": "试驾启动"}],
            "salesAmmo": {"materials": [{"name": "m"}], "recommendedKit": "外放电露营附件套装"},
            "competitorCard": {"detected": False, "competitor": "未检测到竞品", "dimensions": []},
            "nextBestAction": {
                "actions": [
                    {
                        "text": "24小时内出具完整报价单。",
                        "priority": "urgent",
                        "motivation": "商务锁定",
                        "expectedImpact": "打消客户持币观望顾虑。",
                    }
                ],
                "aiReminder": ["未结合露营场景讲解空悬实用性"],
                "followUpScript": "陈总您好，非常感谢您今天抽时间来试驾猛士M817。",
            },
            "customerProfile": {
                "tags": ["华为智驾老用户", "家庭露营需求"],
                "riskAlert": "客户关注7月改款信息，存在持币观望延迟下单的风险",
                "winLossDrivers": {"drivers": [{"driver": "d"}], "blockers": [{"blocker": "b"}]},
            },
        },
        "updated_at": "2026-05-27T16:12:51",
    },
}


def _mock_urlopen(response_dict, status=200):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_dict).encode()
    mock_resp.status = status
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


class TestFetchDetail(unittest.TestCase):
    @patch("detail.urllib.request.urlopen")
    def test_success_parses_result_string(self, mock_urlopen):
        # API 返回 result 为 JSON 字符串
        api_resp = {"code": 0, "message": "success", "result": json.dumps(FULL_RESULT)}
        mock_urlopen.return_value = _mock_urlopen(api_resp)
        obj, err = detail.fetch_detail("1610926234373631604")
        self.assertIsNone(err)
        self.assertEqual(obj["test_drive_id"], "1610926234373631604")

    @patch("detail.urllib.request.urlopen")
    def test_success_result_already_dict(self, mock_urlopen):
        api_resp = {"code": 0, "message": "success", "result": FULL_RESULT}
        mock_urlopen.return_value = _mock_urlopen(api_resp)
        obj, err = detail.fetch_detail("X")
        self.assertIsNone(err)
        self.assertEqual(obj["deal_intent"]["result"]["vehicle"]["model"], "M817")

    @patch("detail.urllib.request.urlopen")
    def test_404_returns_not_found(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "u", 404, "Not Found", {}, io.BytesIO('{"code":1,"message":"记录不存在"}'.encode())
        )
        obj, err = detail.fetch_detail("missing")
        self.assertIsNone(obj)
        self.assertEqual(err, "not_found")

    @patch("detail.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("timeout")
        obj, err = detail.fetch_detail("X")
        self.assertIsNone(obj)
        self.assertEqual(err, "timeout")

    @patch("detail.urllib.request.urlopen")
    def test_code_nonzero_with_not_exist_message(self, mock_urlopen):
        api_resp = {"code": 1, "message": "记录不存在", "result": ""}
        mock_urlopen.return_value = _mock_urlopen(api_resp)
        _, err = detail.fetch_detail("X")
        self.assertEqual(err, "not_found")

    @patch("detail.urllib.request.urlopen")
    def test_code_nonzero_generic(self, mock_urlopen):
        api_resp = {"code": 1, "message": "服务器内部错误", "result": ""}
        mock_urlopen.return_value = _mock_urlopen(api_resp)
        _, err = detail.fetch_detail("X")
        self.assertEqual(err, "api_fail")

    @patch("detail.urllib.request.urlopen")
    def test_empty_result_returns_not_generated(self, mock_urlopen):
        api_resp = {"code": 0, "message": "success", "result": ""}
        mock_urlopen.return_value = _mock_urlopen(api_resp)
        _, err = detail.fetch_detail("X")
        self.assertEqual(err, "not_generated")

    @patch("detail.urllib.request.urlopen")
    def test_sales_phone_passed_when_given(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"code": 0, "result": FULL_RESULT})
        detail.fetch_detail("X", sales_phone="13800000000")
        req = mock_urlopen.call_args[0][0]
        called_url = req.full_url if hasattr(req, "full_url") else str(req)
        self.assertIn("sales_phone=13800000000", called_url)

    @patch("detail.urllib.request.urlopen")
    def test_api_key_passed_as_x_api_key_header(self, mock_urlopen):
        """api_key 非空 → Request 带 X-API-Key 头。"""
        mock_urlopen.return_value = _mock_urlopen({"code": 0, "result": FULL_RESULT})
        detail.fetch_detail("X", api_key="secret-key")
        req = mock_urlopen.call_args[0][0]
        hdrs = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(hdrs.get("x-api-key"), "secret-key")

    @patch("detail.urllib.request.urlopen")
    def test_no_api_key_no_header(self, mock_urlopen):
        """api_key=None → 不带 X-API-Key 头。"""
        mock_urlopen.return_value = _mock_urlopen({"code": 0, "result": FULL_RESULT})
        detail.fetch_detail("X")
        req = mock_urlopen.call_args[0][0]
        hdrs = {k.lower(): v for k, v in req.header_items()}
        self.assertNotIn("x-api-key", hdrs)

    @patch("detail.urllib.request.urlopen")
    def test_401_returns_auth_fail(self, mock_urlopen):
        """401 → classify_error 返 auth_fail，fetch_detail 返 (None, auth_fail)。"""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "u", 401, "Unauthorized", {}, io.BytesIO('{"code":1,"message":"unauth"}'.encode())
        )
        obj, err = detail.fetch_detail("X")
        self.assertIsNone(obj)
        self.assertEqual(err, "auth_fail")


class TestBuildBrief(unittest.TestCase):
    def setUp(self):
        self.brief = detail.build_brief(FULL_RESULT)

    def test_meta_fields(self):
        self.assertEqual(self.brief["test_drive_id"], "1610926234373631604")
        self.assertEqual(self.brief["customer_phone"], "176****5538")  # 脱敏
        self.assertEqual(self.brief["vehicle"], "M817")

    def test_phone_masking(self):
        self.assertEqual(detail.mask_phone("17621765538"), "176****5538")
        self.assertEqual(detail.mask_phone("123"), "123")
        self.assertEqual(detail.mask_phone(""), "")

    def test_deal_intent_brief(self):
        di = self.brief["brief"]["deal_intent"]
        self.assertEqual(di["close_level"], "A")
        self.assertEqual(di["close_probability"], 65)
        self.assertEqual(di["stage"], "客户处于对比评估阶段。")
        self.assertEqual(di["pain_points"], ["空悬配置性价比", "7月改款不确定性"])
        self.assertEqual(di["focus_summary"], "客户核心关注购车成本合理性。")
        self.assertEqual(di["resistance_summary"], "整体抗拒点集中在产品维度。")
        self.assertEqual(di["signal_strength"], 65)

    def test_focus_items_drop_evidence_and_rationale(self):
        # 概要里 focus_items 只保留 dimension/weight/details，不含 evidence/rationaleChain
        fi = self.brief["brief"]["deal_intent"]["focus_items"]
        self.assertEqual(len(fi), 1)
        self.assertEqual(fi[0]["dimension"], "优惠补贴政策")
        self.assertEqual(fi[0]["weight"], 92)
        self.assertNotIn("evidence", fi[0])
        self.assertNotIn("rationaleChain", fi[0])

    def test_sales_check_skips_null_dim(self):
        sc = self.brief["brief"]["sales_check"]
        # deal_guide 为 null → 不进概要
        self.assertIn("communication", sc)
        self.assertIn("knowledge", sc)
        self.assertIn("process", sc)
        self.assertNotIn("deal_guide", sc)

    def test_sales_check_fields(self):
        comm = self.brief["brief"]["sales_check"]["communication"]
        self.assertEqual(comm["score_rate"], "81.0%")
        self.assertEqual(comm["overall_evaluation"], "本次试驾中销售整体沟通表现良好，介绍车辆功能逻辑清晰。")
        self.assertEqual(len(comm["improvement_suggestions"]), 2)

    def test_next_action_brief(self):
        na = self.brief["brief"]["next_action"]
        self.assertEqual(len(na["actions"]), 1)
        self.assertEqual(na["actions"][0]["priority"], "urgent")
        self.assertEqual(na["follow_up_script"], "陈总您好，非常感谢您今天抽时间来试驾猛士M817。")
        self.assertEqual(na["customer_tags"], ["华为智驾老用户", "家庭露营需求"])
        self.assertEqual(na["risk_alert"], "客户关注7月改款信息，存在持币观望延迟下单的风险")
        self.assertEqual(na["recommended_kit"], "外放电露营附件套装")
        self.assertEqual(na["competitor"], "未检测到竞品")

    def test_updated_at_takes_latest_sales_check(self):
        # sales_check 无模块级 updated_at，取四维度最新 → process 的 16:17:50
        self.assertEqual(self.brief["updated_at"]["sales_check"], "2026-05-27T16:17:50")
        self.assertEqual(self.brief["updated_at"]["deal_intent"], "2026-05-27T16:12:02")
        self.assertEqual(self.brief["updated_at"]["next_action"], "2026-05-27T16:12:51")

    def test_hints(self):
        self.assertEqual(
            self.brief["hints"],
            {"has_sales_check": True, "has_deal_intent": True, "has_next_action": True},
        )

    def test_topics_list(self):
        self.assertIn("focus_analysis", self.brief["topics"])
        self.assertIn("timeline", self.brief["topics"])
        self.assertEqual(len(self.brief["topics"]), len(detail.DEEP_DIVE_TOPICS))

    def test_all_modules_null(self):
        b = detail.build_brief({"test_drive_id": "X", "customer_phone": "13800000000",
                                "sales_check": None, "deal_intent": None, "next_action": None})
        self.assertIsNone(b["brief"]["deal_intent"])
        self.assertIsNone(b["brief"]["sales_check"])
        self.assertIsNone(b["brief"]["next_action"])
        self.assertEqual(
            b["hints"],
            {"has_sales_check": False, "has_deal_intent": False, "has_next_action": False},
        )
        self.assertEqual(b["vehicle"], "")  # deal_intent null → 无 vehicle

    def test_brief_bounded_size(self):
        # 概要必须远小于完整 90KB——历史优化的核心保证。
        # 真实数据概要 ~8.7KB / 完整 ~118KB；此处 fixture 是压缩版，用绝对上限校验。
        size = len(json.dumps(self.brief, ensure_ascii=False))
        self.assertLess(size, 12000, f"brief 过大：{size} bytes")


class TestStoreFull(unittest.TestCase):
    def test_store_writes_0600_file(self):
        with patch.object(detail, "CACHE_DIR", tempfile.mkdtemp()):
            path = detail.store_full("TID123", FULL_RESULT)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
            # 权限 0600
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            # 内容是完整 result（非概要）
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            self.assertEqual(stored["test_drive_id"], "1610926234373631604")
            # 完整数据包含 evidence（深挖用）
            self.assertIn("evidence", stored["deal_intent"]["result"]["focusAnalysis"]["items"][0])
            os.remove(path)

    def test_store_returns_none_on_failure(self):
        with patch.object(detail, "CACHE_DIR", "/nonexistent_dir_xyz"):
            self.assertIsNone(detail.store_full("TID", FULL_RESULT))

    def test_writes_to_profile_private_dir(self):
        """无 CACHE_DIR 覆盖时，store_full 写到 HERMES_HOME/.skill_tmp/，0600 + 真实内容。"""
        with tempfile.TemporaryDirectory() as td:
            with patch.object(detail, "CACHE_DIR", None), patch.dict(os.environ, {"HERMES_HOME": td}):
                path = detail.store_full("TID123", FULL_RESULT)
                self.assertIsNotNone(path)
                self.assertTrue(path.startswith(os.path.join(td, ".skill_tmp")))
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                with open(path, encoding="utf-8") as f:
                    stored = json.load(f)
                self.assertEqual(stored["test_drive_id"], "1610926234373631604")
                os.remove(path)

    def test_returns_none_without_profile_context(self):
        """无 HERMES_HOME/HOME → 降级不缓存，store_full 返回 None（绝不回退 /tmp）。"""
        with patch.object(detail, "CACHE_DIR", None), patch.dict(os.environ, {}):
            os.environ.pop("HERMES_HOME", None)
            os.environ.pop("HOME", None)
            self.assertIsNone(detail.store_full("TID", FULL_RESULT))


class TestMain(unittest.TestCase):
    @patch("detail.read_sales_phone", return_value="13800000000")
    @patch("detail.get_api_key", return_value="fake-key")
    @patch("detail.store_full", return_value="/tmp/tdr_detail_X.json")
    @patch("detail.fetch_detail", return_value=(FULL_RESULT, None))
    def test_main_success_prints_brief_with_ok(self, _fetch, _store, _key, _sp):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            sys.argv = ["detail.py", "--test-drive-id", "X"]
            detail.main()
        finally:
            out = sys.stdout.getvalue()
            sys.stdout = old_stdout
        b = json.loads(out)
        self.assertTrue(b["ok"])
        self.assertEqual(b["test_drive_id"], "1610926234373631604")
        self.assertEqual(b["stored_at"], "/tmp/tdr_detail_X.json")
        # stdout 不含完整 evidence（重数据不进会话）
        self.assertNotIn("evidence_should_not_be_in_brief", out)

    @patch("detail.read_sales_phone", return_value="13800000000")
    @patch("detail.get_api_key", return_value="fake-key")
    @patch("detail.fetch_detail", return_value=(None, "not_found"))
    def test_main_error_prints_error(self, _fetch, _key, _sp):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            sys.argv = ["detail.py", "--test-drive-id", "missing"]
            detail.main()
        finally:
            out = sys.stdout.getvalue()
            sys.stdout = old_stdout
        e = json.loads(out)
        self.assertFalse(e["ok"])
        self.assertEqual(e["error"], "not_found")

    @patch("detail.read_sales_phone", return_value=None)
    def test_main_no_sales_identity_returns_error(self, _sp):
        """USER.md 无业务手机号 → {"ok":false,"error":"no_sales_identity"}，不取 key/不调 API。"""
        old = sys.argv
        sys.argv = ["detail.py", "--test-drive-id", "X"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf), patch("detail.get_api_key") as mock_key, patch(
                "detail.fetch_detail"
            ) as mock_fetch:
                detail.main()
        finally:
            sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "no_sales_identity")
        mock_key.assert_not_called()
        mock_fetch.assert_not_called()

    @patch("detail.read_sales_phone", return_value="13900000000")
    @patch("detail.get_api_key", return_value="k")
    @patch("detail.store_full", return_value="/tmp/x")
    @patch("detail.fetch_detail", return_value=(FULL_RESULT, None))
    def test_main_passes_read_sales_phone_to_fetch(self, mock_fetch, _store, _key, _sp):
        """main() 把 read_sales_phone 的值作为权限过滤传给 fetch_detail（不接受 CLI）。"""
        old = sys.argv
        sys.argv = ["detail.py", "--test-drive-id", "X"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf):
                detail.main()
        finally:
            sys.argv = old
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args.args[1], "13900000000")  # fetch_detail(tid, sales_phone, ...)


class TestAuthFail(unittest.TestCase):
    @patch("detail.read_sales_phone", return_value="13800000000")
    @patch("detail.get_api_key", side_effect=Exception("sidecar down"))
    def test_sidecar_down_returns_auth_fail(self, mock_key, _sp):
        """sidecar 不可达 → {"ok":false,"error":"auth_fail"}。"""
        import detail
        old = sys.argv
        sys.argv = ["detail.py", "--test-drive-id", "X"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf):
                detail.main()
        finally:
            sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "auth_fail")


if __name__ == "__main__":
    unittest.main()
