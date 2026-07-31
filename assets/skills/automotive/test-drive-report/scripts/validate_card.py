#!/usr/bin/env python3
"""
validate_card.py — AI 卡片草稿校验 + 消毒 + 兜底

AI 自主建卡后，把草稿交给本脚本校验。stdin 读 run.py 的结构化输出
（取 items），--card-json 读 AI 的卡片草稿。校验策略：**消毒优先于兜底**，
尽量保住 AI 的布局意图；只有结构错误或数据幻觉才弃稿回退。

  python3 run.py ...   # run.py 自动 tee 写 tdr.json（sales_phone 自动从平台 user-context 端点读业务手机号）
  python3 validate_card.py --card-json '<AI 草稿>' < "$HERMES_HOME/.skill_tmp/tdr.json"

stdout = 最终卡片 JSON（消毒/兜底后）或纯文本（0 命中 / 兜底也无数据）。
AI 原样透传 stdout 即可。

校验规则：
  - 草稿解析失败 / 非 dict / msgtype != template_card / card_type != text_notice → 兜底
  - 数据真实性：jump_list / card_action / horizontal_content_list 里的 url 必须来自
    items 的 report_url 集合 → 否则兜底（幻觉，不修正）
  - 字段超限：horizontal_content_list >6、jump_list >3、keyname >5字、value >26字 → 截断
  - 缺 card_action（text_notice 必填）→ 注入 items[0].report_url
  - 缺 main_title 且缺 sub_title_text → 注入默认 title
  - items 为空 → 输出 0-hit 文本（忽略草稿）

只用 Python 标准库，无第三方依赖。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_card import build_fallback_card  # noqa: E402

NO_HIT_TEXT = (
    "没找到匹配的试驾报告——可能是试驾还没完成（报告生成要几分钟），"
    "或者这位客户不归您名下。要不要换个日期或手机号尾号再查？"
)
FAIL_TEXT = "试驾报告查询失败，请稍后重试或联系管理员。"

ALLOWED_CARD_TYPE = "text_notice"
MAX_HCL = 6          # horizontal_content_list 项数上限
MAX_JUMP = 3         # jump_list 项数上限
MAX_KEYNAME = 5      # keyname 字数（建议值，企软约束）
MAX_VALUE = 26       # value 字数


def _collect_urls(card):
    """从卡片各位置收集所有 url，用于数据真实性校验。"""
    tc = card.get("template_card", {})
    urls = []
    for item in tc.get("horizontal_content_list", []) or []:
        if item.get("type") == 1 and item.get("url"):
            urls.append(item["url"])
    for item in tc.get("jump_list", []) or []:
        if item.get("type") == 1 and item.get("url"):
            urls.append(item["url"])
    ca = tc.get("card_action")
    if ca and ca.get("type") == 1 and ca.get("url"):
        urls.append(ca["url"])
    return urls


def _sanitize(card, items):
    """就地消毒：截断超限字段、补缺必填项。返回 True 表示可输出，False 表示必须兜底。"""
    tc = card.get("template_card")
    if not isinstance(tc, dict) or tc.get("card_type") != ALLOWED_CARD_TYPE:
        return False

    # 数据真实性：所有 url 必须来自 items 的 report_url
    valid_urls = {it.get("report_url") for it in items if it.get("report_url")}
    if not valid_urls:
        # 没有 items 却带了卡片 url —— 走不到这（items 空已在上层处理）
        return False
    for u in _collect_urls(card):
        if u not in valid_urls:
            return False  # 幻觉 url，弃稿

    # horizontal_content_list 截断到 6，keyname/value 截断
    hcl = tc.get("horizontal_content_list")
    if isinstance(hcl, list):
        if len(hcl) > MAX_HCL:
            hcl = hcl[:MAX_HCL]
        for item in hcl:
            if isinstance(item, dict):
                if isinstance(item.get("keyname"), str) and len(item["keyname"]) > MAX_KEYNAME:
                    item["keyname"] = item["keyname"][:MAX_KEYNAME]
                if isinstance(item.get("value"), str) and len(item["value"]) > MAX_VALUE:
                    item["value"] = item["value"][:MAX_VALUE]
        tc["horizontal_content_list"] = hcl

    # jump_list 截断到 3
    jl = tc.get("jump_list")
    if isinstance(jl, list) and len(jl) > MAX_JUMP:
        tc["jump_list"] = jl[:MAX_JUMP]

    # card_action 必填（text_notice）——缺失则注入第一个 report_url
    ca = tc.get("card_action")
    if not isinstance(ca, dict) or ca.get("type") != 1 or not ca.get("url"):
        tc["card_action"] = {"type": 1, "url": items[0]["report_url"]}

    # main_title 或 sub_title_text 至少一项
    if not tc.get("main_title") and not tc.get("sub_title_text"):
        tc["main_title"] = {"title": "🚗 试驾报告"}

    card["template_card"] = tc
    return True


def main():
    p = argparse.ArgumentParser(description="AI 卡片草稿校验 + 兜底")
    p.add_argument("--card-json", required=True, help="AI 手写的 template_card 草稿 JSON")
    args = p.parse_args()

    # 读 stdin（run.py 结构化输出）
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        print(FAIL_TEXT)
        return

    items = data.get("items", []) if isinstance(data, dict) else []

    # 0 命中 / 取数失败：忽略草稿，输出文本
    if not data.get("ok", False):
        print(FAIL_TEXT)
        return
    if not items:
        print(NO_HIT_TEXT)
        return

    # 解析草稿
    try:
        draft = json.loads(args.card_json)
    except Exception:
        draft = None

    if isinstance(draft, dict) and _sanitize(draft, items):
        print(json.dumps(draft, ensure_ascii=False))
        return

    # 兜底：从真实 items 建卡
    card = build_fallback_card(items)
    if card is None:
        print(NO_HIT_TEXT)
        return
    print(json.dumps(card, ensure_ascii=False))


if __name__ == "__main__":
    main()
