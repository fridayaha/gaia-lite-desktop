"""MinIO archiver — handles engine data backup/restore to MinIO object storage.

UserGroup 隔离改造后，所有对象路径加组前缀 `groups/{group_code}/`：
  backups:       groups/{group_code}/backups/{agent_id}/latest.tar.gz
  archives:      groups/{group_code}/archives/{agent_id}/{ts}.tar.gz
  engine-config: groups/{group_code}/engine-config/{agent_id}/{config.yaml,.env}

组前缀由调用方（main.py）传入 group_code（取自 instance.group_id → user_groups.code）。
便于按组查询/导出/清理 MinIO 对象。

向后兼容：restore 时 get_archive(archive_path) 直接用 DB 持久化的完整路径
（含 groups/{code}/ 前缀），无需解析。存量旧路径（无 groups 前缀）的归档无法
被新代码按组定位，存量数据少可接受（见 main.py 注释）。

保存时机 (SUSPEND 时):
  Controller exec 进 Pod，tar 数据目录 → 上传 groups/{group_code}/backups/{agent_id}/latest.tar.gz

清理时机 (DESTROY 时):
  复制 backups → groups/{group_code}/archives/{agent_id}/{timestamp}.tar.gz
  删除 groups/{group_code}/backups/{agent_id}/

恢复时机 (ARCHIVED → RUNNING):
  从 archives/ 下载 tar → exec 进新 Pod 解压
"""

import io
import logging
from datetime import UTC, datetime, timedelta

import urllib3
from minio import Minio
from minio.error import S3Error

from pkg.common.config import settings

logger = logging.getLogger(__name__)

BACKUPS_PREFIX = "backups"
ARCHIVES_PREFIX = "archives"

# 兜底组前缀：group_code 缺失时用 default，避免 None 拼成非法路径
DEFAULT_GROUP_CODE = "default"

# 备份对象命名：
#   daily:  groups/{code}/backups/{agent_id}/daily-{YYYYMMDD}.tar.gz  （每日 + 销毁时备份，30 天滚动）
#   legacy: groups/{code}/backups/{agent_id}/latest.tar.gz            （旧版兼容，恢复时回退）
#   archive:groups/{code}/archives/{agent_id}/{ts}.tar.gz             （DESTROY 永久归档）


def _group_prefix(group_code: str | None) -> str:
    """构造组前缀 `groups/{code}/`，group_code 为空时回退 default。"""
    code = (group_code or DEFAULT_GROUP_CODE).strip() or DEFAULT_GROUP_CODE
    return f"groups/{code}/"


