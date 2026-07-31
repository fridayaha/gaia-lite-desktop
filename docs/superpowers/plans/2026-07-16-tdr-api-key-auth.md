# 试驾报告 skill 加 X-API-Key 鉴权 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 test-drive-report skill 的 run.py + detail.py 加 X-API-Key 鉴权（sidecar 取 api_key + X-API-Key 头），参考 customer-profile-update 的实现。

**Architecture:** 新增 auth.py 共享 get_api_key()；run.py + detail.py import 它，API 调用加 X-API-Key 头；manifest 加 config_params；SKILL.md 加 auth_fail 错误。

**Tech Stack:** Python 3 stdlib，unittest，WeCom sidecar (localhost:8004)。

## Global Constraints

- skill 脚本只用 Python 标准库（无第三方依赖）
- 测试用 `python3 -m unittest`（独立于 make test）
- Conventional Commits 中文 + HAPI Co-Authored-By
- 提交到本地 develop 分支
- Working directory: `/home/ubuntu/union_agent`
- 测试前 cd 到 `assets/skills/automotive/test-drive-report/scripts`

---

### Task 1: 新增 auth.py + 测试

**Files:**
- Create: `assets/skills/automotive/test-drive-report/scripts/auth.py`
- Test: `assets/skills/automotive/test-drive-report/scripts/tests/test_auth.py`

**Interfaces:**
- Produces: `get_api_key(sidecar_url=SIDECAR_URL) -> str`（sidecar 不可达时 raise Exception）

- [ ] **Step 1: 写失败测试**

Create `tests/test_auth.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest tests.test_auth -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'auth'`）

- [ ] **Step 3: 实现 auth.py**

Create `auth.py`:

```python
#!/usr/bin/env python3
"""auth.py — 试驾报告系统 API Key 获取（经 sidecar 解密）

参考 customer-profile-update/scripts/profile.py 的 get_api_key 实现。
sidecar 解密 secrets.enc 中的 api_key，返回明文供 X-API-Key 头使用。
只用标准库。
"""
import json
import urllib.request

SIDECAR_URL = "http://localhost:8004/secret?skill=test-drive-report&key=api_key"


def get_api_key(sidecar_url=SIDECAR_URL):
    """从 sidecar 获取试驾报告系统 API Key。

    返回 api_key 字符串。sidecar 不可达/返回异常 → raise（调用方捕获后返 auth_fail）。
    """
    with urllib.request.urlopen(sidecar_url, timeout=5.0) as r:
        return json.loads(r.read().decode())["value"]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest tests.test_auth -v
```
Expected: 4 tests PASS

- [ ] **Step 5: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/test-drive-report/scripts/auth.py assets/skills/automotive/test-drive-report/scripts/tests/test_auth.py
git commit -m "feat(skill): 新增 tdr auth.py（get_api_key 经 sidecar 解密）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 2: run.py + detail.py 加 X-API-Key 鉴权

**Files:**
- Modify: `assets/skills/automotive/test-drive-report/scripts/run.py`（query_api + main）
- Modify: `assets/skills/automotive/test-drive-report/scripts/detail.py`（http_get_json + classify_error + fetch_detail + main）
- Test: `assets/skills/automotive/test-drive-report/scripts/tests/test_run.py`
- Test: `assets/skills/automotive/test-drive-report/scripts/tests/test_detail.py`

**Interfaces:**
- Consumes: `get_api_key` from Task 1's `auth.py`
- Produces: `run.py` main() calls get_api_key() → passes api_key to query_api(); `detail.py` main() calls get_api_key() → passes to fetch_detail()

- [ ] **Step 1: 写失败测试（test_run.py 加 auth_fail 分支）**

在 `tests/test_run.py` 中加：

```python
class TestAuthFail(unittest.TestCase):
    @patch("run.get_api_key", side_effect=Exception("sidecar down"))
    def test_sidecar_down_returns_auth_fail(self, mock_key):
        """sidecar 不可达 → {"ok":false,"error":"auth_fail"}。"""
        import run
        old = sys.argv
        sys.argv = ["run.py", "--sales-phone", "13800000000"]
        buf = io.StringIO()
        try:
            with patch("sys.stdout", buf):
                run.main()
        finally:
            sys.argv = old
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "auth_fail")
```

