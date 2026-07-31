"""Tar/Untar 数据完整性测试

验证 tar → MinIO upload/download → untar 完整链路的二进制保真度。
同时覆盖 V1 (/root/.hermes) 和 V2 (/opt/data/profiles/) 目录结构。

运行方式:
  RUN_INTEGRATION_TESTS=1 pytest tests/test_data_integrity.py -v
"""

import hashlib
import io
import os
import tarfile
import tempfile
from uuid import uuid4

import pytest


RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION_TESTS", "").lower() in ("1", "true", "yes")
pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Integration tests disabled (set RUN_INTEGRATION_TESTS=1 to enable)",
)


# ── 测试辅助函数 ────────────────────────────────────────


def _create_v1_structure(base_dir: str) -> dict[str, str]:
    """创建 V1 单 Profile 目录结构，返回 {tar_path: sha256}"""
    checksums = {}

    # config.yaml
    path = os.path.join(base_dir, "config.yaml")
    content = "model:\n  provider: auto\n  default: \"\"\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums["/root/.hermes/config.yaml"] = hashlib.sha256(content.encode()).hexdigest()

    # .env
    path = os.path.join(base_dir, ".env")
    content = "API_SERVER_ENABLED=true\nAPI_KEY=test-key\n"
    with open(path, "w") as f:
        f.write(content)
    checksums["/root/.hermes/.env"] = hashlib.sha256(content.encode()).hexdigest()

    # session.db (binary)
    path = os.path.join(base_dir, "state.db")
    content = bytes(range(128))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    checksums["/root/.hermes/state.db"] = hashlib.sha256(content).hexdigest()

    # gateway.lock (empty)
    path = os.path.join(base_dir, "gateway.lock")
    with open(path, "w") as f:
        pass
    checksums["/root/.hermes/gateway.lock"] = hashlib.sha256(b"").hexdigest()

    # sessions/
    path = os.path.join(base_dir, "sessions", "sess_001.json")
    content = '{"id": "s1", "messages": []}'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums["/root/.hermes/sessions/sess_001.json"] = hashlib.sha256(content.encode()).hexdigest()

    return checksums


def _create_v2_structure(base_dir: str) -> dict[str, str]:
    """创建 V2 多 Profile 目录结构，返回 {tar_path: sha256}"""
    checksums = {}
    base = "/opt/data/profiles"

    # base profile
    path = os.path.join(base_dir, "profiles", "base", ".env")
    content = "API_SERVER_PORT=8643\nAPI_KEY=base-key\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums[f"{base}/base/.env"] = hashlib.sha256(content.encode()).hexdigest()

    # alice profile
    path = os.path.join(base_dir, "profiles", "alice", ".env")
    content = "API_SERVER_PORT=8644\nAPI_KEY=alice-key\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums[f"{base}/alice/.env"] = hashlib.sha256(content.encode()).hexdigest()

    path = os.path.join(base_dir, "profiles", "alice", "state.db")
    content = b"alice-session-data-binary"
    with open(path, "wb") as f:
        f.write(content)
    checksums[f"{base}/alice/state.db"] = hashlib.sha256(content).hexdigest()

    # bob profile (with subdirectories)
    path = os.path.join(base_dir, "profiles", "bob", ".env")
    content = "API_SERVER_PORT=8645\nAPI_KEY=bob-key\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums[f"{base}/bob/.env"] = hashlib.sha256(content.encode()).hexdigest()

    path = os.path.join(base_dir, "profiles", "bob", "state.db")
    content = b"bob-session-data-binary"
    with open(path, "wb") as f:
        f.write(content)
    checksums[f"{base}/bob/state.db"] = hashlib.sha256(content).hexdigest()

    path = os.path.join(base_dir, "profiles", "bob", "memories", "mem_001.json")
    content = '{"memory": "hello world"}'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    checksums[f"{base}/bob/memories/mem_001.json"] = hashlib.sha256(content.encode()).hexdigest()

    return checksums


