/**
 * Engine session routes — spawn/stop/prompt/steer/followUp/abort/state/events.
 *
 * All paths prefixed with `/api/skill-engine`.
 *
 * Session ID (`sid`) is the role: "dev" or "debug".
 * Internal composite key: `{workspaceId}:{role}`.
 */

import { randomUUID } from "node:crypto";
import { FastifyInstance } from "fastify";

import type { EngineSessionKey } from "../types/engine.js";
import { InstanceNotFoundError } from "../utils/errors.js";
import { devBootstrap, debugBootstrap } from "../engine/skill-bootstrap.js";

export async function engineRoutes(app: FastifyInstance): Promise<void> {
  // ── Spawn / Stop ───────────────────────────────────────────────

  /** POST /api/skill-engine/workspaces/:wid/sessions — Start engine instance */
  app.post<{
    Params: { wid: string };
    Body: { role?: "dev" | "debug" };
  }>("/api/skill-engine/workspaces/:wid/sessions", async (request) => {
    const { wid } = request.params;
    const role = request.body?.role ?? "dev";

    // Ensure workspace exists
    const ws = await app.workspaceStore.get(wid);
    const cwd = ws.localPath;

    // Use skill-bootstrap for role-based tool/skill configuration.
    // (The worker itself mirrors SKILL.md into .pi/skills/ for the debug role
    //  and injects the dev persona via appendSystemPrompt — no content passed here.)
    const bootstrap = role === "dev" ? devBootstrap() : debugBootstrap();

    const options = {
      cwd,
      tools: bootstrap.tools,
      excludeTools: bootstrap.excludeTools,
    };

    const instance = await app.instanceManager.spawn(wid, role, options);
    const sessionId = role; // sid = role

    return {
      sessionId,
      role,
      status: instance.ready ? "ready" : "starting",
    };
  });

  /** DELETE /api/skill-engine/workspaces/:wid/sessions/:sid — Stop engine instance */
  app.delete<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid", async (request) => {
    const { wid, sid } = request.params;
    const key: EngineSessionKey = `${wid}:${sid}`;
    await app.instanceManager.stop(key);
    return { ok: true };
  });

  // ── Prompt / Steer / FollowUp / Abort ──────────────────────────

  /** POST .../sessions/:sid/prompt — Send prompt */
  app.post<{
    Params: { wid: string; sid: string };
    Body: { message?: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/prompt", async (request) => {
    const { wid, sid } = request.params;
    const { message } = request.body ?? {};
    if (!message) {
      return { success: false, error: "message is required" };
    }
    const key: EngineSessionKey = `${wid}:${sid}`;
    return app.instanceManager.prompt(key, message);
  });

  /** POST .../sessions/:sid/steer — Mid-turn steering */
  app.post<{
    Params: { wid: string; sid: string };
    Body: { message?: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/steer", async (request) => {
    const { wid, sid } = request.params;
    const { message } = request.body ?? {};
    if (!message) {
      return { success: false, error: "message is required" };
    }
    const key: EngineSessionKey = `${wid}:${sid}`;
    return app.instanceManager.steer(key, message);
  });

  /** POST .../sessions/:sid/follow-up — Queue follow-up message */
  app.post<{
    Params: { wid: string; sid: string };
    Body: { message?: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/follow-up", async (request) => {
    const { wid, sid } = request.params;
    const { message } = request.body ?? {};
    if (!message) {
      return { success: false, error: "message is required" };
    }
    const key: EngineSessionKey = `${wid}:${sid}`;
    return app.instanceManager.followUp(key, message);
  });

  /** POST .../sessions/:sid/abort — Abort current generation */
  app.post<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/abort", async (request) => {
    const { wid, sid } = request.params;
    const key: EngineSessionKey = `${wid}:${sid}`;
    return app.instanceManager.abort(key);
  });

  /** POST .../sessions/:sid/reload — Hot-reload skills (re-read SKILL.md) */
  app.post<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/reload", async (request) => {
    const { wid, sid } = request.params;
    const key: EngineSessionKey = `${wid}:${sid}`;
    return app.instanceManager.reload(key);
  });

  /**
   * POST .../sessions/:sid/tools/:toolCallId/response — Submit clarify answers.
   * Resolves a blocked clarify tool's `execute()` with the user's structured
   * answers (keyed by question id). The agent turn then continues with the
   * answers as the tool result.
   */
  app.post<{
    Params: { wid: string; sid: string; toolCallId: string };
    Body: { answers?: Record<string, unknown> };
  }>(
    "/api/skill-engine/workspaces/:wid/sessions/:sid/tools/:toolCallId/response",
    async (request) => {
      const { wid, sid, toolCallId } = request.params;
      const answers = request.body?.answers ?? {};
      const key: EngineSessionKey = `${wid}:${sid}`;
      const resp = await app.instanceManager.submitToolResponse(
        key,
        toolCallId,
        answers,
      );
      return { ok: resp.success, error: resp.error };
    },
  );

  // ── State ───────────────────────────────────────────────────────

  /** GET .../sessions/:sid/state — Get instance internal state */
  app.get<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/state", async (request) => {
    const { wid, sid } = request.params;
    const key: EngineSessionKey = `${wid}:${sid}`;
    const response = await app.instanceManager.getState(key);
    return response.data ?? { isStreaming: false, model: null, messageCount: 0 };
  });

  /** GET .../sessions/:sid/messages — Read conversation history (oldest-first) */
  app.get<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/messages", async (request) => {
    const { wid, sid } = request.params;
    // Validate workspace exists (404 via store if missing)
    await app.workspaceStore.get(wid);
    const messages = app.messageCache
      ? await app.messageCache.readMessages(wid, sid, 100)
      : [];
    return { messages };
  });

  /** DELETE .../sessions/:sid/messages — 清除会话全部消息（PG + Redis）。
   *  sid = role（dev/debug 独立）。引擎内存中的消息由前端清除后重启会话清掉。 */
  app.delete<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/messages", async (request) => {
    const { wid, sid } = request.params;
    await app.workspaceStore.get(wid);
    const deleted = app.messageCache
      ? await app.messageCache.clearMessages(wid, sid)
      : 0;
    return { ok: true, deleted };
  });

  // ── SSE Events ─────────────────────────────────────────────────

  /** GET .../sessions/:sid/events — SSE event stream */
  app.get<{
    Params: { wid: string; sid: string };
  }>("/api/skill-engine/workspaces/:wid/sessions/:sid/events", async (request, reply) => {
    const { wid, sid } = request.params;
    const key: EngineSessionKey = `${wid}:${sid}`;
    const instance = app.instanceManager.getInstance(key);
    if (!instance) {
      throw new InstanceNotFoundError(key);
    }

    // Use raw response for SSE (Fastify 5 has no native SSE support).
    // `reply.raw` is the underlying Node ServerResponse — hijack it so we can
    // write the SSE stream directly. (Do NOT use `request.raw.res` — that
    // property is not part of IncomingMessage's public API and is undefined
    // under real Fastify routing, causing the route to 500.)
    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no", // Prevent nginx/proxy buffering
    });

    const clientId = randomUUID();
    let closed = false;

    const sseSend = (msg: import("../types/engine.js").EngineWorkerMessage): void => {
      if (closed) return;
      try {
        res.write(`data: ${JSON.stringify(msg)}\n\n`);
      } catch {
        closed = true;
        instance.eventSubscribers.delete(clientId);
      }
    };

    instance.eventSubscribers.set(clientId, { send: sseSend });

    // Cleanup on client disconnect
    request.raw.on("close", () => {
      closed = true;
      instance.eventSubscribers.delete(clientId);
    });

    // Initial connection confirmation
    res.write(
      `data: ${JSON.stringify({ type: "connected", sessionId: key })}\n\n`,
    );

    // Do NOT resolve the promise — keep the SSE connection open
    return new Promise<void>(() => {});
  });
}
