# 客户画像详情问答能力 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `customer-profile-update` skill 增加「基于画像详情的多轮问答」能力——销售在画像卡后追问画像详情（成交概率/情绪/标签/动机/抗性/雷达图/推理依据等），AI 调 `detail.py` 取概要、`query_detail.py` 取深挖切片，纯文本作答。

**Architecture:** Additive——新增 `detail.py` 复用 `profile.py` 的 `get_api_key`/`fetch_profile`/`fetch_enum_map`/`extract_fields`/`parse_note`/`UPDATE_BASE`，`profile.py` 不改一行，检索/卡片流程零回归。完整画像 ~90KB 存盘 `/tmp/cp_detail_{phone}.json`，stdout 只返 ~5-9KB 概要，避免全量进会话历史。画像可变 → 10min TTL 限定复用（detail.py 每次现取只写不读缓存；query_detail 查 mtime 过期返 `cache_missing` 触发重取）。

**Tech Stack:** Python 3.11+ 纯标准库（urllib/argparse/json/os/sys/time/datetime），测试用 stdlib `unittest`（无需 pytest），mock `detail`/`query_detail` 模块的导入函数。

## Global Constraints

- **纯标准库**，无第三方依赖——引擎 Pod 系统 python3 直接可跑。
- **profile.py 不改**——detail.py `from profile import get_api_key, fetch_profile, fetch_enum_map, extract_fields, parse_note, UPDATE_BASE`。
- **缓存文件** `/tmp/cp_detail_{phone}.json`（0600），内容 `{"profile": <raw>, "enum_map": <map>, "fetched_at": <iso>}`。
- **TTL 10min**（`CACHE_TTL = 600`）——detail.py 每次现取只写不读；query_detail 读前查 mtime，超 10min 返 `cache_missing`。
- **错误集**：`auth_fail`/`forbidden`/`api_fail`/`timeout`（复用 profile.py `classify_error`，**404 折叠为 `api_fail`**——profile API 不返 404，"未找到画像" UX 由 `has_profile=false` 覆盖；spec 错误表的 "404 not_found" 行在此折叠）+ `has_profile=false`。
- **测试**：stdlib `unittest`，`@patch("detail.fetch_profile")` 等 mock 导入函数，独立于 `make test`（与现有 `test_profile.py` 同约定）。运行命令 `python3 tests/test_detail.py -v`（无需 pytest）。
- **版本** `manifest.json` 2.1.0 → 2.1.1。
- **参照**：同目录 `test-drive-report/scripts/detail.py`、`query_detail.py` 已实现同模式（不可变数据；本 skill 的差异是 TTL 限定复用 + api_key/枚举映射复用）。

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `scripts/detail.py` | 新增 | 复用 profile.py 取完整画像+enum_map → 存盘 → 返富概要（含 fetched_at）|
| `scripts/query_detail.py` | 新增 | 纯文件读，mtime TTL 检查，按 `--topic` 取切片；basic_notes 主题复用 parse_note |
| `scripts/tests/test_detail.py` | 新增 | detail.py unittest |
| `scripts/tests/test_query_detail.py` | 新增 | query_detail.py unittest |
| `SKILL.md` | 改 | 加「画像详情问答」节 + gotcha + frontmatter description 扩展 |
| `manifest.json` | 改 | 2.1.0→2.1.1，description 补详情问答 |
| `references/api-spec.md` | 改 | 加 §5 detail.py/query_detail.py 输出 schema |
| `references/profile-model.md` | 改 | 顶部加 Q&A 用法说明（概要 vs 深挖字段归属）|

---

### Task 1: detail.py 纯逻辑（build_brief + store_full + helpers）

**Files:**
- Create: `assets/skills/automotive/customer-profile-update/scripts/detail.py`
- Test: `assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py`

**Interfaces:**
- Consumes: `profile.extract_fields(profile, enum_map) -> dict`、`profile.UPDATE_BASE`（str）
- Produces: `detail.build_brief(profile, enum_map, phone, customer_name="") -> dict`、`detail.store_full(phone, obj) -> str|None`、`detail.mask_phone(phone) -> str`、`detail.DEEP_DIVE_TOPICS`（list）、`detail.CACHE_DIR`（str，测试可改）、`detail.CACHE_TTL=600`

- [ ] **Step 1: 写失败测试（build_brief / store_full / helpers）**

Create `assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'detail'`

- [ ] **Step 3: 写最小实现（纯逻辑部分，无 main）**

Create `assets/skills/automotive/customer-profile-update/scripts/detail.py`:

