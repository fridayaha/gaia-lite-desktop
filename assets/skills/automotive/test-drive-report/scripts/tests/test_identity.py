"""identity.py read_sales_phone 测试（stdlib unittest，独立于 make test）。

read_sales_phone 从 manager user-context 端点读 business.业务手机号。
关键：只取「业务手机号」（business_phone），不取「手机号」（User.phone，在 fields 里）。
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import identity  # noqa: E402


def _mock_resp(body: dict):
    """模拟 urllib.urlopen 返回的 context manager。"""
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _ctx_with_profile(profile_name: str = "prof-test"):
    """设 HERMES_HOME 使 _resolve_profile_name 返回 profile_name。"""
    return patch.dict(os.environ, {"HERMES_HOME": f"/opt/data/profiles/{profile_name}"})


class TestReadSalesPhone(unittest.TestCase):
    def test_reads_business_phone(self):
        """端点返回 business.业务手机号 → 返回该号。"""
        with _ctx_with_profile():
            with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                {"fields": {"手机号": "13000000000"}, "business": {"业务手机号": "13812345678"}}
            )):
                self.assertEqual(identity.read_sales_phone(), "13812345678")

    def test_reads_non_digit_value(self):
        """业务手机号不限定数字，可能是字母（如 admin）→ 原值返回。"""
        with _ctx_with_profile():
            for val in ("admin", "sales001"):
                with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                    {"business": {"业务手机号": val}}
                )):
                    self.assertEqual(identity.read_sales_phone(), val)

    def test_does_not_pick_user_phone(self):
        """只有 fields.手机号（User.phone）无 business.业务手机号 → None。"""
        with _ctx_with_profile():
            with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                {"fields": {"手机号": "13000000000"}, "business": {}}
            )):
                self.assertIsNone(identity.read_sales_phone())

    def test_picks_business_not_user_when_both_present(self):
        """fields.手机号 与 business.业务手机号都在 → 只取业务手机号。"""
        with _ctx_with_profile():
            with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                {"fields": {"手机号": "13000000000"}, "business": {"业务手机号": "13812345678"}}
            )):
                self.assertEqual(identity.read_sales_phone(), "13812345678")

    def test_no_business_phone_returns_none(self):
        """business 无业务手机号 → None。"""
        with _ctx_with_profile():
            with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                {"fields": {"用户名": "张三"}, "business": {}}
            )):
                self.assertIsNone(identity.read_sales_phone())

    def test_endpoint_error_returns_none(self):
        """端点不可达/异常 → None（不抛）。"""
        with _ctx_with_profile():
            with patch.object(identity.urllib.request, "urlopen", side_effect=ConnectionError("timeout")):
                self.assertIsNone(identity.read_sales_phone())

    def test_no_profile_name_returns_none(self):
        """无 HERMES_HOME 且 cwd 无 basename → 不调端点返回 None。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HOME", None)
            with patch("os.getcwd", return_value="/"):
                with patch.object(identity.urllib.request, "urlopen") as m:
                    self.assertIsNone(identity.read_sales_phone())
                    m.assert_not_called()

    def test_sends_internal_token_header(self):
        """配置了 UA_INTERNAL_TOKEN → 请求带 X-Internal-Token 头。"""
        with _ctx_with_profile():
            with patch.dict(os.environ, {"UA_INTERNAL_TOKEN": "secret", "CONTROLLER_URL": "http://mgr:8002"}):
                with patch.object(identity.urllib.request, "urlopen", return_value=_mock_resp(
                    {"business": {"业务手机号": "13800000000"}}
                )) as m:
                    self.assertEqual(identity.read_sales_phone(), "13800000000")
                    req = m.call_args.args[0]
                    assert req.get_header("X-internal-token") == "secret"


if __name__ == "__main__":
    unittest.main()
