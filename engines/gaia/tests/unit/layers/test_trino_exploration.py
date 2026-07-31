"""Unit tests for TrinoQueryEngine exploration helpers.

Covers: list_tables, describe_table, sample_data, test_connection.
The query() core method is already well-tested. These tests cover
the higher-level exploration methods used by DataSourceService.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import OntologyError
from ontology.layers.engine.trino_query_engine import TrinoQueryEngine


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock(spec=TrinoQueryEngine)
    return engine


class TestListTables:
    @pytest.mark.asyncio
    async def test_list_tables_with_schema(self, mock_engine):
        mock_engine.query = AsyncMock(
            return_value=[
                {"Table": "employees"},
                {"Table": "departments"},
            ]
        )
        mock_engine.list_tables = TrinoQueryEngine.list_tables

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.list_tables(engine, "erp_mysql", "dbo")
        assert "employees" in result
        assert "departments" in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_tables_no_schema(self, mock_engine):
        """Without schema, lists schemas first, then tables."""
        call_count = [0]

        async def query_side_effect(sql):
            call_count[0] += 1
            if "SHOW SCHEMAS" in sql:
                return [{"Schema": "public"}, {"Schema": "hr"}]
            elif "SHOW TABLES" in sql:
                return [{"Table": "t1"}]
            return []

        mock_engine.query = AsyncMock(side_effect=query_side_effect)
        mock_engine.list_tables = TrinoQueryEngine.list_tables

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.list_tables(engine, "erp_mysql")
        assert "public.t1" in result
        assert "hr.t1" in result

    @pytest.mark.asyncio
    async def test_list_tables_handles_errors(self, mock_engine):
        """Schema errors are caught, processing continues."""

        async def query_side_effect(sql):
            if "SHOW SCHEMAS" in sql:
                return [{"Schema": "s1"}, {"Schema": "s2"}]
            if '"s2"' in sql:
                raise OntologyError("Failed")
            return [{"Table": "ok_table"}]

        mock_engine.query = AsyncMock(side_effect=query_side_effect)
        mock_engine.list_tables = TrinoQueryEngine.list_tables

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.list_tables(engine, "erp_mysql")
        assert "s1.ok_table" in result


class TestDescribeTable:
    @pytest.mark.asyncio
    async def test_describe_table(self, mock_engine):
        mock_engine.query = AsyncMock(
            return_value=[
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "Primary identifier"},
                {"Column": "name", "Type": "varchar", "Extra": "", "Comment": "Display name"},
            ]
        )
        mock_engine.describe_table = TrinoQueryEngine.describe_table

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.describe_table(engine, "erp", "dbo", "employees")
        assert len(result) == 2
        assert result[0]["Column"] == "id"

    @pytest.mark.asyncio
    async def test_describe_table_fallback(self, mock_engine):
        """On failure, tries SHOW COLUMNS as fallback."""

        async def query_side_effect(sql):
            if "DESCRIBE" in sql:
                raise OntologyError("DESCRIBE not supported")
            return [{"Column": "id", "Type": "int"}]

        mock_engine.query = AsyncMock(side_effect=query_side_effect)
        mock_engine.describe_table = TrinoQueryEngine.describe_table

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.describe_table(engine, "erp", "dbo", "employees")
        assert len(result) == 1
        assert result[0]["Column"] == "id"


class TestSampleData:
    @pytest.mark.asyncio
    async def test_sample_data(self, mock_engine):
        mock_engine.query = AsyncMock(
            return_value=[
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ]
        )
        mock_engine.sample_data = TrinoQueryEngine.sample_data

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.sample_data(engine, "erp", "dbo", "employees", limit=5)
        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        call_sql = mock_engine.query.call_args[0][0]
        assert "LIMIT 5" in call_sql


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_connection_success(self, mock_engine):
        mock_engine.query = AsyncMock(return_value=[{"_col0": 1}])
        mock_engine.test_connection = TrinoQueryEngine.test_connection

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.test_connection(engine, "erp_mysql")
        assert result is True

    @pytest.mark.asyncio
    async def test_connection_failure(self, mock_engine):
        mock_engine.query = AsyncMock(side_effect=Exception("Connection refused"))
        mock_engine.test_connection = TrinoQueryEngine.test_connection

        engine = MagicMock(spec=TrinoQueryEngine)
        engine.query = mock_engine.query

        result = await TrinoQueryEngine.test_connection(engine, "bad_catalog")
        assert result is False