```python
#!/usr/bin/env python3
"""
detail.py — 客户画像详情取数 + 概要提取（纯取数器）

调 GET /api/v1/remote/data/profile/{phone}（复用 profile.py 的 fetch_profile），
把完整画像 + enum_map 存到 /tmp/cp_detail_{phone}.json（0600），stdout 只返回
~5-9KB「概要」（高信号摘要），**避免 90KB 全量进入会话历史**。深挖内容由
query_detail.py 按主题从磁盘文件取。

画像可变（销售可能更新画像），故 detail.py 每次运行都从 API 现取（只写缓存、
不读缓存服务概要）；query_detail 读缓存时查 mtime，超 10min 返 cache_missing
触发本脚本重取。

  python3 detail.py --phone 13912345678 [--customer-name 客户5678] > /tmp/cp_brief.json

stdout 结构：
  {"ok": true, "phone": "139****5678", "customer_name": "...", "update_url": "...",
   "fetched_at": "...", "updated_at": "...", "stored_at": "/tmp/cp_detail_<phone>.json",
   "brief": {deal_level, overall_tag, ..., closing_probability, emotion_current_state,
             inferred_tags, usage_scenarios, ...},
   "topics": [...], "hints": {"has_main_summary": bool, ...}}
  {"ok": true, "has_profile": false, "phone": "...", ...}   # 客户存在但无画像
  {"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}

只用标准库。API Key 经 sidecar 解密（复用 profile.py 的 get_api_key）。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profile import (  # noqa: E402
    get_api_key, fetch_profile, fetch_enum_map, extract_fields, UPDATE_BASE,
)

CACHE_DIR = "/tmp"
CACHE_TTL = 600  # 10min（与 query_detail.py 一致）

# query_detail.py 支持的深挖主题——写进概要让 AI 知道可问什么
DEEP_DIVE_TOPICS = [
    "basic_notes_detail", "customer_overview_detail", "emotion_detail",
    "motivations_detail", "preferences_detail", "resistances_detail",
    "inferred_tags", "usage_scenarios", "personality",
]


def mask_phone(phone):
    """手机号脱敏：前3后4中间****。"""
    if not phone:
        return ""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


def store_full(phone, obj):
    """存 {profile, enum_map, fetched_at} 到 /tmp/cp_detail_{phone}.json (0600)。

    返回路径，失败返回 None。best-effort（磁盘满等不致命，query_detail 会 cache_missing）。
    """
    path = os.path.join(CACHE_DIR, f"cp_detail_{phone}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.chmod(path, 0o600)
    except Exception:
        return None
    return path


def _brief_customer_overview(co):
    """customer_overview 概要字段。"""
    if not co or not isinstance(co, dict):
        return {}
    return {
        "closing_probability": str(co.get("closing_probability", "") or ""),
        "customer_type": str(co.get("customer_type", "") or ""),
        "business_opp_level": str(co.get("business_opp_level", "") or ""),
        "core_issue": str(co.get("core_issue", "") or ""),
    }


def _brief_emotion_state(es):
    """emotion_state 概要字段。"""
    if not es or not isinstance(es, dict):
        return {}
    return {
        "emotion_current_state": str(es.get("current_state", "") or ""),
        "brand_attitude": str(es.get("brand_attitude", "") or ""),
        "sales_attitude": str(es.get("sales_attitude", "") or ""),
    }


def _titles(arr):
    """从 [{title, desc}, ...] 取 title 列表。"""
    if not arr or not isinstance(arr, list):
        return []
    return [str(it.get("title", "")) for it in arr if isinstance(it, dict) and it.get("title")]


def _latest_updated_at(profile):
    """best-effort 取画像 updated_at。API 顶层未必有，返回 ""。"""
    if isinstance(profile, dict):
        v = profile.get("updated_at")
        if v:
            return str(v)
    return ""


def build_brief(profile, enum_map, phone, customer_name=""):
    """从完整画像提富概要。复用 extract_fields + 扩展 customer_overview/emotion_state/标签/场景。

    不含 ok/has_profile/stored_at/fetched_at（由 main 注入）。
    """
    fields = extract_fields(profile, enum_map)
    ms = profile.get("main_summary") or {}
    co = profile.get("customer_overview") or {}
    es = profile.get("emotion_state") or {}

    brief = dict(fields)  # 复用 extract_fields 的 10 字段
    brief["profile_summary"] = str(ms.get("profile_summary", "") or "")
    brief.update(_brief_customer_overview(co))
    brief.update(_brief_emotion_state(es))
    brief["inferred_tags"] = _titles(profile.get("inferred_tags"))
    brief["usage_scenarios"] = _titles(profile.get("usage_scenarios"))

    return {
        "phone": mask_phone(phone),
        "customer_name": customer_name,
        "update_url": f"{UPDATE_BASE}/customer_profile/customer/{phone}/profile",
        "updated_at": _latest_updated_at(profile),
        "brief": brief,
        "topics": DEEP_DIVE_TOPICS,
        "hints": {
            "has_main_summary": bool(ms),
            "has_basic_notes": bool(profile.get("basic_notes")),
            "has_customer_overview": bool(co),
            "has_emotion_state": bool(es),
            "has_motivations": bool(profile.get("purchase_motivations")),
            "has_preferences": bool(profile.get("product_preferences")),
            "has_resistances": bool(profile.get("resistances")),
            "has_inferred_tags": bool(profile.get("inferred_tags")),
            "has_usage_scenarios": bool(profile.get("usage_scenarios")),
        },
    }
```

（`main` 在 Task 2 加。先不加 `if __name__ == "__main__"` 块，Task 2 补。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v`
Expected: PASS（TestMaskPhone 3 + TestBuildBrief 8 + TestStoreFull 2 = 13 tests）

- [ ] **Step 5: 提交**

```bash
git add assets/skills/automotive/customer-profile-update/scripts/detail.py \
        assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py
git commit -m "feat(skill): customer-profile detail.py 纯逻辑（build_brief + store_full）

复用 profile.py 的 extract_fields + UPDATE_BASE，扩展 customer_overview/emotion_state/
inferred_tags/usage_scenarios 高信号字段。存盘 0600。Task 1/5。

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 2: detail.py main + API 接线 + 错误处理

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/scripts/detail.py`（追加 `main()` + `__main__` 块）
- Test: `assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py`（追加 `TestMain`）

**Interfaces:**
- Consumes: Task 1 的 `build_brief`/`store_full`/`mask_phone`；`profile.get_api_key()`、`profile.fetch_profile(phone, api_key) -> (profile|None, error|None)`、`profile.fetch_enum_map(api_key) -> dict`
- Produces: `detail.main()`（CLI 入口）；stdout JSON schema 见文件头注释

- [ ] **Step 1: 写失败测试（TestMain）**

Append to `assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py`，在 `if __name__ == "__main__"` 之前插入：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v`
Expected: FAIL（TestMain 全挂，`AttributeError: module 'detail' has no attribute 'main'`）

- [ ] **Step 3: 追加 main() 实现**

在 `assets/skills/automotive/customer-profile-update/scripts/detail.py` 末尾追加：

