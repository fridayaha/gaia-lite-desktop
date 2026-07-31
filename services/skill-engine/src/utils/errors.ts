/**
 * Custom error hierarchy and Fastify error handler.
 *
 * All Skill Engine errors extend `SkillEngineError`.
 * Route handlers throw these; the Fastify error handler maps them to HTTP status codes.
 */

import type { FastifyError, FastifyRequest, FastifyReply } from "fastify";

// ── Error hierarchy ────────────────────────────────────────────

export class SkillEngineError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number = 500,
  ) {
    super(message);
    this.name = this.constructor.name;
  }
}

export class NotFoundError extends SkillEngineError {
  constructor(message: string) {
    super(message, 404);
  }
}

export class ConflictError extends SkillEngineError {
  constructor(message: string) {
    super(message, 409);
  }
}

export class ValidationError extends SkillEngineError {
  constructor(message: string) {
    super(message, 400);
  }
}

export class CommandTimeoutError extends SkillEngineError {
  constructor(message: string) {
    super(message, 504);
  }
}

// ── Convenience aliases ────────────────────────────────────────

export class WorkspaceNotFoundError extends NotFoundError {
  constructor(id: string) {
    super(`Workspace not found: ${id}`);
  }
}

export class InstanceNotFoundError extends NotFoundError {
  constructor(key: string) {
    super(`Engine instance not found: ${key}`);
  }
}

// ── Fastify error handler ─────────────────────────────────────

/**
 * Register as Fastify `setErrorHandler`.
 * Maps SkillEngineError subclasses to their HTTP status codes;
 * unknown errors get 500 with a generic message.
 */
export function skillEngineErrorHandler(
  error: FastifyError,
  _request: FastifyRequest,
  reply: FastifyReply,
): void {
  if (error instanceof SkillEngineError) {
    reply.status(error.statusCode).send({
      error: error.message,
      code: error.name,
    });
    return;
  }

  // Fastify validation errors
  if (error.validation) {
    reply.status(400).send({
      error: error.message,
      code: "VALIDATION_ERROR",
    });
    return;
  }

  // Unknown errors — don't leak internals to the client, but log the stack
  // server-side so failures are diagnosable.
  console.error("[skill-engine] unhandled error:", error);
  reply.status(500).send({
    error: "Internal server error",
    code: "INTERNAL_ERROR",
  });
}
