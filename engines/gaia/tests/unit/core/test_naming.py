"""Unit tests for ontology.core.naming (v5.2 namespace isolation)."""

import pytest

from ontology.core import naming


class TestSyncPipeline:
    def test_uses_dataset_api_name_no_ontology_prefix(self):
        # SYNC consumer is a Dataset (globally unique) — no ontology needed.
        assert naming.sync_pipeline("order_raw") == "sync_order_raw"

    def test_validates_identifier(self):
        with pytest.raises(ValueError, match="Invalid identifier"):
            naming.sync_pipeline("order-raw")  # hyphen not allowed


class TestDorisIndexTable:
    def test_encodes_ontology_prefix(self):
        # ObjectType api_name is only unique within an ontology, so the
        # ontology prefix is mandatory to avoid cross-ontology collision.
        assert naming.doris_index_table("ont1", "asset") == "idx_ont1__asset"
        assert naming.doris_index_table("ont2", "asset") == "idx_ont2__asset"

    def test_two_ontologies_same_type_do_not_collide(self):
        a = naming.doris_index_table("factory", "sensor")
        b = naming.doris_index_table("warehouse", "sensor")
        assert a != b

    def test_snake_case_preserves_word_boundaries(self):
        # PascalCase inputs are converted to snake_case, not flattened to
        # all-lower (FlightStatusLog → flight_status_log, not flightstatuslog).
        assert naming.doris_index_table("Mfg", "FlightStatusLog") == "idx_mfg__flight_status_log"


class TestIndexPipeline:
    def test_encodes_ontology_prefix(self):
        assert naming.index_pipeline("ont1", "asset") == "index_ont1__asset"

    def test_matches_doris_table_namespace(self):
        # INDEX pipeline writes into the Doris idx table; both must share
        # the {ontology}__{type} namespace so deprovision targets the
        # correct pipeline (fixes the prior sync_{type} bug).
        ont, ot = "mfg", "device"
        assert naming.doris_index_table(ont, ot).startswith("idx_")
        assert naming.index_pipeline(ont, ot).startswith("index_")
        assert naming.doris_index_table(ont, ot).endswith(f"__{ot}")
        assert naming.index_pipeline(ont, ot).endswith(f"__{ot}")

    def test_snake_case_preserves_word_boundaries(self):
        assert naming.index_pipeline("Mfg", "FlightStatusLog") == "index_mfg__flight_status_log"


class TestIndexPipelineSplit:
    """ADR-008 模式选择评估: INDEX sync splits into backfill (BATCH) + stream (STREAMING)."""

    def test_backfill_name_has_suffix(self):
        assert naming.index_backfill_pipeline("shop", "order") == "index_shop__order__backfill"

    def test_stream_name_has_suffix(self):
        assert naming.index_stream_pipeline("shop", "order") == "index_shop__order__stream"

    def test_backfill_and_stream_are_distinct(self):
        # Distinct jobNames so the two jobs (different lifecycles) never collide.
        b = naming.index_backfill_pipeline("shop", "order")
        s = naming.index_stream_pipeline("shop", "order")
        assert b != s

    def test_both_share_group_prefix(self):
        # Both derive from index_pipeline (the logical group name).
        group = naming.index_pipeline("shop", "order")
        assert naming.index_backfill_pipeline("shop", "order").startswith(group)
        assert naming.index_stream_pipeline("shop", "order").startswith(group)

    def test_snake_case_preserved(self):
        assert naming.index_backfill_pipeline("Mfg", "FlightStatusLog") == "index_mfg__flight_status_log__backfill"
        assert naming.index_stream_pipeline("Mfg", "FlightStatusLog") == "index_mfg__flight_status_log__stream"


class TestIcebergS3Location:
    def test_includes_ontology_directory_and_trailing_slash(self):
        loc = naming.iceberg_s3_location("ont1", "asset")
        assert loc == "s3://ontology-warehouse/ont1/asset/"
        # trailing slash = Iceberg table *location* (directory), not object
        assert loc.endswith("/")

    def test_two_ontologies_isolated_by_directory(self):
        a = naming.iceberg_s3_location("ont1", "asset")
        b = naming.iceberg_s3_location("ont2", "asset")
        assert a != b

    def test_snake_case_preserves_word_boundaries(self):
        assert naming.iceberg_s3_location("Mfg", "FlightStatusLog") == "s3://ontology-warehouse/mfg/flight_status_log/"


