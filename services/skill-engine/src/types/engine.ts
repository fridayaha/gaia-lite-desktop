/**
 * Engine instance type definitions.
 *
 * Covers the JSONL protocol between main process and engine-worker,
 * as well as internal instance tracking.
 */

import type { ChildProcess } from "node:child_process";

// ── Session identity ──────────────────────────────────────────

/** Composite key: `"{workspaceId}:{role}"`. */
export type EngineSessionKey = `${string}:${string}`;

/** Engine instance role. */
export type EngineRole = "dev" | "debug";

// ── JSONL protocol: commands (main → worker via stdin) ─────────

/** Command types the main process can send to an engine worker. */
export type EngineCommandType =
  | "prompt"
  | "steer"
  | "follow_up"
  | "abort"
  | "get_state"
  | "reload"
  | "tool_response";

/** A command sent via stdin JSONL to an engine worker. */
export interface EngineCommand {
  type: EngineCommandType;
  id: string; // UUID, echoed back in response
  message?: string; // Required for prompt / steer / follow_up
  /** tool_response: which blocked clarify tool to resolve. */
  toolCallId?: string;
  /** tool_response: user-submitted answers keyed by question id. */
  answers?: Record<string, unknown>;
}

// ── JSONL protocol: messages (worker → main via stdout) ────────

/** Response to a command (matched by `id`). */
export interface EngineCommandResponse {
  type: "response";
  id: string;
  command: string;
  success: boolean;
  data?: Record<string, unknown>;
  error?: string;
}

/** Event forwarded from the SDK `subscribe()` callback. */
export interface EngineEventMessage {
  type: "event";
  eventType: string;
  data: Record<string, unknown>;
}

/** Status update from the engine worker. */
export interface EngineWorkerStatus {
  type: "status";
  status: "initializing" | "ready" | "error" | "shutting_down";
  role?: string;
  cwd?: string;
  error?: string;
}

/** Periodic heartbeat from the engine worker. */
export interface EngineHeartbeat {
  type: "heartbeat";
  ts: number;
}

/** Union of all messages an engine worker can emit on stdout. */
export type EngineWorkerMessage =
  | EngineCommandResponse
  | EngineEventMessage
  | EngineWorkerStatus
  | EngineHeartbeat;

// ── Internal instance tracking ────────────────────────────────

/** Pending request tracker for command-response correlation. */
export interface PendingRequest {
  resolve: (msg: EngineCommandResponse) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

/** SSE subscriber callback. */
export interface EventSubscriber {
  send: (msg: EngineWorkerMessage) => void;
}

/**
 * Persisted tool call (shape mirrors the frontend ToolCall). Stored inside a
 * PersistedPart so history round-trips tool cards.
 */
export interface PersistedToolCall {
  id: string; // toolCallId
  toolName: string;
  args: Record<string, unknown> | null;
  status: "running" | "done" | "error";
  result?: unknown;
  startedAt: string;
}

/**
 * Ordered part of an assistant turn — either a text segment or a tool call.
 * The `tool_calls` JSONB column stores `PersistedPart[]` so the frontend can
 * reconstruct text/tool interleaving from history.
 */
export type PersistedPart =
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: PersistedToolCall };

/**
 * Per-turn buffer accumulated from SDK events between turn_start and turn_end.
 * On turn_end the buffer is flushed as a single persisted assistant message.
 */
export interface TurnBuffer {
  parts: PersistedPart[];
  /** True after message_end — next message_update starts a new text part. */
  textClosed: boolean;
}

/** An active engine instance tracked by EngineInstanceManager. */
export interface EngineInstance {
  workspaceId: string;
  role: string;
  process: ChildProcess;
  lastActivity: Date;
  eventSubscribers: Map<string, EventSubscriber>;
  pendingRequests: Map<string, PendingRequest>;
  ready: boolean;
  stopping: boolean;
  /** Accumulated events for the current turn; flushed on turn_end. */
  currentTurn: TurnBuffer | null;
  /** Original spawn options — used to respawn after a crash. */
  spawnOptions: EngineSpawnOptions;
  /** Crash-restart attempts within the current restart window. */
  restartCount: number;
  /** ms timestamp of the last respawn (0 = none). */
  lastRestartAt: number;
}

// ── API request/response types ─────────────────────────────────

/** POST /sessions request body. */
export interface EngineSpawnRequest {
  role?: EngineRole; // Default: "dev"
}

/** POST /sessions/:sid/prompt request body. */
export interface EnginePromptRequest {
  message: string;
}

/** POST /sessions/:sid/steer request body. */
export interface EngineSteerRequest {
  message: string;
}

/** POST /sessions/:sid/follow-up request body. */
export interface EngineFollowUpRequest {
  message: string;
}

/** GET /sessions/:sid/state response. */
export interface EngineInstanceState {
  isStreaming: boolean;
  model: { provider: string; modelId: string } | null;
  messageCount: number;
}

/** Spawn options passed to EngineInstanceManager. */
export interface EngineSpawnOptions {
  cwd: string;
  tools?: string[];
  excludeTools?: string[];
  env?: Record<string, string>;
}
