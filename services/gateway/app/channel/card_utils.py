"""卡片 JSON 提取工具——中性 JSON 工具，供渠道 adapter 复用。

当前仅企微（WeComAdapter / dispatcher 卡片点击路径）使用：模型回复可能在
卡片 JSON 前后夹带说明文字或 ```json 代码围栏，且一条回复可能含**多个**卡片
JSON。本工具用字符串感知的括号配平从回复中逐个捞出含指定 key 的 JSON 对象，
配合调用方循环发送，使「文本 + 多卡片 JSON」共存于一条回复时每个卡片都能正确
渲染。

不修改 JSON 内容，只做提取；不绑定任何具体渠道语义。
"""

from __future__ import annotations

import json


def extract_card_json(content: str, required_key: str = "msgtype") -> tuple[dict | None, str, str]:
    """从 ``content`` 中提取**第一个**含 ``required_key`` 的 JSON 对象。

    返回 ``(obj, before, after)``：

    - 命中：``obj`` 为解析出的 dict；``before`` 为该 JSON 之前的文本（前导说明文字
      /围栏）；``after`` 为之后的文本（可能还含更多卡片 JSON，调用方可继续递归提取）。
    - 未命中：``(None, content, "")`` 原样返回（``before`` 为全文，``after`` 为空）。

    保留 before/after 位置信息，便于调用方按原文顺序逐个发送「前导文本 → 卡片 →
    后续文本/卡片」。

    扫描每个 ``{`` 起点做**字符串感知的括号配平**（识别 ``"..."`` 字符串与 ``\\"``
    转义，不计字符串内的括号），定位配平的 ``}`` 后 ``json.loads`` 该子串；成功且
    为 dict 且含 ``required_key`` → 命中。否则从下一个 ``{`` 继续——从而容忍 prose
    里的 ``{示例}``、``{a:1}`` 等非法/无关键片段被跳过。

    天然覆盖：纯 JSON、前导/尾部说明文字、```json`` 代码围栏（反引号非括号非引号，
    被扫描器忽略）。误判风险极低（需 prose 恰好含一个合法 JSON 对象且带
    ``required_key``）。

    容错：配平失败（AI 重新输出 JSON 时丢尾部 ``}``）时，若片段含 ``required_key``，
    尝试逐个补 ``}`` 直到 ``json.loads`` 成功——修复后仍需通过调用方的
    ``CARD_MSGTYPES`` 校验才发卡片，误判风险极低。
    """
    if not content:
        return None, content or "", ""

    s = content
    i = 0
    n = len(s)
    while True:
        brace = s.find("{", i)
        if brace < 0:
            return None, content, ""
        depth = 0
        in_str = False
        esc = False
        end = -1
        j = brace
        while j < n:
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end < 0:
            # 配平失败：可能是 AI 重新输出 JSON 时丢了尾部 ``}``。
            # 尝试补全 ``}`` 修复（仅当片段含 required_key 时，避免对普通 prose 误判）。
            candidate = s[brace:]
            if required_key in candidate:
                for extra in range(1, 6):
                    try:
                        obj = json.loads(candidate + "}" * extra)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(obj, dict) and required_key in obj:
                        return obj, content[:brace], ""
            # 补全失败或不含 required_key：原样返回（后续调用方按纯文本处理）
            return None, content, ""
        candidate = s[brace : end + 1]
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict) and required_key in obj:
            return obj, content[:brace], content[end + 1 :]
        # 此起点配平了但不是含 required_key 的 dict JSON，找下一个 '{'
        i = brace + 1
