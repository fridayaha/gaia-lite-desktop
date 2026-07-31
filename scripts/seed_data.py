#!/usr/bin/env python3
"""
UnionAgents 种子数据脚本
- 创建 2 个用户组, 10 个用户, 15 个智能体
- 密码统一: 888888
"""

import urllib.request
import json
import time
import sys
import os

BASE = "http://localhost:8002"
PWD = "88888888"
# 管理员口令：与 seed.py 保持一致。若 seed 时设置了 SEED_ADMIN_PASSWORD，此处也需设置同名变量。
ADMIN_PWD = os.environ.get("SEED_ADMIN_PASSWORD", "admin123")

# ── 角色 ID（从已有数据中获取） ──
ROLES = {
    "admin": "c624863b-7e45-4265-8f05-db6a4b69a543",
    "operator": "1c588ca6-3e74-43a1-a9db-16161307dc0c",
    "end_user": "50f0137d-a6e8-45a0-9823-595670184684",
}

def api(method, path, token=None, data=None, files=False):
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        result = resp.read().decode()
        if result:
            return json.loads(result)
        return None
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  ❌ {method} {path} -> {e.code}: {err}", file=sys.stderr)
        return None

def login():
    print("🔑 登录 admin...")
    r = api("POST", "/api/auth/login", data={"username": "admin", "password": ADMIN_PWD})
    if not r:
        print("❌ 登录失败，请确保 Manager 服务运行在 8002 端口")
        sys.exit(1)
    print(f"  ✅ Token: {r['access_token'][:20]}...")
    return r["access_token"]

# ── 创建用户组 ──
def create_groups(token):
    print("\n📁 创建用户组...")
    groups_data = [
        {"name": "研发中心", "description": "技术研发部门，包含前后端开发、测试、产品"},
        {"name": "市场运营部", "description": "市场营销与运营部门"},
    ]
    groups = []
    for g in groups_data:
        r = api("POST", "/api/user-groups", token, data=g)
        if r:
            groups.append(r)
            print(f"  ✅ {g['name']} -> {r['id']}")
        else:
            print(f"  ⚠️  创建 {g['name']} 失败")
    return groups  # [{id, name, ...}]

# ── 添加用户到用户组 ──
def add_user_to_group(token, user_id, group_id):
    """通过更新组成员接口添加用户"""
    # 获取当前组成员
    r = api("GET", f"/api/user-groups/{group_id}", token)
    if not r:
        return
    members = [m["id"] for m in r.get("members", [])]
    if user_id not in members:
        members.append(user_id)
    # PUT 更新组成员
    api("PUT", f"/api/user-groups/{group_id}", token, data={"member_ids": members})

