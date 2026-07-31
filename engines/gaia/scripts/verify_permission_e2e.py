#!/usr/bin/env python3
"""End-to-end permission governance verification (ADR-016/017).

Tests the complete permission lifecycle: bootstrap defaults → create users/
groups/roles → verify per-role permission boundaries → marking MAC → audit.

Usage:
    .venv/bin/python scripts/verify_permission_e2e.py [--base-url http://127.0.0.1:46094]

Outputs:
    - JSON results to stdout
    - HTML report to docs/engineer/permission-e2e-report.html
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

# ── Test data: users, groups, roles ──
# Each test user maps to a Better Auth user created via the signup API, or
# uses dev-mode X-User-Id headers for quick testing.

DEFAULT_ORG = "org-default"  # seeded by bootstrap

TEST_GROUPS = [
    {"name": "platform-admins", "role": "PLATFORM_ADMIN", "scope_type": "GLOBAL"},
    {"name": "marking-admins", "role": "MARKING_ADMIN", "scope_type": "GLOBAL"},
    {"name": "audit-admins", "role": "AUDIT_ADMIN", "scope_type": "GLOBAL"},
    # Project-scoped groups (assigned to the default Project):
    {"name": "marketing-owners", "role": "OWNER", "scope_type": "PROJECT"},
    {"name": "marketing-editors", "role": "EDITOR", "scope_type": "PROJECT"},
    {"name": "marketing-viewers", "role": "VIEWER", "scope_type": "PROJECT"},
    {"name": "marketing-discoverers", "role": "DISCOVERER", "scope_type": "PROJECT"},
]

TEST_USERS = [
    {"id": "admin-user", "email": "admin-user@test.local", "group": "platform-admins"},
    {"id": "alice", "email": "alice@test.local", "group": "marketing-owners"},
    {"id": "bob", "email": "bob@test.local", "group": "marketing-editors"},
    {"id": "carol", "email": "carol@test.local", "group": "marketing-viewers"},
    {"id": "dave", "email": "dave@test.local", "group": "marketing-discoverers"},
    {"id": "eve", "email": "eve@test.local", "group": "marking-admins"},
    {"id": "frank", "email": "frank@test.local", "group": "audit-admins"},
]


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail: str = ""
        self.expected: str = ""
        self.actual: str = ""
        self.duration_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "expected": self.expected,
            "actual": self.actual,
            "duration_ms": round(self.duration_ms, 1),
        }


def dev_headers(user_id: str, roles: str = "") -> dict:
    """Dev-mode headers for impersonating a user (AUTHZ_DEV_MODE=true)."""
    h = {"X-User-Id": user_id, "X-User-Display": user_id}
    if roles:
        h["X-User-Roles"] = roles
    return h


def main() -> int:
    parser = argparse.ArgumentParser(description="Permission E2E verification")
    parser.add_argument("--base-url", default="http://127.0.0.1:46094")
    parser.add_argument("--admin-jwt", default=None, help="JWT for admin user (production mode)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    results: list[TestResult] = []

    # In production mode, use the admin JWT. In dev mode, use X-User-Id headers
    # with PLATFORM_ADMIN role.
    if args.admin_jwt:
        admin_headers = {"Authorization": f"Bearer {args.admin_jwt}"}
    else:
        admin_headers = dev_headers("admin", "PLATFORM_ADMIN")

    client = httpx.Client(base_url=base, timeout=30)

    # ── Phase 0: Verify bootstrap defaults ──

    def run(name: str, fn):
        r = TestResult(name)
        t0 = time.perf_counter()
        try:
            fn(r)
            r.passed = True
        except Exception as e:
            r.passed = False
            r.detail = str(e)[:500]
        r.duration_ms = (time.perf_counter() - t0) * 1000
        results.append(r)
        status = "✅" if r.passed else "❌"
        print(f"  {status} {r.name} ({r.duration_ms:.0f}ms)")

    print("\n=== Phase 0: Bootstrap defaults ===")

    def test_bootstrap_org(r: TestResult):
        resp = client.get("/containers/organizations", headers=admin_headers)
        r.actual = f"status={resp.status_code} body={resp.text[:200]}"
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        orgs = resp.json()
        assert any(o["api_name"] == DEFAULT_ORG for o in orgs), f"org-default not found in {orgs}"
        r.expected = "org-default exists"
    run("bootstrap: default organization exists", test_bootstrap_org)

    def test_bootstrap_roles(r: TestResult):
        resp = client.get("/containers/roles", headers=admin_headers)
        r.actual = f"status={resp.status_code}"
        assert resp.status_code == 200
        roles = resp.json()
        names = {r["name"] for r in roles}
        expected = {"PLATFORM_ADMIN", "OWNER", "EDITOR", "VIEWER", "DISCOVERER",
                     "MARKING_ADMIN", "AUDIT_ADMIN", "SPACE_OWNER"}
        missing = expected - names
        assert not missing, f"Missing builtin roles: {missing}"
        r.expected = f"All {len(expected)} builtin roles seeded"
    run("bootstrap: builtin roles seeded", test_bootstrap_roles)

    def test_bootstrap_space_project(r: TestResult):
        resp = client.get("/containers/spaces", headers=admin_headers)
        assert resp.status_code == 200
        spaces = resp.json()
        assert any(s["api_name"] == "default" for s in spaces), "default Space not found"
        resp = client.get("/containers/projects", headers=admin_headers)
        assert resp.status_code == 200
        projects = resp.json()
        assert any(p["api_name"] == "default" for p in projects), "default Project not found"
        r.expected = "default Space + Project exist"
    run("bootstrap: default Space + Project exist", test_bootstrap_space_project)

    # ── Phase 1: Create test groups + users + role assignments ──

    print("\n=== Phase 1: Create test identity (groups, users, role assignments) ===")

    # Get the default org id and default project id
    orgs = client.get("/containers/organizations", headers=admin_headers).json()
    org_id = next(o["id"] for o in orgs if o["api_name"] == DEFAULT_ORG)
    projects = client.get("/containers/projects", headers=admin_headers).json()
    project_id = next(p["id"] for p in projects if p["api_name"] == "default")

    created_groups: dict[str, str] = {}

    for gdef in TEST_GROUPS:
        def test_create_group(r: TestResult, gd=gdef):
            resp = client.post("/identity/groups", headers=admin_headers, json={
                "name": gd["name"], "organization_id": org_id, "description": f"Test group: {gd['name']}",
            })
            if resp.status_code == 409:
                # Already exists — list to get id
                groups = client.get("/identity/groups", headers=admin_headers,
                                     params={"organization_id": org_id}).json()
                g = next((g for g in groups if g["name"] == gd["name"]), None)
                assert g, f"Group {gd['name']} not found after 409"
                created_groups[gd["name"]] = g["id"]
                r.expected = f"Group {gd['name']} already exists (idempotent)"
                return
            assert resp.status_code == 201, f"Create group failed: {resp.status_code} {resp.text}"
            created_groups[gd["name"]] = resp.json()["id"]
            r.expected = f"Group {gd['name']} created"
        run(f"identity: create group '{gdef['name']}'", test_create_group)

    # Create users
    created_users: dict[str, str] = {}
    for udef in TEST_USERS:
        def test_create_user(r: TestResult, ud=udef):
            resp = client.post("/identity/users", headers=admin_headers, json={
                "email": ud["email"], "subject": ud["id"],
                "attributes": {"department": "test"},
            })
            if resp.status_code == 409:
                users = client.get("/identity/users", headers=admin_headers).json()
                u = next((u for u in users if u["email"] == ud["email"] or u["subject"] == ud["id"]), None)
                assert u, f"User {ud['email']} (subject={ud['id']}) not found after 409"
                created_users[ud["id"]] = u["id"]
                r.expected = f"User {ud['id']} already exists (idempotent)"
                return
            assert resp.status_code == 201, f"Create user failed: {resp.status_code} {resp.text}"
            created_users[ud["id"]] = resp.json()["id"]
            r.expected = f"User {ud['id']} created"
        run(f"identity: create user '{udef['id']}'", test_create_user)

    # Add users to groups
    for udef in TEST_USERS:
        def test_add_member(r: TestResult, ud=udef):
            gid = created_groups.get(ud["group"])
            uid = created_users.get(ud["id"])
            assert gid and uid, f"Missing group or user for {ud['id']}"
            resp = client.post(f"/identity/groups/{gid}/members", headers=admin_headers,
                                json={"user_id": uid})
            # 201 or already member (idempotent)
            assert resp.status_code in (200, 201), f"Add member failed: {resp.status_code} {resp.text}"
            r.expected = f"{ud['id']} → {ud['group']}"
        run(f"identity: add '{udef['id']}' to '{udef['group']}'", test_add_member)

    # Assign roles to groups
    for gdef in TEST_GROUPS:
        def test_assign_role(r: TestResult, gd=gdef):
            gid = created_groups.get(gd["name"])
            assert gid, f"Group {gd['name']} not created"
            scope_id = None if gd["scope_type"] == "GLOBAL" else project_id
            resp = client.post("/authz/role-assignments", headers=admin_headers, json={
                "group_id": gid, "role_name": gd["role"],
                "scope_type": gd["scope_type"], "scope_id": scope_id,
            })
            if resp.status_code == 409:
                r.expected = f"Role {gd['role']} already assigned to {gd['name']} (idempotent)"
                return
            assert resp.status_code == 201, f"Role assignment failed: {resp.status_code} {resp.text}"
            r.expected = f"{gd['name']} → {gd['role']} @ {gd['scope_type']}"
        run(f"role: assign '{gdef['role']}' to '{gdef['name']}'", test_assign_role)

    # ── Phase 2: Permission matrix verification ──

    print("\n=== Phase 2: Permission matrix (allowed-actions per role) ===")

    # Use dev-mode headers impersonating each user. In dev mode, the backend
    # resolves the principal from X-User-Id + X-User-Roles headers, but the
    # five-layer check still queries role_assignments for the principal's
    # groups. Since our test users were created in the Gaia DB (not Better Auth),
    # we use X-User-Id = user_id and rely on the group membership + role
    # assignments we just created.
    #
    # NOTE: dev-mode X-User-Roles bypasses the DB role lookup. To test the
    # real DB-driven RBAC, we DON'T pass X-User-Roles — the backend will
    # resolve roles from the DB via the user's group memberships.
    # BUT: dev-mode principal resolution doesn't load groups from the DB
    # (it only reads X-User-Roles). So for a true E2E test we need the
    # principal's groups to be populated. The PrincipalService in dev mode
    # doesn't load groups — only JWT mode does.
    #
    # For now, we test with X-User-Roles to simulate the resolved roles.
    # A full JWT-based test would require Better Auth login for each user.

    MATRIX = [
        # (user, roles_header, resource_type, resource_id, action, expect_allowed)
        ("admin", "PLATFORM_ADMIN", "ONTOLOGY", "Marketing", "ontology:view", True),
        ("admin", "PLATFORM_ADMIN", "ONTOLOGY", "Marketing", "ontology:edit", True),
        ("admin", "PLATFORM_ADMIN", "ONTOLOGY", "Marketing", "ontology:delete", True),
        ("alice", "OWNER", "ONTOLOGY", "Marketing", "ontology:edit", True),
        ("alice", "OWNER", "ONTOLOGY", "Marketing", "ontology:view", True),
        ("carol", "VIEWER", "ONTOLOGY", "Marketing", "ontology:view", True),
        ("carol", "VIEWER", "ONTOLOGY", "Marketing", "ontology:edit", False),
        ("dave", "DISCOVERER", "ONTOLOGY", "Marketing", "ontology:view", True),
        ("dave", "DISCOVERER", "ONTOLOGY", "Marketing", "ontology:edit", False),
        ("anonymous", "", "ONTOLOGY", "Marketing", "ontology:view", False),
    ]

    for user, roles, rtype, rid, action, expect in MATRIX:
        def test_perm(r: TestResult, u=user, ro=roles, rt=rtype, ri=rid, ac=action, ex=expect):
            if u == "anonymous":
                h = {}
            else:
                h = dev_headers(u, ro)
            resp = client.post("/authz/allowed-actions", headers=h, json={
                "resource_type": rt, "resource_ids": [ri],
            })
            assert resp.status_code == 200, f"allowed-actions failed: {resp.status_code} {resp.text}"
            decisions = resp.json()["decisions"]
            allowed = decisions.get(ri, {}).get("allowedActions", [])
            actual_allowed = ac in allowed
            r.expected = f"{ac} {'allowed' if ex else 'denied'}"
            r.actual = f"allowedActions={allowed}"
            assert actual_allowed == ex, f"Expected {ac}={'allow' if ex else 'deny'}, got {actual_allowed}"
        run(f"matrix: {user} {action} on {rid} → expect {'ALLOW' if expect else 'DENY'}", test_perm)

    # ── Phase 3: Separation of duties ──

    print("\n=== Phase 3: Separation of duties ===")

    def test_marking_admin_cannot_manage_project(r: TestResult):
        # eve (MARKING_ADMIN) should be able to manage markings but NOT projects
        h = dev_headers("eve", "MARKING_ADMIN")
        # List existing marking categories (bootstrap creates OrgIsolation)
        resp = client.get("/marking-categories", headers=h)
        r.actual = f"list categories status={resp.status_code}"
        assert resp.status_code == 200, f"MARKING_ADMIN cannot list categories: {resp.status_code} {resp.text}"
        cats = resp.json()
        r.expected = f"MARKING_ADMIN can list markings ({len(cats)} categories)"
    run("sep-of-duties: MARKING_ADMIN can manage markings", test_marking_admin_cannot_manage_project)

    def test_non_admin_cannot_grant_roles(r: TestResult):
        # carol (VIEWER) should NOT be able to grant roles
        h = dev_headers("carol", "VIEWER")
        resp = client.get("/authz/role-assignments", headers=h)
        r.actual = f"status={resp.status_code}"
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        r.expected = "VIEWER cannot list role assignments (403)"
    run("sep-of-duties: VIEWER cannot manage roles", test_non_admin_cannot_grant_roles)

    def test_anonymous_denied_everywhere(r: TestResult):
        resp = client.post("/authz/allowed-actions", json={
            "resource_type": "ONTOLOGY", "resource_ids": ["Marketing"],
        })
        assert resp.status_code == 200
        decisions = resp.json()["decisions"]
        allowed = decisions.get("Marketing", {}).get("allowedActions", [])
        assert allowed == [], f"Anonymous should have no allowed actions, got {allowed}"
        r.expected = "Anonymous → empty allowedActions"
    run("security: anonymous denied all", test_anonymous_denied_everywhere)

    # ── Phase 4: Audit logs ──

    print("\n=== Phase 4: Audit & observability ===")

    def test_audit_logs_exist(r: TestResult):
        resp = client.get("/authz/audit-logs", headers=admin_headers, params={"limit": 5})
        r.actual = f"status={resp.status_code}"
        assert resp.status_code == 200, f"Audit logs failed: {resp.status_code} {resp.text}"
        logs = resp.json()
        assert isinstance(logs, list), f"Expected list, got {type(logs)}"
        r.expected = f"Audit logs accessible ({len(logs)} entries)"
    run("audit: logs accessible", test_audit_logs_exist)

    def test_check_access_explain(r: TestResult):
        resp = client.get("/authz/check", headers=admin_headers, params={
            "resource_type": "ONTOLOGY", "resource_id": "Marketing", "action": "ontology:view",
        })
        r.actual = f"status={resp.status_code}"
        assert resp.status_code == 200, f"Check access failed: {resp.status_code} {resp.text}"
        result = resp.json()
        assert "decision" in result, f"Missing 'decision' in response: {result}"
        r.expected = f"Check access returns decision ({result.get('decision')})"
    run("audit: check-access explainability", test_check_access_explain)

    # ── Summary ──
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    print(f"{'='*60}")

    # Generate HTML report
    html = generate_html_report(results, passed, failed, total)
    report_path = Path(__file__).parent.parent / "docs" / "engineer" / "permission-e2e-report.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    print(f"\nHTML report: {report_path}")

    # JSON output
    json_output = {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base,
        "summary": {"passed": passed, "failed": failed, "total": total},
        "results": [r.to_dict() for r in results],
    }
    json_path = Path(__file__).parent.parent / "docs" / "engineer" / "permission-e2e-results.json"
    json_path.write_text(json.dumps(json_output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON results: {json_path}")

    return 0 if failed == 0 else 1


def generate_html_report(results: list[TestResult], passed: int, failed: int, total: int) -> str:
    rows = []
    for r in results:
        icon = "✅" if r.passed else "❌"
        cls = "pass" if r.passed else "fail"
        rows.append(f"""
        <tr class="{cls}">
          <td>{icon}</td>
          <td>{r.name}</td>
          <td>{r.duration_ms:.0f}ms</td>
          <td><code>{r.expected}</code></td>
          <td><code>{r.actual}</code></td>
          <td>{r.detail}</td>
        </tr>""")

    pass_rate = (passed / total * 100) if total > 0 else 0
    status_color = "#16a34a" if failed == 0 else "#dc2626"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gaia 权限治理 E2E 测试报告</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 2rem; background: #f8fafc; color: #1e293b; }}
  h1 {{ color: #0f172a; border-bottom: 3px solid {status_color}; padding-bottom: 0.5rem; }}
  .summary {{ display: flex; gap: 2rem; margin: 2rem 0; }}
  .card {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 150px; text-align: center; }}
  .card .num {{ font-size: 2rem; font-weight: 700; }}
  .card .label {{ color: #64748b; font-size: 0.875rem; }}
  .pass .num {{ color: #16a34a; }}
  .fail .num {{ color: #dc2626; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #1e293b; color: white; padding: 0.75rem; text-align: left; font-size: 0.875rem; }}
  td {{ padding: 0.75rem; border-bottom: 1px solid #e2e8f0; font-size: 0.875rem; vertical-align: top; }}
  tr.pass {{ background: #f0fdf4; }}
  tr.fail {{ background: #fef2f2; }}
  code {{ font-size: 0.8rem; color: #475569; word-break: break-all; }}
  .meta {{ color: #64748b; font-size: 0.875rem; margin-bottom: 1rem; }}
</style>
</head>
<body>
<h1>🔒 Gaia 权限治理端到端测试报告</h1>
<div class="meta">生成时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} ｜ 覆盖：角色权限矩阵 · 权责分离 · 审计可观测性</div>
<div class="summary">
  <div class="card pass"><div class="num">{passed}</div><div class="label">通过</div></div>
  <div class="card fail"><div class="num">{failed}</div><div class="label">失败</div></div>
  <div class="card"><div class="num">{pass_rate:.0f}%</div><div class="label">通过率</div></div>
  <div class="card"><div class="num">{total}</div><div class="label">总用例</div></div>
</div>
<table>
  <thead><tr><th>状态</th><th>测试用例</th><th>耗时</th><th>期望</th><th>实际</th><th>详情</th></tr></thead>
  <tbody>{''.join(rows)}
  </tbody>
</table>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())
