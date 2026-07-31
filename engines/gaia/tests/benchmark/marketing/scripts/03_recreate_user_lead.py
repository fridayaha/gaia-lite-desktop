"""One-off: drop + recreate the User and Lead ObjectTypes after schema fix.

The Marketing ontology was first registered with phoneBrand/phoneDeviceModel
as no-source props (auto-backfilled to non-existent columns) and 建档时间
duplicated on filing_time. After fixing build_ontology to source them on real
always-null columns (phone_brand/phone_device_model/filing_create_time), the
registered OTs are stale. This script deletes the two OTs (CASCADE removes
their properties/links/dataset links) and re-creates them from the refreshed
ontology JSON, then re-links their datasets.

Idempotent-ish: deletes if present, creates fresh. Re-run safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("recreate_user_lead")

ONTO_JSON = Path(__file__).resolve().parents[1] / "data" / "ontology" / "marketing-ontology.json"
ONTO = "Marketing"
# Default: the OTs whose property set changed in this fix pass. Override via
# argv (comma-separated PascalCase api_names).
DEFAULT_TARGETS = {
    "User",
    "Lead",
    "LeadAllocateRecord",
    "LeadDistributeRecord",
    "LeadFollowRecord",
    "ManualOutboundCall",
    "AiOutboundCall",
    "TestDrive",
}
TARGETS = DEFAULT_TARGETS


async def main() -> int:
    base = os.environ.get("MARKETING_API_BASE", "http://localhost:8000")
    if len(sys.argv) > 1:
        global TARGETS
        TARGETS = {s.strip() for s in sys.argv[1].split(",") if s.strip()}
    onto = json.loads(ONTO_JSON.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
        # health
        r = await client.get("/health")
        if r.status_code != 200:
            log.error("backend down: %s", r.status_code)
            return 1
        for ot_def in onto["object_types"]:
            api = ot_def["api_name"]
            if api not in TARGETS:
                continue
            # 1. delete if exists (204 or 404 both fine)
            r = await client.delete(f"/ontologies/{ONTO}/object-types/{api}")
            log.info("delete %s → %s", api, r.status_code)
            # 2. re-create (no links — links need target UUIDs; the original
            #    setup script handles cross-OT links. We only re-create the OT
            #    itself + its properties/dataset binding.)
            payload = {k: v for k, v in ot_def.items() if k != "links"}
            payload["links"] = []
            r = await client.post(f"/ontologies/{ONTO}/object-types/create", json=payload)
            if r.status_code == 201:
                log.info("✓ re-created %s (id=%s)", api, r.json().get("id", "")[:8])
            else:
                log.error("✗ re-create %s failed: %s %s", api, r.status_code, r.text[:300])
                return 1
        # 3. re-create links FROM User/Lead TO other OTs (their outbound links).
        #    First build the api_name→UUID map for ALL OTs.
        r = await client.get(f"/ontologies/{ONTO}/object-types/summary")
        summary = r.json()
        id_map = {s["api_name"]: s["id"] for s in summary}
        existing_links_r = await client.get(f"/ontologies/{ONTO}/link-types")
        existing = {
            (lk["source_object_type_id"], lk["target_object_type_id"], lk["display_name"])
            for lk in existing_links_r.json()
        }
        for ot_def in onto["object_types"]:
            if ot_def["api_name"] not in TARGETS:
                continue
            src_uuid = id_map[ot_def["api_name"]]
            for link_def in ot_def.get("links", []):
                tgt_api = link_def["target_object_type_id"]
                if tgt_api not in id_map:
                    continue
                tgt_uuid = id_map[tgt_api]
                key = (src_uuid, tgt_uuid, link_def["display_name"])
                if key in existing:
                    continue
                payload = {
                    "display_name": link_def["display_name"],
                    "api_name": link_def.get("api_name"),
                    "source_object_type_id": src_uuid,
                    "target_object_type_id": tgt_uuid,
                    "cardinality": link_def.get("cardinality", "MANY"),
                    "direction": link_def.get("direction", "OUTGOING"),
                }
                r = await client.post(f"/ontologies/{ONTO}/link-types", json=payload)
                if r.status_code == 201:
                    log.info("✓ re-linked %s→%s", ot_def["api_name"], tgt_api)
                else:
                    log.warning("link %s→%s: %s %s", ot_def["api_name"], tgt_api, r.status_code, r.text[:120])
    log.info("═══ User/Lead recreation complete ═══")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
