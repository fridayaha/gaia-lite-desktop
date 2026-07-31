# 试驾报告 skill 加 X-API-Key 鉴权设计

**日期**：2026-07-15
**Skill**：test-drive-report
**版本**：1.2.0 → 1.3.0

## 背景

`test-drive-report` 的 `run.py` + `detail.py` 调试驾报告 API（`TEST_DRIVE_API_BASE`，默认 `http://118.145.238.55:5000`）时**无鉴权**（docstring 写"当前接口无需鉴权（内网）"）。现需加 `X-API-Key` 头鉴权，参考 `customer-profile-update` 的实现方式（sidecar 取 api_key + X-API-Key 头 + manifest config_params）。

## 参考（cp 画像 skill 的鉴权模式）

- `profile.py` 有 `get_api_key()` → 调 sidecar `http://localhost:8004/secret?skill=customer-profile-update&key=api_key` → 返回 api_key。
- API 调用带 `headers={"X-API-Key": api_key}`。
- `classify_error`：401→`auth_fail`，403→`forbidden`。
- sidecar 失败 → `{"ok":false,"error":"auth_fail"}`。
- manifest `config_params: [{"name":"api_key","secret":true,...}]`。

## 设计

### 1. 新增 `scripts/auth.py`（共享鉴权模块）

```python
SIDECAR_URL = "http://localhost:8004/secret?skill=test-drive-report&key=api_key"

def get_api_key(sidecar_url=SIDECAR_URL):
    """从 sidecar 获取试驾报告系统 API Key。失败 raise → 调用方返 auth_fail。"""
    # urllib → JSON → ["value"]（同 cp 的 profile.py get_api_key）
```

只用标准库。run.py + detail.py import 它。

### 2. `run.py` 改动

- `from auth import get_api_key`
- `http_get_json(url, timeout)` → `http_get_json(url, headers=None, timeout)` 加 headers 参数，`urllib.request.Request(url, headers=headers or {})`
- `main()` 启动时 `api_key = get_api_key()`（try/except → `{"ok":false,"error":"auth_fail"}`）
- API 调用传 `headers={"X-API-Key": api_key}`
- `classify_error`：加 `if status == 401: return "auth_fail"`

### 3. `detail.py` 改动

同 run.py：import get_api_key + http_get_json 加 headers + main() 调 get_api_key + classify_error 加 401→auth_fail + sidecar 失败→auth_fail。

### 4. `manifest.json`

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

### 5. `SKILL.md`

- 错误表加 `auth_fail` → "系统暂时无法访问试驾报告数据，请稍后重试或联系管理员。"
- 去掉"当前接口无需鉴权（内网）"的说明（run.py + detail.py docstring 同步）。
- Gotcha #15（API 地址可配置）补一句"API Key 经 sidecar 解密，同画像 skill"。

### 6. 测试

- `test_auth.py`（新增）：mock sidecar → get_api_key 成功（返回 key）/ 失败（sidecar down → raise）。
- `test_run.py`：加 auth_fail 分支（sidecar down → `{"ok":false,"error":"auth_fail"}`）+ API 401 → auth_fail。
- `test_detail.py`：同 test_run.py（auth_fail 分支）。

### 7. 版本

`manifest.json`：`1.2.0` → `1.3.0`。

## 不改

- `query_detail.py`（读缓存，无 API 调用，不需 auth）。
- `build_card.py` / `validate_card.py`（不调 API）。
- `references/`（api-spec.md 可选补 auth 说明，非必须）。

## 部署

- 纯 skill 侧，走 skill-sync。
- 部署后需在管理台给 test-drive-report skill 配置 api_key（config_params 新增了 api_key，平台会创建 secrets.enc）。
- 热补丁到 k3s 验证：run.py + detail.py 带 X-API-Key 调 API 正常返回。
