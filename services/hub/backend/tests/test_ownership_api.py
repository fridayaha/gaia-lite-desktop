import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _custom_headers(actor_id: str, roles: str = "contributor") -> dict:
    return {"X-Actor-ID": actor_id, "X-Roles": roles}


def _admin_headers() -> dict:
    return {"X-Actor-ID": "admin", "X-Roles": "platform_admin"}


def _owner_headers() -> dict:
    return {"X-Actor-ID": "owner1", "X-Roles": "asset_owner"}


def _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base):
    monkeypatch.setenv("HUB_AUTH_MODE", "header")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    app.dependency_overrides[get_db] = lambda: db
    return db


def _auth_teardown(app, db, engine, Base):
    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(bind=engine)


class TestOwnershipAdmin:
    def test_admin_can_manage_any_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "owned-by-u1", "type": "tool",
                }, headers=_custom_headers("u1", "contributor"))
                assert r1.status_code == 201
                item_id = r1.json()["id"]

                r2 = c.put(f"/api/hub/items/{item_id}", json={
                    "name": "renamed-by-admin",
                }, headers=_admin_headers())
                assert r2.status_code == 200
                assert r2.json()["name"] == "renamed-by-admin"
        finally:
            _auth_teardown(app, db, engine, Base)


class TestAssetOwnerOwnership:
    def test_owner_can_update_own_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                hdr = _owner_headers()
                r1 = c.post("/api/hub/items", json={
                    "name": "my-asset", "type": "tool",
                }, headers=hdr)
                item_id = r1.json()["id"]

                r2 = c.put(f"/api/hub/items/{item_id}", json={
                    "name": "my-asset-v2",
                }, headers=hdr)
                assert r2.status_code == 200
        finally:
            _auth_teardown(app, db, engine, Base)

    def test_owner_cannot_update_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "owned-by-u2", "type": "tool",
                }, headers=_custom_headers("u2", "asset_owner"))
                item_id = r1.json()["id"]

                r2 = c.put(f"/api/hub/items/{item_id}", json={
                    "name": "hijack",
                }, headers=_owner_headers())
                assert r2.status_code == 403
        finally:
            _auth_teardown(app, db, engine, Base)


class TestContributorOwnership:
    def test_contributor_can_create_version_on_own_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                hdr = _custom_headers("contrib-a", "contributor")
                r1 = c.post("/api/hub/items", json={
                    "name": "my-contrib-item", "type": "tool",
                }, headers=hdr)
                item_id = r1.json()["id"]

                r2 = c.post(f"/api/hub/items/{item_id}/versions", json={
                    "hub_item_id": item_id, "version": "2.0.0",
                }, headers=hdr)
                assert r2.status_code == 201
        finally:
            _auth_teardown(app, db, engine, Base)

    def test_contributor_cannot_create_version_on_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "owned-by-admin", "type": "tool",
                }, headers=_admin_headers())
                item_id = r1.json()["id"]

                r2 = c.post(f"/api/hub/items/{item_id}/versions", json={
                    "hub_item_id": item_id, "version": "2.0.0",
                }, headers=_custom_headers("contrib-a", "contributor"))
                assert r2.status_code == 403
        finally:
            _auth_teardown(app, db, engine, Base)

    def test_contributor_can_submit_own_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                hdr = _custom_headers("contrib-b", "contributor")
                r1 = c.post("/api/hub/items", json={
                    "name": "submit-own", "type": "tool",
                }, headers=hdr)
                item_id = r1.json()["id"]

                r2 = c.post(f"/api/hub/items/{item_id}/submit", json={
                    "operator": "contrib-b", "reason": "ready",
                }, headers=hdr)
                assert r2.status_code == 200
        finally:
            _auth_teardown(app, db, engine, Base)

    def test_contributor_cannot_submit_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "owned-by-admin-2", "type": "tool",
                }, headers=_admin_headers())
                item_id = r1.json()["id"]

                r2 = c.post(f"/api/hub/items/{item_id}/submit", json={
                    "operator": "contrib-c", "reason": "ready",
                }, headers=_custom_headers("contrib-c", "contributor"))
                assert r2.status_code == 403
        finally:
            _auth_teardown(app, db, engine, Base)


