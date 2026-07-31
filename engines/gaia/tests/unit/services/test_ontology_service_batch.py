"""Integration tests for OntologyService batch operations using real DB.

Covers: define_object_type_batch, update_object_type_batch.
Uses in-memory SQLite.

v6 apiName: ObjectType api_name is caller-supplied (PascalCase); Property
api_names are derived by the service (camelCase); Link api_name is
caller-supplied (camelCase, AI-assisted) or derived when omitted.
primary_key / title_property are resolved from property is_primary_key /
is_title_property flags (Q2) when omitted.
"""

from unittest.mock import AsyncMock

import pytest

from ontology.config.settings import settings
from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.models.ontology import OntologyModel
from ontology.core.models.permission import ProjectModel, SpaceModel
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService


@pytest.fixture
async def service(db_session):
    metadata = PostgresMetaStore(db_session)
    catalog = AsyncMock(spec=GravitinoRegistry)
    index = AsyncMock(spec=DorisIndexStore)
    return OntologyService(metadata=metadata, catalog=catalog, index=index)


async def _create_ontology(service, api_name: str = "Hr", display_name: str = "HR"):
    """Create an Ontology bound to a Space + Project (option A requirement).

    Ontology↔Space 1:1 requires: create Ontology → create Space (ontology_id)
    → set Ontology.space_id → create default Project under the Space.
    """
    from ontology.core.models.defaults import new_uuid, utcnow

    session = service._metadata.session  # noqa: SLF001
    ont = OntologyModel(
        id=new_uuid(),
        api_name=api_name,
        display_name=display_name,
        status="ACTIVE",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(ont)
    await session.flush()
    space = SpaceModel(
        id=new_uuid(),
        api_name=api_name,
        display_name=display_name,
        ontology_id=ont.id,
    )
    session.add(space)
    await session.flush()
    ont.space_id = space.id
    project = ProjectModel(
        id=new_uuid(),
        api_name="default",
        display_name="Default",
        space_id=space.id,
    )
    session.add(project)
    await session.commit()
    from ontology.core.schemas.ontology import Ontology

    return Ontology.model_validate(ont)


class TestBatchCreate:
    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED batch 需 catalog/dataset Layer（lite guard 拦截）",
    )
    @pytest.mark.asyncio
    async def test_define_object_type_batch(self, service):
        """Create an ObjectType with properties and links atomically.

        ObjectType api_name is caller-supplied (PascalCase); property api_names
        are derived camelCase from display_name/backing_column; primary_key /
        title_property are derived from property flags (Q2).
        """
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeCreate

        dept = await service.define_object_type(
            "Hr",
            ObjectTypeCreate(
                api_name="Department",
                display_name="Department",
                primary_key="deptId",
                title_property="deptId",
                storage_type="MANAGED",
            ),
        )

        from ontology.core.schemas.ontology import (
            LinkInput,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            description="An employee",
            storage_type="MANAGED",
            properties=[
                PropertyInput(display_name="Name", data_type="STRING", is_title_property=True),
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
                PropertyInput(display_name="Age", data_type="INTEGER"),
            ],
            links=[
                LinkInput(
                    display_name="Belongs To",
                    target_object_type_id=dept.id,
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
            ],
        )
        result = await service.define_object_type_batch("Hr", data)
        assert result.api_name == "Employee"
        # primary_key derived from the is_primary_key property → "employeeId".
        assert result.primary_key == "employeeId"
        # title_property derived from the is_title_property property → "name".
        assert result.title_property == "name"

        props = await service._metadata.get_properties(result.id)
        prop_names = {p.api_name for p in props}
        assert prop_names == {"name", "employeeId", "age"}

    @pytest.mark.asyncio
    async def test_define_object_type_batch_duplicate(self, service):
        """Batch create with duplicate api_name raises ConflictError (user-typed duplicate)."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                )
            ],
        )
        await service.define_object_type_batch("Hr", data)
        with pytest.raises(ConflictError, match="already exists"):
            await service.define_object_type_batch("Hr", data)

    @pytest.mark.asyncio
    async def test_define_object_type_batch_ontology_not_found(self, service):
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                )
            ],
        )
        with pytest.raises(NotFoundError):
            await service.define_object_type_batch("nonexistent", data)

    @pytest.mark.asyncio
    async def test_define_object_type_batch_no_primary_key_raises(self, service):
        """No properties and no explicit primary_key → ValidationError (cannot derive PK)."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeBatchCreate

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[],
        )
        with pytest.raises(ValidationError, match="primary_key"):
            await service.define_object_type_batch("Hr", data)

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED batch 需 catalog/dataset Layer（lite 抛 EditionUnavailableError）",
    )
    @pytest.mark.asyncio
    async def test_define_batch_managed_creates_dataset_governance(self, service):
        """MANAGED batch create writes a PG datasets governance record (kind=MANAGED)."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            storage_type="MANAGED",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
                PropertyInput(display_name="Name", data_type="STRING"),
            ],
        )
        await service.define_object_type_batch("Hr", data)

        ds = await service._metadata.get_dataset("employee")
        assert ds.api_name == "employee"
        assert ds.kind == "MANAGED"
        assert ds.is_view is False
        assert "employee" in ds.storage_location

        # The Iceberg physical table registered via Gravitino MUST use the same
        # camelCase dataset api_name (employee) as the governance record —
        # Iceberg table name == dataset api_name (per naming.py). A PascalCase
        # table name (Employee) would be unreachable from the schema/query
        # paths, which resolve the table by dataset api_name.
        service._catalog.register_dataset.assert_awaited_once()
        reg_kwargs = service._catalog.register_dataset.await_args.kwargs
        assert reg_kwargs["name"] == "employee"
        assert reg_kwargs["location"] == "s3://ontology-warehouse/employee"

    @pytest.mark.asyncio
    async def test_define_batch_managed_backfills_property_backing_mapping(self, service):
        """MANAGED batch create backfills backing_mapping on properties that have none,
        pointing them at the object's own MANAGED dataset (iceberg.ontology.<ot>, column=prop api_name)."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            storage_type="MANAGED",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
                PropertyInput(display_name="Name", data_type="STRING"),
            ],
        )
        result = await service.define_object_type_batch("Hr", data)

        props = await service._metadata.get_properties(result.id)
        by_name = {p.api_name: p for p in props}
        for prop_name, prop in by_name.items():
            assert prop.backing_mapping is not None, f"{prop_name} missing backing_mapping"
            bm = prop.backing_mapping
            assert bm.dataset_api_name == "employee", f"{prop_name} wrong dataset_api_name"
            assert bm.backing_catalog == "iceberg", f"{prop_name} wrong catalog"
            assert bm.backing_schema == "ontology", f"{prop_name} wrong schema"
            assert bm.backing_table == "employee", f"{prop_name} wrong table"
            assert bm.backing_column == prop_name, f"{prop_name} wrong column"

    @pytest.mark.asyncio
    async def test_define_batch_managed_preserves_explicit_backing_mapping(self, service):
        """MANAGED batch create must NOT overwrite an explicit backing_mapping supplied by the caller."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import (
            BackingColumnRef,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            storage_type="MANAGED",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                    backing_mapping=BackingColumnRef(
                        dataset_api_name="legacyEmployees",
                        backing_catalog="other",
                        backing_schema="public",
                        backing_table="employees",
                        backing_column="emp_id",
                    ),
                ),
                PropertyInput(display_name="Name", data_type="STRING"),
            ],
        )
        result = await service.define_object_type_batch("Hr", data)

        props = await service._metadata.get_properties(result.id)
        by_name = {p.api_name: p for p in props}
        # Explicit mapping preserved untouched.
        explicit = by_name["employeeId"]
        assert explicit.backing_mapping is not None
        assert explicit.backing_mapping.dataset_api_name == "legacyEmployees"
        assert explicit.backing_mapping.backing_column == "emp_id"
        # The other property still gets the auto backfill.
        auto = by_name["name"]
        assert auto.backing_mapping is not None
        assert auto.backing_mapping.dataset_api_name == "employee"
        assert auto.backing_mapping.backing_column == "name"

    @pytest.mark.asyncio
    async def test_define_batch_virtual_skips_dataset_governance_and_backfill(self, service):
        """VIRTUAL batch create writes no datasets governance record and does not backfill backing_mapping."""
        await _create_ontology(service)

        from ontology.core.exceptions import NotFoundError
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            storage_type="VIRTUAL",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
            ],
        )
        result = await service.define_object_type_batch("Hr", data)

        with pytest.raises(NotFoundError):
            await service._metadata.get_dataset("employee")
        props = await service._metadata.get_properties(result.id)
        for p in props:
            assert p.backing_mapping is None

    @pytest.mark.asyncio
    async def test_define_batch_managed_idempotent_dataset(self, service):
        """Repeated MANAGED batch create with the same api_name returns the same dataset record
        (create_dataset is idempotent, does not 409)."""
        await _create_ontology(service)

        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            storage_type="MANAGED",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
            ],
        )
        await service.define_object_type_batch("Hr", data)
        ds1 = await service._metadata.get_dataset("employee")

        # Second create of the *dataset* (simulate the idempotent governance write)
        # by re-registering the dataset explicitly.
        from ontology.core.schemas.datasource import DatasetGovernanceCreate

        ds2 = await service._metadata.create_dataset(
            DatasetGovernanceCreate(
                api_name="employee",
                display_name="Employee",
                storage_location="s3://ontology-warehouse/employee",
                kind="MANAGED",
            )
        )
        assert ds2.id == ds1.id


class TestBatchUpdate:
    @pytest.mark.asyncio
    async def test_update_object_type_batch(self, service):
        """Update an ObjectType's properties atomically.

        Property api_names are re-derived on update (delete & recreate).
        """
        await _create_ontology(service)

        from ontology.core.schemas.ontology import (
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        created = await service.define_object_type_batch(
            "Hr",
            ObjectTypeBatchCreate(
                api_name="Employee",
                display_name="Employee",
                storage_type="MANAGED",
                properties=[
                    PropertyInput(
                        display_name="Employee ID",
                        data_type="INTEGER",
                        is_primary_key=True,
                    ),
                    PropertyInput(display_name="Name", data_type="STRING"),
                ],
            ),
        )
        ot_api_name = created.api_name  # "Employee"

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee v2",
            description="Updated description",
            properties=[
                PropertyInput(
                    display_name="Employee ID",
                    data_type="INTEGER",
                    is_primary_key=True,
                ),
                PropertyInput(
                    display_name="Full Name",
                    data_type="STRING",
                    is_title_property=True,
                ),
                PropertyInput(display_name="Email", data_type="STRING"),
            ],
        )
        result = await service.update_object_type_batch("Hr", ot_api_name, data)
        assert result.display_name == "Employee v2"
        assert result.api_name == ot_api_name
        assert result.primary_key == "employeeId"
        assert result.title_property == "fullName"

        props = await service._metadata.get_properties(result.id)
        prop_names = {p.api_name for p in props}
        assert prop_names == {"employeeId", "fullName", "email"}
        assert "name" not in prop_names

    @pytest.mark.asyncio
    async def test_update_object_type_batch_not_found(self, service):
        await _create_ontology(service)
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Ghost",
            display_name="Ghost",
            properties=[PropertyInput(display_name="Ghost ID", data_type="INTEGER", is_primary_key=True)],
        )
        with pytest.raises(NotFoundError):
            await service.update_object_type_batch("Hr", "Ghost", data)


class TestLinkApiName:
    """Link api_name is caller-supplied (camelCase) or derived.

    Mirrors the ObjectType/Action pattern: callers submit displayName +
    apiName together (AI-assisted + user-editable); the service validates
    pattern + uniqueness and does NOT re-derive when api_name is given.
    """

    @pytest.mark.asyncio
    async def test_batch_link_uses_submitted_api_name(self, service):
        """Explicit api_name on LinkInput is adopted as-is."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import (
            LinkInput,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[PropertyInput(display_name="Employee ID", data_type="INTEGER", is_primary_key=True)],
            links=[
                LinkInput(
                    display_name="所属部门",  # Chinese — would fall back to linkType0 without api_name
                    api_name="belongsToDept",
                    target_object_type_id="dept-uuid",
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
            ],
        )
        await service.define_object_type_batch("Hr", data)
        links = await service._metadata.get_link_types("Hr")
        assert len(links) == 1
        assert links[0].api_name == "belongsToDept"
        assert links[0].display_name == "所属部门"

    @pytest.mark.asyncio
    async def test_batch_link_derives_when_api_name_omitted(self, service):
        """Omitted api_name falls back to derivation (backward compatible)."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import (
            LinkInput,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[PropertyInput(display_name="Employee ID", data_type="INTEGER", is_primary_key=True)],
            links=[
                LinkInput(
                    display_name="Belongs To Dept",  # ASCII — derives to belongsToDept
                    target_object_type_id="dept-uuid",
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
            ],
        )
        await service.define_object_type_batch("Hr", data)
        links = await service._metadata.get_link_types("Hr")
        assert links[0].api_name == "belongsToDept"

    @pytest.mark.asyncio
    async def test_batch_link_duplicate_api_name_raises_conflict(self, service):
        """A user-typed duplicate api_name is a ConflictError (not auto-suffixed)."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import (
            LinkInput,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[PropertyInput(display_name="Employee ID", data_type="INTEGER", is_primary_key=True)],
            links=[
                LinkInput(
                    display_name="部门A",
                    api_name="belongsToDept",
                    target_object_type_id="dept-a",
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
                LinkInput(
                    display_name="部门B",
                    api_name="belongsToDept",  # duplicate
                    target_object_type_id="dept-b",
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
            ],
        )
        with pytest.raises(ConflictError):
            await service.define_object_type_batch("Hr", data)

    @pytest.mark.asyncio
    async def test_batch_link_bad_pattern_raises_validation(self, service):
        """A submitted api_name violating the camelCase pattern is a ValidationError."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import (
            LinkInput,
            ObjectTypeBatchCreate,
            PropertyInput,
        )

        data = ObjectTypeBatchCreate(
            api_name="Employee",
            display_name="Employee",
            properties=[PropertyInput(display_name="Employee ID", data_type="INTEGER", is_primary_key=True)],
            links=[
                LinkInput(
                    display_name="部门",
                    api_name="BelongsToDept",  # PascalCase — violates camelCase pattern
                    target_object_type_id="dept-uuid",
                    cardinality="MANY",
                    direction="OUTGOING",
                ),
            ],
        )
        with pytest.raises(ValidationError):
            await service.define_object_type_batch("Hr", data)

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED link 定义需 catalog Layer（lite guard 拦截）",
    )
    @pytest.mark.asyncio
    async def test_define_link_type_uses_submitted_api_name(self, service):
        """Single link creation (define_link_type) also honors submitted api_name."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import LinkTypeDefCreate, ObjectTypeCreate

        dept = await service.define_object_type(
            "Hr",
            ObjectTypeCreate(
                api_name="Department",
                display_name="Department",
                primary_key="deptId",
                title_property="deptId",
                storage_type="MANAGED",
            ),
        )
        emp = await service.define_object_type(
            "Hr",
            ObjectTypeCreate(
                api_name="Employee",
                display_name="Employee",
                primary_key="empId",
                title_property="empId",
                storage_type="MANAGED",
            ),
        )
        link_def = LinkTypeDefCreate(
            display_name="所属部门",
            api_name="belongsToDept",
            source_object_type_id=emp.id,
            target_object_type_id=dept.id,
            cardinality="MANY",
            direction="OUTGOING",
        )
        result = await service.define_link_type("Hr", link_def)
        assert result.api_name == "belongsToDept"
        assert result.display_name == "所属部门"


class TestPropertyApiName:
    """Property api_name is caller-supplied (camelCase) or derived.

    Same caller-supplied-vs-derived contract as Link/ObjectType/Action:
    covers the no-backing-column case (MVP master data whose displayName is
    Chinese — without a submitted api_name these would fall back to propertyN).
    """

    @pytest.mark.asyncio
    async def test_batch_property_uses_submitted_api_name(self, service):
        """Explicit api_name on PropertyInput is adopted as-is."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Capability",
            display_name="Capability",
            properties=[
                PropertyInput(
                    display_name="能力名称",  # Chinese — would fall back to property0
                    api_name="capabilityName",
                    data_type="STRING",
                    is_primary_key=True,
                ),
                PropertyInput(
                    display_name="能力类型",
                    api_name="capabilityType",
                    data_type="STRING",
                ),
            ],
        )
        await service.define_object_type_batch("Hr", data)
        props = await service._metadata.get_properties((await service._metadata.get_object_type("Hr", "Capability")).id)
        names = {p.api_name for p in props}
        assert names == {"capabilityName", "capabilityType"}

    @pytest.mark.asyncio
    async def test_batch_property_derives_when_api_name_omitted(self, service):
        """Omitted api_name falls back to derivation (backward compatible)."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Capability",
            display_name="Capability",
            properties=[
                PropertyInput(
                    display_name="Capability Name",  # ASCII — derives to capabilityName
                    data_type="STRING",
                    is_primary_key=True,
                ),
            ],
        )
        await service.define_object_type_batch("Hr", data)
        props = await service._metadata.get_properties((await service._metadata.get_object_type("Hr", "Capability")).id)
        assert props[0].api_name == "capabilityName"

    @pytest.mark.asyncio
    async def test_batch_property_duplicate_api_name_raises_conflict(self, service):
        """A user-typed duplicate api_name is a ConflictError (not auto-suffixed)."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Capability",
            display_name="Capability",
            properties=[
                PropertyInput(display_name="名称A", api_name="name", data_type="STRING", is_primary_key=True),
                PropertyInput(display_name="名称B", api_name="name", data_type="STRING"),  # duplicate
            ],
        )
        with pytest.raises(ConflictError):
            await service.define_object_type_batch("Hr", data)

    @pytest.mark.asyncio
    async def test_batch_property_bad_pattern_raises_validation(self, service):
        """A submitted api_name violating the camelCase pattern is a ValidationError."""
        await _create_ontology(service)
        from ontology.core.schemas.ontology import ObjectTypeBatchCreate, PropertyInput

        data = ObjectTypeBatchCreate(
            api_name="Capability",
            display_name="Capability",
            properties=[
                PropertyInput(
                    display_name="名称",
                    api_name="CapabilityName",  # PascalCase — violates camelCase pattern
                    data_type="STRING",
                    is_primary_key=True,
                ),
            ],
        )
        with pytest.raises(ValidationError):
            await service.define_object_type_batch("Hr", data)
