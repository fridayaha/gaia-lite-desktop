/**
 * Credential encryption at rest — AES-256-GCM envelope.
 *
 * 用于 skill-studio 的 config_params secret:true 加密存储。skill-engine 自存自解
 *（不与 manager 的 Python Fernet 跨兼容——两套独立存储，无需互读）。
 *
 * Key 从 process.env.UA_CREDENTIAL_ENCRYPTION_KEY 派生（与 manager 同名 env，云端
 * 同值），dev 环境留空时回落到固定 material（仅 dev，对齐 manager 的 fallback）。
 *
 * 密文格式：base64( iv(12B) ‖ ciphertext ‖ authTag(16B) )。
 */

import { createHash, randomBytes, createCipheriv, createDecipheriv } from "node:crypto";

const DEV_KEY_MATERIAL = "ua-credential-dev-key-do-not-use-in-prod";
const IV_LEN = 12;
const AUTH_TAG_LEN = 16;

/** 派生 32 字节 AES-256 key（sha256(material)）。缓存避免每次调用重算。 */
let _cachedKeyMaterial = "";
let _cachedKey: Buffer | null = null;

function getKey(): Buffer {
  const material = process.env.UA_CREDENTIAL_ENCRYPTION_KEY ?? "";
  const effective = material || DEV_KEY_MATERIAL;
  if (effective === _cachedKeyMaterial && _cachedKey) return _cachedKey;
  _cachedKeyMaterial = effective;
  _cachedKey = createHash("sha256").update(effective).digest(); // 32 bytes
  return _cachedKey;
}

/** 加密单个字符串 → base64 token。 */
export function encryptCredential(plaintext: string): string {
  const key = getKey();
  const iv = randomBytes(IV_LEN);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, ct, tag]).toString("base64");
}

/** 解密 base64 token → 原文。失败抛 Error（token 损坏 / key 不匹配 / 篡改）。 */
export function decryptCredential(token: string): string {
  const buf = Buffer.from(token, "base64");
  if (buf.length < IV_LEN + AUTH_TAG_LEN) {
    throw new Error("invalid credential token: too short");
  }
  const iv = buf.subarray(0, IV_LEN);
  const tag = buf.subarray(buf.length - AUTH_TAG_LEN);
  const ct = buf.subarray(IV_LEN, buf.length - AUTH_TAG_LEN);
  const key = getKey();
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  const pt = Buffer.concat([decipher.update(ct), decipher.final()]);
  return pt.toString("utf8");
}

/**
 * 加密一个 dict（JSON envelope）。整个 dict 序列化后一次加密，
 * 整列覆盖语义（对齐 manager encrypt_credentials_dict）。
 */
export function encryptCredentialsDict(dict: Record<string, string>): string {
  return encryptCredential(JSON.stringify(dict));
}

/** 解密 dict token。token 为空时返回 {}（未配置任何密钥）。 */
export function decryptCredentialsDict(token: string): Record<string, string> {
  if (!token) return {};
  return JSON.parse(decryptCredential(token)) as Record<string, string>;
}
