"""字段级加密 util — Fernet (AES-128-CBC + HMAC-SHA256) envelope。

key 来源：settings.credential_encryption_key（prod 显式设置，dev 派生兜底）。
凭证只在内存短暂解密，绝不落 env / PVC / MinIO / 日志。

用途：加密 SkillCredential.credentials_encrypted 等敏感字段。Fernet 自带随机 IV
与 HMAC 完整性校验，相同明文每次加密产物不同且防篡改。
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from pkg.common.config import settings

# dev 兜底 key material（仅 dev 环境；prod 由 assert_credential_encryption_key 拦截空值）
_DEV_KEY_MATERIAL = b"ua-credential-dev-key-do-not-use-in-prod"

# key 文件挂载点（sidecar 用；manager 不读文件，只读 settings）。projected Secret volume
# 由 k8s_manager 注入到 sidecar 容器，支持轮换无需重启（kubelet ~60s 刷新 projected volume）。
_KEY_FILE = "/etc/ua/credential-key/credential-encryption-key"


def _derive_fernet(material: bytes) -> Fernet:
    """raw material → Fernet（sha256 派生 32 字节 urlsafe base64 key）。

    统一 sha256 派生以兼容用户传入任意长度口令（Fernet 要求 32 字节 urlsafe base64 key）。
    """
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def _parse_key_materials(raw: str) -> list[bytes]:
    """多 key 轮换：settings/文件值按换行分割，newest 在前（运维轮换时前置新 key 不删旧）。

    单行（无换行）= list-of-one，向后兼容。空串 → [dev material]。
    """
    if not raw:
        return [_DEV_KEY_MATERIAL]
    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return [_DEV_KEY_MATERIAL]
    return [ln.encode("utf-8") for ln in lines]


def _load_fernet() -> MultiFernet:
    """按 settings.credential_encryption_key 构造 MultiFernet。

    支持多 key 轮换：值按换行分割为多 key（newest 在前）。MultiFernet.encrypt 用首 key
    （新写入用新 key），MultiFernet.decrypt 依次尝试（旧密文仍可解）→ 轮换 key 零 500。
    空值（dev）→ 单 key 派生自 _DEV_KEY_MATERIAL。单行值 = list-of-one，与旧单 Fernet
    字节一致（向后兼容）。
    """
    materials = _parse_key_materials(settings.credential_encryption_key)
    return MultiFernet([_derive_fernet(m) for m in materials])


def encrypt_credential(plaintext: str) -> str:
    """明文字符串 → Fernet token（str）。"""
    return _load_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_credential(token: str) -> str:
    """Fernet token → 明文。InvalidToken → ValueError。"""
    try:
        return _load_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("凭证解密失败：token 无效或 key 不匹配") from e


def encrypt_credentials_dict(data: dict) -> str:
    """dict → JSON → 加密 token。推荐用于多字段凭证（一次调用）。"""
    return encrypt_credential(json.dumps(data, ensure_ascii=False))


def decrypt_credentials_dict(token: str) -> dict:
    """加密 token → JSON → dict。"""
    return json.loads(decrypt_credential(token))
