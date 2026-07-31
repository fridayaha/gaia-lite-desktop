#!/usr/bin/env python3
"""
search.py — 客户画像模糊检索（纯取数器，可选合并取画像）

调 /api/v1/remote/data/profiles，stdout 输出结构化 JSON。**不建卡**——
卡片由 AI 手写，交 validate_card.py 校验兜底。

加 --fetch-profile 后，total=1 时自动调 profile API 取画像并合并到输出，
agent 跳过单独的 profile.py 步骤（省 1 次 LLM 往返）。

  python3 search.py --customer-name-keyword 王 > "$HERMES_HOME/.skill_tmp/cp.json"
  python3 search.py --customer-name-keyword 王 --fetch-profile > "$HERMES_HOME/.skill_tmp/cp.json"
  python3 search.py --phone-tail 8001 > "$HERMES_HOME/.skill_tmp/cp.json"   # 尾号精确匹配

stdout 结构（无 --fetch-profile 或 total>1）：
  {"ok": true, "total": N, "items": [...], "query": {...}, "hints": {...}}

stdout 结构（--fetch-profile + total=1，合并画像）：
  {"ok": true, "total": 1, "items": [...], "has_profile": true,
   "fields": {...}, "update_url": "...", "phone": "...", "customer_name": "...",
   "query": {...}, "hints": {...}}

  {"ok": false, "error": "auth_fail"|"forbidden"|"api_fail"|"timeout"}   # 失败

只用 Python 标准库（urllib），无第三方依赖。API Key 经 sidecar 解密，不硬编码。
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
API_PATH = "/api/v1/remote/data/profiles"

# 输出条数上限（6 页 × 5 条/页 = 30，足够翻页）；total 保留 API 真值
MAX_ITEMS_OUTPUT = 30


def get_api_key(sidecar_url=SIDECAR_URL):
    """从 sidecar 取解密后的 API Key。失败抛异常。"""
    with urllib.request.urlopen(sidecar_url, timeout=5.0) as r:
        return json.loads(r.read().decode())["value"]


def http_get_json(url, headers=None, timeout=10.0):
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
    """HTTP 状态码 → 错误标识（供 AI 选话术）。"""
    if status == 401:
        return "auth_fail"
    if status == 403:
        return "forbidden"
    if status is None:
        return "timeout"
    return "api_fail"


def search_profiles(
    phone_keyword=None,
    customer_name_keyword=None,
    size=100,
    api_key=None,
    phone_tail=None,
):
    """调检索 API，返回结构化 dict（含 ok/total/items/query/hints）。

    phone_tail: 尾号精确匹配——先用 phone_keyword 调 API（模糊），
    再客户端过滤 phone.endswith(tail)。解决 API LIKE %X% 把中间含 X 的也返回的问题。
    """
    if api_key is None:
        try:
            api_key = get_api_key()
        except Exception:
            return {"ok": False, "error": "auth_fail"}

    # phone_tail 时用 tail 值做 API 模糊查询，后续客户端过滤
    effective_keyword = phone_tail or phone_keyword

    params = {"size": str(size)}
    if effective_keyword:
        params["phone_keyword"] = effective_keyword
    if customer_name_keyword:
        params["customer_name_keyword"] = customer_name_keyword
    url = f"{API_BASE}{API_PATH}?{urllib.parse.urlencode(params)}"

    status, data = http_get_json(url, headers={"X-API-Key": api_key})
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "error": classify_error(status)}

    items = data.get("items", []) or []
    # 只保留卡片需要的精简字段
    slim = [
        {
            "id": it.get("id"),
            "phone": it.get("phone", ""),
            "name": it.get("name", ""),
            "deal_level": it.get("deal_level", ""),
            "profile_sync_status": it.get("profile_sync_status", 1),
            "overall_tag": it.get("overall_tag", ""),
        }
        for it in items
    ]
    # phone_tail: 客户端过滤——只保留 phone 以 tail 结尾的
    if phone_tail:
        slim = [s for s in slim if s.get("phone", "").endswith(phone_tail)]
    total = len(slim)  # 尾号过滤后 total = 实际匹配数
    # count_category 基于 total（真值），不基于 len(slim)（可能被 MAX_ITEMS_OUTPUT 截）
    count_category = "none" if total == 0 else "single" if total == 1 else "multi"
    # 输出截到 MAX_ITEMS_OUTPUT（控 terminal 体积；total 保留真值供翻页判断）
    slim_output = slim[:MAX_ITEMS_OUTPUT]
    return {
        "ok": True,
        "total": total,
        "items": slim_output,
        "query": {
            "phone_keyword": phone_keyword,
            "phone_tail": phone_tail,
            "customer_name_keyword": customer_name_keyword,
            "size": size,
        },
        "hints": {"count_category": count_category, "returned": len(slim_output)},
    }


def _fetch_and_merge_profile(out, api_key):
    """total=1 时自动取画像，合并 fields/has_profile/update_url 到搜索输出。

    复用 profile.py 的 fetch_profile + fetch_enum_map + build_output，
    避免重复实现取数 + 枚举映射逻辑。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    # noqa: E402 (lazy import: profile.py is a sibling script, loaded only when merging)
    from profile import (  # noqa: E402
        build_output,
        fetch_enum_map,
        fetch_profile,
    )

    item = out["items"][0]
    phone = item.get("phone", "")
    customer_name = item.get("name", "")
    if not phone:
        return  # 无 phone 无法取画像，保持原样

    profile, err = fetch_profile(phone, api_key)
    if err:
        return  # 取画像失败，保持搜索结果原样（agent 可后续单独跑 profile.py）

    enum_map = fetch_enum_map(api_key) if profile else {}
    profile_out = build_output(phone, customer_name, profile, enum_map)
    # 合并画像字段到搜索输出
    out["has_profile"] = profile_out.get("has_profile", False)
    if profile_out.get("fields"):
        out["fields"] = profile_out["fields"]
    if profile_out.get("update_url"):
        out["update_url"] = profile_out["update_url"]
    out["phone"] = phone
    out["customer_name"] = customer_name


