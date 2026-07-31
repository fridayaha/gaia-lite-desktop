"""minio_archiver.py 配置持久化方法测试

使用 mock minio client 测试真实 MinioArchiver 类。
"""

from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error


@pytest.fixture(autouse=True)
def mock_archiver_client():
    """在每个测试前替换 archiver.client 为 MagicMock

    避免连接真实 MinIO 服务。
    """
    from app.worker.minio_archiver import archiver
    old_client = archiver.client
    archiver.client = MagicMock()
    archiver._bucket_ensured = True  # 跳过 bucket 检查
    yield archiver
    archiver.client = old_client


class TestMinioArchiverConfig:

    def test_save_engine_config(self, mock_archiver_client):
        """save_engine_config 写入 config.yaml 和 .env（默认组前缀 groups/default/）"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        config_yaml = "model:\n  provider: auto\n  default: \"\"\n"
        env_content = "API_SERVER_ENABLED=true\nOPENROUTER_API_KEY=sk-test\n"

        mock_archiver_client.save_engine_config(agent_id, config_yaml, env_content)

        # put_object 应被调用两次
        assert mock_archiver_client.client.put_object.call_count == 2

        # 默认 group_code 缺失 → 回退 groups/default/
        config_call = mock_archiver_client.client.put_object.call_args_list[0]
        assert config_call[1]["object_name"] == f"groups/default/engine-config/{agent_id}/config.yaml"
        assert config_call[1]["content_type"] == "text/yaml"

        env_call = mock_archiver_client.client.put_object.call_args_list[1]
        assert env_call[1]["object_name"] == f"groups/default/engine-config/{agent_id}/.env"
        assert env_call[1]["content_type"] == "text/plain"

    def test_save_engine_config_with_group(self, mock_archiver_client):
        """save_engine_config 传入 group_code 时路径含 groups/{code}/ 前缀"""
        agent_id = "test-agent"
        mock_archiver_client.save_engine_config(agent_id, "cfg", "env", group_code="yanfa")

        config_call = mock_archiver_client.client.put_object.call_args_list[0]
        assert config_call[1]["object_name"] == f"groups/yanfa/engine-config/{agent_id}/config.yaml"
        env_call = mock_archiver_client.client.put_object.call_args_list[1]
        assert env_call[1]["object_name"] == f"groups/yanfa/engine-config/{agent_id}/.env"

    def test_get_engine_config_success(self, mock_archiver_client):
        """get_engine_config 读取已有的 config.yaml + .env"""
        agent_id = "test-agent-123"

        # mock get_object responses
        mock_yaml = MagicMock()
        mock_yaml.read.return_value = b"model:\n  provider: auto\n"
        mock_yaml.__enter__.return_value = mock_yaml
        mock_yaml.__exit__ = MagicMock(return_value=None)
        mock_yaml.release_conn = MagicMock()
        mock_yaml.close = MagicMock()

        mock_env = MagicMock()
        mock_env.read.return_value = b"OPENROUTER_API_KEY=sk-test\n"
        mock_env.__enter__.return_value = mock_env
        mock_env.__exit__ = MagicMock(return_value=None)
        mock_env.release_conn = MagicMock()
        mock_env.close = MagicMock()

        mock_archiver_client.client.get_object.side_effect = [mock_yaml, mock_env]

        result = mock_archiver_client.get_engine_config(agent_id)

        assert result is not None
        assert "config_yaml" in result
        assert result["config_yaml"] == "model:\n  provider: auto\n"
        assert "env" in result
        assert result["env"] == "OPENROUTER_API_KEY=sk-test\n"

    def test_get_engine_config_not_found(self, mock_archiver_client):
        """get_engine_config 在 config 不存在时返回 None"""
        from http.client import HTTPResponse

        # 模拟 S3Error 的 response
        mock_response = MagicMock(spec=HTTPResponse)
        mock_response.status = 404

        mock_archiver_client.client.get_object.side_effect = S3Error(
            mock_response,
            "NoSuchKey",
            "The specified key does not exist",
            "/engine-config/test-agent/.env",
            "test-request-id",
            "test-host-id",
        )

        result = mock_archiver_client.get_engine_config("test-agent")
        assert result is None

    def test_config_exists_true(self, mock_archiver_client):
        """config_exists 在 config.yaml 存在时返回 True（默认组前缀 groups/default/）"""
        mock_archiver_client.client.stat_object.return_value = MagicMock()

        result = mock_archiver_client.config_exists("test-agent")
        assert result is True
        mock_archiver_client.client.stat_object.assert_called_once_with(
            "unionagents-archives",
            "groups/default/engine-config/test-agent/config.yaml",
        )

    def test_config_exists_false(self, mock_archiver_client):
        """config_exists 在 config.yaml 不存在时返回 False"""
        from http.client import HTTPResponse

        mock_response = MagicMock(spec=HTTPResponse)
        mock_response.status = 404

        mock_archiver_client.client.stat_object.side_effect = S3Error(
            mock_response,
            "NoSuchKey",
            "not found",
            "/config.yaml",
            "req-id",
            "host-id",
        )

        result = mock_archiver_client.config_exists("test-agent")
        assert result is False

    def test_save_empty_config(self, mock_archiver_client):
        """空配置也能正常保存"""
        mock_archiver_client.save_engine_config("test-agent", "", "")
        assert mock_archiver_client.client.put_object.call_count == 2

    def test_get_backup_success(self, mock_archiver_client):
        """get_backup 返回 backup 数据（默认组前缀 groups/default/）"""
        agent_id = "test-agent-123"

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"mock-tar-data"
        mock_resp.close = MagicMock()
        mock_resp.release_conn = MagicMock()
        mock_archiver_client.client.get_object.return_value = mock_resp

        result = mock_archiver_client.get_backup(agent_id)

        assert result == b"mock-tar-data"
        mock_archiver_client.client.get_object.assert_called_once_with(
            "unionagents-archives",
            f"groups/default/backups/{agent_id}/latest.tar.gz",
        )

    def test_get_backup_with_group_code(self, mock_archiver_client):
        """get_backup 传入 group_code 时路径含 groups/{code}/ 前缀"""
        agent_id = "test-agent-123"

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"mock-tar-data"
        mock_resp.close = MagicMock()
        mock_resp.release_conn = MagicMock()
        mock_archiver_client.client.get_object.return_value = mock_resp

        mock_archiver_client.get_backup(agent_id, group_code="yanfa")
        mock_archiver_client.client.get_object.assert_called_once_with(
            "unionagents-archives",
            f"groups/yanfa/backups/{agent_id}/latest.tar.gz",
        )

    def test_get_backup_not_found(self, mock_archiver_client):
        """backup 不存在时返回 None"""
        from http.client import HTTPResponse

        mock_response = MagicMock(spec=HTTPResponse)
        mock_response.status = 404

        from minio.error import S3Error
        mock_archiver_client.client.get_object.side_effect = S3Error(
            mock_response, "NoSuchKey", "not found",
            "/backups/test-agent/latest.tar.gz", "req-id", "host-id",
        )

        result = mock_archiver_client.get_backup("test-agent")
        assert result is None

    def test_save_config_creates_bucket(self, mock_archiver_client):
        """首次写入时自动创建 bucket"""
        mock_archiver_client._bucket_ensured = False
        mock_archiver_client.client.bucket_exists.return_value = False

        mock_archiver_client.save_engine_config("test-agent", "config", "env")

        mock_archiver_client.client.make_bucket.assert_called_once()


class TestMinioArchiverSkills:
    """技能 zip 持久化（definition 级，deploy 重放用）。"""

    def test_save_and_get_skill_zip(self, mock_archiver_client):
        """save_skill_zip 写入 skills/{def_id}/{name}.zip；get_skill_zip 取回。"""
        from app.worker.minio_archiver import archiver

        zip_bytes = b"PK\x03\x04fake-zip"
        archiver.save_skill_zip("def-123", "demo-skill", zip_bytes)

        put = archiver.client.put_object.call_args
        assert put[1]["object_name"] == "skills/def-123/demo-skill.zip"
        assert put[1]["content_type"] == "application/zip"
        assert put[1]["length"] == len(zip_bytes)

        # get_skill_zip 读取并返回 bytes
        resp = MagicMock()
        resp.read.return_value = zip_bytes
        archiver.client.get_object.return_value = resp
        data = archiver.get_skill_zip("def-123", "demo-skill")
        assert data == zip_bytes
        archiver.client.get_object.assert_called_with(archiver.bucket, "skills/def-123/demo-skill.zip")

    def test_get_skill_zip_not_found(self, mock_archiver_client):
        """zip 不存在返回 None（不抛）。"""
        from app.worker.minio_archiver import archiver
        from minio.error import S3Error
        from urllib3.response import HTTPResponse

        mock_response = MagicMock(spec=HTTPResponse)
        mock_response.status = 404
        archiver.client.get_object.side_effect = S3Error(
            mock_response, "NoSuchKey", "not found",
            "skills/def-1/x.zip", "req-id", "host-id",
        )
        assert archiver.get_skill_zip("def-1", "x") is None

    def test_list_skill_zips(self, mock_archiver_client):
        """list_skill_zips 列出 skills/{def_id}/ 下 .zip 去后缀。"""
        from app.worker.minio_archiver import archiver

        archiver.client.list_objects.return_value = iter([
            MagicMock(object_name="skills/def-1/alpha.zip"),
            MagicMock(object_name="skills/def-1/beta.zip"),
            MagicMock(object_name="skills/def-1/notazip.txt"),
        ])
        names = archiver.list_skill_zips("def-1")
        assert names == ["alpha", "beta"]
        archiver.client.list_objects.assert_called_with(
            archiver.bucket, prefix="skills/def-1/", recursive=False
        )

    def test_delete_skill_zip(self, mock_archiver_client):
        """delete_skill_zip 调 remove_object。"""
        from app.worker.minio_archiver import archiver

        archiver.delete_skill_zip("def-1", "demo")
        archiver.client.remove_object.assert_called_with(archiver.bucket, "skills/def-1/demo.zip")


class TestMinioArchiverTimeout:
    """Minio client 超时配置(防默认无超时 ~25min 挂死占连接)。"""

    def test_minio_client_uses_strict_http_client(self):
        """Minio 构造注入 http_client(connect=10/read=120/retries=2)。"""
        import app.worker.minio_archiver as mod
        import urllib3

        with patch.object(mod, "Minio", wraps=mod.Minio) as spy:
            arch = mod.MinioArchiver()

        assert arch is not None
        kwargs = spy.call_args.kwargs
        assert "http_client" in kwargs, "Minio 构造必须注入 http_client(防无超时挂死)"
        hc = kwargs["http_client"]
        assert isinstance(hc, urllib3.PoolManager)
        timeout = hc.connection_pool_kw["timeout"]
        assert timeout.connect_timeout == 10
        assert timeout.read_timeout == 120
        assert hc.connection_pool_kw["retries"].total == 2


class TestMinioArchiverVirtualHosted:
    """virtual-hosted-style 自动检测：腾讯云 COS endpoint 被 minio-py 默认 path-style，会被拒 403。"""

    def test_auto_detect_cos_endpoint(self, monkeypatch):
        """endpoint 含 myqcloud.com（腾讯云 COS）自动开启 virtual-hosted-style。"""
        import app.worker.minio_archiver as mod

        monkeypatch.setattr(mod.settings, "minio_endpoint", "https://cos.ap-guangzhou.myqcloud.com")
        arch = mod.MinioArchiver()
        assert arch.client._base_url._virtual_style_flag is True

    def test_local_minio_keeps_path_style(self, monkeypatch):
        """本地 MinIO endpoint（无 myqcloud.com）不触发 virtual-hosted 赋值。"""
        import app.worker.minio_archiver as mod

        monkeypatch.setattr(mod.settings, "minio_endpoint", "http://minio:9000")
        arch = mod.MinioArchiver()
        # 未赋值（mock 环境下属性为 MagicMock；真实环境下 SDK 默认 False）——均非 True，
        # 与 test_auto_detect_cos_endpoint 的 `is True` 形成对照
        assert arch.client._base_url._virtual_style_flag is not True
