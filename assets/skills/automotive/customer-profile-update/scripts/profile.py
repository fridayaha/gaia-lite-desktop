#!/usr/bin/env python3
"""
profile.py — 客户完整画像取数 + 枚举映射（纯取数器）

调 /profile/{phone} + /config/note-attributes，在脚本内完成枚举映射、JSON 字符串
解析、多值拆分，输出**干净字段**。**不建卡**——AI 读 fields 手写画像卡，交
validate_card.py 校验兜底。

  python3 profile.py --phone 13912345678 --customer-name 客户5678 > "$HERMES_HOME/.skill_tmp/cp.json"

stdout 结构：
  {"ok": true, "phone": "...", "customer_name": "...", "update_url": "...",
   "fields": {deal_level, overall_tag, personality_summary, intended_model,
              budget_range, current_stage, breakthrough_point,
              motivations, preferences, resistances},
   "hints": {"has_profile": true}}
  {"ok": true, "has_profile": false, ...}   # 画像空（AI 按错误表出话术）
  {"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}

只用 Python 标准库。API Key 经 sidecar 解密。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SIDECAR_URL = "http://localhost:8004/secret?skill=customer-profile-update&key=api_key"
API_BASE = os.getenv("PROFILE_API_BASE", "https://mhero.dfmc.com.cn/customer_profile/m2m_api")
UPDATE_BASE = os.getenv("PROFILE_UPDATE_BASE", "https://mhero.dfmc.com.cn")


def get_api_key(sidecar_url=SIDECAR_URL):
    with urllib.request.urlopen(sidecar_url, timeout=5.0) as r:
        return json.loads(r.read().decode())["value"]


def http_get_json(url, headers=None, timeout=10.0):
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
    if status == 403:
        return "forbidden"
    if status is None:
        return "timeout"
    return "api_fail"


def fetch_profile(phone, api_key):
    """GET /profile/{phone} → profile dict（或 None 表示空/失败）。返回 (profile, error)。"""
    url = f"{API_BASE}/api/v1/remote/data/profile/{urllib.parse.quote(phone)}"
    status, data = http_get_json(url, headers={"X-API-Key": api_key})
    if status == 200 and isinstance(data, dict) and data:
        return data, None
    if status == 200 and (data is None or data == {}):
        return None, None  # 客户存在但无画像
    return None, classify_error(status)


def fetch_enum_map(api_key):
    """GET /config/note-attributes → {attribute_key: {raw: 中文}}。失败返回 {}。"""
    url = f"{API_BASE}/api/v1/remote/config/note-attributes"
    status, data = http_get_json(url, headers={"X-API-Key": api_key})
    if status != 200 or not isinstance(data, dict):
        return {}
    enum_map = {}
    for c in data.get("configs", []) or []:
        key = c.get("attribute_key")
        items = c.get("items", {})
        if isinstance(items, dict) and items:
            enum_map[key] = items
    return enum_map


def parse_note(notes, key, enum_map):
    """解析 basic_notes 单字段：JSON 字符串取 key + 枚举映射 + || 拆分。不截断。"""
    v = notes.get(key, {})
    raw = v.get("value", "") if isinstance(v, dict) else str(v)
    # JSON 字符串字段（如 driver_license_status 值为 {"key":"yes",...}）
    if isinstance(raw, str) and raw.startswith("{"):
        try:
            raw = json.loads(raw).get("key", raw)
        except Exception:
            pass
    # 枚举映射
    if key in enum_map and raw in enum_map[key]:
        raw = enum_map[key][raw]
    # || 分隔多值
    if "||" in str(raw):
        raw = " / ".join(str(raw).split("||"))
    return str(raw)


def extract_fields(profile, enum_map):
    """从 profile 提取干净字段（已映射/解析/聚合），不截断。"""
    ms = profile.get("main_summary", {}) or {}
    co = profile.get("customer_overview", {}) or {}
    bn = profile.get("basic_notes", {}) or {}

    motivations = " / ".join(
        [m.get("motivation_name", "") for m in (profile.get("purchase_motivations") or [])[:3] if m.get("motivation_name")]
    )
    preferences = " / ".join(
        [p.get("preference_name", "") for p in (profile.get("product_preferences") or [])[:3] if p.get("preference_name")]
    )
    resistances = " / ".join(
        [r.get("resistance_name", "") for r in (profile.get("resistances") or [])[:3] if r.get("resistance_name")]
    )

    return {
        "deal_level": str(ms.get("deal_level", "") or ""),
        "overall_tag": str(ms.get("overall_tag", "") or ""),
        "personality_summary": str(ms.get("personality_summary", "") or ""),
        "intended_model": parse_note(bn, "intended_model", enum_map),
        "budget_range": parse_note(bn, "budget_range", enum_map),
        "current_stage": str(co.get("current_stage", "") or ""),
        "breakthrough_point": str(co.get("breakthrough_point", "") or ""),
        "motivations": motivations,
        "preferences": preferences,
        "resistances": resistances,
    }


def build_output(phone, customer_name, profile, enum_map):
    """组装结构化输出。"""
    update_url = f"{UPDATE_BASE}/customer_profile/customer/{phone}/profile"
    if not profile:
        return {
            "ok": True,
            "has_profile": False,
            "phone": phone,
            "customer_name": customer_name,
            "update_url": update_url,
            "fields": {},
            "hints": {"has_profile": False},
        }
    return {
        "ok": True,
        "has_profile": True,
        "phone": phone,
        "customer_name": customer_name,
        "update_url": update_url,
        "fields": extract_fields(profile, enum_map),
        "hints": {"has_profile": True},
    }


def _tee_cp_json(out):
    """tee：写 .skill_tmp/cp.json（供 validate_card.py stdin）+ stdout 照常输出（供 agent 直读）。"""
    base = os.environ.get("HERMES_HOME") or os.environ.get("HOME")
    if not base:
        return
    d = os.path.join(base, ".skill_tmp")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "cp.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False))
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser(description="客户画像取数 + 枚举映射")
    p.add_argument("--phone", required=True, help="客户完整手机号")
    p.add_argument("--customer-name", default="", help="客户名称（用于卡片标题）")
    args = p.parse_args()

    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail"}, ensure_ascii=False))
        return

    profile, err = fetch_profile(args.phone, api_key)
    if err:
        print(json.dumps({"ok": False, "error": err, "phone": args.phone}, ensure_ascii=False))
        return

    enum_map = fetch_enum_map(api_key) if profile else {}
    out = build_output(args.phone, args.customer_name, profile, enum_map)
    _tee_cp_json(out)  # 写 cp.json 给 validate_card（tee），agent 直读下面的 stdout
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