```python
def main():
    p = argparse.ArgumentParser(description="客户画像详情取数 + 概要")
    p.add_argument("--phone", required=True, help="客户完整手机号")
    p.add_argument("--customer-name", default="", help="客户名称（用于概要标题）")
    args = p.parse_args()

    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "phone": args.phone}, ensure_ascii=False))
        return

    profile, err = fetch_profile(args.phone, api_key)
    if err:
        print(json.dumps({"ok": False, "error": err, "phone": args.phone}, ensure_ascii=False))
        return
    if not profile:  # 200 + 空（has_profile=false）
        print(json.dumps({
            "ok": True,
            "has_profile": False,
            "phone": mask_phone(args.phone),
            "customer_name": args.customer_name,
        }, ensure_ascii=False))
        return

    enum_map = fetch_enum_map(api_key)
    fetched_at = datetime.datetime.now().isoformat()
    stored = store_full(args.phone, {"profile": profile, "enum_map": enum_map, "fetched_at": fetched_at})
    brief = build_brief(profile, enum_map, args.phone, args.customer_name)
    brief["ok"] = True
    brief["has_profile"] = True
    brief["fetched_at"] = fetched_at
    brief["stored_at"] = stored or ""
    print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v`
Expected: PASS（13 + 6 = 19 tests）

- [ ] **Step 5: 冒烟（--help + import）**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 detail.py --help`
Expected: 打印 usage，`--phone` 必填、`--customer-name` 可选，退出码 0。

- [ ] **Step 6: 提交**

```bash
git add assets/skills/automotive/customer-profile-update/scripts/detail.py \
        assets/skills/automotive/customer-profile-update/scripts/tests/test_detail.py
git commit -m "feat(skill): customer-profile detail.py main + API 接线 + 错误处理

复用 profile.py 的 get_api_key/fetch_profile/fetch_enum_map；每次现取只写不读缓存；
has_profile=false/auth_fail/forbidden/api_fail/timeout 分支；概要有界 <10KB 且
reasoning_summary 不外泄。Task 2/5。

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 3: query_detail.py（9 主题 + TTL + main）

**Files:**
- Create: `assets/skills/automotive/customer-profile-update/scripts/query_detail.py`
- Test: `assets/skills/automotive/customer-profile-update/scripts/tests/test_query_detail.py`

**Interfaces:**
- Consumes: `profile.parse_note(notes, key, enum_map) -> str`；detail.py 写的缓存文件格式 `{"profile": <raw>, "enum_map": <map>, "fetched_at": <iso>}`
- Produces: `query_detail.main()`（CLI 入口）；`query_detail.TOPICS`（dict）；`query_detail.load_cache(phone) -> (obj|None, path|None)`；`query_detail.CACHE_DIR`、`query_detail.CACHE_TTL=600`

- [ ] **Step 1: 写失败测试**

Create `assets/skills/automotive/customer-profile-update/scripts/tests/test_query_detail.py`:

```python
"""query_detail.py 深挖切片测试（stdlib unittest，独立于 make test）。"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import query_detail as Q  # noqa: E402

ENUM_MAP = {
    "purchase_type": {"replacement": "换购"},
    "intended_model": {"zhuiguang": "追光"},
}

PROFILE = {
    "main_summary": {"deal_level": "A", "overall_tag": "务实", "profile_summary": "总结"},
    "basic_notes": {
        "intended_model": {"value": "zhuiguang", "reasoning_summary": "多次询问"},
        "budget_range": {"value": "30-40万", "reasoning_summary": "预算明确"},
        "purchase_type": {"value": "replacement", "reasoning_summary": "置换"},
    },
    "customer_overview": {"closing_probability": "85%", "customer_type": "换购"},
    "emotion_state": {
        "current_state": "理性",
        "radar_data": {"items": [{"score": 80, "dimension": "信任"}], "max_score": 100},
    },
    "purchase_motivations": [{"motivation_name": "家庭代步", "reasoning_detail": {"summary": "s"}}],
    "product_preferences": [{"preference_name": "空间"}],
    "resistances": [{"resistance_name": "价格", "severity": "中"}],
    "inferred_tags": [{"title": "家庭导向", "desc": "x"}],
    "usage_scenarios": [{"title": "通勤", "desc": "x"}],
}

CACHE = {"profile": PROFILE, "enum_map": ENUM_MAP, "fetched_at": "2026-07-06T10:00:00"}


def _run(topic, cache=CACHE, phone="13912345678"):
    with tempfile.TemporaryDirectory() as td:
        Q.CACHE_DIR = td
        path = os.path.join(td, f"cp_detail_{phone}.json")
        with open(path, "w") as f:
            json.dump(cache, f)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", phone, "--topic", topic]
            try:
                Q.main()
            finally:
                sys.argv = old
    return json.loads(buf.getvalue())


class TestLoadCache(unittest.TestCase):
    def test_missing(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)

    def test_valid(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                json.dump(CACHE, f)
            obj, _ = Q.load_cache("13912345678")
        self.assertEqual(obj["enum_map"], ENUM_MAP)

    def test_expired_mtime(self):
        """mtime 超 TTL 视为过期 → None。"""
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                json.dump(CACHE, f)
            old = time.time() - 660  # 11min 前
            os.utime(path, (old, old))
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)

    def test_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            path = os.path.join(td, "cp_detail_13912345678.json")
            with open(path, "w") as f:
                f.write("{not json")
            obj, _ = Q.load_cache("13912345678")
        self.assertIsNone(obj)


class TestTopics(unittest.TestCase):
    def test_basic_notes_detail_mapped(self):
        """basic_notes 主题：parse_note 现映射 value + reasoning_summary。"""
        data = _run("basic_notes_detail")
        self.assertTrue(data["ok"])
        bn = data["data"]
        self.assertEqual(bn["intended_model"]["value"], "追光")  # 枚举映射
        self.assertEqual(bn["purchase_type"]["value"], "换购")  # 枚举映射
        self.assertEqual(bn["budget_range"]["value"], "30-40万")  # 直接值
        self.assertEqual(bn["intended_model"]["reasoning_summary"], "多次询问")

    def test_customer_overview_detail(self):
        data = _run("customer_overview_detail")
        self.assertEqual(data["data"]["closing_probability"], "85%")

    def test_emotion_detail(self):
        data = _run("emotion_detail")
        self.assertEqual(data["data"]["current_state"], "理性")
        self.assertEqual(data["data"]["radar_data"]["items"][0]["dimension"], "信任")

    def test_motivations_detail(self):
        data = _run("motivations_detail")
        self.assertEqual(data["data"][0]["motivation_name"], "家庭代步")

    def test_preferences_detail(self):
        data = _run("preferences_detail")
        self.assertEqual(data["data"][0]["preference_name"], "空间")

    def test_resistances_detail(self):
        data = _run("resistances_detail")
        self.assertEqual(data["data"][0]["resistance_name"], "价格")
        self.assertEqual(data["data"][0]["severity"], "中")

    def test_inferred_tags(self):
        data = _run("inferred_tags")
        self.assertEqual(data["data"][0]["title"], "家庭导向")

    def test_usage_scenarios(self):
        data = _run("usage_scenarios")
        self.assertEqual(data["data"][0]["title"], "通勤")

    def test_personality(self):
        data = _run("personality")
        self.assertEqual(data["data"]["deal_level"], "A")
        self.assertEqual(data["data"]["profile_summary"], "总结")


class TestNullModule(unittest.TestCase):
    def test_null_module_returns_data_none_with_message(self):
        """依赖模块缺失 → data=null + 未生成提示。"""
        cache = {"profile": {"main_summary": {}}, "enum_map": {}, "fetched_at": "..."}  # 无 emotion_state
        data = _run("emotion_detail", cache=cache)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["data"])
        self.assertIn("情绪状态", data["message"])


class TestErrors(unittest.TestCase):
    def test_cache_missing(self):
        with tempfile.TemporaryDirectory() as td:
            Q.CACHE_DIR = td
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                old = sys.argv
                sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "emotion_detail"]
                try:
                    Q.main()
                finally:
                    sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "cache_missing")

    def test_unknown_topic(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "nope"]
            try:
                Q.main()
            finally:
                sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "unknown_topic")
        self.assertIn("available", data)

    def test_help_lists_all_topics(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            old = sys.argv
            sys.argv = ["query_detail.py", "--phone", "13912345678", "--topic", "help"]
            try:
                Q.main()
            finally:
                sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["available"]), 9)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_query_detail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'query_detail'`

