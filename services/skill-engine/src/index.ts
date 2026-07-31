/**
 * Skill Engine Service — entry point.
 *
 * Reads config, builds the Fastify app, listens, and handles graceful shutdown.
 */

import { buildApp } from "./app.js";
import { readConfig } from "./config.js";

async function main(): Promise<void> {
  const config = readConfig();
  const app = await buildApp(config);

  try {
    await app.listen({ port: config.port, host: config.host });
    app.instanceManager.startGc();
    app.log.info(
      `Skill Engine listening on http://${config.host}:${config.port}`,
    );
    app.log.info(
      `MAX_CONCURRENT_LLM=${config.maxConcurrentLlm}, IDLE_TIMEOUT=${config.idleTimeoutMs}ms`,
    );
  } catch (err) {
    app.log.error(err, "Failed to start Skill Engine");
    process.exit(1);
  }

  // Graceful shutdown
  const shutdown = async (): Promise<void> => {
    app.log.info("Shutting down Skill Engine...");
    app.instanceManager.stopGc();
    await app.instanceManager.stopAll();
    try { await app.redis?.quit(); } catch { /* best-effort */ }
    await app.sql.end();
    await app.close();
    process.exit(0);
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
