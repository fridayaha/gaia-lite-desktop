/**
 * Workspace store — PostgreSQL metadata + PVC filesystem directories.
 *
 * Metadata (name, description, user_id, status, etc.) is stored in the
 * `workspaces` table via Drizzle ORM. Workspace files and directories
 * are stored on the PVC filesystem.
 */

import {
  mkdirSync,
  rmSync,
  existsSync,
  cpSync,
} from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { eq, and, desc } from "drizzle-orm";

import type { SkillEngineDb } from "../db/client.js";
import { workspaces } from "../db/schema.js";
import type {
  Workspace,
  WorkspaceCreateRequest,
} from "../types/workspace.js";
import { WorkspaceNotFoundError } from "../utils/errors.js";

export class WorkspaceStore {
  constructor(
    private readonly baseDir: string,
    private readonly db: SkillEngineDb,
    private readonly preinstalledSkillsDir: string = "/app/preinstalled-skills",
  ) {}

  /** Create a new workspace with default skill skeleton. */
  async create(req: WorkspaceCreateRequest): Promise<Workspace> {
    const id = randomUUID();
    const localPath = this.getDir(id);

    // Create directory structure on PVC
    mkdirSync(localPath, { recursive: true });
    mkdirSync(join(localPath, ".pi", "skills"), { recursive: true });

    // Seed the preinstalled skill-creator skill into .pi/skills/ so the dev
    // engine loads it as the default skill-authoring tool. Best-effort: if the
    // bundled dir is missing (misconfigured image), skip seeding rather than
    // fail workspace creation.
    const src = join(this.preinstalledSkillsDir, "skill-creator");
    const dest = join(localPath, ".pi", "skills", "skill-creator");
    try {
      if (existsSync(src)) {
        cpSync(src, dest, { recursive: true });
      } else {
        console.warn(
          `[WorkspaceStore] preinstalled skill-creator not found at ${src}; skipping seed`,
        );
      }
    } catch (err) {
      console.error(
        `[WorkspaceStore] failed to seed skill-creator:`,
        (err as Error).message,
      );
    }

    // Insert metadata into PostgreSQL
    await this.db.insert(workspaces).values({
      id,
      userId: req.userId,
      groupId: req.groupId,
      name: req.name,
      description: req.description ?? "",
      status: "active",
      localPath,
    });

    return this._fromRow({
      id,
      userId: req.userId,
      groupId: req.groupId,
      name: req.name,
      description: req.description ?? "",
      status: "active",
      localPath,
      hubItemId: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    });
  }

  /** List workspaces, optionally filtered by user. */
  async list(userId?: string): Promise<Workspace[]> {
    const conditions = [eq(workspaces.status, "active")];
    if (userId) {
      conditions.push(eq(workspaces.userId, userId));
    }

    const rows = await this.db
      .select()
      .from(workspaces)
      .where(and(...conditions));

    return rows.map((r) => this._fromRow(r));
  }

  /** Get a single workspace by ID. Throws NotFoundError if missing. */
  async get(id: string): Promise<Workspace> {
    const rows = await this.db
      .select()
      .from(workspaces)
      .where(eq(workspaces.id, id))
      .limit(1);

    if (rows.length === 0) {
      throw new WorkspaceNotFoundError(id);
    }
    return this._fromRow(rows[0]);
  }

  /** Soft-delete a workspace (marks as deleted + removes directory). */
  async delete(id: string): Promise<void> {
    const ws = await this.get(id);

    // Remove directory from PVC
    if (existsSync(ws.localPath)) {
      rmSync(ws.localPath, { recursive: true, force: true });
    }

    // Soft-delete in DB
    await this.db
      .update(workspaces)
      .set({ status: "deleted", updatedAt: new Date() })
      .where(eq(workspaces.id, id));
  }

  /** Write back the Hub item id after publishing (idempotent re-publish overwrites). */
  async setHubItemId(id: string, hubItemId: string): Promise<void> {
    await this.db
      .update(workspaces)
      .set({ hubItemId, updatedAt: new Date() })
      .where(eq(workspaces.id, id));
  }

  // ── config_params（密钥/配置）─────────────────────────────────

  /** 读取工作区的非密钥 config（jsonb）+ 密钥密文。供 admin GET config。 */
  async getConfig(
    id: string,
  ): Promise<{
    config: Record<string, unknown>;
    credentialsEncrypted: string | null;
  }> {
    const rows = await this.db
      .select({
        config: workspaces.config,
        credentialsEncrypted: workspaces.credentialsEncrypted,
      })
      .from(workspaces)
      .where(eq(workspaces.id, id))
      .limit(1);
    const row = rows[0];
    return {
      config: (row?.config as Record<string, unknown> | null) ?? {},
      credentialsEncrypted: row?.credentialsEncrypted ?? null,
    };
  }

  /** 写回 config / credentials_encrypted / skill_name 缓存。仅写提供的字段。 */
  async saveConfig(
    id: string,
    patch: {
      config?: Record<string, unknown>;
      credentialsEncrypted?: string | null;
      skillName?: string;
    },
  ): Promise<void> {
    await this.db
      .update(workspaces)
      .set({ ...patch, updatedAt: new Date() })
      .where(eq(workspaces.id, id));
  }

  /** 按 skill_name 查最近更新的 workspace（供运行时 /secret 解析）。无匹配返回 null。 */
  async findBySkillName(
    skill: string,
  ): Promise<{ id: string; credentialsEncrypted: string | null } | null> {
    const rows = await this.db
      .select({
        id: workspaces.id,
        credentialsEncrypted: workspaces.credentialsEncrypted,
      })
      .from(workspaces)
      .where(eq(workspaces.skillName, skill))
      .orderBy(desc(workspaces.updatedAt))
      .limit(1);
    return rows[0] ?? null;
  }

  /** Check if a workspace exists and is active. */
  async exists(id: string): Promise<boolean> {
    const rows = await this.db
      .select({ id: workspaces.id })
      .from(workspaces)
      .where(and(eq(workspaces.id, id), eq(workspaces.status, "active")))
      .limit(1);
    return rows.length > 0;
  }

  /** Get the absolute path to a workspace directory. */
  getDir(id: string): string {
    return join(this.baseDir, id);
  }

  // ── Private ─────────────────────────────────────────────────

  private _fromRow(row: {
    id: string;
    userId: string;
    groupId: string;
    name: string;
    description: string;
    status: string;
    localPath: string;
    hubItemId: string | null;
    createdAt: Date;
    updatedAt: Date;
  }): Workspace {
    return {
      id: row.id,
      userId: row.userId,
      groupId: row.groupId,
      name: row.name,
      description: row.description,
      localPath: row.localPath,
      status: row.status as "active" | "deleted",
      hubItemId: row.hubItemId,
      createdAt: row.createdAt.toISOString(),
      updatedAt: row.updatedAt.toISOString(),
    };
  }
}
