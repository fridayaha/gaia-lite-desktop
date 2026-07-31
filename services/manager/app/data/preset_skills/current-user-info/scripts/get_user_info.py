#!/usr/bin/env python3
"""当前用户信息查询脚本（只读 pull）。

由 LLM 通过 terminal 工具执行，调 manager 只读端点获取当前用户的最新基本信息。
固定脚本，LLM 不传参数（不自行生成 HTTP 代码）。

当前用户身份由 profile_name 标识：从 HERMES_HOME 路径末段解析（profile_name 始终
含 user_id，见 gateway profile_resolver._build_profile_name），manager 端点按
profile_name 反查 agent_profiles → user_id → users。

用法：
    python3 get_user_info.py

环境变量（engine pod 已注入，无需 LLM 提供）：
    HERMES_HOME      profile 目录，末段即 profile_name
    CONTROLLER_URL   manager 地址，默认 http://manager:8002
    UA_INTERNAL_TOKEN manager 内部令牌（X-Internal-Token 鉴权）

退出码：0 成功；1 env/profile_name 缺失；2 manager 返回非 2xx；3 网络/HTTP 错误。
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _load_env_fallback() -> tuple[str, str]:
    """terminal 工具不一定继承 pod env，从 profile .env 文件 fallback 读。

    返回 (controller_url, internal_token)。仿 im-channel-push send.py 的 .env fallback。
    """
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
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "CONTROLLER_URL" and not controller_url:
                    controller_url = v
                elif k == "UA_INTERNAL_TOKEN" and not internal_token:
                    internal_token = v
        except Exception:
            continue
        if controller_url and internal_token:
            break

    return controller_url, internal_token


def _resolve_profile_name() -> str:
    """从 HERMES_HOME 路径末段解析 profile_name。"""
    home = os.environ.get("HERMES_HOME", "").rstrip("/")
    if home:
        return os.path.basename(home)
    # fallback：当前工作目录（profile_isolation 以 profile 目录为 HOME/CWD 启动 gateway）
    return os.path.basename(os.getcwd().rstrip("/"))


def main() -> int:
    profile_name = _resolve_profile_name()
    if not profile_name:
        print("ERROR: cannot resolve profile_name (HERMES_HOME unset)", file=sys.stderr)
        return 1

    controller_url, internal_token = _load_env_fallback()
    if not controller_url:
        controller_url = "http://manager:8002"
    controller_url = controller_url.rstrip("/")

    quoted = urllib.parse.quote(profile_name, safe="")
    url = f"{controller_url}/api/controller/profiles/{quoted}/user-context"
    headers = {"Accept": "application/json"}
    if internal_token:
        headers["X-Internal-Token"] = internal_token

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: manager HTTP {e.code}: {err}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: request failed: {e}", file=sys.stderr)
        return 3

    # 原样透传 manager 返回的 JSON（已是 {fields, business} 结构）
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, ensure_ascii=False))
        return 0
    except json.JSONDecodeError:
        print(f"ERROR: manager non-json response: {body}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
