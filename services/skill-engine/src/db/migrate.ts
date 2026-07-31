/**
 * Database migration runner.
 *
 * Applies pending Drizzle migrations on service startup.
 */

import { migrate } from "drizzle-orm/postgres-js/migrator";
import type { SkillEngineDb } from "./client.js";

/**
 * Run all pending database migrations.
 * Called once during app startup.
 */
export async function runMigrations(db: SkillEngineDb): Promise<void> {
  await migrate(db, { migrationsFolder: "src/db/migrations" });
}
