"""Extended PostgresMetaStore tests — Datasource, Credential, SyncTask, Dataset CRUD.

Uses real in-memory SQLite async session.
"""

from datetime import UTC, datetime

import pytest

from ontology.core.exceptions import NotFoundError
from ontology.core.schemas.datasource import (
    CredentialCreate,
    DatasetGovernanceCreate,
    DataSourceCreate,
    SyncTaskCreate,
)
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

NOW = datetime.now(UTC)


@pytest.fixture
async def store(db_session):
    return PostgresMetaStore(db_session)


class TestCredentialCRUD:
    async def test_create_credential(self, store):
        cred = await store.create_credential(
            CredentialCreate(
                api_name="erp_cred",
                credential_type="BASIC_AUTH",
                secret_data={"password": "s3cret"},
            )
        )
        assert cred.id != ""
        assert cred.api_name == "erp_cred"
        assert cred.secret_data == {"password": "s3cret"}

    async def test_get_credential(self, store):
        await store.create_credential(
            CredentialCreate(
                api_name="erp_cred",
                credential_type="BASIC_AUTH",
                secret_data={"password": "s3cret"},
            )
        )
        cred = await store.get_credential("erp_cred")
        assert cred.api_name == "erp_cred"

    async def test_get_credential_not_found(self, store):
        with pytest.raises(NotFoundError):
            await store.get_credential("ghost")

    async def test_list_credentials(self, store):
        await store.create_credential(
            CredentialCreate(
                api_name="c1",
                credential_type="BASIC_AUTH",
                secret_data={},
            )
        )
        await store.create_credential(
            CredentialCreate(
                api_name="c2",
                credential_type="API_KEY",
                secret_data={"key": "xxx"},
            )
        )
        creds = await store.list_credentials()
        assert len(creds) == 2

    async def test_delete_credential(self, store):
        await store.create_credential(
            CredentialCreate(
                api_name="temp_cred",
                credential_type="BASIC_AUTH",
                secret_data={},
            )
        )
        await store.delete_credential("temp_cred")
        with pytest.raises(NotFoundError):
            await store.get_credential("temp_cred")


