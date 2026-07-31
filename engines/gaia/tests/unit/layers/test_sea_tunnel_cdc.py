"""Unit tests for SeaTunnelEngine — pipeline lifecycle management.

2026-07-08 (去 SeaTunnel 化): ACTION_CDC (PG action_execution_logs → Iceberg)
+ PG_TO_KAFKA + KAFKA_TO_DORIS + DUAL_SINK + DORIS_INDEX_TABLE_DDL have been
removed. object_state sync now uses outbox INDEX/ARCHIVE effect. This file
retains only the generic pipeline lifecycle tests (start/stop/status) that are
independent of the removed templates. External-data pipelines (file_sync /
kafka_ingestion / external_cdc / index backfill) have their own test files.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import OntologyError
from ontology.core.schemas.pipeline import PipelineStatus
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine


@pytest.fixture
def mock_client() -> AsyncMock:
    """Mock httpx AsyncClient for SeaTunnel REST API."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    client.get = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    return client


@pytest.fixture
def engine(mock_client) -> SeaTunnelEngine:
    return SeaTunnelEngine(client=mock_client)


class TestPipelineLifecycle:
    """Pipeline start/stop/status — independent of specific templates."""

    @pytest.mark.asyncio
    async def test_start_pipeline_no_op_when_running(self, engine):
        """start() is a no-op when the pipeline is already RUNNING."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="already_running", state="RUNNING")
        )

        result = await engine.start("already_running")

        assert result.state == "RUNNING"
        # No submit should happen — already running.
        engine.client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_without_config_when_not_running_raises(self, engine):
        """start() without config when pipeline is not running raises OntologyError."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="not_running", state="FINISHED")
        )

        with pytest.raises(OntologyError, match="no config provided"):
            await engine.start("not_running")

    @pytest.mark.asyncio
    async def test_start_re_submits_with_config(self, engine):
        """start() with config re-submits when pipeline is not running."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="done", state="FINISHED")
        )
        engine._submit_job = AsyncMock()

        await engine.start("done", config="env { }")

        engine._submit_job.assert_awaited_once_with("done", "env { }")

    @pytest.mark.asyncio
    async def test_stop_pipeline(self, engine, mock_client):
        """stop() calls the SeaTunnel stop-job endpoint when job is running."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="p1", state="RUNNING", job_id="job-123")
        )

        await engine.stop("p1")

        mock_client.post.assert_awaited()

    @pytest.mark.asyncio
    async def test_stop_error_propagates(self, engine, mock_client):
        """stop() propagates submit errors from the stop-job endpoint."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="p1", state="RUNNING", job_id="job-123")
        )
        mock_client.post = AsyncMock(side_effect=RuntimeError("SeaTunnel down"))

        with pytest.raises(OntologyError, match="Failed to stop pipeline"):
            await engine.stop("p1")

    @pytest.mark.asyncio
    async def test_stop_no_op_when_terminal(self, engine, mock_client):
        """stop() is a no-op when the job is already terminal (not running)."""
        engine.get_job_status = AsyncMock(
            return_value=PipelineStatus(name="p1", state="FINISHED")
        )

        await engine.stop("p1")

        # No POST (stop-job) should happen — job already terminal.
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_status_running(self, engine, mock_client):
        """get_job_status returns the pipeline's runtime state."""
        running_resp = MagicMock()
        running_resp.raise_for_status = MagicMock()
        running_resp.json = MagicMock(return_value=[{"jobName": "p1", "jobStatus": "RUNNING"}])
        finished_resp = MagicMock()
        finished_resp.raise_for_status = MagicMock()
        finished_resp.json = MagicMock(return_value=[])
        mock_client.get = AsyncMock(side_effect=[running_resp, finished_resp])

        status = await engine.get_job_status("p1")

        assert status.state == "RUNNING"

    @pytest.mark.asyncio
    async def test_get_status_unknown_when_not_found(self, engine, mock_client):
        """get_job_status returns UNKNOWN when the job is not in running/finished lists."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[])
        mock_client.get = AsyncMock(return_value=resp)

        status = await engine.get_job_status("nonexistent")

        assert status.state == "UNKNOWN"
