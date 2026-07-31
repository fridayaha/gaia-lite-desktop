"""better_auth schema init

Revision ID: d4b5e1f6a7c8
Revises: c5a3e0d329ac
Create Date: 2026-07-21 15:00:00.000000+00:00

Better Auth 认证表的 schema 初始化。

设计决策：
- Better Auth 官方用 `npx @better-auth/cli migrate` 建表，但 cli 有 node-gyp
  原生编译依赖（需 Python + 编译工具链），不宜进运行时镜像。
- 此 migration 把 cli 生成的 DDL 固化为 alembic revision，复用 gaia-api 镜像
  的 alembic 执行能力，无需独立 Job / 独立镜像。
- DDL 由 `npx @better-auth/cli@1.2.0 migrate` 生成（better-auth 1.2.0 + admin/
  organization/jwt 插件），9 张表 + 13 索引 + 7 外键，与 cli 输出完全一致。
- 幂等：用 IF NOT EXISTS + DO 块封装，重跑安全（alembic 本身也保证不重复执行）。
- Better Auth 升级后表结构若变化，新增 revision 同步即可（不要改本文件）。

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b5e1f6a7c8"
down_revision: Union[str, Sequence[str], None] = "c5a3e0d329ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Better Auth schema DDL（由 better-auth cli migrate 生成）
# 封装在 DO 块中实现幂等：表已存在时跳过整个建表逻辑。
_SCHEMA_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'better_auth' AND table_name = 'user'
  ) THEN
    CREATE SCHEMA better_auth;
    CREATE TABLE better_auth.account (
        id text NOT NULL,
        "accountId" text NOT NULL,
        "providerId" text NOT NULL,
        "userId" text NOT NULL,
        "accessToken" text,
        "refreshToken" text,
        "idToken" text,
        "accessTokenExpiresAt" timestamp with time zone,
        "refreshTokenExpiresAt" timestamp with time zone,
        scope text,
        password text,
        "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        "updatedAt" timestamp with time zone NOT NULL
    );
    CREATE TABLE better_auth.invitation (
        id text NOT NULL,
        "organizationId" text NOT NULL,
        email text NOT NULL,
        role text,
        status text NOT NULL,
        "expiresAt" timestamp with time zone NOT NULL,
        "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        "inviterId" text NOT NULL
    );
    CREATE TABLE better_auth.jwks (
        id text NOT NULL,
        "publicKey" text NOT NULL,
        "privateKey" text NOT NULL,
        "createdAt" timestamp with time zone NOT NULL,
        "expiresAt" timestamp with time zone
    );
    CREATE TABLE better_auth.member (
        id text NOT NULL,
        "organizationId" text NOT NULL,
        "userId" text NOT NULL,
        role text NOT NULL,
        "createdAt" timestamp with time zone NOT NULL
    );
    CREATE TABLE better_auth.organization (
        id text NOT NULL,
        name text NOT NULL,
        slug text NOT NULL,
        logo text,
        "createdAt" timestamp with time zone NOT NULL,
        metadata text
    );
    CREATE TABLE better_auth.session (
        id text NOT NULL,
        "expiresAt" timestamp with time zone NOT NULL,
        token text NOT NULL,
        "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        "updatedAt" timestamp with time zone NOT NULL,
        "ipAddress" text,
        "userAgent" text,
        "userId" text NOT NULL,
        "impersonatedBy" text,
        "activeOrganizationId" text
    );
    CREATE TABLE better_auth."ssoProvider" (
        id text NOT NULL,
        issuer text NOT NULL,
        "oidcConfig" text,
        "samlConfig" text,
        "userId" text NOT NULL,
        "providerId" text NOT NULL,
        "organizationId" text,
        domain text NOT NULL
    );
    CREATE TABLE better_auth."user" (
        id text NOT NULL,
        name text NOT NULL,
        email text NOT NULL,
        "emailVerified" boolean NOT NULL,
        image text,
        "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        "updatedAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        role text,
        banned boolean,
        "banReason" text,
        "banExpires" timestamp with time zone
    );
    CREATE TABLE better_auth.verification (
        id text NOT NULL,
        identifier text NOT NULL,
        value text NOT NULL,
        "expiresAt" timestamp with time zone NOT NULL,
        "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
        "updatedAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
    );
    ALTER TABLE ONLY better_auth.account
        ADD CONSTRAINT account_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.invitation
        ADD CONSTRAINT invitation_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.jwks
        ADD CONSTRAINT jwks_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.member
        ADD CONSTRAINT member_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.organization
        ADD CONSTRAINT organization_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.organization
        ADD CONSTRAINT organization_slug_key UNIQUE (slug);
    ALTER TABLE ONLY better_auth.session
        ADD CONSTRAINT session_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.session
        ADD CONSTRAINT session_token_key UNIQUE (token);
    ALTER TABLE ONLY better_auth."ssoProvider"
        ADD CONSTRAINT "ssoProvider_pkey" PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth."ssoProvider"
        ADD CONSTRAINT "ssoProvider_providerId_key" UNIQUE ("providerId");
    ALTER TABLE ONLY better_auth."user"
        ADD CONSTRAINT user_email_key UNIQUE (email);
    ALTER TABLE ONLY better_auth."user"
        ADD CONSTRAINT user_pkey PRIMARY KEY (id);
    ALTER TABLE ONLY better_auth.verification
        ADD CONSTRAINT verification_pkey PRIMARY KEY (id);
    CREATE INDEX "account_userId_idx" ON better_auth.account USING btree ("userId");
    CREATE INDEX invitation_email_idx ON better_auth.invitation USING btree (email);
    CREATE INDEX "invitation_organizationId_idx" ON better_auth.invitation USING btree ("organizationId");
    CREATE INDEX "member_organizationId_idx" ON better_auth.member USING btree ("organizationId");
    CREATE INDEX "member_userId_idx" ON better_auth.member USING btree ("userId");
    CREATE UNIQUE INDEX organization_slug_uidx ON better_auth.organization USING btree (slug);
    CREATE INDEX "session_userId_idx" ON better_auth.session USING btree ("userId");
    CREATE INDEX verification_identifier_idx ON better_auth.verification USING btree (identifier);
    ALTER TABLE ONLY better_auth.account
        ADD CONSTRAINT "account_userId_fkey" FOREIGN KEY ("userId") REFERENCES better_auth."user"(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth.invitation
        ADD CONSTRAINT "invitation_inviterId_fkey" FOREIGN KEY ("inviterId") REFERENCES better_auth."user"(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth.invitation
        ADD CONSTRAINT "invitation_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES better_auth.organization(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth.member
        ADD CONSTRAINT "member_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES better_auth.organization(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth.member
        ADD CONSTRAINT "member_userId_fkey" FOREIGN KEY ("userId") REFERENCES better_auth."user"(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth.session
        ADD CONSTRAINT "session_userId_fkey" FOREIGN KEY ("userId") REFERENCES better_auth."user"(id) ON DELETE CASCADE;
    ALTER TABLE ONLY better_auth."ssoProvider"
        ADD CONSTRAINT "ssoProvider_userId_fkey" FOREIGN KEY ("userId") REFERENCES better_auth."user"(id) ON DELETE CASCADE;
  END IF;
END $$;
"""


def upgrade() -> None:
    """Create better_auth schema (9 tables + indexes + FKs).

    Idempotent via DO block — safe to run on databases where better_auth
    tables already exist (e.g. created manually by @better-auth/cli).
    """
    op.execute(_SCHEMA_SQL)


def downgrade() -> None:
    """Drop better_auth schema entirely."""
    op.execute("DROP SCHEMA IF EXISTS better_auth CASCADE;")
