/**
 * Message cache — Redis hot cache + PostgreSQL cold storage.
 *
 * Write flow: engine event → Redis LPUSH → async batch PostgreSQL
 * Read flow: new instance → try Redis → miss → load from PostgreSQL → backfill Redis
 *
 * When REDIS_URL is not configured, `redis` is null and the cache degrades to
 * PostgreSQL-only mode: writes go straight to PG (no hot cache), reads query
 * PG directly. This keeps history working without a Redis dependency.
 *
 * Redis key design (with `skill-engine:` prefix from client):
 *   msg:{workspaceId}:{role}       LIST   — recent messages JSON (TTL 24h)
 *   msg:seq:{workspaceId}:{role}   STRING — next sequence number (TTL 24h)
 *   msg:dirty:{workspaceId}:{role} STRING — unflushed message count (TTL 24h)
 */

import Redis from "ioredis";
import { eq, and, desc, max } from "drizzle-orm";

import type { SkillEngineDb } from "../db/client.js";
import { messages } from "../db/schema.js";
import type { MessageInsert } from "../db/schema.js";
import type { PersistedPart } from "../types/engine.js";

const TTL_SECONDS = 86400; // 24 hours
const MAX_REDIS_MESSAGES = 200;

export interface CachedMessage {
  id: string;
  workspaceId: string;
  role: string;
  seq: number;
  sender: string;
  content: string;
  /** Ordered text/tool parts; null for user/system messages. */
  toolCalls: PersistedPart[] | null;
  createdAt: string;
}

export class MessageCache {
  constructor(
    private readonly redis: Redis | null,
    private readonly db: SkillEngineDb,
  ) {}

  // ── Write ──────────────────────────────────────────────────────

  /**
   * Append a message to the cache.
   * With Redis: writes to Redis immediately (seq via atomic INCR), accumulates
   *   for batch PG flush.
   * Without Redis: writes straight to PostgreSQL (seq via MAX(seq)+1).
   * `seq` is assigned internally so concurrent appends within a fast turn
   * can't collide on the same sequence number.
   */
  async appendMessage(input: Omit<CachedMessage, "seq">): Promise<void> {
    if (this.redis) {
      try {
        await this._appendToRedis(input);
        return;
      } catch (err) {
        // Redis dropped mid-write — fall through to PG so the message isn't lost.
        console.error("[MessageCache] Redis write failed, falling back to PG:", (err as Error).message);
      }
    }
    await this._appendToPg(input);
  }

  private async _appendToRedis(input: Omit<CachedMessage, "seq">): Promise<void> {
    const key = `msg:${input.workspaceId}:${input.role}`;
    const seqKey = `msg:seq:${input.workspaceId}:${input.role}`;
    const dirtyKey = `msg:dirty:${input.workspaceId}:${input.role}`;

    // Atomic seq — INCR starts at 1 for a new key.
    const seq = await this.redis!.incr(seqKey);
    const msg: CachedMessage = { ...input, seq };

    const pipeline = this.redis!.pipeline();
    pipeline.lpush(key, JSON.stringify(msg));
    pipeline.ltrim(key, 0, MAX_REDIS_MESSAGES - 1);
    pipeline.expire(key, TTL_SECONDS);
    pipeline.expire(seqKey, TTL_SECONDS);
    pipeline.incr(dirtyKey);
    pipeline.expire(dirtyKey, TTL_SECONDS);
    await pipeline.exec();
  }

  private async _appendToPg(input: Omit<CachedMessage, "seq">): Promise<void> {
    const seq = await this._nextSeqPg(input.workspaceId, input.role);
    const row: MessageInsert = {
      id: input.id,
      workspaceId: input.workspaceId,
      role: input.role as "dev" | "debug",
      seq,
      sender: input.sender as "user" | "assistant" | "system",
      content: input.content,
      toolCalls: input.toolCalls,
    };
    await this.db.insert(messages).values(row).onConflictDoNothing();
  }

  /** Next seq for a workspace:role from PG (MAX(seq)+1, starts at 1). */
  private async _nextSeqPg(workspaceId: string, role: string): Promise<number> {
    const rows = await this.db
      .select({ m: max(messages.seq) })
      .from(messages)
      .where(
        and(
          eq(messages.workspaceId, workspaceId),
          eq(messages.role, role as "dev" | "debug"),
        ),
      );
    const m = rows[0]?.m;
    const maxSeq = typeof m === "number" ? m : Number(m) || 0;
    return maxSeq + 1;
  }

  // ── Read ───────────────────────────────────────────────────────

