"""app/models.py 单元测试 — F-GW-050 DB 配置缓存 60s TTL + 主动失效

覆盖：
  - get_channel_config_cached：miss→查 DB→缓存、hit→不查 DB、过期→重查、
    None 配置不缓存
  - _invalidate_channel_config_cache：主动失效
  - _cache_key 格式
"""

from unittest.mock import AsyncMock, patch

import pytest
from app import models


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空 _config_cache，避免相互污染"""
    models._config_cache.clear()
    yield
    models._config_cache.clear()


class TestCacheKey:
    def test_format(self):
        assert models._cache_key("agent-1", "feishu") == "agent-1:feishu"

    def test_different_channels_different_key(self):
        assert models._cache_key("a1", "feishu") != models._cache_key("a1", "wecom")


class TestGetChannelConfigCached:
    @pytest.mark.asyncio
    async def test_miss_queries_db_and_caches(self):
        """缓存未命中 → 查 DB → 写缓存"""
        config = {"app_id": "x"}
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(return_value=config)) as db:
            result = await models.get_channel_config_cached("a1", "feishu")
        assert result == config
        assert db.await_count == 1
        # 已写入缓存
        assert models._cache_key("a1", "feishu") in models._config_cache

    @pytest.mark.asyncio
    async def test_hit_skips_db(self):
        """缓存命中 → 不查 DB"""
        config = {"app_id": "x"}
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(return_value=config)):
            await models.get_channel_config_cached("a1", "feishu")  # 填充缓存
            db2 = AsyncMock(return_value={"app_id": "other"})
            with patch.object(models, "get_channel_config", new=db2):
                result = await models.get_channel_config_cached("a1", "feishu")
        assert result == config  # 返回缓存值，非新 DB 值
        assert db2.await_count == 0

    @pytest.mark.asyncio
    async def test_expired_requeries_db(self):
        """缓存过期（>60s）→ 重新查 DB"""
        config1 = {"app_id": "v1"}
        config2 = {"app_id": "v2"}
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(side_effect=[config1, config2])):
            await models.get_channel_config_cached("a1", "feishu")  # 填充
            # 模拟时间前进 61s（超过 60s TTL）
            base_time = models.time.time()
            with patch.object(models.time, "time", return_value=base_time + 61):
                result = await models.get_channel_config_cached("a1", "feishu")
        assert result == config2  # 过期后重新查 DB 得到新值

    @pytest.mark.asyncio
    async def test_none_config_not_cached(self):
        """DB 返回 None（无配置）→ 不写缓存，每次都查 DB"""
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(return_value=None)) as db:
            await models.get_channel_config_cached("a1", "feishu")
            await models.get_channel_config_cached("a1", "feishu")
        assert db.await_count == 2  # 无缓存，每次都查
        assert models._cache_key("a1", "feishu") not in models._config_cache


class TestInvalidateCache:
    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self):
        """主动失效 → 移除缓存项，下次查询重新查 DB"""
        config = {"app_id": "x"}
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(return_value=config)) as db:
            await models.get_channel_config_cached("a1", "feishu")
            assert models._cache_key("a1", "feishu") in models._config_cache
            models._invalidate_channel_config_cache("a1", "feishu")
            assert models._cache_key("a1", "feishu") not in models._config_cache
            # 失效后再次查询 → 重新查 DB
            await models.get_channel_config_cached("a1", "feishu")
        assert db.await_count == 2

    def test_invalidate_missing_key_noop(self):
        """失效不存在的 key 不报错"""
        models._invalidate_channel_config_cache("none", "feishu")  # 不抛

    @pytest.mark.asyncio
    async def test_invalidate_only_targets_specific_key(self):
        """失效只影响指定 key，不影响其他 agent/channel 缓存"""
        with patch.object(models, "get_channel_config",
                          new=AsyncMock(side_effect=[{"a": 1}, {"b": 2}])):
            await models.get_channel_config_cached("a1", "feishu")
            await models.get_channel_config_cached("a2", "wecom")
        models._invalidate_channel_config_cache("a1", "feishu")
        assert models._cache_key("a1", "feishu") not in models._config_cache
        assert models._cache_key("a2", "wecom") in models._config_cache  # 其他保留
