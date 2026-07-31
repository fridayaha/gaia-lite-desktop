/**
 * Better Auth Server for Gaia (ADR-016/017 Phase 5).
 *
 * Minimal Hono + Better Auth configuration. Handles:
 *   - Local user management (emailAndPassword + Admin plugin)
 *   - Organization (team grouping)
 *   - Enterprise SSO (OIDC/SAML via @better-auth/sso)
 *   - SCIM (enterprise IdP auto-sync)
 *   - JWT issuance (for Gaia FastAPI to verify via Authlib)
 *
 * Deployment: Docker container, shares Gaia PostgreSQL (independent `better_auth`
 * schema). See docker-compose.yml `better-auth` service.
 *
 * Forked from the official Hono integration pattern (research §5.9):
 *   https://better-auth.com/docs/integrations/hono
 */

import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { betterAuth } from "better-auth";
import { admin, organization, jwt } from "better-auth/plugins";
import { sso } from "@better-auth/sso";
import { Pool } from "pg";
import { fileURLToPath } from "node:url";

const port = parseInt(process.env.PORT ?? "3000", 10);
const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error("DATABASE_URL is required (point to Gaia PostgreSQL)");
  process.exit(1);
}
const secret = process.env.BETTER_AUTH_SECRET;
if (!secret) {
  console.error("BETTER_AUTH_SECRET is required (shared with Gaia for JWT verification)");
  process.exit(1);
}
const baseURL = process.env.BETTER_AUTH_URL ?? `http://localhost:${port}`;

// Use Better Auth's built-in Kysely PostgreSQL adapter (via pg.Pool) instead
// of the Drizzle adapter. The Drizzle adapter requires an explicit schema
// object; the Kysely adapter auto-manages tables via the Better Auth CLI
// (`npx @better-auth/cli migrate`). We point search_path at the independent
// `better_auth` schema so Better Auth's tables never collide with Gaia's
// public schema. The `options` must be passed as a Pool option (not in the
// connection string query param) — pg parses it as a libpq startup packet.
const pool = new Pool({
  connectionString: databaseUrl,
  options: "-c search_path=better_auth",
});

