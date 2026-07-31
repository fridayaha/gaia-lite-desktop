import os
import tempfile

import pytest

from app.adapters.local_storage import LocalStorageAdapter, StorageKeyError
from app.adapters.memory_storage import InMemoryStorageAdapter


class TestInMemoryStorageAdapter:
    def test_put_get(self):
        s = InMemoryStorageAdapter()
        s.put_bytes("test/key", b"hello")
        assert s.get_bytes("test/key") == b"hello"

    def test_exists_true(self):
        s = InMemoryStorageAdapter()
        s.put_bytes("k", b"v")
        assert s.exists("k")

    def test_exists_false(self):
        s = InMemoryStorageAdapter()
        assert not s.exists("nonexistent")

    def test_delete(self):
        s = InMemoryStorageAdapter()
        s.put_bytes("k", b"v")
        s.delete("k")
        assert not s.exists("k")

    def test_delete_idempotent(self):
        s = InMemoryStorageAdapter()
        s.delete("nonexistent")
        assert not s.exists("nonexistent")

    def test_get_missing_raises_key_error(self):
        s = InMemoryStorageAdapter()
        with pytest.raises(KeyError):
            s.get_bytes("nonexistent")

    def test_presign_returns_none(self):
        s = InMemoryStorageAdapter()
        assert s.presign_get_url("k") is None


class TestLocalStorageAdapter:
    @pytest.fixture
    def tmp_root(self, tmp_path):
        root = str(tmp_path / ".hub_storage")
        return root

    def test_put_get(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        s.put_bytes("a/b/data.txt", b"content")
        assert s.get_bytes("a/b/data.txt") == b"content"

    def test_exists(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        s.put_bytes("key.txt", b"x")
        assert s.exists("key.txt")
        assert not s.exists("missing.txt")

    def test_delete(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        s.put_bytes("k", b"v")
        s.delete("k")
        assert not s.exists("k")

    def test_delete_idempotent(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        s.delete("nonexistent")

    def test_auto_create_parent_dir(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        subdir = os.path.join(tmp_root, "deep", "nested")
        assert not os.path.isdir(subdir)
        s.put_bytes("deep/nested/file.txt", b"data")
        assert os.path.isdir(subdir)

    def test_rejects_dotdot_traversal(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("../outside.txt", b"data")

    def test_rejects_absolute_path(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("/tmp/absolute.txt", b"data")

    def test_rejects_dotdot_in_middle(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("a/../../outside.txt", b"data")

    def test_rejects_backslash(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("a\\b.txt", b"data")

    def test_rejects_empty_key(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("", b"data")

    def test_get_missing_raises_key_error(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(KeyError):
            s.get_bytes("nonexistent")

    def test_presign_returns_none(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        assert s.presign_get_url("k") is None

    def test_content_not_written_outside_root(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        s.put_bytes("inside.txt", b"safe")
        outside_file = os.path.join(tmp_root, "..", "outside.txt")
        assert not os.path.exists(os.path.normpath(outside_file))

    def test_key_with_special_chars_rejected(self, tmp_root):
        s = LocalStorageAdapter(root=tmp_root)
        with pytest.raises(StorageKeyError):
            s.put_bytes("key with spaces", b"data")
        with pytest.raises(StorageKeyError):
            s.put_bytes("key\0null", b"data")
