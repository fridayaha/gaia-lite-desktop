#!/usr/bin/env python3
"""
query_detail.py — 客户画像详情深挖切片（从 detail.py 存盘的文件取）

读 $HERMES_HOME/.skill_tmp/cp_detail_{phone}.json，按 --topic 返回特定切片。**避免把 90KB 全量塞进
会话历史**——AI 概要答不了时，按主题取一小片。

画像可变：缓存文件 mtime 超 10min 视为陈旧，返 cache_missing 触发 detail.py 重取。

  python3 query_detail.py --phone 13912345678 --topic emotion_detail

stdout 结构：
  {"ok": true, "phone": "...", "topic": "...", "data": <切片>}
  {"ok": true, "phone": "...", "topic": "...", "data": null,
   "message": "该客户的<模块>模块尚未生成"}                      # 依赖模块 null
  {"ok": false, "error": "cache_missing", "phone": "...",
   "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
  {"ok": false, "error": "unknown_topic", "available": [...]}

只用标准库。文件由 detail.py 写入（0600，profile 私有 .skill_tmp）。basic_notes 主题复用
profile.py 的 parse_note 做枚举映射。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profile import parse_note  # noqa: E402
from scratch import skill_scratch  # noqa: E402

# 缓存目录：测试可覆盖；None → 运行时按 profile 解析（HERMES_HOME/.skill_tmp，0700）。
CACHE_DIR = None
CACHE_TTL = 600  # 10min 新鲜度 TTL（与 detail.py 一致；超时返 cache_missing）


def _cache_dir():
    """返回缓存目录：显式覆盖优先，否则按 profile 解析私有目录。"""
    return CACHE_DIR or skill_scratch()

MODULE_LABEL = {
    "basic_notes": "基础属性",
    "customer_overview": "客户总览",
    "emotion_state": "情绪状态",
    "purchase_motivations": "购买动机",
    "product_preferences": "产品偏好",
    "resistances": "抗拒点",
    "inferred_tags": "推断标签",
    "usage_scenarios": "用车场景",
    "main_summary": "主摘要",
}


def _profile(obj):
    return obj.get("profile") if isinstance(obj, dict) else None


def _basic_notes_detail(obj):
    """全部 basic_notes 属性：parse_note 现映射 value + reasoning_summary。"""
    profile = _profile(obj) or {}
    bn = profile.get("basic_notes") or {}
    enum_map = obj.get("enum_map") or {}
    out = {}
    if isinstance(bn, dict):
        for key in bn:
            v = bn.get(key)
            if not isinstance(v, dict):
                continue
            out[key] = {
                "value": parse_note(bn, key, enum_map),
                "reasoning_summary": str(v.get("reasoning_summary", "") or ""),
            }
    return out


def _customer_overview_detail(obj):
    return (_profile(obj) or {}).get("customer_overview")


def _emotion_detail(obj):
    return (_profile(obj) or {}).get("emotion_state")


def _motivations_detail(obj):
    return (_profile(obj) or {}).get("purchase_motivations")


def _preferences_detail(obj):
    return (_profile(obj) or {}).get("product_preferences")


def _resistances_detail(obj):
    return (_profile(obj) or {}).get("resistances")


def _inferred_tags(obj):
    return (_profile(obj) or {}).get("inferred_tags")


def _usage_scenarios(obj):
    return (_profile(obj) or {}).get("usage_scenarios")


def _personality(obj):
    return (_profile(obj) or {}).get("main_summary")


# topic -> (取值函数, 依赖模块)
# 依赖模块用于 null 检测：该模块为 null/空时返回 data=null + "未生成" 提示
TOPICS = {
    "basic_notes_detail": (_basic_notes_detail, "basic_notes"),
    "customer_overview_detail": (_customer_overview_detail, "customer_overview"),
    "emotion_detail": (_emotion_detail, "emotion_state"),
    "motivations_detail": (_motivations_detail, "purchase_motivations"),
    "preferences_detail": (_preferences_detail, "product_preferences"),
    "resistances_detail": (_resistances_detail, "resistances"),
    "inferred_tags": (_inferred_tags, "inferred_tags"),
    "usage_scenarios": (_usage_scenarios, "usage_scenarios"),
    "personality": (_personality, "main_summary"),
}


def load_cache(phone):
    """读 $HERMES_HOME/.skill_tmp/cp_detail_{phone}.json，检查 mtime 新鲜度 TTL。

    返回 (obj, path)；无 profile 上下文 / 缺失 / 过期 / 损坏返回 (None, None)。
    """
    d = _cache_dir()
    if not d:
        return None, None
    path = os.path.join(d, f"cp_detail_{phone}.json")
    if not os.path.exists(path):
        return None, None
    try:
        if time.time() - os.path.getmtime(path) > CACHE_TTL:
            return None, None  # 过期
    except OSError:
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except Exception:
        return None, None


def main():
    p = argparse.ArgumentParser(description="客户画像详情深挖切片")
    p.add_argument("--phone", required=True, help="客户完整手机号")
    p.add_argument("--topic", required=True, help="深挖主题（--topic help 列可用主题）")
    args = p.parse_args()

    if args.topic == "help":
        print(json.dumps({"ok": True, "available": sorted(TOPICS.keys())}, ensure_ascii=False))
        return

    if args.topic not in TOPICS:
        print(json.dumps({
            "ok": False,
            "error": "unknown_topic",
            "available": sorted(TOPICS.keys()),
        }, ensure_ascii=False))
        return

    obj, _path = load_cache(args.phone)
    if obj is None:
        print(json.dumps({
            "ok": False,
            "error": "cache_missing",
            "phone": args.phone,
            "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数",
        }, ensure_ascii=False))
        return

    fn, dep_module = TOPICS[args.topic]
    profile = _profile(obj) or {}
    if not profile.get(dep_module):
        label = MODULE_LABEL.get(dep_module, dep_module)
        print(json.dumps({
            "ok": True,
            "phone": args.phone,
            "topic": args.topic,
            "data": None,
            "message": f"该客户的{label}模块尚未生成",
        }, ensure_ascii=False))
        return

    data = fn(obj)
    print(json.dumps({
        "ok": True,
        "phone": args.phone,
        "topic": args.topic,
        "data": data,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
