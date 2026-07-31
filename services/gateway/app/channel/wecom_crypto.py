"""企业微信回调用加解密工具（自建应用与智能机器人共用）。

底层算法（AES-256-CBC + PKCS#7 + SHA1 签名）对自建应用和智能机器人完全一致，
差异仅在封装格式与 receiveid：
- 自建应用：XML 信封 ``<Encrypt>``，receiveid = CorpID
- 智能机器人：JSON 信封 ``{"encrypt": ...}``，receiveid = ``""``（空串）

本模块只提供纯函数，由各 adapter 自行处理信封封装/解析。参考企微
BizMsgCrypt 实现，与 ``wecom.py`` 原内联实现等价（抽取自该文件）。
"""
import base64
import hashlib
import random
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """企微回调签名：sha1(sort([token, timestamp, nonce, encrypt]))。"""
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def pkcs7_decode(data: bytes) -> bytes:
    """去掉 PKCS#7 填充（企微用 32 字节块）。"""
    pad = data[-1]
    if pad < 1 or pad > 32:
        raise ValueError("invalid padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise ValueError("malformed padding")
    return data[:-pad]


def decrypt_message(encoding_aes_key: str, msg_encrypt: str) -> bytes:
    """解密企微 AES-256-CBC 加密消息，返回明文 bytes。

    明文结构：random(16) + msg_len(4, 大端) + msg + receiveid。
    本函数只取 msg（按 msg_len 截取），不校验 receiveid——因此自建应用
    (receiveid=CorpID) 与智能机器人 (receiveid="") 都能用。
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    decryptor = cipher.decryptor()
    raw = decryptor.update(base64.b64decode(msg_encrypt)) + decryptor.finalize()
    raw = pkcs7_decode(raw)
    msg_len = struct.unpack(">I", raw[16:20])[0]
    msg = raw[20 : 20 + msg_len]
    return msg


def encrypt_message(encoding_aes_key: str, receive_id: str, plaintext: str) -> str:
    """加密明文为企微 AES-256-CBC 密文（base64）。

    ``receive_id``：自建应用传 CorpID，智能机器人传空串 ``""``。
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    raw = bytearray()
    raw.extend(b" " * 16)  # random 占位，下方替换为真随机
    raw.extend(struct.pack(">I", len(plaintext.encode())))
    raw.extend(plaintext.encode())
    raw.extend(receive_id.encode())

    # PKCS7 padding to 32-byte blocks
    block_size = 32
    pad_len = block_size - (len(raw) % block_size)
    raw.extend(bytes([pad_len]) * pad_len)

    # Replace first 16 bytes with random
    rand = bytearray(16)
    for i in range(16):
        rand[i] = random.randint(0, 255)
    raw[0:16] = rand

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(bytes(raw)) + encryptor.finalize()
    return base64.b64encode(encrypted).decode()


def decrypt_media(encoding_aes_key: str, ciphertext: bytes) -> bytes:
    """解密智能机器人媒体文件（图片/文件/视频）下载密文。

    企微文档未明确媒体密文是否含 16B 随机前缀 + 4B msg_len + receiveid 头（同回调
    ``Encrypt`` 信封），还是纯 PKCS7 填充的文件字节。本函数防御式处理：AES-256-CBC
    解密 + PKCS7 去填充后，若开头符合信封结构（msg_len 合理）则剥离头部取 msg，
    否则把去填充后的字节整体作为文件字节返回。

    AES key/IV/PKCS7 与回调加解密一致（IV=AESKey[:16]，填充到 32 字节倍数）。
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    decryptor = cipher.decryptor()
    raw = decryptor.update(ciphertext) + decryptor.finalize()
    try:
        raw = pkcs7_decode(raw)
    except ValueError:
        return b""  # 填充非法，密文损坏或非预期格式
    # 尝试信封格式：16B random + 4B msg_len(大端) + msg + receiveid
    if len(raw) >= 20:
        msg_len = struct.unpack(">I", raw[16:20])[0]
        if 0 < msg_len <= len(raw) - 20:
            return raw[20 : 20 + msg_len]
    # 回退：去填充后的字节即为文件内容
    return raw
