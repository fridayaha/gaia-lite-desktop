"""0.8.110 用户邮箱/手机「未认证 / 已认证」两态模型集成测试 — 真 DB 验证。

测试覆盖：
1. admin create 不传 email → 201 email=null
2. 两个未认证 user 同 email → 都 201
3. admin 发起邮箱认证 → 输入正确 code → email_verified=True
4. 输入错 code → 400 invalid_code, email_verified 仍 False
5. 已认证用户再调 verify → 200 幂等
6. 用户 A 已认证 a@x.com，用户 B 改 email 到 a@x.com 后尝试认证 → 409 email_already_verified
7. admin update 改 email → email_verified=False
8. reset_password 用未认证 email → 404 user_not_found（sent=True 但不发码）
9. 两个未认证 user 同 email 不冲突；一个认证后另一个尝试认证失败
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import hash_password
from app.models import User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    yield session

    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_and_client(db, monkeypatch):
    """admin user + ASGITransport client，mock 掉发码函数捕获 code。"""
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db
    import app.api.users as users_mod
    import app.core.auth as auth

    admin = User(
        username=f"admin_{uuid.uuid4().hex[:8]}",
        email=f"admin_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: True)

    # 用 list 收集发码时实际使用的 code（mock _send_verification_code 整体）
    sent_codes: dict[str, str] = {}  # target -> code

    async def fake_send(db, target, channel, target_value, purpose, actor):
        """绕过 issue_code 和 sender — 直接往 DB 写一条 code 记录，code='123456'。"""
        from app.models import VerificationCode
        from datetime import timedelta
        code = "123456"  # 固定测试 code
        sent_codes[target_value] = code
        record = VerificationCode(
            channel=channel,
            target=target_value,
            purpose=purpose,
            code_hash=hash_password(code),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            ip=None,
        )
        db.add(record)
        await db.flush()
        await db.commit()
        return "sent"

    monkeypatch.setattr(users_mod, "_send_verification_code", fake_send)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield admin, c, sent_codes

    app.dependency_overrides.clear()
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": admin.id})
    await db.commit()


# ── 1. admin create 不传 email ──────────────────────────────


@pytest.mark.asyncio
async def test_create_user_no_email_success(admin_and_client, db):
    """admin create 不传 email → 201, email=None。"""
    admin, client, _ = admin_and_client
    resp = await client.post(
        "/api/manager/users",
        json={
            "username": f"no_email_{uuid.uuid4().hex[:8]}",
            "password": "Admin@2026",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] is None
    await db.execute(
        text("DELETE FROM users WHERE id = :u"), {"u": resp.json()["id"]}
    )
    await db.commit()


# ── 2. 两个未认证 user 同 email → 都 201 ───────────────────


@pytest.mark.asyncio
async def test_create_user_unverified_email_can_duplicate(admin_and_client, db):
    """两个用户都填同一未认证 email → 都 201。"""
    _, client, _ = admin_and_client
    same_email = f"shared_{uuid.uuid4().hex[:8]}@example.com"

    for i in range(2):
        resp = await client.post(
            "/api/manager/users",
            json={
                "username": f"dup_{i}_{uuid.uuid4().hex[:8]}",
                "email": same_email,
                "password": "Admin@2026",
            },
        )
        assert resp.status_code == 201, f"#{i}: {resp.text}"
        assert resp.json()["email_verified"] is False
        await db.execute(
            text("DELETE FROM users WHERE id = :u"), {"u": resp.json()["id"]}
        )
    await db.commit()


# ── 3. admin 发起邮箱认证 + 正确 code → email_verified=True ──


@pytest.mark.asyncio
async def test_verify_email_success_sets_verified_true(admin_and_client, db):
    """admin create user (有 email) → initiate-email-verify → 输入 123456 → verified=True。"""
    admin, client, sent_codes = admin_and_client
    user_email = f"verify_ok_{uuid.uuid4().hex[:8]}@example.com"
    create = await client.post(
        "/api/manager/users",
        json={
            "username": f"verify_ok_{uuid.uuid4().hex[:8]}",
            "email": user_email,
            "password": "Admin@2026",
        },
    )
    assert create.status_code == 201, create.text
    user_id = create.json()["id"]

    init = await client.post(f"/api/manager/users/{user_id}/initiate-email-verify")
    assert init.status_code == 200, init.text
    assert init.json()["sent"] is True

    code = sent_codes[user_email]
    verify = await client.post(
        f"/api/manager/users/{user_id}/verify-email", json={"code": code}
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["email_verified"] is True

    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ── 4. 错 code → 400 invalid_code ──────────────────────────


@pytest.mark.asyncio
async def test_verify_email_wrong_code_returns_400(admin_and_client, db):
    """输入错 code → 400 invalid_code, email_verified 仍 False。"""
    admin, client, sent_codes = admin_and_client
    user_email = f"wrong_{uuid.uuid4().hex[:8]}@example.com"
    create = await client.post(
        "/api/manager/users",
        json={
            "username": f"wrong_{uuid.uuid4().hex[:8]}",
            "email": user_email,
            "password": "Admin@2026",
        },
    )
    user_id = create.json()["id"]

    await client.post(f"/api/manager/users/{user_id}/initiate-email-verify")
    verify = await client.post(
        f"/api/manager/users/{user_id}/verify-email", json={"code": "000000"}
    )
    assert verify.status_code == 400, verify.text
    assert verify.json()["detail"] == "invalid_code"
    # 400 响应只返回 detail，不返回 user object；从 DB 直接查
    from sqlalchemy import select as _sel
    u = (await db.execute(_sel(User).where(User.id == user_id))).scalar_one()
    assert u.email_verified is False

    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ── 5. 已认证用户再调 verify → 200 幂等 ────────────────────


@pytest.mark.asyncio
async def test_verify_email_already_verified_returns_200_idempotent(admin_and_client, db):
    """已认证用户再调 verify-email → 200 幂等。"""
    admin, client, sent_codes = admin_and_client
    user_email = f"idem_{uuid.uuid4().hex[:8]}@example.com"
    create = await client.post(
        "/api/manager/users",
        json={
            "username": f"idem_{uuid.uuid4().hex[:8]}",
            "email": user_email,
            "password": "Admin@2026",
        },
    )
    user_id = create.json()["id"]

    await client.post(f"/api/manager/users/{user_id}/initiate-email-verify")
    await client.post(
        f"/api/manager/users/{user_id}/verify-email", json={"code": sent_codes[user_email]}
    )
    # 再调一次（不发起发码也行，因为已 verified 会跳过 verify_code 直接幂等返回）
    again = await client.post(
        f"/api/manager/users/{user_id}/verify-email", json={"code": "999999"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["email_verified"] is True

    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ── 6. 用户 A 已认证 a@x.com，用户 B 改 email 到 a@x.com → 认证失败 409 ──


@pytest.mark.asyncio
async def test_verify_email_collision_returns_409(admin_and_client, db):
    """A 已认证 a@x.com，B 改 email 到 a@x.com 后尝试认证 → 409 email_already_verified。"""
    admin, client, sent_codes = admin_and_client
    shared_email = f"coll_{uuid.uuid4().hex[:8]}@example.com"

    # 创建 A + 认证
    create_a = await client.post(
        "/api/manager/users",
        json={
            "username": f"coll_a_{uuid.uuid4().hex[:8]}",
            "email": shared_email,
            "password": "Admin@2026",
        },
    )
    a_id = create_a.json()["id"]
    await client.post(f"/api/manager/users/{a_id}/initiate-email-verify")
    await client.post(
        f"/api/manager/users/{a_id}/verify-email", json={"code": sent_codes[shared_email]}
    )

    # 创建 B（同 email 未认证）+ 尝试认证 → 409
    create_b = await client.post(
        "/api/manager/users",
        json={
            "username": f"coll_b_{uuid.uuid4().hex[:8]}",
            "email": shared_email,
            "password": "Admin@2026",
        },
    )
    b_id = create_b.json()["id"]
    # 这里要先清空 sent_codes 否则 initiate 时 sent_codes[shared_email] 被覆盖
    # （不过 initiate 不读 sent_codes，只 fake_send 会写）
    await client.post(f"/api/manager/users/{b_id}/initiate-email-verify")
    code_for_b = sent_codes[shared_email]
    verify_b = await client.post(
        f"/api/manager/users/{b_id}/verify-email", json={"code": code_for_b}
    )
    # verify_code 会成功（因为 B 也有 code），但 endpoint pre-check 会检测到 A 已认证 → 409
    # 不过：DB 上 B 的 code 与 A 的 code hash 不同（因为是不同 issue_code 调用生成的）
    # 但 pre-check 在 verify_code 之前检查 collision → 应返回 409
    assert verify_b.status_code == 409, verify_b.text
    assert verify_b.json()["detail"] == "email_already_verified"

    await db.execute(
        text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": a_id, "b": b_id}
    )
    await db.commit()


# ── 7. admin update 改 email → email_verified=False ────────


@pytest.mark.asyncio
async def test_update_email_resets_verified_false(admin_and_client, db):
    """admin update 把已认证 email 改新值 → email_verified=False。"""
    admin, client, sent_codes = admin_and_client
    user_email = f"upd_{uuid.uuid4().hex[:8]}@example.com"
    create = await client.post(
        "/api/manager/users",
        json={
            "username": f"upd_{uuid.uuid4().hex[:8]}",
            "email": user_email,
            "password": "Admin@2026",
        },
    )
    user_id = create.json()["id"]
    # 先认证
    await client.post(f"/api/manager/users/{user_id}/initiate-email-verify")
    await client.post(
        f"/api/manager/users/{user_id}/verify-email", json={"code": sent_codes[user_email]}
    )
    # 改 email
    new_email = f"updated_{uuid.uuid4().hex[:8]}@example.com"
    upd = await client.put(
        f"/api/manager/users/{user_id}", json={"email": new_email}
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["email"] == new_email
    assert upd.json()["email_verified"] is False

    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ── 8. reset_password 用未认证 email → sent=True 但不发码 ────


@pytest.mark.asyncio
async def test_reset_password_only_works_for_verified_email(admin_and_client, db, monkeypatch):
    """未认证 email 用户调 reset-password 发码 → sent=True（防探测），但不真发码 + audit 记 user_not_found。"""
    admin, client, _ = admin_and_client
    user_email = f"reset_{uuid.uuid4().hex[:8]}@example.com"
    create = await client.post(
        "/api/manager/users",
        json={
            "username": f"reset_{uuid.uuid4().hex[:8]}",
            "email": user_email,
            "password": "Admin@2026",
        },
    )
    user_id = create.json()["id"]
    assert create.json()["email_verified"] is False

    # 直接调 verification-code/send endpoint（不需要登录，但需 captcha）
    from app.services.captcha_service import captcha_service as captcha_svc

    async def _true(*a, **kw):
        return True

    monkeypatch.setattr(captcha_svc, "verify", _true)
    resp = await client.post(
        "/api/manager/auth/verification-code/send",
        json={
            "channel": "email",
            "target": user_email,
            "purpose": "reset_password",
            "captcha_id": "any",
            "captcha_answer": "any",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent"] is True  # 防探测：不告诉用户该 email 是否存在
    # 但实际没有 active provider 也会 sent=true，关键是查不到 user — 这里检查 verification_codes 表里没新增
    from app.models import VerificationCode
    codes = (await db.execute(
        select(VerificationCode).where(
            VerificationCode.target == user_email,
            VerificationCode.purpose == "reset_password",
        )
    )).scalars().all()
    # 因为 user.email_verified=False → 走 user_not_found 路径，不发码也不落 code
    assert len(codes) == 0, f"未认证 email 不应落 code，实际有 {len(codes)} 条"

    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db.commit()


# ── 9. partial unique index 只在 verified=TRUE 时生效 ────────


@pytest.mark.asyncio
async def test_partial_unique_index_only_on_verified(admin_and_client, db):
    """两个未认证 user 同 email 不冲突；一个认证后另一个尝试认证失败。

    这与 test 6 类似，但更聚焦 DB 层 partial unique index 行为。
    """
    admin, client, sent_codes = admin_and_client
    shared_email = f"partial_{uuid.uuid4().hex[:8]}@example.com"

    # 创建两个用户填同一 email（未认证，应都成功）
    create_a = await client.post(
        "/api/manager/users",
        json={
            "username": f"part_a_{uuid.uuid4().hex[:8]}",
            "email": shared_email,
            "password": "Admin@2026",
        },
    )
    create_b = await client.post(
        "/api/manager/users",
        json={
            "username": f"part_b_{uuid.uuid4().hex[:8]}",
            "email": shared_email,
            "password": "Admin@2026",
        },
    )
    a_id = create_a.json()["id"]
    b_id = create_b.json()["id"]

    # A 认证成功
    await client.post(f"/api/manager/users/{a_id}/initiate-email-verify")
    ok = await client.post(
        f"/api/manager/users/{a_id}/verify-email", json={"code": sent_codes[shared_email]}
    )
    assert ok.status_code == 200
    assert ok.json()["email_verified"] is True

    # B 尝试认证 → 409
    await client.post(f"/api/manager/users/{b_id}/initiate-email-verify")
    fail = await client.post(
        f"/api/manager/users/{b_id}/verify-email", json={"code": sent_codes[shared_email]}
    )
    assert fail.status_code == 409, fail.text
    assert fail.json()["detail"] == "email_already_verified"

    await db.execute(
        text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": a_id, "b": b_id}
    )
    await db.commit()
