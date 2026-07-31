import json

from fastapi.testclient import TestClient

from app.manifests import validate_manifest


def _make_manifest(overrides: dict | None = None) -> dict:
    base = {
        "name": "test-asset",
        "type": "agent",
        "version": "0.1.0",
        "description": "A test asset",
    }
    if overrides:
        base.update(overrides)
    return base


class TestValidManifests:
    def test_valid_agent_manifest_pass(self):
        m = _make_manifest({"type": "agent"})
        result = validate_manifest(m)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_valid_skill_manifest_pass(self):
        m = _make_manifest({"type": "skill"})
        result = validate_manifest(m)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_valid_tool_manifest_pass(self):
        m = _make_manifest({"type": "tool"})
        result = validate_manifest(m)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_valid_mcp_manifest_pass(self):
        m = _make_manifest({"type": "mcp"})
        result = validate_manifest(m)
        assert result.valid is True
        assert len(result.errors) == 0


class TestManifestErrors:
    def test_invalid_type_returns_error(self):
        m = _make_manifest({"type": "unknown"})
        result = validate_manifest(m)
        assert result.valid is False
        assert any(
            e.field == "type" and e.level == "error" for e in result.errors
        )

    def test_unsupported_manifest_version_error(self):
        m = _make_manifest({"type": "agent", "manifest_version": "0.5"})
        result = validate_manifest(m)
        assert result.valid is False
        assert any(
            e.field == "manifest_version" and e.level == "error"
            for e in result.errors
        )

    def test_mcp_transport_invalid_error(self):
        m = _make_manifest({"type": "mcp", "transport": "tcp"})
        result = validate_manifest(m)
        assert result.valid is False
        assert any(
            e.field == "transport" and e.level == "error"
            for e in result.errors
        )


class TestManifestWarnings:
    def test_missing_manifest_version_warning_and_default(self):
        m = _make_manifest({"type": "agent"})
        result = validate_manifest(m)
        assert result.valid is True
        assert any(
            w.field == "manifest_version" and w.level == "warning"
            for w in result.warnings
        )
        assert result.normalized_manifest["manifest_version"] == "0.1"

    def test_missing_permission_json_warning_all_types(self):
        for t in ("agent", "skill", "tool", "mcp"):
            m = _make_manifest({"type": t})
            result = validate_manifest(m)
            assert result.valid is True
            assert any(
                w.field == "permission_json" and w.level == "warning"
                for w in result.warnings
            ), f"no permission_json warning for type={t}"

    def test_missing_input_schema_skill_warning(self):
        m = _make_manifest({"type": "skill"})
        result = validate_manifest(m)
        assert result.valid is True
        assert any(
            w.field == "input_schema" and w.level == "warning"
            for w in result.warnings
        )

    def test_missing_output_schema_tool_warning(self):
        m = _make_manifest({"type": "tool"})
        result = validate_manifest(m)
        assert result.valid is True
        assert any(
            w.field == "output_schema" and w.level == "warning"
            for w in result.warnings
        )

    def test_unknown_field_warning(self):
        m = _make_manifest({"type": "agent", "custom_flag": True})
        result = validate_manifest(m)
        assert result.valid is True
        assert any(
            w.field == "custom_flag" and w.level == "warning"
            for w in result.warnings
        )

    def test_metadata_and_extensions_no_warning(self):
        m = _make_manifest(
            {
                "type": "agent",
                "metadata": {"repo": "x"},
                "extensions": [],
            }
        )
        result = validate_manifest(m)
        assert not any(
            w.field in ("metadata", "extensions") for w in result.warnings
        )

    def test_x_prefix_field_no_warning(self):
        m = _make_manifest({"type": "agent", "x_custom": "data"})
        result = validate_manifest(m)
        assert not any(
            w.field == "x_custom" for w in result.warnings
        )


class TestNormalization:
    def test_normalize_name_trim_and_type_lower(self):
        m = {"name": "  Foo Bar  ", "type": "Agent"}
        result = validate_manifest(m)
        assert result.normalized_manifest["name"] == "Foo Bar"
        assert result.normalized_manifest["type"] == "agent"

    def test_missing_version_defaults(self):
        m = _make_manifest({"type": "agent"})
        del m["version"]
        result = validate_manifest(m)
        assert result.normalized_manifest["version"] == "0.1.0"


class TestImportIntegration:
    def test_import_with_manifest_errors_400(self, client: TestClient):
        m = json.dumps({"name": "bad", "type": "unknown"})
        resp = client.post(
            "/api/hub/imports/package",
            files={"file": ("test.json", m, "application/json")},
        )
        assert resp.status_code == 400
        assert "errors" in resp.json()

    def test_import_with_warnings_returns_warnings(self, client: TestClient):
        m = json.dumps({"name": "warn-asset", "type": "agent"})
        resp = client.post(
            "/api/hub/imports/package",
            files={"file": ("test.json", m, "application/json")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "warnings" in data
        assert isinstance(data["warnings"], list)


class TestVersionIntegration:
    def test_create_version_type_mismatch_400(self, client: TestClient):
        r = client.post(
            "/api/hub/items",
            json={"name": "VTest", "type": "agent"},
        )
        item_id = r.json()["id"]
        resp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "manifest_json": {"type": "skill"},
            },
        )
        assert resp.status_code == 400

    def test_create_version_version_mismatch_400(self, client: TestClient):
        r = client.post(
            "/api/hub/items",
            json={"name": "VTest2", "type": "agent"},
        )
        item_id = r.json()["id"]
        resp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "manifest_json": {"version": "2.0.0"},
            },
        )
        assert resp.status_code == 400

    def test_create_version_manifest_name_conflict_warning(
        self, client: TestClient
    ):
        r = client.post(
            "/api/hub/items",
            json={"name": "OriginalName", "type": "agent"},
        )
        item_id = r.json()["id"]
        resp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "manifest_json": {"name": "DifferentName"},
            },
        )
        assert resp.status_code == 201, resp.json()