- [ ] **Step 3: 写 query_detail.py 完整实现**

Create `assets/skills/automotive/customer-profile-update/scripts/query_detail.py`:

```python
#!/usr/bin/env python3
"""
query_detail.py — 客户画像详情深挖切片（从 detail.py 存盘的文件取）

读 /tmp/cp_detail_{phone}.json，按 --topic 返回特定切片。**避免把 90KB 全量塞进
会话历史**——AI 概要答不了时，按主题取一小片。

画像可变：缓存文件 mtime 超 10min 视为陈旧，返 cache_missing 触发 detail.py 重取。

  python3 query_detail.py --phone 13912345678 --topic emotion_detail

stdout 结构：
  {"ok": true, "phone": "...", "topic": "...", "data": <切片>}
  {"ok": true, "phone": "...", "topic": "...", "data": null,
   "message": "该客户的<模块>模块尚未生成"}                      # 依赖模块 null
  {"ok": false, "error": "cache_missing", "phone": "...",
   "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
  {"ok": false, "error": "unknown_topic", "available": [...]}

只用标准库。文件由 detail.py 写入（0600，会话级 /tmp）。basic_notes 主题复用
profile.py 的 parse_note 做枚举映射。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profile import parse_note  # noqa: E402

CACHE_DIR = "/tmp"
CACHE_TTL = 600  # 10min（与 detail.py 一致）

MODULE_LABEL = {
    "basic_notes": "基础属性",
    "customer_overview": "客户总览",
    "emotion_state": "情绪状态",
    "purchase_motivations": "购买动机",
    "product_preferences": "产品偏好",
    "resistances": "抗拒点",
    "inferred_tags": "推断标签",
    "usage_scenarios": "用车场景",
    "main_summary": "主摘要",
}


def _profile(obj):
    return obj.get("profile") if isinstance(obj, dict) else None


def _basic_notes_detail(obj):
    """全部 basic_notes 属性：parse_note 现映射 value + reasoning_summary。"""
    profile = _profile(obj) or {}
    bn = profile.get("basic_notes") or {}
    enum_map = obj.get("enum_map") or {}
    out = {}
    if isinstance(bn, dict):
        for key in bn:
            v = bn.get(key)
            if not isinstance(v, dict):
                continue
            out[key] = {
                "value": parse_note(bn, key, enum_map),
                "reasoning_summary": str(v.get("reasoning_summary", "") or ""),
            }
    return out


def _customer_overview_detail(obj):
    return (_profile(obj) or {}).get("customer_overview")


def _emotion_detail(obj):
    return (_profile(obj) or {}).get("emotion_state")


def _motivations_detail(obj):
    return (_profile(obj) or {}).get("purchase_motivations")


def _preferences_detail(obj):
    return (_profile(obj) or {}).get("product_preferences")


def _resistances_detail(obj):
    return (_profile(obj) or {}).get("resistances")


def _inferred_tags(obj):
    return (_profile(obj) or {}).get("inferred_tags")


def _usage_scenarios(obj):
    return (_profile(obj) or {}).get("usage_scenarios")


def _personality(obj):
    return (_profile(obj) or {}).get("main_summary")


# topic -> (取值函数, 依赖模块)
# 依赖模块用于 null 检测：该模块为 null/空时返回 data=null + "未生成" 提示
TOPICS = {
    "basic_notes_detail": (_basic_notes_detail, "basic_notes"),
    "customer_overview_detail": (_customer_overview_detail, "customer_overview"),
    "emotion_detail": (_emotion_detail, "emotion_state"),
    "motivations_detail": (_motivations_detail, "purchase_motivations"),
    "preferences_detail": (_preferences_detail, "product_preferences"),
    "resistances_detail": (_resistances_detail, "resistances"),
    "inferred_tags": (_inferred_tags, "inferred_tags"),
    "usage_scenarios": (_usage_scenarios, "usage_scenarios"),
    "personality": (_personality, "main_summary"),
}


def load_cache(phone):
    """读 /tmp/cp_detail_{phone}.json，检查 mtime TTL。

    返回 (obj, path)；缺失/过期/损坏返回 (None, None)。
    """
    path = os.path.join(CACHE_DIR, f"cp_detail_{phone}.json")
    if not os.path.exists(path):
        return None, None
    try:
        if time.time() - os.path.getmtime(path) > CACHE_TTL:
            return None, None  # 过期
    except OSError:
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except Exception:
        return None, None


def main():
    p = argparse.ArgumentParser(description="客户画像详情深挖切片")
    p.add_argument("--phone", required=True, help="客户完整手机号")
    p.add_argument("--topic", required=True, help="深挖主题（--topic help 列可用主题）")
    args = p.parse_args()

    if args.topic == "help":
        print(json.dumps({"ok": True, "available": sorted(TOPICS.keys())}, ensure_ascii=False))
        return

    if args.topic not in TOPICS:
        print(json.dumps({
            "ok": False,
            "error": "unknown_topic",
            "available": sorted(TOPICS.keys()),
        }, ensure_ascii=False))
        return

    obj, _path = load_cache(args.phone)
    if obj is None:
        print(json.dumps({
            "ok": False,
            "error": "cache_missing",
            "phone": args.phone,
            "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数",
        }, ensure_ascii=False))
        return

    fn, dep_module = TOPICS[args.topic]
    profile = _profile(obj) or {}
    if not profile.get(dep_module):
        label = MODULE_LABEL.get(dep_module, dep_module)
        print(json.dumps({
            "ok": True,
            "phone": args.phone,
            "topic": args.topic,
            "data": None,
            "message": f"该客户的{label}模块尚未生成",
        }, ensure_ascii=False))
        return

    data = fn(obj)
    print(json.dumps({
        "ok": True,
        "phone": args.phone,
        "topic": args.topic,
        "data": data,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_query_detail.py -v`