class TestDataSourceCRUD:
    async def test_create_datasource(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP MySQL",
                connector_type="mysql",
                connector_config={"host": "localhost", "port": "3306", "database": "erp"},
            )
        )
        assert ds.id != ""
        assert ds.api_name == "erp_mysql"
        assert ds.status == "CONNECTED"

    async def test_get_datasource(self, store):
        await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP MySQL",
                connector_type="mysql",
                connector_config={"host": "localhost", "port": "3306", "database": "erp"},
            )
        )
        ds = await store.get_datasource("erp_mysql")
        assert ds.api_name == "erp_mysql"

    async def test_get_datasource_not_found(self, store):
        with pytest.raises(NotFoundError):
            await store.get_datasource("ghost")

    async def test_get_datasource_by_id(self, store):
        created = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP MySQL",
                connector_type="mysql",
                connector_config={},
            )
        )
        ds = await store.get_datasource_by_id(created.id)
        assert ds.api_name == "erp_mysql"

    async def test_list_datasources(self, store):
        await store.create_datasource(
            DataSourceCreate(
                api_name="ds1",
                display_name="DS1",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.create_datasource(
            DataSourceCreate(
                api_name="ds2",
                display_name="DS2",
                connector_type="postgresql",
                connector_config={},
            )
        )
        dss = await store.list_datasources()
        assert len(dss) == 2

    async def test_update_datasource(self, store):
        await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP MySQL",
                connector_type="mysql",
                connector_config={},
            )
        )
        updated = await store.update_datasource("erp_mysql", {"display_name": "ERP Updated", "status": "ERROR"})
        assert updated.display_name == "ERP Updated"
        assert updated.status == "ERROR"

    async def test_delete_datasource(self, store):
        await store.create_datasource(
            DataSourceCreate(
                api_name="temp_ds",
                display_name="Temp",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.delete_datasource("temp_ds")
        with pytest.raises(NotFoundError):
            await store.get_datasource("temp_ds")


class TestSyncTaskCRUD:
    async def test_create_sync_task(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP",
                connector_type="mysql",
                connector_config={},
            )
        )
        task = await store.create_sync_task(
            SyncTaskCreate(
                api_name="sync_orders",
                data_source_id=ds.id,
                sync_type="FULL_SYNC",
                source_config={"table": "orders"},
                target_dataset_api_name="orders_dataset",
            )
        )
        assert task.id != ""
        assert task.api_name == "sync_orders"
        assert task.status == "DRAFT"

    async def test_get_sync_task(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.create_sync_task(
            SyncTaskCreate(
                api_name="sync_orders",
                data_source_id=ds.id,
                sync_type="FULL_SYNC",
                source_config={"table": "orders"},
                target_dataset_api_name="orders_dataset",
            )
        )
        task = await store.get_sync_task("sync_orders")
        assert task.api_name == "sync_orders"

    async def test_list_sync_tasks_for_datasource(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.create_sync_task(
            SyncTaskCreate(
                api_name="sync_orders",
                data_source_id=ds.id,
                sync_type="FULL_SYNC",
                source_config={"table": "orders"},
                target_dataset_api_name="orders_dataset",
            )
        )
        tasks = await store.list_sync_tasks_for_datasource(ds.id)
        assert len(tasks) == 1

    async def test_update_sync_task(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.create_sync_task(
            SyncTaskCreate(
                api_name="sync_orders",
                data_source_id=ds.id,
                sync_type="FULL_SYNC",
                source_config={"table": "orders"},
                target_dataset_api_name="orders_dataset",
            )
        )
        updated = await store.update_sync_task("sync_orders", {"status": "RUNNING", "pipeline_name": "p-1"})
        assert updated.status == "RUNNING"
        assert updated.pipeline_name == "p-1"

    async def test_delete_sync_task(self, store):
        ds = await store.create_datasource(
            DataSourceCreate(
                api_name="erp_mysql",
                display_name="ERP",
                connector_type="mysql",
                connector_config={},
            )
        )
        await store.create_sync_task(
            SyncTaskCreate(
                api_name="sync_orders",
                data_source_id=ds.id,
                sync_type="FULL_SYNC",
                source_config={"table": "orders"},
                target_dataset_api_name="orders_dataset",
            )
        )
        await store.delete_sync_task("sync_orders")
        with pytest.raises(NotFoundError):
            await store.get_sync_task("sync_orders")


class TestDatasetGovernanceCRUD:
    async def test_create_dataset(self, store):
        ds = await store.create_dataset(
            DatasetGovernanceCreate(
                api_name="orders_dataset",
                display_name="Orders Dataset",
            )
        )
        assert ds.id != ""
        assert ds.api_name == "orders_dataset"

    async def test_get_dataset(self, store):
        await store.create_dataset(
            DatasetGovernanceCreate(
                api_name="orders_dataset",
                display_name="Orders Dataset",
            )
        )
        ds = await store.get_dataset("orders_dataset")
        assert ds.api_name == "orders_dataset"

    async def test_list_datasets(self, store):
        await store.create_dataset(
            DatasetGovernanceCreate(
                api_name="ds1",
                display_name="DS1",
            )
        )
        await store.create_dataset(
            DatasetGovernanceCreate(
                api_name="ds2",
                display_name="DS2",
            )
        )
        dss = await store.list_datasets()
        assert len(dss) == 2


class TestDatasetPagination:
    """list_datasets_paginated — real SQL LIMIT/OFFSET + COUNT.

    Validates paging behavior on sqlite (per CLAUDE.md: DB query logic must
    be tested against real SQL, not mocked).
    """

    async def _seed(self, store, n: int) -> None:
        for i in range(n):
            await store.create_dataset(
                DatasetGovernanceCreate(api_name=f"ds{i:02d}", display_name=f"DS{i:02d}")
            )

    async def test_first_page_returns_page_size_items_and_total(self, store):
        await self._seed(store, 25)
        items, total = await store.list_datasets_paginated(page=1, page_size=10)
        assert total == 25
        assert len(items) == 10
        # ordered by created_at → ds00 first
        assert items[0].api_name == "ds00"

    async def test_second_page_skips_first_page(self, store):
        await self._seed(store, 25)
        items, _ = await store.list_datasets_paginated(page=2, page_size=10)
        assert len(items) == 10
        assert items[0].api_name == "ds10"

    async def test_last_page_returns_remainder(self, store):
        await self._seed(store, 25)
        items, total = await store.list_datasets_paginated(page=3, page_size=10)
        assert total == 25
        assert len(items) == 5  # 25 - 20
        assert items[-1].api_name == "ds24"

    async def test_page_beyond_total_returns_empty(self, store):
        await self._seed(store, 5)
        items, total = await store.list_datasets_paginated(page=10, page_size=10)
        assert total == 5
        assert items == []

    async def test_empty_table_returns_zero_total(self, store):
        items, total = await store.list_datasets_paginated(page=1, page_size=10)
        assert total == 0
        assert items == []

    async def test_update_dataset_stats(self, store):
        await store.create_dataset(
            DatasetGovernanceCreate(
                api_name="orders_dataset",
                display_name="Orders Dataset",
            )
        )
        await store.update_dataset_stats("orders_dataset", 1000)
        ds = await store.get_dataset("orders_dataset")
        assert ds.row_count_estimate == 1000


class TestObjectState:
    async def test_upsert_create(self, store):
        """Creating a new object state (expected_version=0)."""
        version = await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        assert version == 1

    async def test_upsert_update(self, store):
        """Updating an existing object state with version match."""
        await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        version = await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "shipped"},
            expected_version=1,
        )
        assert version == 2

    async def test_upsert_conflict(self, store):
        """Version mismatch returns 0."""
        await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        version = await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "shipped"},
            expected_version=5,  # Wrong version
        )
        assert version == 0

    async def test_upsert_duplicate_create(self, store):
        """Creating the same rid twice returns 0 (conflict)."""
        v1 = await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        assert v1 == 1
        v2 = await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        assert v2 == 0

    async def test_get_object_state(self, store):
        await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        state = await store.get_object_state("obj-1")
        assert state is not None
        assert state["properties"]["status"] == "active"

    async def test_get_object_state_not_found(self, store):
        state = await store.get_object_state("nonexistent")
        assert state is None

    async def test_get_object_states_by_type(self, store):
        await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        await store.upsert_object_state(
            rid="obj-2",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "shipped"},
            expected_version=0,
        )
        results = await store.get_object_states_by_type("order")
        assert len(results) == 2

    async def test_delete_object_state(self, store):
        await store.upsert_object_state(
            rid="obj-1",
            object_type_api_name="order",
            ontology_id="onto1",
            properties={"status": "active"},
            expected_version=0,
        )
        await store.delete_object_state("obj-1")
        state = await store.get_object_state("obj-1")
        assert state is None


