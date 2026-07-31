"""Unit tests for SQLAlchemy ORM models.

Tests model constraints, relationships, and default values without
connecting to a real database — we validate model definitions via
table reflection on an in-memory SQLite engine.
"""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from ontology.core.models.ontology import (
    Base,
    BranchModel,
    OntologyModel,
)
from ontology.core.models.permission import ProjectModel, SpaceModel  # noqa: F401  # kept for future FK-enforcing tests


def _dummy_project_id() -> str:
    """A dummy project_id for ORM tests (SQLite doesn't enforce FKs by default)."""
    return "00000000000000000000000000000001"


@pytest.fixture
def in_memory_engine():
    """Create an in-memory SQLite engine and create all tables."""
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(in_memory_engine):
    """Create a session backed by in-memory SQLite."""
    with Session(in_memory_engine) as s:
        yield s


class TestOntologyModel:
    """ORM model definition constraints."""

    def test_table_exists(self, in_memory_engine):
        """Verify the ontologies table was created."""
        inspector = inspect(in_memory_engine)
        table_names = inspector.get_table_names()
        assert "ontologies" in table_names

    def test_columns_exist(self, in_memory_engine):
        """Verify expected columns on the ontologies table."""
        inspector = inspect(in_memory_engine)
        columns = {col["name"]: col for col in inspector.get_columns("ontologies")}
        assert "id" in columns
        assert "api_name" in columns
        assert "display_name" in columns
        assert "description" in columns
        assert "rid" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_id_primary_key(self, in_memory_engine):
        """Verify id is the primary key."""
        inspector = inspect(in_memory_engine)
        pk = inspector.get_pk_constraint("ontologies")
        assert pk["constrained_columns"] == ["id"]

    def test_api_name_unique_constraint(self, in_memory_engine):
        """Verify api_name has a unique constraint."""
        inspector = inspect(in_memory_engine)
        indexes = inspector.get_indexes("ontologies")
        api_name_indexes = [i for i in indexes if "api_name" in i["column_names"]]
        unique_indexes = [i for i in api_name_indexes if i.get("unique")]
        assert len(unique_indexes) == 1

    def test_create_and_read(self, session):
        """Verify creating an OntologyModel and reading it back."""
        model = OntologyModel(
            api_name="test_ontology",
            display_name="Test Ontology",
            description="A test ontology",
        )
        session.add(model)
        session.commit()

        retrieved = session.get(OntologyModel, model.id)
        assert retrieved is not None
        assert retrieved.api_name == "test_ontology"
        assert retrieved.display_name == "Test Ontology"
        assert retrieved.description == "A test ontology"

    def test_auto_generated_id(self, session):
        """Verify id is auto-generated as UUID hex."""
        model = OntologyModel(api_name="auto_id", display_name="Auto ID")
        session.add(model)
        session.commit()

        assert model.id is not None
        assert len(model.id) == 32  # uuid4().hex = 32 chars
        assert all(c in "0123456789abcdef" for c in model.id)

    def test_timestamps(self, session):
        """Verify created_at and updated_at are set."""
        model = OntologyModel(api_name="timestamps", display_name="Timestamps")
        session.add(model)
        session.commit()

        assert model.created_at is not None
        assert model.updated_at is not None

    def test_created_at_not_updated_on_fetch(self, session):
        """Verify created_at remains stable across fetches."""
        model = OntologyModel(api_name="stable_created", display_name="Stable Created")
        session.add(model)
        session.commit()

        created_at = model.created_at

        # Fetch and verify
        retrieved = session.get(OntologyModel, model.id)
        assert retrieved.created_at == created_at

    def test_branch_relationship(self, session):
        """Verify Ontology → Branch cascade relationship."""
        onto = OntologyModel(api_name="rel_test", display_name="Relation Test")
        branch = BranchModel(name="dev", is_main=False, status="ACTIVE")
        onto.branches.append(branch)
        session.add(onto)
        session.commit()

        retrieved = session.get(OntologyModel, onto.id)
        assert len(retrieved.branches) == 1
        assert retrieved.branches[0].name == "dev"

    def test_cascade_delete(self, session):
        """Verify deleting an Ontology cascades to its Branches."""
        onto = OntologyModel(api_name="cascade_test", display_name="Cascade Test")
        onto.branches.append(BranchModel(name="dev", is_main=False, status="ACTIVE"))
        session.add(onto)
        session.commit()

        onto_id = onto.id
        branch_id = onto.branches[0].id

        session.delete(onto)
        session.commit()

        # Ontology should be gone
        assert session.get(OntologyModel, onto_id) is None
        # Branch should also be gone (cascade)
        assert session.get(BranchModel, branch_id) is None


