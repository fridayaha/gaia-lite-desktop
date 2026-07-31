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