def _tee_cp_json(out):
    """tee：写 .skill_tmp/cp.json（供 validate_card.py stdin）+ stdout 照常输出（供 agent 直读）。

    消除"重定向 stdout 到文件 + read_file 回读"的往返：脚本自己写 cp.json + 打 stdout，
    agent 直接读 terminal stdout，validate_card 仍从 cp.json stdin 读。
    无 HERMES_HOME/HOME → 跳过写文件（best-effort，stdout 仍给 agent）。
    """
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
    p = argparse.ArgumentParser(description="客户画像模糊检索（纯取数）")
    p.add_argument("--phone-keyword", default=None, help="手机号片段（如 5678，模糊匹配任意位置）")
    p.add_argument(
        "--phone-tail",
        default=None,
        help="手机尾号（如 8001，精确匹配尾号——API 模糊查 + 客户端 endswith 过滤）",
    )
    p.add_argument("--customer-name-keyword", default=None, help="客户名称关键词")
    p.add_argument("--size", type=int, default=100, help="每页条数（默认 100 一次拉全）")
    p.add_argument(
        "--fetch-profile",
        action="store_true",
        default=False,
        help="total=1 时自动取画像合并到输出（省 agent 单独跑 profile.py 的 1 次往返）",
    )
    args = p.parse_args()

    if not args.phone_keyword and not args.phone_tail and not args.customer_name_keyword:
        print(json.dumps({"ok": False, "error": "no_keyword"}, ensure_ascii=False))
        return

    # 获取 api_key（--fetch-profile 时复用给 profile API）
    api_key = None
    if args.fetch_profile:
        try:
            api_key = get_api_key()
        except Exception:
            api_key = None  # sidecar 不可达时 search_profiles 内部会再试

    out = search_profiles(
        args.phone_keyword,
        args.customer_name_keyword,
        args.size,
        api_key=api_key,
        phone_tail=args.phone_tail,
    )

    # --fetch-profile + total=1 + 成功 → 合并画像
    if (
        args.fetch_profile
        and out.get("ok")
        and out.get("total") == 1
        and api_key
    ):
        _fetch_and_merge_profile(out, api_key)

    _tee_cp_json(out)  # 写 cp.json 给 validate_card（tee），agent 直读下面的 stdout
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
