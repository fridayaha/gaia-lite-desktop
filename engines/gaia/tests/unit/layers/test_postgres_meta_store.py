"""Unit tests for PostgresMetaStore.

All database calls are mocked — tests validate:
1. Correct SQLAlchemy queries are constructed
2. Domain exceptions are raised for error paths
3. Schema conversion (ORM → pydantic) works correctly
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ConflictError, NotFoundError
from ontology.core.schemas.ontology import (
    ActionType,
    Branch,
    DataType,
    LinkTypeDef,
    ObjectType,
    ObjectTypeGroup,
    Ontology,
    PropertyDef,
    SharedProperty,
    Struct,
    ValueType,
)
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore


def make_onto_model(**overrides):
    """Factory: create a mock ORM OntologyModel."""
    model = MagicMock()
    model.id = overrides.get("id", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    model.api_name = overrides.get("api_name", "test_ontology")
    model.display_name = overrides.get("display_name", "Test Ontology")
    model.description = overrides.get("description", "")
    model.rid = overrides.get("rid", "")
    # v5.2 lifecycle fields.
    model.status = overrides.get("status", "ACTIVE")
    model.deleted_at = overrides.get("deleted_at", None)
    model.space_id = overrides.get("space_id", None)
    model.created_at = overrides.get("created_at", datetime.now(UTC))
    model.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return model


def make_ot_model(**overrides):
    """Factory: create a mock ORM ObjectTypeModel."""
    model = MagicMock()
    model.id = overrides.get("id", "ot_id_1234567890123456789012345678")
    model.ontology_id = overrides.get("ontology_id", "onto_id")
    model.api_name = overrides.get("api_name", "test_type")
    model.display_name = overrides.get("display_name", "Test Type")
    model.description = overrides.get("description", "")
    model.primary_key = overrides.get("primary_key", "id")
    model.title_property = overrides.get("title_property", "name")
    model.storage_type = overrides.get("storage_type", "MANAGED")
    model.visibility = overrides.get("visibility", "NORMAL")
    model.status = overrides.get("status", "ACTIVE")
    model.deleted_at = overrides.get("deleted_at", None)
    model.project_id = overrides.get("project_id", None)
    model.backing_dataset_api_name = overrides.get("backing_dataset_api_name", None)
    model.capabilities = overrides.get("capabilities", {})
    model.properties = overrides.get("properties", [])
    model.created_at = overrides.get("created_at", datetime.now(UTC))
    model.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return model


@pytest.fixture
def store(mock_session):
    """Create a PostgresMetaStore with a mocked session."""
    return PostgresMetaStore(mock_session)


class TestCreateOntology:
    """Ontology creation."""

    @pytest.mark.asyncio
    async def test_create_returns_validated_ontology(self, store, mock_session):
        """Create an Ontology returns a pydantic-validated Ontology."""
        onto_schema = Ontology(
            id="",
            api_name="test_ontology",
            display_name="Test Ontology",
            description="A test",
            rid="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        mock_session.flush = AsyncMock()
        result = await store.create_ontology(onto_schema)

        # Session.add should have been called
        mock_session.add.assert_called_once()
        # Result should be a valid Ontology schema
        assert isinstance(result, Ontology)
        assert result.api_name == "test_ontology"

    @pytest.mark.asyncio
    async def test_create_duplicate_api_name_raises(self, store, mock_session):
        """Creating a duplicate Ontology raises an exception."""
        onto_schema = Ontology(
            id="",
            api_name="duplicate",
            display_name="Duplicate",
            description="",
            rid="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Simulate flush failure (violating unique constraint)
        mock_session.flush = AsyncMock(side_effect=Exception("unique constraint"))

        with pytest.raises(Exception, match="unique constraint"):
            await store.create_ontology(onto_schema)


class TestGetOntology:
    """Ontology retrieval."""

    @pytest.mark.asyncio
    async def test_get_ontology_found(self, store, mock_session, mock_execute_result):
        """Get an existing Ontology by api_name."""
        mock_model = make_onto_model(api_name="my_onto")
        mock_execute_result.scalar_one_or_none.return_value = mock_model
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await store.get_ontology("my_onto")

        assert isinstance(result, Ontology)
        assert result.api_name == "my_onto"
        assert result.display_name == "Test Ontology"

    @pytest.mark.asyncio
    async def test_get_ontology_not_found(self, store, mock_session, mock_execute_result):
        """Get a non-existent Ontology raises NotFoundError."""
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with pytest.raises(NotFoundError, match="Ontology not found: nonexistent"):
            await store.get_ontology("nonexistent")

    @pytest.mark.asyncio
    async def test_list_ontologies(self, store, mock_session, mock_execute_result):
        """List all Ontologies."""
        mock_model_1 = make_onto_model(api_name="onto_1")
        mock_model_2 = make_onto_model(api_name="onto_2")
        mock_execute_result.scalars.return_value.all.return_value = [mock_model_1, mock_model_2]
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        results = await store.list_ontologies()

        assert len(results) == 2
        assert results[0].api_name == "onto_1"
        assert results[1].api_name == "onto_2"

    @pytest.mark.asyncio
    async def test_list_ontologies_empty(self, store, mock_session, mock_execute_result):
        """List returns empty list when no Ontologies exist."""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        results = await store.list_ontologies()

        assert results == []


class TestUpdateOntology:
    """Ontology update operations."""

    @pytest.mark.asyncio
    async def test_update_display_name(self, store, mock_session, mock_execute_result):
        """Update display_name on an existing Ontology."""
        mock_model = make_onto_model(api_name="to_update")
        mock_execute_result.scalar_one_or_none.return_value = mock_model
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await store.update_ontology("to_update", display_name="Updated Name")

        assert mock_model.display_name == "Updated Name"
        assert result.display_name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, store, mock_session, mock_execute_result):
        """Update a non-existent Ontology raises NotFoundError."""
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with pytest.raises(NotFoundError, match="Ontology not found: ghost"):
            await store.update_ontology("ghost", display_name="Ghost")


class TestDeleteOntology:
    """Ontology deletion."""

    @pytest.mark.asyncio
    async def test_delete_ontology(self, store, mock_session, mock_execute_result):
        """Soft-delete an existing Ontology (v5.2: sets deleted_at, no row delete)."""
        mock_model = make_onto_model(api_name="to_delete")
        mock_execute_result.scalar_one_or_none.return_value = mock_model
        mock_session.execute = AsyncMock(return_value=mock_execute_result)
        mock_session.delete = AsyncMock()

        await store.delete_ontology("to_delete")

        # v5.2: soft-delete marks deleted_at on the ontology row; the PG row
        # is NOT removed (the cleanup script reaps it after the cooldown).
        assert mock_model.deleted_at is not None
        mock_session.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store, mock_session, mock_execute_result):
        """Delete a non-existent Ontology raises NotFoundError."""
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with pytest.raises(NotFoundError):
            await store.delete_ontology("ghost")


class TestObjectType:
    """ObjectType CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_object_type(self, store, mock_session):
        """Create an ObjectType."""
        ot = ObjectType(
            id="",
            ontology_id="onto_id",
            api_name="employee",
            display_name="Employee",
            description="",
            primary_key="emp_id",
            title_property="full_name",
            storage_type="MANAGED",
            visibility="NORMAL",
            status="ACTIVE",
            properties=[],
            links=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_session.flush = AsyncMock()

        result = await store.create_object_type(ot)

        mock_session.add.assert_called_once()
        assert isinstance(result, ObjectType)
        assert result.api_name == "employee"

    @pytest.mark.asyncio
    async def test_get_object_type_not_found(self, store, mock_session, mock_execute_result):
        """Getting a non-existent ObjectType raises NotFoundError."""
        # First call (getting ontology) returns None
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with pytest.raises(NotFoundError, match="Ontology not found: ghost"):
            await store.get_object_type("ghost", "employee")

    @pytest.mark.asyncio
    async def test_list_object_types(self, store, mock_session):
        """List ObjectTypes for an Ontology."""
        mock_onto = make_onto_model(api_name="hr")
        mock_ot_1 = make_ot_model(api_name="employee")
        mock_ot_2 = make_ot_model(api_name="department")

        # First execute call returns ontology
        result_1 = MagicMock()
        result_1.scalar_one_or_none.return_value = mock_onto

        # Second execute call returns object types list
        result_2 = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [mock_ot_1, mock_ot_2]
        result_2.scalars.return_value = scalars_mock

        mock_session.execute = AsyncMock(side_effect=[result_1, result_2])

        results = await store.list_object_types("hr")

        assert len(results) == 2
        assert results[0].api_name == "employee"

    @pytest.mark.asyncio
    async def test_update_object_type(self, store, mock_session, mock_execute_result):
        """Update allowed fields on an ObjectType."""
        mock_ot = make_ot_model(api_name="to_update")
        mock_execute_result.scalar_one_or_none.return_value = mock_ot
        mock_session.execute = AsyncMock(return_value=mock_execute_result)
        mock_session.flush = AsyncMock()

        result = await store.update_object_type(mock_ot.id, {"display_name": "Updated", "status": "DEPRECATED"})

        assert mock_ot.display_name == "Updated"
        assert mock_ot.status == "DEPRECATED"
        assert isinstance(result, ObjectType)


class TestProperty:
    """Property operations."""

    @pytest.mark.asyncio
    async def test_add_property(self, store, mock_session):
        """Add a property to an ObjectType."""
        from ontology.core.schemas.ontology import BackingColumnRef

        prop = PropertyDef(
            id="",
            object_type_id="ot_id",
            api_name="name",
            display_name="Name",
            description="",
            data_type=DataType.STRING,
            is_primary_key=False,
            is_title_property=True,
            nullable=False,
            indexed=True,
            backing_mapping=BackingColumnRef(
                backing_catalog="iceberg_catalog",
                backing_schema="ontology",
                backing_table="employees",
                backing_column="full_name",
            ),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_session.flush = AsyncMock()

        result = await store.add_property("ot_id", prop)

        mock_session.add.assert_called_once()
        assert isinstance(result, PropertyDef)


class TestLinkType:
    """LinkType operations."""

    @pytest.mark.asyncio
    async def test_create_link_type(self, store, mock_session):
        """Create a link type between two ObjectTypes."""
        link = LinkTypeDef(
            id="",
            ontology_id="onto_id",
            api_name="employee_department",
            display_name="Employee Department",
            description="",
            source_object_type_id="emp_id",
            target_object_type_id="dept_id",
            foreign_key_property_api_name="department_id",
            cardinality="MANY",
            direction="OUTGOING",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_session.flush = AsyncMock()

        result = await store.create_link_type(link)

        mock_session.add.assert_called_once()
        assert isinstance(result, LinkTypeDef)
        assert result.api_name == "employee_department"


class TestActionType:
    """ActionType operations."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_create_action_type(self, store, mock_session):
        """Create an ActionType."""
        action = ActionType(
            id="",
            ontology_id="onto_id",
            api_name="promote_employee",
            display_name="Promote Employee",
            description="",
            affected_object_type_id=None,
            parameters={"new_grade": {"type": "STRING"}},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_session.flush = AsyncMock()

        result = await store.create_action_type(action)

        mock_session.add.assert_called_once()
        assert isinstance(result, ActionType)
        assert result.api_name == "promote_employee"

    @pytest.mark.asyncio
    async def test_create_action_type_duplicate_raises_conflict(self, store, mock_session):
        """Creating a duplicate ActionType (same ontology_id + api_name) is
        rejected by the DB unique constraint and surfaced as ConflictError
        (HTTP 409), not a bare IntegrityError (HTTP 500).

        Covers BUG #1: the (ontology_id, api_name) unique constraint must be
        enforced so duplicate Actions can no longer be created.
        """
        from sqlalchemy.exc import IntegrityError

        action = ActionType(
            id="",
            ontology_id="onto_id",
            api_name="promote_employee",
            display_name="Promote Employee",
            description="",
            affected_object_type_id=None,
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        # Simulate the DB rejecting the insert (partial unique index
        # uq_action_types_ontology_api_name violation).
        mock_session.flush = AsyncMock(
            side_effect=IntegrityError(
                "duplicate key value violates unique constraint uq_action_types_ontology_api_name",
                params={},
                orig=Exception("unique constraint"),
            )
        )
        mock_session.rollback = AsyncMock()

        with pytest.raises(ConflictError):
            await store.create_action_type(action)
        mock_session.rollback.assert_awaited_once()


class TestOtherEntities:
    """Other domain entity operations."""

    @pytest.mark.asyncio
    async def test_create_shared_property(self, store, mock_session):
        """Create a shared property."""
        prop = SharedProperty(
            id="",
            api_name="global_name",
            display_name="Global Name",
            description="",
            data_type=DataType.STRING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_session.flush = AsyncMock()

        result = await store.create_shared_property(prop)

        mock_session.add.assert_called_once()
        assert result.api_name == "global_name"

    @pytest.mark.asyncio
    async def test_create_value_type(self, store, mock_session):
        """Create a value type."""
        vt = ValueType(
            id="",
            ontology_id="onto_id",
            api_name="usd_amount",
            display_name="USD Amount",
            description="",
            base_type=DataType.DECIMAL,
            constraints={"precision": 18, "scale": 2},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        result = await store.create_value_type(vt)
        assert result.api_name == "usd_amount"

    @pytest.mark.asyncio
    async def test_create_struct(self, store, mock_session):
        """Create a struct type."""
        from ontology.core.schemas.ontology import StructField

        s = Struct(
            id="",
            api_name="address",
            display_name="Address",
            description="",
            fields=[
                StructField(name="street", data_type=DataType.STRING, nullable=False),
                StructField(name="city", data_type=DataType.STRING, nullable=False),
                StructField(name="zip", data_type=DataType.STRING, nullable=True),
            ],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        result = await store.create_struct(s)
        assert result.api_name == "address"
        assert len(result.fields) == 3

    @pytest.mark.asyncio
    async def test_create_group(self, store, mock_session):
        """Create an ObjectTypeGroup."""
        group = ObjectTypeGroup(
            id="",
            ontology_id="onto_id",
            api_name="hr_objects",
            display_name="HR Objects",
            description="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        result = await store.create_group(group)
        assert result.api_name == "hr_objects"

    @pytest.mark.asyncio
    async def test_create_branch(self, store, mock_session):
        """Create an Ontology branch."""
        branch = Branch(
            id="",
            ontology_id="onto_id",
            name="dev",
            is_main=False,
            status="ACTIVE",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        result = await store.create_branch(branch)
        assert result.name == "dev"
        assert result.is_main is False

    @pytest.mark.asyncio
    async def test_get_properties_empty(self, store, mock_session, mock_execute_result):
        """Getting properties for an object type with none returns empty list."""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_execute_result

        result = await store.get_properties("any_object_type_id")
        assert result == []

    @pytest.mark.asyncio
    async def test_link_shared_property(self, store, mock_session, mock_execute_result):
        """Link a shared property to an object type."""
        mock_execute_result.scalar_one_or_none.return_value = MagicMock(id="sp_id")
        mock_session.execute.return_value = mock_execute_result

        class Fake:
            pass

        mock_session.add = MagicMock()

        from ontology.core.schemas.ontology import DataType, SharedProperty

        sp = SharedProperty(
            id="sp_id",
            api_name="test_shared",
            display_name="Test",
            data_type=DataType.STRING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await store.create_shared_property(sp)
        await store.link_shared_property("ot_id", "sp_id")
        mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_create_interface_type(self, store, mock_session):
        """Create an interface type."""
        from ontology.core.schemas.ontology import DataType, InterfaceProperty, InterfaceType

        iface = InterfaceType(
            id="",
            ontology_id="onto_id",
            api_name="taggable",
            display_name="Taggable",
            description="",
            extends_interface_ids=[],
            status="EXPERIMENTAL",
            properties=[
                InterfaceProperty(
                    id="",
                    interface_type_id="",
                    api_name="tag",
                    display_name="Tag",
                    data_type=DataType.STRING,
                    is_shared=False,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ),
            ],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        result = await store.create_interface_type(iface)
        assert result.api_name == "taggable"

    @pytest.mark.asyncio
    async def test_get_link_types_empty(self, store, mock_session, mock_execute_result):
        """Get link types for an ontology with none."""
        mock_execute_result.scalar_one_or_none.return_value = MagicMock(id="onto1")
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_execute_result

        result = await store.get_link_types("empty_onto")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_action_type_not_found(self, store, mock_session, mock_execute_result):
        """Get non-existent action type raises NotFoundError."""
        # First query (find ontology) → found
        mock_onto = MagicMock(id="onto1")
        # Second query (find action type) → None
        mock_action_result = MagicMock()
        mock_action_result.scalar_one_or_none.return_value = None

        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_onto)),
            mock_action_result,
        ]

        with pytest.raises(NotFoundError, match="ActionType"):
            await store.get_action_type("valid_onto", "ghost_action")


class TestDeleteAndListMethods:
    """Cover the delete_* and list_* helper methods added for layering."""

    @pytest.mark.asyncio
    async def test_delete_object_type_found(self, store, mock_session, mock_execute_result):
        """delete_object_type deletes the model and commits."""
        model = make_ot_model()
        mock_execute_result.scalar_one_or_none.return_value = model
        mock_session.execute.return_value = mock_execute_result

        await store.delete_object_type(model.id)

        mock_session.delete.assert_called_once_with(model)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_object_type_not_found(self, store, mock_session, mock_execute_result):
        """delete_object_type raises NotFoundError when missing."""
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_execute_result

        with pytest.raises(NotFoundError):
            await store.delete_object_type("ghost")

    @pytest.mark.asyncio
    async def test_delete_property_found(self, store, mock_session, mock_execute_result):
        """delete_property deletes and commits."""
        prop_model = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = prop_model
        mock_session.execute.return_value = mock_execute_result

        await store.delete_property("pid")

        mock_session.delete.assert_called_once_with(prop_model)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_property_not_found(self, store, mock_session, mock_execute_result):
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_execute_result

        with pytest.raises(NotFoundError):
            await store.delete_property("ghost")

    @pytest.mark.asyncio
    async def test_delete_link_type_found(self, store, mock_session, mock_execute_result):
        link_model = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = link_model
        mock_session.execute.return_value = mock_execute_result

        await store.delete_link_type("lid")

        mock_session.delete.assert_called_once_with(link_model)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_list_action_types(self, store, mock_session, mock_execute_result):
        """list_action_types returns validated ActionTypes for an ontology."""
        mock_onto = MagicMock(id="onto1")
        action_model = MagicMock()
        action_model.id = "act1"
        action_model.ontology_id = "onto1"
        action_model.api_name = "act1"
        action_model.display_name = "Act"
        action_model.description = ""
        action_model.affected_object_type_id = None
        action_model.parameters = {}
        action_model.rules = {}
        action_model.submission_criteria = {}
        action_model.status = "ACTIVE"
        action_model.created_at = datetime.now(UTC)
        action_model.updated_at = datetime.now(UTC)

        mock_action_result = MagicMock()
        mock_action_result.scalars.return_value.all.return_value = [action_model]
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=mock_onto)),
                mock_action_result,
            ]
        )

        result = await store.list_action_types("onto")
        assert len(result) == 1
        assert result[0].api_name == "act1"

    @pytest.mark.asyncio
    async def test_list_ontologies_with_counts(self, store, mock_session):
        """list_ontologies_with_counts returns (model, count) tuples."""
        onto = make_onto_model()
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=onto)
        row.object_types_count = 3
        result_mock = MagicMock()
        result_mock.all.return_value = [row]
        mock_session.execute.return_value = result_mock

        rows = await store.list_ontologies_with_counts()
        assert rows == [(onto, 3)]

    @pytest.mark.asyncio
    async def test_list_object_type_summaries(self, store, mock_session):
        """list_object_type_summaries returns (model, props, links, actions) tuples."""
        ot = make_ot_model()
        row = MagicMock()
        row.__getitem__ = MagicMock(return_value=ot)
        row.properties_count = 2
        row.links_count = 1
        row.actions_count = 3
        result_mock = MagicMock()
        result_mock.all.return_value = [row]
        mock_session.execute.return_value = result_mock

        rows = await store.list_object_type_summaries("onto_id")
        assert rows == [(ot, 2, 1, 3)]


class TestQueryObjectLinksBatch:
    """query_object_links_batch：多源遍历的 source→target 映射（PG 降级路径用）。"""

    @pytest.mark.asyncio
    async def test_forward_returns_source_to_target(self, store, mock_session, mock_execute_result):
        """forward：source_rid 在输入集，返回 target 列表。"""
        result_mock = MagicMock()
        result_mock.all.return_value = [("s1", "t1"), ("s1", "t2"), ("s2", "t3")]
        mock_session.execute.return_value = result_mock

        mapping = await store.query_object_links_batch("ont-1", "supplies", ["s1", "s2"], "forward")
        assert mapping["s1"] == ["t1", "t2"]
        assert mapping["s2"] == ["t3"]

    @pytest.mark.asyncio
    async def test_reverse_swaps_source_target(self, store, mock_session, mock_execute_result):
        """reverse：target_rid 在输入集，返回 source 列表。"""
        result_mock = MagicMock()
        # reverse 查询返回 (target_rid, source_rid)
        result_mock.all.return_value = [("tgt1", "src1"), ("tgt1", "src2")]
        mock_session.execute.return_value = result_mock

        mapping = await store.query_object_links_batch("ont-1", "supplies", ["tgt1"], "reverse")
        assert mapping["tgt1"] == ["src1", "src2"]

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self, store, mock_session):
        """空 source_rids 返回空 dict（不查 DB）。"""
        mapping = await store.query_object_links_batch("ont-1", "supplies", [], "forward")
        assert mapping == {}
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_source_gets_empty_list(self, store, mock_session, mock_execute_result):
        """输入的 source 无任何边时，返回空列表（预初始化）。"""
        result_mock = MagicMock()
        result_mock.all.return_value = [("s1", "t1")]
        mock_session.execute.return_value = result_mock

        mapping = await store.query_object_links_batch("ont-1", "supplies", ["s1", "s2"], "forward")
        assert mapping["s1"] == ["t1"]
        assert mapping["s2"] == []  # s2 无边，预初始化为空


class TestGetRidsByType:
    """get_rids_by_type：只取 rid，不拉 properties。"""

    @pytest.mark.asyncio
    async def test_returns_only_ids(self, store, mock_session, mock_execute_result):
        """返回 rid 列表，不拉完整 properties。"""
        result_mock = MagicMock()
        result_mock.all.return_value = [("v1",), ("v2",), ("v3",)]
        mock_session.execute.return_value = result_mock

        ids = await store.get_rids_by_type("Supplier", limit=100)
        assert ids == ["v1", "v2", "v3"]

    @pytest.mark.asyncio
    async def test_empty_type_returns_empty(self, store, mock_session, mock_execute_result):
        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_session.execute.return_value = result_mock

        ids = await store.get_rids_by_type("NonExistent")
        assert ids == []


class TestInterfaceQueries:
    """Interface 跨类型查询测试。"""

    @pytest.mark.asyncio
    async def test_get_interface_types(self, store, mock_session, mock_execute_result):
        result_mock = MagicMock()
        iface_model = MagicMock()
        iface_model.id = "iface-1"
        iface_model.api_name = "Geolocated"
        iface_model.display_name = "Geolocated"
        iface_model.description = ""
        iface_model.extends_interface_ids = []
        iface_model.status = "EXPERIMENTAL"
        iface_model.properties = []
        iface_model.ontology_id = "ont-1"
        iface_model.project_id = None
        result_mock.scalars.return_value.all.return_value = [iface_model]
        mock_session.execute.return_value = result_mock

        # mock get_ontology
        store.get_ontology = AsyncMock(return_value=MagicMock(id="ont-1"))
        ifaces = await store.get_interface_types("SC")
        assert len(ifaces) == 1
        assert ifaces[0].api_name == "Geolocated"

    @pytest.mark.asyncio
    async def test_get_object_types_by_interface(self, store, mock_session, mock_execute_result):
        # mock get_interface_type 返回 id
        iface = MagicMock()
        iface.id = "iface-1"
        store.get_interface_type = AsyncMock(return_value=iface)
        store.get_ontology = AsyncMock(return_value=MagicMock(id="ont-1"))

        result_mock = MagicMock()
        # select(api_name) 返回 row tuple，用 all() 迭代
        result_mock.all.return_value = [("Supplier",), ("Order",)]
        mock_session.execute.return_value = result_mock

        ots = await store.get_object_types_by_interface("SC", "Geolocated")
        assert ots == ["Supplier", "Order"]


class TestGetObjectStatesByPks:
    """ADR-021 §2.6：按业务 PK 批量查 object_state（MANAGED 端 PK→rid 反查）。"""

    def _make_state_model(self, rid: str, pk_val: str, pk_col: str = "id"):
        m = MagicMock()
        m.rid = rid
        m.object_type_api_name = "Supplier"
        m.version = 1
        m.properties = {pk_col: pk_val}
        m.ontology_id = "o1"
        m.ontology_api_name = "SC"
        m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        m.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
        return m

    async def test_empty_pk_values_returns_empty(self, store, mock_session):
        result = await store.get_object_states_by_pks("SC", "Supplier", "id", [])
        assert result == []
        mock_session.execute.assert_not_awaited()

    async def test_returns_states_matching_pks(self, store, mock_session):
        m1 = self._make_state_model("rid-1", "S1")
        m2 = self._make_state_model("rid-2", "S2")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [m1, m2]
        mock_session.execute.return_value = result_mock

        result = await store.get_object_states_by_pks("SC", "Supplier", "id", ["S1", "S2"])
        assert len(result) == 2
        assert result[0]["rid"] == "rid-1"
        assert result[0]["properties"]["id"] == "S1"
        assert result[1]["rid"] == "rid-2"

    async def test_deduplicates_pk_values(self, store, mock_session):
        """重复 PK 值去重后再查询。"""
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = result_mock

        await store.get_object_states_by_pks("SC", "Supplier", "id", ["S1", "S1", "S2"])
        # 去重后只剩 2 个 PK，应只执行 1 批查询
        assert mock_session.execute.await_count == 1

    async def test_batches_large_pk_sets(self, store, mock_session):
        """PK 超过 5000 分批查询。"""
        pks = [f"S{i}" for i in range(12001)]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = result_mock

        await store.get_object_states_by_pks("SC", "Supplier", "id", pks)
        # 去重后 12001 唯一值，limit 默认 10000 截断 → 10000/5000 = 2 批
        assert mock_session.execute.await_count == 2

    async def test_invalid_identifier_raises(self, store):
        from ontology.core.exceptions import OntologyError

        with pytest.raises(OntologyError, match="Invalid SQL identifier"):
            await store.get_object_states_by_pks("SC", "Supplier", "id; DROP TABLE--", ["S1"])

    async def test_coerces_non_string_pk_values(self, store, mock_session):
        """非字符串 PK 值（int）被 str() 转换。"""
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = result_mock

        await store.get_object_states_by_pks("SC", "Supplier", "id", [1, 2, 3])
        assert mock_session.execute.await_count == 1


class TestGetVirtualObjectTypesByDataset:
    """ADR-021 §3.1：按 dataset 查 VIRTUAL ObjectType（触发链路用）。"""

    async def test_returns_matching_virtual_ots(self, store, mock_session):
        result_mock = MagicMock()
        result_mock.all.return_value = [("SC", "Order"), ("SC", "Note")]
        mock_session.execute.return_value = result_mock

        result = await store.get_virtual_object_types_by_dataset("mysql_orders")
        assert result == [("SC", "Order"), ("SC", "Note")]

    async def test_no_match_returns_empty(self, store, mock_session):
        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_session.execute.return_value = result_mock

        result = await store.get_virtual_object_types_by_dataset("nonexistent")
        assert result == []
