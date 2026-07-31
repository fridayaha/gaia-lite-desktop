"""ASR provider 注册表 + 工厂 单元测试。"""
import pytest

from app.settings import settings
from app.asr import get_asr_provider, reset_asr_provider
from app.asr.providers.volcengine import VolcengineAsrProvider
from app.asr.providers.local_whisper import LocalWhisperAsrProvider


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前重置 provider 单例（避免测试间相互影响）。"""
    reset_asr_provider()
    yield
    reset_asr_provider()


class TestGetAsrProvider:
    def test_unconfigured_returns_none(self, monkeypatch):
        """asr_provider 未配置 → None"""
        monkeypatch.setattr(settings, "asr_provider", "")
        assert get_asr_provider() is None

    def test_unknown_provider_returns_none(self, monkeypatch):
        """未知 provider 名 → None"""
        monkeypatch.setattr(settings, "asr_provider", "nonexistent")
        assert get_asr_provider() is None

    def test_volcengine_missing_apikey_returns_none(self, monkeypatch):
        """volcengine 凭据缺失 → __init__ 抛 AsrError → None"""
        monkeypatch.setattr(settings, "asr_provider", "volcengine")
        monkeypatch.setattr(settings, "asr_volc_api_key", "")
        monkeypatch.setattr(settings, "asr_volc_resource_id", "volc.seedasr.auc")
        assert get_asr_provider() is None

    def test_volcengine_returns_provider(self, monkeypatch):
        """volcengine 配置齐全 → 返回 VolcengineAsrProvider 单例"""
        monkeypatch.setattr(settings, "asr_provider", "volcengine")
        monkeypatch.setattr(settings, "asr_volc_api_key", "ark-test-key")
        monkeypatch.setattr(settings, "asr_volc_resource_id", "")
        monkeypatch.setattr(settings, "asr_volc_endpoint", "")
        monkeypatch.setattr(settings, "asr_timeout", 30.0)

        provider = get_asr_provider()
        assert isinstance(provider, VolcengineAsrProvider)
        assert provider.api_key == "ark-test-key"
        assert provider.resource_id == "volc.seedasr.auc"

        # 单例：第二次调用返回同一实例
        assert get_asr_provider() is provider

    def test_local_missing_url_returns_none(self, monkeypatch):
        """local provider 缺 asr_url → None"""
        monkeypatch.setattr(settings, "asr_provider", "local")
        monkeypatch.setattr(settings, "asr_url", "")
        assert get_asr_provider() is None

    def test_local_returns_provider(self, monkeypatch):
        """local provider 配置 asr_url → 返回 LocalWhisperAsrProvider"""
        monkeypatch.setattr(settings, "asr_provider", "local")
        monkeypatch.setattr(settings, "asr_url", "http://localhost:9100")
        provider = get_asr_provider()
        assert isinstance(provider, LocalWhisperAsrProvider)
        assert provider.asr_url == "http://localhost:9100"
