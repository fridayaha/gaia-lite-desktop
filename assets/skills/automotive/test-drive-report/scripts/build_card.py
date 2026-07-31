#!/usr/bin/env python3
"""
build_card.py — 试驾报告兜底建卡库

**仅作为 validate_card.py 校验失败时的兜底建卡器**。AI 自主建卡流程下，
正常路径不走本脚本；只有当 AI 草稿解析失败、card_type 非法、或引用了
items 中不存在的 url（幻觉）时，validate_card.py 才调用这里的函数
从真实 items 重建一张安全的 text_notice。

保留 build_single_card / build_multi_card（原固定逻辑）+ build_fallback_card
（按命中条数自动选摘要/列表卡）。无 main() 入口——只作为库被 import。
"""


def mask_phone(phone: str) -> str:
    """手机号脱敏：前3后4中间****"""
    if not phone:
        return ""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


def parse_hour(time_str: str) -> int:
    """从 start_time 中提取小时，兼容 'YYYY-MM-DD HH:MM:SS' 和 'YYYY-MM-DDTHH:MM:SS'"""
    if " " in time_str:
        return int(time_str.split(" ")[1].split(":")[0])
    elif "T" in time_str:
        return int(time_str.split("T")[1].split(":")[0])
    return 0


def format_time_range(start: str, end: str) -> str:
    """格式化时间为 'YYYY-MM-DD HH:MM - HH:MM'"""
    s = start.replace("T", " ")[:16]
    e = end.replace("T", " ")[:16]
    return f"{s[:10]} {s[11:16]} - {e[11:16]}"


def build_single_card(item: dict) -> dict:
    """单条命中 → text_notice 摘要卡"""
    name = item.get("customer_name") or f"客户{item['customer_phone'][-4:]}"
    phone = mask_phone(item["customer_phone"])
    time_str = format_time_range(item["start_time"], item["end_time"])
    vehicle = item.get("vehicle", "")
    url = item["report_url"]

    return {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "试驾报告系统"},
            "main_title": {"title": "🚗 试驾报告"},
            "horizontal_content_list": [
                {"keyname": "👤 客户信息", "value": f"{name} · {phone}"},
                {"keyname": "🚙 试驾车型", "value": vehicle},
                {"keyname": "🕐 试驾时间", "value": time_str},
            ],
            "jump_list": [
                {"type": 1, "url": url, "title": "查看完整报告"}
            ],
            "card_action": {"type": 1, "url": url},
        },
    }


def build_multi_card(items: list) -> dict:
    """多条命中 → text_notice 列表卡"""
    card = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "试驾报告系统"},
            "main_title": {"title": f"🚗 找到 {len(items)} 条试驾报告"},
            "sub_title_text": "👇 点击对应客户查看完整试驾报告",
            "horizontal_content_list": [],
            "card_action": {"type": 1, "url": items[0]["report_url"]},
        },
    }

    # 检测是否跨时段（区分上下午）
    hours = [parse_hour(it["start_time"]) for it in items]
    cross_period = any(h < 12 for h in hours) and any(h >= 12 for h in hours)

    for i, item in enumerate(items):
        name = item.get("customer_name") or f"客户{item['customer_phone'][-4:]}"
        tail = item["customer_phone"][-4:]
        model = item.get("vehicle_model", "")

        value = f"📱 {tail} 🚙 {model}"
        if cross_period:
            period = "上午" if parse_hour(item["start_time"]) < 12 else "下午"
            value += f" 🕐 {period}"

        card["template_card"]["horizontal_content_list"].append({
            "keyname": (f"{i+1} {name}")[:5],
            "value": value[:26],
            "type": 1,
            "url": item["report_url"],
        })

    return card


def build_fallback_card(items):
    """兜底建卡：按命中条数选 text_notice 摘要卡/列表卡。无命中返回 None。

    供 validate_card.py 在 AI 草稿无效时调用——只用真实 items，结构必然合法。
    """
    if not items:
        return None
    if len(items) == 1:
        return build_single_card(items[0])
    if len(items) <= 6:
        return build_multi_card(items)
    card = build_multi_card(items[:6])
    card["template_card"]["main_title"]["desc"] = "最多展示6条，请提供更详细的客户信息"
    return card
