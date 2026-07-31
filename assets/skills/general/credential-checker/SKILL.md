---
name: credential-checker
description: 安全凭证自检技能 — 经 sidecar 解密 secret 验证凭证使用（路径 B）。仅用于凭证机制验证。
version: 1.4.0
author: UnionAgents
---

# Credential Checker（安全凭证自检）

验证 UnionAgents 安全凭证机制（路径 B：sidecar 解密）：secret 加密存 DB + 密文落 Pod（secrets.enc），引擎 Pod 内 sidecar 容器持 key 解密，skill 用 execute_code 调 sidecar（localhost）拿明文，直接调外部 API（不经 egress）。

## 何时触发

当用户说「credential check」「验证凭证」「检查安全凭证」等意图时触发。

## ⚠️ 关键：从 sidecar 拿明文，不读 env，不调 egress，不用默认值

- hermes execute_code 沙箱**隔离 Pod env**（读不到 CREDENTIAL_PROXY_URL/API_SERVER_KEY）
- secret **密文**落 Pod 文件（`skills/{name}/secrets.enc`），sidecar 容器持 `CREDENTIAL_ENCRYPTION_KEY` 解密
- skill 用 execute_code 调 **localhost:8004/secret** 拿明文，再用明文调外部 API

**禁止**：用 `os.environ` 读、调 egress、用默认值、在不执行时回答"未检测到"。

## 验证步骤

用 `execute_code` 执行以下脚本（请求用户批准）：

```python
import urllib.request, json

# 1. 从 sidecar 拿解密后的 secret（localhost，不经 egress，不读 env）
r = urllib.request.urlopen(
    "http://localhost:8004/secret?skill=credential-checker&key=api_key", timeout=5)
api_key = json.loads(r.read())["value"]

# 2. 用明文 secret 调外部 API（httpbin 回显 Authorization）
req = urllib.request.Request(
    "https://httpbin.org/anything",
    data=json.dumps({"probe": "credential-checker"}).encode("utf-8"),
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    method="POST",
)
resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
auth = resp.get("headers", {}).get("Authorization", "")
print("Authorization 注入:", "✓ 成功" if auth else "✗ 失败")
print("scheme:", auth.split(" ")[0] if auth else "<无>")
print("值(脱敏):", (auth[:10] + "***" + auth[-4:]) if len(auth) > 14 else auth)
```

### 逐项验证多凭证

把 `key=api_key` 分别改成 `api_secret`、`webhook_token` 各执行一次，确认三个 secret 均已配置且能解密。

## 结果解读

| 现象 | 结论 |
|------|------|
| `Authorization 注入: ✓ 成功` | 凭证机制生效 ✅ |
| sidecar 404 `no secrets for skill` | secrets.enc 未落盘（fan-out 未执行，重装 skill 触发） |
| sidecar 500 `decrypt failed` | sidecar 的 `CREDENTIAL_ENCRYPTION_KEY` 与 manager 不一致 |
| sidecar 404 `secret ... not configured` | 该参数未在 console 配置 |
| execute_code 连不上 localhost:8004 | sidecar 容器未起（引擎 Pod 未重建带 sidecar） |

## 报告规范

- **严禁**输出 secret 明文；脚本只 print 脱敏的 Authorization
- 若 execute_code 未被批准，告知用户需批准执行才能验证
