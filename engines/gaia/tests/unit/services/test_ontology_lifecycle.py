"""v5.2 Ontology lifecycle tests: Deprecate precondition, soft-delete cascade,
restore, and impact report.

These pin the delete-governance decisions (§5, §6, §七):
  - ACTIVE ontology cannot be deleted (must Deprecate first)
  - soft-delete cascades deleted_at to children (via metadata layer)
  - restore clears deleted_at; status stays DEPRECATED
  - impact report lists every affected resource type and blocks on ACTIVE
  - MCP/REST default path excludes non-active (include_non_active=False)
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from ontology.core.exceptions import ConflictError
from ontology.core.schemas.ontology import ImpactReport, Ontology, OntologyUpdate
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService


def _onto(api_name: str = "hr", *, status: str = "ACTIVE", deleted_at=None) -> Ontology:
    return Ontology(
        id="o1",
        api_name=api_name,
        display_name="HR",
        description="",
        rid="",
        status=status,
        deleted_at=deleted_at,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


@pytest.fixture
def mock_metadata() -> AsyncMock:
    m = AsyncMock(spec=PostgresMetaStore)
    m._flush_and_commit = AsyncMock()
    return m


@pytest.fixture
def service(mock_metadata) -> OntologyService:
    return OntologyService(
        metadata=mock_metadata,
        catalog=AsyncMock(spec=GravitinoRegistry),
        index=AsyncMock(spec=DorisIndexStore),
    )


class TestDeletePrecondition:
    @pytest.mark.asyncio
    async def test_active_ontology_rejected(self, service, mock_metadata):
        """An ACTIVE ontology cannot be deleted — must Deprecate first (§5.3)."""
        mock_metadata.get_ontology.return_value = _onto(status="ACTIVE")
        with pytest.raises(ConflictError, match="Deprecate"):
            await service.delete_ontology("hr")
        mock_metadata.delete_ontology.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_deleted_rejected(self, service, mock_metadata):
        """A second delete on an already-soft-deleted ontology is rejected."""
        mock_metadata.get_ontology.return_value = _onto(status="DEPRECATED", deleted_at=datetime(2026, 1, 1))
        with pytest.raises(ConflictError, match="already soft-deleted"):
            await service.delete_ontology("hr")
        mock_metadata.delete_ontology.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deprecated_ontology_soft_deletes(self, service, mock_metadata):
        """A DEPRECATED ontology is soft-deleted (metadata.delete_ontology called)."""
        mock_metadata.get_ontology.return_value = _onto(status="DEPRECATED")
        mock_metadata.list_object_types.return_value = []  # no MANAGED types to deprovision

        await service.delete_ontology("hr")

        mock_metadata.delete_ontology.assert_awaited_once_with("hr")


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_delegates_to_metadata(self, service, mock_metadata):
        """restore clears deleted_at via the metadata layer (§七.3)."""
        mock_metadata.restore_ontology.return_value = _onto(status="DEPRECATED")
        result = await service.restore_ontology("hr")
        mock_metadata.restore_ontology.assert_awaited_once_with("hr")
        # status stays DEPRECATED — restore does NOT re-activate.
        assert result.status == "DEPRECATED"


class TestImpactReport:
    @pytest.mark.asyncio
    async def test_active_blocks_delete(self, service, mock_metadata):
        """Impact report on an ACTIVE ontology: can_delete=False, blocked_reason set."""
        mock_metadata.get_ontology_impact.return_value = {
            "api_name": "hr",
            "status": "ACTIVE",
            "deleted_at": None,
            "object_type_count": 5,
            "property_count": 20,
            "link_type_count": 8,
            "action_type_count": 2,
            "object_instance_count": 100,
            "link_instance_count": 50,
            "managed_object_type_count": 5,
        }
        report = await service.get_ontology_impact("hr")
        assert isinstance(report, ImpactReport)
        assert report.can_delete is False
        assert "ACTIVE" in (report.blocked_reason or "")
        # Every impact category is listed.
        types = {i.resource_type for i in report.impacts}
        assert {
            "object_type",
            "link_type",
            "action_type",
            "object_instance",
            "link_instance",
            "doris_index_table",
            "index_pipeline",
        } <= types

    @pytest.mark.asyncio
    async def test_deprecated_allows_delete(self, service, mock_metadata):
        """Impact report on a DEPRECATED ontology: can_delete=True, no block."""
        mock_metadata.get_ontology_impact.return_value = {
            "api_name": "hr",
            "status": "DEPRECATED",
            "deleted_at": None,
            "object_type_count": 0,
            "property_count": 0,
            "link_type_count": 0,
            "action_type_count": 0,
            "object_instance_count": 0,
            "link_instance_count": 0,
            "managed_object_type_count": 0,
        }
        report = await service.get_ontology_impact("hr")
        assert report.can_delete is True
        assert report.blocked_reason is None


class TestStatusFilterPassthrough:
    """The service default (include_non_active=False) is what MCP/REST rely on
    to hide DEPRECATED / soft-deleted resources (§8.3). Verify the default
    is forwarded to the metadata layer."""

    @pytest.mark.asyncio
    async def test_list_ontologies_default_excludes_non_active(self, service, mock_metadata):
        mock_metadata.list_ontologies.return_value = []
        await service.list_ontologies()
        mock_metadata.list_ontologies.assert_awaited_once_with(include_non_active=False, include_deprecated=False)

    @pytest.mark.asyncio
    async def test_list_ontologies_include_deprecated_forwarded(self, service, mock_metadata):
        """include_deprecated shows DEPRECATED but still hides soft-deleted."""
        mock_metadata.list_ontologies.return_value = []
        await service.list_ontologies(include_deprecated=True)
        mock_metadata.list_ontologies.assert_awaited_once_with(include_non_active=False, include_deprecated=True)

    @pytest.mark.asyncio
    async def test_list_object_types_default_excludes_non_active(self, service, mock_metadata):
        mock_metadata.list_object_types.return_value = []
        await service.list_object_types("hr")
        mock_metadata.list_object_types.assert_awaited_once_with("hr", include_non_active=False)

    @pytest.mark.asyncio
    async def test_get_ontology_default_excludes_non_active(self, service, mock_metadata):
        mock_metadata.get_ontology.return_value = _onto()
        await service.get_ontology("hr")
        mock_metadata.get_ontology.assert_awaited_once_with("hr", include_non_active=False)


class TestDeprecateViaPatch:
    @pytest.mark.asyncio
    async def test_update_status_to_deprecated_forwarded(self, service, mock_metadata):
        """PATCH {status: DEPRECATED} is the Deprecate entry point (§5.5)."""
        mock_metadata.update_ontology.return_value = _onto(status="DEPRECATED")
        await service.update_ontology("hr", OntologyUpdate(status="DEPRECATED"))
        mock_metadata.update_ontology.assert_awaited_once_with(
            "hr", display_name=None, description=None, status="DEPRECATED"
        )
