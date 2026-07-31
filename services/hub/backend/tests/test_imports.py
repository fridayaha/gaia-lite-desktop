import io
import json
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval_record import ApprovalRecord
from app.models.scan_report import ScanReport


def _upload(client: TestClient, filename: str, content: bytes) -> dict:
    resp = client.post(
        "/api/hub/imports/package",
        files={"file": (filename, io.BytesIO(content))},
    )
    return {"status": resp.status_code, "body": resp.json()}


class TestImportJSON:
    def test_import_json_success(self, client: TestClient):
        manifest = json.dumps({"name": "JSON Agent", "type": "agent"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 201
        assert r["body"]["name"] == "JSON Agent"
        assert r["body"]["type"] == "agent"
        assert r["body"]["status"] == "draft"

    def test_import_json_missing_name(self, client: TestClient):
        manifest = json.dumps({"type": "tool"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 400

    def test_import_json_invalid_type(self, client: TestClient):
        manifest = json.dumps({"name": "Bad", "type": "invalid"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 400

    def test_import_json_source_type_upload(self, client: TestClient):
        manifest = json.dumps({"name": "Upload Tool", "type": "tool"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 201
        item_id = r["body"]["item_id"]
        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["source_type"] == "upload"

    def test_import_json_default_version(self, client: TestClient):
        manifest = json.dumps({"name": "Ver Test", "type": "skill"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 201
        assert r["body"]["version"] == "0.1.0"

    def test_import_json_duplicate_version(self, client: TestClient):
        manifest = json.dumps({"name": "Dup Ver", "type": "tool"})
        r1 = _upload(client, "manifest.json", manifest.encode())
        assert r1["status"] == 201
        r2 = _upload(client, "manifest.json", manifest.encode())
        assert r2["status"] == 409


class TestImportYAML:
    def test_import_yaml_success(self, client: TestClient):
        yaml_content = b"name: YAML MCP\ntype: mcp\nversion: 1.0.0\n"
        r = _upload(client, "manifest.yaml", yaml_content)
        assert r["status"] == 201
        assert r["body"]["type"] == "mcp"
        assert r["body"]["version"] == "1.0.0"


class TestImportZip:
    def test_import_zip_success(self, client: TestClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"name": "Zip Tool", "type": "tool"}))
        r = _upload(client, "package.zip", buf.getvalue())
        assert r["status"] == 201
        assert r["body"]["name"] == "Zip Tool"

    def test_import_zip_no_manifest(self, client: TestClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "hello")
        r = _upload(client, "package.zip", buf.getvalue())
        assert r["status"] == 400

    def test_import_zip_slip_dotdot(self, client: TestClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../etc/passwd", "bad")
            zf.writestr("manifest.json", json.dumps({"name": "Slip", "type": "agent"}))
        r = _upload(client, "package.zip", buf.getvalue())
        assert r["status"] == 400

    def test_import_zip_slip_absolute(self, client: TestClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("/root/.ssh", "bad")
            zf.writestr("manifest.json", json.dumps({"name": "Slip2", "type": "agent"}))
        r = _upload(client, "package.zip", buf.getvalue())
        assert r["status"] == 400

    def test_import_zip_slip_windows_abs(self, client: TestClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("C:\\Windows\\evil.exe", "bad")
            zf.writestr("manifest.json", json.dumps({"name": "Slip3", "type": "agent"}))
        r = _upload(client, "package.zip", buf.getvalue())
        assert r["status"] == 400


class TestImportNoAutoActions:
    def test_import_no_scan_report(self, client: TestClient, db_session):
        manifest = json.dumps({"name": "No Scan", "type": "tool"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 201
        count = db_session.query(ScanReport).count()
        assert count == 0

    def test_import_no_approval_record(self, client: TestClient, db_session):
        manifest = json.dumps({"name": "No Approval", "type": "tool"})
        r = _upload(client, "manifest.json", manifest.encode())
        assert r["status"] == 201
        count = db_session.query(ApprovalRecord).count()
        assert count == 0


class TestImportCaseInsensitive:
    def test_name_case_insensitive_dedup(self, client: TestClient):
        m1 = json.dumps({"name": "My TOOL", "type": "tool"})
        r1 = _upload(client, "manifest.json", m1.encode())
        assert r1["status"] == 201
        m2 = json.dumps({"name": "my tool", "type": "tool", "version": "2.0.0"})
        r2 = _upload(client, "manifest.json", m2.encode())
        assert r2["status"] == 201
        assert r2["body"]["item_id"] == r1["body"]["item_id"]
        assert r2["body"]["version"] == "2.0.0"
