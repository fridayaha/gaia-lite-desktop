"""Unit tests for PipelineBuilderService — CRUD, versions, validation, deploy, build.

Uses mock AsyncSession to avoid real database dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.models.pipeline import (
    PipelineExecutionModel,
    PipelineModel,
    PipelineScheduleModel,
    PipelineVersionModel,
)
from ontology.core.schemas.pipeline_builder import (
    ActionConfig,
    PipelineCreate,
    PipelineIR,
    PipelineUpdate,
    ScheduleCreate,
    TriggerConfig,
)
from ontology.services.pipeline_builder_service import PipelineBuilderService

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_service() -> PipelineBuilderService:
    """Create a PipelineBuilderService with a mock PostgresMetaStore.

    The mock store wraps a mock AsyncSession and provides a transaction()
    async context manager that commits/rollbacks as a no-op (tests verify
    session.add/flush calls, not real DB commits).
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.delete = AsyncMock()

    # PostgresMetaStore wraps the session; transaction() is an async ctx mgr
    # that yields then commits. Mock it to yield (no real commit).
    from ontology.services.schema_inference_engine import SchemaInferenceEngine

    return PipelineBuilderService(
        metadata=_make_mock_metadata(session),
        schema_engine=SchemaInferenceEngine(),
    )


def mock_scalar_result(value):
    """Create a mock execute result that returns a scalar."""
    m = MagicMock()
    m.scalar_one_or_none = MagicMock(return_value=value)
    m.scalar = MagicMock(return_value=value)
    m.scalars = MagicMock()
    m.scalars.return_value.all = MagicMock(return_value=[])
    return m


def _make_mock_metadata(session: AsyncMock) -> MagicMock:
    """Wrap a mock AsyncSession in a mock PostgresMetaStore (transaction() yields).

    Service tests construct PipelineBuilderService(metadata=...) — this helper
    builds the mock metadata so tests don't repeat the transaction() boilerplate.
    """
    from contextlib import asynccontextmanager

    metadata = MagicMock()
    metadata._session = session
    metadata.close = AsyncMock()

    @asynccontextmanager
    async def _mock_transaction():
        try:
            yield
        except Exception:
            session.rollback = AsyncMock()
            await session.rollback()
            raise
        await session.commit()

    metadata.transaction = _mock_transaction
    return metadata


def mock_scalars_result(values):
    """Create a mock execute result that returns multiple scalars (list)."""
    m = MagicMock()
    m.scalars = MagicMock()
    m.scalars.return_value.all = MagicMock(return_value=values)
    m.scalar_one_or_none = MagicMock(return_value=None)
    m.scalar = MagicMock(return_value=len(values) if values else 0)
    return m


# ═══════════════════════════════════════════════════════════════════
# Pipeline CRUD
# ═══════════════════════════════════════════════════════════════════


class TestCreatePipeline:
    """POST /pipelines — create with initial version."""

    async def test_create_success(self, mock_service: PipelineBuilderService) -> None:
        # No existing pipeline so the first query returns None
        mock_service._session.execute.return_value = mock_scalar_result(None)

        data = PipelineCreate(
            api_name="test_pipeline",
            display_name="Test Pipeline",
            sink_dataset_api_name="output_dataset",
            graph=PipelineIR(nodes=[]),
        )

        result = await mock_service.create_pipeline(data)
        assert result.api_name == "test_pipeline"
        assert result.status == "DRAFT"
        assert result.current_version_number == 1
        mock_service._session.add.assert_called()
        mock_service._session.commit.assert_awaited()

    async def test_create_duplicate_api_name(self, mock_service: PipelineBuilderService) -> None:
        mock_service._session.execute.return_value = mock_scalar_result(
            PipelineModel(
                api_name="dup",
                display_name="Dup",
                sink_dataset_api_name="ds",
                status="DRAFT",
                write_mode="FULL_REFRESH",
            )
        )
        data = PipelineCreate(
            api_name="dup",
            display_name="Duplicate",
            sink_dataset_api_name="ds",
        )
        with pytest.raises(ConflictError):
            await mock_service.create_pipeline(data)


