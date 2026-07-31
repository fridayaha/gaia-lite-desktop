from fastapi.testclient import TestClient


class TestCreateItem:
    def test_create_item_success(self, client: TestClient):
        payload = {
            "name": "Test Agent",
            "type": "agent",
            "description": "A test agent",
            "created_by": "tester",
        }
        resp = client.post("/api/hub/items", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Agent"
        assert data["type"] == "agent"
        assert data["status"] == "draft"
        assert data["risk_level"] == "low"
        assert data["source_type"] == "manual"
        assert data["discoverable"] is True
        assert data["allow_existing_references"] is True
        assert data["force_disabled"] is False
        assert data["id"] is not None
        assert data["created_at"] is not None
        assert data["updated_at"] is not None


class TestListItems:
    def test_list_items_empty(self, client: TestClient):
        resp = client.get("/api/hub/items")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_items_with_data(self, client: TestClient):
        for i in range(3):
            client.post(
                "/api/hub/items",
                json={"name": f"Item {i}", "type": "tool"},
            )
        resp = client.get("/api/hub/items")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["total"] == 3

    def test_list_items_pagination(self, client: TestClient):
        for i in range(5):
            client.post(
                "/api/hub/items",
                json={"name": f"Item {i}", "type": "tool"},
            )
        resp = client.get("/api/hub/items?skip=0&limit=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5

    def test_list_items_filter_type(self, client: TestClient):
        client.post("/api/hub/items", json={"name": "A", "type": "agent"})
        client.post("/api/hub/items", json={"name": "M", "type": "mcp"})
        resp = client.get("/api/hub/items?type=agent")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["type"] == "agent"

    def test_list_items_filter_keyword(self, client: TestClient):
        client.post(
            "/api/hub/items",
            json={"name": "Alpha", "type": "tool", "description": "first"},
        )
        client.post(
            "/api/hub/items",
            json={"name": "Beta", "type": "tool", "description": "second"},
        )
        resp = client.get("/api/hub/items?keyword=alp")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Alpha"

    def test_list_items_filter_featured(self, client: TestClient):
        client.post("/api/hub/items", json={"name": "普通", "type": "tool"})
        client.post(
            "/api/hub/items",
            json={"name": "精选", "type": "tool", "featured": True},
        )
        resp = client.get("/api/hub/items?featured=true")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "精选"
        assert data["items"][0]["featured"] is True
        # 未标记 featured 的项默认 False
        resp_all = client.get("/api/hub/items")
        for it in resp_all.json()["items"]:
            assert it["featured"] in (True, False)

    def test_list_items_expose_tags_field(self, client: TestClient):
        client.post("/api/hub/items", json={"name": "T", "type": "tool"})
        resp = client.get("/api/hub/items")
        # tags 字段必须存在且为 list（即便为空）
        for it in resp.json()["items"]:
            assert isinstance(it["tags"], list)


class TestGetItem:
    def test_get_item_success(self, client: TestClient):
        resp = client.post(
            "/api/hub/items",
            json={"name": "Detail Test", "type": "skill"},
        )
        item_id = resp.json()["id"]
        resp = client.get(f"/api/hub/items/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Detail Test"

    def test_get_item_not_found(self, client: TestClient):
        resp = client.get("/api/hub/items/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestUpdateItem:
    def test_update_item_success(self, client: TestClient):
        resp = client.post(
            "/api/hub/items",
            json={"name": "Before", "type": "tool"},
        )
        item_id = resp.json()["id"]
        resp = client.put(
            f"/api/hub/items/{item_id}",
            json={"name": "After", "description": "updated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "After"
        assert data["description"] == "updated"

    def test_update_item_not_found(self, client: TestClient):
        resp = client.put(
            "/api/hub/items/00000000-0000-0000-0000-000000000000",
            json={"name": "Nope"},
        )
        assert resp.status_code == 404
