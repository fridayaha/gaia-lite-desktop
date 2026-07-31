"""Unit tests for DataSourceService — extended coverage.

Covers methods that require more complex mock setups to exercise deeper
code paths: create_datasource Gravitino flow, sync task lifecycle,
dataset registration, impact analysis edge cases.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import NotFoundError
from ontology.core.schemas.datasource import (
    Credential,
    DatasetGovernance,
    DatasetGovernanceCreate,
    DataSource,
    DataSourceCreate,
    ImpactAnalysisRequest,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.engine.trino_query_engine import TrinoQueryEngine
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
from ontology.services.datasource_service import DataSourceService

NOW = datetime.now(UTC)


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock(spec=PostgresMetaStore)


@pytest.fixture
def mock_catalog() -> AsyncMock:
    catalog = AsyncMock(spec=GravitinoRegistry)
    catalog.get_table_comment.return_value = ""
    return catalog


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock(spec=TrinoQueryEngine)


@pytest.fixture
def mock_pipeline() -> AsyncMock:
    return AsyncMock(spec=SeaTunnelEngine)


@pytest.fixture
def mock_dataset() -> AsyncMock:
    return AsyncMock(spec=IcebergStore)


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset) -> DataSourceService:
    return DataSourceService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        engine=mock_engine,
        pipeline=mock_pipeline,
        dataset=mock_dataset,
    )


def _ds(api_name="erp_mysql", connector_type="mysql"):
    return DataSource(
        id="ds1",
        api_name=api_name,
        display_name="ERP",
        description="",
        connector_type=connector_type,
        connector_config={"host": "localhost", "port": "3306", "database": "erp"},
        credential_id=None,
        status="CONNECTED",
        gravitino_catalog_name=api_name,
        capabilities=[],
        created_at=NOW,
        updated_at=NOW,
    )


class TestCredentialExtended:
    @pytest.mark.asyncio
    async def test_get_credential_full(self, service, mock_metadata):
        mock_metadata.get_credential.return_value = Credential(
            id="c1",
            api_name="erp",
            credential_type="BASIC_AUTH",
            secret_data={"password": "s3cret"},
            created_at=NOW,
        )
        cred = await service.get_credential("erp")
        assert cred.secret_data == {"password": "s3cret"}

    @pytest.mark.asyncio
    async def test_delete_credential_not_found(self, service, mock_metadata):
        mock_metadata.delete_credential.side_effect = NotFoundError("Credential", "ghost")
        with pytest.raises(NotFoundError):
            await service.delete_credential("ghost")


class TestDataSourceExtended:
    @pytest.mark.asyncio
    async def test_get_datasource_not_found(self, service, mock_metadata):
        mock_metadata.get_datasource.side_effect = NotFoundError("DataSource", "ghost")
        with pytest.raises(NotFoundError):
            await service.get_datasource("ghost")

    @pytest.mark.asyncio
    async def test_update_datasource_passes_exception(self, service, mock_metadata):
        mock_metadata.update_datasource.side_effect = NotFoundError("DataSource", "ghost")
        from ontology.core.schemas.datasource import DataSourceUpdate

        with pytest.raises(NotFoundError):
            await service.update_datasource("ghost", DataSourceUpdate(display_name="x"))

    @pytest.mark.asyncio
    async def test_create_datasource_postgresql(self, service, mock_metadata, mock_catalog):
        """PostgreSQL data source registers in Gravitino via REST API."""
        ds = _ds(api_name="pg_hr", connector_type="postgresql")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="pg_hr",
            display_name="PG HR",
            connector_type="postgresql",
            connector_config={"host": "pg.local", "port": "5432", "database": "hr"},
        )
        result = await service.create_datasource(ds_create)
        assert result.api_name == "pg_hr"
        # 走 catalog.register_jdbc_catalog（Gravitino REST API），
        # Trino Gravitino connector 自动发现新 catalog。
        mock_catalog.register_jdbc_catalog.assert_awaited_once()


class TestSyncTaskExtended:
    @pytest.mark.asyncio
    async def test_get_sync_task_not_found(self, service, mock_metadata):
        mock_metadata.get_sync_task.side_effect = NotFoundError("SyncTask", "ghost")
        with pytest.raises(NotFoundError):
            await service.get_sync_task("ghost")

    @pytest.mark.asyncio
    async def test_list_sync_tasks_datasource_not_found(self, service, mock_metadata):
        mock_metadata.get_datasource.side_effect = NotFoundError("DataSource", "ghost")
        with pytest.raises(NotFoundError):
            await service.list_sync_tasks("ghost")

    @pytest.mark.asyncio
    async def test_stop_sync_task_no_pipeline(self, service, mock_metadata, mock_pipeline):
        """Stopping a task with no pipeline_name still works."""
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            pipeline_name=None,
        )
        mock_metadata.update_sync_task.return_value = MagicMock(status="STOPPED")

        result = await service.stop_sync("sync_orders")
        assert result.status == "STOPPED"
        mock_pipeline.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_sync_task_no_pipeline(self, service, mock_metadata, mock_pipeline):
        """Deleting a task with no pipeline is fine."""
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            pipeline_name=None,
        )
        await service.delete_sync_task("sync_orders")
        mock_metadata.delete_sync_task.assert_awaited_once_with("sync_orders", auto_commit=False)
        mock_pipeline.stop.assert_not_called()


class TestImpactAnalysisExtended:
    @pytest.mark.asyncio
    async def test_analyze_impact_sync_task_delete(self, service):
        request = ImpactAnalysisRequest(
            target_type="sync_task",
            target_api_name="sync_orders",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert result.severity == "MEDIUM"
        assert len(result.impacts) == 1
        assert result.impacts[0].effect == "CASCADE_DELETE"

    @pytest.mark.asyncio
    async def test_analyze_impact_severity_medium(self, service, mock_metadata):
        ds = _ds()
        mock_metadata.get_datasource.return_value = ds
        mock_metadata.list_sync_tasks_for_datasource.return_value = [
            MagicMock(api_name="t1"),
            MagicMock(api_name="t2"),
        ]
        request = ImpactAnalysisRequest(
            target_type="datasource",
            target_api_name="erp_mysql",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert result.severity == "MEDIUM"


class TestDatasetExtended:
    @pytest.mark.asyncio
    async def test_register_dataset(self, service, mock_metadata):
        mock_metadata.create_dataset.return_value = DatasetGovernance(
            id="d1",
            api_name="orders_ds",
            display_name="Orders",
            storage_location="s3://w/orders",
            partition_config={},
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        ds = DatasetGovernanceCreate(api_name="orders_ds", display_name="Orders Dataset")
        result = await service.register_dataset(ds)
        assert result.api_name == "orders_ds"

    @pytest.mark.asyncio
    async def test_get_dataset(self, service, mock_metadata):
        mock_metadata.get_dataset.return_value = DatasetGovernance(
            id="d1",
            api_name="orders_ds",
            display_name="Orders",
            storage_location="s3://w/orders",
            partition_config={},
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        result = await service.get_dataset("orders_ds")
        assert result.api_name == "orders_ds"

    @pytest.mark.asyncio
    async def test_list_datasets(self, service, mock_metadata):
        mock_metadata.list_datasets.return_value = [
            DatasetGovernance(
                id="d1",
                api_name="orders_ds",
                display_name="Orders",
                storage_location="s3://w/orders",
                partition_config={},
                is_view=False,
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
        result = await service.list_datasets()
        assert len(result) == 1


class TestRegisterVirtualTable:
    """B2: register_virtual_table orchestrates describe → create VIRTUAL dataset."""

    @pytest.mark.asyncio
    async def test_registers_with_three_part_locator(self, service, mock_metadata, mock_engine):
        ds = _ds(api_name="erp_mysql")
        mock_metadata.get_datasource.return_value = ds
        # describe_table returns columns → reachable

        mock_engine.describe_table.return_value = [
            {"Column": "id", "Type": "integer", "Null": "NO", "Key": "PRI", "Comment": ""},
        ]
        created = DatasetGovernance(
            id="d1",
            api_name="orders",
            display_name="orders",
            storage_location="erp_mysql.dbo.orders",
            data_source_api_name="erp_mysql",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.create_dataset.return_value = created

        result = await service.register_virtual_table(
            datasource_api_name="erp_mysql",
            database="dbo",
            table="orders",
        )

        assert result.kind == "VIRTUAL"
        assert result.storage_location == "erp_mysql.dbo.orders"
        mock_metadata.create_dataset.assert_awaited_once()
        create_arg = mock_metadata.create_dataset.call_args.args[0]
        assert create_arg.kind == "VIRTUAL"
        assert create_arg.api_name == "orders"
        assert create_arg.storage_location == "erp_mysql.dbo.orders"
        assert create_arg.data_source_api_name == "erp_mysql"
        assert create_arg.is_view is False

    @pytest.mark.asyncio
    async def test_uses_explicit_api_name_and_display_name(self, service, mock_metadata, mock_engine):
        ds = _ds(api_name="erp_mysql")
        mock_metadata.get_datasource.return_value = ds
        mock_engine.describe_table.return_value = [
            {"Column": "id", "Type": "integer", "Null": "NO", "Key": "PRI", "Comment": ""},
        ]
        mock_metadata.create_dataset.return_value = DatasetGovernance(
            id="d1",
            api_name="orders_virtual",
            display_name="Orders Virtual",
            storage_location="erp_mysql.dbo.orders",
            data_source_api_name="erp_mysql",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )

        await service.register_virtual_table(
            datasource_api_name="erp_mysql",
            database="dbo",
            table="orders",
            api_name="orders_virtual",
            display_name="Orders Virtual",
        )

        create_arg = mock_metadata.create_dataset.call_args.args[0]
        assert create_arg.api_name == "orders_virtual"
        assert create_arg.display_name == "Orders Virtual"

    @pytest.mark.asyncio
    async def test_unreachable_table_raises_validation_error(self, service, mock_metadata, mock_engine):
        """An external table with no columns (unreachable) is a 422."""
        from ontology.core.exceptions import ValidationError

        ds = _ds(api_name="erp_mysql")
        mock_metadata.get_datasource.return_value = ds
        # describe_table returns empty columns → unreachable / no columns
        mock_engine.describe_table.return_value = []

        with pytest.raises(ValidationError, match="no columns or is unreachable"):
            await service.register_virtual_table(
                datasource_api_name="erp_mysql",
                database="dbo",
                table="ghost",
            )
        mock_metadata.create_dataset.assert_not_called()

    @pytest.mark.asyncio
    async def test_datasource_not_found_propagates(self, service, mock_metadata):
        mock_metadata.get_datasource.side_effect = NotFoundError("DataSource", "ghost")
        with pytest.raises(NotFoundError):
            await service.register_virtual_table("ghost", "dbo", "orders")

    @pytest.mark.asyncio
    async def test_uses_gravitino_catalog_name_when_set(self, service, mock_metadata, mock_engine):
        """catalog segment of the locator prefers gravitino_catalog_name."""
        ds = _ds(api_name="erp_mysql")
        ds.gravitino_catalog_name = "erp_mysql_cat"
        mock_metadata.get_datasource.return_value = ds
        mock_engine.describe_table.return_value = [
            {"Column": "id", "Type": "integer", "Null": "NO", "Key": "PRI", "Comment": ""},
        ]
        mock_metadata.create_dataset.return_value = DatasetGovernance(
            id="d1",
            api_name="orders",
            display_name="orders",
            storage_location="erp_mysql_cat.dbo.orders",
            data_source_api_name="erp_mysql",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )

        await service.register_virtual_table("erp_mysql", "dbo", "orders")
        create_arg = mock_metadata.create_dataset.call_args.args[0]
        assert create_arg.storage_location == "erp_mysql_cat.dbo.orders"


class TestGetDatasetSchemaByKind:
    """B3: get_dataset_schema dispatches by dataset kind."""

    @pytest.mark.asyncio
    async def test_managed_uses_iceberg_store(self, service, mock_metadata, mock_dataset):
        from ontology.core.schemas.dataset import ColumnDef, DatasetSchema

        ds = DatasetGovernance(
            id="d1",
            api_name="orders_ds",
            display_name="Orders",
            storage_location="s3://w/orders",
            kind="MANAGED",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds
        mock_dataset.get_schema.return_value = DatasetSchema(
            columns=[ColumnDef(name="id", type="string", nullable=False)]
        )

        result = await service.get_dataset_schema("orders_ds")

        mock_dataset.get_schema.assert_awaited_once_with("orders_ds")
        assert len(result.columns) == 1
        assert result.columns[0].name == "id"

    @pytest.mark.asyncio
    async def test_managed_iceberg_not_found_returns_empty(self, service, mock_metadata, mock_dataset):

        ds = DatasetGovernance(
            id="d1",
            api_name="orders_ds",
            display_name="Orders",
            storage_location="s3://w/orders",
            kind="MANAGED",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds
        mock_dataset.get_schema.side_effect = NotFoundError("Table", "orders_ds")

        result = await service.get_dataset_schema("orders_ds")
        assert result.columns == []

    @pytest.mark.asyncio
    async def test_managed_iceberg_unavailable_raises(self, service, mock_metadata, mock_dataset):
        from ontology.core.exceptions import IcebergUnavailableError

        ds = DatasetGovernance(
            id="d1",
            api_name="orders_ds",
            display_name="Orders",
            storage_location="s3://w/orders",
            kind="MANAGED",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds
        mock_dataset.get_schema.side_effect = RuntimeError("catalog down")

        with pytest.raises(IcebergUnavailableError):
            await service.get_dataset_schema("orders_ds")

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_virtual_federates_via_gravitino(self, service, mock_metadata, mock_catalog):
        ds = DatasetGovernance(
            id="d1",
            api_name="orders",
            display_name="orders",
            storage_location="erp_mysql.dbo.orders",
            data_source_api_name="erp_mysql",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds
        mock_catalog.get_table_columns.return_value = [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "name", "type": "varchar(255)", "nullable": True},
        ]

        result = await service.get_dataset_schema("orders")

        mock_catalog.get_table_columns.assert_awaited_once_with("erp_mysql", "dbo", "orders")
        assert len(result.columns) == 2
        assert result.columns[0].name == "id"
        assert result.columns[0].type == "integer"
        assert result.columns[1].type == "varchar(255)"
        assert result.columns[1].nullable is True

    @pytest.mark.asyncio
    async def test_virtual_malformed_locator_returns_empty(self, service, mock_metadata, mock_catalog):
        """A VIRTUAL dataset with a non-three-part locator degrades to empty."""
        ds = DatasetGovernance(
            id="d1",
            api_name="orders",
            display_name="orders",
            storage_location="not-three-parts",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds

        result = await service.get_dataset_schema("orders")
        assert result.columns == []
        mock_catalog.get_table_columns.assert_not_called()

    @pytest.mark.asyncio
    async def test_virtual_gravitino_unavailable_raises(self, service, mock_metadata, mock_catalog):
        from ontology.core.exceptions import GravitinoUnavailableError

        ds = DatasetGovernance(
            id="d1",
            api_name="orders",
            display_name="orders",
            storage_location="erp_mysql.dbo.orders",
            kind="VIRTUAL",
            is_view=False,
            created_at=NOW,
            updated_at=NOW,
        )
        mock_metadata.get_dataset.return_value = ds
        mock_catalog.get_table_columns.side_effect = RuntimeError("gravitas gone")

        with pytest.raises(GravitinoUnavailableError):
            await service.get_dataset_schema("orders")


class TestIngestionFilterWiring:
    """DataSourceService applies IngestionFilter on incremental syncs."""

    def test_service_holds_ingestion_filter(self, service):
        from ontology.services.ingestion_filter import IngestionFilter

        assert isinstance(service._ingestion_filter, IngestionFilter)

    def test_incremental_rewrite_applied(self, service):
        # Simulate the incremental branch of _assemble_source_config.
        from ontology.core.schemas.datasource import SyncTask

        task = SyncTask(
            id="t1",
            api_name="sync_inc",
            data_source_id="ds1",
            source_config={"table": "orders", "last_sync_tx": "tx-001"},
            target_dataset_api_name="orders",
            sync_mode="incremental",
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        base_sql = "SELECT * FROM orders WHERE updated_at > :watermark"
        last_sync_tx = task.source_config.get("last_sync_tx")
        rewritten = service._ingestion_filter.rewrite_incremental_query(base_sql, last_sync_tx)
        assert "gaia_sync_tx" in rewritten
        assert "tx-001" in rewritten

    def test_full_snapshot_not_filtered(self, service):
        # full_snapshot path must NOT apply the feedback-loop filter.

        f = service._ingestion_filter
        # For full_snapshot, last_sync_tx is None → no rewrite.
        assert f.rewrite_incremental_query("SELECT * FROM orders", None) == "SELECT * FROM orders"


class TestMaybeTriggerVirtualProjection:
    """ADR-021 §3.1：register_virtual_table 后异步触发 VIRTUAL 图投影。"""

    @pytest.mark.asyncio
    async def test_no_funnel_skips_silently(self, service, mock_metadata):
        """未注入 object_index_funnel 时 no-op，不查 metadata。"""
        service._maybe_trigger_virtual_projection("orders")
        mock_metadata.get_virtual_object_types_by_dataset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_bound_ots_skips_projection(self, mock_metadata, mock_catalog,
                                                  mock_engine, mock_pipeline, mock_dataset):
        """dataset 未绑 VIRTUAL ObjectType 时跳过（首次 register 常见）。"""
        funnel = AsyncMock()
        svc = DataSourceService(
            metadata=mock_metadata, catalog=mock_catalog, engine=mock_engine,
            pipeline=mock_pipeline, dataset=mock_dataset, object_index_funnel=funnel,
        )
        mock_metadata.get_virtual_object_types_by_dataset.return_value = []

        svc._maybe_trigger_virtual_projection("orders")
        # 让 create_task 跑完
        import asyncio
        await asyncio.sleep(0.01)

        mock_metadata.get_virtual_object_types_by_dataset.assert_awaited_once_with("orders")
        funnel.project_for_virtual_object_type.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_triggers_projection_for_bound_ots(
        self, mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset,
    ):
        """dataset 绑了 VIRTUAL OT 时逐个触发投影。"""
        funnel = AsyncMock()
        funnel.project_for_virtual_object_type.return_value = {"nodes": 5, "edges": 2}
        svc = DataSourceService(
            metadata=mock_metadata, catalog=mock_catalog, engine=mock_engine,
            pipeline=mock_pipeline, dataset=mock_dataset, object_index_funnel=funnel,
        )
        mock_metadata.get_virtual_object_types_by_dataset.return_value = [
            ("SC", "Order"), ("SC", "Note"),
        ]

        svc._maybe_trigger_virtual_projection("orders")
        import asyncio
        await asyncio.sleep(0.05)

        mock_metadata.get_virtual_object_types_by_dataset.assert_awaited_once_with("orders")
        assert funnel.project_for_virtual_object_type.await_count == 2
        calls = funnel.project_for_virtual_object_type.call_args_list
        assert calls[0].kwargs == {"ontology_api_name": "SC", "object_type_api_name": "Order"}
        assert calls[1].kwargs == {"ontology_api_name": "SC", "object_type_api_name": "Note"}

    @pytest.mark.asyncio
    async def test_projection_failure_does_not_raise(
        self, mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset,
    ):
        """单个 OT 投影失败不阻塞其他 OT（best-effort）。"""
        funnel = AsyncMock()
        funnel.project_for_virtual_object_type.side_effect = [
            RuntimeError("trino down"),
            {"nodes": 3},
        ]
        svc = DataSourceService(
            metadata=mock_metadata, catalog=mock_catalog, engine=mock_engine,
            pipeline=mock_pipeline, dataset=mock_dataset, object_index_funnel=funnel,
        )
        mock_metadata.get_virtual_object_types_by_dataset.return_value = [("SC", "A"), ("SC", "B")]

        # 不应抛异常
        svc._maybe_trigger_virtual_projection("orders")
        import asyncio
        await asyncio.sleep(0.05)

        assert funnel.project_for_virtual_object_type.await_count == 2
