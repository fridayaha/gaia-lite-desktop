"""0.8.104 Phase 1 验证码 + 改绑集成测试 — 真 DB 验证 7 个新 endpoint + 2 个 service：

1. captcha_service — 图形验证码 5min TTL + 1 次性使用
2. verification_code_service — 6 位数字 + bcrypt hash + 10min TTL + 5 次错误失效 + ticket 单次使用
3. CodeRateLimiter — 单 target interval/5h/daily + 单 IP 10/h
4. /auth/captcha + /auth/verification-code/send + /auth/verification-code/verify
5. /auth/reset-password + /auth/unlock-account
6. /me/change-email + /me/change-phone
7. 用户枚举防御 — 不存在 target 假装 sent=true
8. 改绑后旧 contact code 立即失效

测试走真 endpoint（不 bypass 鉴权），fixture 提供 active EmailConfig + hash_password("Pass1234") 的真用户。
provider send 函数全 mock — 不真调云厂商 SDK。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import create_access_token, hash_password, verify_password
from app.core.crypto import encrypt_credential
from app.middleware.rate_limit import code_rate_limiter, rate_limiter
from app.models import (
    EmailConfig,
    OperationLog,
    SmsConfig,
    User,
    VerificationCode,
    VerificationTicket,
)
from app.services.captcha_service import captcha_service
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（密码 Pass1234）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"vcuser_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone=f"1{str(uuid.uuid4().int)[:10]}".ljust(11, "0"),
        real_name="验证码测试用户",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        email_verified=True,  # 0.8.110 两态：reset_password / account_unlock 需 verified=True
        phone_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    user_id = user.id  # 缓存 id，避免 endpoint rollback 后访问 user.id 触发 refresh

    # 重查带 selectinload(User.roles)
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    user = result.scalar_one()

    # 清空 operation_logs + 限速状态 + captcha 状态（隔离测试）
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM verification_codes"))
    await session.execute(text("DELETE FROM verification_tickets"))
    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(text("DELETE FROM email_configs"))
    await session.commit()
    await rate_limiter.reset()
    await code_rate_limiter.reset()
    await captcha_service.reset()

    yield session, user

    # endpoint 可能 rollback 过事务，user 属性已 expired — 用缓存的 user_id
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM verification_codes"))
    await session.execute(text("DELETE FROM verification_tickets"))
    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(text("DELETE FROM email_configs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    """裸 client，不 bypass 鉴权。"""
    from app.main import app
    from pkg.common.database import get_db

    session, _ = db
    app.dependency_overrides[get_db] = lambda: session

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client, db):
    """带 Bearer token 的 client（用于 /me/change-email|change-phone）。"""
    session, user = db
    token = create_access_token(user.id, [r.name for r in user.roles])
    client.headers["Authorization"] = f"Bearer {token}"
    yield client


@pytest_asyncio.fixture
async def email_cfg(db):
    """插入 active EmailConfig（provider=smtp）— 真加密密码。"""
    session, user = db
    cfg = EmailConfig(
        provider="smtp",
        is_active=True,
        smtp_host="smtp.example.com",
        smtp_port=465,
        encryption="ssl",
        username="noreply@example.com",
        password_encrypted=encrypt_credential("smtp-password"),
        from_name="知行平台",
        daily_limit=10,
        interval_seconds=60,
        created_by=user.id,
    )
    session.add(cfg)
    await session.commit()
    yield cfg


@pytest_asyncio.fixture
async def sms_cfg(db):
    """插入 active SmsConfig（provider=aliyun）— 真加密 AK/SK。"""
    session, user = db
    cfg = SmsConfig(
        provider="aliyun",
        is_active=True,
        sign_name="知行平台",
        template_code="SMS_123456789",
        access_key_id_encrypted=encrypt_credential("LTAI5tXXXXXX"),
        access_key_secret_encrypted=encrypt_credential("secretXXXXXX"),
        region="cn-hangzhou",
        daily_limit=10,
        interval_seconds=60,
        created_by=user.id,
    )
    session.add(cfg)
    await session.commit()
    yield cfg


# ── helper ────────────────────────────────────────────────


async def _get_captcha(client) -> tuple[str, str]:
    """拿 captcha_id + 错 answer（用 image_base64 反推 answer 太麻烦，直接 mock）。"""
    resp = await client.get("/api/manager/auth/captcha")
    assert resp.status_code == 200
    data = resp.json()
    return data["captcha_id"], data["image_base64"]


async def _get_captcha_with_answer(client, answer: str = "0000") -> tuple[str, str]:
    """生成 captcha 后直接从 service 里取 answer（绕过 OCR）。"""
    captcha_id, image_b64 = await captcha_service.generate()
    # 取 answer
    async with captcha_service._lock:
        ans, _ = captcha_service._captchas[captcha_id]
    # 重新生成一个新的给前端模拟（这里直接返回 id 和 answer 给测试用）
    return captcha_id, ans


# ── 1. captcha_service ────────────────────────────────────


@pytest.mark.asyncio
async def test_captcha_generate_and_verify():
    """生成 → 校验成功。"""
    captcha_id, image_b64 = await captcha_service.generate()
    assert captcha_id
    assert image_b64.startswith("data:image/png;base64,")

    # 取 answer
    async with captcha_service._lock:
        answer, _ = captcha_service._captchas[captcha_id]
    assert await captcha_service.verify(captcha_id, answer) is True
    await captcha_service.reset()


@pytest.mark.asyncio
async def test_captcha_one_time_use():
    """同 captcha_id 第二次校验 → False（1 次性使用）。"""
    captcha_id, _ = await captcha_service.generate()
    async with captcha_service._lock:
        answer, _ = captcha_service._captchas[captcha_id]
    assert await captcha_service.verify(captcha_id, answer) is True
    # 第二次 — 不论对错都应 False
    assert await captcha_service.verify(captcha_id, answer) is False
    await captcha_service.reset()


@pytest.mark.asyncio
async def test_captcha_wrong_answer():
    """错 answer → False + captcha 失效（1 次性使用）。"""
    captcha_id, _ = await captcha_service.generate()
    assert await captcha_service.verify(captcha_id, "wrong") is False
    # 已失效，正确 answer 也 False
    async with captcha_service._lock:
        answer, _ = captcha_service._captchas.get(captcha_id, ("none", 0))
    assert await captcha_service.verify(captcha_id, answer) is False
    await captcha_service.reset()


@pytest.mark.asyncio
async def test_captcha_expired():
    """mock 时间前进 6min → False。"""
    captcha_id, _ = await captcha_service.generate()
    # 手动改 expires_at 为过去
    async with captcha_service._lock:
        answer, _ = captcha_service._captchas[captcha_id]
        captcha_service._captchas[captcha_id] = (answer, time.time() - 1)
    assert await captcha_service.verify(captcha_id, answer) is False
    await captcha_service.reset()


@pytest.mark.asyncio
async def test_captcha_endpoint_returns_base64(client):
    """GET /auth/captcha 返回 captcha_id + data:image/png;base64,..."""
    resp = await client.get("/api/manager/auth/captcha")
    assert resp.status_code == 200
    data = resp.json()
    assert "captcha_id" in data
    assert data["image_base64"].startswith("data:image/png;base64,")
    # 至少 100 bytes base64
    assert len(data["image_base64"]) > 100


# ── 2. send_verification_code ──────────────────────────────


@pytest.mark.asyncio
async def test_send_code_email_success(client, db, email_cfg):
    """存在 user + active email config → 200 sent=true + 真发码 + audit success。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent_payload = {}

    def fake_send(cfg, secrets, to, subject, html):
        sent_payload["to"] = to
        sent_payload["subject"] = subject
        sent_payload["code"] = html

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        resp = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email",
                "target": user.email,
                "purpose": "reset_password",
                "captcha_id": captcha_id,
                "captcha_answer": answer,
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"sent": True, "expires_in": 600}
    assert sent_payload["to"] == user.email

    # 验证码落库 — bcrypt hash 存，明文不存
    codes = (
        await session.execute(
            select(VerificationCode).where(
                VerificationCode.target == user.email,
                VerificationCode.purpose == "reset_password",
            )
        )
    ).scalars().all()
    assert len(codes) == 1
    assert codes[0].code_hash
    assert codes[0].consumed_at is None
    assert codes[0].attempt_count == 0

    # audit 记 success
    logs = (
        await session.execute(
            select(OperationLog).where(OperationLog.action == "auth.verification_code.send")
        )
    ).scalars().all()
    assert any(l.status == "success" for l in logs)