Expected: PASS（TestLoadCache 4 + TestTopics 9 + TestNullModule 1 + TestErrors 3 = 17 tests）

- [ ] **Step 5: 冒烟（--help + --topic help）**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 query_detail.py --phone 13912345678 --topic help`
Expected: `{"ok": true, "available": ["basic_notes_detail", "customer_overview_detail", "emotion_detail", "inferred_tags", "motivations_detail", "personality", "preferences_detail", "resistances_detail", "usage_scenarios"]}`

- [ ] **Step 6: 提交**

```bash
git add assets/skills/automotive/customer-profile-update/scripts/query_detail.py \
        assets/skills/automotive/customer-profile-update/scripts/tests/test_query_detail.py
git commit -m "feat(skill): customer-profile query_detail.py 深挖切片（9 主题 + TTL）

纯文件读免鉴权；mtime 超 10min 返 cache_missing；basic_notes 主题复用 parse_note
现映射；9 主题切片 + null 模块提示 + unknown_topic。Task 3/5。

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 4: SKILL.md 加「画像详情问答」节 + gotcha

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/SKILL.md`

**Interfaces:**
- Consumes: Task 1-3 的 detail.py / query_detail.py（CLI + stdout schema）
- Produces: SKILL.md 新增 Q&A 节（Q1-Q4 + 新鲜度规则 + 防幻觉）+ gotcha + frontmatter description 扩展

- [ ] **Step 1: 扩展 frontmatter description**

在 `SKILL.md` 顶部 frontmatter，把 `description:` 行改为（在原触发场景后追加 Q&A 触发）：

```
description: 当用户（车企销售顾问）请求查看或更新客户画像时使用。触发场景：销售说"查/看/调出XX画像""更新XX画像""XX的客户资料"，或收到【企微卡片按钮点击】回调消息（key 为 restart / select_ / page_next / page_prev / cancel 之一）时——本技能是这类按钮回调的唯一处理者，其他技能不应拦截。也支持在画像卡下发后追问画像详情（"成交概率多少/客户类型/情绪状态/有什么标签/用车场景/动机/抗拒点/雷达图/突破点/推理依据"）。不触发：纯闲聊、查电话号码本身、未提及具体客户。
```

- [ ] **Step 2: 在「## 工作流程」的步骤 6 之后插入「画像详情问答」节**

在 SKILL.md 中定位「### 步骤 6：输出」（画像卡输出）之后、「## 回调处理」之前，插入以下整节：

```markdown
## 画像详情问答（Q&A）

画像卡下发后（`last_action=showed_profile`），销售常会追问画像详情——"成交概率多少""客户类型是什么""情绪状态怎么样""有什么推断标签/用车场景""动机/抗拒点的推理依据""雷达图明细""突破点怎么来的"等。这类问题不再出卡，**调详情脚本取数后用纯文本/Markdown 作答**。

### 核心原则：90KB 画像不进会话历史，且画像可变需限定复用

`GET /profile/{phone}` 返回 ~90KB 完整画像（7 模块）。**绝不能每轮把全量喂进会话**。同时画像**可变**（销售可能点「更新画像」跳外部系统传素材重生成），与试驾报告（不可变）不同——故概要**不能无限复用**，需 10min TTL 限定：

- `detail.py`：取完整画像 + enum_map **存盘** `/tmp/cp_detail_{phone}.json`(0600)，stdout **只返 ~5-9KB 概要**（复用 extract_fields + 扩展 customer_overview/emotion_state/标签/场景）。**每次运行都从 API 现取**（只写缓存、不读缓存服务概要）。
- `query_detail.py`：概要答不了的深挖（reasoning_detail/雷达图/全部 basic_notes），按 `--topic` 从磁盘取**小切片**。读前查 mtime，**超 10min 返 `cache_missing`** → 触发重跑 `detail.py`。
- 完整 90KB 永不进会话历史；多轮问答历史只累积「概要 + 少量切片」。

