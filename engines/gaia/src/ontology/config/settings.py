"""Application settings via pydantic-settings.

All configuration is managed here, loaded from environment variables
with a `.env` file fallback. No manual hot-reloading allowed.
"""

import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Edition（发行版开关）──
    # full = 云上全量（PG/Gravitino/Trino/Doris/Iceberg/Neo4j/...）
    # lite  = 桌面单机（SQLite 元数据 + DuckDB 联邦，砍重依赖）
    # container.py 按 edition 决定装配哪些 Layer/Service（A3）。
    edition: Literal["full", "lite"] = "full"

    # ── SQLite（lite 桌面版元数据）──
    # lite 版用本地 SQLite 文件库存业务本体元数据（替代 PostgreSQL）。
    # ~ 展开；database.py 会确保父目录存在。不走 Alembic（迁移含 PG-only
    # 构造），改由 main.py lifespan 调 Base.metadata.create_all 建空库（B1）。
    lite_db_path: str = "~/.gaia-lite/gaia-lite.db"

    # ── DuckDB（lite 桌面版联邦查询引擎）──
    # 嵌入式 DuckDB 持久化文件库，ATTACH 外部数据源（PG/MySQL/CSV/SQLite）做联邦
    # 查询（替代 Trino，B2）。~ 展开；DuckDBEngine 会确保父目录存在。
    lite_warehouse_path: str = "~/.gaia-lite/warehouse.duckdb"

    # ── PostgreSQL ──
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "ontology"
    pg_password: str = "ontology"
    pg_database: str = "ontology"

    # ── Ontology lifecycle (v5.2 soft-delete + cooldown) ──
    # Soft-deleted ontologies (and their children) are retained for this many
    # days before the cleanup script physically deletes the PG rows. During
    # the window a POST /restore recovers them. See design §七.
    soft_delete_retention_days: int = 7

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def pg_sync_dsn(self) -> str:
        """同步 DSN，供 Alembic migration 使用（Alembic 默认同步连接）。"""
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    # ── Gravitino ──
    gravitino_host: str = "localhost"
    gravitino_port: int = 8090
    # Host override for JDBC URLs rendered into Gravitino/Trino catalogs.
    # The backend resolves a datasource's host from its own view (typically
    # "localhost" when the backend runs on the host), but the Gravitino catalog
    # is consumed *inside* the Gravitino/Trino container, where localhost points
    # to the container itself. When set, registering a JDBC catalog rewrites
    # the jdbc-url host to this value so Gravitino can dial the source DB
    # (e.g. "benchmark-mysql" container name on the shared docker network).
    # Empty = use the datasource's own host (only correct when the backend and
    # Gravitino share the host view, e.g. both on the host or both in the same
    # container). Distinct from seatunnel_source_host_override because the two
    # consumers may live in different containers/networks.
    catalog_jdbc_host_override: str = ""

    @property
    def gravitino_uri(self) -> str:
        return f"http://{self.gravitino_host}:{self.gravitino_port}"

    # ── Iceberg REST Catalog (served by Gravitino) ──
    iceberg_rest_uri: str = "http://localhost:9001/iceberg"
    iceberg_warehouse: str = "s3://ontology-warehouse/"
    # Iceberg namespace under which all synced tables live. Dataset
    # api_names are stored without this prefix, so IcebergStore prepends
    # it when talking to the REST catalog / Trino.
    iceberg_namespace: str = "ontology"

    # ── RustFS / S3 ──
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    s3_path_style_access: bool = True
    s3_region: str = "us-east-1"

    # ── SeaTunnel-facing endpoints (container-internal addresses) ──
    # 后端服务跑在宿主机，用 localhost 访问 Gravitino/rustfs；但渲染给
    # SeaTunnel 的 pipeline 配置由 SeaTunnel 容器执行，localhost 会指向
    # SeaTunnel 容器自身。故 SeaTunnel 模板用这对容器内地址，与后端自己的
    # iceberg_rest_uri / s3_endpoint 分离，避免 host 视角冲突。
    seatunnel_iceberg_rest_uri: str = "http://gravitino:9001/iceberg"
    seatunnel_s3_endpoint: str = "http://rustfs:9000"
    # Doris FE host as seen from SeaTunnel's container (for the INDEX
    # pipeline's Doris stream-load sink `fenodes`). The backend reaches Doris
    # via localhost; SeaTunnel must use the container name on the shared
    # docker network. Defaults to the compose service name.
    seatunnel_doris_host: str = "ontology-doris-fe"
    # Host override for JDBC sources rendered into SeaTunnel sync pipelines.
    # The backend resolves datasource host from its own view (localhost), but
    # SeaTunnel runs in a container where localhost points to itself. When set,
    # the sync engine rewrites the JDBC source URL's host to this value so
    # SeaTunnel can reach the source DB (e.g. "benchmark-mysql" container name
    # after joining the gaia_default network). Empty = use the datasource's
    # own host (only works when backend and SeaTunnel share the host view).
    seatunnel_source_host_override: str = ""
    # SeaTunnel 访问 Gaia PG（object_state/PostGIS/TimescaleDB 同实例）的容器内
    # 地址。SeaTunnel 容器用 localhost 指向自身，需用 compose 服务名 ontology-postgres。
    # 用于 Kafka→TimescaleDB sink 的 JDBC URL 渲染（graph-reasoning §5.3）。
    seatunnel_pg_host: str = "ontology-postgres"
    # SeaTunnel 访问 Kafka 的容器内地址。CDC 模板（PG→Kafka→Doris）
    # 在 SeaTunnel 容器内执行，bootstrap.servers 必须用 compose 服务名，
    # 不能用 localhost（SeaTunnel 容器的 localhost 指向自身）。Kafka advertised
    # listener 为 kafka:9092（见 docker-compose.yml KAFKA_ADVERTISED_LISTENERS）。
    # 与 seatunnel_pg_host / seatunnel_s3_endpoint / seatunnel_doris_host 同构
    # （P0 CDC 链路联调，2026-07-06）。Doris 复用上方已有的 seatunnel_doris_host。
    seatunnel_kafka_bootstrap_servers: str = "kafka:9092"

    # ── Doris ──
    doris_host: str = "localhost"
    doris_port: int = 9030
    # Doris FE HTTP port — SeaTunnel's Doris sink `fenodes` expects the FE
    # HTTP endpoint (stream load), NOT the MySQL protocol port (doris_port).
    doris_fe_http_port: int = 8030
    doris_user: str = "root"
    doris_password: str = ""
    # Number of BE replicas for index tables. Single-BE dev environments
    # must set this to 1 (Doris default is 3, which fails with
    # "replication num should be less than the number of available backends"
    # when only one BE is running). Production clusters set it to 3+.
    doris_replication_num: int = 1

    # ── Trino ──
    trino_host: str = "localhost"
    trino_port: int = 8080
    trino_user: str = "ontology-api"
    trino_catalog: str = "gravitino"
    trino_schema: str = "ontology"

    # ── SeaTunnel ──
    seatunnel_host: str = "localhost"
    seatunnel_rest_port: int = 5801

    # ── Kafka (for real-time index sync) ──
    kafka_bootstrap_servers: str = "localhost:9092"

    # ── Kestra (Pipeline Builder, ADR-018 D6) ──
    kestra_host: str = "localhost"
    kestra_port: int = 8080
    kestra_password: str = ""
    # Kestra namespace prefix for pipeline flows. Each pipeline's flow lives in
    # gaia.{project_api_name}.  The namespace is created on first deploy.
    kestra_namespace_prefix: str = "gaia"

    # ── Neo4j (Graph Layer, graph-reasoning-design.md §4) ──
    # Neo4j 独立服务，docker-compose profile=graph 按需启停。driver 在
    # container 持单例，main.py lifespan aclose 关闭。
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change-me"
    # 图遍历独立信号量限流（同时 ≤ N 个多跳，C9 防线四资源隔离）。
    graph_traversal_concurrency: int = 5
    # 多跳结果上限（Palantir Search Around 官方限额，C9 防线二）。
    graph_traversal_result_limit: int = 1_000_000
    # 图遍历超时（秒，C9 防线三，只计本引擎执行）。
    graph_traversal_timeout: float = 30.0
    # 水合上限（Palantir Functions 实践，C9 防线二）。
    hydrate_limit: int = 10_000
    # Ibis filter 单步 rid 集上限（R2: memtable ~8400 行报错，保守取百万）。
    ibis_filter_result_limit: int = 1_000_000
    # 候选 rid 集分批大小（R2: PG IN 子句 >3000 性能劣化，保守取 5000）。
    rid_batch_size: int = 5_000

    # ── Application ──
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"

    # ── AI / LLM ──
    # Model identifier in pydantic-ai format: provider:model_name
    # Supported prefixes: openai, deepseek, anthropic, google, mistral,
    #   moonshotai, alibaba, groq, grok/xai, openrouter, fireworks,
    #   together, cerebras, bedrock, cohere, huggingface, ollama, etc.
    #
    # Provider API keys are declared as fields so pydantic-settings loads
    # them from .env, then re-exported to os.environ below (see settings =
    # Settings()). pydantic-ai's providers discover keys via os.getenv
    # (e.g. DeepSeekProvider reads DEEPSEEK_API_KEY), so without this
    # re-export a key present only in .env is invisible to pydantic-ai and
    # the agent call fails with a missing-key / 401 error.
    ai_model: str = "openai:gpt-4o"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 16384
    ai_retries: int = 2

    # Provider API keys (loaded from .env, re-exported to os.environ below).
    # Only the key matching ai_model's provider needs to be set, but all are
    # declared so switching providers is just an .env change.
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    mistral_api_key: str = ""
    moonshot_api_key: str = ""
    alibaba_api_key: str = ""
    groq_api_key: str = ""
    grok_api_key: str = ""
    openrouter_api_key: str = ""
    # Custom OpenAI-compatible endpoint base URL (e.g. GLM/Pangu/ZhipuAI or
    # any OpenAI-compatible gateway). Only takes effect when ai_model uses the
    # `openai:` / `openai-chat:` prefix; ignored for built-in providers
    # (deepseek/moonshotai/alibaba/... which have their own base_url baked in).
    # Empty = use OpenAI's default https://api.openai.com/v1. Re-exported to
    # the OPENAI_BASE_URL env var below ONLY when non-empty (an empty string
    # would shadow pydantic-ai's default and break requests — see comment at
    # the re-export block).
    ai_openai_base_url: str = ""

    # ── CORS ──
    # Comma-separated list of allowed origins for the web-ui. The AG-UI
    # /ai/agent endpoint is consumed via SSE from the browser, so the web-ui
    # origin must be explicitly allow-listed. Production: set to the real
    # web-ui domain. Never use "*" with allow_credentials=True.
    cors_allowed_origins: str = "http://localhost:5173"

    # ── Permission governance (ADR-016/017) ──
    # Dev-mode principal resolution: when true (and no Better Auth URL is
    # set), the AuthMiddleware resolves the Principal from X-User-Id /
    # X-User-Roles request headers (design §3.1 dev fallback). Phase 5 flips
    # this off once Better Auth is deployed and JWT verification is wired.
    authz_dev_mode: bool = True
    # Better Auth Server URL (Phase 5). Empty = dev mode (header fallback).
    # When set, PrincipalService verifies Better Auth-issued JWTs via JWKS
    # (Better Auth's jwt() plugin signs with EdDSA/Ed25519 and exposes the
    # public key at {url}/api/auth/jwks).
    better_auth_url: str = ""
    # Better Auth JWT issuer / audience for verification (Phase 5). When empty,
    # they default to better_auth_url (Better Auth uses baseURL for both).
    better_auth_jwt_issuer: str = ""
    better_auth_jwt_audience: str = ""
    # JIT auto-provisioning token (design §2.3). Shared between Better Auth
    # and Gaia — Better Auth's databaseHooks.user.create.after calls
    # POST /identity/users with this token to auto-create a Gaia user on
    # signup. Empty = JIT disabled (admin must manually create Gaia users).
    gaia_provision_token: str = ""
    # BETTER_AUTH_SECRET is still required by the better-auth service itself
    # (session-cookie signing) but Gaia verifies JWTs via JWKS, not the secret.
    # cashews permission cache URL (ADR-017 D2). mem:// for dev (single
    # process), redis://host:6379/0 for distributed production. Same code,
    # only the URL changes (Phase 1 wires this into AuthorizationService).
    permission_cache_url: str = "mem://"
    permission_cache_client_side: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


