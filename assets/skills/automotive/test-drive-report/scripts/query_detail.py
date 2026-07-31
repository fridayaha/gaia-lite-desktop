#!/usr/bin/env python3
"""
query_detail.py — 试驾报告详情深挖切片（从 detail.py 存盘的文件取）

读 $HERMES_HOME/.skill_tmp/tdr_detail_{id}.json，按 --topic 返回特定切片。**避免把 90KB 全量塞进
会话历史**——AI 概要答不了时，按主题取一小片。

  python3 query_detail.py --test-drive-id <id> --topic focus_analysis

stdout 结构：
  {"ok": true, "test_drive_id": "...", "topic": "...", "data": <切片>}
  {"ok": true, "test_drive_id": "...", "topic": "...", "data": null,
   "message": "该报告的<模块>模块尚未生成"}                      # 依赖模块 null
  {"ok": false, "error": "cache_missing", "test_drive_id": "...",
   "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数"}
  {"ok": false, "error": "unknown_topic", "available": [...]}

只用标准库。文件由 detail.py 写入（0600，profile 私有 .skill_tmp）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scratch import skill_scratch  # noqa: E402

# 缓存目录：测试可覆盖；None → 运行时按 profile 解析（HERMES_HOME/.skill_tmp，0700）。
CACHE_DIR = None


def _cache_dir():
    """返回缓存目录：显式覆盖优先，否则按 profile 解析私有目录。"""
    return CACHE_DIR or skill_scratch()

MODULE_LABEL = {
    "sales_check": "销售检核",
    "deal_intent": "成交意愿",
    "next_action": "下一步建议",
}


def _di_result(obj):
    di = obj.get("deal_intent")
    return di.get("result") if isinstance(di, dict) else None


def _na_result(obj):
    na = obj.get("next_action")
    return na.get("result") if isinstance(na, dict) else None


def _sc_detail(obj):
    return obj.get("sales_check")


def _di_detail(obj):
    return _di_result(obj)


def _na_detail(obj):
    return _na_result(obj)


def _improvement_suggestions(obj):
    """聚合四维度的改进建议。"""
    sc = obj.get("sales_check") or {}
    out = {}
    if isinstance(sc, dict):
        for dim in ("communication", "knowledge", "deal_guide", "process"):
            v = sc.get(dim)
            if isinstance(v, dict):
                r = v.get("result") or {}
                if r:
                    out[dim] = r.get("improvement_suggestions") or []
    return out


def _focus_analysis(obj):
    return (_di_result(obj) or {}).get("focusAnalysis")


def _resistance(obj):
    return (_di_result(obj) or {}).get("resistanceAnalysis")


def _signals_risks(obj):
    return (_di_result(obj) or {}).get("signalsAndRisks")


def _timeline(obj):
    return (_na_result(obj) or {}).get("timeline")


def _emotion_heatmap(obj):
    return (_di_result(obj) or {}).get("emotionHeatmap")


def _competitor(obj):
    return (_na_result(obj) or {}).get("competitorCard")


def _customer_profile(obj):
    return (_na_result(obj) or {}).get("customerProfile")


def _sales_ammo(obj):
    return (_na_result(obj) or {}).get("salesAmmo")


def _pain_points(obj):
    cd = (_di_result(obj) or {}).get("closerDashboard") or {}
    return cd.get("painPoints")


def _actions(obj):
    nba = (_na_result(obj) or {}).get("nextBestAction") or {}
    return nba.get("actions")


# topic -> (取值函数, 依赖模块)
# 依赖模块用于 null 检测：该模块为 null 时返回 data=null + "未生成" 提示
TOPICS = {
    "sales_check_detail": (_sc_detail, "sales_check"),
    "deal_intent_detail": (_di_detail, "deal_intent"),
    "next_action_detail": (_na_detail, "next_action"),
    "improvement_suggestions": (_improvement_suggestions, "sales_check"),
    "focus_analysis": (_focus_analysis, "deal_intent"),
    "resistance": (_resistance, "deal_intent"),
    "signals_risks": (_signals_risks, "deal_intent"),
    "timeline": (_timeline, "next_action"),
    "emotion_heatmap": (_emotion_heatmap, "deal_intent"),
    "competitor": (_competitor, "next_action"),
    "customer_profile": (_customer_profile, "next_action"),
    "sales_ammo": (_sales_ammo, "next_action"),
    "pain_points": (_pain_points, "deal_intent"),
    "actions": (_actions, "next_action"),
}


def load_cache(test_drive_id):
    """读 $HERMES_HOME/.skill_tmp/tdr_detail_{id}.json。返回 (obj, path)；无上下文/缺失/损坏返回 (None, None)。"""
    d = _cache_dir()
    if not d:
        return None, None
    path = os.path.join(d, f"tdr_detail_{test_drive_id}.json")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except Exception:
        return None, None


def main():
    p = argparse.ArgumentParser(description="试驾报告详情深挖切片")
    p.add_argument("--test-drive-id", required=True, help="试驾业务 ID")
    p.add_argument("--topic", required=True, help="深挖主题（见 --topic=help 列可用主题）")
    args = p.parse_args()

    if args.topic == "help":
        print(json.dumps({"ok": True, "available": sorted(TOPICS.keys())}, ensure_ascii=False))
        return

    if args.topic not in TOPICS:
        print(json.dumps(
            {"ok": False, "error": "unknown_topic", "available": sorted(TOPICS.keys())},
            ensure_ascii=False,
        ))
        return

    obj, _path = load_cache(args.test_drive_id)
    if obj is None:
        print(json.dumps({
            "ok": False,
            "error": "cache_missing",
            "test_drive_id": args.test_drive_id,
            "message": "详情缓存已过期或不存在，请重新提问以触发 detail.py 取数",
        }, ensure_ascii=False))
        return

    fn, dep_module = TOPICS[args.topic]
    # 依赖模块整体 null → 该主题无数据
    if not obj.get(dep_module):
        label = MODULE_LABEL.get(dep_module, dep_module)
        print(json.dumps({
            "ok": True,
            "test_drive_id": args.test_drive_id,
            "topic": args.topic,
            "data": None,
            "message": f"该报告的{label}模块尚未生成",
        }, ensure_ascii=False))
        return

    data = fn(obj)
    print(json.dumps({
        "ok": True,
        "test_drive_id": args.test_drive_id,
        "topic": args.topic,
        "data": data,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
