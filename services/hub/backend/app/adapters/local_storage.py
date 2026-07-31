import re
from pathlib import Path

from app.adapters.storage import StorageAdapter

_KEY_RE = re.compile(r"^[a-zA-Z0-9._/\-]+$")


class StorageKeyError(ValueError):
    """storage key 不安全或无效。"""


class LocalStorageAdapter:
    """本地文件系统存储适配器。

    所有 key 写入 {root}/key 路径。自动创建父目录。
    禁止路径穿越和绝对路径。
    """

    def __init__(self, root: str = ".hub_storage"):
        self._root = Path(root).resolve()

    def _resolve(self, key: str) -> Path:
        if not key or not _KEY_RE.match(key):
            raise StorageKeyError(f"invalid storage key: {key!r}")
        if ".." in key.split("/"):
            raise StorageKeyError(f"path traversal in key: {key!r}")
        path = (self._root / key).resolve()
        if self._root not in path.parents and path != self._root:
            raise StorageKeyError(
                f"storage key resolves outside root: {key!r}"
            )
        return path

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.is_file():
            path.unlink()

    def presign_get_url(
        self,
        key: str,
        expires_seconds: int | None = None,
    ) -> str | None:
        return None
