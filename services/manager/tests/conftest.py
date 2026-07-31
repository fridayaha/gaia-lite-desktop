"""
Manager 服务测试共享 fixtures。
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

# ── 在导入 app 前 mock kubernetes / minio（worker router 经 k8s_manager / minio_archiver
#    引入这两个库）。必须在 `from app.main import app` 之前注入，否则无集群环境导入即失败，
#    且 mock 与真实库的异常类不一致会导致 worker 测试断言失败。
#    E2E 测试（RUN_E2E_TESTS=1）需要真实 k8s/minio 连接本地集群，跳过 mock。──
import sys

_RUN_E2E = os.environ.get("RUN_E2E_TESTS", "").lower() in ("1", "true", "yes")

if not _RUN_E2E:
    _mock_kubernetes = MagicMock()
    _mock_kubernetes.config.ConfigException = Exception
    _mock_kubernetes.client.ApiException = type("ApiException", (Exception,), {"status": 409})


class _MockObj:
    """保留构造函数参数的 mock 对象，用于验证 K8s 资源配置。"""

    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, i):
        return self


if not _RUN_E2E:
    _mock_kubernetes.client.AppsV1Api = MagicMock
    _mock_kubernetes.client.CoreV1Api = MagicMock
    _mock_kubernetes.client.V1Deployment = _MockObj
    _mock_kubernetes.client.V1DeploymentSpec = _MockObj
    _mock_kubernetes.client.V1Container = _MockObj
    _mock_kubernetes.client.V1PodSpec = _MockObj
    _mock_kubernetes.client.V1PodTemplateSpec = _MockObj
    _mock_kubernetes.client.V1Volume = _MockObj
    _mock_kubernetes.client.V1VolumeMount = _MockObj
    _mock_kubernetes.client.V1ObjectMeta = MagicMock
    _mock_kubernetes.client.V1LabelSelector = MagicMock
    _mock_kubernetes.client.V1Service = _MockObj
    _mock_kubernetes.client.V1ServiceSpec = _MockObj
    _mock_kubernetes.client.V1ServicePort = MagicMock
    _mock_kubernetes.client.V1ResourceRequirements = MagicMock
    _mock_kubernetes.client.V1EnvVar = MagicMock
    _mock_kubernetes.client.V1EnvVarSource = MagicMock
    _mock_kubernetes.client.V1SecretKeySelector = MagicMock
    _mock_kubernetes.client.V1VolumeMount = MagicMock
    _mock_kubernetes.client.V1Volume = MagicMock
    _mock_kubernetes.client.V1EmptyDirVolumeSource = MagicMock
    _mock_kubernetes.client.V1PersistentVolumeClaim = _MockObj
    _mock_kubernetes.client.V1PersistentVolumeClaimSpec = _MockObj
    _mock_kubernetes.client.V1PersistentVolumeClaimVolumeSource = _MockObj
    _mock_kubernetes.client.V1ContainerPort = MagicMock
    _mock_kubernetes.client.exceptions = type(
        "exceptions", (), {"ApiException": type("ApiException", (Exception,), {})}
    )()

    _mock_minio = MagicMock()
    _mock_minio.Minio = MagicMock
    _mock_minio.error = type("error", (), {"S3Error": type("S3Error", (Exception,), {})})()

    sys.modules["kubernetes"] = _mock_kubernetes
    sys.modules["kubernetes.config"] = MagicMock()
    sys.modules["kubernetes.client"] = _mock_kubernetes.client
    sys.modules["kubernetes.client.exceptions"] = _mock_kubernetes.client.exceptions
    sys.modules["kubernetes.stream"] = MagicMock()
    sys.modules["kubernetes.stream.stream"] = MagicMock()
    sys.modules["kubernetes.stream.ws_client"] = MagicMock()
    sys.modules["minio"] = _mock_minio
    sys.modules["minio.Minio"] = _mock_minio.Minio
    sys.modules["minio.error"] = _mock_minio.error


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests requiring real MinIO/Docker (set RUN_INTEGRATION_TESTS=1)"
    )
    config.addinivalue_line("markers", "e2e: tests requiring k3s cluster with MinIO")


def _test_db_name() -> str:
    """从 settings.test_database_url 解析库名，用于守卫。"""
    from urllib.parse import urlparse

    path = urlparse(settings.test_database_url).path
    return path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_database():
    """自动创建测试库 + 建表，并强校验库名含 "test"。

    防止真 DB 集成测试误连生产库：V3 测试夹具 teardown 会 DELETE FROM <全表>
    清空数据，若 test_database_url 指向生产 unionagents 库会灾难性丢数据。
    库名不含 "test" 时直接 fail，拒绝运行。
    """
    import asyncio

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    db_name = _test_db_name()
    if "test" not in db_name:
        pytest.fail(
            f"拒绝运行真 DB 测试：test_database_url 库名 '{db_name}' 不含 'test'，"
            f"可能指向生产库。请设置 TEST_DATABASE_URL 指向专用测试库。"
        )

    async def _setup() -> None:
        # 连 maintenance 库（postgres）按需创建测试库
        from urllib.parse import urlparse, urlunparse

        parts = urlparse(settings.test_database_url)
        maint = urlunparse(parts._replace(path="/postgres"))
        maint_engine = create_async_engine(maint, isolation_level="AUTOCOMMIT")
        try:
            async with maint_engine.connect() as conn:
                exists = (
                    await conn.execute(
                        sa.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                        {"n": db_name},
                    )
                ).scalar()
                if not exists:
                    # 库名已校验含 test，可直接拼接
                    await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
        finally:
            await maint_engine.dispose()

        # 在测试库上建表
        from app.models import Base

        engine = create_async_engine(settings.test_database_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            # 执行 migration SQL（create_all 不含 migration 添加的索引/约束）
            # asyncpg 不支持多语句 prepared statement，需逐条执行
            # 用独立连接执行，避免与 begin() 事务冲突
            import glob
            migrations_dir = os.path.join(
                os.path.dirname(__file__), "..", "migrations"
            )
            async with engine.connect() as conn:
                for sql_file in sorted(glob.glob(os.path.join(migrations_dir, "*.sql"))):
                    with open(sql_file) as f:
                        sql = f.read()
                    # 先去掉 -- 注释行，再按分号拆分为单独语句
                    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
                    clean_sql = "\n".join(lines)
                    statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
                    for stmt in statements:
                        try:
                            await conn.execute(sa.text(stmt))
                            await conn.commit()
                        except Exception:
                            await conn.rollback()
        finally:
            await engine.dispose()

    try:
        asyncio.run(_setup())
    except OSError as e:
        # 本地无 PostgreSQL 测试库时，跳过 DB 初始化；纯 helper / 单元测试
        # （不依赖 DB fixture）仍可运行。依赖 DB 的集成测试会在 fixture 调用时
        # 自然失败并报错。
        import warnings
        warnings.warn(
            f"测试 DB 不可用（{e}），跳过建表。依赖 DB 的集成测试将失败，"
            f"纯 helper 测试继续运行。"
        )



from app.main import app
from pkg.common.database import get_db
from pkg.common.config import settings
from app.core.auth import get_current_user
from app.core.group_scope import get_current_group_ids
from app.models import User


TEST_USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TEST_USERNAME = "test_admin"


def make_mock_user() -> User:
    user = MagicMock(spec=User)
    user.id = TEST_USER_ID
    user.username = TEST_USERNAME
    user.email = "test@example.com"
    user.is_active = True
    return user


class FakeObj:
    """按属性存取真实值的简单对象，模拟 SQLAlchemy ORM row。"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_mock_result(return_value):
    """
    模拟 SQLAlchemy execute() 返回值链：
    Result.scalars().first() → return_value (单对象)
    Result.scalars().all() → [return_value] (单对象) 或 return_value (列表)
    Result.unique().scalars().all() → 同上
    Result.scalar() → count
    """
    mock_result = MagicMock()

    # scalars() 子链
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = return_value
    if isinstance(return_value, list):
        mock_scalars.all.return_value = return_value
    else:
        mock_scalars.all.return_value = [return_value] if return_value else []
    mock_result.scalars.return_value = mock_scalars

    # unique().scalars().all() → 分页查询
    mock_unique = MagicMock()
    mock_unique_scalars = MagicMock()
    if isinstance(return_value, list):
        mock_unique_scalars.all.return_value = return_value
    else:
        mock_unique_scalars.all.return_value = [return_value] if return_value else []
    mock_unique.scalars.return_value = mock_unique_scalars
    mock_result.unique.return_value = mock_unique

    # scalar() → count
    if isinstance(return_value, list):
        mock_result.scalar.return_value = len(return_value)
    else:
        mock_result.scalar.return_value = 1 if return_value else 0

    return mock_result


@pytest.fixture
def mock_user():
    return make_mock_user()


@pytest.fixture
def mock_db_session():
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def _mock_flag_modified(monkeypatch):
    """flag_modified 需 SQLAlchemy 模型对象；测试用 FakeObj mock，置为 no-op。"""
    monkeypatch.setattr("app.api.agent_skills.flag_modified", lambda *a, **kw: None)


@pytest.fixture
def client(mock_db_session, mock_user):
    app.dependency_overrides[get_db] = lambda: mock_db_session
    app.dependency_overrides[get_current_user] = lambda: mock_user
    # mock 测试不验组隔离，旁路为平台管理员（group_ids=None）
    app.dependency_overrides[get_current_group_ids] = lambda: None

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")

    yield c

    app.dependency_overrides.clear()
