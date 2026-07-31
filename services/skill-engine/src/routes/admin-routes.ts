/**
 * Admin monitoring routes.
 *
 * All paths prefixed with `/api/skill-engine`.
 * Requires platform_admin role in X-Roles header.
 */

import { totalmem, freemem, cpus, loadavg } from "node:os";
import { FastifyInstance } from "fastify";

import { extractAuthContext, isPlatformAdmin } from "../utils/auth-context.js";
import { SkillEngineError } from "../utils/errors.js";

export async function adminRoutes(app: FastifyInstance): Promise<void> {
  /** GET /api/skill-engine/admin/stats — Per-instance stats */
  app.get("/api/skill-engine/admin/stats", async (request) => {
    const ctx = extractAuthContext(request.headers as Record<string, string>);
    if (!isPlatformAdmin(ctx)) {
      throw new SkillEngineError("Platform admin role required", 403);
    }
    return app.instanceManager.getStats();
  });

  /** GET /api/skill-engine/admin/resources — System resource overview */
  app.get("/api/skill-engine/admin/resources", async (request) => {
    const ctx = extractAuthContext(request.headers as Record<string, string>);
    if (!isPlatformAdmin(ctx)) {
      throw new SkillEngineError("Platform admin role required", 403);
    }
    return {
      totalMemoryMB: Math.round(totalmem() / 1024 / 1024),
      freeMemoryMB: Math.round(freemem() / 1024 / 1024),
      cpuCount: cpus().length,
      loadAvg: loadavg(),
      ...app.instanceManager.getResources(),
    };
  });
}
