"""Tests for core.property_mapping — api_name ↔ backing_column conversion."""

from datetime import UTC, datetime

from ontology.core.property_mapping import (
    api_field_to_backing,
    api_to_backing,
    api_to_backing_map,
    backing_to_api,
    backing_to_api_map,
)
from ontology.core.schemas.ontology import ObjectType, PropertyDef

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _prop(api_name: str, backing_column: str | None = None, *, indexed: bool = False) -> PropertyDef:
    """Build a PropertyDef. backing_column=None ⇒ no backing_mapping."""
    if backing_column is None:
        return PropertyDef(
            id=f"p_{api_name}",
            object_type_id="ot1",
            api_name=api_name,
            display_name=api_name,
            data_type="STRING",
            indexed=indexed,
            created_at=_TS,
            updated_at=_TS,
        )
    return PropertyDef(
        id=f"p_{api_name}",
        object_type_id="ot1",
        api_name=api_name,
        display_name=api_name,
        data_type="STRING",
        indexed=indexed,
        backing_mapping={
            "dataset_api_name": "",
            "backing_catalog": "cat",
            "backing_schema": "public",
            "backing_table": "t",
            "backing_column": backing_column,
        },
        created_at=_TS,
        updated_at=_TS,
    )


def _ot(props: list[PropertyDef]) -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name="Lead",
        display_name="Lead",
        primary_key="leadsId",
        title_property="leadsId",
        storage_type="MANAGED",
        properties=props,
        created_at=_TS,
        updated_at=_TS,
    )


class TestApiToBackingMap:
    def test_only_renaming_pairs_included(self):
        ot = _ot([_prop("leadsId", "leads_id"), _prop("status"), _prop("operationTime", "operation_time")])
        m = api_to_backing_map(ot)
        assert m == {"leadsId": "leads_id", "operationTime": "operation_time"}
        # status has no backing_mapping ⇒ api_name == backing_column ⇒ omitted.

    def test_empty_when_all_passthrough(self):
        ot = _ot([_prop("status"), _prop("name")])
        assert api_to_backing_map(ot) == {}

    def test_empty_when_no_properties(self):
        ot = _ot([])
        assert api_to_backing_map(ot) == {}


class TestBackingToApiMap:
    def test_inverse_of_api_to_backing(self):
        ot = _ot([_prop("leadsId", "leads_id"), _prop("operationTime", "operation_time")])
        assert backing_to_api_map(ot) == {"leads_id": "leadsId", "operation_time": "operationTime"}


class TestApiToBacking:
    def test_renames_known_keys(self):
        ot = _ot([_prop("leadsId", "leads_id"), _prop("operationTime", "operation_time")])
        out = api_to_backing(ot, {"leadsId": "L1", "operationTime": "2026-01-01"})
        assert out == {"leads_id": "L1", "operation_time": "2026-01-01"}

    def test_passthrough_unknown_keys(self):
        # visibility is not a declared property → kept as-is.
        ot = _ot([_prop("leadsId", "leads_id")])
        out = api_to_backing(ot, {"leadsId": "L1", "visibility": "NORMAL"})
        assert out == {"leads_id": "L1", "visibility": "NORMAL"}

    def test_no_mapping_returns_copy(self):
        ot = _ot([_prop("status")])
        src = {"status": "ACTIVE"}
        out = api_to_backing(ot, src)
        assert out == src
        # Returns a copy, not the same dict (no aliasing).
        assert out is not src

    def test_none_ot_passthrough(self):
        out = api_to_backing(None, {"a": 1})
        assert out == {"a": 1}


class TestBackingToApi:
    def test_renames_known_keys(self):
        ot = _ot([_prop("leadsId", "leads_id"), _prop("operationTime", "operation_time")])
        out = backing_to_api(ot, {"leads_id": "L1", "operation_time": "2026-01-01"})
        assert out == {"leadsId": "L1", "operationTime": "2026-01-01"}

    def test_passthrough_unknown_keys(self):
        # count / sum_amount (aggregation aliases) and extras survive.
        ot = _ot([_prop("leadsId", "leads_id")])
        out = backing_to_api(ot, {"leads_id": "L1", "count": 5, "extra": "x"})
        assert out == {"leadsId": "L1", "count": 5, "extra": "x"}

    def test_no_mapping_returns_copy(self):
        ot = _ot([_prop("status")])
        out = backing_to_api(ot, {"status": "A"})
        assert out == {"status": "A"}

    def test_none_ot_passthrough(self):
        assert backing_to_api(None, {"a": 1}) == {"a": 1}


class TestApiFieldToBacking:
    def test_translates_known_field(self):
        ot = _ot([_prop("leadsId", "leads_id")])
        assert api_field_to_backing(ot, "leadsId") == "leads_id"

    def test_passthrough_unknown_field(self):
        ot = _ot([_prop("leadsId", "leads_id")])
        assert api_field_to_backing(ot, "visibility") == "visibility"

    def test_passthrough_when_no_mapping(self):
        ot = _ot([_prop("status")])
        assert api_field_to_backing(ot, "status") == "status"

    def test_none_ot_passthrough(self):
        assert api_field_to_backing(None, "anything") == "anything"