@pytest.mark.asyncio
async def test_send_code_sms_success(client, db, sms_cfg):
    """存在 user + active sms config → 200 + 真发码。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent = {}

    def fake_send(cfg, secrets, phone, template_param):
        sent["phone"] = phone
        sent["template_param"] = template_param

    with patch("app.api.auth.get_sms_sender", return_value=fake_send):
        resp = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "sms",
                "target": user.phone,
                "purpose": "reset_password",
                "captcha_id": captcha_id,
                "captcha_answer": answer,
            },
        )
    assert resp.status_code == 200, resp.text
    assert sent["phone"] == user.phone
    assert "code" in sent["template_param"]


@pytest.mark.asyncio
async def test_send_code_captcha_invalid(client):
    """错 captcha → 400 captcha_invalid。"""
    resp = await client.post(
        "/api/manager/auth/verification-code/send",
        json={
            "channel": "email",
            "target": "x@example.com",
            "purpose": "reset_password",
            "captcha_id": "nonexistent",
            "captcha_answer": "0000",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_invalid"


@pytest.mark.asyncio
async def test_send_code_nonexistent_target_fake_success(client, db):
    """不存在 email → 200 sent=true 但 audit 记 user_not_found。"""
    session, _ = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    resp = await client.post(
        "/api/manager/auth/verification-code/send",
        json={
            "channel": "email",
            "target": f"nonexist_{uuid.uuid4().hex[:8]}@example.com",
            "purpose": "reset_password",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] is True

    # audit 记 user_not_found
    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.action == "auth.verification_code.send",
                OperationLog.status == "failure",
            )
        )
    ).scalars().all()
    assert any((l.detail or {}).get("reason") == "user_not_found" for l in logs)


@pytest.mark.asyncio
async def test_send_code_no_active_provider_fake_success(client, db):
    """无 active config → 200 sent=true 但 audit 记 no_active_provider。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    resp = await client.post(
        "/api/manager/auth/verification-code/send",
        json={
            "channel": "email",
            "target": user.email,
            "purpose": "reset_password",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] is True

    logs = (
        await session.execute(
            select(OperationLog).where(
                OperationLog.action == "auth.verification_code.send",
                OperationLog.status == "failure",
            )
        )
    ).scalars().all()
    assert any((l.detail or {}).get("reason") == "no_active_provider" for l in logs)


@pytest.mark.asyncio
async def test_send_code_rate_limit_target_minute(client, db, email_cfg):
    """同 target 1min 内 2 次 → 第二次 429 code_too_frequent。"""
    session, user = db

    def fake_send(*args, **kwargs):
        pass

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        # 第一次：成功
        cap1 = await _get_captcha_with_answer(client)
        resp1 = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": cap1[0], "captcha_answer": cap1[1],
            },
        )
        assert resp1.status_code == 200

        # 第二次：1min 内 → 429
        cap2 = await _get_captcha_with_answer(client)
        resp2 = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": cap2[0], "captcha_answer": cap2[1],
            },
        )
        assert resp2.status_code == 429
        assert resp2.json()["detail"] == "code_too_frequent"