class TestManagedDatasetApiName:
    """managed_dataset_api_name: PascalCase ObjectType api_name → snake_case dataset api_name.

    The dataset api_name doubles as the physical Iceberg table name, and
    Iceberg table names must be all-lower (SeaTunnel SYNC/INDEX pipelines
    sink/source via snake_case; Trino's iceberg REST client lower-cases on
    lookup while the server preserves case). So the derived name is
    snake_case (all-lower, word boundaries preserved), not merely camelCase
    nor flattened all-lower.
    """

    @pytest.mark.parametrize(
        "ot, expected",
        [
            ("Flight", "flight"),
            ("FlightStatusLog", "flight_status_log"),  # snake_case, NOT flightstatuslog
            ("Employee", "employee"),
            ("A", "a"),
            ("URLShortener", "url_shortener"),  # 连续大写缩写词作整词处理
            ("CustomerOrder", "customer_order"),
        ],
    )
    def test_converts_to_snake_case(self, ot, expected):
        assert naming.managed_dataset_api_name(ot) == expected

    def test_result_satisfies_dataset_pattern(self):
        import re

        from ontology.core.naming import DATASET_API_NAME_PATTERN

        # The derived dataset api_name must be a valid snake_case identifier.
        for ot in ["Flight", "FlightStatusLog", "Employee", "CustomerOrder"]:
            ds = naming.managed_dataset_api_name(ot)
            assert re.match(DATASET_API_NAME_PATTERN, ds), ds

    def test_result_equals_iceberg_table_name_convention(self):
        # SeaTunnel SYNC/INDEX pipelines look up the Iceberg table via
        # snake_case conversion of dataset_api_name / object_type_api_name.
        # The derived dataset api_name must already be all-lower so it
        # round-trips through Trino without a NoSuchTableException.
        for ot in ["Flight", "FlightStatusLog", "URLShortener"]:
            ds = naming.managed_dataset_api_name(ot)
            assert ds == ds.lower()

    @pytest.mark.parametrize("bad", ["", "flight", "123", "_priv"])
    def test_rejects_non_pascal_case(self, bad):
        with pytest.raises(ValueError):
            naming.managed_dataset_api_name(bad)


class TestValidateIdentifier:
    @pytest.mark.parametrize(
        "name",
        ["asset", "order_raw", "_priv", "t1", "A", "x_y_z", "type2"],
    )
    def test_accepts_valid(self, name):
        assert naming.validate_identifier(name) == name

    @pytest.mark.parametrize(
        "name",
        ["", "1lead", "has-dash", "has.dot", "has space", "has$spec", "café"],
    )
    def test_rejects_invalid(self, name):
        with pytest.raises(ValueError):
            naming.validate_identifier(name)


class TestGraphLabel:
    """graph_label: Neo4j 节点标签 = {Ontology}{ObjectType} PascalCase 带本体前缀。"""

    def test_pascalcase_with_ontology_prefix(self):
        assert naming.graph_label("SupplyChain", "Supplier") == "SupplyChainSupplier"
        assert naming.graph_label("SupplyChain", "Order") == "SupplyChainOrder"

    def test_two_ontologies_same_type_do_not_collide(self):
        a = naming.graph_label("Factory", "Sensor")
        b = naming.graph_label("Warehouse", "Sensor")
        assert a != b

    def test_preserves_pascalcase_word_boundaries(self):
        # FlightStatusLog 保留词界，不折叠
        assert naming.graph_label("Mfg", "FlightStatusLog") == "MfgFlightStatusLog"

    def test_rejects_non_pascalcase(self):
        # ontology api_name 必须 PascalCase（首字母大写）
        with pytest.raises(ValueError):
            naming.graph_label("supplychain", "Supplier")
        with pytest.raises(ValueError):
            naming.graph_label("SupplyChain", "supplier")  # OT 必须 PascalCase