在 `tests/test_detail.py` 中加：

```python
class TestAuthFail(unittest.TestCase):
    @patch("detail.get_api_key", side_effect=Exception("sidecar down"))
    def test_sidecar_down_returns_auth_fail(self, mock_key):
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest tests.test_run.TestAuthFail tests.test_detail.TestAuthFail -v
```
Expected: FAIL（run.py/detail.py 没有 get_api_key import + main 不调 get_api_key）

- [ ] **Step 3: 修改 run.py**

3a. 在 run.py 的 import 段（`from build_card import parse_hour` 之后）加：

```python
from auth import get_api_key  # noqa: E402
```

3b. 修改 `query_api` 函数签名 + URL 调用（加 api_key 参数 + Request headers）：

将 `query_api` 的签名改为：
```python
def query_api(sales_phone, customer_name=None, customer_phone=None, drive_date=None, limit=20, api_key=None):
```

将 `url = f"{API_BASE}{API_PATH}?{urllib.parse.urlencode(params)}"` 之后的 urlopen 改为：
```python
        url = f"{API_BASE}{API_PATH}?{urllib.parse.urlencode(params)}"
        headers = {"X-API-Key": api_key} if api_key else {}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode())
```

3c. 修改 `main()`，在 `args = p.parse_args()` 之后、`query = {...}` 之前加：

```python
    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "query": {}}, ensure_ascii=False))
        return
```

3d. 修改 `query_api` 调用，传 `api_key=api_key`：

```python
    result = query_api(
        args.sales_phone, args.customer_name, args.customer_phone, args.drive_date, args.limit, api_key=api_key
    )
```

- [ ] **Step 4: 修改 detail.py**

4a. 在 detail.py 的 import 段（`from scratch import skill_scratch` 之后）加：

```python
from auth import get_api_key  # noqa: E402
```

4b. 修改 `http_get_json` 加 headers 参数：

```python
def http_get_json(url, headers=None, timeout=15.0):
    """GET 返回 (status, data)；网络异常返回 (None, None)。"""
    req = urllib.request.Request(url, headers=headers or {})
```

4c. 修改 `classify_error` 加 401→auth_fail：

```python
def classify_error(status):
    if status == 401:
        return "auth_fail"
    if status == 404:
        return "not_found"
    if status == 400:
        return "bad_request"
    if status is None:
        return "timeout"
    return "api_fail"
```

4d. 修改 `fetch_detail` 加 api_key 参数 + headers：

签名改为：
```python
def fetch_detail(test_drive_id, sales_phone=None, api_key=None):
```

在 `status, data = http_get_json(url)` 之前加 headers + 改调用：
```python
    headers = {"X-API-Key": api_key} if api_key else {}
    status, data = http_get_json(url, headers=headers)
```

4e. 修改 `main()`，在 `args = p.parse_args()` 之后加：

```python
    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "test_drive_id": args.test_drive_id}, ensure_ascii=False))
        return
```

4f. 修改 `fetch_detail` 调用，传 `api_key=api_key`：

```python
    result, err = fetch_detail(args.test_drive_id, args.sales_phone, api_key=api_key)
```

4g. 修改 detail.py docstring：去掉"当前接口无需鉴权（内网）"，改为"API Key 经 sidecar 解密（参考画像 skill），以 X-API-Key 头调用。"

- [ ] **Step 5: 跑测试确认通过**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest tests.test_run.TestAuthFail tests.test_detail.TestAuthFail -v
```
Expected: 2 tests PASS

- [ ] **Step 6: 跑全量测试确认无回归**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest discover -s tests -p "test_*.py"
```
Expected: ALL PASS

- [ ] **Step 7: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/test-drive-report/scripts/run.py assets/skills/automotive/test-drive-report/scripts/detail.py assets/skills/automotive/test-drive-report/scripts/tests/test_run.py assets/skills/automotive/test-drive-report/scripts/tests/test_detail.py
git commit -m "feat(skill): run.py + detail.py 加 X-API-Key 鉴权（sidecar 取 key + auth_fail）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 3: manifest + SKILL.md + 版本 + docstrings