  /**
   * Get recent messages for a workspace:role (newest-first).
   * With Redis: tries Redis first; on miss, loads from PostgreSQL and
   *   backfills Redis.
   * Without Redis: queries PostgreSQL directly (ordered by seq desc).
   */
  async getRecentMessages(
    workspaceId: string,
    role: string,
    limit: number = 50,
  ): Promise<CachedMessage[]> {
    if (!this.redis) {
      return this._pgRecent(workspaceId, role, limit);
    }

    const key = `msg:${workspaceId}:${role}`;

    // Try Redis first. If Redis is unavailable (connection refused), degrade
    // gracefully to PostgreSQL — the message cache is a hot-cache optimization,
    // not a hard dependency.
    let rawMessages: string[] = [];
    try {
      rawMessages = await this.redis.lrange(key, 0, limit - 1);
    } catch {
      rawMessages = [];
    }
    if (rawMessages.length > 0) {
      return rawMessages
        .map((raw) => {
          try {
            return JSON.parse(raw) as CachedMessage;
          } catch {
            return null;
          }
        })
        .filter(Boolean) as CachedMessage[];
    }

    // Redis miss — load from PostgreSQL
    const rows = await this._pgRows(workspaceId, role, limit);

    if (rows.length === 0) return [];

    // Backfill Redis from PG (best-effort; skip if Redis unavailable)
    try {
      const pipeline = this.redis.pipeline();
      for (const row of rows.slice().reverse()) {
        // reverse: oldest first for RPUSH
        const cached: CachedMessage = {
          id: row.id,
          workspaceId: row.workspaceId,
          role: row.role,
          seq: row.seq,
          sender: row.sender,
          content: row.content,
          toolCalls: (row.toolCalls as PersistedPart[] | null) ?? null,
          createdAt: row.createdAt.toISOString(),
        };
        pipeline.rpush(key, JSON.stringify(cached));
      }
      pipeline.expire(key, TTL_SECONDS);
      await pipeline.exec();
    } catch {
      // Redis unavailable — backfill skipped, PG rows still returned below
    }

    // Return in reverse order (newest first)
    return rows
      .reverse()
      .map((row) => ({
        id: row.id,
        workspaceId: row.workspaceId,
        role: row.role,
        seq: row.seq,
        sender: row.sender,
        content: row.content,
        toolCalls: (row.toolCalls as PersistedPart[] | null) ?? null,
        createdAt: row.createdAt.toISOString(),
      }));
  }

  /** PG-direct recent messages, newest-first. */
  private async _pgRecent(
    workspaceId: string,
    role: string,
    limit: number,
  ): Promise<CachedMessage[]> {
    const rows = await this._pgRows(workspaceId, role, limit);
    // _pgRows returns newest-first (ORDER BY seq DESC) — return as-is.
    return rows.map((row) => ({
      id: row.id,
      workspaceId: row.workspaceId,
      role: row.role,
      seq: row.seq,
      sender: row.sender,
      content: row.content,
      toolCalls: (row.toolCalls as PersistedPart[] | null) ?? null,
      createdAt: row.createdAt.toISOString(),
    }));
  }

  /** Raw PG rows for a workspace:role, newest-first. */
  private async _pgRows(workspaceId: string, role: string, limit: number) {
    return this.db
      .select()
      .from(messages)
      .where(
        and(
          eq(messages.workspaceId, workspaceId),
          eq(messages.role, role as "dev" | "debug"),
        ),
      )
      .orderBy(desc(messages.seq))
      .limit(limit);
  }

  /**
   * Read conversation history for a workspace:role in chronological order
   * (oldest first). This is the read path for the frontend chat history:
   * `getRecentMessages` returns newest-first (cache-oriented); this wrapper
   * reverses it so the UI can render messages top-to-bottom.
   */
  async readMessages(
    workspaceId: string,
    role: string,
    limit: number = 100,
  ): Promise<CachedMessage[]> {
    const recent = await this.getRecentMessages(workspaceId, role, limit);
    // getRecentMessages is newest-first → reverse to oldest-first.
    return recent.slice().reverse();
  }

  /**
   * Clear all messages for a workspace+role (dev/debug 独立)：删 PostgreSQL 行 +
   * Redis recent list + dirty 计数。返回删除的行数。用于「清除会话」。
   */
  async clearMessages(workspaceId: string, role: string): Promise<number> {
    let deleted = 0;
    try {
      const rows = await this.db
        .delete(messages)
        .where(
          and(
            eq(messages.workspaceId, workspaceId),
            eq(messages.role, role as "dev" | "debug"),
          ),
        )
        .returning({ id: messages.id });
      deleted = rows.length;
    } catch {
      /* PG 删除失败：仍尝试清 Redis */
    }
    if (this.redis) {
      try {
        await this.redis.del(
          `msg:${workspaceId}:${role}`,
          `msg:dirty:${workspaceId}:${role}`,
        );
      } catch {
        /* ignore Redis errors */
      }
    }
    return deleted;
  }

  // ── Flush ──────────────────────────────────────────────────────

  /**
   * Flush accumulated messages from Redis to PostgreSQL.
   * Called when an engine instance stops or periodically.
   * No-op when Redis is not configured (writes already went straight to PG).
   */
  async flushToPostgres(
    workspaceId: string,
    role: string,
  ): Promise<number> {
    if (!this.redis) return 0;

    const dirtyKey = `msg:dirty:${workspaceId}:${role}`;
    let dirtyCount: string | null = null;
    try {
      dirtyCount = await this.redis.get(dirtyKey);
    } catch {
      return 0;
    }

    if (!dirtyCount || parseInt(dirtyCount, 10) === 0) {
      return 0;
    }

    const key = `msg:${workspaceId}:${role}`;
    const count = parseInt(dirtyCount, 10);

    // Read dirty messages from Redis (newest first)
    let rawMessages: string[] = [];
    try {
      rawMessages = await this.redis.lrange(key, 0, count - 1);
    } catch {
      return 0;
    }
    const toInsert: MessageInsert[] = [];

    for (const raw of rawMessages) {
      try {
        const msg = JSON.parse(raw) as CachedMessage;
        toInsert.push({
          id: msg.id,
          workspaceId: msg.workspaceId,
          role: msg.role as "dev" | "debug",
          seq: msg.seq,
          sender: msg.sender as "user" | "assistant" | "system",
          content: msg.content,
          toolCalls: msg.toolCalls,
        });
      } catch {
        // Skip malformed messages
      }
    }

    if (toInsert.length > 0) {
      await this.db.insert(messages).values(toInsert).onConflictDoNothing();
    }

    // Reset dirty counter
    await this.redis.set(dirtyKey, "0", "EX", TTL_SECONDS);

    return toInsert.length;
  }
}
