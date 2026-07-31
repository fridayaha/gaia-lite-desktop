#!/bin/sh
# Kestra entrypoint shim.
#
# PostgreSQL with wal_level=logical (required by SeaTunnel CDC) means
# all tables used for UPDATE/DELETE need REPLICA IDENTITY. Kestra's
# Flyway migration creates tables without it, so we fix them here
# before Kestra starts (idempotent).
PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-ontology}"
PGPASSWORD="${PGPASSWORD:-ontology}"
PGDATABASE="${PGDATABASE:-ontology}"

export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

# ── 1. PostgreSQL REPLICA IDENTITY (wal_level=logical compat) ──
if psql -t -c "SELECT 1 FROM pg_tables WHERE schemaname='kestra' AND tablename!='flyway_schema_history' LIMIT 1" 2>/dev/null | grep -q 1; then
  psql -t -c "
    DO \$\$
    DECLARE
      r RECORD;
    BEGIN
      FOR r IN SELECT tablename FROM pg_tables
               WHERE schemaname = 'kestra' AND tablename != 'flyway_schema_history'
      LOOP
        EXECUTE format('ALTER TABLE kestra.%I REPLICA IDENTITY FULL', r.tablename);
      END LOOP;
    END;
    \$\$;
  " 2>/dev/null || echo "[kestra-entrypoint] WARNING: replica identity fix skipped (kestra schema may not exist yet)"
fi

# ── 2. DuckDB Iceberg extension (预装，避免首次 LOAD 在线下载) ──
# DuckDB 1.5.3 linux_amd64。来源 https://github.com/duckdb/duckdb_iceberg/releases
#
# 分发方式（二进制 ~18MB 超 gitcode 10MB 限制，不入 git，见 infra/Dockerfile.kestra）：
#   主路径：自定义 Kestra 镜像预装 → ACR 分发（docker pull 即自带，见 infra/Dockerfile.kestra）
#   备路径：挂载 ./infra/extensions/ 卷（本地手动放置 .gz）
#   兜底  ：首次 LOAD iceberg 时 DuckDB 在线下载（需外网，部署环境通常不可用）
DUCKDB_EXT_DIR="${HOME}/.duckdb/extensions/v1.5.3/linux_amd64"
ICEBERG_EXT="${DUCKDB_EXT_DIR}/iceberg.duckdb_extension"
ICEBERG_SRC="/opt/kestra/extensions/iceberg.duckdb_extension.gz"

if [ -f "${ICEBERG_SRC}" ] && [ ! -f "${ICEBERG_EXT}" ]; then
  mkdir -p "${DUCKDB_EXT_DIR}"
  if gunzip -c "${ICEBERG_SRC}" > "${ICEBERG_EXT}" 2>/dev/null; then
    chmod 644 "${ICEBERG_EXT}"
    echo "[kestra-entrypoint] Installed DuckDB Iceberg extension (49M)"
  else
    echo "[kestra-entrypoint] WARNING: failed to decompress Iceberg extension"
  fi
else
  echo "[kestra-entrypoint] DuckDB Iceberg extension not bundled; first LOAD will fetch online (needs egress to duckdb.org)"
fi

exec /app/kestra "$@"
