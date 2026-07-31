from app.adapters.local_storage import LocalStorageAdapter
from app.adapters.memory_storage import InMemoryStorageAdapter
from app.adapters.storage import StorageAdapter
from app.core.config import settings

_storage: StorageAdapter | None = None


class DisabledStorageAdapter:
    """禁用存储适配器。所有操作静默忽略。"""

    def put_bytes(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> None:
        pass

    def get_bytes(self, key: str) -> bytes:
        raise KeyError(key)

    def exists(self, key: str) -> bool:
        return False

    def delete(self, key: str) -> None:
        pass

    def presign_get_url(
        self, key: str, expires_seconds: int | None = None
    ) -> str | None:
        return None


def _create_storage() -> StorageAdapter:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorageAdapter(root=settings.storage_local_root)
    elif backend == "memory":
        return InMemoryStorageAdapter()
    else:
        return DisabledStorageAdapter()


def get_storage() -> StorageAdapter:
    global _storage
    if _storage is not None:
        return _storage
    _storage = _create_storage()
    return _storage


def set_storage(adapter) -> None:
    """测试注入：替换全局 storage 实例。"""
    global _storage
    _storage = adapter


def reset_storage() -> None:
    """重置 storage 为默认实现。"""
    global _storage
    _storage = None
