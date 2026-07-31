"""Marketing benchmark — ontology registration (DESIGN.md P2, principle 3.5).

Consumes ``data/ontology/marketing-ontology.json`` and registers the Marketing
ontology + all ObjectTypes + Links + ActionTypes **through the system REST API**
(no direct DB writes — this is the core of principle 3.5: write path goes via API).

Flow:
  1. POST /ontologies                        → create Marketing ontology (409 = already exists, skip)
  2. POST /ontologies/Marketing/object-types/create (no links) → create each ObjectType
     Collect returned ObjectType.id (UUID) → build api_name→UUID map.
  3. POST /ontologies/Marketing/link-types   → create each link, resolving
     target_object_type_id from api_name → UUID via the map.
  4. POST /actions/definitions/Marketing/{action_type} → create each ActionType.

Idempotency: 409 (ConflictError) on ObjectType/Action/Ontology is treated as
"already registered, skip" (DESIGN.md §四-7, reuse old benchmark's pattern).
Links that already exist (409) are also skipped.

Requires: backend app running (DESIGN.md §1.1). Base URL via env
MARKETING_API_BASE (default http://localhost:8000).

Usage:
    python -m tests.benchmark.marketing.scripts.01_setup_ontology
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

ONTOLOGY_JSON = Path(__file__).resolve().parents[1] / "data" / "ontology" / "marketing-ontology.json"


class SetupError(Exception):
    """Non-recoverable setup failure."""


async def _create_ontology(client: httpx.AsyncClient, onto: dict) -> None:
    """POST /ontologies — create the Marketing ontology (409 = skip)."""
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
    """Create each ObjectType WITHOUT links (links need target UUIDs first).

    Returns ``{object_type_api_name: uuid}`` for link resolution.
    """
    id_map: dict[str, str] = {}
    for ot in object_types:
        # Strip links — they are created separately in step 3.
        ot_payload = {k: v for k, v in ot.items() if k != "links"}
        ot_payload["links"] = []
        api_name = ot["api_name"]
        resp = await client.post(f"/ontologies/{ontology}/object-types/create", json=ot_payload)
        if resp.status_code == 201:
            created = resp.json()
            id_map[api_name] = created["id"]
            logger.info("✓ Created ObjectType %-22s (%s)", api_name, created["id"][:8])
        elif resp.status_code == 409:
            # Already exists — fetch its id via GET for the link map.
            resp2 = await client.get(f"/ontologies/{ontology}/object-types/{api_name}")
            if resp2.status_code != 200:
                raise SetupError(f"existing ObjectType {api_name} fetch failed: {resp2.status_code}")
            id_map[api_name] = resp2.json()["id"]
            logger.info("→ ObjectType %-22s already exists, fetched id", api_name)
        else:
            raise SetupError(f"create ObjectType {api_name} failed: {resp.status_code} {resp.text}")
    return id_map


async def _create_links(
    client: httpx.AsyncClient,
    ontology: str,
    object_types: list[dict],
    id_map: dict[str, str],
) -> int:
    """Create each link via POST /link-types, resolving target api_name → UUID.

    Idempotent: fetches existing links first and skips any whose
    (source_uuid, target_uuid, display_name) already exists. The backend's
    define_link_type derives api_name with a fallback prefix (linkTypeN) for
    non-ASCII display_names, so it does NOT 409 on duplicates — we must
    pre-filter.
    """
    # Fetch existing links → dedup set of (source, target, display_name).
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
                logger.warning(
                    "  ⚠ link target %s not in id_map (source=%s), skip",
                    target_api,
                    ot["api_name"],
                )
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
                logger.warning(
                    "  ⚠ link %s→%s failed: %s %s",
                    ot["api_name"],
                    target_api,
                    resp.status_code,
                    resp.text[:120],
                )
    if skipped:
        logger.info("  (skipped %d already-existing links)", skipped)
    return created


async def _create_actions(client: httpx.AsyncClient, ontology: str, actions: list[dict]) -> int:
    """POST /actions/definitions/{ontology}/{action_type} for each ActionType."""
    created = 0
    for action in actions:
        api_name = action["api_name"]
        resp = await client.post(
            f"/actions/definitions/{ontology}/{api_name}",
            json=action,
        )
        if resp.status_code == 201:
            created += 1
            logger.info("✓ Created Action %-22s", api_name)
        elif resp.status_code == 409:
            logger.info("→ Action %-22s already exists, skip", api_name)
        else:
            raise SetupError(f"create Action {api_name} failed: {resp.status_code} {resp.text}")
    return created


async def setup(base_url: str) -> None:
    if not ONTOLOGY_JSON.exists():
        raise SetupError(f"ontology JSON not found: {ONTOLOGY_JSON}")

    onto = json.loads(ONTOLOGY_JSON.read_text(encoding="utf-8"))
    object_types = onto["object_types"]
    actions = onto.get("action_types", [])
    ontology_name = onto["api_name"]

    logger.info(
        "Registering ontology %s: %d ObjectTypes, %d Actions",
        ontology_name,
        len(object_types),
        len(actions),
    )

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        # 0. Health check
        resp = await client.get("/health")
        if resp.status_code != 200:
            raise SetupError(
                f"backend not healthy at {base_url} (GET /health → {resp.status_code}). "
                "Start it with: .venv/bin/python scripts/start_backend_detached.py"
            )
        logger.info("Backend healthy at %s", base_url)

        # 1. Ontology
        await _create_ontology(client, onto)

        # 2. ObjectTypes (no links)
        logger.info("--- Creating %d ObjectTypes (links deferred) ---", len(object_types))
        id_map = await _create_object_types(client, ontology_name, object_types)
        logger.info("ObjectType id_map built: %d entries", len(id_map))

        # 3. Links (resolve target api_name → UUID)
        total_links = sum(len(ot.get("links", [])) for ot in object_types)
        logger.info("--- Creating %d Links ---", total_links)
        n_links = await _create_links(client, ontology_name, object_types, id_map)
        logger.info("✓ Created %d / %d links (rest already existed or skipped)", n_links, total_links)

        # 4. Actions
        logger.info("--- Creating %d ActionTypes ---", len(actions))
        n_actions = await _create_actions(client, ontology_name, actions)
        logger.info("✓ Created %d / %d actions", n_actions, len(actions))

    logger.info("═══ Ontology registration complete ═══")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Silence httpx request logs (they flood the link-creation step).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    base_url = os.environ.get("MARKETING_API_BASE", "http://localhost:8000")
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    try:
        asyncio.run(setup(base_url))
    except SetupError as e:
        logger.error("✗ Setup failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
