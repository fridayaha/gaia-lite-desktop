#!/bin/sh
# SeaTunnel container entrypoint shim.
#
# WHY THIS EXISTS
# ---------------
# SeaTunnel 2.3.13 ships both `postgresql-42.4.3.jar` and
# `opengauss-jdbc-5.1.0.jar` in /opt/seatunnel/lib. The openGauss driver
# jar bundles a *full copy* of `org/postgresql/Driver.class` (for PG
# compatibility), so both jars register a driver with the identical class
# name `org.postgresql.Driver` for the `jdbc:postgresql://` URL scheme.
#
# SeaTunnel's `AbstractJdbcCatalog.getConnection` (which already includes
# the PR #8986 "pick driver by class name" logic) cannot disambiguate
# two drivers with the *same* class name — it falls back to
# `DriverManager.getConnection`, which iterates registered drivers in
# load order. The openGauss driver is loaded first and wins, producing
# `Protocol error. Session setup failed` against standard PostgreSQL
# (openGauss's SCRAM/SHA-256 auth is not wire-compatible with vanilla PG).
#
# This is tracked upstream as apache/seatunnel#10229 / #10242 (closed
# 2026-01 via a ClassLoader-level fix that has NOT shipped in a release
# as of 2.3.13). Until a SeaTunnel release containing that fix is adopted
# here, the only reliable workaround is to keep the two conflicting
# jars off the same classpath.
#
# WHAT WE DO (reversible, not destructive)
# ----------------------------------------
# Move the openGauss driver into a *parked* directory that is not on the
# engine classpath. The file is preserved inside the container, so
# restoring it is a one-line `mv` — no image rebuild needed when an
# openGauss source is introduced later. We pick on openGauss (rather
# than postgresql) because:
#   1. openGauss's driver is not wire-compatible with standard PG auth,
#      so keeping it would break the PG path that this project uses today.
#   2. The canonical postgresql driver connects to openGauss *only* when
#      openGauss is configured with `password_encryption_type=1` (MD5-
#      compatible). For full openGauss sha256 support, the right long-
#      term fix is upgrading SeaTunnel to the release that ships #10229,
#      at which point this shim can be deleted.
#
# To re-enable openGauss support later: remove this entrypoint override
# (or `mv /opt/seatunnel/lib.parked/opengauss-jdbc-*.jar back`), after
# upgrading SeaTunnel to a version with the upstream ClassLoader fix.
set -e

PARKED_DIR="/opt/seatunnel/lib.parked"
mkdir -p "$PARKED_DIR"

for jar in /opt/seatunnel/lib/opengauss-jdbc-*.jar; do
    [ -e "$jar" ] || continue
    parked="$PARKED_DIR/$(basename "$jar")"
    if [ ! -e "$parked" ]; then
        echo "[seatunnel-entrypoint] parking conflicting driver: $(basename "$jar")"
        mv "$jar" "$parked"
    elif [ -e "$jar" ]; then
        # Already parked on a previous boot but the file reappeared (image
        # layer reset) — remove the duplicate on the active classpath.
        echo "[seatunnel-entrypoint] removing re-appeared conflicting driver: $(basename "$jar")"
        rm -f "$jar"
    fi
done

exec "$@"
