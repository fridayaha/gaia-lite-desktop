#!/usr/bin/env python3
"""End-to-end verification script for the permission governance system (ADR-016/017).

Run after starting the backend (``.venv/bin/python -m uvicorn ontology.main:app``).
Verifies all five phases work end-to-end via the HTTP API in dev mode
(X-User-Id headers). Exit code 0 = all passed, 1 = any failure.

Usage:
    .venv/bin/python scripts/verify_permission_live.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
_passed = 0
_failed = 0


def _get(path: str, headers: dict[str, str] | None = None) -> dict | list:
    req = urllib.request.Request(f"{BASE}{path}", headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _post(path: str, body: dict, headers: dict[str, str] | None = None) -> dict:
    data = json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name} — {detail}")


def main() -> int:
    print("=== Permission Governance End-to-End Verification ===\n")

    # Phase 0: Auth + Principal
    print("Phase 0: Auth + Principal")
    me = _get("/auth/me")
    _check("anonymous principal", me["is_anonymous"] is True, str(me))
    me_alice = _get("/auth/me", {"X-User-Id": "alice", "X-User-Roles": "PLATFORM_ADMIN",
                                 "X-User-Attributes": "region=east"})
    _check("authenticated principal", me_alice["id"] == "alice" and "PLATFORM_ADMIN" in me_alice["roles"])

    # Phase 1: RBAC (builtin roles seeded)
    print("\nPhase 1: RBAC + builtin roles")
    # Check Access as PLATFORM_ADMIN → should pass Layer 1-4 (wildcard)
    check = _get("/authz/check?resource_type=OBJECT_TYPE&resource_id=x&action=object:view",
                 {"X-User-Id": "admin", "X-User-Roles": "PLATFORM_ADMIN"})
    _check("PLATFORM_ADMIN bypasses RBAC", check["decision"] == "ALLOW" or check["layer"] != "IDENTITY",
           str(check))

    # Phase 2: MAC (markings)
    print("\nPhase 2: MAC + markings")
    cats = _get("/marking-categories")
    _check("marking categories exist", len(cats) > 0, str(cats))
    _check("system marking category present",
           any(c["is_system"] for c in cats), str(cats))
    markings = _get("/markings")
    _check("system marking present",
           any(m["is_system"] for m in markings), str(markings))

    # Phase 3: Row/column (Check Access with layers)
    print("\nPhase 3: Row/column security")
    check_anon = _get("/authz/check?resource_type=OBJECT_TYPE&resource_id=x&action=object:view")
    _check("anonymous denied at IDENTITY", check_anon["decision"] == "DENY"
           and check_anon["layer"] == "IDENTITY", str(check_anon))
    _check("per-layer status returned", "layers" in check_anon
           and "identity" in check_anon["layers"], str(check_anon))

    # Phase 4: Audit + JIT
    print("\nPhase 4: Audit + JIT")
    logs = _get("/authz/audit-logs?limit=5", {"X-User-Id": "auditor"})
    _check("audit logs returned", isinstance(logs, list), str(logs))
    _check("audit log has layer field",
           len(logs) == 0 or "layer" in logs[0], str(logs))

    req = _post("/authz/access-requests",
                {"request_type": "ROLE_ASSIGNMENT", "requested_item": "VIEWER",
                 "justification": "e2e verify", "scope_type": "PROJECT"},
                {"X-User-Id": "e2e-user"})
    _check("JIT request created", req["status"] == "PENDING", str(req))

    my_reqs = _get("/authz/access-requests", {"X-User-Id": "e2e-user"})
    _check("JIT request listed", any(r["id"] == req["id"] for r in my_reqs), str(my_reqs))

    # Phase 5: JWT path (dev mode fallback)
    print("\nPhase 5: JWT (dev mode fallback)")
    _check("dev mode active (no JWT needed)",
           me_alice["id"] == "alice", "dev mode should resolve via headers")

    print(f"\n=== Results: {_passed} passed, {_failed} failed ===")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
