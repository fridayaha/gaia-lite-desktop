"""Tests for ObjectType capabilities (graph/geotime indexing opt-in, ADR-015 §capabilities).

Tests cover:
  - define_object_type with capabilities (graph/geotime enabled → provision called)
  - define_object_type without capabilities (default all-disabled → provision skipped)
  - VIRTUAL types cannot enable capabilities (Gate 1)
  - update_object_type_fields with capabilities toggle
  - enabling a capability triggers schema provisioning
  - disabling a capability does not deprovision (best-effort, idempotent)
  - ObjectTypeCapabilities None handling (pre-migration rows)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.config.settings import settings
from ontology.core.exceptions import NotFoundError, ValidationError
from ontology.core.schemas.ontology import (
    ObjectType,
    ObjectTypeCapabilities,
    ObjectTypeCreate,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    m = AsyncMock(spec=PostgresMetaStore)
    m._resolve_default_project_for_space.return_value = "project-default-id"
    return m


@pytest.fixture
def mock_catalog() -> AsyncMock:
    return AsyncMock(spec=GravitinoRegistry)


@pytest.fixture
def mock_index() -> AsyncMock:
    return AsyncMock(spec=DorisIndexStore)


def _make_ot(
    *,
    api_name: str = "order",
    storage_type: str = "MANAGED",
    capabilities: ObjectTypeCapabilities | None = None,
    links: list | None = None,
    properties: list | None = None,
) -> ObjectType:
    return ObjectType(
        id="ot123",
        ontology_id="onto1",
        api_name=api_name,
        display_name="Order",
        description="",
        primary_key="order_id",
        title_property="description",
        storage_type=storage_type,  # type: ignore[arg-type]
        visibility="NORMAL",
        status="ACTIVE",
        capabilities=capabilities or ObjectTypeCapabilities(),
        properties=properties or [],
        links=links or [],
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )


class TestCapabilitiesSchema:
    """ObjectTypeCapabilities pydantic model behavior."""

    def test_defaults_all_disabled(self):
        caps = ObjectTypeCapabilities()
        assert caps.graph_indexing_enabled is False
        assert caps.geotime_indexing_enabled is False

    def test_none_becomes_empty(self):
        """None (null column / pre-migration) → all-disabled default."""
        caps = ObjectTypeCapabilities.model_validate(None)
        assert caps.graph_indexing_enabled is False
        assert caps.geotime_indexing_enabled is False

    def test_from_dict(self):
        caps = ObjectTypeCapabilities.model_validate(
            {"graph_indexing_enabled": True, "geotime_indexing_enabled": False}
        )
        assert caps.graph_indexing_enabled is True
        assert caps.geotime_indexing_enabled is False

    def test_partial_dict_defaults_missing_keys(self):
        caps = ObjectTypeCapabilities.model_validate({"graph_indexing_enabled": True})
        assert caps.graph_indexing_enabled is True
        assert caps.geotime_indexing_enabled is False


class TestDefineObjectTypeWithCapabilities:
    """define_object_type respects capabilities gates."""

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED + graph schema provisioning（lite 无 graph/catalog Layer）",
    )
    @pytest.mark.asyncio
    async def test_graph_enabled_triggers_graph_schema(self, mock_metadata, mock_catalog, mock_index):
        """When graph_indexing_enabled=True, _provision_graph_schema is called."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        # Spy on _provision_graph_schema
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        mock_metadata.get_ontology.return_value = MagicMock(id="onto1", space_id=None)
        mock_metadata.create_object_type.return_value = _make_ot(
            capabilities=ObjectTypeCapabilities(graph_indexing_enabled=True)
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "order")

        await service.define_object_type(
            "hr",
            ObjectTypeCreate(
                api_name="Order",
                display_name="Order",
                primary_key="order_id",
                storage_type="MANAGED",
                capabilities=ObjectTypeCapabilities(graph_indexing_enabled=True),
            ),
        )

        service._provision_graph_schema.assert_awaited_once()
        service._provision_geotime_schema.assert_not_awaited()

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED + geotime schema provisioning（lite 无 geotime/catalog Layer）",
    )
    @pytest.mark.asyncio
    async def test_geotime_enabled_triggers_geotime_schema(self, mock_metadata, mock_catalog, mock_index):
        """When geotime_indexing_enabled=True, _provision_geotime_schema is called."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        mock_metadata.get_ontology.return_value = MagicMock(id="onto1", space_id=None)
        mock_metadata.create_object_type.return_value = _make_ot(
            capabilities=ObjectTypeCapabilities(geotime_indexing_enabled=True)
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "order")

        await service.define_object_type(
            "hr",
            ObjectTypeCreate(
                api_name="Order",
                display_name="Order",
                primary_key="order_id",
                storage_type="MANAGED",
                capabilities=ObjectTypeCapabilities(geotime_indexing_enabled=True),
            ),
        )

        service._provision_geotime_schema.assert_awaited_once()
        service._provision_graph_schema.assert_not_awaited()

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED ObjectType 需 catalog/index Layer（lite guard 拦截）",
    )
    @pytest.mark.asyncio
    async def test_both_disabled_skips_graph_and_geotime(self, mock_metadata, mock_catalog, mock_index):
        """When both capabilities are False, neither schema provision is called."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        mock_metadata.get_ontology.return_value = MagicMock(id="onto1", space_id=None)
        mock_metadata.create_object_type.return_value = _make_ot()
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "order")

        await service.define_object_type(
            "hr",
            ObjectTypeCreate(
                api_name="Order",
                display_name="Order",
                primary_key="order_id",
                storage_type="MANAGED",
            ),
        )

        service._provision_graph_schema.assert_not_awaited()
        service._provision_geotime_schema.assert_not_awaited()