class TestGraphRelationshipType:
    """graph_relationship_type: Neo4j 关系类型 = {Ontology}{LinkType} PascalCase。

    LinkType api_name 是 camelCase，首字母大写化后拼接本体前缀。
    """

    def test_capitalizes_link_then_prefixes_ontology(self):
        assert naming.graph_relationship_type("SupplyChain", "supplies") == "SupplyChainSupplies"
        assert naming.graph_relationship_type("SupplyChain", "hasItems") == "SupplyChainHasItems"

    def test_two_ontologies_same_link_do_not_collide(self):
        a = naming.graph_relationship_type("Factory", "produces")
        b = naming.graph_relationship_type("Warehouse", "produces")
        assert a != b

    def test_rejects_empty_link(self):
        with pytest.raises(ValueError):
            naming.graph_relationship_type("SupplyChain", "")


class TestGeoTable:
    """geo_table: PostGIS 静态空间表 = geo_{ont}__{type} snake_case 带本体前缀。"""

    def test_snake_case_with_ontology_prefix(self):
        assert naming.geo_table("SupplyChain", "Supplier") == "geo_supply_chain__supplier"
        assert naming.geo_table("ont1", "asset") == "geo_ont1__asset"

    def test_two_ontologies_same_type_do_not_collide(self):
        a = naming.geo_table("factory", "sensor")
        b = naming.geo_table("warehouse", "sensor")
        assert a != b

    def test_snake_case_preserves_word_boundaries(self):
        assert naming.geo_table("Mfg", "FlightStatusLog") == "geo_mfg__flight_status_log"

    def test_rejects_invalid_identifier(self):
        with pytest.raises(ValueError):
            naming.geo_table("has-dash", "asset")


class TestTimeseriesHypertable:
    """timeseries_hypertable: TimescaleDB 超表 = timeseries_{ont}__{type}__{series}。

    一个对象的每个时序属性对应一张超表，三段式命名。
    """

    def test_three_part_snake_case(self):
        assert naming.timeseries_hypertable("Logistics", "Vehicle", "track") == "timeseries_logistics__vehicle__track"
        assert (
            naming.timeseries_hypertable("ont1", "asset", "inventoryHistory")
            == "timeseries_ont1__asset__inventory_history"
        )

    def test_two_series_do_not_collide(self):
        a = naming.timeseries_hypertable("Logistics", "Vehicle", "track")
        b = naming.timeseries_hypertable("Logistics", "Vehicle", "fuelHistory")
        assert a != b

    def test_snake_case_preserves_word_boundaries(self):
        assert (
            naming.timeseries_hypertable("Mfg", "FlightStatusLog", "telemetry")
            == "timeseries_mfg__flight_status_log__telemetry"
        )

    def test_rejects_invalid_identifier(self):
        with pytest.raises(ValueError):
            naming.timeseries_hypertable("ont1", "asset", "has-dash")


# ═══════════════════════════════════════════════════════════════════
# Pipeline Builder (ADR-018) naming
# ═══════════════════════════════════════════════════════════════════


class TestKestraNaming:
    """kestra_flow_id / kestra_namespace (ADR-018 #3 naming收口)."""

    def test_kestra_flow_id(self) -> None:
        assert naming.kestra_flow_id("cust_etl") == "pipeline_cust_etl"

    def test_kestra_flow_id_rejects_invalid(self) -> None:
        with pytest.raises(ValueError, match="identifier"):
            naming.kestra_flow_id("bad-name!")

    def test_kestra_namespace_default(self) -> None:
        assert naming.kestra_namespace("pipelines") == "gaia.pipelines"

    def test_kestra_namespace_custom_project(self) -> None:
        assert naming.kestra_namespace("marketing") == "gaia.marketing"

    def test_kestra_namespace_rejects_invalid(self) -> None:
        with pytest.raises(ValueError, match="identifier"):
            naming.kestra_namespace("bad ns")
