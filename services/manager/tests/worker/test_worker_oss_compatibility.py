"""OSS 兼容性测试（对阿里云 OSS）

使用环境变量中的 OSS 凭据，在同一套基类测试上验证 S3 兼容性。
运行前需设置：
  UA_OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
  UA_OSS_ACCESS_KEY=your-access-key
  UA_OSS_SECRET_KEY=your-secret-key
  UA_OSS_BUCKET=unionagents-archives-test  （建议用独立测试 bucket）
"""

import os
import pytest
from unittest.mock import patch

from app.worker.minio_archiver import MinioArchiver
from tests.worker.test_worker_minio_base import MinioPersistenceTestBase


# ── 检查 OSS 凭据 ───────────────────────────────────────

OSS_ENDPOINT = os.environ.get("UA_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
OSS_ACCESS_KEY = os.environ.get("UA_OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.environ.get("UA_OSS_SECRET_KEY")
OSS_BUCKET = os.environ.get("UA_OSS_BUCKET", "unionagents-archives-test")

HAS_OSS_CREDENTIALS = bool(OSS_ACCESS_KEY and OSS_SECRET_KEY)


pytestmark = pytest.mark.skipif(
    not HAS_OSS_CREDENTIALS,
    reason="OSS credentials not configured (set UA_OSS_ACCESS_KEY and UA_OSS_SECRET_KEY)",
)


# ── Fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="session")
def oss_config():
    return {
        "UA_MINIO_ENDPOINT": OSS_ENDPOINT,
        "UA_MINIO_USER": OSS_ACCESS_KEY,
        "UA_MINIO_PASSWORD": OSS_SECRET_KEY,
        "UA_MINIO_BUCKET": OSS_BUCKET,
    }


@pytest.fixture
def oss_archiver_client(oss_config):
    """指向 OSS 端点的 MinioArchiver 实例（每测试独立）"""
    from app.worker.minio_archiver import MinioArchiver
    with patch.dict(os.environ, oss_config, clear=False):
        archiver = MinioArchiver()
        archiver._bucket_ensured = False
        yield archiver


# ── Tests ───────────────────────────────────────────────


class TestOSSCompatibility(MinioPersistenceTestBase):
    """对阿里云 OSS 运行所有基类测试"""

    @pytest.fixture
    def archiver(self, oss_archiver_client):
        return oss_archiver_client
