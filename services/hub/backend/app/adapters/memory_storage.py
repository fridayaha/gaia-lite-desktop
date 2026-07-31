from app.adapters.storage import StorageAdapter


class InMemoryStorageAdapter:
    """仅用于测试的 dict 存储适配器。"""

    def __init__(self):
        self._store: dict[str, bytes] = {}

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        self._store[key] = data

    def get_bytes(self, key: str) -> bytes:
        if key not in self._store:
            raise KeyError(key)
        return self._store[key]

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def presign_get_url(
        self,
        key: str,
        expires_seconds: int | None = None,
    ) -> str | None:
        return None