class TestTransactionControl:
    async def test_commit_transaction(self, store):
        await store.commit_transaction()

    async def test_rollback_transaction(self, store):
        await store.rollback_transaction()

    async def test_get_object_types_for_dataset_empty(self, store):
        ots = await store.get_object_types_for_dataset("nonexistentDataset")
        assert ots == []


class TestActionExecutionHelpers:
    async def test_create_execution_log(self, store):
        model = await store.create_execution_log(
            action_type_api_name="shipOrder",
            object_type_api_name="order",
            ontology_id="onto1",
            idempotency_key="key-123",
            parameters={"status": "shipped"},
            mutations=[{"type": "UPDATE_OBJECT"}],
        )
        assert model.id != ""
        assert model.status == "COMPLETED"

    async def test_get_execution_by_idempotency_key(self, store):
        await store.create_execution_log(
            action_type_api_name="shipOrder",
            object_type_api_name="order",
            ontology_id="onto1",
            idempotency_key="key-123",
            parameters={},
            mutations=[],
        )
        log = await store.get_execution_by_idempotency_key("key-123")
        assert log is not None
        assert log.idempotency_key == "key-123"

    async def test_create_outbox_record(self, store):
        model = await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="webhook",
            effect_config={"url": "https://example.com"},
            payload={"data": "test"},
        )
        assert model.id != ""
        assert model.status == "PENDING"

    async def test_fetch_pending_outbox(self, store):
        await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="webhook",
            effect_config={"url": "https://example.com"},
        )
        records = await store.fetch_pending_outbox()
        assert len(records) == 1

    async def test_mark_outbox_completed(self, store):
        model = await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="webhook",
            effect_config={"url": "https://example.com"},
        )
        await store.mark_outbox_completed(model.id)
        records = await store.fetch_pending_outbox()
        assert len(records) == 0

    async def test_retry_outbox(self, store):
        from datetime import timedelta

        model = await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="webhook",
            effect_config={"url": "https://example.com"},
        )
        next_retry = NOW + timedelta(minutes=5)
        await store.retry_outbox(model.id, retry_count=1, error="timeout", next_retry_at=next_retry)
        records = await store.fetch_pending_outbox()
        # Since next_retry is in the future, it won't be fetched by fetch_pending
        # But the record is now in PENDING state
        assert len(records) == 0  # next_retry_at is in the future

    async def test_move_outbox_to_dlq(self, store):
        model = await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="webhook",
            effect_config={"url": "https://example.com"},
        )
        await store.move_outbox_to_dlq(model.id, error="permanent failure")
        records = await store.fetch_pending_outbox()
        assert len(records) == 0  # DLQ records are not pending


