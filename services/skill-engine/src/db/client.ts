/**
 * Database connection factory using postgres.js + Drizzle ORM.
 *
 * Connects to the independent `skill_engine` PostgreSQL database
 * (NOT the manager's `unionagents` database).
 */

import { drizzle, type PostgresJsDatabase } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema.js";

export type SkillEngineDb = PostgresJsDatabase<typeof schema>;

/**
 * Create a Drizzle database instance from a connection string.
 *
 * @param databaseUrl - PostgreSQL connection string, e.g. "postgresql://user:pass@host:5432/skill_engine"
 * @returns Drizzle database instance with full schema typing
 */
export function createDbPool(databaseUrl: string): {
  db: SkillEngineDb;
  sql: postgres.Sql<{}>;
} {
  const sql = postgres(databaseUrl);
  const db = drizzle(sql, { schema });
  return { db, sql };
}
