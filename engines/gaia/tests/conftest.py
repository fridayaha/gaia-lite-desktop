"""Global pytest fixtures and mock factories."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.config.settings import settings
from ontology.core.schemas.ontology import (
    Ontology,
    OntologyCreate,
)

# lite 版跳过直接 import 云版重依赖（pyiceberg/trino/numpy/aiomysql）的 cloud-only
# 测试模块——这些模块 import 阶段即 ModuleNotFoundError，skipif 救不了（收集发生在
# skipif 判断之前），故用 collect_ignore_glob 整模块跳过。full 版该列表为空，全量收集。
# 列表随 A3（Service lazy import）收窄；新增 cloud-only 模块需在此登记。
if settings.edition == "lite":
    collect_ignore_glob: list[str] = [
        "unit/benchmark/test_textsql_builders.py",
        "unit/layers/test_iceberg_store.py",
        "unit/layers/test_trino_classify_error.py",
        "unit/layers/test_trino_exploration.py",
        "unit/layers/test_trino_query_engine.py",
        "unit/services/test_action_object_state_keys.py",
        "unit/services/test_action_service.py",
        "unit/services/test_batch_action.py",
        "unit/services/test_datasource_catalog_reconcile.py",
        "unit/services/test_datasource_multi_source.py",
        "unit/services/test_datasource_service_extended.py",
        "unit/services/test_datasource_service.py",
        "unit/services/test_embedding.py",
        "unit/services/test_ontology_service.py",
        "unit/services/test_vector_recall.py",
    ]
else:
    collect_ignore_glob = []

# ── Real async SQLite session for integration-level MetaStore tests ──


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Create a real in-memory SQLite async session.

    This fixture creates tables from the ORM models on setup and drops them
    after the test. Use for tests that need actual SQL execution coverage.
    """
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    # Create all ontology tables
    from ontology.core.models.datasource import (
        CredentialModel,
    )
    from ontology.core.models.ontology import (
        OntologyModel,
    )

    async with engine.begin() as conn:
        await conn.run_sync(OntologyModel.metadata.create_all)
        await conn.run_sync(CredentialModel.metadata.create_all)

    async with session_local() as session:
        yield session

    await engine.dispose()


def utcnow() -> datetime:
    return datetime.now(UTC)


# ── Factory helpers ──


def make_ontology(
    api_name: str = "test_ontology",
    display_name: str = "Test Ontology",
    **overrides,
) -> Ontology:
    """Factory: create a test Ontology schema object with sensible defaults."""
    return Ontology(
        id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        api_name=api_name,
        display_name=display_name,
        description=overrides.get("description", ""),
        rid=overrides.get("rid", ""),
        created_at=overrides.get("created_at", utcnow()),
        updated_at=overrides.get("updated_at", utcnow()),
    )


def make_ontology_create(
    api_name: str = "test_ontology",
    display_name: str = "Test Ontology",
    description: str = "",
) -> OntologyCreate:
    """Factory: create a test OntologyCreate schema."""
    return OntologyCreate(
        api_name=api_name,
        display_name=display_name,
        description=description,
    )


# ── Mock session ──


@pytest_asyncio.fixture
async def mock_session() -> AsyncSession:
    """Create a mock AsyncSession for unit tests."""
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.delete = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest_asyncio.fixture
async def mock_execute_result() -> MagicMock:
    """Create a mock execute result (scalar_one_or_none etc.)."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    result.scalars = MagicMock()
    result.scalars.return_value.all = MagicMock(return_value=[])
    return result
