import io
import json

import pytest

from app.adapters.memory_storage import InMemoryStorageAdapter
from app.core.enums import HubItemStatus, HubItemType, RiskLevel
from app.core.storage import reset_storage, set_storage
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.services.export_service import ExportService
from app.services.import_service import ImportService
from app.services.openapi_import_service import OpenAPIImportService


@pytest.fixture(autouse=True)
def use_memory_storage():
    set_storage(InMemoryStorageAdapter())
    yield
    reset_storage()


class TestImportPackageSavesOriginal:
    def test_saves_original_json(self, db_session):
        content = json.dumps({
            "manifest_version": "0.1",
            "name": "test-capability",
            "type": "skill",
            "version": "0.1.0",
            "description": "test",
        }).encode("utf-8")

        from fastapi import UploadFile
        file = UploadFile(
            filename="manifest.json",
            file=io.BytesIO(content),
            headers={"content-type": "application/json"},
        )
        file.size = len(content)

        svc = ImportService(db_session)
        result = svc.import_package(file, created_by="test-ui")

        key = f"packages/{result['item_id']}/{result['version_id']}/original.json"
        from app.core.storage import get_storage
        storage = get_storage()
        assert storage.exists(key)
        stored = storage.get_bytes(key)
        assert stored == content

    def test_saves_original_zip(self, db_session):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps({
                    "manifest_version": "0.1",
                    "name": "test-zip",
                    "type": "tool",
                    "version": "0.1.0",
                    "description": "zip test",
                }),
            )
        buf.seek(0)

        from fastapi import UploadFile
        file = UploadFile(
            filename="package.zip",
            file=buf,
            headers={"content-type": "application/zip"},
        )
        file.size = buf.getbuffer().nbytes

        svc = ImportService(db_session)
        result = svc.import_package(file, created_by="test-ui")

        key = f"packages/{result['item_id']}/{result['version_id']}/original.zip"
        from app.core.storage import get_storage
        storage = get_storage()
        assert storage.exists(key)


class TestOpenAPIImportSavesOriginal:
    def test_saves_original_spec(self, db_session):
        content = json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "getTest",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }).encode("utf-8")

        svc = OpenAPIImportService(db_session)
        result = svc.import_from_spec(content, "spec.json", created_by="test-ui")

        batch_id = result["batch_id"]
        key = f"imports/openapi/{batch_id}/original.json"
        from app.core.storage import get_storage
        storage = get_storage()
        assert storage.exists(key)
        stored = storage.get_bytes(key)
        assert stored == content


class TestExportCache:
    def _make_published(self, db_session):
        item = HubItem(
            name="CacheExport",
            type=HubItemType.tool,
            status=HubItemStatus.published,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            status=HubItemStatus.published,
            risk_level=RiskLevel.low,
            manifest_json={"manifest_version": "0.1"},
            config_json={"key": "val"},
        )
        db_session.add(version)
        db_session.flush()
        item.current_version_id = version.id
        db_session.commit()
        return item, version

    def test_first_export_cache_miss_then_write(self, db_session):
        item, version = self._make_published(db_session)
        cache_key = f"exports/items/{item.id}/versions/{version.id}/capability.zip"

        from app.core.storage import get_storage
        storage = get_storage()
        assert not storage.exists(cache_key)

        svc = ExportService(db_session)
        result = svc.build_version_package(item.id, version.id)
        assert result is not None

        assert storage.exists(cache_key)

    def test_second_export_cache_hit(self, db_session):
        item, version = self._make_published(db_session)
        svc = ExportService(db_session)

        first = svc.build_version_package(item.id, version.id)
        assert first is not None
        first_buf, first_name = first
        first_data = first_buf.read()

        second = svc.build_version_package(item.id, version.id)
        assert second is not None
        second_buf, second_name = second
        second_data = second_buf.read()

        assert first_data == second_data

    def test_export_works_when_cache_get_fails(self, db_session):
        item, version = self._make_published(db_session)

        cache_key = f"exports/items/{item.id}/versions/{version.id}/capability.zip"
        from app.core.storage import get_storage
        storage = get_storage()
        storage.put_bytes(cache_key, b"corrupt")

        svc = ExportService(db_session)
        result = svc.build_version_package(item.id, version.id)
        assert result is not None


class TestStorageFailureDoesNotBlockImport:
    def test_import_succeeds_even_if_storage_unavailable(self, db_session):
        content = json.dumps({
            "manifest_version": "0.1",
            "name": "no-storage-test",
            "type": "skill",
            "version": "0.1.0",
            "description": "test",
        }).encode("utf-8")

        from fastapi import UploadFile
        file = UploadFile(
            filename="manifest.json",
            file=io.BytesIO(content),
            headers={"content-type": "application/json"},
        )
        file.size = len(content)

        class FailingStorage:
            name = "failing"

            def put_bytes(self, key, data, content_type=None):
                raise RuntimeError("simulated storage failure")

            def get_bytes(self, key):
                raise KeyError(key)

            def exists(self, key):
                return False

            def delete(self, key):
                pass

            def presign_get_url(self, key, expires_seconds=None):
                return None

        set_storage(FailingStorage())

        try:
            svc = ImportService(db_session)
            result = svc.import_package(file, created_by="test-ui")
            assert result["name"] == "no-storage-test"
            assert result["status"] == "draft"
        finally:
            reset_storage()