class TestObjectTypeModel:
    """ObjectType model constraints."""

    def test_tables_exist(self, in_memory_engine):
        """Verify related tables exist."""
        inspector = inspect(in_memory_engine)
        tables = inspector.get_table_names()
        assert "object_types" in tables
        assert "properties" in tables
        assert "link_types" in tables
        assert "action_types" in tables

    def test_object_type_defaults(self, session):
        """Verify default values on ObjectType."""
        onto = OntologyModel(api_name="ot_defaults", display_name="OT Defaults")
        session.add(onto)
        session.flush()

        from ontology.core.models.ontology import ObjectTypeModel
        project_id = _dummy_project_id()

        ot = ObjectTypeModel(
            ontology_id=onto.id,
            api_name="test_type",
            display_name="Test Type",
            primary_key="id",
            title_property="name",
            project_id=project_id,
            storage_type="MANAGED",
        )
        session.add(ot)
        session.commit()

        assert ot.visibility == "NORMAL"
        assert ot.status == "ACTIVE"

    def test_property_relationship(self, session):
        """Verify ObjectType → Property cascade."""
        from ontology.core.models.ontology import ObjectTypeModel, PropertyDefModel

        onto = OntologyModel(api_name="prop_rel", display_name="Prop Relation")
        session.add(onto)
        session.flush()
        project_id = _dummy_project_id()

        ot = ObjectTypeModel(
            ontology_id=onto.id,
            api_name="with_props",
            display_name="With Props",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            project_id=project_id,
        )
        prop = PropertyDefModel(
            object_type_id=ot.id,
            api_name="name",
            display_name="Name",
            data_type="STRING",
                project_id="00000000000000000000000000000001",
            is_primary_key=False,
        )
        ot.properties.append(prop)
        session.add(ot)
        session.commit()

        retrieved = session.get(ObjectTypeModel, ot.id)
        assert len(retrieved.properties) == 1
        assert retrieved.properties[0].api_name == "name"


class TestSchemaPydantic:
    """pydantic schema validation."""

    def test_ontology_create_valid(self):
        """Valid OntologyCreate passes validation."""
        from ontology.core.schemas.ontology import OntologyCreate

        data = OntologyCreate(api_name="MyOntology", display_name="My Ontology")
        assert data.api_name == "MyOntology"
        assert data.display_name == "My Ontology"

    def test_ontology_create_invalid_api_name(self):
        """api_name must match pattern (PascalCase, no lowercase-first)."""
        from ontology.core.schemas.ontology import OntologyCreate

        with pytest.raises(Exception):
            OntologyCreate(api_name="myOntology", display_name="Bad")  # starts lowercase

    def test_ontology_create_default_description(self):
        """description defaults to empty string."""
        from ontology.core.schemas.ontology import OntologyCreate

        data = OntologyCreate(api_name="Test", display_name="Test")
        assert data.description == ""

    def test_ontology_from_attributes(self):
        """Ontology schema can be created from ORM-like dict."""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import Ontology

        data = Ontology(
            id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            api_name="test",
            display_name="Test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert data.model_dump()["api_name"] == "test"

    def test_data_type_enum_values(self):
        """Verify all DataType enum values are accessible."""
        from ontology.core.schemas.ontology import DataType

        assert DataType.STRING.value == "STRING"
        assert DataType.INTEGER.value == "INTEGER"
        assert DataType.VECTOR.value == "VECTOR"
        assert DataType.GEOPOINT.value == "GEOPOINT"
