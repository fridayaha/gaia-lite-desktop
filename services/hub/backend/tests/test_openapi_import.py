import io
import os

from fastapi.testclient import TestClient


def _openapi_fixture(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(path) as f:
        return f.read()


def _upload(client: TestClient, content: str, filename: str = "spec.yaml"):
    return client.post(
        "/api/hub/imports/openapi",
        files={"file": (filename, io.BytesIO(content.encode()))},
    )


class TestBasicImport:
    def test_import_minimal_spec_creates_one_tool(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Import1", "version": "0.1.0"},
            "servers": [{"url": "https://petstore.example.com/v1"}],
            "paths": {
                "/pets": {
                    "get": {
                        "operationId": "import1ListPets",
                        "summary": "List all pets",
                        "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        data = resp.json()
        assert data["tools_created"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"].startswith("import1ListPets")
        assert data["items"][0]["type"] == "tool"

    def test_operation_id_becomes_name(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Import2", "version": "0.1.0"},
            "paths": {"/pets": {"get": {"operationId": "import2Op", "responses": {"200": {"description": "ok"}}}}},
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        assert resp.json()["items"][0]["name"].startswith("import2Op")

    def test_fallback_name_without_operation_id(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Import3", "version": "0.1.0"},
            "paths": {"/import3data": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        assert resp.json()["items"][0]["name"].startswith("get_import3data")

    def test_name_conflict_appends_suffix(self, client: TestClient):
        op_id = "import4Conflict"
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Import4", "version": "0.1.0"},
            "paths": {
                "/a": {"get": {"operationId": op_id, "responses": {"200": {"description": "ok"}}}},
                "/b": {"get": {"operationId": op_id, "responses": {"200": {"description": "ok"}}}},
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        data = resp.json()
        names = [i["name"] for i in data["items"]]
        assert len(names) == 2
        assert all(n.startswith(op_id) for n in names)

    def test_created_tool_is_draft(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "draft"

    def test_generated_tool_has_runtime_compatibility(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = versions[0]
        assert v["runtime_compatibility"] is not None
        assert v["runtime_compatibility"]["source"] == "openapi_import"


class TestSchemaConversion:
    def test_parameters_to_input_schema(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        inp = versions[0]["input_schema"]
        assert inp is not None
        assert "limit" in inp.get("properties", {})

    def test_request_body_to_input_schema(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "0.1.0"},
            "servers": [{"url": "https://example.com"}],
            "paths": {
                "/pets": {
                    "post": {
                        "operationId": "createPet",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                                }
                            }
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        inp = versions[0]["input_schema"]
        assert "body" in inp.get("properties", {})

    def test_responses_to_output_schema(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        outp = versions[0]["output_schema"]
        assert outp is not None
        assert outp.get("type") == "array"

    def test_servers_path_method_to_invocation(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        manifest = versions[0]["manifest_json"]
        invocation = manifest.get("invocation", {})
        assert invocation.get("method") == "GET"
        assert invocation["endpoint"] == "https://petstore.example.com/v1/pets"


class TestSecurityConversion:
    def test_security_schemes_to_permission_json(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Secure", "version": "0.1.0"},
            "servers": [{"url": "https://api.example.com"}],
            "components": {
                "securitySchemes": {"api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
            },
            "security": [{"api_key": []}],
            "paths": {
                "/secure": {
                    "get": {
                        "operationId": "secureOp",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        perm = versions[0]["permission_json"]
        assert perm is not None
        assert perm.get("auth_required") is True
        assert "api_key" in perm.get("security_schemes", [])
        assert "api.example.com" in perm.get("allowed_domains", [])

    def test_no_security_generates_minimal_permission(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        perm = versions[0]["permission_json"]
        assert perm is not None
        assert perm.get("auth_required") is False


class TestRefResolution:
    def test_local_ref_resolved_in_responses(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "RefTest", "version": "0.1.0"},
            "servers": [{"url": "https://example.com"}],
            "paths": {
                "/pets/{petId}": {
                    "get": {
                        "operationId": "getPet",
                        "responses": {
                            "200": {
                                "description": "A pet",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Pet"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "Pet": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    }
                }
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        outp = versions[0]["output_schema"]
        assert outp is not None
        assert outp.get("properties", {}).get("id", {}).get("type") == "integer"


class TestServerFallback:
    def test_no_servers_still_imports_with_warning(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "NoServer", "version": "0.1.0"},
            "paths": {
                "/health": {
                    "get": {
                        "operationId": "healthCheck",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["warnings"]) >= 1
        assert any("no server URL" in w["detail"] for w in data["warnings"])


class TestErrorHandling:
    def test_non_openapi_file_returns_400(self, client: TestClient):
        resp = _upload(client, '{"not": "openapi"}', "spec.json")
        assert resp.status_code == 400

    def test_empty_paths_returns_400(self, client: TestClient):
        spec = {"openapi": "3.0.0", "info": {"title": "Empty"}, "paths": {}}
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 400

    def test_invalid_yaml_returns_400(self, client: TestClient):
        resp = _upload(client, ":::invalid:::yaml:::", "spec.yaml")
        assert resp.status_code == 400

    def test_invalid_json_returns_400(self, client: TestClient):
        resp = _upload(client, "not json", "spec.json")
        assert resp.status_code == 400

    def test_partial_success_one_op_fails_other_succeeds(self, client: TestClient):
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Partial", "version": "0.1.0"},
            "servers": [{"url": "https://example.com"}],
            "paths": {
                "/pets": {
                    "get": {
                        "operationId": "okOp",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
            },
        }
        resp = _upload(client, str(spec).replace("'", '"'), "spec.json")
        assert resp.status_code == 201
        assert resp.json()["tools_created"] == 1


class TestScanAfterImport:
    def test_submit_review_still_triggers_scan(self, client: TestClient):
        content = _openapi_fixture("minimal_openapi.json")
        resp = _upload(client, content, "spec.json")
        assert resp.status_code == 201
        item_id = resp.json()["items"][0]["item_id"]

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        version_id = versions[0]["id"]

        submit_resp = client.post(
            f"/api/hub/versions/{version_id}/submit-review",
            json={"operator": "dev"},
        )
        assert submit_resp.status_code == 200

        scan_resp = client.get(f"/api/hub/versions/{version_id}/scan-report")
        assert scan_resp.status_code == 200
        report = scan_resp.json()
        assert report["risk_level"] in ("low", "medium")
