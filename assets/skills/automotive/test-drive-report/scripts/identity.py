#!/usr/bin/env python3
"""identity.py — 从平台 user-context 端点读取当前销售的业务手机号作为 sales_phone。

sales_phone 必须是平台绑定的**业务手机号**（BusinessUserBinding.business_phone），
由 manager ``GET /api/controller/profiles/{profile_name}/user-context`` 端点返回
（``business.业务手机号``）。run.py / detail.py 自动读取，**不接受对话/CLI 传入**——
使用者即销售顾问本人，避免越权（用他人手机号查询他人报告）。

profile_name 从 ``HERMES_HOME`` 路径末段解析（与 current-user-info skill 同源）；
``CONTROLLER_URL`` + ``UA_INTERNAL_TOKEN`` 从 pod env 读（fallback profile .env）。
只用标准库。
"""
import json
import os
import urllib.parse
import urllib.request


def _load_env() -> tuple[str, str]:
    """读 CONTROLLER_URL + UA_INTERNAL_TOKEN（pod env，fallback profile .env）。"""
    controller_url = os.environ.get("CONTROLLER_URL", "")
    internal_token = os.environ.get("UA_INTERNAL_TOKEN", "")
    if controller_url and internal_token:
        return controller_url, internal_token
    for env_path in [
        os.path.join(os.environ.get("HERMES_HOME", ""), ".env"),
        os.path.expanduser("~/.hermes/.env"),
    ]:
        if not env_path or not os.path.isfile(env_path):
            continue
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "CONTROLLER_URL" and not controller_url:
                        controller_url = v
                    elif k == "UA_INTERNAL_TOKEN" and not internal_token:
                        internal_token = v
        except OSError:
            continue
        if controller_url and internal_token:
            break
    return controller_url, internal_token


def _resolve_profile_name() -> str:
    """从 HERMES_HOME 路径末段解析 profile_name（fallback cwd）。"""
    home = os.environ.get("HERMES_HOME", "").rstrip("/")
    if home:
        return os.path.basename(home)
    return os.path.basename(os.getcwd().rstrip("/"))


def read_sales_phone() -> str | None:
    """从 manager user-context 端点读取业务手机号作为 sales_phone。

    Returns:
        业务手机号字符串（原值，不限定数字，可为字母等业务标识）；端点不可达 /
        非 200 / 无 ``business.业务手机号`` / 值为空 → None（调用方按平台故障处理）。
    """
    profile_name = _resolve_profile_name()
    if not profile_name:
        return None
    controller_url, internal_token = _load_env()
    if not controller_url:
        controller_url = "http://manager:8002"
    controller_url = controller_url.rstrip("/")
    quoted = urllib.parse.quote(profile_name, safe="")
    url = f"{controller_url}/api/controller/profiles/{quoted}/user-context"
    headers = {"Accept": "application/json"}
    if internal_token:
        headers["X-Internal-Token"] = internal_token
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    business = data.get("business") if isinstance(data, dict) else None
    if not isinstance(business, dict):
        return None
    val = business.get("业务手机号")
    if not val:
        return None
    return str(val).strip() or None