### 新鲜度三层兜底

1. **TTL 限定复用（10min）**：概要带 `fetched_at`；query_detail 缓存 mtime 超 10min → `cache_missing` → 重跑 detail.py 刷新概要+缓存 → 重跑 query_detail。深挖路径自愈。
2. **`updated_at` + `fetched_at` 可见**：概要带画像 API 的 `updated_at`（若提供）+ 取数 `fetched_at`，销售可见"画像上次更新于 X，取数于 Y"。
3. **显式刷新意图**：销售说"刷新画像/取最新画像/画像是不是最新的" → 无视 TTL 直接重跑 `detail.py`。

> 跨会话由会话超时（10min 空闲清状态）自然解决——新会话首轮必跑 detail.py 现取。

### 状态跟踪

复用现有状态，新增 Q&A 态：

| 字段 | 说明 |
|------|------|
| `last_action` | 扩展 `in_profile_qa`（原有 `searched_list`/`showed_profile`/`null` 不变）|
| `current_customer` | 复用现有 `{customer_id, phone, name, level}`；`phone` 作 detail.py / query_detail.py 缓存 key |

> 画像卡只展示一个客户，Q&A 无多命中歧义——`current_customer.phone` 在 `showed_profile` 后已设值，直接用。

### 触发识别（AI 路由判断）

`last_action ∈ {showed_profile, in_profile_qa}` 时，收到新消息按意图判：

| 意图 | 信号 | 动作 |
|------|------|------|
| **追问** | 问画像内容（"成交概率/客户类型/情绪/标签/场景/动机/抗拒/雷达图/突破点/推理依据"）或指代词（"他/这个客户"）| 进 Q&A 流程 |
| **新检索** | 带新客户标识 + 查询意图（"查 5678 / 王总的画像"）| 跑 search.py，覆盖状态 |
| **取消/换一个** | "取消/换一个" | 清状态回搜索 |
| **刷新画像** | "刷新/取最新/是不是最新的"（`in_profile_qa` 态）| 重跑 detail.py（不换客户）|

### Q&A 工作流程

#### 步骤 Q1：定位 phone

- `last_action=in_profile_qa` → 复用 `current_customer.phone`，直接进步骤 Q2
- `last_action=showed_profile` → 直接用 `current_customer.phone`（画像卡展示后已设）

#### 步骤 Q2：取概要（仅首轮或概要不在历史或已陈旧时）

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/detail.py \
  --phone <完整手机号> [--customer-name <客户名>] > /tmp/cp_brief.json
```

stdout 概要结构见 `references/api-spec.md` §5。`brief` 含三大类高信号摘要（复用 extract_fields + customer_overview + emotion_state + 标签/场景）；`topics` 列可深挖主题；`hints.has_*` 标记模块是否生成。

- `ok:false` `error=auth_fail` → "系统暂时无法访问客户数据，请稍后重试或联系管理员。"
- `ok:false` `error=forbidden` → "该客户可能不归属您，无法查询。"
- `ok:false` `error=api_fail`/`timeout` → "系统繁忙，请稍后重试。"
- `ok:true` `has_profile=false` → "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。"（不进 Q&A）
- `ok:true` `has_profile=true` → 读 `brief` 作答，设 `last_action=in_profile_qa`

> **概要复用**：后续追问若概要已在会话历史里且够答且 `fetched_at` 在 10min 内，**直接复用作答，不重调 detail.py**。若 `fetched_at` 跨小时/隔天，重跑 detail.py 取最新。深挖时 query_detail 返 `cache_missing` 也触发重跑。

#### 步骤 Q3：深挖（概要不够时）

概要舍去了 reasoning_detail / evidence / radar_data 明细 / 全部 basic_notes 等深挖字段。问题涉及这些时，按主题取切片：

```bash
python3 {{profile_skills_dir}}/customer-profile-update/scripts/query_detail.py \
  --phone <完整手机号> --topic <topic>
```

主题清单见概要的 `topics` 字段或 `references/api-spec.md` §5（`basic_notes_detail`/`customer_overview_detail`/`emotion_detail`/`motivations_detail`/`preferences_detail`/`resistances_detail`/`inferred_tags`/`usage_scenarios`/`personality`）。`--topic help` 列全部。

- `ok:false` `error=cache_missing` → 详情缓存过期（10min TTL / pod 重启），重跑 `detail.py` 取概要后再答
- `ok:true` `data=null` → 该模块未生成，回文本"该客户的<模块>模块尚未生成"
- `ok:false` `error=unknown_topic` → 列可用主题，让销售重问
- `ok:true` → 读 `data` 切片作答

#### 步骤 Q4：作答（纯文本/Markdown）

- **输出形态**：纯文本/Markdown，可分点/加粗/列表。**不过 `validate_card.py`**，不输出卡片 JSON
- 数字 / 等级 / 概率**逐字引用脚本输出**（如"成交概率 85%"、"deal_level A"、"closeLevel A"）
- 多维问题分点作答（如"客户整体怎么样"→ 按 overall_tag + customer_type + closing_probability + emotion 分点）
- 引用 `profile_summary` / `reasoning_detail.summary` 等长文本时原样给出，不改写

### 防幻觉（Q&A 无校验器，最高优先级）

Q&A 轮不过 `validate_card.py`，防幻觉靠 grounding：

1. **只用脚本输出**：答案只能来自 `detail.py` 概要或 `query_detail.py` 切片。严禁用先验知识补全客户/画像信息
2. **概要不够就深挖**：不要凭概要里的摘要猜深挖细节——涉及 reasoning/evidence/雷达图/全部 basic_notes 就调 `query_detail.py` 取真实切片
3. **没有就说没有**：字段 null / 模块未生成（`hints.has_*=false` 或 `data=null`）→ 明说"该客户的 X 模块尚未生成"，**绝不编造**
4. **数字逐字**：概率/等级等数字必须与脚本输出一致，不四舍五入不臆测

### 允许使用的字段

- 概要：`brief.*`（复用 extract_fields + customer_overview + emotion_state + inferred_tags + usage_scenarios）+ `hints` + `topics` + `fetched_at` + `updated_at`
- 切片：`query_detail.py` 返回的 `data`（主题对应字段）
- 元信息：`phone`(脱敏) / `customer_name` / `update_url`

**严禁编造**完整手机号、report_url、分析字段值。Q&A 轮校验器兜不到，全靠你 grounding。
```

