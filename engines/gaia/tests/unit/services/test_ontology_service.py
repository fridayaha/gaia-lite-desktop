"""Unit tests for OntologyService.

All Layer dependencies are mocked. Tests validate:
1. Ontology CRUD delegation to PostgresMetaStore
2. ObjectType creation with MANAGED storage type triggers
   Gravitino registration and Doris index table creation
3. Error paths and exception propagation
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import NotFoundError
from ontology.core.schemas.ontology import (
    DataType,
    LinkTypeDef,
    ObjectType,
    ObjectTypeCreate,
    Ontology,
    OntologyCreate,
    PropertyDef,
    SharedProperty,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    """Mock PostgresMetaStore."""
    m = AsyncMock(spec=PostgresMetaStore)
    # option A: _resolve_default_project_for_space returns a valid project id.
    m._resolve_default_project_for_space.return_value = "project-default-id"
    return m


@pytest.fixture
def mock_catalog() -> AsyncMock:
    """Mock GravitinoRegistry."""
    return AsyncMock(spec=GravitinoRegistry)


@pytest.fixture
def mock_index() -> AsyncMock:
    """Mock DorisIndexStore."""
    return AsyncMock(spec=DorisIndexStore)


@pytest.fixture
def mock_dataset() -> AsyncMock:
    """Mock IcebergStore (Catalog First managed-table provisioning)."""
    return AsyncMock(spec=IcebergStore)


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_index, mock_dataset) -> OntologyService:
    """Create OntologyService with mocked dependencies."""
    return OntologyService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        index=mock_index,
        dataset=mock_dataset,
    )


class TestCreateOntology:
    """Ontology creation through the service layer."""

    @pytest.mark.asyncio
    async def test_create_ontology(self, service, mock_metadata):
        """Create an Ontology delegates to metadata store."""
        mock_metadata.create_ontology.return_value = Ontology(
            id="id123",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )

        result = await service.create_ontology(OntologyCreate(api_name="Hr", display_name="HR"))

        assert result.api_name == "hr"
        mock_metadata.create_ontology.assert_awaited_once()


class TestDefineObjectType:
    """ObjectType definition at the service layer."""

    @pytest.mark.asyncio
    async def test_define_physical_object_type(self, service, mock_metadata, mock_catalog, mock_index, mock_dataset):
        """MANAGED ObjectType triggers IcebergStore + Doris provisioning."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.create_object_type.return_value = ObjectType(
            id="ot123",
            ontology_id="onto1",
            api_name="order",
            display_name="Order",
            description="",
            primary_key="order_id",
            title_property="description",
            storage_type="MANAGED",
            visibility="NORMAL",
            status="ACTIVE",
            properties=[],
            links=[],
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )

        ot_create = ObjectTypeCreate(
            api_name="Order",
            display_name="Order",
            primary_key="order_id",
            title_property="description",
            storage_type="MANAGED",
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "order")
        result = await service.define_object_type("hr", ot_create)

        assert result.api_name == "order"
        mock_metadata.create_object_type.assert_awaited_once()
        # Catalog First: managed Iceberg table is created via IcebergStore
        # (pyiceberg, with PK/comment/NULL), not GravitinoRegistry.register_dataset.
        mock_dataset.create_managed_table.assert_awaited_once()
        mock_catalog.register_dataset.assert_not_called()
        mock_index.create_index_table.assert_awaited_once()
        # MANAGED ObjectType must also write a PG datasets governance record
        # (kind=MANAGED) so the dataset is visible on the data-connections
        # page and link_dataset can resolve it. The dataset api_name is the
        # lower-cased form of the ObjectType api_name.
        mock_metadata.create_dataset.assert_awaited_once()
        ds_arg = mock_metadata.create_dataset.await_args.args[0]
        assert ds_arg.api_name == "order"
        assert ds_arg.kind == "MANAGED"
        assert ds_arg.is_view is False
        # The Iceberg physical table MUST be registered under the same
        # lower-cased dataset api_name as the governance record (Iceberg
        # table name == dataset api_name, per naming.py).
        reg_call = mock_dataset.create_managed_table.await_args
        assert reg_call.args[0] == "order"
        schema_arg = reg_call.args[1]
        # single-type define: PK column only, marked as primary key
        assert len(schema_arg.columns) == 1
        assert schema_arg.columns[0].name == "order_id"
        assert schema_arg.columns[0].is_primary_key is True

    @pytest.mark.asyncio
    async def test_define_virtual_object_type(self, service, mock_metadata, mock_catalog, mock_index, mock_dataset):
        """VIRTUAL ObjectType skips physical registration."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.create_object_type.return_value = ObjectType(
            id="ot456",
            ontology_id="onto1",
            api_name="active_orders",
            display_name="Active Orders",
            description="",
            primary_key="order_id",
            title_property="description",
            storage_type="VIRTUAL",
            visibility="NORMAL",
            status="ACTIVE",
            properties=[],
            links=[],
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )

        ot_create = ObjectTypeCreate(
            api_name="ActiveOrders",
            display_name="Active Orders",
            primary_key="order_id",
            title_property="description",
            storage_type="VIRTUAL",
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "active_orders")
        result = await service.define_object_type("hr", ot_create)

        assert result.storage_type == "VIRTUAL"
        mock_catalog.register_dataset.assert_not_called()
        mock_dataset.create_managed_table.assert_not_called()
        mock_index.create_index_table.assert_not_called()
        # VIRTUAL ObjectType must NOT write a MANAGED datasets governance record.
        mock_metadata.create_dataset.assert_not_called()

    @pytest.mark.asyncio
    async def test_define_managed_idempotent_dataset_governance(self, service, mock_metadata, mock_catalog, mock_index):
        """Defining a MANAGED ObjectType whose dataset already exists must not raise;
        create_dataset is idempotent (returns the existing record)."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.create_object_type.return_value = ObjectType(
            id="ot123",
            ontology_id="onto1",
            api_name="order",
            display_name="Order",
            description="",
            primary_key="order_id",
            title_property="description",
            storage_type="MANAGED",
            visibility="NORMAL",
            status="ACTIVE",
            properties=[],
            links=[],
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        # create_dataset returns an existing record (idempotent path).
        mock_metadata.create_dataset.return_value = MagicMock()

        ot_create = ObjectTypeCreate(
            api_name="Order",
            display_name="Order",
            primary_key="order_id",
            title_property="description",
            storage_type="MANAGED",
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "order")
        result = await service.define_object_type("hr", ot_create)

        assert result.api_name == "order"
        mock_metadata.create_dataset.assert_awaited_once()


