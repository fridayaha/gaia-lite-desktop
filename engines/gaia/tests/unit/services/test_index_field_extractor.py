"""Unit tests for IndexFieldExtractor.

Validates the mapping from ObjectType properties → Doris IndexField[],
including the red-line enforcement (no full-detail/binary types indexed)
and the requirement that only physically-mapped properties are eligible.
"""

from types import SimpleNamespace

from ontology.services.index_field_extractor import IndexFieldExtractor


def _prop(
    api_name: str,
    data_type: str = "STRING",
    is_primary_key: bool = False,
    indexed: bool = False,
    backing_column: str | None = "col",
) -> SimpleNamespace:
    """Build a duck-typed property resembling PropertyDefModel (ORM).

    By default backing_column differs from api_name ("col") to keep tests
    honest about the extractor using the *physical* name. Tests that care
    about names pass backing_column explicitly.
    """
    return SimpleNamespace(
        api_name=api_name,
        data_type=data_type,
        is_primary_key=is_primary_key,
        indexed=indexed,
        backing_column=backing_column,
    )


class TestPrimaryKey:
    def test_primary_key_always_indexed(self):
        """PK participates even without indexed=True and without a physical column."""
        props = [_prop("id", is_primary_key=True, indexed=False, backing_column=None)]
        result = IndexFieldExtractor().extract(props)
        assert len(result.fields) == 1
        assert result.fields[0].name == "id"
        assert result.fields[0].index_type == "PRIMARY_KEY"
        assert result.skipped == []

    def test_primary_key_matched_by_object_type_primary_key(self):
        """A property whose api_name matches ObjectType.primary_key is PK,
        even without the per-property is_primary_key flag (API batch create
        sets primary_key at the ObjectType level, not per-property)."""
        props = [_prop("ticket_id", is_primary_key=False, indexed=False, backing_column=None)]
        result = IndexFieldExtractor().extract(props, primary_key="ticket_id")
        assert len(result.fields) == 1
        assert result.fields[0].name == "ticket_id"
        assert result.fields[0].index_type == "PRIMARY_KEY"

    def test_primary_key_uses_backing_column_when_available(self):
        """PK column name mirrors backing_mapping.column_name when present."""
        props = [_prop("id", is_primary_key=True, backing_column="pk_col")]
        result = IndexFieldExtractor().extract(props)
        assert result.fields[0].name == "pk_col"

    def test_primary_key_is_schema_prefix_when_not_first_property(self):
        """PK must be the FIRST field even when it is not the first property.

        Doris (and most OLAP engines) require key columns to be an ordered
        prefix of the schema: ``CREATE TABLE ... UNIQUE KEY(pk, ...)`` fails
        with "Key columns should be a ordered prefix of the schema" when the
        PK column is declared after non-key columns. The extractor iterates
        properties in definition order, so a PK that isn't property[0] would
        land mid-schema without this rule — breaking provision.
        """
        props = [
            _prop("name", data_type="STRING", indexed=True, backing_column="name"),
            _prop("status", data_type="STRING", indexed=True, backing_column="status"),
            _prop("id", is_primary_key=True, backing_column="id"),  # PK, but 3rd property
            _prop("age", data_type="INTEGER", indexed=True, backing_column="age"),
        ]
        result = IndexFieldExtractor().extract(props, primary_key="id")
        # PK must be fields[0] regardless of its position in the property list.
        assert result.fields[0].name == "id"
        assert result.fields[0].index_type == "PRIMARY_KEY"
        # No other PRIMARY_KEY field exists; the rest keep their relative order.
        assert [f.index_type for f in result.fields[1:]] == ["INVERTED", "INVERTED", "RANGE"]
        assert [f.name for f in result.fields[1:]] == ["name", "status", "age"]

    def test_empty_properties_with_primary_key_synthesizes_pk_field(self):
        """Empty properties + primary_key → synthesize a PK field.

        Regression: ``define_object_type`` (single-type create) calls provision
        with ``properties=[]`` because properties arrive later via add_property.
        Without this fallback the extractor returns 0 fields, the Doris DDL
        degenerates to ``CREATE TABLE (...)`` with an empty column list, and
        Doris rejects it with ``no viable alternative at input '(\n\n)'``.
        """
        result = IndexFieldExtractor().extract([], primary_key="ticket_id")
        assert len(result.fields) == 1
        assert result.fields[0].name == "ticket_id"
        assert result.fields[0].index_type == "PRIMARY_KEY"
        assert result.stored_columns == ["ticket_id"]

    def test_empty_properties_without_primary_key_returns_empty(self):
        """Empty properties + no primary_key → empty fields (caller must guard)."""
        result = IndexFieldExtractor().extract([], primary_key=None)
        assert result.fields == []


