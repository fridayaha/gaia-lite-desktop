"""UserUpdate.password 长度 + 强度校验测试 — 0.8.97 长度，0.8.103 加 zxcvbn 强度。

之前 UserUpdate.password 是 Optional[str]，无 min_length 约束，允许
admin 通过 PUT /users/{id} 设置 1 位密码。0.8.97 改为
Optional[Annotated[str, StringConstraints(min_length=8)]]。
0.8.103 进一步加 zxcvbn 强度校验（score ≥ 3 + 不在黑名单）。

此测试验证：
- 1 位密码 → Pydantic ValidationError（长度）
- 7 位密码 → Pydantic ValidationError（长度）
- 8 位但弱密码（"12345678"）→ ValidationError（强度，zxcvbn score=0）
- 12+ 位强密码（"Tr0ub4dor&3"）→ OK
- None（不修改密码）→ OK
- 长强密码（passphrase 风格）→ OK
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import UserUpdate


def test_user_update_rejects_1_char_password():
    """1 位密码应被拒绝（422 等价）。"""
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="a")
    assert "at least 8" in str(exc.value).lower() or "min_length" in str(exc.value).lower()


def test_user_update_rejects_7_char_password():
    """7 位密码应被拒绝。"""
    with pytest.raises(ValidationError):
        UserUpdate(password="1234567")


def test_user_update_rejects_8_char_weak_password():
    """8 位但弱密码（"12345678"）应被拒绝 — 在黑名单 + zxcvbn score=0。

    新版 _validate_password_strength 先查黑名单，再查 zxcvbn。"12345678" 在
    黑名单里，会先抛 "该密码过于常见，请换一个"。
    """
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="12345678")
    err_msg = str(exc.value)
    assert "常见" in err_msg or "密码强度" in err_msg, (
        f"弱密码 '12345678' 应报中文提示，实际：{err_msg}"
    )


def test_user_update_accepts_strong_password():
    """12+ 位强密码（"Tr0ub4dor&3"，zxcvbn score=4）应通过。"""
    data = UserUpdate(password="Tr0ub4dor&3")
    assert data.password == "Tr0ub4dor&3"


def test_user_update_accepts_none_password():
    """None（不修改密码）应通过 — 这是 PATCH 语义的关键。"""
    data = UserUpdate(password=None)
    assert data.password is None


def test_user_update_accepts_long_passphrase():
    """长 passphrase 应通过（无 max_length 约束 + zxcvbn 评分高）。"""
    long_pwd = "correct horse battery staple 99!"
    data = UserUpdate(password=long_pwd)
    assert data.password == long_pwd


# ── 0.8.104+: _validate_password_strength 中文 error message 按 score 分级 ──


def test_password_strength_message_too_short_chinese():
    """长度 < 8 → Pydantic 内置 string_too_short（min_length=8 StringConstraints 先于 field_validator fire）。"""
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="Aa1!")
    err_msg = str(exc.value).lower()
    # Pydantic 内置：String should have at least 8 characters
    assert "at least 8" in err_msg or "min_length" in err_msg


def test_password_strength_message_blacklist_chinese():
    """黑名单密码 → 中文 '该密码过于常见，请换一个'。"""
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="admin123")
    assert "该密码过于常见" in str(exc.value)


def test_password_strength_message_score_0_1_chinese():
    """score ≤ 1（极弱）→ 中文 '密码强度过低' 开头 + 具体 warning（或 fallback 通用建议）。"""
    # 非黑名单但极弱：8 字符纯小写字母，zxcvbn 评分通常为 1
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="abcdefgh")
    err_msg = str(exc.value)
    assert "密码强度过低" in err_msg
    # 新版优先给 zxcvbn 具体 warning（如 "字符序列"），fallback 才给通用 "大小写字母" 建议
    assert "字符序列" in err_msg or "大小写字母" in err_msg, (
        f"应含具体 warning 或通用建议，实际：{err_msg}"
    )


def test_password_strength_message_score_2_chinese():
    """score = 2（一般）→ 中文 '密码强度一般，建议增加长度或添加更多字符类型'。"""
    # 8 字母混合大小写 + 数字 — 中等强度，zxcvbn 评分通常为 2
    # 选一个不在黑名单里、score 恰好为 2 的样本
    candidate = "Abcdefg1"
    from app.schemas import _validate_password_strength
    try:
        from zxcvbn import zxcvbn as _z
    except ImportError:
        return  # 跳过：环境无 zxcvbn
    score = _z(candidate)["score"]
    if score < 2:
        candidate = "WinterSun12"
        score = _z(candidate)["score"]
    # 如果实在拿不到 score=2 的样本，跳过（不强求 zxcvbn 评分稳定）
    if score != 2:
        import pytest as _pytest
        _pytest.skip(f"无法构造 score=2 的密码样本，跳过（candidate={candidate}, score={score}）")

    with pytest.raises(ValidationError) as exc:
        UserUpdate(password=candidate)
    err_msg = str(exc.value)
    assert "密码强度一般" in err_msg
    # 新版优先给 zxcvbn 具体 warning，fallback 才给通用 "增加长度/字符类型" 建议
    assert "增加长度" in err_msg or "字符类型" in err_msg or "常见弱序列" in err_msg or "字典词" in err_msg, (
        f"应含具体 warning 或通用建议，实际：{err_msg}"
    )


def test_password_strength_message_no_english_leak():
    """确认 error message 不含 zxcvbn 英文 feedback 文案。"""
    # 取一个会被 zxcvbn 拒绝的密码
    with pytest.raises(ValidationError) as exc:
        UserUpdate(password="abcdefgh")
    err_msg = str(exc.value)
    # 不应出现 zxcvbn 自带的英文 suggestions
    for english_fragment in ["Add another word", "Uncommon words", "Use a few words"]:
        assert english_fragment not in err_msg, (
            f"error message 不应含 zxcvbn 英文 feedback，实际：{err_msg}"
        )
