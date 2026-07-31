"""字段级加密 util 测试 — Fernet round-trip / 篡改 / prod key 校验。"""

import pytest
from app.core.crypto import (
    decrypt_credential,
    decrypt_credentials_dict,
    encrypt_credential,
    encrypt_credentials_dict,
)

from pkg.common.config import settings
from pkg.common.security import assert_credential_encryption_key


def test_encrypt_decrypt_roundtrip():
    plaintext = "sk-super-secret-api-key"
    token = encrypt_credential(plaintext)
    assert token != plaintext
    assert decrypt_credential(token) == plaintext


def test_encrypt_dict_roundtrip():
    data = {"api_key": "sk-xxx", "api_secret": "yyy", "n": 1, "nested": {"a": "b"}}
    token = encrypt_credentials_dict(data)
    assert token != str(data)
    assert decrypt_credentials_dict(token) == data


def test_different_tokens_for_same_plaintext():
    """Fernet 自带随机 IV，相同明文每次加密产物不同。"""
    p = "same-secret"
    t1 = encrypt_credential(p)
    t2 = encrypt_credential(p)
    assert t1 != t2
    assert decrypt_credential(t1) == decrypt_credential(t2) == p


def test_decrypt_tampered_token_raises():
    token = encrypt_credential("secret")
    tampered = token[:-4] + "AAAA"
    with pytest.raises(ValueError):
        decrypt_credential(tampered)


def test_decrypt_invalid_token_raises():
    with pytest.raises(ValueError):
        decrypt_credential("not-a-valid-fernet-token")


def test_assert_key_dev_allows_empty():
    """dev 环境空 key 不报错（走派生兜底）。"""
    assert_credential_encryption_key("", "dev")
    assert_credential_encryption_key(None, "dev")


def test_assert_key_prod_requires_nonempty():
    """prod 环境空 key 拒绝启动。"""
    with pytest.raises(RuntimeError):
        assert_credential_encryption_key("", "prod")
    with pytest.raises(RuntimeError):
        assert_credential_encryption_key(None, "prod")


def test_assert_key_prod_ok_with_value():
    """prod 环境有值不抛。"""
    assert_credential_encryption_key("some-key", "prod")


def test_dev_key_derivable_when_empty(monkeypatch):
    """dev 空 key 时 _load_fernet 用固定 material 派生，可正常加解密。"""
    monkeypatch.setattr(settings, "credential_encryption_key", "")
    token = encrypt_credential("x")
    assert decrypt_credential(token) == "x"


# ── 多 key 轮换（MultiFernet）───────────────────────────────────────────


def test_multikey_rotation_old_decrypts_new_writes(monkeypatch):
    """轮换：旧 key 加密的密文在新 key 前置后仍可解；新写入用新 key（首 key）。"""
    monkeypatch.setattr(settings, "credential_encryption_key", "old-key")
    old_token = encrypt_credential("secret-x")

    monkeypatch.setattr(settings, "credential_encryption_key", "new-key\nold-key")
    new_token = encrypt_credential("secret-y")

    # 旧密文仍可解（MultiFernet 依次尝试 new → old）
    assert decrypt_credential(old_token) == "secret-x"
    # 新密文可解
    assert decrypt_credential(new_token) == "secret-y"
    # 新写入用首 key（new-key）：仅 new-key 也能解
    monkeypatch.setattr(settings, "credential_encryption_key", "new-key")
    assert decrypt_credential(new_token) == "secret-y"


def test_multikey_invalid_when_no_key_matches(monkeypatch):
    """密文的 key 不在多 key 列表中 → ValueError。"""
    monkeypatch.setattr(settings, "credential_encryption_key", "unknown-key")
    token = encrypt_credential("secret")
    monkeypatch.setattr(settings, "credential_encryption_key", "new-key\nold-key")
    with pytest.raises(ValueError):
        decrypt_credential(token)


def test_single_key_no_newline_backward_compat(monkeypatch):
    """单行 key（无换行）= list-of-one，与旧单 Fernet 行为一致。"""
    monkeypatch.setattr(settings, "credential_encryption_key", "just-one-key")
    token = encrypt_credential("v")
    assert decrypt_credential(token) == "v"
