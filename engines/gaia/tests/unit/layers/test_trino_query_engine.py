"""Unit tests for TrinoQueryEngine.

The Trino client's dbapi is mocked. Tests validate:
1. SQL queries are dispatched correctly
2. Results are properly parsed from DBAPI tuples to dicts
3. Error paths raise domain exceptions
"""

import re
from unittest.mock import MagicMock

import pytest
from trino.exceptions import TrinoConnectionError, TrinoQueryError

from ontology.core.exceptions import (
    DataSourceUnreachableError,
    OntologyError,
    TrinoUnavailableError,
)
from ontology.layers.engine.trino_query_engine import TrinoQueryEngine, _classify_trino_error


@pytest.fixture
def mock_cursor() -> MagicMock:
    """Mock Trino DBAPI cursor.

    DBAPI spec: cursor.description returns 7-element tuples per column,
    with column name at index 0. cursor.fetchall() returns list of tuples.
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (1, "Alice"),
        (2, "Bob"),
    ]
    cursor.description = [
        ("id", None, None, None, None, None, None),
        ("name", None, None, None, None, None, None),
    ]
    return cursor


@pytest.fixture
def mock_connection(mock_cursor) -> MagicMock:
    """Mock Trino DBAPI connection."""
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    return conn


@pytest.fixture
def engine(mock_connection) -> TrinoQueryEngine:
    """Create a TrinoQueryEngine with mocked connection."""
    return TrinoQueryEngine(connection=mock_connection)


class TestQuery:
    """SQL query execution."""

    @pytest.mark.asyncio
    async def test_query_returns_dicts(self, engine, mock_cursor):
        """Query returns list of dicts with correct keys."""
        result = await engine.query("SELECT * FROM employees")

        assert len(result) == 2
        assert result[0] == {"id": 1, "name": "Alice"}
        assert result[1] == {"id": 2, "name": "Bob"}
        mock_cursor.execute.assert_called_once_with("SELECT * FROM employees")

    @pytest.mark.asyncio
    async def test_query_empty_result(self, engine, mock_cursor):
        """Empty query result returns empty list."""
        mock_cursor.fetchall.return_value = []
        mock_cursor.description = []

        result = await engine.query("SELECT * FROM empty")

        assert result == []

    @pytest.mark.asyncio
    async def test_query_with_parameters(self, engine, mock_cursor):
        """Query with parameters is supported."""
        mock_cursor.fetchall.return_value = [(42,)]
        mock_cursor.description = [("id", None, None, None, None, None, None)]

        result = await engine.query(
            "SELECT * FROM employees WHERE id = ?",
            params=[42],
        )

        assert result[0]["id"] == 42
        mock_cursor.execute.assert_called_once_with("SELECT * FROM employees WHERE id = ?", [42])

    @pytest.mark.asyncio
    async def test_query_raises_on_error(self, engine, mock_cursor):
        """Database errors raise OntologyError."""
        mock_cursor.execute.side_effect = Exception("Trino query failed")

        with pytest.raises(OntologyError, match="Trino query failed"):
            await engine.query("SELECT * FROM bad_sql")

    @pytest.mark.asyncio
    async def test_query_closes_cursor(self, engine, mock_cursor):
        """Cursor is closed after query execution."""
        await engine.query("SELECT 1")

        mock_cursor.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_uses_correct_catalog(self, mock_connection):
        """Connection uses the correct catalog and schema."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1,)]
        mock_cursor.description = [("x", None, None, None, None, None, None)]
        mock_connection.cursor.return_value = mock_cursor

        engine = TrinoQueryEngine(connection=mock_connection)

        await engine.query("SELECT 1")

        mock_connection.cursor.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# Trino error classification (_classify_trino_error)
# ═══════════════════════════════════════════════════════════════


def _trino_query_error(message: str, *, error_name: str = "GENERIC_INTERNAL_ERROR") -> TrinoQueryError:
    """Build a TrinoQueryError like the trino client would."""
    return TrinoQueryError(
        {
            "message": message,
            "errorName": error_name,
            "errorType": "INTERNAL_ERROR",
        },
        query_id="test_qid",
    )


