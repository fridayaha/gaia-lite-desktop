from fastapi.testclient import TestClient


def _init_presets(client: TestClient) -> dict:
    resp = client.post("/api/hub/presets/init")
    assert resp.status_code == 200
    return resp.json()


class TestPresetInit:
    def test_init_creates_all_types(self, client: TestClient):
        result = _init_presets(client)
        assert result["created"] >= 4
        types = {item["type"] for item in result["items"]}
        assert types == {"agent", "mcp", "skill", "tool"}

    def test_init_idempotent(self, client: TestClient):
        first = _init_presets(client)
        second = _init_presets(client)
        assert second["created"] == 0
        assert second["skipped"] == first["created"] + first["skipped"]

    def test_preset_source_type(self, client: TestClient):
        result = _init_presets(client)
        for item in result["items"]:
            assert item["source_type"] == "preset"

    def test_preset_status_draft(self, client: TestClient):
        result = _init_presets(client)
        for item in result["items"]:
            assert item["status"] == "draft"
