import { describe, it, expect } from "vitest";
import {
  extractAuthContext,
  isPlatformAdmin,
} from "../../src/utils/auth-context.js";

describe("extractAuthContext", () => {
  it("extracts all fields from headers", () => {
    const ctx = extractAuthContext({
      "x-actor-id": "user-123",
      "x-group-id": "group-456",
      "x-roles": "platform_admin,contributor",
      "x-user-name": "Alice",
      "x-user-email": "alice@example.com",
    });
    expect(ctx.actorId).toBe("user-123");
    expect(ctx.groupId).toBe("group-456");
    expect(ctx.roles).toEqual(["platform_admin", "contributor"]);
    expect(ctx.userName).toBe("Alice");
    expect(ctx.userEmail).toBe("alice@example.com");
  });

  it("uses defaults for missing headers", () => {
    const ctx = extractAuthContext({});
    expect(ctx.actorId).toBe("anonymous");
    expect(ctx.groupId).toBe("default");
    expect(ctx.roles).toEqual([]);
  });

  it("handles single role", () => {
    const ctx = extractAuthContext({ "x-roles": "contributor" });
    expect(ctx.roles).toEqual(["contributor"]);
  });
});

describe("isPlatformAdmin", () => {
  it("returns true for platform_admin", () => {
    const ctx = extractAuthContext({ "x-roles": "platform_admin" });
    expect(isPlatformAdmin(ctx)).toBe(true);
  });

  it("returns false for non-admin", () => {
    const ctx = extractAuthContext({ "x-roles": "contributor" });
    expect(isPlatformAdmin(ctx)).toBe(false);
  });

  it("returns false with no roles", () => {
    const ctx = extractAuthContext({});
    expect(isPlatformAdmin(ctx)).toBe(false);
  });
});
