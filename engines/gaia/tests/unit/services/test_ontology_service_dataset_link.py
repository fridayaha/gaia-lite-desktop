"""Unit tests for OntologyService.link_dataset / unlink_dataset (A1)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import NotFoundError, ValidationError
from ontology.core.schemas.datasource import DatasetGovernance
from ontology.core.schemas.ontology import (
    BackingColumnRef,
    DataType,
    ObjectType,
    PropertyDef,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService

_now = datetime.now(UTC)


@pytest.fixture
def mock_metadata() -> AsyncMock:
    m = AsyncMock(spec=PostgresMetaStore)
    m.session = MagicMock()
    m._flush_and_commit = AsyncMock()
    return m


@pytest.fixture
def service(mock_metadata: AsyncMock) -> OntologyService:
    return OntologyService(
        metadata=mock_metadata,
        catalog=AsyncMock(spec=GravitinoRegistry),
        index=AsyncMock(spec=DorisIndexStore),
    )


def _ot(storage_type: str = "MANAGED") -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="onto1",
        api_name="employee",
        display_name="Employee",
        primary_key="id",
        title_property="name",
        storage_type=storage_type,  # type: ignore[arg-type]
        properties=[
            PropertyDef(
                id="p1",
                object_type_id="ot1",
                api_name="id",
                display_name="Id",
                data_type=DataType.STRING,
                is_primary_key=True,
                created_at=_now,
                updated_at=_now,
            ),
            PropertyDef(
                id="p2",
                object_type_id="ot1",
                api_name="name",
                display_name="Name",
                data_type=DataType.STRING,
                created_at=_now,
                updated_at=_now,
            ),
        ],
        created_at=_now,
        updated_at=_now,
    )


@pytest.mark.asyncio
async def test_link_dataset_managed_writes_backing_mapping(service: OntologyService, mock_metadata: AsyncMock) -> None:
    ot = _ot("MANAGED")
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds1",
            api_name="hr_employee",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    result = await service.link_dataset(
        "hr",
        "employee",
        "hr_employee",
        [
            {"property_api_name": "id", "column_name": "emp_id"},
            {"property_api_name": "name", "column_name": "full_name"},
        ],
    )

    assert result is ot
    # Both properties written (full-mapping invariant requires all).
    assert mock_metadata.update_property_backing_mapping.await_count == 2
    # First call writes id → emp_id.
    call = mock_metadata.update_property_backing_mapping.await_args_list[0]
    assert call.args[0] == "p1"
    mapping: BackingColumnRef = call.args[1]
    assert mapping.dataset_api_name == "hr_employee"
    assert mapping.backing_catalog == "iceberg"
    assert mapping.backing_schema == "ontology"
    assert mapping.backing_table == "hr_employee"
    assert mapping.backing_column == "emp_id"


@pytest.mark.asyncio
async def test_link_dataset_virtual_parses_locator(service: OntologyService, mock_metadata: AsyncMock) -> None:
    ot = _ot("VIRTUAL")
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds2",
            api_name="hr_employee_virtual",
            kind="VIRTUAL",
            storage_location="pg_catalog.public.employees",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    await service.link_dataset(
        "hr",
        "employee",
        "hr_employee_virtual",
        [
            {"property_api_name": "id", "column_name": "emp_id"},
            {"property_api_name": "name", "column_name": "full_name"},
        ],
    )

    # Second call (name → full_name) carries the VIRTUAL locator.
    mapping: BackingColumnRef = mock_metadata.update_property_backing_mapping.await_args.args[1]
    assert mapping.backing_catalog == "pg_catalog"
    assert mapping.backing_schema == "public"
    assert mapping.backing_table == "employees"
    assert mapping.backing_column == "full_name"


@pytest.mark.asyncio
async def test_link_dataset_rejects_storage_type_mismatch(service: OntologyService, mock_metadata: AsyncMock) -> None:
    mock_metadata.get_object_type = AsyncMock(return_value=_ot("MANAGED"))
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds2",
            api_name="virtual_ds",
            kind="VIRTUAL",
            storage_location="c.s.t",
            created_at=_now,
            updated_at=_now,
        )
    )
    with pytest.raises(ValidationError, match="storage_type mismatch"):
        await service.link_dataset("hr", "employee", "virtual_ds", [])


@pytest.mark.asyncio
async def test_link_dataset_unknown_property_raises(service: OntologyService, mock_metadata: AsyncMock) -> None:
    ot = _ot("MANAGED")
    mock_metadata.get_object_type = AsyncMock(return_value=ot)
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds1",
            api_name="hr_employee",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)
    # Full mapping count, but one references a non-existent property.
    with pytest.raises(NotFoundError):
        await service.link_dataset(
            "hr",
            "employee",
            "hr_employee",
            [
                {"property_api_name": "id", "column_name": "x"},
                {"property_api_name": "nope", "column_name": "y"},
            ],
        )


@pytest.mark.asyncio
async def test_unlink_dataset_clears_all(service: OntologyService, mock_metadata: AsyncMock) -> None:
    ot = _ot("MANAGED")
    ot.properties[0].backing_mapping = BackingColumnRef(
        dataset_api_name="ds",
        backing_catalog="iceberg",
        backing_schema="ontology",
        backing_table="t",
        backing_column="c",
    )
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    await service.unlink_dataset("hr", "employee")

    assert mock_metadata.update_property_backing_mapping.await_count == 1
    assert mock_metadata.update_property_backing_mapping.await_args.args[1] is None


@pytest.mark.asyncio
async def test_unlink_dataset_selective(service: OntologyService, mock_metadata: AsyncMock) -> None:
    ot = _ot("MANAGED")
    for p in ot.properties:
        p.backing_mapping = BackingColumnRef(
            dataset_api_name="ds",
            backing_catalog="iceberg",
            backing_schema="ontology",
            backing_table="t",
            backing_column=p.api_name,
        )
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    await service.unlink_dataset("hr", "employee", property_api_names=["name"])

    assert mock_metadata.update_property_backing_mapping.await_count == 1
    assert mock_metadata.update_property_backing_mapping.await_args.args[0] == "p2"


@pytest.mark.asyncio
async def test_link_dataset_rejects_partial_mapping(
    service: OntologyService, mock_metadata: AsyncMock
) -> None:
    """Every property must be mapped; partial mappings are rejected."""
    ot = _ot("MANAGED")  # has id + name (2 properties)
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds1",
            api_name="hr_employee",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    # Only map `id`; `name` left unmapped → ValidationError.
    with pytest.raises(ValidationError, match="每个属性必须映射到源列"):
        await service.link_dataset(
            "hr",
            "employee",
            "hr_employee",
            [{"property_api_name": "id", "column_name": "emp_id"}],
        )
    # Nothing written.
    mock_metadata.update_property_backing_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_dataset_rejects_nonexistent_column(
    service: OntologyService, mock_metadata: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A column_name not in the target dataset's schema is rejected."""
    ot = _ot("MANAGED")
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds1",
            api_name="hr_employee",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    # Wire a fake container whose datasource_service returns a schema with
    # only `emp_id` and `full_name` — `bogus_col` is not in it.
    from ontology.core.schemas.dataset import ColumnDef, DatasetSchema

    fake_container = MagicMock()
    fake_ds_svc = AsyncMock()
    fake_ds_svc.get_dataset_schema = AsyncMock(
        return_value=DatasetSchema(
            columns=[
                ColumnDef(name="emp_id", type="string", nullable=True),
                ColumnDef(name="full_name", type="string", nullable=True),
            ]
        )
    )
    type(fake_container).datasource_service = property(lambda self: fake_ds_svc)
    service._container = fake_container  # type: ignore[assignment]

    with pytest.raises(ValidationError, match="在数据集.*中不存在"):
        await service.link_dataset(
            "hr",
            "employee",
            "hr_employee",
            [
                {"property_api_name": "id", "column_name": "emp_id"},
                {"property_api_name": "name", "column_name": "bogus_col"},
            ],
        )
    mock_metadata.update_property_backing_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_dataset_anchors_primary_backing_on_first_bind(
    service: OntologyService, mock_metadata: AsyncMock
) -> None:
    """First link_dataset sets ObjectType.backing_dataset_api_name (Palantir
    "backing datasource" semantics). The OT-level field is a convenience
    primary-source reference; authoritative binding stays per-property."""
    ot = _ot("MANAGED")  # backing_dataset_api_name defaults to None
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds1",
            api_name="hr_employee",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    await service.link_dataset(
        "hr",
        "employee",
        "hr_employee",
        [
            {"property_api_name": "id", "column_name": "emp_id"},
            {"property_api_name": "name", "column_name": "full_name"},
        ],
    )

    # OT-level primary backing dataset anchored on first bind.
    mock_metadata.set_object_type_backing_dataset.assert_awaited_once_with(
        "ot1", "hr_employee"
    )


