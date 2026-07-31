"""MinioArchiver 集成测试共享基类

在同一套测试代码对 MinIO 和 OSS 两个后端运行。
"""

import pytest
from uuid import uuid4


class MinioPersistenceTestBase:
    """持久化层集成测试基类

    子类需提供 archiver fixture（指向后端 MinIO/OSS 的 MinioArchiver 实例）
    """

    @property
    def archiver(self):
        raise NotImplementedError

    # ── Backup 操作 ─────────────────────────────────────

    def test_save_and_get_backup(self):
        """写入 bytes → 读回 bytes 完全一致（默认组前缀 groups/default/）"""
        agent_id = str(uuid4())
        data = b"hello-world-backup-data"

        obj_name = self.archiver.save_backup(agent_id, data)
        assert obj_name == f"groups/default/backups/{agent_id}/latest.tar.gz"

        assert self.archiver.backup_exists(agent_id) is True
        retrieved = self.archiver.get_backup(agent_id)
        assert retrieved == data

    def test_save_and_get_backup_with_group_code(self):
        """group_code 生效时路径含 groups/{code}/ 前缀，跨组隔离"""
        agent_id = str(uuid4())
        data = b"group-scoped-data"

        obj_name = self.archiver.save_backup(agent_id, data, group_code="yanfa")
        assert obj_name == f"groups/yanfa/backups/{agent_id}/latest.tar.gz"

        # 同组可读
        assert self.archiver.backup_exists(agent_id, group_code="yanfa") is True
        assert self.archiver.get_backup(agent_id, group_code="yanfa") == data
        # 跨组不可见（不同前缀）
        assert self.archiver.backup_exists(agent_id, group_code="default") is False
        assert self.archiver.get_backup(agent_id, group_code="default") is None

    def test_backup_exists_false(self):
        """不存在的 agent → exists=False"""
        assert self.archiver.backup_exists("nonexistent-agent") is False

    def test_get_backup_nonexistent_returns_none(self):
        """不存在的 agent → get_backup 返回 None"""
        assert self.archiver.get_backup("nobody") is None

    def test_empty_backup(self):
        """0 字节数据也能保存和读取"""
        agent_id = str(uuid4())
        self.archiver.save_backup(agent_id, b"")
        retrieved = self.archiver.get_backup(agent_id)
        assert retrieved == b""

    # ── Archive 操作 ────────────────────────────────────

    def test_archive_backup_flow(self):
        """save → archive → 备份清除 → 归档可读 → 内容一致（默认组前缀）"""
        agent_id = str(uuid4())
        original = b"archive-test-data"

        self.archiver.save_backup(agent_id, original)
        archive_path = self.archiver.archive_backup(agent_id)

        assert archive_path is not None
        assert archive_path.startswith(f"groups/default/archives/{agent_id}/")
        assert archive_path.endswith(".tar.gz")

        # 备份应被清理
        assert self.archiver.backup_exists(agent_id) is False

        # 归档应可读且内容一致
        archived = self.archiver.get_archive(archive_path)
        assert archived == original

    def test_archive_backup_flow_with_group_code(self):
        """group_code 生效时 archive 路径含 groups/{code}/archives/ 前缀"""
        agent_id = str(uuid4())
        original = b"group-archive-data"

        self.archiver.save_backup(agent_id, original, group_code="yanfa")
        archive_path = self.archiver.archive_backup(agent_id, group_code="yanfa")

        assert archive_path is not None
        assert archive_path.startswith(f"groups/yanfa/archives/{agent_id}/")
        assert archive_path.endswith(".tar.gz")

        # backup 在该组下已清理
        assert self.archiver.backup_exists(agent_id, group_code="yanfa") is False
        # 归档可读
        assert self.archiver.get_archive(archive_path) == original

    def test_archive_backup_no_backup_exists(self):
        """无备份时 archive → None"""
        result = self.archiver.archive_backup("nobody-ever")
        assert result is None

    def test_multiple_archives_different_timestamps(self):
        """多次 archive 产生不同时间戳副本，互不覆盖"""
        agent_id = str(uuid4())

        self.archiver.save_backup(agent_id, b"v1")
        p1 = self.archiver.archive_backup(agent_id)

        self.archiver.save_backup(agent_id, b"v2")
        p2 = self.archiver.archive_backup(agent_id)

        assert p1 != p2  # 不同时间戳
        assert self.archiver.get_archive(p1) == b"v1"
        assert self.archiver.get_archive(p2) == b"v2"

    def test_get_archive_invalid_path_raises(self):
        """非法路径 → RuntimeError"""
        with pytest.raises(RuntimeError, match="Failed to get archive"):
            self.archiver.get_archive("archives/nobody/never.tar.gz")

    # ── Engine Config 操作 ──────────────────────────────

    def test_save_and_get_engine_config(self):
        """config.yaml + .env 读写一致（默认组前缀 groups/default/）"""
        agent_id = str(uuid4())
        yaml_content = "model:\n  provider: auto\n  default: \"\"\n"
        env_content = "API_KEY=secret\nAPI_SERVER_ENABLED=true\n"

        self.archiver.save_engine_config(agent_id, yaml_content, env_content)
        assert self.archiver.config_exists(agent_id) is True

        result = self.archiver.get_engine_config(agent_id)
        assert result is not None
        assert result["config_yaml"] == yaml_content
        assert result["env"] == env_content

    def test_save_and_get_engine_config_with_group_code(self):
        """group_code 生效时 engine-config 跨组隔离"""
        agent_id = str(uuid4())
        yaml_content = "model:\n  provider: openai-api\n"
        env_content = "API_KEY=yanfa-key\n"

        self.archiver.save_engine_config(agent_id, yaml_content, env_content, group_code="yanfa")
        # 同组可读
        assert self.archiver.config_exists(agent_id, group_code="yanfa") is True
        result = self.archiver.get_engine_config(agent_id, group_code="yanfa")
        assert result is not None
        assert result["config_yaml"] == yaml_content
        assert result["env"] == env_content
        # 跨组不可见
        assert self.archiver.config_exists(agent_id, group_code="default") is False
        assert self.archiver.get_engine_config(agent_id, group_code="default") is None

    def test_config_not_exists(self):
        """不存在的 agent → config_exists=False"""
        assert self.archiver.config_exists("nobody") is False

    def test_get_engine_config_not_found(self):
        """不存在的 agent → get_engine_config 返回 None"""
        result = self.archiver.get_engine_config("nobody")
        assert result is None

    def test_empty_engine_config(self):
        """空字符串配置也能正常保存"""
        agent_id = str(uuid4())
        self.archiver.save_engine_config(agent_id, "", "")
        result = self.archiver.get_engine_config(agent_id)
        assert result is not None
        assert result["config_yaml"] == ""
        assert result["env"] == ""

    # ── Bucket 操作 ─────────────────────────────────────

    def test_lazy_bucket_creation(self):
        """首次写入自动创建 bucket"""
        self.archiver._bucket_ensured = False
        # 用全新的随机 bucket 名
        import os
        original_bucket = self.archiver.bucket
        new_bucket = f"test-bucket-{uuid4().hex[:8]}"
        self.archiver.bucket = new_bucket
        try:
            self.archiver.save_backup(str(uuid4()), b"test-data")
            assert self.archiver._bucket_ensured is True
        finally:
            self.archiver.bucket = original_bucket

    def test_large_backup(self):
        """15MB 数据能完整保存和读取"""
        agent_id = str(uuid4())
        import hashlib
        large_data = b"x" * (15 * 1024 * 1024)
        original_hash = hashlib.sha256(large_data).hexdigest()

        self.archiver.save_backup(agent_id, large_data)
        retrieved = self.archiver.get_backup(agent_id)

        assert hashlib.sha256(retrieved).hexdigest() == original_hash
        assert len(retrieved) == len(large_data)
