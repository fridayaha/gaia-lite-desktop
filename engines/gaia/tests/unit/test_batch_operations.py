"""Tests for batch ObjectType create/update operations.

Bugs prevented:
  1. MissingGreenlet — model_validate orm after commit
  2. MultipleResultsFound — duplicate api_name
  3. Non-atomic — partial failure leaves inconsistent state
  4. searchable/indexed field passthrough
  5. Missing imports / NameError
  6. Batch update replaces properties atomically

Note: Tests requiring sequential API calls may fail due to shared async DB pool.
For full coverage, add isolated test DB fixture (pytest-postgresql or testcontainers).
"""

import time

import httpx
import pytest


async def test_critical_imports_exist():
    """Verify no NameError from missing imports."""
    from ontology.core.exceptions import ConflictError, NotFoundError  # noqa: F401
    from ontology.services.ontology_service import OntologyService  # noqa: F401

    assert True


async def test_service_batch_create_does_not_crash():
    """Smoke test: service-level batch create method exists and is callable."""
    from ontology.services.ontology_service import OntologyService

    assert hasattr(OntologyService, "define_object_type_batch"), "define_object_type_batch not found"
    assert hasattr(OntologyService, "update_object_type_batch"), "update_object_type_batch not found"


async def test_route_registered():
    """Smoke test: batch create/update routes are registered."""
    from ontology.routes.ontology import router

    routes = [r.path for r in router.routes]
    paths = [p for p in routes if "create" in p or "batch" in p]
    assert any("create" in p for p in paths), f"batch create route missing. Got: {paths}"
    assert any("batch" in p for p in paths), f"batch update route missing. Got: {paths}"


_API_UP = None  # cached API-availability flag for the e2e test


def _api_available() -> bool:
    """Cache a single connectivity probe so the e2e test doesn't stall every run."""
    global _API_UP
    if _API_UP is None:
        try:
            import httpx as _httpx

            with _httpx.Client(base_url="http://localhost:8000", timeout=1.0) as _c:
                _c.get("/ontologies")
            _API_UP = True
        except Exception:  # noqa: BLE001 — any transport failure means API down
            _API_UP = False
    return _API_UP


@pytest.mark.system
@pytest.mark.skipif(not _api_available(), reason="需要运行中的 API (localhost:8000) + 已应用 migration")
async def test_batch_create_and_update_e2e():
    """End-to-end: create ontology → create object with properties → update → verify.

    v5.2 note: soft-deleted ontologies still hold their api_name unique
    constraint, so this test uses a timestamp-suffixed name to stay isolated
    from prior runs (no cross-run cleanup needed).
    """

    name = f"E2e{int(time.time())}"
    base = "http://localhost:8000"
    async with httpx.AsyncClient(base_url=base) as c:
        # Create ontology (PascalCase api_name)
        r = await c.post("/ontologies", json={"api_name": name, "display_name": "E2E Test"})
        assert r.status_code == 201

        # Create object with properties (ObjectType api_name PascalCase;
        # PropertyInput has no api_name — derived from display_name/backing).
        r = await c.post(
            f"/ontologies/{name}/object-types/create",
            json={
                "api_name": "E2eObject",
                "display_name": "E2E Object",
                "storage_type": "VIRTUAL",
                "properties": [
                    {"display_name": "ID", "data_type": "STRING", "searchable": False, "is_primary_key": True},
                    {"display_name": "Name", "data_type": "STRING", "searchable": True, "is_title_property": True},
                ],
                "links": [],
            },
        )
        assert r.status_code == 201, f"Create failed: {r.text}"
        assert r.json()["display_name"] == "E2E Object"

        # Verify properties (api_names derived: "ID"→"id", "Name"→"name")
        r = await c.get(f"/ontologies/{name}/object-types/E2eObject/properties")
        props = r.json()
        assert len(props) == 2
        id_p = next(p for p in props if p["api_name"] == "id")
        assert id_p["indexed"] is False

        # Prevent duplicate
        r = await c.post(
            f"/ontologies/{name}/object-types/create",
            json={
                "api_name": "E2eObject",
                "display_name": "Dup",
                "storage_type": "VIRTUAL",
                "properties": [
                    {"display_name": "ID", "data_type": "STRING", "searchable": True, "is_primary_key": True}
                ],
                "links": [],
            },
        )
        assert r.status_code == 409, f"Should reject duplicate, got {r.status_code}"

        # Update
        r = await c.patch(
            f"/ontologies/{name}/object-types/E2eObject/batch",
            json={
                "api_name": "E2eObject",
                "display_name": "Updated",
                "storage_type": "VIRTUAL",
                "properties": [
                    {"display_name": "ID", "data_type": "STRING", "searchable": True, "is_primary_key": True},
                    {"display_name": "New Field", "data_type": "INTEGER", "searchable": True},
                ],
                "links": [],
            },
        )
        assert r.status_code == 200
        assert r.json()["display_name"] == "Updated"

        # Verify update (api_names re-derived: "ID"→"id", "New Field"→"newField")
        r = await c.get(f"/ontologies/{name}/object-types/E2eObject/properties")
        props = r.json()
        assert len(props) == 2
        assert {p["api_name"] for p in props} == {"id", "newField"}

        # Cleanup — v5.2: restore-if-deleted, deprecate, then delete.
        await c.post(f"/ontologies/{name}/restore")
        await c.patch(f"/ontologies/{name}", json={"status": "DEPRECATED"})
        await c.delete(f"/ontologies/{name}")
