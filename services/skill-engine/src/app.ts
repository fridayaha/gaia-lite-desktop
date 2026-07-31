/**
 * Fastify app factory.
 *
 * Creates and configures a Fastify instance with all plugins,
 * decorators, error handler, and route modules registered.
 *
 * For testing, pass `testDecorators` to inject mock stores.
 */

import Fastify, { type FastifyInstance } from "fastify";
import cors from "@fastify/cors";
import Redis from "ioredis";

import type { SkillEngineConfig } from "./config.js";
import { readConfig } from "./config.js";
import { skillEngineErrorHandler } from "./utils/errors.js";
import { WorkspaceStore } from "./workspace/workspace-store.js";
import { FileStore } from "./workspace/file-store.js";
import { EngineInstanceManager } from "./engine/instance-manager.js";
import { HubClient } from "./hub/hub-client.js";
import { LlmCredentialClient } from "./llm/credential-client.js";
import { workspaceRoutes } from "./routes/workspace-routes.js";
import { engineRoutes } from "./routes/engine-routes.js";
import { adminRoutes } from "./routes/admin-routes.js";
import { publishRoutes } from "./routes/publish-routes.js";
import { configRoutes } from "./routes/config-routes.js";
import { createDbPool, type SkillEngineDb } from "./db/client.js";
import { runMigrations } from "./db/migrate.js";
import { createRedisClient } from "./redis/client.js";
import { MessageCache } from "./redis/message-cache.js";
import type { Sql } from "postgres";

declare module "fastify" {
  interface FastifyInstance {
    db: SkillEngineDb;
    sql: Sql<{}>;
    redis: Redis | null;
    messageCache: MessageCache;
    workspaceStore: WorkspaceStore;
    fileStore: FileStore;
    instanceManager: EngineInstanceManager;
    hubClient: HubClient;
    engineConfig: SkillEngineConfig;
  }
}

/** Optional pre-built decorators for testing (bypasses DB/Redis requirement). */
export interface TestDecorators {
  db: SkillEngineDb;
  sql: Sql<{}>;
  redis: Redis | null;
  messageCache: MessageCache;
  workspaceStore: WorkspaceStore;
  fileStore: FileStore;
  instanceManager: EngineInstanceManager;
  hubClient: HubClient;
}

/**
 * Build a Fastify application instance.
 *
 * @param configOverrides - Partial config overrides (for testing).
 * @param testDecorators - Pre-built decorators (for testing without real DB/Redis).
 */
export async function buildApp(
  configOverrides?: Partial<SkillEngineConfig>,
  testDecorators?: Partial<TestDecorators>,
): Promise<FastifyInstance> {
  const config = readConfig(configOverrides);
  const app = Fastify({
    logger: {
      level: config.logLevel,
      transport:
        process.env.NODE_ENV !== "production"
          ? { target: "pino/file" }
          : undefined,
    },
  });

  // ── Plugins ────────────────────────────────────────────────────

  await app.register(cors, { origin: true });

  // ── Database ────────────────────────────────────────────────────

  let db: SkillEngineDb | undefined = testDecorators?.db;
  let sql: Sql<{}> | undefined = testDecorators?.sql;

  if (!db && !sql && config.databaseUrl) {
    const pool = createDbPool(config.databaseUrl);
    db = pool.db;
    sql = pool.sql;

    // Run pending migrations
    await runMigrations(db);
    app.log.info("Database connected and migrations applied");
  } else if (!db) {
    throw new Error(
      "DATABASE_URL is required (or pass testDecorators.db for testing)",
    );
  }

  if (!sql) {
    sql = { end: async () => {} } as unknown as Sql<{}>;
  }

  // ── Redis ──────────────────────────────────────────────────────

  let redis: Redis | null = testDecorators?.redis ?? null;
  if (!testDecorators?.redis && config.redisUrl) {
    redis = createRedisClient(config.redisUrl);
    await redis.connect();
    app.log.info("Redis connected");
  } else if (!testDecorators?.redis) {
    app.log.warn("No REDIS_URL configured — message caching uses PostgreSQL only");
  }
  // MessageCache is always present: with Redis it's a hot cache + PG cold
  // storage; without Redis it degrades to PG-direct writes/reads so history
  // still persists across reloads.
  const messageCache: MessageCache =
    testDecorators?.messageCache ?? new MessageCache(redis, db);

  // ── Decorators ─────────────────────────────────────────────────

  const workspaceStore =
    testDecorators?.workspaceStore ??
    new WorkspaceStore(config.workspaceBaseDir, db, config.preinstalledSkillsDir);
  const fileStore =
    testDecorators?.fileStore ?? new FileStore(config.workspaceBaseDir);
  const instanceManager =
    testDecorators?.instanceManager ??
    new EngineInstanceManager(
      config,
      messageCache,
      config.internalToken
        ? new LlmCredentialClient(config.managerBaseUrl, config.internalToken)
        : undefined,
    );
  const hubClient =
    testDecorators?.hubClient ?? new HubClient(config.hubBaseUrl);

  app.decorate("db", db);
  app.decorate("sql", sql);
  app.decorate("redis", redis);
  app.decorate("messageCache", messageCache);
  app.decorate("workspaceStore", workspaceStore);
  app.decorate("fileStore", fileStore);
  app.decorate("instanceManager", instanceManager);
  app.decorate("hubClient", hubClient);
  app.decorate("engineConfig", config);

  // ── Error handler ──────────────────────────────────────────────

  app.setErrorHandler(skillEngineErrorHandler);

  // ── Routes ─────────────────────────────────────────────────────

  await app.register(workspaceRoutes);
  await app.register(engineRoutes);
  await app.register(adminRoutes);
  await app.register(publishRoutes);
  await app.register(configRoutes);

  return app;
}