class TestDatasetOntologyMap:
    """get_dataset_ontology_map — reverse-lookup dataset → ontologies.

    Validates real SQL behavior (JOIN across properties × object_types ×
    ontologies), not a mock, per CLAUDE.md testing rules.
    """

    async def _seed_ontology_with_ot(
        self,
        store,
        ont_api: str,
        ot_api: str,
        *,
        backing_dataset: str | None = "ds_one",
        extra_prop_backing: str | None = None,
    ) -> str:
        """Seed (ontology?) + OT + properties. Returns the ontology id.

        If an ontology with ``ont_api`` already exists, reuse it (OTs under
        the same ontology are allowed)."""
        from sqlalchemy import select

        from ontology.core.models.ontology import (
            ObjectTypeModel,
            OntologyModel,
            PropertyDefModel,
        )

        existing = (
            await store._session.execute(
                select(OntologyModel).where(OntologyModel.api_name == ont_api)
            )
        ).scalar_one_or_none()
        if existing:
            ont = existing
        else:
            ont = OntologyModel(api_name=ont_api, display_name=ont_api)
            store._session.add(ont)
            await store._session.flush()

        ot = ObjectTypeModel(
            ontology_id=ont.id,
            api_name=ot_api,
            display_name=ot_api,
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            project_id="00000000000000000000000000000001",
        )
        ot.properties.append(
            PropertyDefModel(
                object_type_id=ot.id,
                api_name="name",
                display_name="Name",
                data_type="STRING",
                project_id="00000000000000000000000000000001",
                is_primary_key=False,
                backing_dataset_api_name=backing_dataset,
                backing_table=backing_dataset,
                backing_column="name",
            )
        )
        if extra_prop_backing:
            ot.properties.append(
                PropertyDefModel(
                    object_type_id=ot.id,
                    api_name="ref",
                    display_name="Ref",
                    data_type="STRING",
                project_id="00000000000000000000000000000001",
                    is_primary_key=False,
                    backing_dataset_api_name=extra_prop_backing,
                    backing_table=extra_prop_backing,
                    backing_column="ref",
                )
            )
        store._session.add(ot)
        await store._session.flush()
        return ont.id

    async def test_map_returns_backing_datasets_grouped_by_ontology(self, store):
        """A property's backing_dataset_api_name maps to its ontology."""
        await self._seed_ontology_with_ot(store, "Marketing", "Dealership", backing_dataset="dealership")
        await self._seed_ontology_with_ot(store, "DVP", "TestItem", backing_dataset="test_item")

        result = await store.get_dataset_ontology_map()

        assert result["dealership"] == [
            {
                "ontology_id": result["dealership"][0]["ontology_id"],
                "ontology_api_name": "Marketing",
                "ontology_display_name": "Marketing",
                "object_type_api_name": "Dealership",
            }
        ]
        assert result["test_item"][0]["ontology_api_name"] == "DVP"
        assert result["test_item"][0]["object_type_api_name"] == "TestItem"

    async def test_map_dedupes_multiple_properties_same_dataset(self, store):
        """One OT with two properties backing the same dataset → single entry."""
        await self._seed_ontology_with_ot(
            store, "Marketing", "Dealership", backing_dataset="dealership", extra_prop_backing="dealership"
        )

        result = await store.get_dataset_ontology_map()

        # Same (dataset, ontology, ot) referenced by two properties — deduped to 1.
        assert len(result["dealership"]) == 1
        assert result["dealership"][0]["object_type_api_name"] == "Dealership"

    async def test_map_dataset_referenced_by_two_ontologies(self, store):
        """A dataset referenced by two ontologies gets both entries."""
        await self._seed_ontology_with_ot(store, "Marketing", "Customer", backing_dataset="shared_ds")
        await self._seed_ontology_with_ot(store, "ChainSmoke", "Customer2", backing_dataset="shared_ds")

        result = await store.get_dataset_ontology_map()

        onts = sorted(r["ontology_api_name"] for r in result["shared_ds"])
        assert onts == ["ChainSmoke", "Marketing"]

    async def test_map_excludes_properties_without_backing(self, store):
        """Properties with null backing_dataset_api_name don't pollute the map."""
        await self._seed_ontology_with_ot(store, "Marketing", "Dealership", backing_dataset="dealership")
        # OT under the SAME ontology, but its property has NO backing dataset.
        await self._seed_ontology_with_ot(store, "Marketing", "NoBacking", backing_dataset=None)

        result = await store.get_dataset_ontology_map()

        assert "dealership" in result
        # NoBacking's property had null backing → it produces no dataset key.
        # Only Dealership's backing should appear.
        assert sorted(result.keys()) == ["dealership"]

    async def test_map_empty_when_no_backings(self, store):
        """No backing refs at all → empty dict."""
        result = await store.get_dataset_ontology_map()
        assert result == {}


