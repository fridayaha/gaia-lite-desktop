"""run.py 取数器测试（stdlib unittest，独立于 make test）。"""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import run  # noqa: E402

API_RESPONSE = {
    "code": 0,
    "data": {
        "total": 2,
        "items": [
            {
                "test_drive_id": "1",
                "customer_name": "王",
                "customer_phone": "176****5538",
                "start_time": "2026-05-27 13:59:57",
                "end_time": "2026-05-27 16:05:52",
                "vehicle": "M817",
                "vehicle_model": "M817",
                "vehicle_variant": "A",
                "report_url": "http://r/1",
            },
            {
                "test_drive_id": "2",
                "customer_name": "李",
                "customer_phone": "138****8476",
                "start_time": "2026-05-27 09:00:00",
                "end_time": "2026-05-27 10:00:00",
                "vehicle": "M9",
                "vehicle_model": "M9",
                "vehicle_variant": "B",
                "report_url": "http://r/2",
            },
        ],
    },
}


def _mock_urlopen(response_dict):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_dict).encode()
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


class TestQueryApi(unittest.TestCase):
    @patch("run.urllib.request.urlopen")
    def test_success_returns_items_total_code(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen(API_RESPONSE)
        result = run.query_api("13800000000")
        self.assertIsNotNone(result)
        items, total, code = result
        self.assertEqual(total, 2)
        self.assertEqual(code, 0)
        self.assertEqual(len(items), 2)

    @patch("run.urllib.request.urlopen")
    def test_exception_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("timeout")
        self.assertIsNone(run.query_api("13800000000"))

    @patch("run.urllib.request.urlopen")
    def test_code_nonzero_returns_none(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"code": 1, "message": "err"})
        self.assertIsNone(run.query_api("13800000000"))

    @patch("run.urllib.request.urlopen")
    def test_api_key_passed_as_x_api_key_header(self, mock_urlopen):
        """api_key 非空 → Request 带 X-API-Key 头。"""
        mock_urlopen.return_value = _mock_urlopen(API_RESPONSE)
        run.query_api("13800000000", api_key="secret-key")
        req = mock_urlopen.call_args[0][0]
        hdrs = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(hdrs.get("x-api-key"), "secret-key")

    @patch("run.urllib.request.urlopen")
    def test_no_api_key_no_header(self, mock_urlopen):
        """api_key=None → 不带 X-API-Key 头（兼容空 headers）。"""
        mock_urlopen.return_value = _mock_urlopen(API_RESPONSE)
        run.query_api("13800000000")
        req = mock_urlopen.call_args[0][0]
        hdrs = {k.lower(): v for k, v in req.header_items()}
        self.assertNotIn("x-api-key", hdrs)


class TestComputeHints(unittest.TestCase):
    def test_cross_time_slot_multi(self):
        h = run.compute_hints(API_RESPONSE["data"]["items"])
        self.assertTrue(h["cross_time_slot"])  # 13:00 + 09:00
        self.assertFalse(h["same_customer_multi_car"])
        self.assertEqual(h["count_category"], "multi")

    def test_single(self):
        h = run.compute_hints([API_RESPONSE["data"]["items"][0]])
        self.assertEqual(h["count_category"], "single")
        self.assertFalse(h["cross_time_slot"])

    def test_empty(self):
        h = run.compute_hints([])
        self.assertEqual(h["count_category"], "none")
        self.assertFalse(h["cross_time_slot"])

    def test_same_customer_multi_car(self):
        items = [
            {
                "customer_phone": "176****5538",
                "start_time": "2026-05-27 13:00:00",
                "vehicle_model": "M817",
            },
            {
                "customer_phone": "176****5538",
                "start_time": "2026-05-27 14:00:00",
                "vehicle_model": "M9",
            },
        ]
        self.assertTrue(run.compute_hints(items)["same_customer_multi_car"])

    def test_same_customer_same_car_not_flagged(self):
        items = [
            {
                "customer_phone": "176****5538",
                "start_time": "2026-05-27 13:00:00",
                "vehicle_model": "M817",
            },
            {
                "customer_phone": "176****5538",
                "start_time": "2026-05-27 14:00:00",
                "vehicle_model": "M817",
            },
        ]
        self.assertFalse(run.compute_hints(items)["same_customer_multi_car"])

    def test_many(self):
        items = [
            {"customer_phone": str(i), "start_time": "2026-05-27 13:00:00", "vehicle_model": "M"}
            for i in range(7)
        ]
        self.assertEqual(run.compute_hints(items)["count_category"], "many")


