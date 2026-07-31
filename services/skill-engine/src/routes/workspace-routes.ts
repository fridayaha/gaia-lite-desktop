/**
 * Workspace CRUD + file read/write routes.
 *
 * All paths prefixed with `/api/skill-engine`.
 * X-Actor-Id and X-Group-Id headers (injected by Manager proxy)
 * are used for workspace ownership.
 */

import { FastifyInstance } from "fastify";

import { extractAuthContext, isPlatformAdmin } from "../utils/auth-context.js";

export async function workspaceRoutes(app: FastifyInstance): Promise<void> {
  // ── Workspace CRUD ─────────────────────────────────────────────

  /** POST /api/skill-engine/workspaces — Create workspace */
  app.post<{
    Body: { name?: string; description?: string };
  }>("/api/skill-engine/workspaces", async (request, reply) => {
    const { name = "my-skill", description } = request.body ?? {};
    const ctx = extractAuthContext(request.headers as Record<string, string>);

    const workspace = await app.workspaceStore.create({
      name,
      description,
      userId: ctx.actorId,
      groupId: ctx.groupId,
    });
    reply.status(201);
    return {
      id: workspace.id,
      name: workspace.name,
      description: workspace.description,
      localPath: workspace.localPath,
      status: workspace.status,
    };
  });

  /** GET /api/skill-engine/workspaces — List workspaces */
  app.get("/api/skill-engine/workspaces", async (request) => {
    const ctx = extractAuthContext(request.headers as Record<string, string>);

    // Platform admins see all workspaces; others see only their own
    const filterUserId = isPlatformAdmin(ctx) ? undefined : ctx.actorId;

    const workspaces = await app.workspaceStore.list(filterUserId);
    return { workspaces };
  });

  /** GET /api/skill-engine/workspaces/:id — Get workspace detail */
  app.get<{
    Params: { id: string };
  }>("/api/skill-engine/workspaces/:id", async (request) => {
    return app.workspaceStore.get(request.params.id);
  });

  /** DELETE /api/skill-engine/workspaces/:id — Delete workspace */
  app.delete<{
    Params: { id: string };
  }>("/api/skill-engine/workspaces/:id", async (request) => {
    const { id } = request.params;

    // Stop any running engine instances for this workspace
    for (const role of ["dev", "debug"]) {
      const key = `${id}:${role}` as const;
      if (app.instanceManager.hasInstance(key)) {
        await app.instanceManager.stop(key);
      }
    }

    await app.workspaceStore.delete(id);
    return { ok: true };
  });

  // ── File operations ────────────────────────────────────────────

  /** GET /api/skill-engine/workspaces/:id/files — List file tree */
  app.get<{
    Params: { id: string };
  }>("/api/skill-engine/workspaces/:id/files", async (request) => {
    const { id } = request.params;
    // Validate workspace exists (404 via store if missing)
    await app.workspaceStore.get(id);
    const files = app.fileStore.listFiles(id);
    return { files };
  });

  /** GET /api/skill-engine/workspaces/:id/files/* — Read file
   *  传 ?base64=1 时返回 base64（供对话区 imageResolver 渲染技能产出的图片）。*/
  app.get<{
    Params: { id: string; "*": string };
    Querystring: { base64?: string };
  }>("/api/skill-engine/workspaces/:id/files/*", async (request) => {
    const { id } = request.params;
    const filePath = request.params["*"];
    if (request.query.base64) {
      return app.fileStore.readFileBase64(id, filePath);
    }
    return app.fileStore.readFile(id, filePath);
  });

  /** PUT /api/skill-engine/workspaces/:id/files/* — Write file */
  app.put<{
    Params: { id: string; "*": string };
    Body: { content?: string };
  }>("/api/skill-engine/workspaces/:id/files/*", async (request) => {
    const { id } = request.params;
    const filePath = request.params["*"];
    const { content = "" } = request.body ?? {};
    return app.fileStore.writeFile(id, filePath, content);
  });
}
