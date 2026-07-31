"""Integration tests for datasource/credential/sync/dataset HTTP routes.

Uses FastAPI TestClient with mocked services. Uses real pydantic schema
objects as return values to pass response_model validation.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import Container
from ontology.core.schemas.datasource import (
    ConnectionTestResult,
    CredentialResponse,
    DatasetGovernance,
    DataSource,
    ImpactAnalysis,
    SyncTask,
)
from ontology.main import app

_now = datetime.now(UTC)


@pytest.fixture
def container() -> Container:
    c = Container()
    c._metadata = AsyncMock()
    c._catalog = AsyncMock()
    c._dataset = AsyncMock()
    c._index = AsyncMock()
    c._pipeline = AsyncMock()
    c._engine = AsyncMock()
    return c


@pytest.fixture
def client(container) -> TestClient:
    with patch("ontology.routes.ontology.container", container):
        with patch("ontology.routes.query.container", container):
            with patch("ontology.routes.action.container", container):
                with patch("ontology.routes.datasource.container", container):
                    yield TestClient(app)


def _make_credential_response() -> CredentialResponse:
    return CredentialResponse(
        id="c1",
        api_name="erp_cred",
        credential_type="BASIC_AUTH",
        secret_data="***",
        created_at=_now,
    )


def _make_datasource(api_name: str = "erp_mysql") -> DataSource:
    return DataSource(
        id="ds1",
        api_name=api_name,
        display_name="ERP MySQL",
        description="",
        connector_type="mysql",
        connector_config={},
        credential_id=None,
        status="CONNECTED",
        gravitino_catalog_name=api_name,
        capabilities=[],
        created_at=_now,
        updated_at=_now,
    )


def _make_sync_task() -> SyncTask:
    return SyncTask(
        id="st1",
        api_name="sync_orders",
        data_source_id="ds1",
        sync_type="FULL_SYNC",
        source_config={"table": "orders"},
        target_dataset_api_name="orders_dataset",
        sync_mode="FULL",
        transaction_type="BATCH",
        allow_schema_changes=False,
        max_duration_minutes=60,
        file_filters={},
        schedule=None,
        status="DRAFT",
        pipeline_name=None,
        last_run_at=None,
        created_at=_now,
        updated_at=_now,
    )


def _make_dataset() -> DatasetGovernance:
    return DatasetGovernance(
        id="d1",
        api_name="orders_dataset",
        display_name="Orders",
        storage_location="s3://ontology-warehouse/orders",
        partition_config={},
        source_dataset_api_name=None,
        data_source_api_name=None,
        kind="MANAGED",
        is_view=False,
        created_at=_now,
        updated_at=_now,
    )


def _make_impact() -> ImpactAnalysis:
    return ImpactAnalysis(
        severity="LOW",
        action="delete",
        target_api_name="erp_mysql",
        target_type="datasource",
        impacts=[],
    )


class TestCredentialRoutes:
    def test_create_credential(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].create_credential = AsyncMock(
            return_value=_make_credential_response(),
        )

        resp = client.post(
            "/api/credentials",
            json={
                "api_name": "erp_cred",
                "credential_type": "BASIC_AUTH",
                "secret_data": {"password": "secret123"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["api_name"] == "erp_cred"

    def test_list_credentials(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].list_credentials = AsyncMock(return_value=[])

        resp = client.get("/api/credentials")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_delete_credential(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].delete_credential = AsyncMock()

        resp = client.delete("/api/credentials/erp_cred")
        assert resp.status_code == 204

    def test_delete_credential_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].delete_credential = AsyncMock(
            side_effect=NotFoundError("Credential", "ghost"),
        )
        resp = client.delete("/api/credentials/ghost")
        assert resp.status_code == 404


class TestDataSourceRoutes:
    def test_create_datasource(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].create_datasource = AsyncMock(
            return_value=_make_datasource(),
        )

        resp = client.post(
            "/api/datasources",
            json={
                "api_name": "erp_mysql",
                "display_name": "ERP MySQL",
                "connector_type": "mysql",
                "connector_config": {"host": "localhost", "port": "3306", "database": "erp"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["api_name"] == "erp_mysql"

    def test_list_datasources(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].list_datasources = AsyncMock(return_value=[])

        resp = client.get("/api/datasources")
        assert resp.status_code == 200

    def test_get_datasource_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].get_datasource = AsyncMock(
            side_effect=NotFoundError("DataSource", "ghost"),
        )
        resp = client.get("/api/datasources/ghost")
        assert resp.status_code == 404

    def test_update_datasource_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].update_datasource = AsyncMock(
            side_effect=NotFoundError("DataSource", "ghost"),
        )
        resp = client.patch("/api/datasources/ghost", json={"display_name": "New"})
        assert resp.status_code == 404

    def test_delete_datasource(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].delete_datasource = AsyncMock()

        resp = client.delete("/api/datasources/erp_mysql")
        assert resp.status_code == 204

    def test_test_connection_success(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].test_connection = AsyncMock(
            return_value=ConnectionTestResult(success=True, message="OK"),
        )

        resp = client.post("/api/datasources/erp_mysql/test-connection")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_test_connection_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].test_connection = AsyncMock(
            side_effect=NotFoundError("DataSource", "ghost"),
        )
        resp = client.post("/api/datasources/ghost/test-connection")
        assert resp.status_code == 404


class TestExploreRoutes:
    def test_explore_datasource(self, client, container):
        from ontology.core.schemas.datasource import ExploreResult

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].explore = AsyncMock(
            return_value=ExploreResult(database="dbo", tables=[]),
        )

        resp = client.post("/api/datasources/erp_mysql/explore", json={"database": "dbo"})
        assert resp.status_code == 200

    def test_sample_data(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].sample_data = AsyncMock(
            return_value=[
                {"id": 1, "name": "Alice"},
            ]
        )

        resp = client.get("/api/datasources/erp_mysql/explore/dbo/employees/sample?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestSyncTaskRoutes:
    def test_create_sync_task(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].get_datasource = AsyncMock(
            return_value=DataSource(
                id="ds1",
                api_name="erp_mysql",
                display_name="ERP",
                description="",
                connector_type="mysql",
                connector_config={},
                credential_id=None,
                status="CONNECTED",
                gravitino_catalog_name="erp_mysql",
                capabilities=[],
                created_at=_now,
                updated_at=_now,
            ),
        )
        container.service_overrides["datasource_service"].create_sync_task = AsyncMock(
            return_value=_make_sync_task(),
        )

        resp = client.post(
            "/api/datasources/erp_mysql/sync-tasks",
            json={
                "api_name": "sync_orders",
                "data_source_id": "ds1",
                "sync_type": "FULL_SYNC",
                "source_config": {"table": "orders"},
                "target_dataset_api_name": "orders_dataset",
            },
        )
        assert resp.status_code == 201

    def test_list_sync_tasks(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].list_sync_tasks = AsyncMock(return_value=[])

        resp = client.get("/api/datasources/erp_mysql/sync-tasks")
        assert resp.status_code == 200

    def test_start_sync(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].start_sync = AsyncMock(
            return_value=SyncTask(
                id="st1",
                api_name="sync_orders",
                data_source_id="ds1",
                sync_type="FULL_SYNC",
                source_config={},
                target_dataset_api_name="orders_dataset",
                sync_mode="FULL",
                transaction_type="BATCH",
                allow_schema_changes=False,
                max_duration_minutes=60,
                file_filters={},
                schedule=None,
                status="RUNNING",
                pipeline_name="p-1",
                last_run_at=_now,
                created_at=_now,
                updated_at=_now,
            ),
        )

        resp = client.post("/api/sync-tasks/sync_orders/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "RUNNING"

    def test_start_cdc_sync(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].start_cdc_sync = AsyncMock(
            return_value=SyncTask(
                id="st2",
                api_name="ordersCdc",
                data_source_id="ds1",
                sync_type="table",
                source_config={"sync_mode": "cdc"},
                target_dataset_api_name="erp_orders",
                sync_mode="incremental",
                transaction_type="append",
                status="RUNNING",
                pipeline_name="ext_cdc_erp_orders",
                last_run_at=_now,
                created_at=_now,
                updated_at=_now,
            ),
        )

        resp = client.post(
            "/api/datasources/erp_mysql/cdc-sync",
            json={
                "datasource_api_name": "erp_mysql",
                "source_table": "erp.orders",
                "target_dataset_api_name": "erp_orders",
                "cdc_config": {
                    "cdc_connector": "MySQL-CDC",
                    "hostname": "mysql.internal",
                    "port": "3306",
                    "username": "u",
                    "password": "p",
                },
                "primary_keys": ["id"],
                "task_api_name": "ordersCdc",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "RUNNING"
        assert body["pipeline_name"] == "ext_cdc_erp_orders"
        container.service_overrides["datasource_service"].start_cdc_sync.assert_awaited_once()

    def test_stop_sync(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].stop_sync = AsyncMock(
            return_value=SyncTask(
                id="st1",
                api_name="sync_orders",
                data_source_id="ds1",
                sync_type="FULL_SYNC",
                source_config={},
                target_dataset_api_name="orders_dataset",
                sync_mode="FULL",
                transaction_type="BATCH",
                allow_schema_changes=False,
                max_duration_minutes=60,
                file_filters={},
                schedule=None,
                status="STOPPED",
                pipeline_name="p-1",
                last_run_at=_now,
                created_at=_now,
                updated_at=_now,
            ),
        )

        resp = client.post("/api/sync-tasks/sync_orders/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "STOPPED"

    def test_delete_sync_task(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].delete_sync_task = AsyncMock()

        resp = client.delete("/api/sync-tasks/sync_orders")
        assert resp.status_code == 204

    def test_start_sync_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].start_sync = AsyncMock(
            side_effect=NotFoundError("SyncTask", "ghost"),
        )
        resp = client.post("/api/sync-tasks/ghost/start")
        assert resp.status_code == 404


class TestDatasetRoutes:
    def test_register_dataset(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].register_dataset = AsyncMock(
            return_value=_make_dataset(),
        )

        resp = client.post(
            "/api/datasets",
            json={
                "api_name": "orders_dataset",
                "display_name": "Orders Dataset",
            },
        )
        assert resp.status_code == 201

    def test_list_datasets(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].list_datasets = AsyncMock(return_value=[])

        resp = client.get("/api/datasets")
        assert resp.status_code == 200

    def test_get_dataset_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].get_dataset = AsyncMock(
            side_effect=NotFoundError("Dataset", "ghost"),
        )
        resp = client.get("/api/datasets/ghost")
        assert resp.status_code == 404


class TestVirtualTableRoutes:
    """B2: POST /datasources/{ds}/virtual-tables."""

    def test_register_virtual_table_success(self, client, container):

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].register_virtual_table = AsyncMock(
            return_value=DatasetGovernance(
                id="d1",
                api_name="orders",
                display_name="orders",
                storage_location="erp_mysql.dbo.orders",
                data_source_api_name="erp_mysql",
                kind="VIRTUAL",
                is_view=False,
                created_at=_now,
                updated_at=_now,
            ),
        )

        resp = client.post(
            "/api/datasources/erp_mysql/virtual-tables",
            json={"database": "dbo", "table": "orders"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "VIRTUAL"
        assert body["storage_location"] == "erp_mysql.dbo.orders"
        container.service_overrides["datasource_service"].register_virtual_table.assert_awaited_once()

    def test_register_virtual_table_with_explicit_names(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].register_virtual_table = AsyncMock(
            return_value=DatasetGovernance(
                id="d1",
                api_name="orders_virtual",
                display_name="Orders Virtual",
                storage_location="erp_mysql.dbo.orders",
                kind="VIRTUAL",
                is_view=False,
                created_at=_now,
                updated_at=_now,
            ),
        )

        resp = client.post(
            "/api/datasources/erp_mysql/virtual-tables",
            json={
                "database": "dbo",
                "table": "orders",
                "api_name": "orders_virtual",
                "display_name": "Orders Virtual",
            },
        )
        assert resp.status_code == 201
        call_kwargs = container.service_overrides["datasource_service"].register_virtual_table.await_args.kwargs
        assert call_kwargs["api_name"] == "orders_virtual"
        assert call_kwargs["display_name"] == "Orders Virtual"

    def test_register_virtual_table_unreachable_returns_422(self, client, container):
        from ontology.core.exceptions import ValidationError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].register_virtual_table = AsyncMock(
            side_effect=ValidationError("External table has no columns or is unreachable"),
        )

        resp = client.post(
            "/api/datasources/erp_mysql/virtual-tables",
            json={"database": "dbo", "table": "ghost"},
        )
        assert resp.status_code == 422

    def test_register_virtual_table_datasource_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].register_virtual_table = AsyncMock(
            side_effect=NotFoundError("DataSource", "ghost"),
        )

        resp = client.post(
            "/api/datasources/ghost/virtual-tables",
            json={"database": "dbo", "table": "orders"},
        )
        assert resp.status_code == 404


class TestImpactAnalysisRoute:
    def test_analyze_impact(self, client, container):
        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].analyze_impact = AsyncMock(
            return_value=_make_impact(),
        )

        resp = client.post(
            "/api/impact-analysis",
            json={
                "target_type": "datasource",
                "target_api_name": "erp_mysql",
                "action": "delete",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["severity"] == "LOW"

    def test_analyze_impact_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["datasource_service"] = AsyncMock()
        container.service_overrides["datasource_service"].analyze_impact = AsyncMock(
            side_effect=NotFoundError("DataSource", "ghost"),
        )
        resp = client.post(
            "/api/impact-analysis",
            json={
                "target_type": "datasource",
                "target_api_name": "ghost",
                "action": "delete",
            },
        )
        assert resp.status_code == 404
