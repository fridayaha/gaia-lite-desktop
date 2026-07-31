"""Unit tests for ``_classify_trino_error`` catalog-missing classification.

Verifies that ``CATALOG_NOT_FOUND`` / ``SCHEMA_NOT_FOUND`` TrinoUserError
are mapped to ``CatalogNotRegisteredError`` (HTTP 502, code
``CATALOG_NOT_REGISTERED``), distinct from ``DataSourceUnreachableError``
(source DB down) and ``TrinoUnavailableError`` (Trino server down).
"""

from ontology.core.exceptions import (
    CatalogNotRegisteredError,
    DataSourceUnreachableError,
    OntologyError,
    TrinoUnavailableError,
)
from ontology.layers.engine.trino_query_engine import _classify_trino_error

# trino-client is a real dependency; import lazily inside tests so the
# module itself stays importable in stripped environments.


def _make_query_error(name: str, message: str):
    from trino.exceptions import TrinoQueryError

    # TrinoQueryError.error_name reads the "errorName" key (camelCase),
    # matching the Trino server's JSON error envelope.
    return TrinoQueryError({"errorName": name, "name": name, "message": message}, "test-query-id")


def _make_connection_error(message: str):
    from trino.exceptions import TrinoConnectionError

    return TrinoConnectionError(message)


class TestClassifyCatalogMissing:
    """CATALOG_NOT_FOUND / SCHEMA_NOT_FOUND → CatalogNotRegisteredError."""

    def test_catalog_not_found_maps_to_catalog_not_registered(self):
        exc = _make_query_error("CATALOG_NOT_FOUND", "line 1:1: Catalog 'xiaoling' not found")
        result = _classify_trino_error(exc)
        assert isinstance(result, CatalogNotRegisteredError)
        assert result.code == "CATALOG_NOT_REGISTERED"

    def test_schema_not_found_maps_to_catalog_not_registered(self):
        exc = _make_query_error("SCHEMA_NOT_FOUND", "Schema 'public' not found")
        result = _classify_trino_error(exc)
        assert isinstance(result, CatalogNotRegisteredError)
        assert result.code == "CATALOG_NOT_REGISTERED"

    def test_gravitino_catalog_not_exists_maps_to_catalog_not_registered(self):
        """The Gravitino Connector's own error name (distinct from Trino's CATALOG_NOT_FOUND).

        Observed in production when Gravitino was rebuilt and its PG-backed
        catalog metadata was wiped: Trino reaches the connector but Gravitino
        reports the catalog is gone → ``TrinoExternalError`` with
        ``error_name=GRAVITINO_CATALOG_NOT_EXISTS``.
        """
        from trino.exceptions import TrinoExternalError

        exc = TrinoExternalError(
            {"errorName": "GRAVITINO_CATALOG_NOT_EXISTS", "message": "Catalog does not exist"},
            "test-query-id",
        )
        result = _classify_trino_error(exc)
        assert isinstance(result, CatalogNotRegisteredError)
        assert result.code == "CATALOG_NOT_REGISTERED"

    def test_catalog_missing_error_message_is_user_facing(self):
        """The mapped message should be actionable, not a raw stack trace."""
        exc = _make_query_error("CATALOG_NOT_FOUND", "line 1:1: Catalog 'xiaoling' not found")
        result = _classify_trino_error(exc)
        msg = str(result)
        assert "未注册" in msg or "丢失" in msg  # 面向用户的中文提示


class TestClassifyPreservesExistingBehavior:
    """Existing classification buckets must not regress."""

    def test_connection_error_maps_to_trino_unavailable(self):
        exc = _make_connection_error("Connection refused")
        result = _classify_trino_error(exc)
        assert isinstance(result, TrinoUnavailableError)
        assert result.code == "TRINO_UNAVAILABLE"

    def test_jdbc_connection_failure_maps_to_datasource_unreachable(self):
        """TrinoQueryError with a JDBC connection-failure marker stays DataSourceUnreachableError."""
        exc = _make_query_error(
            "EXTERNAL",
            "Connection refused: gaia-postgres.gaia.svc.cluster.local/10.0.0.5:5432",
        )
        result = _classify_trino_error(exc)
        assert isinstance(result, DataSourceUnreachableError)
        assert result.code == "DATASOURCE_UNREACHABLE"

    def test_unknown_query_error_falls_back_to_ontology_error(self):
        """Non-catalog, non-connection TrinoUserError → generic OntologyError."""
        exc = _make_query_error("SYNTAX_ERROR", "line 1:1: mismatched input 'SELCT'")
        result = _classify_trino_error(exc)
        assert type(result) is OntologyError
        assert result.code is None

    def test_generic_exception_falls_back_to_ontology_error(self):
        result = _classify_trino_error(RuntimeError("boom"))
        assert type(result) is OntologyError


class TestCatalogMissingNotConfusedWithUnreachable:
    """Critical: a missing catalog must NOT be misclassified as source-unreachable.

    If it were, the UI would tell the user "数据源服务未运行" when in fact the
    source DB is fine — only the Gravitino registration is stale. The whole
    point of the new error code is to route these to the reconcile self-heal
    path, not to the "restart your DB" hint.
    """

    def test_catalog_not_found_is_not_datasource_unreachable(self):
        exc = _make_query_error("CATALOG_NOT_FOUND", "line 1:1: Catalog 'xiaoling' not found")
        result = _classify_trino_error(exc)
        assert not isinstance(result, DataSourceUnreachableError)
        assert isinstance(result, CatalogNotRegisteredError)