class TestRelationOwnership:
    def test_can_create_relation_on_own_source(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                hdr = _custom_headers("contrib-d", "contributor")
                r1 = c.post("/api/hub/items", json={
                    "name": "my-source", "type": "agent",
                }, headers=hdr)
                src_id = r1.json()["id"]

                r2 = c.post("/api/hub/items", json={
                    "name": "some-target", "type": "tool",
                }, headers=_admin_headers())
                tgt_id = r2.json()["id"]

                r3 = c.post("/api/hub/relations", json={
                    "source_item_id": src_id,
                    "target_item_id": tgt_id,
                    "relation_type": "invokes",
                }, headers=hdr)
                assert r3.status_code == 201
        finally:
            _auth_teardown(app, db, engine, Base)


class TestExportOwnership:
    def test_contributor_cannot_export_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "admin-asset", "type": "tool",
                }, headers=_admin_headers())
                item_id = r1.json()["id"]

                r2 = c.get(f"/api/hub/exports/items/{item_id}",
                           headers=_custom_headers("contrib-e", "contributor"))
                assert r2.status_code == 403
        finally:
            _auth_teardown(app, db, engine, Base)


class TestPublisherApproverNoOwnership:
    def test_publisher_can_publish_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "for-publish", "type": "tool",
                }, headers=_custom_headers("contrib-f", "contributor"))
                item_id = r1.json()["id"]
                v_id = c.post(f"/api/hub/items/{item_id}/versions", json={
                    "hub_item_id": item_id, "version": "1.0.0",
                }, headers=_custom_headers("contrib-f", "contributor")).json()["id"]
                c.post(f"/api/hub/versions/{v_id}/submit-review",
                       json={"operator": "contrib-f"},
                       headers=_custom_headers("contrib-f", "contributor"))
                c.post(f"/api/hub/versions/{v_id}/approve",
                       json={"operator": "approver"},
                       headers=_custom_headers("approver-x", "security_reviewer"))
                r2 = c.post(f"/api/hub/versions/{v_id}/publish",
                            json={"operator": "publisher"},
                            headers=_custom_headers("pub-x", "publisher"))
                assert r2.status_code == 200
        finally:
            _auth_teardown(app, db, engine, Base)

    def test_approver_can_approve_others_asset(self, monkeypatch):
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
        db = _auth_header_mode(monkeypatch, app, get_db, SessionLocal, engine, Base)

        try:
            with TestClient(app) as c:
                r1 = c.post("/api/hub/items", json={
                    "name": "for-approve", "type": "tool",
                }, headers=_custom_headers("contrib-g", "contributor"))
                item_id = r1.json()["id"]
                v_id = c.post(f"/api/hub/items/{item_id}/versions", json={
                    "hub_item_id": item_id, "version": "1.0.0",
                }, headers=_custom_headers("contrib-g", "contributor")).json()["id"]
                c.post(f"/api/hub/versions/{v_id}/submit-review",
                       json={"operator": "contrib-g"},
                       headers=_custom_headers("contrib-g", "contributor"))
                r2 = c.post(f"/api/hub/versions/{v_id}/approve",
                            json={"operator": "other-approver"},
                            headers=_custom_headers("other-approver", "security_reviewer"))
                assert r2.status_code == 200
        finally:
            _auth_teardown(app, db, engine, Base)


class TestLegacyMissingOwner:
    def test_missing_owner_fail_open(self, monkeypatch):
        from app.main import app
        from app.db.session import get_db
        from sqlalchemy import StaticPool, create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base
        from app.models.hub_item import HubItem
        from app.core.enums import HubItemStatus, HubItemType, RiskLevel
        import uuid as _uuid

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        monkeypatch.setenv("HUB_AUTH_MODE", "header")
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()

        legacy_item = HubItem(
            id=_uuid.uuid4(),
            name="legacy",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            created_by=None,
        )
        db.add(legacy_item)
        db.commit()

        try:
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app) as c:
                r = c.put(f"/api/hub/items/{legacy_item.id}", json={
                    "name": "legacy-updated",
                }, headers=_custom_headers("some-user", "asset_owner"))
                assert r.status_code == 200
        finally:
            _auth_teardown(app, db, engine, Base)


class TestDevModeUnaffected:
    def test_dev_mode_items_still_work(self, client: TestClient):
        r1 = client.post("/api/hub/items", json={
            "name": "dev-item", "type": "tool",
        })
        assert r1.status_code == 201
        item_id = r1.json()["id"]

        r2 = client.post(f"/api/hub/items/{item_id}/versions", json={
            "hub_item_id": item_id, "version": "1.0.0",
        })
        assert r2.status_code == 201
