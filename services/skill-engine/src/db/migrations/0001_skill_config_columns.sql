ALTER TABLE "workspaces" ADD COLUMN "skill_name" varchar(128);--> statement-breakpoint
ALTER TABLE "workspaces" ADD COLUMN "config" jsonb DEFAULT '{}'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE "workspaces" ADD COLUMN "credentials_encrypted" text;--> statement-breakpoint
CREATE INDEX "idx_workspaces_skill_name" ON "workspaces" USING btree ("skill_name");