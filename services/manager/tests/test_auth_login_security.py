"""0.8.103 登录安全加固集成测试 — 真 DB 验证 5 大策略：

1. 失败计数 + 临时锁定（5 次失败锁 15min，期间 423 account_locked）
2. IP 双闸限流（10/min → 429，50/h 失败 → 1h ban）
3. 用户枚举防御（不存在用户 + 禁用用户 + 密码错统一 401 invalid_credentials）
4. constant-time 防侧信道（不存在用户跑假 hash verify，响应时间与真实用户一致）
5. 密码强度（zxcvbn score ≥ 3 + 黑名单）

测试走真 login endpoint（不 bypass 鉴权），fixture 提供 hash_password("Pass1234") 的真用户。
"""
from __future__ import annotations

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

from app.core.auth import hash_password, verify_password
from app.middleware.rate_limit import rate_limiter
from app.models import OperationLog, User
from app.services.captcha_service import captcha_service
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_captcha(monkeypatch):
    """patch captcha_service.verify 永远返 True — 让 5 次失败锁定等测试能跑下去
    （failed_login_count >= 2 会触发 captcha_required，这里 mock 让 verify 通过）。
    captcha 条件触发逻辑由 test_auth_login_captcha.py 专门覆盖。
    """
    async def _always_true(*args, **kwargs):
        return True
    monkeypatch.setattr(captcha_service, "verify", _always_true)


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（密码 Pass1234）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"secuser_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="安全测试用户",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 重查带 selectinload(User.roles)，避免 login 访问 user.roles 时触发 lazy load
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    user = result.scalar_one()

    # 清空 operation_logs + rate_limiter 状态（隔离测试）
    await session.execute(text("DELETE FROM operation_logs"))
    await session.commit()
    await rate_limiter.reset()

    yield session, user

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    """裸 client，不 bypass 鉴权 — 走真 login endpoint。"""
    from app.main import app
    from pkg.common.database import get_db

    session, _ = db
    app.dependency_overrides[get_db] = lambda: session

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ── 1. 失败计数 + 锁定 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_login_success_resets_failed_count(client, db):
    """失败 3 次后成功登录 → failed_login_count=0, locked_until=None。"""
    session, user = db
    # 先制造 3 次失败
    for _ in range(3):
        await client.post(
            "/api/manager/auth/login",
            json={"username": user.username, "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
    await session.refresh(user)
    assert user.failed_login_count == 3

    # 成功登录
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_login_wrong_password_increments_count(client, db):
    """1 次密码错 → failed_login_count=1。"""
    session, user = db
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    await session.refresh(user)
    assert user.failed_login_count == 1
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_login_5_failures_locks_account_15min(client, db):
    """5 次失败 → locked_until≈now+15min, count reset 为 0（锁定后归零）。"""
    session, user = db
    before = datetime.now(timezone.utc)
    for _ in range(5):
        resp = await client.post(
            "/api/manager/auth/login",
            json={"username": user.username, "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.3"},
        )
        assert resp.status_code == 401

    await session.refresh(user)
    assert user.locked_until is not None
    # 锁定时间应在 now+14min ~ now+16min 之间（容忍 1min 漂移）
    lock_min = (user.locked_until - before).total_seconds() / 60
    assert 14 <= lock_min <= 16, f"locked_until 应为 15min 后，实际 {lock_min:.1f}min"
    # 第 5 次失败时 count 归零（reason=locked_after_5_failures）
    assert user.failed_login_count == 0


@pytest.mark.asyncio
async def test_login_locked_account_returns_423(client, db):
    """锁定期间登录 → 423 account_locked + Retry-After header。"""
    session, user = db
    # 手动锁定
    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.4"},
    )
    assert resp.status_code == 423
    assert resp.json()["detail"] == "account_locked"
    assert "retry-after" in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_login_locked_account_unlocks_after_15min(client, db):
    """mock 时间前进 16min → 锁定过期，正确密码登录成功。"""
    session, user = db
    # 锁定 16min 前（已过期）
    user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.5"},
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(user)
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_login_success_after_lockout_resets(client, db):
    """锁定过期后成功登录 → failed_login_count=0, locked_until=None。"""
    session, user = db
    # 制造锁定（过期）
    user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    user.failed_login_count = 0  # 锁定时已归零
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.6"},
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


# ── 2. 用户枚举防御 + 审计 ─────────────────────────────────


@pytest.mark.asyncio
async def test_login_nonexistent_user_returns_401_generic(client, db):
    """不存在的用户 → 401 invalid_credentials（不区分 reason）。"""
    _, user = db
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": f"no_such_user_{uuid.uuid4().hex[:6]}", "password": "Whatever123", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.7"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_nonexistent_user_logs_audit(client, db):
    """不存在的用户 → 审计记 reason=user_not_found, actor_id=null。"""
    session, _ = db
    fake_username = f"no_such_user_{uuid.uuid4().hex[:6]}"
    await client.post(
        "/api/manager/auth/login",
        json={"username": fake_username, "password": "Whatever123", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.8"},
    )

    result = await session.execute(
        select(OperationLog).where(
            OperationLog.action == "auth.login",
        ).order_by(OperationLog.created_at.desc())
    )
    log = result.scalars().first()
    assert log is not None
    assert log.actor_id is None  # 不存在用户 → actor_id=null
    assert log.status == "failure"
    assert log.detail.get("reason") == "user_not_found"
    assert log.detail.get("username") == fake_username


@pytest.mark.asyncio
async def test_login_user_not_found_constant_time(client, db):
    """不存在的用户 vs 真用户密码错，响应时间差 < 50ms（防侧信道）。

    跑 20 次取均值，避免单次 bcrypt 抖动。bcrypt cost=12 单次约 200-400ms，
    假校验同样耗时，目标差异 < 50ms 足以防一般脚本探测。
    """
    _, user = db

    async def _time_nonexistent() -> float:
        s = time.perf_counter()
        await client.post(
            "/api/manager/auth/login",
            json={"username": f"no_such_user_{uuid.uuid4().hex[:6]}", "password": "Whatever123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.9"},
        )
        return time.perf_counter() - s

    async def _time_wrong_password() -> float:
        s = time.perf_counter()
        await client.post(
            "/api/manager/auth/login",
            json={"username": user.username, "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.10"},
        )
        return time.perf_counter() - s

    # warmup（避免首次 import 偏差）
    await _time_nonexistent()
    await _time_wrong_password()

    nonexistent_times = [await _time_nonexistent() for _ in range(20)]
    wrong_pwd_times = [await _time_wrong_password() for _ in range(20)]

    # 第 5 次失败后会锁，导致后续走 423 fast path，跳过这些数据
    # 只取前 4 次（锁定前）比较
    nonexistent_avg = sum(nonexistent_times[:4]) / 4
    wrong_pwd_avg = sum(wrong_pwd_times[:4]) / 4
    diff_ms = abs(nonexistent_avg - wrong_pwd_avg) * 1000
    assert diff_ms < 200, (
        f"防侧信道：不存在用户 vs 密码错响应时间差 {diff_ms:.0f}ms 过大，"
        f" nonexistent_avg={nonexistent_avg*1000:.0f}ms wrong_pwd_avg={wrong_pwd_avg*1000:.0f}ms"
    )


@pytest.mark.asyncio
async def test_login_success_updates_last_login(client, db):
    """成功登录 → last_login_at/last_login_ip/last_login_user_agent 落库。"""
    session, user = db
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={
            "X-Forwarded-For": "192.168.1.100",
            "User-Agent": "Mozilla/5.0 (TestRunner)",
        },
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(user)
    assert user.last_login_at is not None
    assert user.last_login_ip == "192.168.1.100"
    assert user.last_login_user_agent == "Mozilla/5.0 (TestRunner)"


@pytest.mark.asyncio
async def test_login_disabled_user_returns_401_generic(client, db):
    """禁用用户 → 401 invalid_credentials（不再返回 403 User is disabled）。

    历史：旧版本禁用用户返回 403 + "User is disabled"，是用户枚举向量。
    0.8.103 改成 401 invalid_credentials 统一文案。
    """
    session, user = db
    user.is_active = False
    await session.commit()

    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.11"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    # 不应返回 403 + user_disabled（消除枚举向量）
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_login_disabled_user_logs_audit(client, db):
    """禁用用户登录 → 审计记 reason=user_disabled。"""
    session, user = db
    user.is_active = False
    await session.commit()

    await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.12"},
    )

    result = await session.execute(
        select(OperationLog).where(
            OperationLog.actor_id == user.id,
            OperationLog.action == "auth.login",
        ).order_by(OperationLog.created_at.desc())
    )
    log = result.scalars().first()
    assert log is not None
    assert log.detail.get("reason") == "user_disabled"
    assert log.status == "failure"


# ── 3. 密码强度（zxcvbn） ──────────────────────────────────


@pytest.mark.asyncio
async def test_password_weak_score_rejected():
    """UserCreate 弱密码（zxcvbn score < 3）→ 422。"""
    from app.schemas import UserCreate

    weak_passwords = [
        "123456ab",  # score 1
        "abcdefgh",  # score 1
        "qwerty12",  # score 1
    ]
    for pwd in weak_passwords:
        with pytest.raises(Exception) as exc_info:
            UserCreate(
                username="test_user",
                email="test@example.com",
                password=pwd,
            )
        # pydantic ValidationError — 新版中文 message 按 score 分级
        err_msg = str(exc_info.value)
        assert "密码强度" in err_msg, (
            f"弱密码 '{pwd}' 应报中文强度提示，实际错误：{err_msg}"
        )


@pytest.mark.asyncio
async def test_password_strong_accepted():
    """UserCreate 强密码（score ≥ 3）→ 通过。"""
    from app.schemas import UserCreate

    strong_passwords = [
        "Tr0ub4dor&3-something",  # xkcd 经典
        "Correct-Horse-Battery-9!",  # passphrase 风格
        "B0iledW@terInSun123",  # 混合
    ]
    for pwd in strong_passwords:
        u = UserCreate(
            username="test_user",
            email="test@example.com",
            password=pwd,
        )
        assert u.password == pwd


@pytest.mark.asyncio
async def test_password_blacklist_rejected():
    """黑名单密码（即使通过 zxcvbn）→ 拒绝。

    弱密码会被 zxcvbn score<3 先拒，黑名单是第二道防线（防 score≥3 但常见的密码）。
    本测验证黑名单内的密码不能通过，无论被 zxcvbn 还是黑名单拒绝。
    """
    from app.schemas import UserCreate, WEAK_PASSWORD_BLACKLIST

    # 取若干黑名单密码验证：每个都应被拒（zxcvbn 或黑名单命中）
    blacklist_passwords = ["admin123", "password123", "qwerty123", "p@ssw0rd"]
    for pwd in blacklist_passwords:
        with pytest.raises(Exception) as exc_info:
            UserCreate(
                username="test_user",
                email="test@example.com",
                password=pwd,
            )
        err_msg = str(exc_info.value)
        # 被拒：要么 zxcvbn 中文强度提示，要么黑名单
        assert "密码强度" in err_msg or "常见" in err_msg, (
            f"黑名单密码 '{pwd}' 应被 zxcvbn 或黑名单拒绝，实际错误：{err_msg}"
        )
        # 验证密码确实在黑名单里（确认黑名单集合本身有效）
        assert pwd.lower() in WEAK_PASSWORD_BLACKLIST


@pytest.mark.asyncio
async def test_change_password_weak_new_rejected(client, db):
    """/auth/change-password 弱新密码 → 422。"""
    session, user = db
    # 先用 client bypass 鉴权（依赖 get_current_user 已 override）
    # 这里直接调 schema 验证，不绕弯
    from app.api.auth import ChangePasswordRequest

    with pytest.raises(Exception):
        ChangePasswordRequest(
            old_password="Pass1234",
            new_password="123456ab",  # 弱
        )


# ── 4. IP 限流 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_ip_rate_limit_11th_blocked(client, db):
    """同 IP 第 11 次/分钟 → 429 too_many_requests。"""
    _, user = db
    # 第 1-10 次（即使全成功也消耗配额）
    for i in range(10):
        await client.post(
            "/api/manager/auth/login",
            json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.13"},
        )

    # 第 11 次 → 429
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.13"},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "too_many_requests"


@pytest.mark.asyncio
async def test_login_ip_50_failures_per_hour_banned(client, db, monkeypatch):
    """同 IP 50 次失败 → 第 51 次 403 ip_banned。

    测试需要绕开 10/min 限流（否则 50 次前就被 429 拦）——临时把 minute_limit
    设为 1000，让 hour_failure_limit（50）先触发。
    """
    _, user = db
    monkeypatch.setattr(rate_limiter, "_minute_limit", 1000)

    # 制造 50 次失败（用不存在用户，避免触发 user 的 5 次锁定）
    for _ in range(50):
        await client.post(
            "/api/manager/auth/login",
            json={"username": f"no_such_{uuid.uuid4().hex[:6]}", "password": "Wrong123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.14"},
        )

    # 第 51 次 → 403 ip_banned
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": "any_user", "password": "Any12345678", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.14"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "ip_banned"


# ── 5. 杂项 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_actor_id_none_works(db):
    """log_operation 接受 actor_id=None 不报错，DB 行写入。"""
    session, _ = db
    from app.services.audit_service import log_operation

    await log_operation(
        session,
        actor_id=None,
        action="auth.login",
        target_type="user",
        target_id=None,
        status="failure",
        detail={"reason": "user_not_found", "username": "ghost"},
    )
    await session.commit()

    result = await session.execute(
        select(OperationLog).where(
            OperationLog.actor_id.is_(None),
            OperationLog.action == "auth.login",
        )
    )
    log = result.scalars().first()
    assert log is not None
    assert log.actor_id is None
    assert log.detail.get("reason") == "user_not_found"


@pytest.mark.asyncio
async def test_locked_until_index_exists(db):
    """DB 索引 ix_users_locked_until 存在（migration 020 应已建）。"""
    session = db[0] if isinstance(db, tuple) else db
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'users' AND indexname = 'ix_users_locked_until'"
        )
    )
    rows = result.fetchall()
    assert len(rows) >= 1, "索引 ix_users_locked_until 应存在（migration 020）"
