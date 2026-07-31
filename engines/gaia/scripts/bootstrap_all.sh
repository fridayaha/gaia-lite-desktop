#!/usr/bin/env bash
# Bootstrap all infrastructure services + initialize Gravitino metalake
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Starting Ontology Infrastructure ==="

# Start core storage
echo "[1/4] Starting RustFS (S3 storage)..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d rustfs
sleep 3

# Note: the ontology-warehouse bucket is created by the backend code
# (IcebergStore.ensure_warehouse_bucket) on first use, NOT here — keeping
# storage provisioning in code (alongside Doris DB / Iceberg namespace
# auto-create) avoids external-script dependencies.

# Start Gravitino（含内置 Iceberg REST Catalog，端口 9001，无独立 iceberg-rest 服务）
echo "[2/4] Starting Gravitino (+ built-in Iceberg REST on 9001)..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d gravitino
sleep 5

# Start PostgreSQL (runs init-pg-schema.sql + gravitino-pg-schema.sql)
echo "[3/4] Starting PostgreSQL (metadata store)..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d postgres
sleep 5

# Start Gravitino, Doris, Trino, SeaTunnel
echo "[4/4] Starting query and pipeline engines..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d gravitino doris-fe doris-be trino seatunnel-master seatunnel-worker

echo ""
echo "=== Waiting for Gravitino to be healthy ==="
for i in $(seq 1 30); do
    if curl -sf http://localhost:8090/api/metalakes >/dev/null 2>&1; then
        echo "Gravitino ready."
        break
    fi
    echo -n "."
    sleep 3
done

# ── Initialize Gravitino metalake & pg catalog (idempotent) ──
echo ""
echo "=== Initializing Gravitino metalake & catalog ==="

# SeaTunnel cluster health: ensure the worker has joined the master
# (without a worker, jobs fail with NoEnoughResourceException). Wait up
# to 60s for 2 members.
echo "=== Waiting for SeaTunnel cluster (master + worker) ==="
for i in $(seq 1 20); do
    MEMBERS=$(curl -sf http://localhost:5801/hazelcast/rest/maps/cluster-info 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('members',[])))" 2>/dev/null || echo 0)
    if [ "$MEMBERS" -ge 2 ]; then
        echo "SeaTunnel cluster ready ($MEMBERS members)."
        break
    fi
    echo -n "."
    sleep 3
done
if [ "$MEMBERS" -lt 2 ]; then
    echo "[WARN] SeaTunnel cluster has only $MEMBERS member(s); worker may not have joined."
fi

echo ""

# Check if metalake already exists
EXISTING=$(curl -sf http://localhost:8090/api/metalakes | python3 -c "
import json,sys
mls=json.load(sys.stdin).get('metalakes',[])
names=[m['name'] for m in mls]
print(','.join(names))
" 2>/dev/null || echo "")

if echo "$EXISTING" | grep -q "ontology"; then
    echo "Metalake 'ontology' already exists — skipping."
else
    echo "Creating metalake 'ontology'..."
    curl -sf -X POST http://localhost:8090/api/metalakes \
        -H "Content-Type: application/json" \
        -d '{"name":"ontology","comment":"Gaia ontology metalake","properties":{}}' >/dev/null
    echo "Metalake created."
fi

# Check if pg catalog already exists under ontology metalake
CAT_EXISTING=$(curl -sf http://localhost:8090/api/metalakes/ontology/catalogs | python3 -c "
import json,sys
d=json.load(sys.stdin)
cats=d.get('identifiers',[]) or d.get('catalogs',[])
names=[(c['name'] if isinstance(c,dict) else c) for c in cats]
print(','.join(names))
" 2>/dev/null || echo "")

if echo "$CAT_EXISTING" | grep -q "pg"; then
    echo "Catalog 'pg' already exists — ensuring type-converter properties..."
    curl -sf -X PUT http://localhost:8090/api/metalakes/ontology/catalogs/pg \
        -H "Content-Type: application/json" \
        -d '{
            "updates": [
                {"@type": "setProperty", "property": "type-converter.enabled", "value": "true"},
                {"@type": "setProperty", "property": "type-converter.custom.mapping", "value": "jsonb=JSON,json=JSON,uuid=VARCHAR,inet=VARCHAR,geometry=VARCHAR,geography=VARCHAR,text=VARCHAR"}
            ]
        }' >/dev/null
    echo "Type-converter properties ensured."
else
    echo "Creating JDBC catalog 'pg'..."
    curl -sf -X POST http://localhost:8090/api/metalakes/ontology/catalogs \
        -H "Content-Type: application/json" \
        -d '{
            "name":"pg",
            "type":"relational",
            "provider":"jdbc-postgresql",
            "comment":"PostgreSQL data source",
            "properties":{
                "jdbc-url":"jdbc:postgresql://postgres:5432/ontology",
                "jdbc-user":"ontology",
                "jdbc-password":"ontology",
                "jdbc-database":"ontology",
                "jdbc-driver":"org.postgresql.Driver",
                "type-converter.enabled":"true",
                "type-converter.custom.mapping":"jsonb=JSON,json=JSON,uuid=VARCHAR,inet=VARCHAR,geometry=VARCHAR,geography=VARCHAR,text=VARCHAR"
            }
        }' >/dev/null
    echo "Catalog created."
fi

echo ""
echo "=== Initializing Iceberg namespace ==="
# Iceberg namespace must exist before sync tasks can run
NS=$(curl -sf http://localhost:9001/iceberg/v1/namespaces | python3 -c "
import json,sys
ns=json.load(sys.stdin).get('namespaces',[])
names=[n[0] for n in ns]
print(','.join(names))
" 2>/dev/null || echo "")

if echo "$NS" | grep -q "ontology"; then
    echo "Iceberg namespace 'ontology' already exists — skipping."
else
    echo "Creating Iceberg namespace 'ontology'..."
    curl -sf -X POST http://localhost:9001/iceberg/v1/namespaces \
        -H "Content-Type: application/json" \
        -d '{"namespace":["ontology"],"properties":{}}' >/dev/null
    echo "Namespace created."
fi

echo ""
echo "=== Infrastructure Ready ==="
echo "  PostgreSQL  :5432     (ontology metadata)"
echo "  Gravitino   :8090     (physical asset registry → PG-backed)"
echo "  Iceberg REST:9001     (Gravitino 内置 REST catalog，无 8181)"
echo "  RustFS      :9000     (S3-compatible storage)"
echo "  Doris FE    :9030     (index acceleration)"
echo "  Trino       :8080     (query engine)"
echo ""
echo "  ⚠️ 本脚本为早期手写编排，新环境部署推荐直接 docker compose up -d"
echo "     （depends_on + healthcheck 已自动编排，且后端会幂等自举 metalake/namespace）"
echo "     部署完成性检查见：bash scripts/healthcheck.sh"
echo ""
echo "Start the API with:  docker compose up -d api"
echo "Or locally with:     uv run uvicorn ontology.main:app --reload"
