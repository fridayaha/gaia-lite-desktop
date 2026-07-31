CREATE TYPE "public"."message_role" AS ENUM('dev', 'debug');--> statement-breakpoint
CREATE TYPE "public"."message_sender" AS ENUM('user', 'assistant', 'system');--> statement-breakpoint
CREATE TYPE "public"."workspace_status" AS ENUM('active', 'deleted');--> statement-breakpoint
CREATE TABLE "messages" (
	"id" uuid PRIMARY KEY NOT NULL,
	"workspace_id" uuid NOT NULL,
	"role" "message_role" NOT NULL,
	"seq" integer NOT NULL,
	"sender" "message_sender" NOT NULL,
	"content" text NOT NULL,
	"tool_calls" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "workspaces" (
	"id" uuid PRIMARY KEY NOT NULL,
	"user_id" varchar(36) NOT NULL,
	"group_id" varchar(36) NOT NULL,
	"name" varchar(128) NOT NULL,
	"description" text DEFAULT '' NOT NULL,
	"status" "workspace_status" DEFAULT 'active' NOT NULL,
	"local_path" varchar(512) NOT NULL,
	"hub_item_id" varchar(36),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "messages" ADD CONSTRAINT "messages_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_messages_workspace_role" ON "messages" USING btree ("workspace_id","role");--> statement-breakpoint
CREATE INDEX "idx_workspaces_user_id" ON "workspaces" USING btree ("user_id");