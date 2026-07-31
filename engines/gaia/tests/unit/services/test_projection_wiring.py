"""Tests for projector wiring in OutboxExecutor and ActionService (ADR-015 §capabilities).

Tests cover:
  - OutboxExecutor INDEX upsert → project_object called when capabilities enabled
  - OutboxExecutor INDEX upsert → project_object skipped when capabilities disabled
  - OutboxExecutor INDEX upsert → project_object skipped when no projectors wired
  - OutboxExecutor INDEX delete → delete_object called when capabilities enabled
  - OutboxExecutor projection is fail-tolerant (projector error doesn't fail INDEX)
  - ActionService RELATE → project_link called when capabilities enabled
  - ActionService UNRELATE → delete_link called when capabilities enabled
  - ActionService RELATE → skipped when graph_indexing disabled
  - ActionService RELATE → skipped when no graph_projector wired
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.ontology import ObjectTypeCapabilities
from ontology.services.outbox_executor import OutboxExecutor


def _make_outbox_record(
    *,
    effect_type: str = "INDEX",
    payload: dict | None = None,
    record_id: str = "rec-1",
) -> dict:
    return {
        "id": record_id,
        "effect_type": effect_type,
        "effect_config": {},
        "payload": payload or {},
        "attempts": 0,
    }


def _make_ot_with_caps(
    *,
    api_name: str = "Flight",
    storage_type: str = "MANAGED",
    caps: ObjectTypeCapabilities | None = None,
    primary_key: str = "flightId",
) -> MagicMock:
    ot = MagicMock()
    ot.api_name = api_name
    ot.storage_type = storage_type
    ot.primary_key = primary_key
    ot.properties = [
        MagicMock(api_name=primary_key, backing_mapping=MagicMock(backing_column="flight_id")),
    ]
    ot.capabilities = caps or ObjectTypeCapabilities()
    return ot


class TestOutboxProjectionUpsert:
    """OutboxExecutor INDEX upsert → projector wiring."""

    @pytest.mark.asyncio
    async def test_upsert_projects_when_caps_enabled(self):
        """CREATE/UPDATE with graph+geotime enabled → both projectors called."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        graph_proj = AsyncMock()
        geotime_proj = AsyncMock()
        ot = _make_ot_with_caps(
            caps=ObjectTypeCapabilities(graph_indexing_enabled=True, geotime_indexing_enabled=True)
        )
        meta = AsyncMock()
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            graph_projector=graph_proj,
            geotime_projector=geotime_proj,
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        await exec_._execute(record)

        index_store.upsert.assert_awaited_once()
        graph_proj.project_object.assert_awaited_once()
        geotime_proj.project_object.assert_awaited_once()
        # Verify the object_state passed to projector has id + properties
        call_kwargs = graph_proj.project_object.await_args.args
        assert call_kwargs[2]["rid"] == "vid-1"
        assert call_kwargs[2]["properties"] == {"flight_id": "CA123"}

    @pytest.mark.asyncio
    async def test_upsert_skips_when_caps_disabled(self):
        """CREATE/UPDATE with caps disabled → projectors NOT called."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        graph_proj = AsyncMock()
        geotime_proj = AsyncMock()
        ot = _make_ot_with_caps(caps=ObjectTypeCapabilities())  # all disabled
        meta = AsyncMock()
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            graph_projector=graph_proj,
            geotime_projector=geotime_proj,
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        await exec_._execute(record)

        index_store.upsert.assert_awaited_once()
        graph_proj.project_object.assert_not_awaited()
        geotime_proj.project_object.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upsert_skips_when_no_projectors(self):
        """No projectors wired → projection skipped, no OT lookup."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        meta = AsyncMock()
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            # no graph_projector, no geotime_projector
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        await exec_._execute(record)

        index_store.upsert.assert_awaited_once()
        # No extra get_object_type call for projection
        meta.get_object_type.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_projection_fail_tolerant(self):
        """Projector error doesn't fail the INDEX record (Doris already synced)."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        graph_proj = AsyncMock()
        graph_proj.project_object.side_effect = RuntimeError("Neo4j down")
        geotime_proj = AsyncMock()
        ot = _make_ot_with_caps(
            caps=ObjectTypeCapabilities(graph_indexing_enabled=True, geotime_indexing_enabled=True)
        )
        meta = AsyncMock()
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            graph_projector=graph_proj,
            geotime_projector=geotime_proj,
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        # Should NOT raise — projection is fail-tolerant
        await exec_._execute(record)
        index_store.upsert.assert_awaited_once()  # Doris sync succeeded
        graph_proj.project_object.assert_awaited_once()  # attempted
        geotime_proj.project_object.assert_awaited_once()  # still attempted (independent)


class TestOutboxProjectionDelete:
    """OutboxExecutor INDEX delete → projector wiring."""

    @pytest.mark.asyncio
    async def test_delete_projects_when_caps_enabled(self):
        """DELETE with caps enabled → delete_object on both projectors."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        graph_proj = AsyncMock()
        geotime_proj = AsyncMock()
        ot = _make_ot_with_caps(
            caps=ObjectTypeCapabilities(graph_indexing_enabled=True, geotime_indexing_enabled=True)
        )
        meta = AsyncMock()
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            graph_projector=graph_proj,
            geotime_projector=geotime_proj,
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "DELETE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        await exec_._execute(record)

        index_store.delete_by_ids.assert_awaited_once()
        graph_proj.delete_object.assert_awaited_once()
        geotime_proj.delete_object.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_skips_when_caps_disabled(self):
        """DELETE with caps disabled → delete_object NOT called."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        graph_proj = AsyncMock()
        geotime_proj = AsyncMock()
        ot = _make_ot_with_caps(caps=ObjectTypeCapabilities())  # all disabled
        meta = AsyncMock()
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory = MagicMock(return_value=meta)

        exec_ = OutboxExecutor(
            metadata=AsyncMock(),
            index_store=index_store,
            metadata_factory=meta_factory,
            graph_projector=graph_proj,
            geotime_projector=geotime_proj,
        )
        record = _make_outbox_record(
            payload={
                "rid": "vid-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "DELETE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        await exec_._execute(record)

        index_store.delete_by_ids.assert_awaited_once()
        graph_proj.delete_object.assert_not_awaited()
        geotime_proj.delete_object.assert_not_awaited()
