#!/usr/bin/env python3
"""
detail.py — 试驾报告详情取数 + 概要提取（纯取数器）

调 GET /api/drive_analysis，把完整结果存到 $HERMES_HOME/.skill_tmp/tdr_detail_{id}.json（0600），
stdout 只返回 ~5KB「概要」（高信号摘要），**避免 90KB 全量进入会话历史**。
深挖内容由 query_detail.py 按主题从磁盘文件取。

  python3 detail.py --test-drive-id <id> > "$HERMES_HOME/.skill_tmp/tdr_brief.json"

sales_phone 由 detail.py 自动从平台 user-context 端点读取「业务手机号」（平台绑定的业务用户手机号），
**不接受 CLI/对话传入**——始终作为权限过滤传给 API，避免越权（查他人报告）。

stdout 结构：
  {"ok": true, "test_drive_id": "...", "customer_phone": "176****5538", "vehicle": "...",
   "stored_at": "$HERMES_HOME/.skill_tmp/tdr_detail_<id>.json",
   "updated_at": {"sales_check": "...", "deal_intent": "...", "next_action": "..."},
   "brief": {deal_intent: {...}, sales_check: {...}, next_action: {...}},
   "topics": [...],
   "hints": {"has_sales_check": bool, "has_deal_intent": bool, "has_next_action": bool}}
  {"ok": false, "error": "not_found"|"not_generated"|"api_fail"|"timeout"|"bad_request"}

只用标准库。API Key 经 sidecar 解密（参考画像 skill），以 X-API-Key 头调用。API base 用环境变量 TEST_DRIVE_API_BASE 覆盖。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scratch import skill_scratch  # noqa: E402
from auth import get_api_key  # noqa: E402
from identity import read_sales_phone  # noqa: E402

API_BASE = os.getenv("TEST_DRIVE_API_BASE", "https://mhero.dfmc.com.cn/drive-insight/backend")
API_PATH = "/api/drive_analysis"
# 缓存目录：测试可覆盖；None → 运行时按 profile 解析（HERMES_HOME/.skill_tmp，0700）。
CACHE_DIR = None


def _cache_dir():
    """返回缓存目录：显式覆盖优先，否则按 profile 解析私有目录。"""
    return CACHE_DIR or skill_scratch()

# query_detail.py 支持的深挖主题——写进概要让 AI 知道可问什么
DEEP_DIVE_TOPICS = [
    "sales_check_detail", "deal_intent_detail", "next_action_detail",
    "improvement_suggestions", "focus_analysis", "resistance", "signals_risks",
    "timeline", "emotion_heatmap", "competitor", "customer_profile",
    "sales_ammo", "pain_points", "actions",
]


def http_get_json(url, headers=None, timeout=15.0):
    """GET 返回 (status, data)；网络异常返回 (None, None)。"""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = None
        return e.code, body
    except Exception:
        return None, None


def classify_error(status):
    if status == 401:
        return "auth_fail"
    if status == 404:
        return "not_found"
    if status == 400:
        return "bad_request"
    if status is None:
        return "timeout"
    return "api_fail"


def mask_phone(phone):
    """手机号脱敏：前3后4中间****。"""
    if not phone:
        return ""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


def fetch_detail(test_drive_id, sales_phone=None, api_key=None):
    """调 drive_analysis API。返回 (result_obj, error)。

    result 字段是 JSON 字符串需解析；空/非 dict → not_generated。
    """
    params = {"test_drive_id": test_drive_id}
    if sales_phone:
        params["sales_phone"] = sales_phone
    url = f"{API_BASE}{API_PATH}?{urllib.parse.urlencode(params)}"

    headers = {"X-API-Key": api_key} if api_key else {}
    status, data = http_get_json(url, headers=headers)
    if status != 200 or not isinstance(data, dict):
        return None, classify_error(status)

    if data.get("code") != 0:
        msg = data.get("message") or ""
        if "不存在" in msg or "未找到" in msg:
            return None, "not_found"
        return None, "api_fail"

    result = data.get("result")
    if isinstance(result, str):
        if not result.strip():
            return None, "not_generated"
        try:
            result = json.loads(result)
        except Exception:
            return None, "api_fail"
    if not isinstance(result, dict) or not result:
        return None, "not_generated"
    return result, None


def store_full(test_drive_id, obj):
    """存完整 result 到 $HERMES_HOME/.skill_tmp/tdr_detail_{id}.json (0600)。返回路径，失败返回 None。best-effort。"""
    d = _cache_dir()
    if not d:
        return None
    path = os.path.join(d, f"tdr_detail_{test_drive_id}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.chmod(path, 0o600)
    except Exception:
        return None
    return path


def _brief_deal_intent(di):
    """deal_intent 概要。di = {result, updated_at} 或 null。"""
    if not di or not isinstance(di, dict):
        return None
    r = di.get("result") or {}
    cd = r.get("closerDashboard") or {}
    fa = r.get("focusAnalysis") or {}
    sr = r.get("signalsAndRisks") or {}
    ra = r.get("resistanceAnalysis") or {}
    # focus_items 只取维度+权重+详情，丢 evidence/rationaleChain（深挖主题取）
    focus_items = [
        {
            "dimension": it.get("dimension", ""),
            "weight": it.get("weight"),
            "details": it.get("details", ""),
        }
        for it in (fa.get("items") or [])[:4]
        if isinstance(it, dict)
    ]
    return {
        "close_level": cd.get("closeLevel"),
        "close_probability": cd.get("closeProbability"),
        "stage": cd.get("stage", ""),
        "ai_insight": cd.get("aiInsight", ""),
        "pain_points": cd.get("painPoints") or [],
        "focus_summary": fa.get("summary", ""),
        "focus_items": focus_items,
        "signal_strength": sr.get("signalStrength"),
        "risks": sr.get("risks") or [],
        "explicit_signals": sr.get("explicitSignals") or [],
        "implicit_signals": sr.get("implicitSignals") or [],
        "resistance_summary": ra.get("overallSummary", ""),
    }


def _brief_sales_check(sc):
    """sales_check 概要。sc = {<dim>: {result, updated_at}}，维度可能 null。"""
    if not sc or not isinstance(sc, dict):
        return None
    out = {}
    for dim in ("communication", "knowledge", "deal_guide", "process"):
        v = sc.get(dim)
        if not v or not isinstance(v, dict):
            continue
        r = v.get("result") or {}
        if not r:
            continue
        st = r.get("statistics") or {}
        out[dim] = {
            "score_rate": st.get("score_rate"),
            "overall_evaluation": r.get("overall_evaluation", ""),
            "improvement_suggestions": r.get("improvement_suggestions") or [],
        }
    return out


def _brief_next_action(na):
    """next_action 概要。na = {result, updated_at} 或 null。"""
    if not na or not isinstance(na, dict):
        return None
    r = na.get("result") or {}
    nba = r.get("nextBestAction") or {}
    cp = r.get("customerProfile") or {}
    sa = r.get("salesAmmo") or {}
    cc = r.get("competitorCard") or {}
    return {
        "actions": nba.get("actions") or [],
        "follow_up_script": nba.get("followUpScript", ""),
        "ai_reminder": nba.get("aiReminder") or [],
        "customer_tags": cp.get("tags") or [],
        "risk_alert": cp.get("riskAlert", ""),
        "recommended_kit": sa.get("recommendedKit", ""),
        "competitor": cc.get("competitor", ""),
    }


def _latest_sales_check_ts(sc):
    """sales_check 无模块级 updated_at，取四维度里最新的。"""
    ts = []
    if isinstance(sc, dict):
        for v in sc.values():
            if isinstance(v, dict) and v.get("updated_at"):
                ts.append(v["updated_at"])
    return max(ts) if ts else ""


def build_brief(obj):
    """从完整 result 提取概要（不含 ok/stored_at，由 main 注入）。"""
    sc = obj.get("sales_check")
    di = obj.get("deal_intent")
    na = obj.get("next_action")
    vehicle = ""
    if di and isinstance(di, dict):
        v = (di.get("result") or {}).get("vehicle") or {}
        vehicle = v.get("model", "") or ""

    return {
        "test_drive_id": obj.get("test_drive_id", ""),
        "customer_phone": mask_phone(obj.get("customer_phone", "")),
        "vehicle": vehicle,
        "updated_at": {
            "sales_check": _latest_sales_check_ts(sc),
            "deal_intent": (di or {}).get("updated_at", "") if isinstance(di, dict) else "",
            "next_action": (na or {}).get("updated_at", "") if isinstance(na, dict) else "",
        },
        "brief": {
            "deal_intent": _brief_deal_intent(di),
            "sales_check": _brief_sales_check(sc),
            "next_action": _brief_next_action(na),
        },
        "topics": DEEP_DIVE_TOPICS,
        "hints": {
            "has_sales_check": bool(sc),
            "has_deal_intent": bool(di),
            "has_next_action": bool(na),
        },
    }


def main():
    p = argparse.ArgumentParser(description="试驾报告详情取数 + 概要")
    p.add_argument("--test-drive-id", required=True, help="试驾业务 ID")
    args = p.parse_args()

    # sales_phone 从平台 user-context 端点读取业务手机号（平台注入的业务身份），不接受对话/CLI 传入。
    # 始终作为权限过滤传给 API（防御越权：用他人 test_drive_id 查他人报告）。
    # 缺失 = 平台故障（用户未绑定业务用户）→ no_sales_identity，不查。
    sales_phone = read_sales_phone()
    if not sales_phone:
        print(json.dumps({"ok": False, "error": "no_sales_identity", "test_drive_id": args.test_drive_id}, ensure_ascii=False))
        return

    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "test_drive_id": args.test_drive_id}, ensure_ascii=False))
        return

    result, err = fetch_detail(args.test_drive_id, sales_phone, api_key=api_key)
    if err:
        out = {"ok": False, "error": err, "test_drive_id": args.test_drive_id}
        print(json.dumps(out, ensure_ascii=False))
        return

    stored = store_full(args.test_drive_id, result)
    brief = build_brief(result)
    brief["ok"] = True
    brief["stored_at"] = stored or ""
    print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
