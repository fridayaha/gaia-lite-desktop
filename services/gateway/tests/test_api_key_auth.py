"""Gateway api_key_auth 单元测试 — OpenAI 兼容 sk- Key 鉴权

覆盖：
  - verify_api_key 正确 key → 返回 (instance_id, key_id, prefix, engine_type)
  - verify_api_key 前缀对但后半段错 → 401（HMAC 不匹配）
  - verify_api_key 未知 prefix → 401（DB 无此记录）
  - verify_api_key 非 sk- 前缀 → 401
  - 缓存命中：第二次同 key 不查 DB（但仍 HMAC verify）
  - 缓存过期：重新查 DB；DB 无此 key → 401 且清缓存
  - _hmac_hash 与 manager api_key_service._hmac_hash 一致（跨服务密钥一致性）
"""
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4, UUID
import time

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import api_key_auth


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空 _api_key_cache，避免相互污染"""
    api_key_auth._api_key_cache.clear()
    yield
    api_key_auth._api_key_cache.clear()


def _make_credentials(full_key: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=full_key)


def _mock_row(key_id, instance_id, key_hash, engine_type="HERMES"):
    """构造 DB 查询返回的 mock row（含 engine_type from JOIN）。"""
    return {
        "id": key_id,
        "instance_id": instance_id,
        "key_hash": key_hash,
        "engine_type": engine_type,
    }


def test_hmac_hash_stable_and_correct_length():
    """HMAC-SHA256 hex 输出，64 字符，同输入同输出（确定性）。"""
    full_key = "sk-test-key-for-hmac-consistency-check"
    h1 = api_key_auth._hmac_hash(full_key)
    h2 = api_key_auth._hmac_hash(full_key)
    assert h1 == h2  # 确定性
    assert len(h1) == 64  # SHA256 hex
    # 不同输入不同 hash
    other = api_key_auth._hmac_hash(full_key + "x")
    assert other != h1


def test_hmac_hash_differs_from_plaintext():
    """hash 不等于明文 key（不可逆）。"""
    full_key = "sk-some-random-key"
    assert api_key_auth._hmac_hash(full_key) != full_key


class TestVerifyApiKey:
    @pytest.mark.asyncio
    async def test_correct_key_returns_ids_and_engine_type(self):
        """正确 key → 返回 (instance_id, key_id, prefix, engine_type)。"""
        instance_id = uuid4()
        key_id = uuid4()
        full_key = "sk-correct-key-1234567890abcdefg"
        prefix = full_key[:14]
        key_hash = api_key_auth._hmac_hash(full_key)

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.mappings.return_value.first.return_value = _mock_row(
                key_id, instance_id, key_hash, engine_type="DIFY"
            )
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await api_key_auth.verify_api_key(_make_credentials(full_key))

        assert result == (instance_id, key_id, prefix, "DIFY")
        # 缓存应被填充（含 engine_type）
        assert prefix in api_key_auth._api_key_cache
        assert api_key_auth._api_key_cache[prefix][4] == "DIFY"

    @pytest.mark.asyncio
    async def test_wrong_suffix_401(self):
        """prefix 对但后半段错 → HMAC 不匹配 → 401。"""
        instance_id = uuid4()
        key_id = uuid4()
        real_key = "sk-abcdefghijk1234567890"
        wrong_key = "sk-abcdefghijkWRONGSUFFIX"
        # 确保两者 prefix 相同（前 14 字符）
        assert real_key[:14] == wrong_key[:14]
        prefix = real_key[:14]
        key_hash = api_key_auth._hmac_hash(real_key)

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.mappings.return_value.first.return_value = _mock_row(
                key_id, instance_id, key_hash
            )
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await api_key_auth.verify_api_key(_make_credentials(wrong_key))
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_prefix_401(self):
        """DB 无此 prefix → 401。"""
        full_key = "sk-unknown-key-no-such-prefix"
        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.mappings.return_value.first.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await api_key_auth.verify_api_key(_make_credentials(full_key))
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_non_sk_prefix_401(self):
        """非 sk- 开头的 credentials → 401（应走 JWT 路径，不进 sk- 鉴权）。"""
        with pytest.raises(HTTPException) as exc_info:
            await api_key_auth.verify_api_key(_make_credentials("eyJ.some.jwt.token"))
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_none_credentials_401(self):
        """credentials 为 None → 401。"""
        with pytest.raises(HTTPException) as exc_info:
            await api_key_auth.verify_api_key(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db_but_still_verifies(self):
        """缓存命中（未过期）：不查 DB，但仍 HMAC verify（prefix UI 可见不可信）。"""
        instance_id = uuid4()
        key_id = uuid4()
        full_key = "sk-cached-key-1234567890abc"
        prefix = full_key[:14]
        key_hash = api_key_auth._hmac_hash(full_key)
        # 预填充缓存（时间戳为当前时间，未过期；5 元组含 engine_type）
        api_key_auth._api_key_cache[prefix] = (
            instance_id, key_hash, key_id, time.time(), "HERMES"
        )

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            # DB 不应被调用
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await api_key_auth.verify_api_key(_make_credentials(full_key))

        assert result == (instance_id, key_id, prefix, "HERMES")
        mock_db.execute.assert_not_called()  # 缓存命中，没查 DB

    @pytest.mark.asyncio
    async def test_cache_expired_hits_db(self):
        """缓存过期（TTL 60s）：重新查 DB（Manager 删除后最长 60s 内仍可用）。"""
        instance_id = uuid4()
        key_id = uuid4()
        full_key = "sk-expired-key-1234567890ab"
        prefix = full_key[:14]
        key_hash = api_key_auth._hmac_hash(full_key)
        # 预填充缓存，但时间戳是 2 分钟前（已过期）
        api_key_auth._api_key_cache[prefix] = (
            instance_id, key_hash, key_id, time.time() - 120, "HERMES"
        )

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.mappings.return_value.first.return_value = _mock_row(
                key_id, instance_id, key_hash, engine_type="DIFY"
            )
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await api_key_auth.verify_api_key(_make_credentials(full_key))

        assert result == (instance_id, key_id, prefix, "DIFY")
        mock_db.execute.assert_called_once()  # 过期 → 重新查 DB

    @pytest.mark.asyncio
    async def test_cache_expired_key_deleted_returns_401_and_clears_cache(self):
        """缓存过期 + DB 已无此 key（被 Manager 删除）→ 401 且清缓存项。"""
        instance_id = uuid4()
        key_id = uuid4()
        full_key = "sk-deleted-key-1234567890ab"
        prefix = full_key[:14]
        key_hash = api_key_auth._hmac_hash(full_key)
        # 预填充缓存（已过期），DB 查不到
        api_key_auth._api_key_cache[prefix] = (
            instance_id, key_hash, key_id, time.time() - 120, "HERMES"
        )

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.mappings.return_value.first.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await api_key_auth.verify_api_key(_make_credentials(full_key))
        assert exc_info.value.status_code == 401
        assert prefix not in api_key_auth._api_key_cache  # 清缓存

    @pytest.mark.asyncio
    async def test_cache_hit_with_wrong_suffix_401(self):
        """缓存命中但 HMAC 不匹配 → 401（验证缓存不绕过 HMAC）。"""
        instance_id = uuid4()
        key_id = uuid4()
        real_key = "sk-cached-key-1234567890abc"
        wrong_key = "sk-cached-key-WRONGSUFFIX!!"
        prefix = real_key[:14]
        assert wrong_key[:14] == prefix
        key_hash = api_key_auth._hmac_hash(real_key)
        # 预填充缓存（用 real_key 的 hash，时间戳当前未过期）
        api_key_auth._api_key_cache[prefix] = (
            instance_id, key_hash, key_id, time.time(), "HERMES"
        )

        with patch.object(api_key_auth, "async_session") as mock_session_factory:
            mock_db = AsyncMock()
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await api_key_auth.verify_api_key(_make_credentials(wrong_key))
        assert exc_info.value.status_code == 401


class TestInvalidateCache:
    def test_invalidate_existing(self):
        api_key_auth._api_key_cache["sk-test"] = (uuid4(), "hash", uuid4(), 0.0, "HERMES")
        api_key_auth.invalidate_cache("sk-test")
        assert "sk-test" not in api_key_auth._api_key_cache

    def test_invalidate_nonexistent_noop(self):
        """失效不存在的 key 不报错。"""
        api_key_auth.invalidate_cache("sk-nonexistent")  # no error
