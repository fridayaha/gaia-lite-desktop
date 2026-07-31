"""scratch.py — profile 私有临时目录 helper 测试（stdlib unittest，独立于 make test）。

覆盖多租户隔离的核心保证：
  - 按 HERMES_HOME / HOME 解析 profile 私有目录（绝不写共享 /tmp）
  - 0700 权限
  - 无 profile 上下文返回 None（降级不缓存）
  - 超 TTL 文件被清理、新文件保留
  - 不同 HERMES_HOME → 不同目录（租户隔离）
"""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scratch  # noqa: E402


class TestSkillScratch(unittest.TestCase):
    def test_uses_hermes_home(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                d = scratch.skill_scratch()
                self.assertEqual(d, os.path.join(td, ".skill_tmp"))
                self.assertTrue(os.path.isdir(d))
                self.assertEqual(os.stat(d).st_mode & 0o777, 0o700)

    def test_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HOME": td}):
                os.environ.pop("HERMES_HOME", None)
                d = scratch.skill_scratch()
                self.assertEqual(d, os.path.join(td, ".skill_tmp"))

    def test_no_env_returns_none(self):
        """无 HERMES_HOME/HOME → 返回 None（调用方降级不缓存，绝不回退 /tmp）。"""
        with patch.dict(os.environ, {}):
            os.environ.pop("HERMES_HOME", None)
            os.environ.pop("HOME", None)
            self.assertIsNone(scratch.skill_scratch())

    def test_dir_idempotent_reuses(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                d1 = scratch.skill_scratch()
                d2 = scratch.skill_scratch()
                self.assertEqual(d1, d2)

    def test_different_hermes_home_isolates_tenants(self):
        """两个 profile（不同 HERMES_HOME）→ 不同 .skill_tmp，互不可见。"""
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            with patch.dict(os.environ, {"HERMES_HOME": td1}):
                d1 = scratch.skill_scratch()
            with patch.dict(os.environ, {"HERMES_HOME": td2}):
                d2 = scratch.skill_scratch()
            self.assertEqual(d1, os.path.join(td1, ".skill_tmp"))
            self.assertEqual(d2, os.path.join(td2, ".skill_tmp"))
            self.assertNotEqual(d1, d2)


class TestSweep(unittest.TestCase):
    def test_sweeps_expired_files(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                d = scratch.skill_scratch()
                fresh = os.path.join(d, "fresh.json")
                stale = os.path.join(d, "stale.json")
                with open(fresh, "w") as f:
                    f.write("{}")
                with open(stale, "w") as f:
                    f.write("{}")
                old = time.time() - (scratch.SKILL_TMP_TTL + 60)
                os.utime(stale, (old, old))
                # 再次调用 → sweep 删 stale、留 fresh
                scratch.skill_scratch()
                self.assertTrue(os.path.exists(fresh))
                self.assertFalse(os.path.exists(stale))

    def test_keeps_fresh_files(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                d = scratch.skill_scratch()
                f = os.path.join(d, "cp_detail_139.json")
                with open(f, "w") as fh:
                    fh.write("{}")
                scratch.skill_scratch()
                self.assertTrue(os.path.exists(f))


if __name__ == "__main__":
    unittest.main()