class TestSharedProperty:
    """Shared property management."""

    @pytest.mark.asyncio
    async def test_add_shared_property(self, service, mock_metadata):
        """Creating a shared property derives api_name and delegates to metadata."""
        mock_metadata.list_shared_properties.return_value = []
        mock_metadata.create_shared_property.return_value = SharedProperty(
            id="sp1",
            api_name="globalName",
            display_name="Global Name",
            data_type=DataType.STRING,
            description="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )

        result = await service.add_shared_property(
            display_name="Global Name",
            data_type=DataType.STRING,
        )

        assert result.api_name == "globalName"
        mock_metadata.create_shared_property.assert_awaited_once()
        # Verify the derived api_name was passed through.
        passed = mock_metadata.create_shared_property.call_args.args[0]
        assert passed.api_name == "globalName"


class TestLinkType:
    """Link type management."""

    @pytest.mark.asyncio
    async def test_define_link_type(self, service, mock_metadata):
        """Creating a link type delegates to metadata."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.create_link_type.return_value = MagicMock()

        from ontology.core.schemas.ontology import LinkTypeDefCreate

        link = LinkTypeDefCreate(
            api_name="empDept",
            display_name="Employee Department",
            source_object_type_id="emp_id",
            target_object_type_id="dept_id",
            cardinality="MANY",
            direction="OUTGOING",
        )
        result = await service.define_link_type("hr", link)

        assert result is not None
        mock_metadata.create_link_type.assert_awaited_once()


class TestActionType:
    """Action type management."""

    @pytest.mark.asyncio
    async def test_define_action_type(self, service, mock_metadata):
        """Creating an action type delegates to metadata."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.create_action_type.return_value = MagicMock()

        result = await service.define_action_type(
            ontology_api_name="hr",
            api_name="promote",
            display_name="Promote",
            parameters={"grade": {"type": "STRING"}},
        )

        assert result is not None
        mock_metadata.create_action_type.assert_awaited_once()


class TestErrorHandling:
    """Service layer error propagation."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_ontology(self, service, mock_metadata):
        """NotFoundError propagates from metadata store."""
        mock_metadata.get_ontology.side_effect = NotFoundError("Ontology", "ghost")

        with pytest.raises(NotFoundError, match="Ontology not found"):
            await service.get_ontology("ghost")

    @pytest.mark.asyncio
    async def test_get_nonexistent_object_type(self, service, mock_metadata):
        """NotFoundError for ObjectType propagates."""
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "ghost")

        with pytest.raises(NotFoundError, match="ObjectType not found"):
            await service.get_object_type("hr", "ghost")


class TestLayeredDelegates:
    """Cover the thin service delegates added to keep routes off _metadata."""

    @pytest.mark.asyncio
    async def test_delete_object_type(self, service, mock_metadata):
        ot = ObjectType(
            id="ot1",
            ontology_id="o1",
            api_name="employee",
            display_name="E",
            description="",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_object_type.return_value = ot
        await service.delete_object_type("hr", "employee")
        mock_metadata.delete_object_type.assert_awaited_once_with("ot1")

    @pytest.mark.asyncio
    async def test_delete_property(self, service, mock_metadata):
        ot = ObjectType(
            id="ot1",
            ontology_id="o1",
            api_name="employee",
            display_name="E",
            description="",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        prop = PropertyDef(
            id="p1",
            object_type_id="ot1",
            api_name="age",
            display_name="Age",
            data_type="INTEGER",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_object_type.return_value = ot
        mock_metadata.get_properties.return_value = [prop]
        await service.delete_property("hr", "employee", "age")
        mock_metadata.delete_property.assert_awaited_once_with("p1")

    @pytest.mark.asyncio
    async def test_delete_property_not_found(self, service, mock_metadata):
        ot = ObjectType(
            id="ot1",
            ontology_id="o1",
            api_name="employee",
            display_name="E",
            description="",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_object_type.return_value = ot
        mock_metadata.get_properties.return_value = []
        with pytest.raises(NotFoundError):
            await service.delete_property("hr", "employee", "ghost")

    @pytest.mark.asyncio
    async def test_delete_link_type(self, service, mock_metadata):
        link = LinkTypeDef(
            id="l1",
            ontology_id="o1",
            api_name="works_for",
            display_name="Works For",
            source_object_type_id="a",
            target_object_type_id="b",
            cardinality="MANY",
            direction="OUTGOING",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_link_types.return_value = [link]
        await service.delete_link_type("hr", "works_for")
        mock_metadata.delete_link_type.assert_awaited_once_with("l1")

    @pytest.mark.asyncio
    async def test_delete_link_type_not_found(self, service, mock_metadata):
        mock_metadata.get_link_types.return_value = []
        with pytest.raises(NotFoundError):
            await service.delete_link_type("hr", "ghost")

    @pytest.mark.asyncio
    async def test_list_link_types(self, service, mock_metadata):
        mock_metadata.get_link_types.return_value = []
        result = await service.list_link_types("hr")
        assert result == []
        mock_metadata.get_link_types.assert_awaited_once_with("hr", include_non_active=False)

    @pytest.mark.asyncio
    async def test_list_action_types(self, service, mock_metadata):
        mock_metadata.list_action_types.return_value = []
        result = await service.list_action_types("hr")
        assert result == []
        mock_metadata.list_action_types.assert_awaited_once_with("hr")

    @pytest.mark.asyncio
    async def test_list_ontologies_with_counts(self, service, mock_metadata):
        mock_metadata.list_ontologies_with_counts.return_value = []
        result = await service.list_ontologies_with_counts()
        assert result == []
        mock_metadata.list_ontologies_with_counts.assert_awaited_once()