settings = Settings()

# Re-export provider API keys to os.environ so pydantic-ai's providers can
# discover them via os.getenv (e.g. DeepSeekProvider → DEEPSEEK_API_KEY).
# pydantic-settings only populates the Settings object from .env, not the
# process environment, so without this step keys defined solely in .env are
# invisible to pydantic-ai. We never overwrite a key that's already set in
# the real environment (real env wins over .env).
_PROVIDER_KEY_ENV = {
    "openai_api_key": "OPENAI_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "mistral_api_key": "MISTRAL_API_KEY",
    "moonshot_api_key": "MOONSHOT_API_KEY",
    "alibaba_api_key": "ALIBABA_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
    "grok_api_key": "GROK_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
}
for _field, _env in _PROVIDER_KEY_ENV.items():
    _val = getattr(settings, _field)
    if _val and _env not in os.environ:
        os.environ[_env] = _val

# Re-export the custom OpenAI-compatible base URL. IMPORTANT: only set the
# OPENAI_BASE_URL env var when ai_openai_base_url is non-empty. pydantic-ai's
# OpenAIProvider reads OPENAI_BASE_URL unconditionally via os.getenv and passes
# it straight to AsyncOpenAI(base_url=...); an empty string makes base_url ""
# (requests go to a malformed URL) instead of falling back to OpenAI's default.
# The absence of the env var is what triggers the default, so we never export
# an empty value.
if settings.ai_openai_base_url and "OPENAI_BASE_URL" not in os.environ:
    os.environ["OPENAI_BASE_URL"] = settings.ai_openai_base_url
