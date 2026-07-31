/**
 * Skill Engine service configuration.
 *
 * All settings read from environment variables with sensible defaults.
 * Override via env vars or pass partial overrides to `readConfig()`.
 */

export interface SkillEngineConfig {
  /** HTTP listen port. Default: 8004 */
  port: number;
  /** HTTP listen host. Default: "0.0.0.0" */
  host: string;
  /** Base directory for workspace data (PVC mount in prod). Default: "/data/skill-engine-workspaces" */
  workspaceBaseDir: string;
  /** PostgreSQL connection URL (independent skill_engine database). Required in production. */
  databaseUrl: string;
  /** Redis connection URL. Optional; message caching disabled if empty. */
  redisUrl: string;
  /** Max concurrent LLM requests across all instances. Default: 5 */
  maxConcurrentLlm: number;
  /** Idle timeout before GC stops an instance (ms). Default: 1_800_000 (30 min) */
  idleTimeoutMs: number;
  /** Max time to wait for a worker to report ready (ms). Default: 30_000 */
  workerStartupTimeoutMs: number;
  /** Max time to wait for a command response (ms). Default: 120_000 */
  commandTimeoutMs: number;
  /** Grace period for SIGTERM → SIGKILL on worker stop (ms). Default: 5_000 */
  gracefulShutdownMs: number;
  /** Interval between idle GC sweeps (ms). Default: 60_000 */
  gcIntervalMs: number;
  /** Worker heartbeat interval (ms). Default: 30_000 */
  heartbeatIntervalMs: number;
  /** Pino log level. Default: "info" */
  logLevel: string;
  /** Hub service base URL (for publish/scan). Default: "http://hub:8003" */
  hubBaseUrl: string;
  /** Manager service base URL (for fetching LLM credentials at runtime). Default: "http://manager:8002" */
  managerBaseUrl: string;
  /** Internal token to authenticate with manager internal endpoints. Optional; if empty, LLM credentials are read from pod env (LITELLM_API_KEY). */
  internalToken: string;
  /** 凭据加密 key material（config_params secret:true 加密存储用）。dev 留空走固定 fallback；prod 由 UA_CREDENTIAL_ENCRYPTION_KEY 注入（与 manager 同值）。 */
  credentialEncryptionKey: string;
  /** Directory holding preinstalled skills (bundled in image). New workspaces seed .pi/skills/ from here. Default: "/app/preinstalled-skills" */
  preinstalledSkillsDir: string;
  /** Max concurrent engine worker subprocesses. Default: 50 */
  maxInstances: number;
  /** Max respawn attempts after an unexpected worker crash. Default: 3 */
  maxRestarts: number;
  /** Window over which restarts are counted; stable run beyond it resets the budget. Default: 60_000 (ms) */
  restartWindowMs: number;
  /** Delay before respawning a crashed worker. Default: 1_000 (ms) */
  restartDelayMs: number;
}

/**
 * Read configuration from environment variables with defaults.
 * Pass partial overrides for testing.
 */
export function readConfig(
  overrides?: Partial<SkillEngineConfig>,
): SkillEngineConfig {
  return {
    port: parseInt(process.env.PORT ?? "8004", 10),
    host: process.env.HOST ?? "0.0.0.0",
    workspaceBaseDir:
      process.env.WORKSPACE_BASE_DIR ?? "/data/skill-engine-workspaces",
    databaseUrl: process.env.DATABASE_URL ?? "",
    redisUrl: process.env.REDIS_URL ?? "",
    maxConcurrentLlm: parseInt(
      process.env.MAX_CONCURRENT_LLM ?? "5",
      10,
    ),
    idleTimeoutMs: parseInt(
      process.env.IDLE_TIMEOUT_MS ?? "1800000",
      10,
    ),
    workerStartupTimeoutMs: parseInt(
      process.env.WORKER_STARTUP_TIMEOUT_MS ?? "30000",
      10,
    ),
    commandTimeoutMs: parseInt(
      process.env.COMMAND_TIMEOUT_MS ?? "120000",
      10,
    ),
    gracefulShutdownMs: parseInt(
      process.env.GRACEFUL_SHUTDOWN_MS ?? "5000",
      10,
    ),
    gcIntervalMs: parseInt(process.env.GC_INTERVAL_MS ?? "60000", 10),
    heartbeatIntervalMs: parseInt(
      process.env.HEARTBEAT_INTERVAL_MS ?? "30000",
      10,
    ),
    logLevel: process.env.LOG_LEVEL ?? "info",
    hubBaseUrl: process.env.HUB_BASE_URL ?? "http://hub:8003",
    managerBaseUrl: process.env.MANAGER_BASE_URL ?? "http://manager:8002",
    internalToken: process.env.UA_INTERNAL_TOKEN ?? "",
    credentialEncryptionKey: process.env.UA_CREDENTIAL_ENCRYPTION_KEY ?? "",
    preinstalledSkillsDir:
      process.env.PREINSTALLED_SKILLS_DIR ?? "/app/preinstalled-skills",
    maxInstances: parseInt(process.env.MAX_INSTANCES ?? "50", 10),
    maxRestarts: parseInt(process.env.MAX_RESTARTS ?? "3", 10),
    restartWindowMs: parseInt(process.env.RESTART_WINDOW_MS ?? "60000", 10),
    restartDelayMs: parseInt(process.env.RESTART_DELAY_MS ?? "1000", 10),
    ...overrides,
  };
}
