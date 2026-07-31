import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import AdmZip from "adm-zip";

import { FileStore } from "../../src/workspace/file-store.js";
import { validateWorkspace, buildPackageZip } from "../../src/skill/package-builder.js";

const VALID_MANIFEST = {
  name: "chart-skill",
  version: "0.1.0",
  author: "skilldev",
  description: "图表绘制技能",
  engine: "hermes",
  type: "skill",
};

const VALID_SKILL = [
  "---",
  "name: chart-skill",
  "version: 0.1.0",
  "description: 图表绘制技能",
  "author: skilldev",
  "---",
  "",
  "# 图表绘制技能",
].join("\n");

describe("package-builder", () => {
  let baseDir: string;
  let fileStore: FileStore;
  const wid = "ws-test";

  beforeEach(() => {
    baseDir = mkdtempSync(join(tmpdir(), "pkg-builder-"));
    fileStore = new FileStore(baseDir);
    mkdirSync(join(baseDir, wid), { recursive: true });
  });

  afterEach(() => rmSync(baseDir, { recursive: true, force: true }));

  function writeFiles(skill: string | null, manifest: string | null) {
    if (skill !== null) writeFileSync(join(baseDir, wid, "SKILL.md"), skill);
    if (manifest !== null) writeFileSync(join(baseDir, wid, "manifest.json"), manifest);
  }

  describe("validateWorkspace", () => {
    it("passes for valid SKILL.md + manifest", () => {
      writeFiles(VALID_SKILL, JSON.stringify(VALID_MANIFEST));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(true);
      expect(r.errors).toEqual([]);
      expect(r.manifest?.name).toBe("chart-skill");
    });

    it("reports missing SKILL.md", () => {
      writeFiles(null, JSON.stringify(VALID_MANIFEST));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "SKILL.md")).toBe(true);
    });

    it("reports missing frontmatter", () => {
      writeFiles("no frontmatter here", JSON.stringify(VALID_MANIFEST));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "SKILL.md")).toBe(true);
    });

    it("accepts SKILL.md frontmatter with only name+description (version/author in manifest)", () => {
      // skill-creator 教学只写 name+description，version/author 由 manifest 承担
      const skill = [
        "---",
        "name: chart-skill",
        "description: 图表绘制技能",
        "---",
        "",
        "# 图表绘制技能",
      ].join("\n");
      writeFiles(skill, JSON.stringify(VALID_MANIFEST));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(true);
      expect(r.errors).toEqual([]);
    });

    it("reports missing manifest.json", () => {
      writeFiles(VALID_SKILL, null);
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json")).toBe(true);
    });

    it("reports invalid JSON manifest", () => {
      writeFiles(VALID_SKILL, "{ not json }");
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json")).toBe(true);
    });

    it("reports missing required manifest fields", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ name: "x", type: "skill" }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:version")).toBe(true);
      expect(r.errors.some((e) => e.field === "manifest.json:engine")).toBe(true);
    });

    it("rejects type != skill", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ ...VALID_MANIFEST, type: "agent" }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:type")).toBe(true);
    });

    it("rejects engine outside enum", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ ...VALID_MANIFEST, engine: "bogus" }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:engine")).toBe(true);
    });

    it("accepts engine openclaw", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ ...VALID_MANIFEST, engine: "openclaw" }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(true);
    });

    it("rejects config_params not an array", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ ...VALID_MANIFEST, config_params: { x: 1 } }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params")).toBe(true);
    });

    it("rejects config_params item missing name/label/type", () => {
      writeFiles(VALID_SKILL, JSON.stringify({ ...VALID_MANIFEST, config_params: [{ name: "a" }] }));
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params[0].label")).toBe(true);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params[0].type")).toBe(true);
    });

    it("rejects config_params type outside enum", () => {
      writeFiles(
        VALID_SKILL,
        JSON.stringify({ ...VALID_MANIFEST, config_params: [{ name: "a", label: "A", type: "blob" }] }),
      );
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params[0].type")).toBe(true);
    });

    it("rejects secret not boolean", () => {
      writeFiles(
        VALID_SKILL,
        JSON.stringify({ ...VALID_MANIFEST, config_params: [{ name: "a", label: "A", type: "string", secret: "yes" }] }),
      );
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params[0].secret")).toBe(true);
    });

    it("rejects select without options", () => {
      writeFiles(
        VALID_SKILL,
        JSON.stringify({ ...VALID_MANIFEST, config_params: [{ name: "a", label: "A", type: "select" }] }),
      );
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.field === "manifest.json:config_params[0].options")).toBe(true);
    });

    it("rejects duplicate config_params name", () => {
      writeFiles(
        VALID_SKILL,
        JSON.stringify({
          ...VALID_MANIFEST,
          config_params: [
            { name: "a", label: "A", type: "string" },
            { name: "a", label: "A2", type: "string" },
          ],
        }),
      );
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(false);
      expect(r.errors.some((e) => e.message.includes("重复"))).toBe(true);
    });

    it("accepts valid config_params with secret", () => {
      writeFiles(
        VALID_SKILL,
        JSON.stringify({
          ...VALID_MANIFEST,
          config_params: [
            { name: "api_key", label: "API Key", type: "string", secret: true },
            { name: "mode", label: "Mode", type: "select", options: ["a", "b"] },
          ],
        }),
      );
      const r = validateWorkspace(fileStore, wid);
      expect(r.valid).toBe(true);
    });
  });

  describe("buildPackageZip", () => {
    it("includes skill files and excludes .pi/node_modules", () => {
      writeFiles(VALID_SKILL, JSON.stringify(VALID_MANIFEST));
      // extra file + nested dir
      mkdirSync(join(baseDir, wid, "scripts"), { recursive: true });
      writeFileSync(join(baseDir, wid, "scripts", "run.sh"), "echo hi");
      // excluded dirs
      mkdirSync(join(baseDir, wid, ".pi", "skills"), { recursive: true });
      writeFileSync(join(baseDir, wid, ".pi", "secret"), "should not ship");
      mkdirSync(join(baseDir, wid, "node_modules"), { recursive: true });
      writeFileSync(join(baseDir, wid, "node_modules", "x"), "nope");

      const buf = buildPackageZip(fileStore, wid);
      const zip = new AdmZip(buf);
      const names = zip.getEntries().map((e) => e.entryName);
      expect(names).toContain("SKILL.md");
      expect(names).toContain("manifest.json");
      expect(names).toContain("scripts/run.sh");
      expect(names.some((n) => n.startsWith(".pi/"))).toBe(false);
      expect(names.some((n) => n.startsWith("node_modules/"))).toBe(false);
    });

    it("produces a non-empty zip buffer", () => {
      writeFiles(VALID_SKILL, JSON.stringify(VALID_MANIFEST));
      const buf = buildPackageZip(fileStore, wid);
      expect(buf.length).toBeGreaterThan(0);
    });
  });
});
