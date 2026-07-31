"""DVP benchmark — VIRTUAL dataset registration + ontology registration.

DESIGN.md P2, principle 3.5: all registration goes through the system REST
API. No direct DB writes. DVP is all-VIRTUAL: we register a MySQL data source
(Gravitino MySQL catalog → Trino federation) and 21 VIRTUAL virtual-tables
(no Iceberg/Doris), then register the DVP ontology + 24 ObjectTypes (all
storage_type=VIRTUAL) + 31 Links. There are NO ActionTypes (DVP has no write
path).

Flow:
  0. GET /health
  1. POST /api/datasources/credentials      → MySQL username/password credential (409=skip)
  2. POST /api/datasources                  → MySQL datasource (connector_type="mysql",
                                              auto-registers Gravitino+Trino catalog) (409=skip)
  3. POST /api/datasources/{ds}/virtual-tables × 21  → register each t_* table as a
                                              VIRTUAL dataset (409=skip)
  4. POST /ontologies                       → DVP ontology (409=skip)
  5. POST /ontologies/DVP/object-types/create × 24   → each ObjectType (no links) (409=skip)
     Collect api_name → UUID map.
  6. POST /ontologies/DVP/link-types × 31   → each link, resolving target UUID (409=skip)

Idempotency: 409 = already registered, skip. Links are de-duped by
(source_uuid, target_uuid, display_name) since the backend may not 409 on
duplicate link display_names.

Requires: backend app running + MySQL source seeded (seed_dvp.py).
Base URL via env DVP_API_BASE (default http://localhost:8000).

Usage:
    python -m tests.benchmark.dvp.scripts.01_setup_ontology
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

logger = logging.getLogger("setup_ontology")

HERE = Path(__file__).resolve().parent
ONTOLOGY_JSON = HERE.parent / "data" / "ontology" / "dvp-ontology.json"

# Import the VIRTUAL_DATASETS list from build_ontology (single source of truth).
sys.path.insert(0, str(HERE))
from build_ontology import VIRTUAL_DATASETS  # noqa: E402

# ── MySQL connection config (must match seed_dvp.py defaults) ────────────
MYSQL_HOST = os.environ.get("DVP_MYSQL_HOST", "marketing-mysql")  # container name for Trino
MYSQL_PORT = int(os.environ.get("DVP_MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("DVP_MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("DVP_MYSQL_PASSWORD", "marketing123")
MYSQL_DATABASE = "dvp_benchmark"

CREDENTIAL_API_NAME = "dvpMysqlCred"
DATASOURCE_API_NAME = "dvpMysql"


class SetupError(Exception):
    """Non-recoverable setup failure."""


# ═══════════════════════════════════════════════════════════════════════════
# Step 1-2: credential + datasource
# ═══════════════════════════════════════════════════════════════════════════

async def _create_credential(client: httpx.AsyncClient) -> str:
    """Create MySQL username/password credential. Returns credential UUID."""
    resp = await client.get("/api/credentials")
    if resp.status_code == 200:
        for cred in resp.json():
            if cred["api_name"] == CREDENTIAL_API_NAME:
                logger.info("→ Credential %s already exists, reuse", CREDENTIAL_API_NAME)
                return cred["id"]
    payload = {
        "api_name": CREDENTIAL_API_NAME,
        "credential_type": "username_password",
        "secret_data": {"username": MYSQL_USER, "password": MYSQL_PASSWORD},
    }
    resp = await client.post("/api/credentials", json=payload)
    if resp.status_code == 201:
        logger.info("✓ Created credential %s", CREDENTIAL_API_NAME)
        return resp.json()["id"]
    raise SetupError(f"create credential failed: {resp.status_code} {resp.text}")


async def _create_datasource(client: httpx.AsyncClient, credential_id: str) -> None:
    """Create MySQL datasource (auto-registers Gravitino+Trino catalog)."""
    # Check existing
    resp = await client.get("/api/datasources")
    if resp.status_code == 200:
        for ds in resp.json():
            if ds["api_name"] == DATASOURCE_API_NAME:
                logger.info("→ Datasource %s already exists, skip", DATASOURCE_API_NAME)
                return
    payload = {
        "api_name": DATASOURCE_API_NAME,
        "display_name": "DVP MySQL 源端",
        "description": "DVP benchmark MySQL source (VIRTUAL federation target)",
        "connector_type": "mysql",
        "connector_config": {
            "host": MYSQL_HOST,
            "port": MYSQL_PORT,
            "database": MYSQL_DATABASE,
            "username": MYSQL_USER,
            "password": MYSQL_PASSWORD,
        },
        "credential_id": credential_id,
    }
    resp = await client.post("/api/datasources", json=payload)
    if resp.status_code == 201:
        logger.info("✓ Created datasource %s (Gravitino+Trino catalog registered)", DATASOURCE_API_NAME)
        return
    if resp.status_code == 409:
        logger.info("→ Datasource %s already exists, skip", DATASOURCE_API_NAME)
        return
    raise SetupError(f"create datasource failed: {resp.status_code} {resp.text}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 3: register VIRTUAL virtual-tables
# ═══════════════════════════════════════════════════════════════════════════

async def _register_virtual_tables(client: httpx.AsyncClient) -> int:
    """Register each (dataset_api_name, mysql_table) as a VIRTUAL dataset."""
    # Fetch existing datasets to skip already-registered ones.
    resp = await client.get("/api/datasets")
    existing: set[str] = set()
    if resp.status_code == 200:
        existing = {d["api_name"] for d in resp.json()}

    created = 0
    skipped = 0
    for dataset_api, mysql_table, display_name in VIRTUAL_DATASETS:
        if dataset_api in existing:
            skipped += 1
            continue
        payload = {
            "database": MYSQL_DATABASE,
            "table": mysql_table,
            "api_name": dataset_api,
            "display_name": display_name,
        }
        resp = await client.post(
            f"/api/datasources/{DATASOURCE_API_NAME}/virtual-tables", json=payload
        )
        if resp.status_code == 201:
            created += 1
            row_count = resp.json().get("row_count_estimate")
            logger.info("  ✓ VIRTUAL %-24s ← %-30s (rows=%s)", dataset_api, mysql_table, row_count)
        elif resp.status_code == 409:
            skipped += 1
        else:
            logger.warning(
                "  ⚠ register VIRTUAL %s failed: %s %s",
                dataset_api, resp.status_code, resp.text[:160],
            )
    if skipped:
        logger.info("  (skipped %d already-existing VIRTUAL datasets)", skipped)
    return created


# ═══════════════════════════════════════════════════════════════════════════
# Step 4-6: ontology + object types + links
# ═══════════════════════════════════════════════════════════════════════════

async def _create_ontology(client: httpx.AsyncClient, onto: dict) -> None:
    payload = {
        "api_name": onto["api_name"],
        "display_name": onto["display_name"],
        "description": onto["description"],
    }
    resp = await client.post("/ontologies", json=payload)
    if resp.status_code == 201:
        logger.info("✓ Created ontology %s", onto["api_name"])
    elif resp.status_code == 409:
        logger.info("→ Ontology %s already exists, skip", onto["api_name"])
    else:
        raise SetupError(f"create ontology failed: {resp.status_code} {resp.text}")


async def _create_object_types(client: httpx.AsyncClient, ontology: str, object_types: list[dict]) -> dict[str, str]:
    """Create each ObjectType WITHOUT links. Returns {api_name: uuid}."""
    id_map: dict[str, str] = {}
    for ot in object_types:
        api = ot["api_name"]
        ot_payload = {k: v for k, v in ot.items() if k != "links"}
        ot_payload["links"] = []
        resp = await client.post(f"/ontologies/{ontology}/object-types/create", json=ot_payload)
        if resp.status_code == 201:
            id_map[api] = resp.json()["id"]
            logger.info("  ✓ OT %-24s", api)
        elif resp.status_code == 409:
            # Already exists — fetch its id via GET for the link map.
            resp2 = await client.get(f"/ontologies/{ontology}/object-types/{api}")
            if resp2.status_code == 200:
                id_map[api] = resp2.json()["id"]
                logger.info("  → OT %-24s already exists, fetched id", api)
            else:
                logger.warning("  ⚠ OT %s exists but GET failed: %s", api, resp2.status_code)
        else:
            raise SetupError(f"create OT {api} failed: {resp.status_code} {resp.text[:200]}")
    return id_map


async def _create_links(client: httpx.AsyncClient, ontology: str, object_types: list[dict], id_map: dict[str, str]) -> int:
    """Create each link, resolving target api_name → UUID. De-dup by (src, tgt, display)."""
    resp = await client.get(f"/ontologies/{ontology}/link-types")
    if resp.status_code != 200:
        raise SetupError(f"list link-types failed: {resp.status_code} {resp.text}")
    existing: set[tuple[str, str, str]] = {
        (lk["source_object_type_id"], lk["target_object_type_id"], lk["display_name"]) for lk in resp.json()
    }
    logger.info("  (existing links on server: %d)", len(existing))

    created = 0
    skipped = 0
    for ot in object_types:
        source_uuid = id_map[ot["api_name"]]
        for link_def in ot.get("links", []):
            target_api = link_def["target_object_type_id"]
            if target_api not in id_map:
                logger.warning("  ⚠ link target %s not in id_map (source=%s), skip", target_api, ot["api_name"])
                continue
            target_uuid = id_map[target_api]
            display_name = link_def["display_name"]
            key = (source_uuid, target_uuid, display_name)
            if key in existing:
                skipped += 1
                continue
            payload = {
                "display_name": display_name,
                "api_name": link_def.get("api_name"),
                "source_object_type_id": source_uuid,
                "target_object_type_id": target_uuid,
                "cardinality": link_def.get("cardinality", "MANY"),
                "direction": link_def.get("direction", "OUTGOING"),
            }
            resp = await client.post(f"/ontologies/{ontology}/link-types", json=payload)
            if resp.status_code == 201:
                created += 1
                existing.add(key)
            elif resp.status_code == 409:
                skipped += 1
            else:
                logger.warning("  ⚠ link %s→%s failed: %s %s", ot["api_name"], target_api, resp.status_code, resp.text[:120])
    if skipped:
        logger.info("  (skipped %d already-existing links)", skipped)
    return created


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

async def setup(base_url: str) -> None:
    if not ONTOLOGY_JSON.exists():
        raise SetupError(f"ontology JSON not found: {ONTOLOGY_JSON}. Run build_ontology first.")
    onto = json.loads(ONTOLOGY_JSON.read_text(encoding="utf-8"))
    object_types = onto["object_types"]
    ontology_name = onto["api_name"]

    logger.info("═══ DVP setup: %d VIRTUAL datasets + ontology %s (%d OT, %d links) ═══",
                len(VIRTUAL_DATASETS), ontology_name, len(object_types),
                sum(len(ot.get("links", [])) for ot in object_types))

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        # 0. Health
        resp = await client.get("/health")
        if resp.status_code != 200:
            raise SetupError(f"backend not healthy at {base_url} (GET /health → {resp.status_code})")
        logger.info("Backend healthy at %s", base_url)

        # 1-2. Credential + Datasource (MySQL catalog)
        logger.info("--- Step 1-2: MySQL credential + datasource ---")
        cred_id = await _create_credential(client)
        await _create_datasource(client, cred_id)

        # 3. VIRTUAL virtual-tables
        logger.info("--- Step 3: Register %d VIRTUAL virtual-tables ---", len(VIRTUAL_DATASETS))
        n_vt = await _register_virtual_tables(client)
        logger.info("✓ Registered %d / %d VIRTUAL datasets", n_vt, len(VIRTUAL_DATASETS))

        # 4. Ontology
        logger.info("--- Step 4: Ontology %s ---", ontology_name)
        await _create_ontology(client, onto)

        # 5. ObjectTypes
        logger.info("--- Step 5: %d ObjectTypes (links deferred) ---", len(object_types))
        id_map = await _create_object_types(client, ontology_name, object_types)
        logger.info("  id_map built: %d entries", len(id_map))

        # 6. Links
        total_links = sum(len(ot.get("links", [])) for ot in object_types)
        logger.info("--- Step 6: %d Links ---", total_links)
        n_links = await _create_links(client, ontology_name, object_types, id_map)
        logger.info("✓ Created %d / %d links", n_links, total_links)

    logger.info("═══ DVP setup complete ═══")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    base_url = os.environ.get("DVP_API_BASE", "http://localhost:8000")
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    try:
        asyncio.run(setup(base_url))
    except SetupError as e:
        logger.error("✗ Setup failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
