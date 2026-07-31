"""messages.py 提示常量单元测试。

校验：所有常量非空、带 emoji 前缀、长度合理（简洁一句）。
"""
import pytest

from app import messages


class TestMessages:
    """提示语常量完整性校验。"""

    @pytest.mark.parametrize("name", [
        "ENGINE_STARTING", "PROFILE_PREPARING",
        "VOICE_RECOGNIZE_FAILED", "REPLY_FAILED", "STREAM_INTERRUPTED", "CARD_RENDER_FAILED",
        "ENGINE_START_FAILED", "AGENT_UNAVAILABLE", "CARD_DEGRADED_TO_TEXT",
        "NOT_BOUND", "ACCESS_DENIED", "MESSAGE_FORMAT_INVALID",
        "SESSION_RESET", "SESSION_RESET_FAILED",
    ])
    def test_constant_non_empty_str(self, name):
        """所有提示常量是非空字符串"""
        val = getattr(messages, name)
        assert isinstance(val, str) and len(val) > 0, f"{name} 应为非空字符串"

    @pytest.mark.parametrize("name,emoji", [
        ("ENGINE_STARTING", "🤖"),
        ("PROFILE_PREPARING", "🕐"),
        ("VOICE_RECOGNIZE_FAILED", "🎤"),
        ("REPLY_FAILED", "⚠️"),
        ("STREAM_INTERRUPTED", "⚠️"),
        ("CARD_RENDER_FAILED", "⚠️"),
        ("ENGINE_START_FAILED", "🛠️"),
        ("AGENT_UNAVAILABLE", "🛠️"),
        ("CARD_DEGRADED_TO_TEXT", "⚠️"),
        ("NOT_BOUND", "⚠️"),
        ("ACCESS_DENIED", "🚫"),
        ("MESSAGE_FORMAT_INVALID", "⚠️"),
        ("SESSION_RESET", "✅"),
        ("SESSION_RESET_FAILED", "⚠️"),
    ])
    def test_constant_has_emoji_prefix(self, name, emoji):
        """每个提示带约定 emoji 前缀（统一风格）"""
        val = getattr(messages, name)
        assert val.startswith(emoji), f"{name} 应以 {emoji} 开头，实际: {val[:6]}"

    @pytest.mark.parametrize("name", [
        "ENGINE_STARTING", "PROFILE_PREPARING", "VOICE_RECOGNIZE_FAILED", "REPLY_FAILED",
        "ENGINE_START_FAILED", "AGENT_UNAVAILABLE", "NOT_BOUND", "ACCESS_DENIED",
        "MESSAGE_FORMAT_INVALID", "CARD_RENDER_FAILED",
        "SESSION_RESET", "SESSION_RESET_FAILED",
    ])
    def test_constant_concise(self, name):
        """提示语简洁（≤30 字，含 emoji）"""
        val = getattr(messages, name)
        assert len(val) <= 30, f"{name} 过长（{len(val)} 字）：{val}"

    def test_temp_failure_prompts_suggest_retry(self):
        """临时故障类提示含'重试'（引导用户重试）"""
        for name in ["VOICE_RECOGNIZE_FAILED", "REPLY_FAILED", "CARD_RENDER_FAILED", "MESSAGE_FORMAT_INVALID", "SESSION_RESET_FAILED"]:
            val = getattr(messages, name)
            assert "重试" in val or "重新" in val, f"{name} 临时故障应引导重试：{val}"

    def test_admin_prompts_mention_admin(self):
        """配置问题类提示含'联系管理员'"""
        for name in ["ENGINE_START_FAILED", "AGENT_UNAVAILABLE", "NOT_BOUND", "ACCESS_DENIED"]:
            val = getattr(messages, name)
            assert "管理员" in val, f"{name} 应提示联系管理员：{val}"