class TestUpdateCapabilities:
    """update_object_type_fields with capabilities key."""

    @pytest.mark.asyncio
    async def test_enable_graph_indexing_triggers_provision(self, mock_metadata, mock_catalog, mock_index):
        """Enabling graph_indexing_enabled triggers _provision_graph_schema."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        old_ot = _make_ot(capabilities=ObjectTypeCapabilities())
        new_ot = _make_ot(capabilities=ObjectTypeCapabilities(graph_indexing_enabled=True))
        mock_metadata.get_object_type.return_value = old_ot
        mock_metadata.get_properties.return_value = []
        mock_metadata.update_object_type.return_value = new_ot

        await service.update_object_type_fields(
            "hr",
            "order",
            {"capabilities": {"graph_indexing_enabled": True, "geotime_indexing_enabled": False}},
        )

        service._provision_graph_schema.assert_awaited_once()
        service._provision_geotime_schema.assert_not_awaited()
        # Verify the capabilities dict was persisted
        update_call = mock_metadata.update_object_type.await_args
        assert update_call.args[1] == {
            "capabilities": {"graph_indexing_enabled": True, "geotime_indexing_enabled": False}
        }

    @pytest.mark.asyncio
    async def test_enable_geotime_indexing_triggers_provision(self, mock_metadata, mock_catalog, mock_index):
        """Enabling geotime_indexing_enabled triggers _provision_geotime_schema."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        old_ot = _make_ot(capabilities=ObjectTypeCapabilities())
        new_ot = _make_ot(capabilities=ObjectTypeCapabilities(geotime_indexing_enabled=True))
        mock_metadata.get_object_type.return_value = old_ot
        mock_metadata.get_properties.return_value = []
        mock_metadata.update_object_type.return_value = new_ot

        await service.update_object_type_fields(
            "hr",
            "order",
            {"capabilities": {"graph_indexing_enabled": False, "geotime_indexing_enabled": True}},
        )

        service._provision_geotime_schema.assert_awaited_once()
        service._provision_graph_schema.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disable_does_not_trigger_provision(self, mock_metadata, mock_catalog, mock_index):
        """Disabling a capability does not trigger provisioning (only enabling does)."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        old_ot = _make_ot(capabilities=ObjectTypeCapabilities(graph_indexing_enabled=True))
        new_ot = _make_ot(capabilities=ObjectTypeCapabilities())
        mock_metadata.get_object_type.return_value = old_ot
        mock_metadata.get_properties.return_value = []
        mock_metadata.update_object_type.return_value = new_ot

        await service.update_object_type_fields(
            "hr",
            "order",
            {"capabilities": {"graph_indexing_enabled": False, "geotime_indexing_enabled": False}},
        )

        service._provision_graph_schema.assert_not_awaited()
        service._provision_geotime_schema.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_virtual_cannot_enable_capabilities(self, mock_metadata, mock_catalog, mock_index):
        """Gate 1: VIRTUAL ObjectType cannot enable graph/geotime indexing."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)

        virtual_ot = _make_ot(storage_type="VIRTUAL")
        mock_metadata.get_object_type.return_value = virtual_ot

        with pytest.raises(ValidationError, match="VIRTUAL"):
            await service.update_object_type_fields(
                "hr",
                "order",
                {"capabilities": {"graph_indexing_enabled": True, "geotime_indexing_enabled": False}},
            )

    @pytest.mark.asyncio
    async def test_invalid_capabilities_type_raises(self, mock_metadata, mock_catalog, mock_index):
        """Passing non-dict/non-ObjectTypeCapabilities raises ValidationError."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        ot = _make_ot()
        mock_metadata.get_object_type.return_value = ot

        with pytest.raises(ValidationError, match="capabilities must be"):
            await service.update_object_type_fields("hr", "order", {"capabilities": "invalid"})

    @pytest.mark.asyncio
    async def test_graph_enabled_without_links_warns_but_succeeds(
        self, mock_metadata, mock_catalog, mock_index, caplog
    ):
        """Gate 3: enabling graph_indexing without LinkType logs warning but succeeds."""
        import logging

        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        service._provision_graph_schema = AsyncMock()  # type: ignore[method-assign]
        service._provision_geotime_schema = AsyncMock()  # type: ignore[method-assign]

        old_ot = _make_ot(capabilities=ObjectTypeCapabilities(), links=[])
        new_ot = _make_ot(capabilities=ObjectTypeCapabilities(graph_indexing_enabled=True))
        mock_metadata.get_object_type.return_value = old_ot
        mock_metadata.get_link_types.return_value = []
        mock_metadata.get_properties.return_value = []
        mock_metadata.update_object_type.return_value = new_ot

        with caplog.at_level(logging.WARNING):
            result = await service.update_object_type_fields(
                "hr",
                "order",
                {"capabilities": {"graph_indexing_enabled": True, "geotime_indexing_enabled": False}},
            )

        # Succeeds despite warning
        assert result is not None
        # Warning was logged
        assert any("no LinkType connects" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_non_capabilities_update_passthrough(self, mock_metadata, mock_catalog, mock_index):
        """Updates without capabilities key pass through to metadata unchanged."""
        service = OntologyService(metadata=mock_metadata, catalog=mock_catalog, index=mock_index)
        ot = _make_ot()
        mock_metadata.get_object_type.return_value = ot
        mock_metadata.update_object_type.return_value = ot

        await service.update_object_type_fields("hr", "order", {"display_name": "New Name"})

        mock_metadata.update_object_type.assert_awaited_once_with("ot123", {"display_name": "New Name"})