# ── 创建用户 ──
def create_users(token, groups):
    print("\n👤 创建用户...")
    # groups[0] = 研发中心, groups[1] = 市场运营部
    dev_group = groups[0]["id"]
    mkt_group = groups[1]["id"]

    users_data = [
        {"username": "zhangsan", "email": "zhangsan@unionagents.io", "password": PWD, "role_ids": [ROLES["operator"]]},
        {"username": "lisi", "email": "lisi@unionagents.io", "password": PWD, "role_ids": [ROLES["operator"]]},
        {"username": "wangwu", "email": "wangwu@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "zhaoliu", "email": "zhaoliu@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "sunqi", "email": "sunqi@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "zhouba", "email": "zhouba@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "wujiu", "email": "wujiu@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "zhengshi", "email": "zhengshi@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "chenyi", "email": "chenyi@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
        {"username": "huanger", "email": "huanger@unionagents.io", "password": PWD, "role_ids": [ROLES["end_user"]]},
    ]
    # 组归属：前 5 个用户进研发中心，后 5 个进市场运营部
    group_map = [dev_group] * 5 + [mkt_group] * 5

    created = []
    for i, u in enumerate(users_data):
        r = api("POST", "/api/users", token, data=u)
        if r:
            uid = r["id"]
            # 分配用户组
            add_user_to_group(token, uid, group_map[i])
            # 打组名
            gname = "研发中心" if i < 5 else "市场运营部"
            created.append({**r, "group_name": gname})
            role_name = "运维人员" if u["role_ids"][0] == ROLES["operator"] else "终端用户"
            print(f"  ✅ {u['username']:12s} | {role_name} | {gname}")
        else:
            print(f"  ⚠️  创建 {u['username']} 失败")
        time.sleep(0.1)
    return created

# ── 创建智能体 ──
def create_agents(token, users, groups):
    print("\n🤖 创建智能体...")
    dev_group = groups[0]
    mkt_group = groups[1]

    # 找特定用户
    zhangsan = next(u for u in users if u["username"] == "zhangsan")
    lisi = next(u for u in users if u["username"] == "lisi")
    wangwu = next(u for u in users if u["username"] == "wangwu")

    agents_data = [
        {"name": "代码审查助手", "description": "自动审查代码质量和安全漏洞，支持多种编程语言", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "数据分析师", "description": "数据趋势分析和可视化报告生成", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "文档生成器", "description": "自动生成项目文档和 API 文档", "engine_type": "OPENCLAW", "access_scope": "ALL"},
        {"name": "智能客服", "description": "解答技术问题和内部流程咨询", "engine_type": "HERMES", "access_scope": "USER_GROUP", "group_ids": [dev_group["id"]]},
        {"name": "日志分析专家", "description": "实时日志分析和异常检测告警", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "SQL 优化助手", "description": "SQL 查询优化和索引建议", "engine_type": "OPENCLAW", "access_scope": "USER_GROUP", "group_ids": [dev_group["id"]]},
        {"name": "市场调研助手", "description": "竞品分析和市场趋势报告", "engine_type": "HERMES", "access_scope": "USER_GROUP", "group_ids": [mkt_group["id"]]},
        {"name": "内容创作助手", "description": "营销文案和社交媒体内容生成", "engine_type": "OPENCLAW", "access_scope": "USER_GROUP", "group_ids": [mkt_group["id"]]},
        {"name": "运维监控助手", "description": "系统监控告警和故障排查", "engine_type": "HERMES", "access_scope": "USER", "user_ids": [zhangsan["id"], lisi["id"]]},
        {"name": "知识库问答", "description": "基于内部知识库的智能问答", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "代码重构助手", "description": "代码重构建议和自动化重构", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "测试用例生成器", "description": "自动生成单元测试和集成测试", "engine_type": "OPENCLAW", "access_scope": "USER_GROUP", "group_ids": [dev_group["id"]]},
        {"name": "数据爬虫助手", "description": "网页数据采集和结构化提取", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "周报生成助手", "description": "自动汇总工作内容生成周报", "engine_type": "HERMES", "access_scope": "ALL"},
        {"name": "翻译助手", "description": "多语言翻译和本地化支持", "engine_type": "OPENCLAW", "access_scope": "ALL"},
    ]

    config_template = {
        "model": "deepseek-v4-flash",
        "system_prompt": "你是一个专业的智能助手，请准确、高效地完成用户请求。",
        "avatar_color": "#386bf5"
    }

    created = []
    for i, a in enumerate(agents_data):
        payload = {**config_template, **a}
        r = api("POST", "/api/agents", token, data=payload)
        if r:
            created.append(r)
            status = "✅"
            a_name = a["name"]
            print(f"  ✅ {a_name:12s} | {a['engine_type']:8s} | {a['access_scope']}")
        else:
            print(f"  ⚠️  创建 {a['name']} 失败")
        time.sleep(0.1)

    # 发布部分智能体
    print("\n📢 发布智能体...")
    publish_names = ["代码审查助手", "数据分析师", "文档生成器", "智能客服", "日志分析专家",
                     "SQL 优化助手", "市场调研助手", "内容创作助手", "运维监控助手", "知识库问答",
                     "数据爬虫助手"]
    for a in created:
        if a["name"] in publish_names:
            r = api("POST", f"/api/agents/{a['id']}/publish", token)
            if r:
                print(f"  ✅ 已发布: {a['name']}")
            time.sleep(0.1)

    # 下架一个
    for a in created:
        if a["name"] == "周报生成助手":
            api("POST", f"/api/agents/{a['id']}/offline", token)
            print(f"  ✅ 已下架: {a['name']}")
            break

    return created

def summary(token):
    print("\n" + "=" * 50)
    print("📊 数据汇总")
    print("=" * 50)

    r = api("GET", "/api/agents?page=1&page_size=100", token)
    if r:
        agents = r.get("items", [])
        pub = sum(1 for a in agents if a["status"] == "PUBLISHED")
        draft = sum(1 for a in agents if a["status"] == "DRAFT")
        off = sum(1 for a in agents if a["status"] == "OFFLINE")
        print(f"🤖 智能体: {len(agents)} 个 (已发布 {pub} / 草稿 {draft} / 已下架 {off})")

    r = api("GET", "/api/users?page=1&page_size=50", token)
    if r:
        print(f"👤 用户: {r['total']} 个")

    r = api("GET", "/api/user-groups", token)
    if r:
        print(f"📁 用户组: {len(r)} 个")
        for g in r:
            print(f"    - {g['name']} (成员: {g.get('member_count', '?')})")

    print("\n--- 登录信息 ---")
    print(f"admin / {ADMIN_PWD}        (系统管理员)")
    print("zhangsan / 88888888     (运维人员, 研发中心)")
    print("lisi / 88888888         (运维人员, 市场运营部)")
    print("其余用户密码均为 88888888")

if __name__ == "__main__":
    token = login()
    groups = create_groups(token)
    users = create_users(token, groups)
    agents = create_agents(token, users, groups)
    summary(token)
    print("\n✅ 种子数据创建完成！")