class TestIndexTypeMapping:
    def test_string_indexed_is_inverted(self):
        props = [_prop("status", data_type="STRING", indexed=True)]
        result = IndexFieldExtractor().extract(props)
        assert result.fields[0].index_type == "INVERTED"

    def test_numeric_indexed_is_range(self):
        for dt in ("INTEGER", "LONG", "DOUBLE", "DECIMAL"):
            props = [_prop(f"f_{dt}", data_type=dt, indexed=True)]
            r = IndexFieldExtractor().extract(props)
            assert r.fields[0].index_type == "RANGE", dt

    def test_temporal_indexed_is_range(self):
        for dt in ("DATE", "TIMESTAMP"):
            props = [_prop(f"f_{dt}", data_type=dt, indexed=True)]
            r = IndexFieldExtractor().extract(props)
            assert r.fields[0].index_type == "RANGE", dt

    def test_vector_indexed_is_vector(self):
        props = [_prop("emb", data_type="VECTOR", indexed=True)]
        result = IndexFieldExtractor().extract(props)
        assert result.fields[0].index_type == "VECTOR"

    def test_uses_backing_column_name_not_api_name(self):
        """The Doris column mirrors the Iceberg column, which may differ from api_name."""
        props = [_prop("status", data_type="STRING", indexed=True, backing_column="order_status")]
        result = IndexFieldExtractor().extract(props)
        assert result.fields[0].name == "order_status"


class TestEligibility:
    def test_unindexed_property_stored_only(self):
        # Post ADR-001 revision: non-indexed properties land as STORED_ONLY
        # (full-detail column, no index) so Doris holds complete rows.
        props = [_prop("note", data_type="STRING", indexed=False)]
        result = IndexFieldExtractor().extract(props)
        names = {f.name: f.index_type for f in result.fields}
        assert names == {"col": "STORED_ONLY"}
        assert result.stored_columns == ["col"]
        assert result.skipped == []

    def test_no_backing_mapping_skipped(self):
        props = [_prop("logical", data_type="STRING", indexed=True, backing_column=None)]
        result = IndexFieldExtractor().extract(props)
        assert result.fields == []
        assert ("logical", "no backing_mapping") in result.skipped


class TestFormerRedLine:
    """Post ADR-001 revision (2026-06-25): former redline types (STRUCT/ARRAY/
    binary) are now STORED_ONLY — Doris stores their serialized form, no index."""

    def test_struct_stored_only_even_if_indexed(self):
        props = [_prop("payload", data_type="STRUCT", indexed=True)]
        result = IndexFieldExtractor().extract(props)
        names = {f.name: f.index_type for f in result.fields}
        assert names == {"col": "STORED_ONLY"}
        assert result.skipped == []

    def test_attachment_stored_only(self):
        props = [_prop("file", data_type="ATTACHMENT", indexed=True)]
        result = IndexFieldExtractor().extract(props)
        names = {f.name: f.index_type for f in result.fields}
        assert names == {"col": "STORED_ONLY"}

    def test_array_stored_only(self):
        props = [_prop("tags", data_type="ARRAY", indexed=True)]
        result = IndexFieldExtractor().extract(props)
        names = {f.name: f.index_type for f in result.fields}
        assert names == {"col": "STORED_ONLY"}


class TestMixed:
    def test_mixed_property_set(self):
        """A realistic ObjectType: PK + 2 indexed + 1 unindexed + 1 former-redline."""
        props = [
            _prop("order_id", is_primary_key=True, backing_column="order_id"),
            _prop("status", data_type="STRING", indexed=True, backing_column="status"),
            _prop("amount", data_type="DECIMAL", indexed=True, backing_column="amount"),
            _prop("description", data_type="STRING", indexed=False, backing_column="description"),
            _prop("payload", data_type="STRUCT", indexed=True, backing_column="payload"),
        ]
        result = IndexFieldExtractor().extract(props)
        names = {f.name: f.index_type for f in result.fields}
        assert names == {
            "order_id": "PRIMARY_KEY",
            "status": "INVERTED",
            "amount": "RANGE",
            "description": "STORED_ONLY",
            "payload": "STORED_ONLY",
        }
        # stored_columns is the full superset (all storable columns).
        assert set(result.stored_columns) == {"order_id", "status", "amount", "description", "payload"}
        assert result.skipped == []


class TestSchemaStyleInput:
    """Extractor also accepts schema PropertyDef (backing_mapping.column_name)."""

    def test_schema_backing_mapping(self):
        pm = SimpleNamespace(backing_column="src_col")
        prop = SimpleNamespace(
            api_name="name",
            data_type="STRING",
            is_primary_key=False,
            indexed=True,
            backing_column=None,
            backing_mapping=pm,
        )
        result = IndexFieldExtractor().extract([prop])
        assert result.fields[0].name == "src_col"
        assert result.fields[0].index_type == "INVERTED"