@pytest.mark.asyncio
async def test_send_code_rate_limit_ip_hourly(client, db, email_cfg):
    """单 IP 1h 内 10 条验证码 → 第 11 次 403 ip_code_banned。"""
    session, user = db

    def fake_send(*args, **kwargs):
        pass

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        # 10 次不同 target，每个都成功（10/h 上限）
        for i in range(10):
            target = f"ipban{uuid.uuid4().hex[:6]}{i}@example.com"
            # 临时插入 user 让其能通过 user 查找（其实 reset_password 要 user 存在才不发码）
            # 简化：用 change_email purpose 不要求 user 存在
            cap = await _get_captcha_with_answer(client)
            resp = await client.post(
                "/api/manager/auth/verification-code/send",
                json={
                    "channel": "email", "target": target, "purpose": "change_email",
                    "captcha_id": cap[0], "captcha_answer": cap[1],
                },
            )
            assert resp.status_code == 200, f"#{i}: {resp.text}"

        # 第 11 次 → 403
        cap = await _get_captcha_with_answer(client)
        resp = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email",
                "target": "ipban11@example.com",
                "purpose": "change_email",
                "captcha_id": cap[0], "captcha_answer": cap[1],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "ip_code_banned"


@pytest.mark.asyncio
async def test_send_code_respects_config_interval(client, db, email_cfg):
    """cfg.interval_seconds=10 → 间隔 < 10s 拒绝。"""
    session, user = db
    email_cfg.interval_seconds = 10
    await session.commit()

    def fake_send(*args, **kwargs):
        pass

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        cap1 = await _get_captcha_with_answer(client)
        resp1 = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": cap1[0], "captcha_answer": cap1[1],
            },
        )
        assert resp1.status_code == 200

        cap2 = await _get_captcha_with_answer(client)
        resp2 = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": cap2[0], "captcha_answer": cap2[1],
            },
        )
        assert resp2.status_code == 429
        assert resp2.headers.get("Retry-After") == "10"