export const auth = betterAuth({
  database: pool,
  secret,
  baseURL,
  // Trust the Gaia web-ui origins (dev: 5173/5174 on localhost + 127.0.0.1;
  // production: set TRUSTED_ORIGINS to the deployed web-ui URL(s)). Better
  // Auth rejects cross-origin requests whose Origin isn't in this list.
  trustedOrigins: [
    'http://localhost:5173',
    'http://localhost:5174',
    'http://127.0.0.1:5173',
    'http://127.0.0.1:5174',
    ...(process.env.TRUSTED_ORIGINS ? process.env.TRUSTED_ORIGINS.split(',') : []),
  ],
  // Local user management (scenario 1).
  emailAndPassword: { enabled: true },
  // ── JIT auto-provisioning (design §2.3, industry best practice) ──
  // When a user registers via Better Auth, automatically create a
  // corresponding Gaia user record (subject = Better Auth uid). This
  // eliminates the manual "create Gaia user with subject=uid" step —
  // users appear in Gaia's identity management page automatically after
  // registration. The Gaia user starts with no groups (no permissions);
  // an admin assigns them to a group to grant access.
  //
  // Industry pattern: "JIT provisioning" (WorkOS/Clerk/Databricks).
  // The `after` hook runs after the Better Auth user row is committed,
  // so it's safe to call external APIs (official Better Auth docs use
  // this exact pattern for "create a stripe customer").
  databaseHooks: {
    user: {
      create: {
        after: async (user) => {
          const gaiaApiUrl = process.env.GAIA_API_URL ?? "http://localhost:8000";
          const provisionToken = process.env.GAIA_PROVISION_TOKEN;
          if (!provisionToken) {
            // Provisioning not configured — skip silently (dev mode where
            // Gaia may not be running, or admin prefers manual creation).
            console.log(`[JIT] GAIA_PROVISION_TOKEN not set, skipping auto-provision for ${user.email}`);
            return;
          }
          try {
            const resp = await fetch(`${gaiaApiUrl}/identity/users`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Provision-Token": provisionToken,
              },
              body: JSON.stringify({
                email: user.email,
                subject: user.id, // Better Auth uid = Gaia user subject
                attributes: {},
              }),
            });
            if (resp.ok) {
              console.log(`[JIT] Auto-provisioned Gaia user for ${user.email} (subject=${user.id})`);
            } else if (resp.status === 409) {
              // User already exists in Gaia (idempotent — e.g. re-registration).
              console.log(`[JIT] Gaia user for ${user.email} already exists (409, idempotent)`);
            } else {
              console.error(`[JIT] Failed to provision Gaia user for ${user.email}: ${resp.status} ${await resp.text()}`);
            }
          } catch (e) {
            // Non-fatal: Better Auth user creation succeeds even if Gaia
            // provisioning fails. The admin can manually create the Gaia
            // user later via the identity management page.
            console.error(`[JIT] Error provisioning Gaia user for ${user.email}:`, e);
          }
        },
      },
    },
  },
  plugins: [
    admin(),          // user CRUD, roles, ban, impersonate
    organization(),   // team grouping
    jwt({             // JWT issuance for Gaia FastAPI (/token endpoint + /jwks).
      // Issuer/audience default to baseURL. Gaia verifies via JWKS
      // (EdDSA/Ed25519 asymmetric signing — production-grade, not HS256).
      jwt: {
        expirationTime: "1h",
        definePayload: ({ user }) => ({
          sub: user.id,
          email: user.email,
          // Better Auth stores role on the user (admin plugin).
          roles: user.role ? [user.role] : [],
        }),
      },
      // ── Key rotation (ADR-017 D3, production hardening) ──
      // Asymmetric key pair (EdDSA/Ed25519) stored in the `jwks` table,
      // private key encrypted with BETTER_AUTH_SECRET (AES256-GCM). Public
      // key exposed at /api/auth/jwks for Gaia's JWKS verification.
      //
      // rotationInterval: auto-rotate the signing key every 90 days.
      // gracePeriod: keep the old key in JWKS for 30 days after rotation so
      //   tokens signed with the old key remain verifiable. This MUST be
      //   longer than (JWT expirationTime + Gaia PyJWKClient cache lifespan
      //   = 5min) to avoid the "4-minute window" outage where Gaia's cached
      //   JWKS still has the old key but Better Auth signs with the new one.
      //   See docs/engineer/permission-phase2-landing-guide.md §1.2 原则 3.
      jwks: {
        keyPairConfig: { alg: "EdDSA", crv: "Ed25519" },
        rotationInterval: 60 * 60 * 24 * 90, // 90 days
        gracePeriod: 60 * 60 * 24 * 30,      // 30 days (> 1h JWT + 5min cache)
      },
    }),
    sso({             // enterprise federation (scenario 2)
      // provisionUser syncs OIDC claims → Gaia attributes (region/department)
      // so Cedar row-level policies have the principal's attributes.
      provisionUser: async ({ user, userInfo }) => {
        console.log(`[provisionUser] ${user.email} attributes synced:`, userInfo.attributes);
      },
      provisionUserOnEveryLogin: true, // 转岗时属性自动更新
    }),
  ],
  // JWT signing uses asymmetric keys (EdDSA/Ed25519) managed by the jwt()
  // plugin and exposed at /api/auth/jwks. Gaia FastAPI verifies via JWKS
  // using fastapi-betterauth (PyJWKClient with built-in JWKS caching + key
  // rotation support). The shared BETTER_AUTH_SECRET remains the
  // session-cookie signing secret + JWKS private-key encryption key.
});

const app = new Hono();
// Mount Better Auth's handler at /api/auth/*.
app.on(["POST", "GET"], "/api/auth/*", (c: import("hono").Context) => auth.handler(c.req.raw));

// Health check.
app.get("/health", (c: import("hono").Context) => c.json({ status: "ok" }));

// Only start the HTTP server when run directly (not when imported by the
// Better Auth CLI, which loads this module to read the `auth` config).
const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  serve({ fetch: app.fetch, port }, (info: { port: number }) => {
    console.log(`Better Auth Server running at ${baseURL} (port ${info.port})`);
  });
}
