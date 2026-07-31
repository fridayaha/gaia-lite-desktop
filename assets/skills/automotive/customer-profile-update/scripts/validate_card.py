#!/usr/bin/env python3
"""
validate_card.py — AI 卡片草稿校验 + 消毒 + 兜底（选择卡 button_interaction / 画像卡 text_notice）

AI 自主建卡后，把草稿交本脚本校验。stdin 读 search.py 或 profile.py 的结构化
输出，--card-json 读 AI 草稿。按上下文（选择卡 / 画像卡）校验：**消毒优先于兜底**，
只有结构错误或数据幻觉才弃稿回退。

  python3 search.py --customer-name-keyword 王 > "$HERMES_HOME/.skill_tmp/cp.json"
  python3 validate_card.py --card-json '<选择卡草稿>' < "$HERMES_HOME/.skill_tmp/cp.json"

  python3 profile.py --phone 139... --customer-name 客户5678 > "$HERMES_HOME/.skill_tmp/cp.json"
  python3 validate_card.py --card-json '<画像卡草稿>' < "$HERMES_HOME/.skill_tmp/cp.json"

stdout = 最终卡片 JSON（消毒/兜底后）或纯文本（0 命中/失败）。AI 原样透传。

校验规则：
  - 通用：草稿解析失败/非 dict/msgtype!=template_card → 兜底
  - 选择卡（button_interaction）：button_list[].key 唯一；select_{id} 必须对应 items 真实 id；
            control key 放行；缺 task_id → 注入；超 6 截断
  - 画像卡（text_notice）：jump_list/card_action 的 type:1 url 必须 == stdin 的 update_url；
            缺 card_action → 注入；button_list/task_id 删除；hcl 超 6/字数超限截断
  - 0 命中/取数失败/无画像 → 输出固定话术文本（忽略草稿）

只用标准库。
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_card import build_selection_card, build_profile_card  # noqa: E402

ALLOWED_CARD_TYPE = "button_interaction"
MAX_BUTTON = 6
MAX_HCL = 6
MAX_KEYNAME = 5
MAX_VALUE = 26
CONTROL_KEYS = {"page_next", "page_prev", "restart", "cancel"}

NO_HIT_TEXT = "未找到匹配的客户。请确认手机号或姓名，或确认该客户是否归属您。"
NO_PROFILE_TEXT = "暂未找到该客户的画像记录。可能尚未上传过素材，请前往画像系统上传素材生成画像。"
FAIL_TEXTS = {
    "auth_fail": "系统暂时无法访问客户数据，请稍后重试或联系管理员。",
    "forbidden": "该客户可能不归属您，无法查询。",
    "api_fail": "系统繁忙，请稍后重试。",
    "timeout": "系统繁忙，请稍后重试。",
}


def _sanitize_button(btn):
    """单个 button 字段消毒：name→text（企微规范），删多余 type/url（本技能 button 全回调）。

    AI 常误用 name 代替 text → 自动改名；button_interaction 的 button 跳转需 type+url，
    本技能 button 全是回调 control key，多余的 type（尤其 type=0 无 url）/url 会导致企微
    校验失败，删掉。返回 True OK，False 缺 text 须兜底。
    """
    if not isinstance(btn, dict):
        return False
    if not btn.get("text") and btn.get("name"):
        btn["text"] = btn.pop("name")
    if not btn.get("text"):
        return False
    for k in ("type", "url"):
        btn.pop(k, None)
    return True


def _sanitize_selection(card, items):
    """选择卡校验+消毒。返回 True 可输出，False 须兜底。"""
    tc = card.get("template_card")
    if not isinstance(tc, dict) or tc.get("card_type") != ALLOWED_CARD_TYPE:
        return False

    valid_ids = {str(it.get("id")) for it in items if it.get("id") is not None}
    bl = tc.get("button_list")
    if not isinstance(bl, list) or not bl:
        return False

    seen_keys = set()
    for btn in bl:
        if not _sanitize_button(btn):
            return False  # 缺 text 须兜底
        key = btn.get("key")
        if not key or key in seen_keys:
            return False  # 缺 key 或重复
        seen_keys.add(key)
        if key.startswith("select_"):
            sid = key[len("select_"):]
            if sid not in valid_ids:
                return False  # 幻觉客户 id
        elif key not in CONTROL_KEYS:
            return False  # 未知 key
    # 删多余 card_action（build_card 不加；url 空会导致企微校验失败）
    tc.pop("card_action", None)
    if len(bl) > MAX_BUTTON:
        tc["button_list"] = bl[:MAX_BUTTON]
    if not tc.get("task_id"):
        tc["task_id"] = f"task_{uuid.uuid4().hex[:8]}"
    if not tc.get("main_title"):
        tc["main_title"] = {"title": "选择客户"}
    card["template_card"] = tc
    return True


def _sanitize_profile(card, data):
    """画像卡校验+消毒（text_notice）。返回 True 可输出，False 须兜底。

    text_notice 结构：jump_list + card_action（必填）做「查看完整画像」跳转，
    无 button_list、无 task_id。url 幻觉 → 兜底；缺 card_action → 注入。
    """
    tc = card.get("template_card")
    if not isinstance(tc, dict) or tc.get("card_type") != "text_notice":
        return False

    update_url = data.get("update_url", "")

    # jump_list url 校验（每个 type:1 url 必须 == update_url）
    for item in tc.get("jump_list", []) or []:
        if isinstance(item, dict) and item.get("type") == 1 and item.get("url"):
            if item["url"] != update_url:
                return False  # 幻觉 url

    # card_action（text_notice 必填）：缺失→注入；幻觉 url→兜底
    ca = tc.get("card_action")
    if not isinstance(ca, dict) or ca.get("type") != 1:
        if update_url:
            tc["card_action"] = {"type": 1, "url": update_url}
        else:
            tc["card_action"] = {"type": 1, "url": "https://work.weixin.qq.com"}
    elif ca.get("url") and update_url and ca["url"] != update_url:
        return False  # 幻觉 url

    # 删 button_list（text_notice 无按钮）+ task_id（不需要）
    tc.pop("button_list", None)
    tc.pop("task_id", None)

    # hcl 截断 + 删残留 type:1 url 行（"更新画像"残留）
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
                if item.get("type") == 1:
                    item.pop("type", None)
                    item.pop("url", None)
        tc["horizontal_content_list"] = hcl
    if not tc.get("main_title"):
        tc["main_title"] = {"title": "客户画像"}
    card["template_card"] = tc
    return True


def main():
    p = argparse.ArgumentParser(description="AI 卡片草稿校验 + 兜底（选择卡/画像卡）")
    p.add_argument("--card-json", required=True, help="AI 手写的 template_card 草稿 JSON")
    args = p.parse_args()

    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        print(FAIL_TEXTS["api_fail"])
        return

    if not isinstance(data, dict) or not data.get("ok", False):
        print(FAIL_TEXTS.get(data.get("error") if isinstance(data, dict) else None, FAIL_TEXTS["api_fail"]))
        return

    # 解析草稿
    try:
        draft = json.loads(args.card_json)
    except Exception:
        draft = None

    # 上下文判定：has_profile/fields 优先（search.py --fetch-profile 合并输出
    # 同时含 items + fields 时，应按画像上下文处理，不是选择卡）
    is_profile = "has_profile" in data or "fields" in data
    is_selection = not is_profile and isinstance(data.get("items"), list)

    if is_selection:
        items = data["items"]
        if not items:
            print(NO_HIT_TEXT)
            return
        if isinstance(draft, dict) and _sanitize_selection(draft, items):
            print(json.dumps(draft, ensure_ascii=False))
            return
        card = build_selection_card(items, page=1)
        print(json.dumps(card, ensure_ascii=False))
        return

    if is_profile:
        if data.get("has_profile") is False or not data.get("fields"):
            print(NO_PROFILE_TEXT)
            return
        if isinstance(draft, dict) and _sanitize_profile(draft, data):
            print(json.dumps(draft, ensure_ascii=False))
            return
        card = build_profile_card(data)
        print(json.dumps(card, ensure_ascii=False))
        return

    # 未知上下文
    print(FAIL_TEXTS["api_fail"])


if __name__ == "__main__":
    main()