class TestAuthFail(unittest.TestCase):
    @patch("run.read_sales_phone", return_value="13800000000")
    @patch("run.get_api_key", side_effect=Exception("sidecar down"))
    def test_sidecar_down_returns_auth_fail(self, mock_key, _sp):
        """sidecar 不可达 → {"ok":false,"error":"auth_fail"}。"""
        import run
        old = sys.argv
        sys.argv = ["run.py"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf):
                run.main()
        finally:
            sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "auth_fail")


class TestSalesIdentity(unittest.TestCase):
    """sales_phone 从 USER.md 业务手机号读取（run.py 自动），缺失 → no_sales_identity。"""

    @patch("run.read_sales_phone", return_value=None)
    def test_no_sales_identity_returns_error(self, _sp):
        """USER.md 无业务手机号 → {"ok":false,"error":"no_sales_identity"}，不取 key/不调 API。"""
        import run
        old = sys.argv
        sys.argv = ["run.py"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf), patch("run.get_api_key") as mock_key, patch(
                "run.query_api"
            ) as mock_q:
                run.main()
        finally:
            sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "no_sales_identity")
        mock_key.assert_not_called()
        mock_q.assert_not_called()

    @patch("run.read_sales_phone", return_value="13900000000")
    @patch("run.get_api_key", return_value="k")
    @patch("run.query_api", return_value=([], 0, 0))
    def test_main_uses_read_sales_phone_value(self, mock_q, _key, _sp):
        """main() 用 read_sales_phone 的值调 query_api（不接受 CLI --sales-phone）。"""
        import run
        old = sys.argv
        sys.argv = ["run.py"]  # 无 --sales-phone
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            try:
                with patch.dict(os.environ, {"HERMES_HOME": td}), patch("sys.stdout", buf):
                    run.main()
            finally:
                sys.argv = old
        mock_q.assert_called_once()
        self.assertEqual(mock_q.call_args.args[0], "13900000000")  # 首参 = sales_phone


class TestMainOutputCapAndTee(unittest.TestCase):
    """main() 输出截到 MAX_CARD_ITEMS=6 + tee 写 tdr.json + total=api_total。"""

    def _run_main(self, api_items, tmp_dir):
        """跑 main()（mock query_api 返回 api_items），返回 stdout 解析后的 dict。"""
        old = sys.argv
        sys.argv = ["run.py"]
        buf = io.StringIO()
        try:
            with patch.dict(os.environ, {"HERMES_HOME": tmp_dir}), patch(
                "run.query_api", return_value=(api_items, len(api_items), 0)
            ), patch("run.get_api_key", return_value="k"), patch(
                "run.read_sales_phone", return_value="13800000000"
            ), patch("sys.stdout", buf):
                run.main()
        finally:
            sys.argv = old
        return json.loads(buf.getvalue())

    @staticmethod
    def _item(i, hour=13):
        return {
            "test_drive_id": str(i),
            "customer_name": f"c{i}",
            "customer_phone": str(i),
            "start_time": f"2026-05-27 {hour:02d}:00:00",
            "vehicle_model": "M",
            "report_url": f"http://r/{i}",
        }

    def test_output_capped_to_6_when_many(self):
        """命中 8 条 → items 截到 6，total=8（后过滤全量），count_category=many。"""
        with tempfile.TemporaryDirectory() as td:
            out = self._run_main([self._item(i) for i in range(8)], td)
            self.assertEqual(len(out["items"]), 6)  # 截到 6
            self.assertEqual(out["total"], 8)  # 后过滤命中数（未截）
            self.assertEqual(out["hints"]["count_category"], "many")  # >6 → many

    def test_tee_writes_tdr_json(self):
        """run.py tee：写 .skill_tmp/tdr.json，内容 = stdout。"""
        with tempfile.TemporaryDirectory() as td:
            out = self._run_main([self._item(1)], td)
            tdr_path = os.path.join(td, ".skill_tmp", "tdr.json")
            self.assertTrue(os.path.exists(tdr_path))
            with open(tdr_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), out)  # 文件 = stdout

    def test_total_is_api_count(self):
        """total = API 全量命中数（无时段过滤，items 即全量）。"""
        items = [self._item(1, 9), self._item(2, 10), self._item(3, 14), self._item(4, 15)]
        with tempfile.TemporaryDirectory() as td:
            out = self._run_main(items, td)
            self.assertEqual(out["total"], 4)  # API 全量 4 条
            self.assertEqual(len(out["items"]), 4)  # <6 不截

    def test_no_redirect_no_read_file_needed(self):
        """stdout 直接含 items（agent 读 terminal 输出，不 read_file）。"""
        with tempfile.TemporaryDirectory() as td:
            out = self._run_main([self._item(1), self._item(2)], td)
            self.assertEqual(len(out["items"]), 2)  # stdout 有完整 items
            self.assertIn("hints", out)


if __name__ == "__main__":
    unittest.main()