class TestDatasetPaginationFilters:
    """list_datasets_paginated filtering — real SQL WHERE clauses."""

    async def test_search_filters_by_api_name_or_display(self, store):
        await store.create_dataset(DatasetGovernanceCreate(api_name="orders_2024", display_name="Orders"))
        await store.create_dataset(DatasetGovernanceCreate(api_name="customers", display_name="VIP客户"))
        # search matches api_name
        items, total = await store.list_datasets_paginated(page=1, page_size=20, search="orders")
        assert total == 1
        assert items[0].api_name == "orders_2024"
        # search matches display_name (中文)
        items, total = await store.list_datasets_paginated(page=1, page_size=20, search="客户")
        assert total == 1
        assert items[0].api_name == "customers"

    async def test_type_filter_virtual(self, store):
        await store.create_dataset(DatasetGovernanceCreate(api_name="managed1", kind="MANAGED"))
        await store.create_dataset(DatasetGovernanceCreate(api_name="virtual1", kind="VIRTUAL"))
        items, total = await store.list_datasets_paginated(page=1, page_size=20, type_filter="virtual")
        assert total == 1
        assert items[0].api_name == "virtual1"

    async def test_type_filter_transform(self, store):
        await store.create_dataset(DatasetGovernanceCreate(api_name="base1", kind="MANAGED"))
        await store.create_dataset(
            DatasetGovernanceCreate(api_name="derived1", kind="MANAGED", source_dataset_api_name="base1")
        )
        items, total = await store.list_datasets_paginated(page=1, page_size=20, type_filter="transform")
        assert total == 1
        assert items[0].api_name == "derived1"

    async def test_type_filter_managed_excludes_transform(self, store):
        await store.create_dataset(DatasetGovernanceCreate(api_name="managed1", kind="MANAGED"))
        await store.create_dataset(
            DatasetGovernanceCreate(api_name="derived1", kind="MANAGED", source_dataset_api_name="base1")
        )
        await store.create_dataset(DatasetGovernanceCreate(api_name="virtual1", kind="VIRTUAL"))
        items, total = await store.list_datasets_paginated(page=1, page_size=20, type_filter="managed")
        assert total == 1
        assert items[0].api_name == "managed1"

    async def test_ontology_filter(self, store):
        # seed: 一个本体引用 ds_backed，另一个本体不引用
        from ontology.core.models.ontology import (
            ObjectTypeModel,
            OntologyModel,
            PropertyDefModel,
        )
        ont = OntologyModel(api_name="OntA", display_name="OntA")
        store._session.add(ont)
        await store._session.flush()
        ot = ObjectTypeModel(
            ontology_id=ont.id, api_name="OtA", display_name="OtA",
            primary_key="id", title_property="name", storage_type="MANAGED",
            project_id="00000000000000000000000000000001",
        )
        ot.properties.append(PropertyDefModel(
            object_type_id=ot.id, api_name="name", display_name="Name",
            data_type="STRING", is_primary_key=False,
            project_id="00000000000000000000000000000001",
            backing_dataset_api_name="ds_backed", backing_table="ds_backed", backing_column="name",
        ))
        store._session.add(ot)
        await store.create_dataset(DatasetGovernanceCreate(api_name="ds_backed", kind="MANAGED"))
        await store.create_dataset(DatasetGovernanceCreate(api_name="ds_orphan", kind="MANAGED"))

        items, total = await store.list_datasets_paginated(
            page=1, page_size=20, ontology_api_name="OntA"
        )
        assert total == 1
        assert items[0].api_name == "ds_backed"

    async def test_combined_filters(self, store):
        await store.create_dataset(DatasetGovernanceCreate(api_name="orders_sync", kind="MANAGED"))
        await store.create_dataset(DatasetGovernanceCreate(api_name="orders_virtual", kind="VIRTUAL"))
        # search "orders" + type virtual → only the virtual one
        items, total = await store.list_datasets_paginated(
            page=1, page_size=20, search="orders", type_filter="virtual"
        )
        assert total == 1
        assert items[0].api_name == "orders_virtual"
