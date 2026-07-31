import { describe, it, expect } from "vitest";
import { devBootstrap, debugBootstrap } from "../../src/engine/skill-bootstrap.js";

describe("devBootstrap", () => {
  it("returns skill-creator dev skill config", () => {
    const config = devBootstrap();
    expect(config.skillName).toBe("skill-creator");
    expect(config.tools).toEqual([
      "read",
      "write",
      "edit",
      "bash",
      "grep",
      "find",
      "ls",
      "clarify",
    ]);
    expect(config.excludeTools).toBeUndefined();
  });
});

describe("debugBootstrap", () => {
  it("returns user skill config with full execution tools", () => {
    const config = debugBootstrap();
    expect(config.skillName).toBe("user-skill");
    expect(config.tools).toEqual(["read", "write", "edit", "bash", "grep", "find", "ls"]);
    expect(config.excludeTools).toBeUndefined();
  });
});
