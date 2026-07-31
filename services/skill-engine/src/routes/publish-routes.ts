/**
 * Publish routes — validate / build-package / publish / scan.
 *
 * 复用 Hub 的 import + scan API（hub-client.ts）。所有路径前缀 /api/skill-engine。
 */

import { FastifyInstance } from "fastify";

import { validateWorkspace, buildPackageZip } from "../skill/package-builder.js";
import { HubError } from "../hub/hub-client.js";
import { ValidationError } from "../utils/errors.js";

export async function publishRoutes(app: FastifyInstance): Promise<void> {
  // ── validate ──────────────────────────────────────────────────

  /** POST /api/skill-engine/workspaces/:wid/validate — 校验 SKILL.md + manifest.json */
  app.post<{
    Params: { wid: string };
  }>("/api/skill-engine/workspaces/:wid/validate", async (request) => {
    const { wid } = request.params;
    // 确认工作区存在（不存在则 store 抛 404）
    await app.workspaceStore.get(wid);
    return validateWorkspace(app.fileStore, wid);
  });

  // ── build-package ─────────────────────────────────────────────

  /** GET .../build-package — 打包为 zip 下载（先校验，无效 400） */
  app.get<{
    Params: { wid: string };
  }>("/api/skill-engine/workspaces/:wid/build-package", async (request, reply) => {
    const { wid } = request.params;
    await app.workspaceStore.get(wid);
    const result = validateWorkspace(app.fileStore, wid);
    if (!result.valid) {
      return reply.code(400).send({
        error: "validate failed",
        errors: result.errors,
      });
    }
    const zip = buildPackageZip(app.fileStore, wid);
    const name = String(result.manifest?.name ?? wid);
    const version = String(result.manifest?.version ?? "0");
    const filename = `${name}-${version}.zip`;
    reply.header("Content-Type", "application/zip");
    reply.header("Content-Disposition", `attachment; filename="${filename}"`);
    return reply.send(zip);
  });

  // ── publish ───────────────────────────────────────────────────

  /** POST .../publish — build → hub import → scan → 回写 hubItemId */
  app.post<{
    Params: { wid: string };
  }>("/api/skill-engine/workspaces/:wid/publish", async (request, reply) => {
    const { wid } = request.params;
    await app.workspaceStore.get(wid);

    const result = validateWorkspace(app.fileStore, wid);
    if (!result.valid) {
      return reply.code(400).send({
        error: "validate failed",
        errors: result.errors,
      });
    }

    const zip = buildPackageZip(app.fileStore, wid);

    let imp;
    try {
      imp = await app.hubClient.importPackage(zip);
    } catch (err) {
      return reply.code(502).send({ error: hubErrMessage(err), detail: hubErrDetail(err) });
    }
    if (!imp.itemId || !imp.versionId) {
      return reply.code(502).send({ error: "hub import returned no item/version id" });
    }

    // 同步扫描（hub scan 阻塞至完成）。scan 失败不阻断发布——仍回写 itemId，
    // 让用户能去 Hub 重扫；scan 结果尽力返回。
    let scan: { riskLevel: string; findingsCount: number; findings: unknown[] } | null = null;
    try {
      const report = await app.hubClient.scanVersion(imp.versionId, "skill-engine");
      scan = {
        riskLevel: report.riskLevel,
        findingsCount: Array.isArray(report.findings) ? report.findings.length : 0,
        findings: report.findings,
      };
    } catch (err) {
      app.log.warn({ err: (err as Error).message }, "hub scan failed after import");
    }

    // 回写 hubItemId（幂等：重复发布覆盖为最新 itemId）
    await app.workspaceStore.setHubItemId(wid, imp.itemId);

    return {
      itemId: imp.itemId,
      versionId: imp.versionId,
      scan,
      warnings: imp.warnings,
    };
  });

  // ── scan ──────────────────────────────────────────────────────

  /** POST .../scan — 重扫已发布工作区的最新版本（无 hubItemId 则 400） */
  app.post<{
    Params: { wid: string };
  }>("/api/skill-engine/workspaces/:wid/scan", async (request, reply) => {
    const { wid } = request.params;
    const ws = await app.workspaceStore.get(wid);
    if (!ws.hubItemId) {
      throw new ValidationError("workspace not published yet");
    }

    const item = await app.hubClient.getItem(ws.hubItemId);
    if (!item.currentVersionId) {
      return reply.code(404).send({ error: "hub item has no current version" });
    }
    const report = await app.hubClient.scanVersion(item.currentVersionId, "skill-engine");
    return {
      riskLevel: report.riskLevel,
      findingsCount: Array.isArray(report.findings) ? report.findings.length : 0,
      findings: report.findings,
      summary: report.summary,
    };
  });
}

function hubErrMessage(err: unknown): string {
  if (err instanceof HubError) return err.message;
  return (err as Error)?.message ?? "hub request failed";
}

function hubErrDetail(err: unknown): unknown {
  if (err instanceof HubError) return err.body;
  return undefined;
}
