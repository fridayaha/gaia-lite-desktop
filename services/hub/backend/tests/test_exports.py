import io
import json
import uuid
import zipfile

from fastapi.testclient import TestClient


def _create_item(client: TestClient, name: str, item_type: str = "agent") -> str:
    resp = client.post("/api/hub/items", json={"name": name, "type": item_type})
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_version(
    client: TestClient, item_id: str, version: str = "1.0.0", **extra
) -> str:
    payload = {"hub_item_id": item_id, "version": version, **extra}
    resp = client.post(f"/api/hub/items/{item_id}/versions", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def _publish(client: TestClient, item_id: str, version_id: str):
    for path, body in [
        (f"/api/hub/versions/{version_id}/submit-review", {"operator": "dev"}),
        (f"/api/hub/versions/{version_id}/approve", {"operator": "approver", "comment": "ok"}),
        (f"/api/hub/versions/{version_id}/publish", {"operator": "approver"}),
    ]:
        resp = client.post(path, json=body)
        assert resp.status_code == 200


def _setup_published(client: TestClient, name: str, item_type: str = "agent") -> tuple[str, str]:
    item_id = _create_item(client, name, item_type)
    vid = _create_version(client, item_id, "1.0.0", input_schema={"a": 1})
    _publish(client, item_id, vid)
    return item_id, vid


class TestRuntimeManifest:
    def test_download_success(self, client: TestClient):
        item_id, _ = _setup_published(client, "ManAgent")
        resp = client.get(f"/api/runtime/capabilities/{item_id}/manifest")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert data["name"] == "ManAgent"
        assert "exported_at" in data
        assert "status" not in data

    def test_draft_returns_404(self, client: TestClient):
        item_id = _create_item(client, "ManDraft")
        _create_version(client, item_id)
        resp = client.get(f"/api/runtime/capabilities/{item_id}/manifest")
        assert resp.status_code == 404

    def test_disabled_returns_404(self, client: TestClient):
        item_id, vid = _setup_published(client, "ManDis")
        client.post(f"/api/hub/items/{item_id}/disable", json={"operator": "admin"})
        resp = client.get(f"/api/runtime/capabilities/{item_id}/manifest")
        assert resp.status_code == 404

    def test_blocking_returns_404(self, client: TestClient):
        item_id, vid = _setup_published(client, "ManBlock")
        client.put(f"/api/hub/items/{item_id}", json={"risk_level": "blocking"})
        resp = client.get(f"/api/runtime/capabilities/{item_id}/manifest")
        assert resp.status_code == 404

    def test_content_has_schema_fields(self, client: TestClient):
        item_id, vid = _setup_published(client, "ManContent")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"description": "test desc"},
        )
        resp = client.get(f"/api/runtime/capabilities/{item_id}/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert "manifest_json" in data
        assert "config_json" in data
        assert "input_schema" in data
        assert "output_schema" in data
        assert "permission_json" in data
        assert "runtime_compatibility" in data
        assert "relations" in data


class TestVersionPackage:
    def test_download_success(self, client: TestClient):
        item_id, vid = _setup_published(client, "PkgAgent")
        resp = client.get(
            f"/api/hub/exports/items/{item_id}/versions/{vid}/package"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/zip")

    def test_item_not_found(self, client: TestClient):
        resp = client.get(
            f"/api/hub/exports/items/{uuid.uuid4()}/versions/{uuid.uuid4()}/package"
        )
        assert resp.status_code == 404

    def test_version_not_found(self, client: TestClient):
        item_id, _ = _setup_published(client, "PkgNF")
        resp = client.get(
            f"/api/hub/exports/items/{item_id}/versions/{uuid.uuid4()}/package"
        )
        assert resp.status_code == 404

    def test_version_wrong_item(self, client: TestClient):
        item1, _ = _setup_published(client, "PkgItem1")
        item2, vid2 = _setup_published(client, "PkgItem2")
        resp = client.get(
            f"/api/hub/exports/items/{item1}/versions/{vid2}/package"
        )
        assert resp.status_code == 404

    def test_zip_content(self, client: TestClient):
        item_id, vid = _setup_published(client, "PkgContent")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"description": "zip test"},
        )
        resp = client.get(
            f"/api/hub/exports/items/{item_id}/versions/{vid}/package"
        )
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "manifest.json" in names
        assert "input_schema.json" in names
        assert "relations.json" in names
        assert "README.md" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["name"] == "PkgContent"
        assert manifest["type"] == "agent"
        assert manifest["version"] == "1.0.0"
        assert "relations" in manifest
        assert "outgoing" in manifest["relations"]
        assert "incoming" in manifest["relations"]
        relations_data = json.loads(zf.read("relations.json"))
        assert "outgoing" in relations_data
        assert "incoming" in relations_data


class TestItemExport:
    def test_download_success(self, client: TestClient):
        item_id, _ = _setup_published(client, "ExpAgent")
        resp = client.get(f"/api/hub/exports/items/{item_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/zip")

    def test_item_not_found(self, client: TestClient):
        resp = client.get(
            f"/api/hub/exports/items/{uuid.uuid4()}"
        )
        assert resp.status_code == 404

    def test_zip_content(self, client: TestClient):
        item_id, vid = _setup_published(client, "ExpContent")
        client.put(f"/api/hub/items/{item_id}", json={"description": "export test"})
        resp = client.get(f"/api/hub/exports/items/{item_id}")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "item.json" in names
        assert "versions.json" in names
        assert "relations.json" in names
        assert "README.md" in names
        item_data = json.loads(zf.read("item.json"))
        assert item_data["name"] == "ExpContent"
        versions_data = json.loads(zf.read("versions.json"))
        assert len(versions_data) >= 1
        assert versions_data[0]["version"] == "1.0.0"
        assert "manifest_json" in versions_data[0]
        assert "config_json" in versions_data[0]

    def test_draft_item_can_be_exported(self, client: TestClient):
        item_id = _create_item(client, "ExpDraft")
        _create_version(client, item_id)
        resp = client.get(f"/api/hub/exports/items/{item_id}")
        assert resp.status_code == 200
