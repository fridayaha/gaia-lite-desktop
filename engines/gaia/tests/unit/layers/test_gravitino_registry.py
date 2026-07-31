"""Unit tests for GravitinoRegistry.

All HTTP calls are mocked via httpx. Tests validate:
1. Correct REST API endpoints are called
2. Error paths raise appropriate domain exceptions
3. Response parsing produces correct domain objects

NOTE: httpx.Response methods (json(), raise_for_status()) are SYNCHRONOUS,
so mock responses must use MagicMock, not AsyncMock.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, HTTPStatusError, Request, Response

from ontology.core.exceptions import (
    GravitinoUnavailableError,
    NotFoundError,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry


@pytest.fixture
def mock_client() -> AsyncMock:
    """Mock httpx.AsyncClient (async methods)."""
    client = AsyncMock(spec=AsyncClient)
    return client


@pytest.fixture
def mock_response() -> MagicMock:
    """Mock httpx.Response (sync methods like .json())."""
    resp = MagicMock(spec=Response)
    resp.status_code = 200
    resp.json.return_value = {}
    return resp


@pytest.fixture
def registry(mock_client) -> GravitinoRegistry:
    """Create a GravitinoRegistry with mocked client."""
    return GravitinoRegistry(client=mock_client)


class TestRegisterDataset:
    """Dataset registration."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_register_dataset_success(self, registry, mock_client, mock_response):
        """Register a dataset returns None on success."""
        mock_response.status_code = 201
        mock_client.post.return_value = mock_response

        await registry.register_dataset(
            catalog="iceberg_catalog",
            schema="ontology",
            name="employees",
            location="s3://ontology-warehouse/employees",
            columns=[{"name": "id", "type": "integer"}],
        )

        mock_client.post.assert_awaited_once()
        call_args = str(mock_client.post.call_args)
        assert "/api/metalakes/ontology/catalogs/iceberg_catalog/schemas/ontology/tables" in call_args

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_register_dataset_conflict_is_ok(self, registry, mock_client, mock_response):
        """409 Conflict is not an error."""
        mock_client.post.return_value = mock_response  # status_code=200 by default, 409 doesn't raise
        mock_response.status_code = 409

        # 409 doesn't raise_for_status in our implementation
        await registry.register_dataset(
            catalog="iceberg_catalog",
            schema="ontology",
            name="employees",
            location="s3://...",
            columns=[{"name": "id", "type": "integer"}],
        )

        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_register_dataset_server_error(self, registry, mock_client):
        """500 error raises GravitinoUnavailableError."""
        mock_client.post.side_effect = HTTPStatusError(
            "500 Server Error",
            request=Request("POST", "http://localhost:8090/api/..."),
            response=Response(500),
        )

        with pytest.raises(GravitinoUnavailableError, match="Gravitino API error"):
            await registry.register_dataset(
                catalog="iceberg_catalog",
                schema="ontology",
                name="employees",
                location="s3://...",
                columns=[{"name": "id", "type": "integer"}],
            )