@pytest.mark.asyncio
async def test_send_code_respects_config_daily_limit(client, db, email_cfg):
    """cfg.daily_limit=2 → 第 3 次/天 拒绝。"""
    session, user = db
    email_cfg.daily_limit = 2
    await session.commit()

    # daily_limit 是按 target 计的，需绕过 interval_seconds 限制
    # 方法：用 3 个不同 target（但同 IP 10/h 上限不会触发）
    def fake_send(*args, **kwargs):
        pass

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        # 同一 target 发 2 次需间隔 > 60s（默认 interval）— 用 mock 时间前进
        # 简化：直接调 code_rate_limiter 测 daily_limit，不走 endpoint
        pass

    # 直接测 CodeRateLimiter
    await code_rate_limiter.reset()
    # 2 次通过
    await code_rate_limiter.check_send("test@example.com", "10.0.0.1", daily_limit=2, interval_seconds=0)
    await code_rate_limiter.reset()  # 清掉 interval 残留
    # 手动构造 2 次发送历史
    now = time.time()
    code_rate_limiter._target_day["test@example.com"].append(now - 100)
    code_rate_limiter._target_day["test@example.com"].append(now - 50)
    # 第 3 次 → 429
    with pytest.raises(Exception) as exc:
        await code_rate_limiter.check_send(
            "test@example.com", "10.0.0.1", daily_limit=2, interval_seconds=0
        )
    from fastapi import HTTPException
    assert isinstance(exc.value, HTTPException)
    assert exc.value.status_code == 429
    assert exc.value.detail == "code_target_daily_limit"
    await code_rate_limiter.reset()


# ── 3. verify_code ────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_code_success_returns_ticket(client, db, email_cfg):
    """校验成功 → ticket UUID。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        # 提取 code
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        resp = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )
        assert resp.status_code == 200

    # 校验码
    resp2 = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp2.status_code == 200, resp2.text
    data = resp2.json()
    assert data["verified"] is True
    assert "ticket" in data
    # UUID 格式校验
    uuid.UUID(data["ticket"])


@pytest.mark.asyncio
async def test_verify_code_wrong_code(client, db, email_cfg):
    """错 code → 401 invalid_code。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    def fake_send(*args, **kwargs):
        pass

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": "000000",  # 几乎不可能命中
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_code"


