"""Shared column default factories for ORM models.

Extracted to a separate module to avoid circular imports and to provide
a single source of truth for UUID/ timestamp generation.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# JSONB 在 PostgreSQL 下使用原生 JSONB（支持索引、高性能），
# 在其他后端（如测试用的 SQLite）退化为普通 JSON。
# 这样 ORM 模型既能生产用 JSONB，又能在 SQLite 内存库上跑单元测试。
JSONBType = JSON().with_variant(JSONB(), "postgresql")


def new_uuid() -> str:
    """Generate a UUID v4 hex string as primary key."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Current UTC timestamp as a naive datetime.

    Returns a timezone-naive datetime in UTC. This matches the Gaia ORM
    columns which are `timestamp` (without time zone) — passing an aware
    datetime to asyncpg for a naive-timestamp column raises
    `can't subtract offset-naive and offset-aware datetimes`.

    The value is still a correct UTC instant (just without tzinfo attached);
    readers interpret it as UTC. See docs/bugfix/seatunnel-pg-cdc-
    timestamptz-blocker.md for why the columns are naive-timestamp.
    """
    return datetime.now(UTC).replace(tzinfo=None)
