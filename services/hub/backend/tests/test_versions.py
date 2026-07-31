from fastapi.testclient import TestClient


class TestCreateVersion:
    def test_create_version_success(self, client: TestClient):
        resp = client.post(
            "/api/hub/items", json={"name": "V-Test", "type": "tool"}
        )
        item_id = resp.json()["id"]
        resp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={"hub_item_id": item_id, "version": "1.0.0"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["version"] == "1.0.0"
        assert data["status"] == "draft"
        assert data["risk_level"] == "low"
        assert data["hub_item_id"] == item_id

    def test_create_version_item_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = client.post(
            f"/api/hub/items/{fake_id}/versions",
            json={"hub_item_id": fake_id, "version": "1.0.0"},
        )
        assert resp.status_code == 404

    def test_create_version_duplicate(self, client: TestClient):
        resp = client.post(
            "/api/hub/items", json={"name": "Dup-Test", "type": "tool"}
        )
        item_id = resp.json()["id"]
        payload = {"hub_item_id": item_id, "version": "2.0.0"}
        r1 = client.post(
            f"/api/hub/items/{item_id}/versions", json=payload
        )
        assert r1.status_code == 201
        r2 = client.post(
            f"/api/hub/items/{item_id}/versions", json=payload
        )
        assert r2.status_code == 409


class TestListVersions:
    def test_list_versions(self, client: TestClient):
        resp = client.post(
            "/api/hub/items", json={"name": "L-Test", "type": "agent"}
        )
        item_id = resp.json()["id"]
        for v in ["0.1.0", "0.2.0", "1.0.0"]:
            client.post(
                f"/api/hub/items/{item_id}/versions",
                json={"hub_item_id": item_id, "version": v},
            )
        resp = client.get(f"/api/hub/items/{item_id}/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_list_versions_item_not_found(self, client: TestClient):
        resp = client.get(
            "/api/hub/items/00000000-0000-0000-0000-000000000000/versions"
        )
        assert resp.status_code == 404


class TestGetVersion:
    def test_get_version_success(self, client: TestClient):
        resp = client.post(
            "/api/hub/items", json={"name": "G-Test", "type": "skill"}
        )
        item_id = resp.json()["id"]
        vresp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={"hub_item_id": item_id, "version": "3.0.0"},
        )
        v_id = vresp.json()["id"]
        resp = client.get(f"/api/hub/items/{item_id}/versions/{v_id}")
        assert resp.status_code == 200
        assert resp.json()["version"] == "3.0.0"

    def test_get_version_not_found(self, client: TestClient):
        resp = client.post(
            "/api/hub/items", json={"name": "NF-Test", "type": "tool"}
        )
        item_id = resp.json()["id"]
        fake_vid = "00000000-0000-0000-0000-000000000000"
        resp = client.get(f"/api/hub/items/{item_id}/versions/{fake_vid}")
        assert resp.status_code == 404