class MinioArchiver:
    """将引擎数据归档到 MinIO / 从 MinIO 恢复"""

    def __init__(self):
        _region = settings.minio_region or None
        # MinIO client 默认无超时（connect=300s read=300s retries=5，单次最坏 ~25min），
        # 长 IO 卡住会占着 manager 连接不归还 → 连接池耗尽。注入严格超时 + 有限重试。
        _http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=10, read=120),
            maxsize=10,
            retries=urllib3.Retry(
                total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
            ),
        )
        self.client = Minio(
            endpoint=settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_user,
            secret_key=settings.minio_password,
            secure=settings.minio_endpoint.startswith("https"),
            region=_region,
            http_client=_http_client,
        )
        self.bucket = settings.minio_bucket
        self._bucket_ensured = False
        # 腾讯云 COS 要求 virtual-host-style；minio-py 仅对 .amazonaws.com/.aliyuncs.com
        # 启用，对 COS 回退 path-style 会被拒（403 AccessDenied）。endpoint 含 myqcloud.com
        # 即判定为 COS，强制开启 virtual-host-style。本地 MinIO（minio:9000）不含该后缀，
        # 保持 path-style；阿里云 OSS 已被 SDK 自动识别。
        _ep_host = settings.minio_endpoint.lower()
        if "myqcloud.com" in _ep_host:
            self.client._base_url._virtual_style_flag = True

    def _lazy_ensure_bucket(self):
        """延迟确保 bucket 存在（首次使用时才检查）"""
        if self._bucket_ensured:
            return
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created MinIO bucket: {self.bucket}")
            self._bucket_ensured = True
        except Exception as e:
            logger.warning(f"MinIO bucket check failed (will retry): {e}")

    # ── 内部上传/下载/校验 helper ────────────────────────

    def _put_and_verify(self, object_name: str, data: bytes, content_type: str) -> None:
        """上传对象后回读 stat 校验 size 一致，不符即 raise（防截断/静默失败）。"""
        length = len(data)
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=length,
            content_type=content_type,
        )
        try:
            stat = self.client.stat_object(self.bucket, object_name)
        except S3Error as e:
            raise RuntimeError(f"stat verify failed for {object_name}: {e}")
        if stat.size != length:
            raise RuntimeError(
                f"upload size mismatch for {object_name}: wrote {length}, stat {stat.size}"
            )
        logger.info(f"Saved {object_name} ({length} bytes, verified)")

    def _get_object_bytes(self, object_name: str) -> bytes | None:
        """下载对象全文，不存在返回 None。"""
        try:
            response = self.client.get_object(self.bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error:
            return None

    def _daily_prefix(self, agent_id: str, group_code: str | None) -> str:
        return f"{_group_prefix(group_code)}{BACKUPS_PREFIX}/{agent_id}/"

    def list_daily(self, agent_id: str, group_code: str | None = None) -> list[str]:
        """列出 daily-*.tar.gz 对象名，按日期降序（最近在前）。"""
        self._lazy_ensure_bucket()
        prefix = self._daily_prefix(agent_id, group_code)
        names: list[str] = []
        try:
            for obj in self.client.list_objects(self.bucket, prefix=prefix, recursive=True):
                if "/daily-" in obj.object_name and obj.object_name.endswith(".tar.gz"):
                    names.append(obj.object_name)
        except S3Error as e:
            logger.warning(f"list_daily failed for {agent_id}: {e}")
        # daily-YYYYMMDD.tar.gz 字典序与日期序一致，降序取最近
        names.sort(reverse=True)
        return names

    def _latest_backup_object(self, agent_id: str, group_code: str | None) -> str | None:
        """返回最近可用的备份对象名：优先最近 daily，回退 legacy latest.tar.gz。"""
        daily = self.list_daily(agent_id, group_code)
        if daily:
            return daily[0]
        latest = f"{self._daily_prefix(agent_id, group_code)}latest.tar.gz"
        try:
            self.client.stat_object(self.bucket, latest)
            return latest
        except S3Error:
            return None

    # ── SUSPEND / 销毁 / 每日 时存档 ────────────────────────

    def save_daily(
        self,
        agent_id: str,
        tar_data: bytes,
        group_code: str | None = None,
        date_str: str | None = None,
    ) -> str:
        """上传当日 daily 备份：groups/{code}/backups/{agent_id}/daily-{YYYYMMDD}.tar.gz

        覆盖当日对象（同一天多次备份/销毁备份取最新）。上传后 stat 校验 size。
        """
        self._lazy_ensure_bucket()
        if not date_str:
            date_str = datetime.now(UTC).strftime("%Y%m%d")
        object_name = f"{self._daily_prefix(agent_id, group_code)}daily-{date_str}.tar.gz"
        self._put_and_verify(object_name, tar_data, "application/gzip")
        return object_name

    def save_backup(self, agent_id: str, tar_data: bytes, group_code: str | None = None) -> str:
        """（legacy）上传 latest.tar.gz。保留向后兼容；新代码用 save_daily。"""
        self._lazy_ensure_bucket()
        object_name = f"{self._daily_prefix(agent_id, group_code)}latest.tar.gz"
        self._put_and_verify(object_name, tar_data, "application/gzip")
        return object_name

    def backup_exists(self, agent_id: str, group_code: str | None = None) -> bool:
        """是否存在任何可恢复备份（daily 或 legacy latest）。"""
        self._lazy_ensure_bucket()
        return self._latest_backup_object(agent_id, group_code) is not None

    def get_backup(self, agent_id: str, group_code: str | None = None) -> bytes | None:
        """（legacy）下载 latest.tar.gz。"""
        self._lazy_ensure_bucket()
        object_name = f"{self._daily_prefix(agent_id, group_code)}latest.tar.gz"
        return self._get_object_bytes(object_name)

    def get_latest_daily(self, agent_id: str, group_code: str | None = None) -> bytes | None:
        """恢复用：取最近 daily；无 daily 时回退 legacy latest.tar.gz。均无返回 None。"""
        self._lazy_ensure_bucket()
        src = self._latest_backup_object(agent_id, group_code)
        if not src:
            return None
        return self._get_object_bytes(src)

    def delete_daily_older_than(
        self, agent_id: str, group_code: str | None = None, days: int = 30
    ) -> int:
        """删除 N 天前的 daily-* 备份，返回删除数。legacy latest 不动。"""
        self._lazy_ensure_bucket()
        prefix = self._daily_prefix(agent_id, group_code)
        cutoff = datetime.now(UTC) - timedelta(days=days)
        deleted = 0
        try:
            for obj in self.client.list_objects(self.bucket, prefix=prefix, recursive=True):
                on = obj.object_name
                if "/daily-" not in on or not on.endswith(".tar.gz"):
                    continue
                try:
                    datepart = on.rsplit("/daily-", 1)[1].removesuffix(".tar.gz")
                    d = datetime.strptime(datepart, "%Y%m%d").replace(tzinfo=UTC)
                except Exception:
                    continue
                if d < cutoff:
                    try:
                        self.client.remove_object(self.bucket, on)
                        deleted += 1
                    except S3Error as e:
                        logger.warning(f"delete_daily failed {on}: {e}")
        except S3Error as e:
            logger.warning(f"delete_daily_older_than list failed for {agent_id}: {e}")
        return deleted

    # ── DESTROY 时转为永久存档 ─────────────────────────

    def archive_backup(self, agent_id: str, group_code: str | None = None) -> str | None:
        """将最近备份（daily 或 legacy latest）复制到 archives 作为永久留存。

        路径: groups/{group_code}/archives/{agent_id}/{timestamp}.tar.gz

        复制后 stat 校验 dest size > 0；源 daily 不删除（由 30 天滚动清理统一管，
        保留恢复窗口）。无任何可复制备份时返回 None（调用方必须拒删 PVC）。
        """
        self._lazy_ensure_bucket()
        prefix = _group_prefix(group_code)
        source = self._latest_backup_object(agent_id, group_code)
        if not source:
            logger.warning(f"No backup to archive for {agent_id} (no daily/latest)")
            return None
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = f"{prefix}{ARCHIVES_PREFIX}/{agent_id}/{timestamp}.tar.gz"

        try:
            # MinIO 服务端复制（使用 CopySource 解决较新 SDK 的兼容性）
            from minio.commonconfig import CopySource

            self.client.copy_object(
                bucket_name=self.bucket,
                object_name=dest,
                source=CopySource(self.bucket, source),
            )
            # 完整性校验：dest size 必须 > 0
            stat = self.client.stat_object(self.bucket, dest)
            if not stat.size or stat.size <= 0:
                raise RuntimeError(f"archive dest empty after copy: {dest}")
            logger.info(f"Archived {source} → {dest} (verified {stat.size} bytes)")
            # 归档成功后清理 legacy latest.tar.gz（已被永久 archive 取代）。
            # daily-* 保留（30 天滚动清理统一管，留恢复窗口）。
            if source.endswith("/latest.tar.gz"):
                try:
                    self.client.remove_object(self.bucket, source)
                except S3Error as e:
                    logger.warning(f"remove legacy latest after archive failed: {e}")
            return dest
        except S3Error as e:
            logger.warning(f"Failed to archive backup for {agent_id}: {e}")
            return None

    def _cleanup_backups(self, agent_id: str, group_code: str | None = None):
        """清理 backups 目录"""
        prefix = f"{_group_prefix(group_code)}{BACKUPS_PREFIX}/{agent_id}/"
        try:
            objects = self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
            for obj in objects:
                self.client.remove_object(self.bucket, obj.object_name)
            logger.info(f"Cleaned up {prefix}")
        except S3Error as e:
            logger.warning(f"Failed to cleanup backups for {agent_id}: {e}")

    # ── 从archives恢复 ─────────────────────────────────

    def get_archive(self, archive_path: str) -> bytes:
        """从 MinIO 下载归档数据。

        archive_path 为 DB 持久化的完整对象路径（含 groups/{group_code}/ 前缀，
        由 archive_backup 返回并写入 agent_deployments.archive_path）。无需再传
        group_code。

        向后兼容：存量旧路径（无 groups/ 前缀，形如 archives/{agent_id}/{ts}.tar.gz）
        仍可按原路径直接下载——MinIO 不关心前缀语义。故 restore 旧存档不受影响。
        """
        self._lazy_ensure_bucket()
        # archive_path 格式: groups/{group_code}/archives/{agent_id}/{timestamp}.tar.gz
        # （或存量旧路径 archives/{agent_id}/{timestamp}.tar.gz）
        try:
            response = self.client.get_object(self.bucket, archive_path)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            raise RuntimeError(f"Failed to get archive {archive_path}: {e}")

    # ── 引擎配置独立读写（前缀 groups/{group_code}/engine-config/） ───────

    CONFIG_PREFIX = "engine-config"

    def save_engine_config(
        self,
        agent_id: str,
        config_yaml: str,
        env_content: str,
        group_code: str | None = None,
    ):
        """将引擎配置单独写入 MinIO（比全量 tar 更轻量）

        写入:
          groups/{group_code}/engine-config/{agent_id}/config.yaml
          groups/{group_code}/engine-config/{agent_id}/.env
        """
        self._lazy_ensure_bucket()
        base = f"{_group_prefix(group_code)}{self.CONFIG_PREFIX}/{agent_id}"

        # config.yaml
        yaml_data = config_yaml.encode("utf-8")
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=f"{base}/config.yaml",
            data=io.BytesIO(yaml_data),
            length=len(yaml_data),
            content_type="text/yaml",
        )

        # .env
        env_data = env_content.encode("utf-8")
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=f"{base}/.env",
            data=io.BytesIO(env_data),
            length=len(env_data),
            content_type="text/plain",
        )
        logger.info(f"Engine config saved for {agent_id} to {base}/")

    def get_engine_config(self, agent_id: str, group_code: str | None = None) -> dict | None:
        """从 MinIO 读取引擎配置，返回 {"config_yaml": str, "env": str} 或 None"""
        self._lazy_ensure_bucket()
        base = f"{_group_prefix(group_code)}{self.CONFIG_PREFIX}/{agent_id}"
        result = {}
        for name in ("config.yaml", ".env"):
            try:
                response = self.client.get_object(self.bucket, f"{base}/{name}")
                data = response.read().decode("utf-8")
                response.close()
                response.release_conn()
                key = "env" if name == ".env" else name.replace(".", "_")
                result[key] = data
            except S3Error:
                logger.info(f"Engine config {base}/{name} not found")
                return None
        return result

    def config_exists(self, agent_id: str, group_code: str | None = None) -> bool:
        """检查 engine-config 是否存在"""
        self._lazy_ensure_bucket()
        base = f"{_group_prefix(group_code)}{self.CONFIG_PREFIX}/{agent_id}"
        try:
            self.client.stat_object(self.bucket, f"{base}/config.yaml")
            return True
        except S3Error:
            return False

    def delete_engine_config(self, agent_id: str, group_code: str | None = None) -> int:
        """删除 engine-config 下全部对象（DESTROY 成功后清孤儿，避免存储泄漏）。返回删除数。"""
        self._lazy_ensure_bucket()
        base = f"{_group_prefix(group_code)}{self.CONFIG_PREFIX}/{agent_id}/"
        deleted = 0
        try:
            for obj in self.client.list_objects(self.bucket, prefix=base, recursive=True):
                try:
                    self.client.remove_object(self.bucket, obj.object_name)
                    deleted += 1
                except S3Error as e:
                    logger.warning(f"delete engine-config {obj.object_name} failed: {e}")
        except S3Error as e:
            logger.warning(f"list engine-config for {agent_id} failed: {e}")
        return deleted

    # ── 技能 zip 持久化（definition 级，deploy 时重装到各实例 Pod）──
    # key 不加组前缀：definition_id 全局唯一，且技能挂在 definition 层（非 instance/group）。
    SKILLS_PREFIX = "skills"

    def _skill_key(self, definition_id: str, skill_name: str) -> str:
        return f"{self.SKILLS_PREFIX}/{definition_id}/{skill_name}.zip"

    def save_skill_zip(self, definition_id: str, skill_name: str, zip_bytes: bytes) -> None:
        """持久化技能 zip 到 MinIO（install 时存，deploy 时取回 fan-out）。"""
        self._lazy_ensure_bucket()
        key = self._skill_key(definition_id, skill_name)
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=key,
            data=io.BytesIO(zip_bytes),
            length=len(zip_bytes),
            content_type="application/zip",
        )
        logger.info(f"Skill zip saved: {definition_id[:8]}/{skill_name}")

    def get_skill_zip(self, definition_id: str, skill_name: str) -> bytes | None:
        """取回技能 zip；不存在返回 None。"""
        self._lazy_ensure_bucket()
        try:
            resp = self.client.get_object(self.bucket, self._skill_key(definition_id, skill_name))
            data = resp.read()
            resp.close()
            resp.release_conn()
            return data
        except S3Error:
            return None

    def list_skill_zips(self, definition_id: str) -> list[str]:
        """列出该 definition 已持久化的技能名（去 .zip 后缀）。"""
        self._lazy_ensure_bucket()
        prefix = f"{self.SKILLS_PREFIX}/{definition_id}/"
        names: list[str] = []
        try:
            for obj in self.client.list_objects(self.bucket, prefix=prefix, recursive=False):
                if obj.object_name.endswith(".zip"):
                    # skills/{def_id}/{name}.zip → name
                    name = obj.object_name[len(prefix):].removesuffix(".zip")
                    if name:
                        names.append(name)
        except Exception as e:
            logger.warning(f"list_skill_zips for {definition_id[:8]} failed: {e}")
        return names

    def delete_skill_zip(self, definition_id: str, skill_name: str) -> None:
        """删除技能 zip（uninstall 时清理）。"""
        self._lazy_ensure_bucket()
        try:
            self.client.remove_object(self.bucket, self._skill_key(definition_id, skill_name))
        except S3Error:
            pass


# Singleton
archiver = MinioArchiver()
