"""Unit tests for TimeTravelService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ForbiddenError
from ontology.services.time_travel_service import TimeTravelService


@pytest.fixture
def mock_catalog() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_authz() -> AsyncMock:
    """Permissive AuthorizationService mock (PDP allows by default)."""
    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    return az


@pytest.fixture
def service(mock_catalog, mock_engine, mock_authz) -> TimeTravelService:
    return TimeTravelService(catalog=mock_catalog, engine=mock_engine, authorization_service=mock_authz)


def _principal():
    from ontology.core.schemas.permission import Principal
    return Principal(id="u1", display_name="u1", is_anonymous=False)


class TestLoadObjectsAsOf:
    """Time travel / historical snapshot queries."""

    @pytest.mark.asyncio
    async def test_load_as_of_success(self, service, mock_catalog, mock_engine, mock_authz):
        """Load historical objects successfully (PDP allows)."""
        mock_authz.check_access.return_value = MagicMock(allowed=True, reason="")
        mock_catalog.resolve_backing_table.return_value = {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "employees",
        }
        mock_engine.query.return_value = [
            {"id": "1", "name": "Alice (v1)"},
        ]

        result = await service.load_objects_as_of(
            object_type_api_name="hr.employee",
            ids=["1"],
            properties=["id", "name"],
            snapshot_id=12345,
            principal=_principal(),
        )

        assert len(result) == 1
        assert result[0]["name"] == "Alice (v1)"
        sql = mock_engine.query.call_args[0][0]
        assert "FOR VERSION AS OF 12345" in sql
        assert "employee" in sql

    @pytest.mark.asyncio
    async def test_load_as_of_forbidden(self, service, mock_authz, mock_engine):
        """PDP denies → ForbiddenError, query not executed."""
        mock_authz.check_access.return_value = MagicMock(allowed=False, reason="denied")

        with pytest.raises(ForbiddenError, match="hr.secret"):
            await service.load_objects_as_of(
                object_type_api_name="hr.secret",
                ids=["1"],
                properties=["id"],
                snapshot_id=1,
                principal=_principal(),
            )
        mock_engine.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_as_of_no_principal_skips_check(self, service, mock_catalog, mock_engine, mock_authz):
        """No principal → permission check skipped (backwards-compatible read)."""
        mock_catalog.resolve_backing_table.return_value = {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "employees",
        }
        mock_engine.query.return_value = [{"id": "1", "name": "Alice"}]

        result = await service.load_objects_as_of(
            object_type_api_name="hr.employee",
            ids=["1"],
            properties=["id", "name"],
            snapshot_id=1,
        )
        assert len(result) == 1
        mock_authz.check_access.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_as_of_multiple_ids(self, service, mock_catalog, mock_engine):
        """Load historical data for multiple objects."""
        mock_catalog.resolve_backing_table.return_value = {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "orders",
        }
        mock_engine.query.return_value = [
            {"id": "1", "status": "active"},
            {"id": "2", "status": "pending"},
            {"id": "3", "status": "completed"},
        ]

        result = await service.load_objects_as_of(
            object_type_api_name="hr.order",
            ids=["1", "2", "3"],
            properties=["id", "status"],
            snapshot_id=500,
        )

        assert len(result) == 3
        sql = mock_engine.query.call_args[0][0]
        # Verify IN clause has all IDs
        assert "'1'" in sql
        assert "'2'" in sql
        assert "'3'" in sql

    @pytest.mark.asyncio
    async def test_load_as_of_table_not_found(self, service, mock_catalog, mock_engine):
        """Table resolution failure propagates."""
        mock_catalog.resolve_backing_table.side_effect = Exception("Table not found in Gravitino")

        with pytest.raises(Exception, match="Table not found"):
            await service.load_objects_as_of(
                object_type_api_name="hr.ghost",
                ids=["1"],
                properties=["id"],
                snapshot_id=1,
            )