class TestGetPipeline:
    """GET /pipelines/{api_name}."""

    async def test_get_existing(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        # Also return a version number when queried
        mock_service._session.get = AsyncMock(return_value=None)

        result = await mock_service.get_pipeline("test")
        assert result.api_name == "test"

    async def test_get_not_found(self, mock_service: PipelineBuilderService) -> None:
        mock_service._session.execute.return_value = mock_scalar_result(None)
        with pytest.raises(NotFoundError):
            await mock_service.get_pipeline("nonexistent")


class TestListPipelines:
    """GET /pipelines."""

    async def test_list_empty(self, mock_service: PipelineBuilderService) -> None:
        # execute: count returns scalar=0, list returns scalars=[]
        mock_service._session.execute.side_effect = [
            mock_scalar_result(0),  # count query
            mock_scalars_result([]),  # list query
        ]
        items, total = await mock_service.list_pipelines()
        assert total == 0
        assert items == []

    async def test_list_with_items(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            api_name="p1",
            display_name="Pipeline 1",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.side_effect = [
            mock_scalar_result(1),  # count query → 1
            mock_scalars_result([pipeline]),  # list query → [pipeline]
        ]
        items, total = await mock_service.list_pipelines()
        assert total == 1
        assert len(items) == 1


class TestUpdatePipeline:
    """PATCH /pipelines/{api_name}."""

    async def test_update_display_name(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            api_name="test",
            display_name="Old Name",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        data = PipelineUpdate(display_name="New Name")
        result = await mock_service.update_pipeline("test", data)
        assert result.display_name == "New Name"

    async def test_update_with_graph_creates_new_version(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.side_effect = [
            mock_scalar_result(pipeline),  # _get_pipeline_or_raise
            mock_scalar_result(2),  # max version number
            mock_scalar_result(None),  # _get_version_number → no version (just created)
        ]

        data = PipelineUpdate(graph=PipelineIR(nodes=[]), change_summary="Added filter")
        result = await mock_service.update_pipeline("test", data)
        assert result.display_name == "Test"

    async def test_update_not_found(self, mock_service: PipelineBuilderService) -> None:
        mock_service._session.execute.return_value = mock_scalar_result(None)
        with pytest.raises(NotFoundError):
            await mock_service.update_pipeline("nonexistent", PipelineUpdate(display_name="X"))


class TestDeletePipeline:
    """DELETE /pipelines/{api_name}."""

    async def test_soft_delete(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)
        await mock_service.delete_pipeline("test")
        assert pipeline.deleted_at is not None
        mock_service._session.commit.assert_awaited()


# ═══════════════════════════════════════════════════════════════════
# Pipeline Versions
# ═══════════════════════════════════════════════════════════════════


class TestVersions:
    """Version listing, retrieval, rollback."""

    async def test_list_versions(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)
        versions = await mock_service.list_versions("test")
        assert isinstance(versions, list)

    async def test_rollback_version(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        mock_service._session.execute.side_effect = [
            mock_scalar_result(pipeline),  # _get_pipeline_or_raise (rollback_version)
            mock_scalar_result(version),  # get version by number
            mock_scalar_result(pipeline),  # _get_pipeline_or_raise (inside get_pipeline)
            mock_scalar_result(None),  # _get_version_number (in get_pipeline)
        ]

        result = await mock_service.rollback_version("test", 1)
        assert result.api_name == "test"

    async def test_rollback_nonexistent_version(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.side_effect = [
            mock_scalar_result(pipeline),  # _get_pipeline_or_raise
            mock_scalar_result(None),  # version not found
        ]

        with pytest.raises(NotFoundError):
            await mock_service.rollback_version("test", 999)


# ═══════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════


class TestValidation:
    """Pipeline IR validation."""

    async def test_validate_with_api_name(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        mock_service._session.get = AsyncMock(return_value=version)

        result = await mock_service.validate_pipeline(api_name="test")
        assert isinstance(result.valid, bool)

    async def test_validate_with_graph(self, mock_service: PipelineBuilderService) -> None:
        ir = PipelineIR(nodes=[], edges=[])
        result = await mock_service.validate_pipeline(graph=ir.model_dump(mode="json"))
        assert isinstance(result.valid, bool)

    async def test_validate_resolves_source_dataset_schemas(self) -> None:
        """validate_pipeline should fetch Source dataset schemas and inject them.

        Reproduces the fix for: Join config panel showed "请先将两个上游节点
        连接到本节点" because Source output schema was empty. The service
        must pre-fetch dataset schemas from IcebergStore and pass them to
        the schema inference engine.
        """
        from unittest.mock import AsyncMock, MagicMock

        from ontology.core.schemas.dataset import ColumnDef, DatasetSchema
        from ontology.core.schemas.pipeline_builder import (
            IRNode,
            PipelineIR,
        )
        from ontology.services.pipeline_builder_service import PipelineBuilderService

        session = AsyncMock()
        metadata = MagicMock()
        metadata._session = session
        metadata.get_dataset = AsyncMock()
        # Two MANAGED datasets with different schemas
        metadata.get_dataset.side_effect = [
            MagicMock(kind="MANAGED", storage_location=""),
            MagicMock(kind="MANAGED", storage_location=""),
        ]
        dataset_store = MagicMock()
        dataset_store.get_schema = AsyncMock(
            side_effect=[
                DatasetSchema(columns=[ColumnDef(name="id", type="STRING"), ColumnDef(name="a", type="STRING")]),
                DatasetSchema(columns=[ColumnDef(name="id", type="STRING"), ColumnDef(name="b", type="INTEGER")]),
            ]
        )
        svc = PipelineBuilderService(metadata=metadata, dataset=dataset_store)

        graph = PipelineIR(
            nodes=[
                IRNode(id="s1", type="Source", operator_type="Source", config={"extra": {"dataset": "ds_a"}}),
                IRNode(id="s2", type="Source", operator_type="Source", config={"extra": {"dataset": "ds_b"}}),
            ],
            edges=[],
        )
        result = await svc.validate_pipeline(graph=graph)

        # Both Source nodes got real schemas from the dataset store
        s1_fields = [f.name for f in result.node_schemas["s1"].fields]
        s2_fields = [f.name for f in result.node_schemas["s2"].fields]
        assert s1_fields == ["id", "a"], f"s1 should have dataset schema, got {s1_fields}"
        assert s2_fields == ["id", "b"], f"s2 should have dataset schema, got {s2_fields}"
        # Types preserved
        assert any(f.data_type == "INTEGER" for f in result.node_schemas["s2"].fields)

    async def test_validate_source_schema_best_effort_on_failure(self) -> None:
        """Source schema fetch failures must not block validation.

        If IcebergStore raises (e.g. table doesn't exist yet), the Source
        node gets an empty schema but validation still completes — editing
        must stay responsive.
        """
        from unittest.mock import AsyncMock, MagicMock

        from ontology.core.schemas.pipeline_builder import IRNode, PipelineIR
        from ontology.services.pipeline_builder_service import PipelineBuilderService

        session = AsyncMock()
        metadata = MagicMock()
        metadata._session = session
        metadata.get_dataset = AsyncMock(return_value=MagicMock(kind="MANAGED", storage_location=""))
        dataset_store = MagicMock()
        dataset_store.get_schema = AsyncMock(side_effect=RuntimeError("table not found"))
        svc = PipelineBuilderService(metadata=metadata, dataset=dataset_store)

        graph = PipelineIR(
            nodes=[IRNode(id="s1", type="Source", operator_type="Source", config={"extra": {"dataset": "missing"}})],
            edges=[],
        )
        # Should not raise
        result = await svc.validate_pipeline(graph=graph)
        assert result.valid is True
        # Source schema absent from injected map → falls back to empty (registry default)
        assert result.node_schemas["s1"].fields == []

    async def test_validate_nonexistent_pipeline(self, mock_service: PipelineBuilderService) -> None:
        mock_service._session.execute.return_value = mock_scalar_result(None)
        with pytest.raises(NotFoundError):
            await mock_service.validate_pipeline(api_name="nonexistent")


# ═══════════════════════════════════════════════════════════════════
# Deploy & Build
# ═══════════════════════════════════════════════════════════════════


class TestDeploy:
    """POST /pipelines/{api_name}/deploy."""

    async def test_deploy_success(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        mock_service._session.get = AsyncMock(return_value=version)

        result = await mock_service.deploy_pipeline("test")
        assert result.status == "PUBLISHED"
        assert result.deployed_version_id == "v1"

    async def test_deploy_no_version(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        with pytest.raises(ValidationError):
            await mock_service.deploy_pipeline("test")


class TestTriggerBuild:
    """POST /pipelines/{api_name}/builds."""

    async def test_trigger_build_success(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=2,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        mock_service._session.get = AsyncMock(return_value=version)

        result = await mock_service.trigger_build("test")
        assert result.status == "PENDING"
        assert result.version_number == 2

    async def test_trigger_build_no_version(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        with pytest.raises(ValidationError):
            await mock_service.trigger_build("test")


class TestCancelBuild:
    """POST /pipelines/{api_name}/builds/{build_id}/cancel."""

    async def test_cancel_pending_build(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        execution = PipelineExecutionModel(
            id="e1",
            pipeline_id="p1",
            version_id="v1",
            trigger_type="MANUAL",
            current_state="PENDING",
            created_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(execution)

        result = await mock_service.cancel_build("test", "e1")
        assert result.status == "CANCELLED"

    async def test_cancel_terminated_build_fails(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        execution = PipelineExecutionModel(
            id="e1",
            pipeline_id="p1",
            version_id="v1",
            trigger_type="MANUAL",
            current_state="SUCCESS",
            created_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(execution)

        with pytest.raises(ValidationError):
            await mock_service.cancel_build("test", "e1")


class TestDataRollback:
    """POST /pipelines/{api_name}/builds/{build_id}/rollback."""

    async def test_rollback_no_snapshot_fails(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        execution = PipelineExecutionModel(
            id="e1",
            pipeline_id="p1",
            version_id="v1",
            trigger_type="MANUAL",
            current_state="SUCCESS",
            output_snapshot_id=None,
            created_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(execution)

        with pytest.raises(ValidationError):
            await mock_service.data_rollback("test", "e1")


# ═══════════════════════════════════════════════════════════════════
# Schedules
# ═══════════════════════════════════════════════════════════════════


class TestSchedules:
    """Schedule CRUD."""

    async def test_create_schedule(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            status="DRAFT",
            write_mode="FULL_REFRESH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        # First execute = _get_pipeline_or_raise → pipeline
        # Second execute = check existing schedule → None (not found)
        mock_service._session.execute.side_effect = [
            mock_scalar_result(pipeline),
            mock_scalar_result(None),
        ]

        data = ScheduleCreate(
            api_name="daily",
            trigger=TriggerConfig(type="time", cron="0 9 * * *"),
            action_config=ActionConfig(retry_count=2),
        )
        result = await mock_service.create_schedule("test", data)
        assert result.api_name == "daily"
        assert result.trigger.cron == "0 9 * * *"

    async def test_create_duplicate_schedule(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        existing_schedule = PipelineScheduleModel(
            id="s1",
            pipeline_id="p1",
            api_name="daily",
            trigger={"type": "time", "cron": "0 9 * * *"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(existing_schedule)

        data = ScheduleCreate(
            api_name="daily",
            trigger=TriggerConfig(type="time", cron="0 10 * * *"),
        )
        with pytest.raises(ConflictError):
            await mock_service.create_schedule("test", data)

    async def test_list_schedules(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        schedules = await mock_service.list_schedules("test")
        assert isinstance(schedules, list)

    async def test_toggle_schedule(self, mock_service: PipelineBuilderService) -> None:
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        schedule = PipelineScheduleModel(
            id="s1",
            pipeline_id="p1",
            api_name="daily",
            trigger={"type": "time", "cron": "0 9 * * *"},
            enabled=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(schedule)

        result = await mock_service.toggle_schedule("test", "daily", enabled=False)
        assert result.enabled is False


# ═══════════════════════════════════════════════════════════════════
# Deprecate pipeline (ADR-018: PUBLISHED → DEPRECATED + Kestra undeploy)
# ═══════════════════════════════════════════════════════════════════


class TestDeprecatePipeline:
    async def test_deprecate_published_pipeline(self, mock_service: PipelineBuilderService) -> None:
        """Deprecating a PUBLISHED pipeline transitions to DEPRECATED."""
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            status="PUBLISHED",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=3,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        mock_service._session.get = AsyncMock(return_value=version)

        result = await mock_service.deprecate_pipeline("test")
        assert result.status == "DEPRECATED"
        assert result.deployed_version_id == "v1"
        assert result.deployed_version_number == 3

    async def test_deprecate_draft_pipeline_fails(self, mock_service: PipelineBuilderService) -> None:
        """Deprecating a DRAFT pipeline (not PUBLISHED) should raise ValidationError."""
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            status="DRAFT",
            sink_dataset_api_name="ds",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_service._session.execute.return_value = mock_scalar_result(pipeline)

        with pytest.raises(ValidationError):
            await mock_service.deprecate_pipeline("test")

    async def test_deprecate_with_kestra_engine_calls_undeploy(self) -> None:
        """Deprecate should call KestraEngine.undeploy (best-effort)."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        pipeline = PipelineModel(
            id="p1",
            api_name="test",
            display_name="Test",
            status="PUBLISHED",
            sink_dataset_api_name="ds",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.execute.return_value = mock_scalar_result(pipeline)
        session.get = AsyncMock(return_value=None)

        kestra = AsyncMock()
        kestra.undeploy = AsyncMock(return_value=None)

        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        svc = PipelineBuilderService(
            metadata=_make_mock_metadata(session),
            schema_engine=SchemaInferenceEngine(),
            kestra_engine=kestra,
        )
        result = await svc.deprecate_pipeline("test")
        assert result.status == "DEPRECATED"
        kestra.undeploy.assert_awaited_once_with("test", namespace="gaia.pipelines")


# ═══════════════════════════════════════════════════════════════════
# Deploy with KestraEngine integration
# ═══════════════════════════════════════════════════════════════════


class TestDeployWithKestra:
    async def test_deploy_calls_kestra_engine(self) -> None:
        """Deploy should translate IR and call KestraEngine.deploy."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        pipeline = PipelineModel(
            id="p1",
            api_name="cust_etl",
            display_name="Customer ETL",
            status="DRAFT",
            sink_dataset_api_name="ds_out",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.execute.return_value = mock_scalar_result(pipeline)
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        session.get = AsyncMock(return_value=version)

        kestra = AsyncMock()
        kestra.deploy = AsyncMock(
            return_value={
                "id": "pipeline_cust_etl",
                "namespace": "gaia.pipelines",
            }
        )

        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        svc = PipelineBuilderService(
            metadata=_make_mock_metadata(session),
            schema_engine=SchemaInferenceEngine(),
            kestra_engine=kestra,
        )
        result = await svc.deploy_pipeline("cust_etl")
        assert result.status == "PUBLISHED"
        assert result.kestra_flow_id == "pipeline_cust_etl"
        assert result.kestra_namespace == "gaia.pipelines"
        kestra.deploy.assert_awaited_once()

    async def test_deploy_swallows_kestra_unavailable(self) -> None:
        """Deploy should still update status when Kestra is unavailable (best-effort)."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        pipeline = PipelineModel(
            id="p1",
            api_name="cust_etl",
            display_name="Customer ETL",
            status="DRAFT",
            sink_dataset_api_name="ds_out",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.execute.return_value = mock_scalar_result(pipeline)
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        session.get = AsyncMock(return_value=version)

        from ontology.layers.pipeline.kestra_engine import KestraUnavailableError

        kestra = AsyncMock()
        kestra.deploy = AsyncMock(side_effect=KestraUnavailableError("connection refused"))

        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        svc = PipelineBuilderService(
            metadata=_make_mock_metadata(session),
            schema_engine=SchemaInferenceEngine(),
            kestra_engine=kestra,
        )
        result = await svc.deploy_pipeline("cust_etl")
        # Status still updated, kestra_flow_id is None (best-effort)
        assert result.status == "PUBLISHED"
        assert result.kestra_flow_id is None


# ═══════════════════════════════════════════════════════════════════
# Trigger build with KestraEngine integration
# ═══════════════════════════════════════════════════════════════════


class TestTriggerBuildWithKestra:
    async def test_trigger_build_backfills_kestra_execution_id(self) -> None:
        """trigger_build should call Kestra and backfill kestra_execution_id."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        pipeline = PipelineModel(
            id="p1",
            api_name="cust_etl",
            display_name="Customer ETL",
            status="PUBLISHED",
            sink_dataset_api_name="ds_out",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.execute.return_value = mock_scalar_result(pipeline)
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        session.get = AsyncMock(return_value=version)

        kestra = AsyncMock()
        kestra.trigger_build = AsyncMock(
            return_value={
                "id": "kestra-exec-123",
                "flowId": "pipeline_cust_etl",
                "namespace": "gaia.pipelines",
            }
        )

        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        svc = PipelineBuilderService(
            metadata=_make_mock_metadata(session),
            schema_engine=SchemaInferenceEngine(),
            kestra_engine=kestra,
        )
        result = await svc.trigger_build("cust_etl")
        assert result.build_id  # non-empty
        kestra.trigger_build.assert_awaited_once()

    async def test_trigger_build_marks_failed_on_kestra_error(self) -> None:
        """trigger_build should mark execution FAILED if Kestra raises."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        pipeline = PipelineModel(
            id="p1",
            api_name="cust_etl",
            display_name="Customer ETL",
            status="PUBLISHED",
            sink_dataset_api_name="ds_out",
            current_version_id="v1",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.execute.return_value = mock_scalar_result(pipeline)
        version = PipelineVersionModel(
            id="v1",
            pipeline_id="p1",
            version_number=1,
            graph={"nodes": [], "edges": []},
            created_at=datetime.now(UTC),
        )
        session.get = AsyncMock(return_value=version)

        kestra = AsyncMock()
        kestra.trigger_build = AsyncMock(side_effect=RuntimeError("kestra 500"))

        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        svc = PipelineBuilderService(
            metadata=_make_mock_metadata(session),
            schema_engine=SchemaInferenceEngine(),
            kestra_engine=kestra,
        )
        result = await svc.trigger_build("cust_etl")
        assert result.status == "FAILED"
