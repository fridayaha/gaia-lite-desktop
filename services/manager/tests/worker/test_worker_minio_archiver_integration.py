"""MinioArchiver 集成测试（对真实 MinIO）

使用 testcontainers 启动临时 MinIO 容器，验证所有 archiver 方法的真实 I/O。

运行方式:
  RUN_INTEGRATION_TESTS=1 pytest tests/test_minio_archiver_integration.py -v

前置条件:
  Docker 环境（testcontainers 自动管理 MinIO 容器生命周期）
"""

import os
import pytest
from unittest.mock import patch

from app.worker.minio_archiver import MinioArchiver
from tests.worker.test_worker_minio_base import MinioPersistenceTestBase


# ── 集成测试开关 ────────────────────────────────────────

RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION_TESTS", "").lower() in ("1", "true", "yes")


pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Integration tests disabled (set RUN_INTEGRATION_TESTS=1 to enable)",
)


# ── Fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="session")
def minio_container():
    """Session 级别：启动 MinIO Docker 容器"""
    from testcontainers.minio import MinioContainer

    with MinioContainer() as mc:
        yield mc


@pytest.fixture
def archiver_client(minio_container):
    """每个测试：指向容器端点的 MinioArchiver 实例

    注意：每次测试创建新的 MinioArchiver（独立的 bucket 状态）
    """
    endpoint = minio_container.get_endpoint()
    # testcontainers DefaultContainer 暴露 get_endpoint()
    # 返回类似 "localhost:55678"（不含 scheme）
    with patch.dict(os.environ, {
        "UA_MINIO_ENDPOINT": f"http://{endpoint}",
        "UA_MINIO_USER": minio_container.MINIO_ROOT_USER,
        "UA_MINIO_PASSWORD": minio_container.MINIO_ROOT_PASSWORD,
        "UA_MINIO_BUCKET": "unionagents-archives",
    }, clear=False):
        archiver = MinioArchiver()
        archiver._bucket_ensured = False  # 强制第一次操作创建 bucket
        yield archiver


# ── Tests ───────────────────────────────────────────────


class TestMinioArchiverIntegration(MinioPersistenceTestBase):
    """对真实 MinIO 运行所有基类测试"""

    @pytest.fixture
    def archiver(self, archiver_client):
        return archiver_client
