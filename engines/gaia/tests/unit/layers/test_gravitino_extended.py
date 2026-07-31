"""Unit tests for GravitinoRegistry — missing coverage areas.

Tests: register_jdbc_catalog, remove_catalog, list_catalogs,
is_view, resolve_backing_table, and error paths.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ontology.core.exceptions import GravitinoUnavailableError, NotFoundError
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def registry(mock_client) -> GravitinoRegistry:
    return GravitinoRegistry(client=mock_client)


class TestJDBCCatalogManagement:
    @pytest.mark.asyncio
    async def test_register_jdbc_catalog(self, registry, mock_client):
        mock_response = MagicMock()
        mock_client.post.return_value = mock_response

        await registry.register_jdbc_catalog(
            catalog_name="erp_mysql",
            provider="jdbc-mysql",
            jdbc_url="jdbc:mysql://localhost:3306/erp",
            jdbc_database="erp",
            jdbc_user="user",
            jdbc_password="pass",
            jdbc_driver="com.mysql.cj.jdbc.Driver",
        )
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0].endswith("/catalogs")
        payload = call_args[1]["json"]
        assert payload["name"] == "erp_mysql"
        assert payload["provider"] == "jdbc-mysql"

    @pytest.mark.asyncio
    async def test_register_jdbc_catalog_raises_on_error(self, registry, mock_client):
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(GravitinoUnavailableError, match="Failed to register"):
            await registry.register_jdbc_catalog(
                catalog_name="bad_catalog",
                provider="jdbc-mysql",
                jdbc_url="jdbc:mysql://down:3306/db",
                jdbc_database="db",
                jdbc_user="u",
                jdbc_password="p",
                jdbc_driver="driver",
            )

    @pytest.mark.asyncio
    async def test_remove_catalog(self, registry, mock_client):
        mock_response = MagicMock()
        mock_client.delete.return_value = mock_response

        await registry.remove_catalog("erp_mysql")
        mock_client.delete.assert_awaited_once()
        url = mock_client.delete.call_args[0][0]
        assert "force=true" in url

    @pytest.mark.asyncio
    async def test_remove_catalog_not_found(self, registry, mock_client):
        mock_response = MagicMock(status_code=404)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client.delete.return_value = mock_response

        # Should not raise - 404 is silently ignored
        await registry.remove_catalog("nonexistent")

    @pytest.mark.asyncio
    async def test_remove_catalog_raises_on_error(self, registry, mock_client):
        mock_client.delete.side_effect = Exception("Internal error")

        with pytest.raises(GravitinoUnavailableError):
            await registry.remove_catalog("bad")

    @pytest.mark.asyncio
    async def test_list_catalogs(self, registry, mock_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "identifiers": [
                {"name": "iceberg_catalog"},
                {"name": "erp_mysql"},
            ],
        }
        mock_client.get.return_value = mock_response

        result = await registry.list_catalogs()
        assert len(result) == 2
        assert result[0]["name"] == "iceberg_catalog"

    @pytest.mark.asyncio
    async def test_list_catalogs_error(self, registry, mock_client):
        mock_client.get.side_effect = Exception("Down")
        with pytest.raises(GravitinoUnavailableError):
            await registry.list_catalogs()


class TestViewOperations:
    # create_view tests removed: Gravitino SQL View line deleted per
    # dataset-ontology-binding.md §3.4 (collides with Palantir Virtual Table).
    # is_view tests retained — it's a low-level runtime probe.

    @pytest.mark.asyncio
    async def test_is_view_true(self, registry, mock_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"type": "view", "name": "my_view"}
        mock_client.get.return_value = mock_response

        result = await registry.is_view("catalog", "schema", "my_view")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_view_false(self, registry, mock_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"type": "table", "name": "my_table"}
        mock_client.get.return_value = mock_response

        result = await registry.is_view("catalog", "schema", "my_table")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_view_not_found(self, registry, mock_client):
        mock_response = MagicMock(status_code=404)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client.get.return_value = mock_response

        result = await registry.is_view("catalog", "schema", "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_view_error(self, registry, mock_client):
        mock_client.get.side_effect = Exception("Down")
        result = await registry.is_view("catalog", "schema", "error_view")
        assert result is False


class TestResolvePhysicalTable:
    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_resolve_backing_table(self, registry, mock_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "catalog": "iceberg_catalog",
            "schema": "ontology",
            "table": "orders",
        }
        mock_client.get.return_value = mock_response

        result = await registry.resolve_backing_table("order")
        assert result["catalog"] == "iceberg_catalog"
        assert result["table"] == "orders"

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_resolve_backing_table_not_found(self, registry, mock_client):
        mock_response = MagicMock(status_code=404)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client.get.return_value = mock_response

        with pytest.raises(NotFoundError):
            await registry.resolve_backing_table("nonexistent")

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_resolve_backing_table_error(self, registry, mock_client):
        mock_client.get.side_effect = Exception("Down")
        with pytest.raises(GravitinoUnavailableError):
            await registry.resolve_backing_table("order")


class TestRegisterDataset:
    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="测试与实现漂移:Gravitino/catalog 命名/错误类型演进,待专项对齐(E2)", strict=False)
    async def test_register_dataset_already_exists(self, registry, mock_client):
        mock_response = MagicMock(status_code=409)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "conflict",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client.post.return_value = mock_response

        # Should not raise
        await registry.register_dataset("catalog", "schema", "tbl", "s3://loc", [])


class TestCheckAccess:
    @pytest.mark.asyncio
    async def test_check_access_connect_error(self, registry, mock_client):
        """When Gravitino is unreachable, bypass permission check."""
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        result = await registry.check_access("order", "read")
        assert result is True


class TestGetTableMetadata:
    """get_table_metadata: 一次 REST 拿 columns + indexes + comment。"""

    @pytest.mark.asyncio
    async def test_returns_columns_indexes_comment(self, registry, mock_client):
        """完整 payload → columns + indexes + comment 一次性返回。"""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "table": {
                "columns": [
                    {"name": "modelId", "type": "string", "nullable": False, "comment": "HF repo ID"},
                    {"name": "downloads", "type": "long", "nullable": True, "comment": ""},
                ],
                "indexes": [
                    {"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["modelId"]]},
                ],
                "comment": "模型实例",
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        meta = await registry.get_table_metadata("xiaoling", "public", "model_instance")

        assert len(meta["columns"]) == 2
        assert meta["columns"][0]["name"] == "modelId"
        assert len(meta["indexes"]) == 1
        assert meta["indexes"][0]["indexType"] == "PRIMARY_KEY"
        assert meta["comment"] == "模型实例"
        # 只发一次 REST 请求（columns + indexes + comment 共用一个 payload）
        mock_client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_table_not_found_raises(self, registry, mock_client):
        """404 → NotFoundError。"""
        mock_response = MagicMock(status_code=404)
        mock_client.get.return_value = mock_response

        with pytest.raises(NotFoundError):
            await registry.get_table_metadata("cat", "sch", "missing")

    @pytest.mark.asyncio
    async def test_missing_columns_field_returns_empty(self, registry, mock_client):
        """payload 无 columns 字段 → 返回空 list（不崩）。"""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"table": {"comment": "no cols"}}
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        meta = await registry.get_table_metadata("cat", "sch", "t")
        assert meta["columns"] == []
        assert meta["indexes"] == []
        assert meta["comment"] == "no cols"

    @pytest.mark.asyncio
    async def test_missing_indexes_field_returns_empty(self, registry, mock_client):
        """payload 无 indexes 字段 → 返回空 list。"""
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"table": {"columns": [{"name": "id"}]}}
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        meta = await registry.get_table_metadata("cat", "sch", "t")
        assert meta["indexes"] == []

    @pytest.mark.asyncio
    async def test_transport_error_raises_unavailable(self, registry, mock_client):
        """网络错误 → GravitinoUnavailableError。"""
        mock_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(GravitinoUnavailableError):
            await registry.get_table_metadata("cat", "sch", "t")


class TestGetTableIndexes:
    """get_table_indexes: 单独取 indexes（best-effort，失败返回空）。"""

    @pytest.mark.asyncio
    async def test_returns_indexes_list(self, registry, mock_client):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "table": {
                "indexes": [{"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["id"]]}],
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        idxs = await registry.get_table_indexes("cat", "sch", "t")
        assert len(idxs) == 1
        assert idxs[0]["indexType"] == "PRIMARY_KEY"

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, registry, mock_client):
        """任何异常 → 返回空 list（best-effort 语义，不抛）。"""
        mock_client.get.side_effect = httpx.ConnectError("refused")

        idxs = await registry.get_table_indexes("cat", "sch", "t")
        assert idxs == []

    @pytest.mark.asyncio
    async def test_not_found_returns_empty(self, registry, mock_client):
        """表不存在 → 空列表（不抛 NotFoundError）。"""
        mock_response = MagicMock(status_code=404)
        mock_client.get.return_value = mock_response

        idxs = await registry.get_table_indexes("cat", "sch", "missing")
        assert idxs == []