- [ ] **Step 3: 在「## Gotchas」节追加 Q&A 相关 gotcha**

在 SKILL.md 的 Gotchas 列表末尾（现有 11 条之后）追加：

```markdown
12. **Q&A 别把全量画像喂会话**：`detail.py` 已把 90KB 存盘只返概要。**不要**用 `execute_code` 自己调 `/profile/{phone}` 把全量读进上下文——会污染会话历史。深挖走 `query_detail.py` 取小切片。

13. **画像可变，概要不可无限复用**：与试驾报告（不可变）不同，画像可能被销售更新。概要带 `fetched_at`，**10min 内可复用**，超时或销售说"刷新"必须重跑 `detail.py`。query_detail 缓存 mtime 超 10min 会返 `cache_missing` 触发重取。

14. **`query_detail.py` 缓存会过期**：详情存 `/tmp/cp_detail_{phone}.json`（0600），10min TTL 或 pod 重启后过期。收到 `cache_missing` 别慌——重跑 `detail.py` 取概要（会重新存盘），再答。

15. **模块 null 不是"成交概率 0%"**：`hints.has_deal_intent=false` 或切片 `data=null` 表示**该模块未生成分析**，不是客户属性为 0。回"该客户的 X 模块尚未生成"，别编造"成交概率 0%"。

16. **Q&A 输出纯文本不是卡片**：追问轮**不过 `validate_card.py`**，不输出 `{"msgtype":...}` JSON。直接 Markdown 文本作答。卡片轮和问答轮输出形态不同，别混。

17. **Q&A 用 `current_customer.phone`，无多命中歧义**：画像卡只展示一个客户，`showed_profile` 后 `current_customer.phone` 已设。不像试驾报告可能多命中——直接用，不必从列表反查。
```

- [ ] **Step 4: 在「## References」节补详情脚本引用**

在 SKILL.md 的 References 表格末尾追加两行：

```markdown
| `references/api-spec.md` §5 | 查 detail.py / query_detail.py 输出 schema、深挖主题清单 |
| `scripts/detail.py` / `scripts/query_detail.py` | Q&A 取数 + 深挖切片脚本 |
```

- [ ] **Step 5: 提交**

```bash
git add assets/skills/automotive/customer-profile-update/SKILL.md
git commit -m "docs(skill): customer-profile SKILL.md 加画像详情问答节 + gotcha

Q1-Q4 流程 + 新鲜度三层兜底（10min TTL + fetched_at/updated_at 可见 + 显式刷新）
+ 防幻觉 grounding 四原则 + gotcha #12-#17。Task 4/5。

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 5: manifest.json + api-spec.md + profile-model.md

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/manifest.json`
- Modify: `assets/skills/automotive/customer-profile-update/references/api-spec.md`
- Modify: `assets/skills/automotive/customer-profile-update/references/profile-model.md`

**Interfaces:**
- Consumes: Task 1-3 的 detail.py / query_detail.py stdout schema
- Produces: manifest 版本 2.1.1；api-spec §5；profile-model Q&A 用法说明

- [ ] **Step 1: manifest.json 版本 + description**

修改 `assets/skills/automotive/customer-profile-update/manifest.json`：

- `"version": "2.1.0"` → `"version": "2.1.1"`
- `description` 末尾追加：「支持基于画像详情（成交意愿/情绪/标签/动机/抗性/雷达图/推理依据）的多轮问答，详情来自 GET /api/v1/remote/data/profile/{phone}。」

修改后完整 description：
```
"description": "车企销售顾问查询/更新客户画像。模糊检索客户→展示画像卡，更新画像跳转外部系统。支持基于画像详情（成交意愿/情绪/标签/动机/抗性/雷达图/推理依据）的多轮问答，详情来自 GET /api/v1/remote/data/profile/{phone}。"
```

- [ ] **Step 2: api-spec.md 加 §5**

在 `references/api-spec.md` 末尾追加（§4 search.py/profile.py 输出 之后）：

