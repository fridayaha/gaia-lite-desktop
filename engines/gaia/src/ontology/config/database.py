"""SQLAlchemy async engine and session factory.

full 版用 asyncpg driver 连 PostgreSQL；lite 版用 aiosqlite 连本地 SQLite
（桌面版元数据）。DSN 按 settings.edition 切换——仅此让 `import database`
在 lite 版（未装 asyncpg）下不 ImportError。

PostgresMetaStore 的 PG 专属构造（`postgresql.insert(...).on_conflict_do_nothing`、
`.with_for_update(skip_locked=True)`、`.returning(...)`、JSONB `properties[k].as_string()`）
经实测在 SQLite 上原样工作（PG 与 SQLite 都支持 `ON CONFLICT`，SQLAlchemy 自动
方言化 JSON 访问），故无需 dialect dispatch（B1）。建表由 main.py lifespan 调
`Base.metadata.create_all` 完成（lite 跳过 Alembic）。
"""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ontology.config.settings import settings

if settings.edition == "lite":
    # 桌面版：SQLite + aiosqlite，文件库来自 settings.lite_db_path。
    # check_same_thread=False 让 FastAPI 多请求共享同一连接池。
    _lite_path = Path(settings.lite_db_path).expanduser()
    _lite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{_lite_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        settings.pg_dsn,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session with auto-rollback."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
