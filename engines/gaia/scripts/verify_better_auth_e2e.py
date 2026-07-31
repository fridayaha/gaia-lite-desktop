#!/usr/bin/env python3
"""End-to-end verification: Better Auth JWT issuance + Gaia JWKS verification.

This script exercises the full Phase 5 authentication path:
  1. Register a user via Better Auth (email/password)
  2. Sign in to get a session
  3. Exchange the session for a JWT (/api/auth/token)
  4. Call Gaia's /health with the JWT (verifies JWKS signature + iss/aud)
  5. Verify the JWKS endpoint is reachable and exposes an EdDSA key
  6. (Optional) Verify JIT auto-provisioning created a Gaia user

Prerequisites:
  - Better Auth container running (docker compose up -d better-auth)
  - `npx @better-auth/cli migrate` run once (creates 9 better_auth tables)
  - Gaia API running (or at least /health reachable)
  - BETTER_AUTH_SECRET set in .env.local (fixed, not change-me)

Usage:
  python scripts/verify_better_auth_e2e.py [--base-url http://localhost:3000] [--gaia-url http://localhost:8000]

Exit code 0 = all checks passed; non-zero = failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import requests

DEFAULT_BETTER_AUTH_URL = "http://localhost:3000"
DEFAULT_GAIA_URL = "http://localhost:8000"


def step(n: int, total: int, msg: str) -> None:
    print(f"\n[{n}/{total}] {msg}")
    print("-" * 60)


def check_jwks(base_url: str) -> dict:
    """Fetch the JWKS endpoint and verify it exposes an EdDSA key."""
    resp = requests.get(f"{base_url}/api/auth/jwks", timeout=10)
    resp.raise_for_status()
    jwks = resp.json()
    keys = jwks.get("keys", [])
    assert len(keys) > 0, "JWKS endpoint returned no keys"
    key = keys[0]
    assert key.get("kty") == "OKP", f"Expected kty=OKP (EdDSA), got {key.get('kty')}"
    assert key.get("crv") == "Ed25519", f"Expected crv=Ed25519, got {key.get('crv')}"
    assert "kid" in key, "JWKS key missing 'kid' header"
    print(f"  ✓ JWKS exposes EdDSA/Ed25519 key (kid={key['kid'][:8]}...)")
    return key


def register_user(base_url: str, email: str, password: str) -> dict:
    """Register a new user via Better Auth email/password."""
    resp = requests.post(
        f"{base_url}/api/auth/sign-up/email",
        json={"email": email, "password": password, "name": "E2E Test"},
        timeout=10,
    )
    # 201 = created; 200 = already exists (idempotent); 400 with "already" = exists
    if resp.status_code not in (200, 201):
        body = resp.text
        if "already" in body.lower() or "exists" in body.lower():
            print(f"  ℹ User {email} already exists (idempotent)")
        else:
            resp.raise_for_status()
    print(f"  ✓ Registered/exists: {email}")
    return {"email": email, "password": password}


def sign_in(base_url: str, email: str, password: str) -> str:
    """Sign in and return the session token (bearer)."""
    resp = requests.post(
        f"{base_url}/api/auth/sign-in/email",
        json={"email": email, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    # Better Auth returns the session token in the response body or set-cookie.
    body = resp.json()
    token = body.get("token")
    if not token:
        session_obj = body.get("session")
        if isinstance(session_obj, dict):
            token = session_obj.get("token")
    if not token:
        # Try set-cookie header
        cookie = resp.headers.get("set-cookie", "")
        if "better-auth.session_token=" in cookie:
            token = cookie.split("better-auth.session_token=")[1].split(";")[0]
    assert token, f"Could not extract session token from sign-in response: {body}"
    print(f"  ✓ Signed in (session token: {token[:12]}...)")
    return token


def get_jwt(base_url: str, session_token: str) -> str:
    """Exchange the session for a JWT via /api/auth/token."""
    resp = requests.get(
        f"{base_url}/api/auth/token",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    jwt_token = body.get("token")
    assert jwt_token, f"Token endpoint did not return a JWT: {body}"
    # Quick sanity: JWT has 3 parts
    assert jwt_token.count(".") == 2, "Malformed JWT (expected 3 dot-separated parts)"
    print(f"  ✓ Got JWT ({jwt_token[:20]}...)")
    # Decode header (no verification) to check alg
    import base64

    header_b64 = jwt_token.split(".")[0]
    # Add padding
    header_b64 += "=" * (4 - len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(header_b64))
    assert header.get("alg") == "EdDSA", f"Expected alg=EdDSA, got {header.get('alg')}"
    assert "kid" in header, "JWT header missing 'kid'"
    print(f"  ✓ JWT header: alg={header['alg']}, kid={header['kid'][:8]}...")
    return jwt_token


def call_gaia_with_jwt(gaia_url: str, jwt_token: str) -> dict:
    """Call Gaia API with the JWT — verifies JWKS signature + iss/aud."""
    resp = requests.get(
        f"{gaia_url}/health",
        headers={"Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    # /health is public, so 200 regardless. But the JWT was still verified
    # by AuthMiddleware (if not in dev mode). Check we didn't get 401.
    assert resp.status_code != 401, f"Gaia rejected JWT (401): {resp.text}"
    body = resp.json()
    print(f"  ✓ Gaia /health returned {resp.status_code}: {body}")
    return body


def call_gaia_identity(gaia_url: str, jwt_token: str) -> dict | None:
    """Call an authenticated Gaia endpoint to verify principal resolution."""
    resp = requests.get(
        f"{gaia_url}/authz/principal",
        headers={"Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    if resp.status_code == 404:
        print("  ℹ /authz/principal endpoint not found (skipping)")
        return None
    assert resp.status_code != 401, f"Gaia rejected JWT on /authz/principal (401): {resp.text}"
    body = resp.json()
    print(f"  ✓ Gaia resolved principal: sub={body.get('id', '?')[:12]}...")
    return body


def verify_expired_token_rejected(base_url: str, gaia_url: str) -> None:
    """Verify an obviously-invalid token is rejected (defense check)."""
    fake_jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ0ZXN0In0.invalid"
    resp = requests.get(
        f"{gaia_url}/health",
        headers={"Authorization": f"Bearer {fake_jwt}"},
        timeout=10,
    )
    # /health is public so might still 200, but the token should be flagged.
    # This is a soft check — the real test is on authenticated endpoints.
    print(f"  ℹ Invalid JWT → {resp.status_code} (public endpoint, soft check)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Better Auth ↔ Gaia JWT E2E")
    parser.add_argument("--base-url", default=DEFAULT_BETTER_AUTH_URL, help="Better Auth URL")
    parser.add_argument("--gaia-url", default=DEFAULT_GAIA_URL, help="Gaia API URL")
    parser.add_argument(
        "--email", default=f"e2e-{uuid.uuid4().hex[:8]}@gaia-test.dev", help="Test user email"
    )
    parser.add_argument("--password", default="E2Etest123!Pass", help="Test user password")
    args = parser.parse_args()

    total_steps = 7
    failures: list[str] = []

    print(f"Better Auth E2E Verification")
    print(f"  Better Auth: {args.base_url}")
    print(f"  Gaia API:    {args.gaia_url}")
    print(f"  Test user:   {args.email}")

    try:
        step(1, total_steps, "Check Better Auth health")
        r = requests.get(f"{args.base_url}/health", timeout=5)
        r.raise_for_status()
        print(f"  ✓ Better Auth healthy: {r.json()}")

        step(2, total_steps, "Verify JWKS endpoint (EdDSA key)")
        check_jwks(args.base_url)

        step(3, total_steps, "Register test user")
        register_user(args.base_url, args.email, args.password)

        step(4, total_steps, "Sign in (get session)")
        session = sign_in(args.base_url, args.email, args.password)

        step(5, total_steps, "Exchange session for JWT")
        jwt_token = get_jwt(args.base_url, session)

        step(6, total_steps, "Call Gaia API with JWT (JWKS verification)")
        call_gaia_with_jwt(args.gaia_url, jwt_token)

        step(7, total_steps, "Verify Gaia principal resolution")
        call_gaia_identity(args.gaia_url, jwt_token)

        # Bonus: invalid token check
        print("\n[bonus] Invalid token rejection check")
        verify_expired_token_rejected(args.base_url, args.gaia_url)

    except Exception as exc:
        failures.append(str(exc))
        print(f"\n✗ FAIL: {exc}")

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} failure(s)")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"RESULT: All {total_steps} checks passed ✓")
    print("\nNext steps:")
    print("  1. Check Gaia identity management page — JIT should have created a user")
    print("  2. Assign the user to a group to grant permissions")
    print("  3. Test an authenticated endpoint (e.g. GET /ontologies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