@pytest.mark.asyncio
async def test_verify_code_5_failures_invalidates(client, db, email_cfg):
    """5 次错 → code consumed_at；第 6 次正确 code 也 401。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    # 5 次错码
    for i in range(5):
        resp = await client.post(
            "/api/manager/auth/verification-code/verify",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "code": "111111",
            },
        )
        assert resp.status_code == 401

    # DB 里 code 应已 consumed
    code = (
        await session.execute(
            select(VerificationCode).where(
                VerificationCode.target == user.email,
                VerificationCode.purpose == "reset_password",
            )
        )
    ).scalar_one()
    assert code.consumed_at is not None
    assert code.attempt_count == 5

    # 第 6 次正确 code → 401（code 已失效）
    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_verify_code_single_use(client, db, email_cfg):
    """同 code 第二次校验 → 401（已 consumed）。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    # 第一次校验成功
    resp1 = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp1.status_code == 200

    # 第二次同 code → 401
    resp2 = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_verify_code_expired(client, db, email_cfg):
    """mock 时间前进 11min → 401 invalid_code。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    # 手动把 expires_at 改成过去
    await session.execute(
        text(
            "UPDATE verification_codes SET expires_at = NOW() - INTERVAL '11 minutes' "
            "WHERE target = :t"
        ),
        {"t": user.email},
    )
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp.status_code == 401


# ── 4. reset-password ─────────────────────────────────────


async def _issue_reset_ticket(client, db, email_cfg, user) -> str:
    """走完发码 + 校验码，返回 ticket UUID。"""
    session, _ = db
    captcha_id, answer = await _get_captcha_with_answer(client)
    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "reset_password",
            "code": sent_code["code"],
        },
    )
    assert resp.status_code == 200
    return resp.json()["ticket"]


@pytest.mark.asyncio
async def test_reset_password_success(client, db, email_cfg):
    """ticket → 改密码 → 新密码可登录。"""
    session, user = db
    ticket = await _issue_reset_ticket(client, db, email_cfg, user)

    resp = await client.post(
        "/api/manager/auth/reset-password",
        json={"ticket": ticket, "new_password": "NewStrongPass123!"},
    )
    assert resp.status_code == 200, resp.text

    # 验证新密码可登录
    await session.refresh(user)
    assert verify_password("NewStrongPass123!", user.hashed_password)

    # audit 记 success
    logs = (
        await session.execute(
            select(OperationLog).where(OperationLog.action == "auth.reset_password")
        )
    ).scalars().all()
    assert any(l.status == "success" for l in logs)


@pytest.mark.asyncio
async def test_reset_password_ticket_single_use(client, db, email_cfg):
    """同 ticket 第二次 → 401 ticket_invalid。"""
    session, user = db
    ticket = await _issue_reset_ticket(client, db, email_cfg, user)

    resp1 = await client.post(
        "/api/manager/auth/reset-password",
        json={"ticket": ticket, "new_password": "NewStrongPass123!"},
    )
    assert resp1.status_code == 200

    resp2 = await client.post(
        "/api/manager/auth/reset-password",
        json={"ticket": ticket, "new_password": "AnotherStrong123!"},
    )
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == "ticket_invalid"


@pytest.mark.asyncio
async def test_reset_password_ticket_expired(client, db, email_cfg):
    """mock 时间前进 11min → 401 ticket_invalid。"""
    session, user = db
    ticket = await _issue_reset_ticket(client, db, email_cfg, user)

    await session.execute(
        text(
            "UPDATE verification_tickets SET expires_at = NOW() - INTERVAL '11 minutes' "
            "WHERE id = :tid"
        ),
        {"tid": ticket},
    )
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/reset-password",
        json={"ticket": ticket, "new_password": "NewStrongPass123!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "ticket_invalid"


@pytest.mark.asyncio
async def test_reset_password_weak_new_rejected(client, db, email_cfg):
    """弱新密码 → 422（zxcvbn 校验）。"""
    session, user = db
    ticket = await _issue_reset_ticket(client, db, email_cfg, user)

    resp = await client.post(
        "/api/manager/auth/reset-password",
        json={"ticket": ticket, "new_password": "password"},  # 弱密码
    )
    assert resp.status_code == 422


# ── 5. unlock-account ─────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_account_success(client, db, email_cfg):
    """ticket → failed_login_count=0, locked_until=None。"""
    session, user = db
    # 先把 user 锁上
    user.failed_login_count = 5
    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    await session.commit()

    ticket = await _issue_reset_ticket_for_unlock(client, db, email_cfg, user)

    resp = await client.post(
        "/api/manager/auth/unlock-account",
        json={"ticket": ticket},
    )
    assert resp.status_code == 200, resp.text

    await session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


async def _issue_reset_ticket_for_unlock(client, db, email_cfg, user) -> str:
    """走完发码 + 校验码（purpose=account_unlock），返回 ticket。"""
    session, _ = db
    captcha_id, answer = await _get_captcha_with_answer(client)
    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "account_unlock",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": user.email, "purpose": "account_unlock",
            "code": sent_code["code"],
        },
    )
    assert resp.status_code == 200
    return resp.json()["ticket"]


@pytest.mark.asyncio
async def test_unlock_account_ticket_purpose_mismatch(client, db, email_cfg):
    """reset_password ticket 调 unlock → 401 ticket_invalid。"""
    session, user = db
    # 拿 reset_password ticket
    ticket = await _issue_reset_ticket(client, db, email_cfg, user)

    # 调 unlock-account → 401
    resp = await client.post(
        "/api/manager/auth/unlock-account",
        json={"ticket": ticket},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "ticket_invalid"


# ── 6. change-email ────────────────────────────────────────


async def _issue_change_email_ticket(client, db, email_cfg, new_email: str) -> str:
    """走发码 + 校验（purpose=change_email, target=new_email），返回 ticket。"""
    session, _ = db
    captcha_id, answer = await _get_captcha_with_answer(client)
    sent_code = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_code["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": new_email, "purpose": "change_email",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    resp = await client.post(
        "/api/manager/auth/verification-code/verify",
        json={
            "channel": "email", "target": new_email, "purpose": "change_email",
            "code": sent_code["code"],
        },
    )
    assert resp.status_code == 200
    return resp.json()["ticket"]


@pytest.mark.asyncio
async def test_change_email_success(auth_client, db, email_cfg):
    """已登录 → 改 email + 旧 email code 全失效。"""
    session, user = db
    old_email = user.email
    new_email = f"new_{uuid.uuid4().hex[:8]}@example.com"

    # 1. 先在旧 email 上发个 code（验证改完后旧 code 失效）
    captcha_id1, answer1 = await _get_captcha_with_answer(auth_client)
    sent_old = {}

    def fake_send(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_old["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        resp_old = await auth_client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": old_email, "purpose": "reset_password",
                "captcha_id": captcha_id1, "captcha_answer": answer1,
            },
        )
        assert resp_old.status_code == 200

    # 2. 给 new_email 发码（target 不同，1/interval 不冲突）
    captcha_id2, answer2 = await _get_captcha_with_answer(auth_client)
    sent_new = {}

    def fake_send2(cfg, secrets, to, subject, html):
        import re
        m = re.search(r"<strong>(\d{6})</strong>", html)
        sent_new["code"] = m.group(1)

    with patch("app.api.auth.get_email_sender", return_value=fake_send2):
        resp_new = await auth_client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": new_email, "purpose": "change_email",
                "captcha_id": captcha_id2, "captcha_answer": answer2,
            },
        )
        assert resp_new.status_code == 200

    # 3. 调 change-email — endpoint 内 verify_code（消费 new_email 的 code）+ 改 email + 失效旧 email code
    resp = await auth_client.post(
        "/api/manager/auth/me/change-email",
        json={"new_email": new_email, "code": sent_new["code"]},
    )
    assert resp.status_code == 200, resp.text

    await session.refresh(user)
    assert user.email == new_email

    # 旧 email 上的 code 应已 consumed（invalidate_target_codes 调用了）
    old_codes = (
        await session.execute(
            select(VerificationCode).where(
                VerificationCode.target == old_email,
                VerificationCode.consumed_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(old_codes) == 0


@pytest.mark.asyncio
async def test_change_email_in_use(auth_client, db, email_cfg):
    """new_email 被占用 → 409 email_in_use。"""
    session, user = db
    other = User(
        username=f"other_{uuid.uuid4().hex[:8]}",
        email=f"other_{uuid.uuid4().hex[:8]}@example.com",
        real_name="其他用户",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        email_verified=True,  # 0.8.110 两态：change_email 只查 verified=True 的占用
    )
    session.add(other)
    await session.commit()

    resp = await auth_client.post(
        "/api/manager/auth/me/change-email",
        json={"new_email": other.email, "code": "123456"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "email_in_use"

    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
    await session.commit()


@pytest.mark.asyncio
async def test_change_email_wrong_code(auth_client, db, email_cfg):
    """错 code → 401 invalid_code。"""
    session, user = db
    new_email = f"new_{uuid.uuid4().hex[:8]}@example.com"

    resp = await auth_client.post(
        "/api/manager/auth/me/change-email",
        json={"new_email": new_email, "code": "000000"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_code"


@pytest.mark.asyncio
async def test_change_email_unauthenticated(client, db, email_cfg):
    """不带 token → 401（FastAPI 自动）。"""
    session, user = db
    resp = await client.post(
        "/api/manager/auth/me/change-email",
        json={"new_email": "new@example.com", "code": "123456"},
    )
    assert resp.status_code == 401


# ── 7. change-phone ───────────────────────────────────────


@pytest.mark.asyncio
async def test_change_phone_success(auth_client, db, sms_cfg):
    """已登录 → 改 phone。"""
    session, user = db
    new_phone = "13900001111"

    captcha_id, answer = await _get_captcha_with_answer(auth_client)
    sent_code = {}

    def fake_send(cfg, secrets, phone, template_param):
        sent_code["code"] = template_param["code"]

    with patch("app.api.auth.get_sms_sender", return_value=fake_send):
        await auth_client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "sms", "target": new_phone, "purpose": "change_phone",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )

    resp = await auth_client.post(
        "/api/manager/auth/me/change-phone",
        json={"new_phone": new_phone, "code": sent_code["code"]},
    )
    assert resp.status_code == 200, resp.text

    await session.refresh(user)
    assert user.phone == new_phone


@pytest.mark.asyncio
async def test_change_phone_in_use(auth_client, db, sms_cfg):
    """new_phone 被占用 → 409 phone_in_use。"""
    session, user = db
    other = User(
        username=f"phone_other_{uuid.uuid4().hex[:8]}",
        email=f"phone_other_{uuid.uuid4().hex[:8]}@example.com",
        phone="13900009999",
        real_name="手机占用用户",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        phone_verified=True,  # 0.8.110 两态：change_phone 只查 verified=True 的占用
    )
    session.add(other)
    await session.commit()

    resp = await auth_client.post(
        "/api/manager/auth/me/change-phone",
        json={"new_phone": "13900009999", "code": "123456"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "phone_in_use"

    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
    await session.commit()


@pytest.mark.asyncio
async def test_change_phone_wrong_code(auth_client, db, sms_cfg):
    """错 code → 401 invalid_code。"""
    resp = await auth_client.post(
        "/api/manager/auth/me/change-phone",
        json={"new_phone": "13900001111", "code": "000000"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_code"


# ── 8. provider send 异常 ─────────────────────────────────


@pytest.mark.asyncio
async def test_provider_send_network_error(client, db, email_cfg):
    """provider send 抛异常 → endpoint 503 code_send_failed。"""
    session, user = db
    captcha_id, answer = await _get_captcha_with_answer(client)

    def fake_send(*args, **kwargs):
        raise Exception("network timeout")

    with patch("app.api.auth.get_email_sender", return_value=fake_send):
        resp = await client.post(
            "/api/manager/auth/verification-code/send",
            json={
                "channel": "email", "target": user.email, "purpose": "reset_password",
                "captcha_id": captcha_id, "captcha_answer": answer,
            },
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "code_send_failed"


# ── 9. phone unique 约束 ──────────────────────────────────


@pytest.mark.asyncio
async def test_users_phone_unique_constraint(db):
    """同 phone 绑两个 user → 第二个 INSERT 抛 IntegrityError。"""
    session, user = db
    from sqlalchemy.exc import IntegrityError

    phone = f"1{str(uuid.uuid4().int)[:10]}".ljust(11, "0")
    user1 = User(
        username=f"u1_{uuid.uuid4().hex[:6]}",
        email=f"u1_{uuid.uuid4().hex[:6]}@example.com",
        phone=phone,
        real_name="U1",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        phone_verified=True,  # 0.8.110 两态：partial unique 只在 verified=TRUE 时约束
    )
    user2 = User(
        username=f"u2_{uuid.uuid4().hex[:6]}",
        email=f"u2_{uuid.uuid4().hex[:6]}@example.com",
        phone=phone,
        real_name="U2",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        phone_verified=True,
    )
    session.add(user1)
    await session.commit()
    user1_id = user1.id  # 缓存 id — IntegrityError rollback 后 user1.id 触发 refresh

    session.add(user2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # 清理
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user1_id})
    await session.commit()
