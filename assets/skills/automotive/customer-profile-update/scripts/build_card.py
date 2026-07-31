#!/usr/bin/env python3
"""
build_card.py — 客户画像兜底建卡库

**仅作为 validate_card.py 校验失败时的兜底建卡器**。AI 自主建卡流程下，正常路径
不走本脚本；只有当 AI 草稿解析失败、card_type 非法、引用幻觉 select_id / url 时，
validate_card.py 才调用这里的函数从真实数据重建一张安全的模板卡片。

- build_selection_card(items, page): 客户选择列表卡（5/页客户端分页）
- build_profile_card(data): 画像卡（text_notice，字段取舍 + 查看完整画像跳转）
- build_error_card(error_key): 错误话术卡

无 main() 入口——只作为库被 import。只用标准库。
"""
import math


def mask_phone(phone):
    """手机号脱敏：前3后4中间****。"""
    if not phone:
        return ""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


def page_layout(n, page):
    """客户端分页布局：第1页5条，中间页4条，末页剩余。返回 (start, count, has_prev, has_next, total_pages)。"""
    if n <= 0:
        return (0, 0, False, False, 1)
    if n <= 5:
        return (0, n, False, False, 1)
    total_pages = 1 + math.ceil((n - 5) / 4)
    page = max(1, min(page, total_pages))
    if page == 1:
        return (0, 5, False, True, total_pages)
    start = 5 + (page - 2) * 4
    if page == total_pages:
        return (start, n - start, True, False, total_pages)
    return (start, 4, True, True, total_pages)


def build_selection_card(items, page=1):
    """多条命中 → button_interaction 客户选择卡（含翻页按钮）。items 为空返回 None。"""
    if not items:
        return None
    n = len(items)
    start, count, has_prev, has_next, total_pages = page_layout(n, page)
    slc = items[start:start + count]

    button_list = []
    for it in slc:
        name = it.get("name") or f"客户{str(it.get('phone',''))[-4:]}"
        phone = it.get("phone", "")
        button_list.append({
            "text": f"{name} · {mask_phone(phone)}",
            "style": 1,
            "key": f"select_{it.get('id')}",
        })
    if has_prev:
        button_list.append({"text": "上一页", "style": 2, "key": "page_prev"})
    if has_next:
        button_list.append({"text": "下一页", "style": 2, "key": "page_next"})

    title = f"找到 {n} 位匹配客户" if total_pages == 1 else f"找到 {n} 位匹配客户（第 {page}/{total_pages} 页）"
    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "button_interaction",
            "source": {"desc": "客户画像"},
            "main_title": {"title": title},
            "sub_title_text": "点击客户名称选择，或输入手机号片段精确查找",
            "task_id": "task_select",
            "button_list": button_list,
        },
    }


def build_profile_card(data):
    """单客户画像 → text_notice 画像卡。data 为 profile.py 输出（fields/update_url/phone/customer_name）。

    text_notice 结构（参考试驾报告卡）：horizontal_content_list 展示画像字段，
    jump_list + card_action 做「查看完整画像」跳转，sub_title_text 用 \n 换行展示
    动机/偏好/抗性。无 button_list（无「换一个」）、无 task_id、无「更新画像」hcl 行。
    """
    fields = data.get("fields", {}) or {}
    phone = data.get("phone", "")
    customer_name = data.get("customer_name") or f"客户{phone[-4:]}" if phone else "客户"
    update_url = data.get("update_url", "")

    title = f"{customer_name} · {mask_phone(phone)}"
    deal_level = fields.get("deal_level", "")
    personality = (fields.get("personality_summary", "") or "")[:20]
    desc = f"{deal_level}级意向 · {personality}" if deal_level else personality

    # 字段取舍优先级（取前 5 非空）
    priority = [
        ("整体标签", fields.get("overall_tag", "")),
        ("意向车型", fields.get("intended_model", "")),
        ("预算区间", fields.get("budget_range", "")),
        ("购买阶段", fields.get("current_stage", "")),
        ("突破策略", fields.get("breakthrough_point", "")),
    ]
    hcl = [{"keyname": k[:5], "value": (str(v)[:26] if v else "—")} for k, v in priority if v]
    hcl = hcl[:5]

    # sub_title_text：动机/偏好/抗性 \n 换行
    motivations = fields.get("motivations", "")
    preferences = fields.get("preferences", "")
    resistances = fields.get("resistances", "")
    parts = []
    if motivations:
        parts.append(f"动机：{motivations}")
    if preferences:
        parts.append(f"偏好：{preferences}")
    if resistances:
        parts.append(f"抗性：{resistances}")
    sub_title = "\n".join(parts)[:112] if parts else "暂无动机/偏好/抗性摘要"

    card = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "客户画像"},
            "main_title": {"title": title[:26], "desc": desc} if desc else {"title": title[:26]},
            "sub_title_text": sub_title,
            "horizontal_content_list": hcl,
        },
    }
    # text_notice 必填 card_action；有 update_url 时加 jump_list「查看完整画像」
    if update_url:
        card["template_card"]["jump_list"] = [{"type": 1, "url": update_url, "title": "查看完整画像"}]
        card["template_card"]["card_action"] = {"type": 1, "url": update_url}
    else:
        card["template_card"]["card_action"] = {"type": 1, "url": "https://work.weixin.qq.com"}
    return card


def build_error_card(error_key, phone="", customer_name=""):
    """错误话术卡（带重新搜索/结束按钮）。error_key ∈ not_found/forbidden/no_profile/syncing。"""
    messages = {
        "not_found": ("未找到匹配客户", "请确认手机号或姓名，或确认该客户是否归属您。"),
        "forbidden": ("无法查询该客户", "该客户可能不归属您。"),
        "no_profile": ("暂无画像记录", "可能尚未上传过素材，请前往画像系统上传素材生成画像。"),
        "syncing": ("画像生成中", "该客户画像正在生成中，请稍后再查。"),
    }
    title, desc = messages.get(error_key, ("查询失败", "请稍后重试。"))
    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "button_interaction",
            "source": {"desc": "客户画像"},
            "main_title": {"title": title},
            "sub_title_text": desc,
            "task_id": "task_error",
            "button_list": [
                {"text": "重新搜索", "style": 1, "key": "restart"},
                {"text": "结束", "style": 2, "key": "cancel"},
            ],
        },
    }
