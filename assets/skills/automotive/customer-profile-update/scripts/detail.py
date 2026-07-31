#!/usr/bin/env python3
"""
detail.py — 客户画像详情取数 + 概要提取（纯取数器）

调 GET /api/v1/remote/data/profile/{phone}（复用 profile.py 的 fetch_profile），
把完整画像 + enum_map 存到 $HERMES_HOME/.skill_tmp/cp_detail_{phone}.json（0600），stdout 只返回
~5-9KB「概要」（高信号摘要），**避免 90KB 全量进入会话历史**。深挖内容由
query_detail.py 按主题从磁盘文件取。

画像可变（销售可能更新画像），故 detail.py 每次运行都从 API 现取（只写缓存、
不读缓存服务概要）；query_detail 读缓存时查 mtime，超 10min 返 cache_missing
触发本脚本重取。

  python3 detail.py --phone 13912345678 [--customer-name 客户5678] > "$HERMES_HOME/.skill_tmp/cp_brief.json"

stdout 结构：
  {"ok": true, "phone": "139****5678", "customer_name": "...", "update_url": "...",
   "fetched_at": "...", "updated_at": "...", "stored_at": "$HERMES_HOME/.skill_tmp/cp_detail_<phone>.json",
   "brief": {deal_level, overall_tag, ..., closing_probability, emotion_current_state,
             inferred_tags, usage_scenarios, ...},
   "topics": [...], "hints": {"has_main_summary": bool, ...}}
  {"ok": true, "has_profile": false, "phone": "...", ...}   # 客户存在但无画像
  {"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}

只用标准库。API Key 经 sidecar 解密（复用 profile.py 的 get_api_key）。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profile import (  # noqa: E402
    get_api_key, fetch_profile, fetch_enum_map, extract_fields, UPDATE_BASE,
)
from scratch import skill_scratch  # noqa: E402

# 缓存目录：测试可覆盖（D.CACHE_DIR = td）；None → 运行时按 profile 解析私有目录。
CACHE_DIR = None
CACHE_TTL = 600  # 10min 新鲜度 TTL（与 query_detail.py 一致；超时 query_detail 返 cache_missing）


def _cache_dir():
    """返回缓存目录：显式覆盖优先，否则按 profile 解析（HERMES_HOME/.skill_tmp，0700）。"""
    return CACHE_DIR or skill_scratch()

# query_detail.py 支持的深挖主题——写进概要让 AI 知道可问什么
DEEP_DIVE_TOPICS = [
    "basic_notes_detail", "customer_overview_detail", "emotion_detail",
    "motivations_detail", "preferences_detail", "resistances_detail",
    "inferred_tags", "usage_scenarios", "personality",
]


def mask_phone(phone):
    """手机号脱敏：前3后4中间****。"""
    if not phone:
        return ""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


def store_full(phone, obj):
    """存 {profile, enum_map, fetched_at} 到 $HERMES_HOME/.skill_tmp/cp_detail_{phone}.json (0600)。

    返回路径，失败返回 None。best-effort（无 profile 上下文 / 磁盘满等不致命，query_detail 会 cache_missing）。
    """
    d = _cache_dir()
    if not d:
        return None
    path = os.path.join(d, f"cp_detail_{phone}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.chmod(path, 0o600)
    except Exception:
        return None
    return path


def _brief_customer_overview(co):
    """customer_overview 概要字段（缺模块时各字段回落为 ""）。"""
    if not co or not isinstance(co, dict):
        co = {}
    return {
        "closing_probability": str(co.get("closing_probability", "") or ""),
        "customer_type": str(co.get("customer_type", "") or ""),
        "business_opp_level": str(co.get("business_opp_level", "") or ""),
        "core_issue": str(co.get("core_issue", "") or ""),
    }


def _brief_emotion_state(es):
    """emotion_state 概要字段（缺模块时各字段回落为 ""）。"""
    if not es or not isinstance(es, dict):
        es = {}
    return {
        "emotion_current_state": str(es.get("current_state", "") or ""),
        "brand_attitude": str(es.get("brand_attitude", "") or ""),
        "sales_attitude": str(es.get("sales_attitude", "") or ""),
    }


def _titles(arr):
    """从 [{title, desc}, ...] 取 title 列表。"""
    if not arr or not isinstance(arr, list):
        return []
    return [str(it.get("title", "")) for it in arr if isinstance(it, dict) and it.get("title")]


def _latest_updated_at(profile):
    """best-effort 取画像 updated_at。API 顶层未必有，返回 ""。"""
    if isinstance(profile, dict):
        v = profile.get("updated_at")
        if v:
            return str(v)
    return ""


def build_brief(profile, enum_map, phone, customer_name=""):
    """从完整画像提富概要。复用 extract_fields + 扩展 customer_overview/emotion_state/标签/场景。

    不含 ok/has_profile/stored_at/fetched_at（由 main 注入）。
    """
    fields = extract_fields(profile, enum_map)
    ms = profile.get("main_summary") or {}
    co = profile.get("customer_overview") or {}
    es = profile.get("emotion_state") or {}

    brief = dict(fields)  # 复用 extract_fields 的 10 字段
    brief["profile_summary"] = str(ms.get("profile_summary", "") or "")
    brief.update(_brief_customer_overview(co))
    brief.update(_brief_emotion_state(es))
    brief["inferred_tags"] = _titles(profile.get("inferred_tags"))
    brief["usage_scenarios"] = _titles(profile.get("usage_scenarios"))

    return {
        "phone": mask_phone(phone),
        "customer_name": customer_name,
        "update_url": f"{UPDATE_BASE}/customer_profile/customer/{phone}/profile",
        "updated_at": _latest_updated_at(profile),
        "brief": brief,
        "topics": DEEP_DIVE_TOPICS,
        "hints": {
            "has_main_summary": bool(ms),
            "has_basic_notes": bool(profile.get("basic_notes")),
            "has_customer_overview": bool(co),
            "has_emotion_state": bool(es),
            "has_motivations": bool(profile.get("purchase_motivations")),
            "has_preferences": bool(profile.get("product_preferences")),
            "has_resistances": bool(profile.get("resistances")),
            "has_inferred_tags": bool(profile.get("inferred_tags")),
            "has_usage_scenarios": bool(profile.get("usage_scenarios")),
        },
    }


def main():
    p = argparse.ArgumentParser(description="客户画像详情取数 + 概要")
    p.add_argument("--phone", required=True, help="客户完整手机号")
    p.add_argument("--customer-name", default="", help="客户名称（用于概要标题）")
    args = p.parse_args()

    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "phone": args.phone}, ensure_ascii=False))
        return

    profile, err = fetch_profile(args.phone, api_key)
    if err:
        print(json.dumps({"ok": False, "error": err, "phone": args.phone}, ensure_ascii=False))
        return
    if not profile:  # 200 + 空（has_profile=false）
        print(json.dumps({
            "ok": True,
            "has_profile": False,
            "phone": mask_phone(args.phone),
            "customer_name": args.customer_name,
        }, ensure_ascii=False))
        return

    enum_map = fetch_enum_map(api_key)
    fetched_at = datetime.datetime.now().isoformat()
    stored = store_full(args.phone, {"profile": profile, "enum_map": enum_map, "fetched_at": fetched_at})
    brief = build_brief(profile, enum_map, args.phone, args.customer_name)
    brief["ok"] = True
    brief["has_profile"] = True
    brief["fetched_at"] = fetched_at
    brief["stored_at"] = stored or ""
    print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