```markdown
---

## 5. `detail.py` / `query_detail.py` 结构化输出（详情问答用）

`detail.py` 调 `GET /profile/{phone}`（复用 profile.py 取数原语）+ 把完整结果存 `/tmp/cp_detail_{phone}.json`(0600)，stdout **只返回 ~5-9KB 概要**（避免 90KB 全量进入会话历史）。`query_detail.py` 从磁盘文件按主题取深挖切片。

> 画像可变：`detail.py` 每次运行都从 API 现取（只写不读缓存）；`query_detail.py` 读前查文件 mtime，超 10min 返 `cache_missing` 触发重取。

### detail.py（概要 + 存盘）

```bash
python3 detail.py --phone <完整手机号> [--customer-name <客户名>] > /tmp/cp_brief.json
```

```json
{"ok": true, "phone": "139****5678", "customer_name": "...", "update_url": "...",
 "fetched_at": "2026-07-06T10:00:00", "updated_at": "...",
 "stored_at": "/tmp/cp_detail_<phone>.json",
 "brief": {
   "deal_level": "A", "overall_tag": "...", "personality_summary": "...",
   "intended_model": "追光", "budget_range": "30-40万",
   "current_stage": "...", "breakthrough_point": "...",
   "motivations": "...", "preferences": "...", "resistances": "...",
   "profile_summary": "...",
   "closing_probability": "85%", "customer_type": "...", "business_opp_level": "...", "core_issue": "...",
   "emotion_current_state": "...", "brand_attitude": "...", "sales_attitude": "...",
   "inferred_tags": ["..."], "usage_scenarios": ["..."]
 },
 "topics": ["basic_notes_detail","customer_overview_detail","emotion_detail",
            "motivations_detail","preferences_detail","resistances_detail",
            "inferred_tags","usage_scenarios","personality"],
 "hints": {"has_main_summary": true, "has_basic_notes": true, "has_customer_overview": true,
           "has_emotion_state": true, "has_motivations": true, "has_preferences": true,
           "has_resistances": true, "has_inferred_tags": true, "has_usage_scenarios": true}}
{"ok": true, "has_profile": false, "phone": "...", "customer_name": "..."}
{"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}
```

| 字段 | 说明 |
|------|------|
| `brief` | 高信号摘要，答 ~80% 常见问题；复用 profile.py `extract_fields` + 扩展 customer_overview/emotion_state/标签/场景 |
| `topics` | `query_detail.py` 支持的深挖主题清单 |
| `hints.has_*` | 模块是否生成（null → 该模块问答答「未生成」）|
| `fetched_at` | 取数时间戳（ISO）；AI 据此判断概要是否需刷新（10min TTL）|
| `updated_at` | 画像 API 的 updated_at（若提供），销售可见画像上次更新时间 |
| `stored_at` | 完整结果磁盘路径（query_detail.py 按 phone 读，不直接用此路径）|

### query_detail.py（深挖切片）

```bash
python3 query_detail.py --phone <完整手机号> --topic <topic>
```

```json
{"ok": true, "phone": "...", "topic": "...", "data": <切片>}
{"ok": true, "phone": "...", "topic": "...", "data": null,
 "message": "该客户的<模块>模块尚未生成"}                      # 依赖模块 null
{"ok": false, "error": "cache_missing", "phone": "...",
 "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
{"ok": false, "error": "unknown_topic", "available": [...]}
```

**主题清单（`--topic`）：**

| 主题 | 内容 | 依赖模块 |
|------|------|---------|
| `basic_notes_detail` | 全部 basic_notes 属性（parse_note 映射 value + reasoning_summary）| basic_notes |
| `customer_overview_detail` | 完整 customer_overview + `*_reasoning` | customer_overview |
| `emotion_detail` | emotion_state + `radar_data`(items/max_score) + `*_reasoning` | emotion_state |
| `motivations_detail` | `purchase_motivations[]` + `reasoning_detail` | purchase_motivations |
| `preferences_detail` | `product_preferences[]` + `reasoning_detail` | product_preferences |
| `resistances_detail` | `resistances[]` + `severity` + `reasoning_detail` | resistances |
| `inferred_tags` | `[{title, desc}]` | inferred_tags |
| `usage_scenarios` | `[{title, desc}]` | usage_scenarios |
| `personality` | main_summary 完整（含 `profile_summary`）| main_summary |

> `--topic help` 列出全部可用主题。文件缺失/过期（mtime 超 10min / pod 重启）→ `cache_missing`，重跑 `detail.py` 即可。`basic_notes_detail` 复用 profile.py `parse_note` 做枚举映射（`replacement`→"换购" 等）。
```

- [ ] **Step 3: profile-model.md 顶部加 Q&A 用法说明**

在 `references/profile-model.md` 的「> **注意**」引用块之后，插入一段：

```markdown
> **Q&A 字段归属**：`detail.py` 概要含 `main_summary`(deal_level/overall_tag/personality_summary/profile_summary) + `basic_notes` 的 6 个高优字段（intended_model/budget_range 等，经 parse_note 映射）+ `customer_overview`(closing_probability/customer_type/business_opp_level/core_issue/current_stage/breakthrough_point) + `emotion_state`(current_state/brand_attitude/sales_attitude) + `inferred_tags`/`usage_scenarios` 的 title 列表 + 动机/偏好/抗性聚合名。`reasoning_detail`/evidence/`radar_data` 明细/全部 basic_notes 属性走 `query_detail.py` 深挖（见 `api-spec.md` §5）。
```

- [ ] **Step 4: 跑全部 skill 测试确认全绿**

Run: `cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v && python3 tests/test_query_detail.py -v && python3 tests/test_profile.py -v && python3 tests/test_search.py -v`
Expected: 全部 PASS（确认 detail/query_detail 新测试 + 现有 profile/search 测试均绿，profile.py 未被破坏）

- [ ] **Step 5: 提交**

```bash
git add assets/skills/automotive/customer-profile-update/manifest.json \
        assets/skills/automotive/customer-profile-update/references/api-spec.md \
        assets/skills/automotive/customer-profile-update/references/profile-model.md
git commit -m "feat(skill): customer-profile manifest v2.1.1 + api-spec §5 + profile-model Q&A 说明

版本 2.1.0→2.1.1；api-spec 加 detail.py/query_detail.py 输出 schema + 9 主题清单；
profile-model 加 Q&A 字段归属说明。Task 5/5。

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

## 完成后验证（全部任务结束后）

- [ ] **全量测试**：`cd assets/skills/automotive/customer-profile-update/scripts && python3 tests/test_detail.py -v && python3 tests/test_query_detail.py -v && python3 tests/test_profile.py -v && python3 tests/test_search.py -v && python3 tests/test_validate_card.py -v && python3 tests/test_build_card_fallback.py -v` 全绿
- [ ] **冒烟**：`python3 detail.py --help`、`python3 query_detail.py --phone 1 --topic help` 正常
- [ ] **profile.py 未改**：`git diff HEAD~5 -- assets/skills/automotive/customer-profile-update/scripts/profile.py` 应为空
- [ ] **版本号一致性**：`grep '"version"' manifest.json` 显示 `2.1.1`