@pytest.mark.asyncio
async def test_link_dataset_does_not_overwrite_existing_primary_backing(
    service: OntologyService, mock_metadata: AsyncMock
) -> None:
    """Re-binding to a *different* dataset does NOT overwrite the OT's primary
    backing_dataset_api_name. First bound = primary source (MDO: additional
    datasets are secondary, surfaced only via per-property backing_mapping)."""
    ot = _ot("MANAGED")
    ot.backing_dataset_api_name = "hr_employee"  # already anchored
    mock_metadata.get_object_type = AsyncMock(side_effect=[ot, ot])
    mock_metadata.get_dataset = AsyncMock(
        return_value=DatasetGovernance(
            id="ds2",
            api_name="hr_employee_extra",
            kind="MANAGED",
            storage_location="",
            created_at=_now,
            updated_at=_now,
        )
    )
    mock_metadata.get_properties = AsyncMock(return_value=ot.properties)

    await service.link_dataset(
        "hr",
        "employee",
        "hr_employee_extra",
        [
            {"property_api_name": "id", "column_name": "emp_id"},
            {"property_api_name": "name", "column_name": "full_name"},
        ],
    )

    # Primary backing dataset NOT overwritten — property-level mapping still
    # updated (the authoritative binding).
    mock_metadata.set_object_type_backing_dataset.assert_not_awaited()
    assert mock_metadata.update_property_backing_mapping.await_count == 2
