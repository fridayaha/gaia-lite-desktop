import json
import os
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.operator import resolve_effective_created_by

MOCK_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "responses": {
                    "200": {"description": "ok"}
                }
            }
        }
    },
}


def _admin_headers() -> dict:
    return {"X-Actor-ID": "admin", "X-Roles": "platform_admin"}


def _custom_headers(actor_id: str) -> dict:
    return {"X-Actor-ID": actor_id, "X-Roles": "contributor"}


class TestResolveEffectiveCreatedBy:
    def test_actor_id_priority(self):
        ctx = AuthContext(actor_id="user-from-header", roles=["contributor"])
        result = resolve_effective_created_by(ctx, "body-user")
        assert result == "user-from-header"

    def test_fallback_to_body(self):
        ctx = AuthContext(actor_id=None, roles=["contributor"])
        result = resolve_effective_created_by(ctx, "body-user")
        assert result == "body-user"

    def test_unknown_when_both_empty(self):
        ctx = AuthContext(actor_id=None)
        result = resolve_effective_created_by(ctx, None)
        assert result == "unknown"

    def test_actor_id_whitespace_fallback(self):
        ctx = AuthContext(actor_id="  ")
        result = resolve_effective_created_by(ctx, "body-user")
        assert result == "body-user"

    def test_body_none_uses_actor_id(self):
        ctx = AuthContext(actor_id="header-user")
        result = resolve_effective_created_by(ctx, None)
        assert result == "header-user"


class TestCreateItemCreatedBy:
    def test_dev_mode_creates_as_dev_admin(self, client: TestClient):
        resp = client.post("/api/hub/items", json={
            "name": "dev-item", "type": "tool",
        })
        assert resp.status_code == 201
        assert resp.json()["created_by"] == "dev-admin"

    def test_dev_mode_overrides_body_created_by(self, client: TestClient):
        resp = client.post("/api/hub/items", json={
            "name": "override-item", "type": "tool",
            "created_by": "fake-user",
        })
        assert resp.status_code == 201
        assert resp.json()["created_by"] == "dev-admin"

    def test_header_mode_uses_actor_id(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db_session = SessionLocal()

        try:
            app.dependency_overrides[get_db] = lambda: db_session
            with TestClient(app) as c:
                resp = c.post("/api/hub/items", json={
                    "name": "header-item", "type": "tool",
                }, headers=_custom_headers("user-abc"))
                assert resp.status_code == 201
                assert resp.json()["created_by"] == "user-abc"
        finally:
            app.dependency_overrides.clear()
            db_session.close()
            Base.metadata.drop_all(bind=engine)

    def test_header_mode_priority_over_body(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db_session = SessionLocal()

        try:
            app.dependency_overrides[get_db] = lambda: db_session
            with TestClient(app) as c:
                resp = c.post("/api/hub/items", json={
                    "name": "prio-item", "type": "tool",
                    "created_by": "fake-user",
                }, headers=_custom_headers("real-user"))
                assert resp.status_code == 201
                assert resp.json()["created_by"] == "real-user"
        finally:
            app.dependency_overrides.clear()
            db_session.close()
            Base.metadata.drop_all(bind=engine)


class TestCreateVersionCreatedBy:
    def test_dev_mode_versions_as_dev_admin(self, client: TestClient):
        item_resp = client.post("/api/hub/items", json={
            "name": "v-item", "type": "tool",
        })
        item_id = item_resp.json()["id"]

        resp = client.post(f"/api/hub/items/{item_id}/versions", json={
            "hub_item_id": item_id, "version": "1.0.0",
        })
        assert resp.status_code == 201
        assert resp.json()["created_by"] == "dev-admin"

    def test_header_mode_version_uses_actor_id(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db_session = SessionLocal()

        try:
            app.dependency_overrides[get_db] = lambda: db_session
            with TestClient(app) as c:
                hdr = _custom_headers("creator-x")
                item_resp = c.post("/api/hub/items", json={
                    "name": "v2-item", "type": "tool",
                }, headers=hdr)
                item_id = item_resp.json()["id"]

                resp = c.post(f"/api/hub/items/{item_id}/versions", json={
                    "hub_item_id": item_id, "version": "1.0.0",
                    "created_by": "fake-versioner",
                }, headers=hdr)
                assert resp.status_code == 201
                assert resp.json()["created_by"] == "creator-x"
        finally:
            app.dependency_overrides.clear()
            db_session.close()
            Base.metadata.drop_all(bind=engine)


class TestImportCreatedBy:
    def test_package_import_writes_created_by(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db_session = SessionLocal()

        manifest = {
            "name": "pkg-tool",
            "type": "tool",
            "version": "1.0.0",
            "input_schema": {"type": "object", "properties": {}},
        }
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
        buf.seek(0)

        try:
            app.dependency_overrides[get_db] = lambda: db_session
            with TestClient(app) as c:
                resp = c.post(
                    "/api/hub/imports/package",
                    files={"file": ("bundle.zip", buf, "application/zip")},
                    headers=_custom_headers("importer-1"),
                )
                assert resp.status_code == 201
                data = resp.json()
                item_id = data["item_id"]
                version_id = data["version_id"]

                item_resp = c.get(f"/api/hub/items/{item_id}",
                                  headers=_custom_headers("importer-1"))
                assert item_resp.status_code == 200
                assert item_resp.json()["created_by"] == "importer-1"

                versions_resp = c.get(f"/api/hub/items/{item_id}/versions",
                                      headers=_custom_headers("importer-1"))
                v = next(v for v in versions_resp.json() if v["id"] == version_id)
                assert v["created_by"] == "importer-1"
        finally:
            app.dependency_overrides.clear()
            db_session.close()
            Base.metadata.drop_all(bind=engine)

    def test_openapi_import_writes_created_by(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db_session = SessionLocal()

        spec_bytes = json.dumps(MOCK_OPENAPI_SPEC).encode("utf-8")

        try:
            app.dependency_overrides[get_db] = lambda: db_session
            with TestClient(app) as c:
                resp = c.post(
                    "/api/hub/imports/openapi",
                    files={"file": ("spec.json", spec_bytes, "application/json")},
                    headers=_custom_headers("openapi-importer"),
                )
                assert resp.status_code == 201
                data = resp.json()
                items = data.get("items", [])
                assert len(items) >= 1

                for tool_info in items:
                    item_id = tool_info["item_id"]
                    item_resp = c.get(f"/api/hub/items/{item_id}",
                                      headers=_custom_headers("openapi-importer"))
                    assert item_resp.json()["created_by"] == "openapi-importer"

                    versions_resp = c.get(
                        f"/api/hub/items/{item_id}/versions",
                        headers=_custom_headers("openapi-importer"),
                    )
                    assert len(versions_resp.json()) >= 1
                    v = versions_resp.json()[0]
                    assert v["created_by"] == "openapi-importer"
        finally:
            app.dependency_overrides.clear()
            db_session.close()
            Base.metadata.drop_all(bind=engine)
