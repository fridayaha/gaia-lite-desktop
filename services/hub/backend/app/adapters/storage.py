from typing import Protocol


class StorageAdapter(Protocol):
    """对象存储适配器接口。

    P1 实现：LocalStorageAdapter, InMemoryStorageAdapter
    P2 实现：S3StorageAdapter (MinIO / S3 compatible)
    """

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """上传字节数据。"""
        ...

    def get_bytes(self, key: str) -> bytes:
        """下载字节数据。不存在时抛出 KeyError。"""
        ...

    def exists(self, key: str) -> bool:
        """检查 key 是否存在。"""
        ...

    def delete(self, key: str) -> None:
        """删除对象。不存在时幂等。"""
        ...

    def presign_get_url(
        self,
        key: str,
        expires_seconds: int | None = None,
    ) -> str | None:
        """生成预签名下载 URL。

        P1 (local/memory): 返回 None。
        P2 (s3): 返回预签名 URL。
        """
        ...
