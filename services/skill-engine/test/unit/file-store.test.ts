import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";

import { FileStore } from "../../src/workspace/file-store.js";

// Helper: collect entry paths for assertion readability.
const pathsOf = (entries: { path: string }[]) =>
  entries.map((e) => e.path).sort();

describe("FileStore", () => {
  let baseDir: string;
  let fileStore: FileStore;
  let workspaceId: string;

  beforeEach(() => {
    baseDir = mkdtempSync(join(tmpdir(), "skill-engine-test-"));
    fileStore = new FileStore(baseDir);

    // Create workspace directory directly (no WorkspaceStore/DB needed)
    workspaceId = randomUUID();
    mkdirSync(join(baseDir, workspaceId), { recursive: true });
  });

  afterEach(() => {
    rmSync(baseDir, { recursive: true, force: true });
  });

  describe("readFile", () => {
    it("reads a text file", () => {
      writeFileSync(
        join(baseDir, workspaceId, "hello.txt"),
        "Hello, World!",
        "utf-8",
      );
      const result = fileStore.readFile(workspaceId, "hello.txt");
      expect(result.content).toBe("Hello, World!");
      expect(result.isText).toBe(true);
      expect(result.size).toBe(13);
    });

    it("reads a file in a subdirectory", () => {
      mkdirSync(join(baseDir, workspaceId, "scripts"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, "scripts", "run.py"),
        "print('hi')",
        "utf-8",
      );
      const result = fileStore.readFile(workspaceId, "scripts/run.py");
      expect(result.content).toBe("print('hi')");
    });

    it("detects binary files", () => {
      const buf = Buffer.alloc(16);
      buf[4] = 0; // null byte
      writeFileSync(join(baseDir, workspaceId, "data.bin"), buf);
      const result = fileStore.readFile(workspaceId, "data.bin");
      expect(result.isText).toBe(false);
      expect(result.content).toBeInstanceOf(Buffer);
    });
  });

  describe("readFileBase64", () => {
    it("returns base64 + is_image true for a PNG", () => {
      const buf = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
      mkdirSync(join(baseDir, workspaceId, "output", "charts"), { recursive: true });
      writeFileSync(join(baseDir, workspaceId, "output", "charts", "x.png"), buf);
      const result = fileStore.readFileBase64(workspaceId, "output/charts/x.png");
      expect(result.isImage).toBe(true);
      expect(result.mime).toBe("image/png");
      expect(result.contentB64).toBe(buf.toString("base64"));
      expect(result.size).toBe(buf.length);
    });

    it("returns is_image false for a non-image file", () => {
      writeFileSync(join(baseDir, workspaceId, "notes.txt"), "hi", "utf-8");
      const result = fileStore.readFileBase64(workspaceId, "notes.txt");
      expect(result.isImage).toBe(false);
      expect(result.mime).toBe("application/octet-stream");
    });

    it("base64-encodes SVG (text) too", () => {
      writeFileSync(join(baseDir, workspaceId, "logo.svg"), "<svg></svg>", "utf-8");
      const result = fileStore.readFileBase64(workspaceId, "logo.svg");
      expect(result.isImage).toBe(true);
      expect(result.mime).toBe("image/svg+xml");
      expect(result.contentB64).toBe(Buffer.from("<svg></svg>").toString("base64"));
    });

    it("throws on missing file", () => {
      expect(() => fileStore.readFileBase64(workspaceId, "nope.png")).toThrow();
    });
  });

  describe("writeFile", () => {
    it("writes a file", () => {
      const result = fileStore.writeFile(workspaceId, "test.txt", "content");
      expect(result.ok).toBe(true);
      expect(result.path).toBe("test.txt");
    });

    it("creates parent directories", () => {
      const result = fileStore.writeFile(
        workspaceId,
        "deep/nested/file.txt",
        "deep content",
      );
      expect(result.ok).toBe(true);

      // Verify it can be read back
      const read = fileStore.readFile(workspaceId, "deep/nested/file.txt");
      expect(read.content).toBe("deep content");
    });
  });

  describe("listFiles", () => {
    it("lists user-visible files and directories, excluding internals", () => {
      // User-visible skill files
      writeFileSync(join(baseDir, workspaceId, "SKILL.md"), "# skill", "utf-8");
      writeFileSync(
        join(baseDir, workspaceId, "manifest.json"),
        "{}",
        "utf-8",
      );
      mkdirSync(join(baseDir, workspaceId, "scripts"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, "scripts", "draw_chart.py"),
        "print('chart')",
        "utf-8",
      );

      // Engine internals / VCS noise — must be excluded
      mkdirSync(join(baseDir, workspaceId, ".pi"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, ".pi", "internal.txt"),
        "internal",
        "utf-8",
      );
      mkdirSync(join(baseDir, workspaceId, ".git"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, ".git", "config"),
        "git",
        "utf-8",
      );
      mkdirSync(join(baseDir, workspaceId, "node_modules"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, "node_modules", "x.js"),
        "module.exports",
        "utf-8",
      );
      writeFileSync(
        join(baseDir, workspaceId, ".DS_Store"),
        "ds",
        "utf-8",
      );

      const entries = fileStore.listFiles(workspaceId);

      // Visible entries present (files + the scripts directory)
      const paths = pathsOf(entries);
      expect(paths).toContain("SKILL.md");
      expect(paths).toContain("manifest.json");
      expect(paths).toContain("scripts");
      expect(paths).toContain("scripts/draw_chart.py");

      // Internals excluded
      expect(paths).not.toContain(".pi");
      expect(paths).not.toContain(".pi/internal.txt");
      expect(paths).not.toContain(".git");
      expect(paths).not.toContain(".git/config");
      expect(paths).not.toContain("node_modules");
      expect(paths).not.toContain("node_modules/x.js");
      expect(paths).not.toContain(".DS_Store");
    });

    it("returns directory entries with isDir true and size 0", () => {
      mkdirSync(join(baseDir, workspaceId, "scripts"), { recursive: true });
      writeFileSync(
        join(baseDir, workspaceId, "scripts", "a.py"),
        "x",
        "utf-8",
      );
      const entries = fileStore.listFiles(workspaceId);
      const dir = entries.find((e) => e.path === "scripts");
      expect(dir).toBeDefined();
      expect(dir!.isDir).toBe(true);
      expect(dir!.size).toBe(0);
      expect(typeof dir!.modifiedAt).toBe("string");
    });

    it("returns file entries with size and isDir false", () => {
      writeFileSync(join(baseDir, workspaceId, "SKILL.md"), "hello", "utf-8");
      const entries = fileStore.listFiles(workspaceId);
      const file = entries.find((e) => e.path === "SKILL.md");
      expect(file).toBeDefined();
      expect(file!.isDir).toBe(false);
      expect(file!.size).toBe(5);
    });

    it("uses posix relative paths (no absolute, no ..)", () => {
      mkdirSync(join(baseDir, workspaceId, "refs"), { recursive: true });
      writeFileSync(join(baseDir, workspaceId, "refs", "a.md"), "x", "utf-8");
      const entries = fileStore.listFiles(workspaceId);
      for (const e of entries) {
        expect(e.path).not.toMatch(/^\//); // not absolute
        expect(e.path).not.toContain("..");
        expect(e.path).not.toContain("\\"); // posix separator only
      }
    });

    it("returns empty array when workspace directory does not exist", () => {
      const entries = fileStore.listFiles("nonexistent-workspace-id");
      expect(entries).toEqual([]);
    });
  });

  describe("safeResolve", () => {
    it("rejects path traversal with ..", () => {
      expect(() =>
        fileStore.safeResolve(workspaceId, "../../../etc/passwd"),
      ).toThrow(/traversal/i);
    });

    it("rejects absolute paths", () => {
      expect(() =>
        fileStore.safeResolve(workspaceId, "/etc/passwd"),
      ).toThrow(/traversal/i);
    });

    it("rejects null bytes", () => {
      expect(() =>
        fileStore.safeResolve(workspaceId, "file\0.txt"),
      ).toThrow(/null byte/i);
    });

    it("resolves normal paths", () => {
      const resolved = fileStore.safeResolve(workspaceId, "scripts/run.py");
      expect(resolved).toBe(join(baseDir, workspaceId, "scripts", "run.py"));
    });
  });
});
