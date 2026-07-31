"""验证码生成 + 存储 + 校验。

6 位数字 OTP，bcrypt hash 存 verification_codes 表（不存明文，防 DB dump 泄露）。
10min 有效，5 次错误失效（防爆破 — 6 位数字 10^6，5 次试错 + 1/min 限速 → 爆破不可行）。
单次使用 — 成功 verify 后 consumed_at 标记，不可重放。

ticket：验证码校验通过后生成 VerificationTicket（UUID id + purpose + target + 10min expires），
用于 reset-password / unlock-account / change-email / change-phone 的临时凭证。
单次使用 — consume_ticket 调用后 consumed_at 标记。
"""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_password, verify_password
from app.models import VerificationCode, VerificationTicket

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5


def _generate_code() -> str:
    """密码学安全 RNG 生成 6 位数字（000000-999999）。

    secrets.randbelow 是 CSPRNG（基于 os.urandom），不可预测。
    random.randint 是 PRNG，不安全 — 不能用于验证码。
    """
    return str(secrets.randbelow(1000000)).zfill(6)


async def issue_code(
    db: AsyncSession,
    *,
    channel: str,
    target: str,
    purpose: str,
    ip: str | None,
) -> str:
    """生成验证码 + 落库。返回明文 code（调用方负责发送到用户）。

    code_hash 用 bcrypt 存（cost 12，与用户密码一致）。
    expires_at = now + 10min。
    """
    code = _generate_code()
    record = VerificationCode(
        channel=channel,
        target=target,
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES),
        ip=ip,
    )
    db.add(record)
    await db.flush()
    return code


async def verify_code(
    db: AsyncSession,
    *,
    channel: str,
    target: str,
    purpose: str,
    code: str,
) -> str | None:
    """校验验证码。

    成功 → 生成 VerificationTicket，标记 code consumed_at，返回 ticket UUID。
    失败 → increment attempt_count，达 5 次标记 code consumed_at（防爆破）；返回 None。

    5 次错误或过期后，该 code 不可再用 — 用户需重新发码。
    """
    stmt = (
        select(VerificationCode)
        .where(
            VerificationCode.channel == channel,
            VerificationCode.target == target,
            VerificationCode.purpose == purpose,
            VerificationCode.consumed_at.is_(None),
            VerificationCode.expires_at > datetime.now(timezone.utc),
        )
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()

    if not record:
        # 无可用 code（已过期 / 已消费 / 不存在）— 假装 increment 让攻击者无法区分
        return None

    if verify_password(code, record.code_hash):
        # 成功 → 标记 code consumed_at + 生成 ticket
        record.consumed_at = datetime.now(timezone.utc)
        ticket = VerificationTicket(
            code_id=record.id,
            purpose=purpose,
            target=target,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES),
        )
        db.add(ticket)
        await db.flush()
        return str(ticket.id)

    # 失败 → increment attempt_count，达 5 次标记 consumed_at
    record.attempt_count = (record.attempt_count or 0) + 1
    if record.attempt_count >= MAX_ATTEMPTS:
        record.consumed_at = datetime.now(timezone.utc)
    return None


async def consume_ticket(
    db: AsyncSession,
    ticket_id: str,
    purpose: str,
    target: str | None = None,
) -> VerificationTicket | None:
    """消费 ticket — 单次使用。

    Args:
        ticket_id: UUID 字符串
        purpose: 期望的 purpose（reset_password / account_unlock 等），不匹配则拒绝
        target: 期望的 target，None 表示不校验 target（由调用方用 ticket.target 反查 user）

    Returns:
        VerificationTicket 对象（成功）；None（不存在 / 已消费 / 过期 / purpose/target 不匹配）
    """
    try:
        uid = UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id
    except (ValueError, AttributeError):
        return None

    stmt = select(VerificationTicket).where(
        VerificationTicket.id == uid,
        VerificationTicket.consumed_at.is_(None),
        VerificationTicket.expires_at > datetime.now(timezone.utc),
        VerificationTicket.purpose == purpose,
    )
    if target is not None:
        stmt = stmt.where(VerificationTicket.target == target)

    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if not ticket:
        return None
    ticket.consumed_at = datetime.now(timezone.utc)
    return ticket


async def invalidate_target_codes(db: AsyncSession, target: str) -> None:
    """改绑成功后调用 — 失效该 target 上所有未消费 code。

    防攻击者改完一个再改一个（拿到 new_email ticket 改完 email 后，旧 email 的所有 code 全失效）。
    """
    await db.execute(
        update(VerificationCode)
        .where(
            VerificationCode.target == target,
            VerificationCode.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(timezone.utc))
    )
