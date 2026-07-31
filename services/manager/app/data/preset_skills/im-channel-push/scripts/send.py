#!/usr/bin/env python3
"""IM 通道消息推送脚本（出站）。

由 LLM 通过 terminal 工具执行，调 gateway send 端点下发 IM 消息。
固定脚本，LLM 只传参数（不自行生成 HTTP 代码）。

用法：
    python3 send.py --touser LiuWei --msgtype markdown --content "日报内容..."
    python3 send.py --touser LiuWei --msgtype text --content "简短通知"
    python3 send.py --touser LiuWei --msgtype template_card --content '{"template_card":{...}}'

环境变量（engine pod 已注入，无需 LLM 提供）：
    AGENT_ID        智能体 ID（gateway send 端点路径用）
    API_SERVER_KEY  gateway 鉴权 key（= gateway api_server_key，Bearer）
    GATEWAY_URL     gateway 地址，默认 http://gateway.unionagents:8010

退出码：0 成功；1 参数/env 缺失；2 gateway 返回非 ok；3 网络/HTTP 错误。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="IM 通道消息推送（出站，调 gateway send）")
    parser.add_argument("--touser", required=True, help="接收方 IM user_id（单聊）")
    parser.add_argument(
        "--channel-type", default="wecom",
        help="IM 通道类型：wecom（默认）/ dingtalk / feishu 等",
    )
    parser.add_argument(
        "--msgtype", default="markdown",
        choices=["markdown", "text", "template_card"],
        help="消息类型，默认 markdown",
    )
    parser.add_argument("--content", required=True, help="消息内容（template_card 时为 JSON 字符串）")
    parser.add_argument("--chat-id", default="", help="暂未支持（传入返回 501）；当前仅支持单聊 --touser")
    args = parser.parse_args()

    agent_id = os.environ.get("AGENT_ID", "")
    api_key = os.environ.get("API_SERVER_KEY", "")
    gateway_url = os.environ.get("GATEWAY_URL", "http://gateway.unionagents:8010").rstrip("/")

    # hermes terminal 工具不一定继承 pod env，从 profile .env 文件 fallback 读
    if not agent_id or not api_key:
        for env_path in [
            os.path.join(os.environ.get("HERMES_HOME", ""), ".env"),
            os.path.expanduser("~/.hermes/.env"),
        ]:
            if not os.path.isfile(env_path):
                continue
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "AGENT_ID" and not agent_id:
                    agent_id = v
                elif k == "API_SERVER_KEY" and not api_key:
                    api_key = v
                elif k == "GATEWAY_URL" and gateway_url == "http://gateway.unionagents:8010":
                    gateway_url = v.rstrip("/")
            if agent_id and api_key:
                break

    if not agent_id:
        print("ERROR: AGENT_ID env not set", file=sys.stderr)
        return 1
    if not api_key:
        print("ERROR: API_SERVER_KEY env not set", file=sys.stderr)
        return 1

    chat_id = args.chat_id or args.touser
    content = args.content
    if args.msgtype in ("text", "markdown"):
        # LLM 传字面 \n（反斜杠n），转成实际换行符，避免 IM 显示成 "nn"
        content = content.replace("\\n", "\n")
    body = json.dumps({
        "touser": args.touser,
        "chat_id": args.chat_id,
        "msgtype": args.msgtype,
        "content": content,
    }).encode("utf-8")

    url = f"{gateway_url}/api/gateway/channel/{args.channel_type}/{agent_id}/send"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: gateway HTTP {e.code}: {err}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: request failed: {e}", file=sys.stderr)
        return 3

    try:
        result = json.loads(resp_body)
    except json.JSONDecodeError:
        print(f"ERROR: gateway non-json response: {resp_body}", file=sys.stderr)
        return 2

    if result.get("ok"):
        print(json.dumps({"ok": True, "chat_id": chat_id, "msgtype": args.msgtype}, ensure_ascii=False))
        return 0
    print(f"ERROR: gateway returned ok=false: {result}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