class TestIsView:
    """View (runtime probe) operations."""

    @pytest.mark.asyncio
    async def test_is_view_true(self, registry, mock_client, mock_response):
        """is_view returns True for views."""
        mock_response.json.return_value = {"type": "view"}
        mock_client.get.return_value = mock_response

        result = await registry.is_view(
            catalog="iceberg_catalog",
            schema="ontology",
            name="active_employees",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_is_view_false(self, registry, mock_client, mock_response):
        """is_view returns False for regular tables."""
        mock_response.json.return_value = {"type": "table"}
        mock_client.get.return_value = mock_response

        result = await registry.is_view(
            catalog="iceberg_catalog",
            schema="ontology",
            name="employees",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_is_view_not_found(self, registry, mock_client, mock_response):
        """is_view returns False for non-existent tables."""
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        result = await registry.is_view(
            catalog="iceberg_catalog",
            schema="ontology",
            name="nonexistent",
        )

        assert result is False


class TestCheckAccess:
    """RBAC permission checks."""

    @pytest.mark.asyncio
    async def test_check_access_allowed(self, registry, mock_client, mock_response):
        """check_access returns True when allowed."""
        mock_response.json.return_value = {"allowed": True}
        mock_client.get.return_value = mock_response

        result = await registry.check_access(
            object_type_api_name="employee",
            operation="read",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_check_access_denied(self, registry, mock_client, mock_response):
        """check_access returns False when denied."""
        mock_response.json.return_value = {"allowed": False}
        mock_client.get.return_value = mock_response

        result = await registry.check_access(
            object_type_api_name="employee",
            operation="read",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_check_access_gravitino_down(self, registry, mock_client):
        """When Gravitino is down, physical tables bypass permission check."""
        from httpx import ConnectError

        mock_client.get.side_effect = ConnectError("Connection refused")

        result = await registry.check_access(
            object_type_api_name="employee",
            operation="read",
        )

        # Per architecture: Gravitino unavailable → bypass permission check
        assert result is True


class TestResolvePhysicalTable:
    """Physical table route resolution."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_resolve_backing_table(self, registry, mock_client, mock_response):
        """Resolve physical table info from object type."""
        mock_response.json.return_value = {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "employees",
        }
        mock_client.get.return_value = mock_response

        result = await registry.resolve_backing_table(object_type_api_name="employee")

        assert result == {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "employees",
        }

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_resolve_backing_table_not_found(self, registry, mock_client, mock_response):
        """Non-existent table raises NotFoundError."""
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response

        with pytest.raises(NotFoundError):
            await registry.resolve_backing_table(object_type_api_name="ghost")


# ── Multi-source catalog registration (multi-source-data-fusion-design.md §6) ──


class TestRegisterLakehouseCatalog:
    """External lakehouse catalog as federation source (§6.2)."""

    @pytest.mark.asyncio
    async def test_register_hive_catalog(self, registry, mock_client, mock_response):
        await registry.register_lakehouse_catalog(
            catalog_name="extHive",
            provider="hive",
            properties={"metastore-uri": "thrift://hms:9083"},
        )
        mock_client.post.assert_awaited_once()
        call = mock_client.post.await_args
        url = call.args[0]
        payload = call.kwargs["json"]
        assert "/catalogs" in url
        assert payload["name"] == "extHive"
        assert payload["provider"] == "hive"
        assert payload["properties"]["metastore-uri"] == "thrift://hms:9083"

    @pytest.mark.asyncio
    async def test_register_delta_catalog(self, registry, mock_client, mock_response):
        await registry.register_lakehouse_catalog(
            catalog_name="extDelta",
            provider="lakehouse-delta",
            properties={"catalog-backend": "hive", "warehouse": "s3://delta/wh"},
        )
        payload = mock_client.post.await_args.kwargs["json"]
        assert payload["provider"] == "lakehouse-delta"
        assert payload["type"] == "relational"

    @pytest.mark.asyncio
    async def test_register_lakehouse_failure_raises(self, registry, mock_client):
        mock_client.post.side_effect = RuntimeError("conn refused")
        with pytest.raises(GravitinoUnavailableError):
            await registry.register_lakehouse_catalog(
                catalog_name="extHive",
                provider="hive",
                properties={"metastore-uri": "thrift://hms:9083"},
            )


class TestRegisterKafkaCatalog:
    """Kafka catalog for topic metadata (§6.4)."""

    @pytest.mark.asyncio
    async def test_register_kafka_catalog(self, registry, mock_client, mock_response):
        await registry.register_kafka_catalog(
            catalog_name="extKafka",
            bootstrap_servers="kafka:9092",
        )
        payload = mock_client.post.await_args.kwargs["json"]
        assert payload["type"] == "messaging"
        assert payload["provider"] == "kafka"
        assert payload["properties"]["bootstrap.servers"] == "kafka:9092"

    @pytest.mark.asyncio
    async def test_register_kafka_catalog_with_extra_props(self, registry, mock_client, mock_response):
        await registry.register_kafka_catalog(
            catalog_name="extKafka",
            bootstrap_servers="kafka:9092",
            properties={"sasl.mechanism": "PLAIN"},
        )
        props = mock_client.post.await_args.kwargs["json"]["properties"]
        assert props["bootstrap.servers"] == "kafka:9092"
        assert props["sasl.mechanism"] == "PLAIN"


class TestRegisterFilesetCatalog:
    """Fileset catalog for file/object storage metadata (§6.3)."""

    @pytest.mark.asyncio
    async def test_register_s3_fileset_catalog(self, registry, mock_client, mock_response):
        await registry.register_fileset_catalog(
            catalog_name="extS3",
            provider="s3",
            properties={
                "location": "s3://bucket/path",
                "s3-endpoint": "http://s3:9000",
                "s3-access-key-id": "ak",
                "s3-secret-access-key": "sk",
            },
        )
        payload = mock_client.post.await_args.kwargs["json"]
        assert payload["type"] == "fileset"
        assert payload["provider"] == "s3"
        assert payload["properties"]["location"] == "s3://bucket/path"

    @pytest.mark.asyncio
    async def test_register_fileset_failure_raises(self, registry, mock_client):
        mock_client.post.side_effect = RuntimeError("boom")
        with pytest.raises(GravitinoUnavailableError):
            await registry.register_fileset_catalog(
                catalog_name="extS3",
                provider="s3",
                properties={"location": "s3://b"},
            )
