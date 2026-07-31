import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  encryptCredential,
  decryptCredential,
  encryptCredentialsDict,
  decryptCredentialsDict,
} from "../../src/utils/crypto.js";

const ORIGINAL_KEY = process.env.UA_CREDENTIAL_ENCRYPTION_KEY;

describe("crypto (AES-256-GCM)", () => {
  beforeEach(() => {
    process.env.UA_CREDENTIAL_ENCRYPTION_KEY = "test-key-for-skill-engine";
  });
  afterEach(() => {
    process.env.UA_CREDENTIAL_ENCRYPTION_KEY = ORIGINAL_KEY;
  });

  describe("encryptCredential / decryptCredential", () => {
    it("round-trips a string", () => {
      const token = encryptCredential("sk-test-12345");
      expect(token).not.toBe("sk-test-12345");
      expect(decryptCredential(token)).toBe("sk-test-12345");
    });

    it("produces different ciphertext for same plaintext (random IV)", () => {
      const a = encryptCredential("same");
      const b = encryptCredential("same");
      expect(a).not.toBe(b);
      expect(decryptCredential(a)).toBe("same");
      expect(decryptCredential(b)).toBe("same");
    });

    it("round-trips unicode / long strings", () => {
      const s = "密钥-测试-" + "x".repeat(1000) + "-中文";
      expect(decryptCredential(encryptCredential(s))).toBe(s);
    });

    it("throws on tampered ciphertext (auth tag mismatch)", () => {
      const token = encryptCredential("secret");
      const buf = Buffer.from(token, "base64");
      buf[buf.length - 1] ^= 0xff; // 翻转 authTag 最后一字节
      const tampered = buf.toString("base64");
      expect(() => decryptCredential(tampered)).toThrow();
    });

    it("throws on wrong key", () => {
      const token = encryptCredential("secret");
      process.env.UA_CREDENTIAL_ENCRYPTION_KEY = "different-key";
      expect(() => decryptCredential(token)).toThrow();
    });

    it("throws on too-short token", () => {
      expect(() => decryptCredential(Buffer.from("short").toString("base64"))).toThrow();
    });
  });

  describe("encryptCredentialsDict / decryptCredentialsDict", () => {
    it("round-trips a dict", () => {
      const dict = { api_key: "sk-xxx", token: "tok-yyy" };
      const token = encryptCredentialsDict(dict);
      expect(decryptCredentialsDict(token)).toEqual(dict);
    });

    it("returns {} for empty token", () => {
      expect(decryptCredentialsDict("")).toEqual({});
    });

    it("empty dict round-trips", () => {
      const token = encryptCredentialsDict({});
      expect(decryptCredentialsDict(token)).toEqual({});
    });
  });

  describe("dev fallback key", () => {
    it("works without UA_CREDENTIAL_ENCRYPTION_KEY (dev fallback)", () => {
      delete process.env.UA_CREDENTIAL_ENCRYPTION_KEY;
      const token = encryptCredential("dev-secret");
      expect(decryptCredential(token)).toBe("dev-secret");
    });
  });
});