**Files:**
- Modify: `assets/skills/automotive/test-drive-report/manifest.json`
- Modify: `assets/skills/automotive/test-drive-report/SKILL.md`
- Modify: `assets/skills/automotive/test-drive-report/scripts/run.py`（docstring）
- Modify: `assets/skills/automotive/test-drive-report/scripts/detail.py`（docstring，如 Step 4g 未做）

- [ ] **Step 1: manifest.json 加 config_params + 版本 bump**

将 `"config_params": []` 改为：
```json
"config_params": [
  {
    "name": "api_key",
    "label": "试驾报告系统 API Key",
    "type": "string",
    "secret": true,
    "description": "试驾报告系统鉴权密钥，通过 sidecar 解密后以 X-API-Key 头调用 API。"
  }
]
```

将 `"version": "1.2.0"` 改为 `"version": "1.3.0"`。

- [ ] **Step 2: SKILL.md 加 auth_fail 错误 + 去掉"无需鉴权"**

在 SKILL.md 的错误处理表（或 Gotcha #15 附近）加 auth_fail：
```markdown
| API Key 无效（HTTP 401） | "系统暂时无法访问试驾报告数据，请稍后重试或联系管理员。" | 纯文本 |
```

在 Gotcha #15（API 地址可配置）补一句：
```markdown
API Key 经 sidecar 解密（同画像 skill），以 X-API-Key 头调用，无需在 SKILL.md 中暴露。
```

- [ ] **Step 3: run.py docstring 去掉"无需鉴权"（如有）**

检查 run.py 顶部 docstring，如有"当前接口无需鉴权"或类似，改为"API Key 经 sidecar 解密，以 X-API-Key 头调用"。

- [ ] **Step 4: 跑全量测试**

```bash
cd assets/skills/automotive/test-drive-report/scripts && python3 -m unittest discover -s tests -p "test_*.py"
```
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/test-drive-report/manifest.json assets/skills/automotive/test-drive-report/SKILL.md assets/skills/automotive/test-drive-report/scripts/run.py assets/skills/automotive/test-drive-report/scripts/detail.py
git commit -m "docs(skill): tdr manifest config_params + SKILL.md auth_fail + v1.3.0

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 4: 热补丁 + 验证

**Files:** 无新改动（部署 + 验证）

- [ ] **Step 1: 热补丁到 k3s**

```bash
DEF=794e042f-ee92-4478-96af-62dcf1a6abcb
POD=$(ssh ua-cloud 'kubectl -n unionagents get pods -l app=engine-hermes-adf57637 -o jsonpath="{.items[0].metadata.name}"')
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='secrets.enc' -C assets/skills/automotive/test-drive-report . | ssh ua-cloud "kubectl -n unionagents exec -i $POD -c engine -- tar xzf - --no-same-owner -C /opt/data/skills/$DEF/test-drive-report/"
```

- [ ] **Step 2: 验证 run.py + detail.py 带 auth 调 API**

发一条试驾报告查询，确认正常返回（如果 tdr 的 api_key 已在管理台配置 + secrets.enc 已生成）。如果 api_key 未配置 → auth_fail（预期，需先在管理台配置）。

- [ ] **Step 3: 推送到 origin/develop**

```bash
cd /home/ubuntu/union_agent
git push origin develop
```

---

## Self-Review

**1. Spec coverage:**
- auth.py get_api_key：Task 1 ✓
- run.py 加 headers + auth_fail：Task 2 ✓
- detail.py 加 headers + classify_error 401 + auth_fail：Task 2 ✓
- manifest config_params：Task 3 ✓
- SKILL.md auth_fail + 去掉"无需鉴权"：Task 3 ✓
- 测试：Task 1 (test_auth) + Task 2 (test_run/test_detail auth_fail) ✓
- 版本 1.2.0→1.3.0：Task 3 ✓
- 部署验证：Task 4 ✓

**2. Placeholder scan:** 无 TBD/TODO。所有步骤含完整代码。✓

**3. Type consistency:** `get_api_key() -> str`（Task 1）→ run.py main() 调用（Task 2）→ detail.py main() 调用（Task 2）。`query_api(..., api_key=None)` + `fetch_detail(..., api_key=None)` + `http_get_json(url, headers=None, timeout)` 签名一致。✓