def _do_tar(src_dir: str, arcnames: list[str]) -> bytes:
    """模拟 k8s `tar czf - DIR1 DIR2` 行为"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname in arcnames:
            tar.add(src_dir, arcname=arcname)
    return buf.getvalue()


def _do_untar(tar_data: bytes) -> dict[str, bytes]:
    """模拟 k8s `tar xzf - -C /` 行为，返回 {path: content}"""
    extracted = {}
    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                extracted[member.name] = f.read() if f else b""
    return extracted


# ── 利用 MinIO archiver 的集成测试（需要 MinIO 容器） ───


@pytest.fixture(scope="session")
def minio_container():
    from testcontainers.minio import MinioContainer
    with MinioContainer() as mc:
        yield mc


@pytest.fixture
def archiver_client(minio_container):
    from unittest.mock import patch
    from app.worker.minio_archiver import MinioArchiver
    endpoint = minio_container.get_endpoint()
    with patch.dict(os.environ, {
        "UA_MINIO_ENDPOINT": f"http://{endpoint}",
        "UA_MINIO_USER": minio_container.MINIO_ROOT_USER,
        "UA_MINIO_PASSWORD": minio_container.MINIO_ROOT_PASSWORD,
        "UA_MINIO_BUCKET": "unionagents-archives",
    }, clear=False):
        archiver = MinioArchiver()
        archiver._bucket_ensured = False
        yield archiver


class TestDataIntegrity:
    """Tar/Untar + MinIO 数据完整性验证"""

    # ── V1 目录结构 ──

    def test_v1_backup_integrity(self, archiver_client):
        """V1 /root/.hermes 目录：tar → upload → download → untar → SHA256 一致"""
        agent_id = str(uuid4())

        with tempfile.TemporaryDirectory() as src_dir:
            v1_dir = os.path.join(src_dir, "root", ".hermes")
            expected = _create_v1_structure(v1_dir)

            # tar（模拟 k8s exec_tar_data）
            tar_data = _do_tar(src_dir, ["/root/.hermes"])

            # upload to MinIO
            archiver_client.save_backup(agent_id, tar_data)

        # download from MinIO
        retrieved = archiver_client.get_backup(agent_id)

        # untar（模拟 k8s exec_untar_data, tar xzf - -C /）
        extracted = _do_untar(retrieved)

        # 校验
        for path, expected_hash in expected.items():
            assert path in extracted, f"Missing file: {path}"
            actual_hash = hashlib.sha256(extracted[path]).hexdigest()
            assert actual_hash == expected_hash, f"SHA256 mismatch: {path}"

    # ── V2 多 Profile 目录结构 ──

    def test_v2_profile_backup_integrity(self, archiver_client):
        """V2 /opt/data/profiles/ 多 Profile 目录结构完整性验证"""
        agent_id = str(uuid4())

        with tempfile.TemporaryDirectory() as src_dir:
            v2_dir = os.path.join(src_dir, "opt", "data")
            expected = _create_v2_structure(v2_dir)

            tar_data = _do_tar(src_dir, ["/opt/data"])
            archiver_client.save_backup(agent_id, tar_data)

        retrieved = archiver_client.get_backup(agent_id)
        extracted = _do_untar(retrieved)

        for path, expected_hash in expected.items():
            assert path in extracted, f"Missing V2 file: {path}"
            actual_hash = hashlib.sha256(extracted[path]).hexdigest()
            assert actual_hash == expected_hash, f"SHA256 mismatch: {path}"

    # ── V1 + V2 混合 ──

    def test_mixed_v1_v2_backup_integrity(self, archiver_client):
        """V1 + V2 混合：tar 同时包含 /root/.hermes 和 /opt/data"""
        agent_id = str(uuid4())

        with tempfile.TemporaryDirectory() as src_dir:
            v1_dir = os.path.join(src_dir, "root", ".hermes")
            v2_dir = os.path.join(src_dir, "opt", "data")
            expected = {}
            expected.update(_create_v1_structure(v1_dir))
            expected.update(_create_v2_structure(v2_dir))

            # tar 同时打包两个目录（模拟修复后的 exec_tar_data）
            tar_data = _do_tar(src_dir, ["/root/.hermes", "/opt/data"])
            archiver_client.save_backup(agent_id, tar_data)

        retrieved = archiver_client.get_backup(agent_id)
        extracted = _do_untar(retrieved)

        for path, expected_hash in expected.items():
            assert path in extracted, f"Missing file: {path}"
            actual_hash = hashlib.sha256(extracted[path]).hexdigest()
            assert actual_hash == expected_hash, f"SHA256 mismatch: {path}"

        # 两个目录的内容都恢复到了各自的位置
        assert "/root/.hermes/config.yaml" in extracted
        assert "/opt/data/profiles/alice/state.db" in extracted
        assert "/opt/data/profiles/bob/memories/mem_001.json" in extracted

    # ── 大文件 ──

    def test_large_file_integrity(self, archiver_client):
        """15MB 文件通过 tar → MinIO → untar 后 checksum 一致"""
        agent_id = str(uuid4())

        with tempfile.TemporaryDirectory() as src_dir:
            large_file_dir = os.path.join(src_dir, "root", ".hermes")
            os.makedirs(large_file_dir, exist_ok=True)

            large_path = os.path.join(large_file_dir, "model_cache.bin")
            large_content = os.urandom(15 * 1024 * 1024)  # 15MB
            with open(large_path, "wb") as f:
                f.write(large_content)

            expected_hash = hashlib.sha256(large_content).hexdigest()

            tar_data = _do_tar(src_dir, ["/root/.hermes"])
            archiver_client.save_backup(agent_id, tar_data)

        retrieved = archiver_client.get_backup(agent_id)
        extracted = _do_untar(retrieved)

        assert "/root/.hermes/model_cache.bin" in extracted
        actual_hash = hashlib.sha256(extracted["/root/.hermes/model_cache.bin"]).hexdigest()
        assert actual_hash == expected_hash

    # ── Archive 后数据一致性 ──

    def test_archive_checksum(self, archiver_client):
        """archive_backup（服务端 CopyObject）后数据 checksum 不变"""
        agent_id = str(uuid4())

        with tempfile.TemporaryDirectory() as src_dir:
            v1_dir = os.path.join(src_dir, "root", ".hermes")
            _create_v1_structure(v1_dir)
            tar_data = _do_tar(src_dir, ["/root/.hermes"])

        original_hash = hashlib.sha256(tar_data).hexdigest()

        # save → archive
        archiver_client.save_backup(agent_id, tar_data)
        archive_path = archiver_client.archive_backup(agent_id)

        # 从 archive 读取
        archived = archiver_client.get_archive(archive_path)
        archived_hash = hashlib.sha256(archived).hexdigest()

        assert archived_hash == original_hash, "Archive 后数据不一致（服务端 CopyObject 问题）"
