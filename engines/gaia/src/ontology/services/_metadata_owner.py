"""Mixin for services that own a request-scoped ``PostgresMetaStore``.

Services constructed via ``container.<service>`` receive a fresh
``PostgresMetaStore`` (and thus a fresh ``AsyncSession``) on each access.
If the service is not closed, the session leaks: its connection sits
"idle in transaction" and the QueuePool exhausts under load (observed as
HTTP 500 / 30s timeouts once the pool of 20+10 is drained).

Route handlers must close the service after use. The idiomatic FastAPI
way is a yield dependency::

    def get_query_service() -> Iterator[ObjectQueryService]:
        svc = container.object_query_service
        try:
            yield svc
        finally:
            asyncio.ensure_future(svc.aclose())  # or await in async dep

For async dependencies FastAPI awaits the close automatically::

    async def get_query_service() -> AsyncIterator[ObjectQueryService]:
        svc = container.object_query_service
        try:
            yield svc
        finally:
            await svc.aclose()

Services whose sub-components share the same ``PostgresMetaStore``
instance (e.g. ``ActionService`` sharing with its ``ActionAuthorizer``)
only need to close ``self._metadata`` once — ``AsyncSession.close()`` is
idempotent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class MetadataOwnerMixin:
    """Provides ``aclose()`` and ``transaction()`` for services holding a ``PostgresMetaStore``.

    Subclasses must set the metadata store in ``__init__`` as either
    ``self._metadata`` (most services) or ``self.metadata``
    (``DataSourceService``).
    """

    async def aclose(self) -> None:
        """Close the owned metadata session and return its connection.

        Safe to call multiple times (``AsyncSession.close`` is idempotent).
        Services without a metadata store no-op.
        """
        meta = getattr(self, "_metadata", None) or getattr(self, "metadata", None)
        if meta is not None:
            await meta.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Service-level unit-of-work transaction boundary (ADR Action 版本快照修复).

        对标 SQLAlchemy 2.0 / FastAPI 最佳实践：use-case 在 service 层用一个事务
        单元包裹多个低层 metadata 操作，正常退出 commit、异常 rollback。事务内
        的 metadata 方法应传 ``auto_commit=False``（只 flush 不 commit），由本
        单元统一提交。

        委托给 ``PostgresMetaStore.transaction()``（层能力），service 不直接操作
        session，保持分层隔离（架构 §1：层间不互调，service 经层方法编排）。

        用法::

            async with self.transaction():
                created = await self._metadata.create_action_type(at, auto_commit=False)
                await self._publish_version_snapshot(created)  # 内部 auto_commit=False
        """
        meta = getattr(self, "_metadata", None) or getattr(self, "metadata", None)
        if meta is None:
            # 无 metadata 的 service（如纯计算 service）无需事务，直接 yield。
            yield
            return
        async with meta.transaction():
            yield
