"""auth.py — get_api_key 测试（stdlib unittest，独立于 make test）。"""
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auth  # noqa: E402


class TestGetApiKey(unittest.TestCase):
    @patch("auth.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        """sidecar 正常返回 → 返回 api_key 字符串。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"value": "test-api-key-123"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        key = auth.get_api_key()
        self.assertEqual(key, "test-api-key-123")
        mock_urlopen.assert_called_once()

    @patch("auth.urllib.request.urlopen")
    def test_sidecar_url_correct(self, mock_urlopen):
        """默认 sidecar URL 含 skill=test-drive-report。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"value": "k"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        auth.get_api_key()
        called_url = mock_urlopen.call_args[0][0]
        self.assertIn("skill=test-drive-report", called_url)
        self.assertIn("key=api_key", called_url)

    @patch("auth.urllib.request.urlopen")
    def test_sidecar_down_raises(self, mock_urlopen):
        """sidecar 不可达 → raise Exception（调用方捕获返 auth_fail）。"""
        mock_urlopen.side_effect = Exception("connection refused")
        with self.assertRaises(Exception):
            auth.get_api_key()

    @patch("auth.urllib.request.urlopen")
    def test_custom_sidecar_url(self, mock_urlopen):
        """自定义 sidecar_url → 用传入的 URL。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"value": "k"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        auth.get_api_key("http://custom:9999/secret")
        called_url = mock_urlopen.call_args[0][0]
        self.assertIn("custom:9999", called_url)


if __name__ == "__main__":
    unittest.main()
