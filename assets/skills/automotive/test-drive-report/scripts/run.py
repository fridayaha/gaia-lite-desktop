#!/usr/bin/env python3
"""
run.py — 试驾报告查询（纯取数器）

只负责调 API + 计算 hints，stdout 输出结构化 JSON（自动 tee 写 .skill_tmp/tdr.json）。**不建卡**——
卡片由 AI 自主手写，再交 validate_card.py 校验兜底。

sales_phone 由 run.py 自动从平台 user-context 端点读取「业务手机号」（平台绑定的业务用户手机号），
**不接受 CLI/对话传入**——使用者即销售顾问本人，避免越权（用他人手机号查询他人报告）。

供 skill 的 terminal 工具调用：

  python3 run.py \\
    [--customer-name <客户名>] [--customer-phone <号/尾号>] \\
    [--drive-date YYYY-MM-DD]

run.py 自动 tee：stdout 给 agent 直读 + 写 .skill_tmp/tdr.json 给 validate_card.py stdin
（不需重定向、不需 read_file）：

  python3 run.py ...
  python3 validate_card.py --card-json '<AI 草稿>' < "$HERMES_HOME/.skill_tmp/tdr.json"

stdout 结构：
  {"ok": true, "code": 0, "total": N, "items": [...], "hints": {...}, "query": {...}}
  {"ok": false, "error": "no_sales_identity"}  # 端点未返回业务手机号（平台故障）
  {"ok": false, "error": "auth_fail"}   # sidecar 不可达
  {"ok": false, "error": "api_fail"}    # API 异常 / code != 0

只用 Python 标准库（urllib），无第三方依赖——terminal 工具的系统 python3 即可运行。
API Key 经 sidecar 解密（参考画像 skill），以 X-API-Key 头调用。API base 用环境变量 TEST_DRIVE_API_BASE 覆盖。
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

# 同目录 import build_card 的解析函数（仅复用 parse_hour，不建卡）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_card import parse_hour  # noqa: E402
from auth import get_api_key  # noqa: E402
from identity import read_sales_phone  # noqa: E402

API_BASE = os.getenv("TEST_DRIVE_API_BASE", "https://mhero.dfmc.com.cn/drive-insight/backend")
API_PATH = "/api/test_drive_reports"

# 企微卡片 horizontal_content_list 上限 6（对齐 validate_card.py MAX_HCL=6）。
# run.py 输出截到 6 条：① 卡片天然上限；② 控 terminal stdout 体积
# （<50k 截断阈值，省 read_file 往返的前提）。
# 注意：这是【输出】上限，不是 API --limit——后者保留 20 覆盖时段过滤
# （API 不支持时段，run.py 客户端过滤；limit 太小则全天样本不足以覆盖上午/下午子集）。
MAX_CARD_ITEMS = 6


def _tee_tdr_json(out: dict) -> None:
    """tee：写 tdr.json（供 validate_card stdin）+ stdout 给 agent 直读。

    消除"重定向 stdout 到文件 + read_file 回读"的往返：run.py 自己写 tdr.json + 打 stdout，
    agent 直接读 terminal stdout，validate_card 仍从 tdr.json stdin 读。无 profile 上下文
    （HERMES_HOME/HOME 缺失）→ 跳过写文件（best-effort，stdout 仍给 agent）。
    """
    from scratch import skill_scratch

    d = skill_scratch()
    if not d:
        return
    try:
        with open(os.path.join(d, "tdr.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False))
    except OSError:
        pass


def query_api(sales_phone, customer_name=None, customer_phone=None, drive_date=None, limit=20, api_key=None):
    """调试驾报告 API，返回 (items, total, code)；失败返回 None。"""
    params = {"sales_phone": sales_phone, "limit": str(limit)}
    if customer_name:
        params["customer_name"] = customer_name
    if customer_phone:
        params["customer_phone"] = customer_phone
    if drive_date:
        params["drive_date"] = drive_date
    try:
        url = f"{API_BASE}{API_PATH}?{urllib.parse.urlencode(params)}"
        headers = {"X-API-Key": api_key} if api_key else {}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    code = data.get("code")
    if code != 0:
        return None
    items = data.get("data", {}).get("items", [])
    total = data.get("data", {}).get("total", len(items))
    return items, total, code


def compute_hints(items):
    """从 items 计算呈现线索，供 AI 选卡片形态时参考。"""
    if not items:
        return {"cross_time_slot": False, "same_customer_multi_car": False, "count_category": "none"}

    hours = [parse_hour(it.get("start_time", "")) for it in items]
    cross_time_slot = any(h < 12 for h in hours) and any(h >= 12 for h in hours)

    # 同一客户手机号对应多个不同车型
    phone_to_models = {}
    for it in items:
        phone = it.get("customer_phone", "")
        model = it.get("vehicle_model", "")
        if phone:
            phone_to_models.setdefault(phone, set()).add(model)
    same_customer_multi_car = any(len(m) > 1 for m in phone_to_models.values())

    n = len(items)
    if n == 1:
        count_category = "single"
    elif n <= 6:
        count_category = "multi"
    else:
        count_category = "many"

    return {
        "cross_time_slot": cross_time_slot,
        "same_customer_multi_car": same_customer_multi_car,
        "count_category": count_category,
    }


def main():
    p = argparse.ArgumentParser(description="试驾报告查询（纯取数）")
    p.add_argument("--customer-name", default=None, help="客户姓名（模糊）")
    p.add_argument("--customer-phone", default=None, help="客户手机号/尾号（模糊）")
    p.add_argument("--drive-date", default=None, help="试驾日期 YYYY-MM-DD")
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="API 取数上限（输出另截 MAX_CARD_ITEMS=6）",
    )
    args = p.parse_args()

    # sales_phone 从平台 user-context 端点读取业务手机号（平台注入的业务身份），不接受对话/CLI 传入。
    # 缺失 = 平台故障（用户未绑定业务用户）→ no_sales_identity，不查。
    sales_phone = read_sales_phone()
    if not sales_phone:
        print(json.dumps({"ok": False, "error": "no_sales_identity", "query": {}}, ensure_ascii=False))
        return

    try:
        api_key = get_api_key()
    except Exception:
        print(json.dumps({"ok": False, "error": "auth_fail", "query": {}}, ensure_ascii=False))
        return

    query = {
        "sales_phone": sales_phone,
        "customer_name": args.customer_name,
        "customer_phone": args.customer_phone,
        "drive_date": args.drive_date,
    }

    result = query_api(
        sales_phone,
        args.customer_name,
        args.customer_phone,
        args.drive_date,
        args.limit,
        api_key=api_key,
    )
    if result is None:
        print(json.dumps({"ok": False, "error": "api_fail", "query": query}, ensure_ascii=False))
        return

    items, api_total, code = result  # api_total = API 全量匹配数（无时段过滤，即真命中数）
    out = {
        "ok": True,
        "code": code,
        "total": api_total,  # 全量命中数（卡片"共N条"用此）
        "items": items[:MAX_CARD_ITEMS],  # 输出截到 6（卡片上限 + stdout 体积控制）
        "hints": compute_hints(items),  # 全量 items 算（count_category 用 len(items)，>6 仍 many）
        "query": query,
    }
    _tee_tdr_json(out)  # 写 tdr.json 给 validate_card（tee），agent 直读下面的 stdout
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