class TestClassifyTrinoError:
    """Map raw trino-client exceptions to domain exceptions.

    Regression guard for the “探索失败 请确认 Gravitino 和 Trino 服务可用”
    misdirection: a stopped external DB must surface as
    DataSourceUnreachableError (502), not a generic 500 blamed on Trino.
    """

    def test_datasource_dns_failure(self):
        """UnknownHostException (stopped container / wrong host) → 502."""
        exc = _trino_query_error(
            "Failed to operate object operation [LIST] under [marketingMysql], "
            "reason [Cannot create PoolableConnectionFactory (Communications link failure ...)] "
            "Caused by: java.net.UnknownHostException: marketing-mysql",
        )
        result = _classify_trino_error(exc)
        assert isinstance(result, DataSourceUnreachableError)
        assert result.code == "DATASOURCE_UNREACHABLE"

    def test_datasource_connection_refused(self):
        """Connection refused marker → DataSourceUnreachableError."""
        exc = _trino_query_error("Connection refused to mysql-svc:3306")
        assert isinstance(_classify_trino_error(exc), DataSourceUnreachableError)

    def test_datasource_connection_timed_out(self):
        """Connection timed out marker → DataSourceUnreachableError."""
        exc = _trino_query_error("Connection timed out while dialing db")
        assert isinstance(_classify_trino_error(exc), DataSourceUnreachableError)

    def test_trino_server_unreachable(self):
        """TrinoConnectionError (Trino server down) → TrinoUnavailableError (503)."""
        exc = TrinoConnectionError("Failed to establish a connection to trino:8080")
        result = _classify_trino_error(exc)
        assert isinstance(result, TrinoUnavailableError)
        assert result.code == "TRINO_UNAVAILABLE"

    def test_other_query_error_stays_generic(self):
        """Non-connection TrinoQueryError (syntax/type) → generic OntologyError.

        Crucially NOT misclassified as a data-source failure.
        """
        exc = _trino_query_error("line 1:1: mismatched input 'SELEC'", error_name="SYNTAX_ERROR")
        result = _classify_trino_error(exc)
        assert type(result) is OntologyError
        assert not isinstance(result, DataSourceUnreachableError)
        assert not isinstance(result, TrinoUnavailableError)
        assert re.search(r"Trino query failed", str(result))

    def test_plain_exception_stays_generic(self):
        """A non-trino Exception → generic OntologyError."""
        result = _classify_trino_error(RuntimeError("boom"))
        assert type(result) is OntologyError

    @pytest.mark.asyncio
    async def test_query_propagates_datasource_unreachable(self, engine, mock_cursor):
        """query() raises DataSourceUnreachableError, not generic OntologyError,
        when the backing DB is down (end-to-end through _execute)."""
        mock_cursor.execute.side_effect = _trino_query_error(
            "Cannot create PoolableConnectionFactory (Communications link failure) "
            "Caused by: java.net.UnknownHostException: marketing-mysql",
        )
        with pytest.raises(DataSourceUnreachableError):
            await engine.query('SHOW TABLES FROM "marketingMysql"."public"')

    @pytest.mark.asyncio
    async def test_query_propagates_trino_unavailable(self, engine, mock_cursor):
        """query() raises TrinoUnavailableError when Trino server is down."""
        mock_cursor.execute.side_effect = TrinoConnectionError("connection refused")
        with pytest.raises(TrinoUnavailableError):
            await engine.query("SELECT 1")

    @pytest.mark.asyncio
    async def test_query_generic_error_unchanged(self, engine, mock_cursor):
        """Non-connection errors still raise the legacy OntologyError."""
        mock_cursor.execute.side_effect = _trino_query_error("mismatched input")
        with pytest.raises(OntologyError) as exc_info:
            await engine.query("SELECT bad")
        assert not isinstance(exc_info.value, (DataSourceUnreachableError, TrinoUnavailableError))
