/**
 * Drizzle ORM schema for the skill_engine database.
 *
 * Independent database — not shared with the manager's unionagents DB.
 * Tables: workspaces, messages
 */

import {
  pgTable,
  uuid,
  varchar,
  text,
  integer,
  timestamp,
  jsonb,
  pgEnum,
  index,
} from "drizzle-orm/pg-core";

// ── Enums ─────────────────────────────────────────────────────

export const workspaceStatusEnum = pgEnum("workspace_status", [
  "active",
  "deleted",
]);

export const messageRoleEnum = pgEnum("message_role", ["dev", "debug"]);

export const messageSenderEnum = pgEnum("message_sender", [
  "user",
  "assistant",
  "system",
]);

// ── Workspaces ────────────────────────────────────────────────

export const workspaces = pgTable(
  "workspaces",
  {
    id: uuid("id").primaryKey(),
    userId: varchar("user_id", { length: 36 }).notNull(),
    groupId: varchar("group_id", { length: 36 }).notNull(),
    name: varchar("name", { length: 128 }).notNull(),
    description: text("description").default("").notNull(),
    status: workspaceStatusEnum("status").default("active").notNull(),
    localPath: varchar("local_path", { length: 512 }).notNull(),
    hubItemId: varchar("hub_item_id", { length: 36 }),
    // ── config_params 落地（skill-studio 开发期密钥/配置）──
    // skill_name：缓存 manifest.name，供运行时 /secret?skill=&key= 按 skill_name 查找
    // （manifest 保存时刷新，见 config-routes PUT）。
    skillName: varchar("skill_name", { length: 128 }),
    // config：非密钥 config_params 明文值（jsonb），syncUserSkill 镜像 SKILL.md 时替换 ${config.param}。
    config: jsonb("config").default({}).notNull(),
    // credentials_encrypted：secret:true 的 dict 经 AES-256-GCM 加密（见 utils/crypto.ts）。
    credentialsEncrypted: text("credentials_encrypted"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    index("idx_workspaces_user_id").on(table.userId),
    index("idx_workspaces_skill_name").on(table.skillName),
  ],
);

// ── Messages ──────────────────────────────────────────────────

export const messages = pgTable(
  "messages",
  {
    id: uuid("id").primaryKey(),
    workspaceId: uuid("workspace_id")
      .notNull()
      .references(() => workspaces.id, { onDelete: "cascade" }),
    role: messageRoleEnum("role").notNull(),
    seq: integer("seq").notNull(),
    sender: messageSenderEnum("sender").notNull(),
    content: text("content").notNull(),
    toolCalls: jsonb("tool_calls"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => [
    index("idx_messages_workspace_role").on(table.workspaceId, table.role),
  ],
);

// ── Type exports for Drizzle inference ────────────────────────

export type WorkspaceRow = typeof workspaces.$inferSelect;
export type WorkspaceInsert = typeof workspaces.$inferInsert;
export type MessageRow = typeof messages.$inferSelect;
export type MessageInsert = typeof messages.$inferInsert;
