"""Unit tests for SeaTunnelEngine.

httpx calls to SeaTunnel REST API are mocked. Tests validate:
1. SYNC pipeline creation generates correct config and submits via REST
2. INDEX pipeline creation
3. Pipeline lifecycle (start, stop, get_status)
4. Jinja2 template rendering for config files

v5.2: MAIN→SYNC, INDEX_SYNC→INDEX enum rename. SYNC pipeline impl is
``_build_sync_pipeline`` (routed by ``create_sync_pipeline``); the INDEX
pipeline is ``create_index_pipeline`` (ontology-namespaced).
"""

import re
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient, Response

from ontology.core.exceptions import OntologyError
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock httpx.AsyncClient — client methods are async, response methods are sync."""
    client = MagicMock(spec=AsyncClient)
    return client


@pytest.fixture
def mock_response() -> MagicMock:
    """Mock httpx.Response — all methods are sync."""
    resp = MagicMock(spec=Response)
    resp.status_code = 200
    resp.json.return_value = {}
    return resp


@pytest.fixture
def engine(mock_client) -> SeaTunnelEngine:
    """Create SeaTunnelEngine with mocked client."""
    return SeaTunnelEngine(client=mock_client)


class TestSyncPipeline:
    """SYNC data ingestion pipeline (source → Iceberg)."""

    @pytest.mark.asyncio
    async def test_create_sync_pipeline(self, engine, mock_client, mock_response):
        """Create a SYNC pipeline with source → Iceberg sink."""
        mock_response.json.return_value = {"jobId": "job_123"}
        mock_client.post.return_value = mock_response

        result = await engine._build_sync_pipeline(
            source={
                "type": "mysql-cdc",
                "config": {
                    "host": "localhost",
                    "port": 3306,
                    "table": "orders",
                },
            },
            target_dataset="ontology.orders",
        )

        assert result.name == "sync_orders"
        assert result.type == "SYNC"
        mock_client.post.assert_called_once()
        call_url = str(mock_client.post.call_args[0][0])
        assert "submit" in call_url.lower()

    @pytest.mark.asyncio
    async def test_create_sync_pipeline_with_transforms(self, engine, mock_client, mock_response):
        """Create a SYNC pipeline with transform steps."""
        mock_response.json.return_value = {"jobId": "job_456"}
        mock_client.post.return_value = mock_response

        result = await engine._build_sync_pipeline(
            source={
                "type": "kafka",
                "config": {"topic": "orders", "bootstrap.servers": "kafka:9092"},
            },
            target_dataset="ontology.orders",
            transforms=[
                {"type": "field-mapping", "config": {"map": {"id": "order_id"}}},
                {"type": "filter", "config": {"condition": "status != 'deleted'"}},
            ],
        )

        assert result.name == "sync_orders"
        mock_client.post.assert_called_once()


class TestSyncPipelineConfigRendering:
    """Verify the SYNC pipeline config pulls S3/Iceberg credentials from settings,
    not from hardcoded literals (regression test for hardcoded minioadmin)."""

    @pytest.mark.asyncio
    async def test_iceberg_sink_uses_settings_credentials(self):
        """Rendered config must carry the S3 credentials from settings."""
        from ontology.layers.pipeline.sea_tunnel_engine import _render_sync_config_v2

        cfg = _render_sync_config_v2(
            source={"driver": "d", "url": "u", "user": "u", "password": "p", "table": "t"},
            target_table="orders",
            transforms=[],
        )
        # MAIN 模板为 HOCON 语法，用正则提取 sink 段的配置值。
        from ontology.config.settings import settings

        def _val(key: str) -> str:
            m = re.search(rf'"?{re.escape(key)}"?\s*=\s*"([^"]*)"', cfg)
            assert m, f"{key} not found in rendered config"
            return m.group(1)

        # Credentials come from settings, not hardcoded "minioadmin".
        assert _val("s3.access-key-id") == settings.s3_access_key_id
        assert _val("s3.secret-access-key") == settings.s3_secret_access_key
        assert _val("uri") == settings.seatunnel_iceberg_rest_uri
        assert _val("s3.endpoint") == settings.seatunnel_s3_endpoint
        assert _val("s3.region") == settings.s3_region
        # bool rendered as lowercase string for HOCON consumption
        assert _val("s3.path-style-access") == str(settings.s3_path_style_access).lower()

    @pytest.mark.asyncio
    async def test_credentials_follow_settings_override(self, monkeypatch):
        """Changing settings must change the rendered credentials — this is the
        whole point of the fix: production credential rotation must not require
        a code change."""
        from ontology.config.settings import settings
        from ontology.layers.pipeline.sea_tunnel_engine import _render_sync_config_v2

        monkeypatch.setattr(settings, "s3_access_key_id", "prod-ak")
        monkeypatch.setattr(settings, "s3_secret_access_key", "prod-sk")
        monkeypatch.setattr(settings, "seatunnel_iceberg_rest_uri", "http://prod-iceberg:9001/iceberg")

        cfg = _render_sync_config_v2(
            source={"driver": "d", "url": "u", "user": "u", "password": "p", "table": "t"},
            target_table="orders",
            transforms=[],
        )

        # MAIN 模板为 HOCON 语法，用正则提取。
        def _val(key: str) -> str:
            m = re.search(rf'"?{re.escape(key)}"?\s*=\s*"([^"]*)"', cfg)
            assert m, f"{key} not found in rendered config"
            return m.group(1)

        assert _val("s3.access-key-id") == "prod-ak"
        assert _val("s3.secret-access-key") == "prod-sk"
        assert _val("uri") == "http://prod-iceberg:9001/iceberg"
        # Hardcoded defaults must NOT leak back in.
        assert _val("s3.access-key-id") != "minioadmin"


class TestSubmitJobFailureDetection:
    """Regression tests for the silent-submit-failure bug.

    SeaTunnel returns HTTP 200 with {"status":"fail",...} when a job
    config is rejected. The old _submit_job only checked HTTP status,
    so rejected jobs were treated as successful — SyncTasks were marked
    RUNNING with no real job behind them. These tests pin the fix.
    """

    @pytest.mark.asyncio
    async def test_submit_raises_on_fail_body(self, mock_client, mock_response):
        """A 200 response with status=fail must raise OntologyError."""
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "fail",
            "message": "Unable to create a source for identifier 'Jdbc'.",
        }
        mock_client.post.return_value = mock_response
        engine = SeaTunnelEngine(client=mock_client)

        with pytest.raises(OntologyError, match="SeaTunnel rejected job"):
            await engine._build_sync_pipeline(
                source={
                    "driver": "org.postgresql.Driver",
                    "url": "jdbc:postgresql://x",
                    "user": "u",
                    "password": "p",
                    "table": "t",
                },
                target_dataset="ontology.t",
            )

    @pytest.mark.asyncio
    async def test_submit_returns_job_id_on_success(self, mock_client, mock_response):
        """A successful submit returns a PipelineDef carrying the jobId."""
        mock_response.status_code = 200
        mock_response.json.return_value = {"jobId": "999", "jobName": "sync_t"}
        mock_client.post.return_value = mock_response
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine._build_sync_pipeline(
            source={
                "driver": "org.postgresql.Driver",
                "url": "jdbc:postgresql://x",
                "user": "u",
                "password": "p",
                "table": "t",
            },
            target_dataset="ontology.t",
        )

        assert result.job_id == "999"


class TestPipelineLifecycle:
    """Pipeline start/stop/status.

    get_status now resolves via the running-jobs + finished-jobs lists
    (SeaTunnel keys job-info by jobId, not jobName, so the old
    /job-info?jobName=... call never worked). These tests exercise that
    lookup path.
    """

    @pytest.mark.asyncio
    async def test_get_status_running(self, mock_client):
        """A job present in running-jobs is reported as RUNNING."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [
            {
                "jobId": "111",
                "jobName": "pipeline_orders",
                "jobStatus": "RUNNING",
                "metrics": {"TableSourceReceivedCount": {"s": "50000"}},
            }
        ]
        mock_client.get.return_value = running_resp
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.get_status("pipeline_orders")

        assert status.name == "pipeline_orders"
        assert status.state == "RUNNING"
        assert status.job_id == "111"
        assert status.records_processed == 50000

    @pytest.mark.asyncio
    async def test_get_status_finished(self, mock_client):
        """A job only in finished-jobs is reported as FINISHED with error_msg."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = [
            {
                "jobId": "222",
                "jobName": "pipeline_orders",
                "jobStatus": "FINISHED",
                "errorMsg": None,
                "finishTime": "2026-06-18 03:05:20",
            }
        ]
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.get_status("pipeline_orders")

        assert status.state == "FINISHED"
        assert status.job_id == "222"
        assert status.error_msg is None

    @pytest.mark.asyncio
    async def test_get_status_failed_carries_error_msg(self, mock_client):
        """A FAILED finished job surfaces its errorMsg for the UI."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = [
            {
                "jobId": "333",
                "jobName": "pipeline_orders",
                "jobStatus": "FAILED",
                "errorMsg": "Protocol error. Session setup failed.",
            }
        ]
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.get_status("pipeline_orders")

        assert status.state == "FAILED"
        assert "Protocol error" in (status.error_msg or "")

    @pytest.mark.asyncio
    async def test_get_status_unknown_when_absent(self, mock_client):
        """A job absent from both lists is UNKNOWN (not an exception)."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = []
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.get_status("pipeline_orders")

        assert status.state == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_get_status_raises_on_api_error(self, mock_client):
        """Transport errors still raise OntologyError."""
        mock_client.get.side_effect = Exception("SeaTunnel API error")
        engine = SeaTunnelEngine(client=mock_client)

        with pytest.raises(OntologyError, match="SeaTunnel API error"):
            await engine.get_status("pipeline_orders")

    @pytest.mark.asyncio
    async def test_start_no_op_when_already_running(self, mock_client):
        """start() short-circuits when the job is already RUNNING."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [{"jobId": "111", "jobName": "p", "jobStatus": "RUNNING"}]
        mock_client.get.return_value = running_resp
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.start("p", config="dummy")

        assert status.state == "RUNNING"
        mock_client.post.assert_not_awaited()  # no re-submit

    @pytest.mark.asyncio
    async def test_start_re_submits_with_config(self, mock_client, mock_response):
        """start() re-submits the job when it is not running."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []  # not running
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = []  # not finished either
        after_submit_resp = MagicMock(spec=Response)
        after_submit_resp.status_code = 200
        after_submit_resp.json.return_value = [{"jobId": "999", "jobName": "p", "jobStatus": "RUNNING"}]
        mock_client.get.side_effect = [
            running_resp,
            finished_resp,
            after_submit_resp,
            MagicMock(spec=Response, json=MagicMock(return_value=[])),
        ]
        mock_response.json.return_value = {"jobId": "999", "jobName": "p"}
        mock_client.post.return_value = mock_response
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.start("p", config="env{}")

        assert status.state == "RUNNING"
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_without_config_when_not_running_raises(self, mock_client):
        """start() without config cannot restart a missing job → OntologyError."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = []
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        with pytest.raises(OntologyError, match="no config provided"):
            await engine.start("p")

    @pytest.mark.asyncio
    async def test_stop_resolves_jobid_then_posts_to_stop_job(self, mock_client):
        """stop() resolves name→jobId via running-jobs, then POSTs to stop-job.

        Regression: the old implementation POSTed to ``cancel-job`` with a
        ``{jobName}`` body, which SeaTunnel 2.3.13 rejects with
        ``Missing map name`` for *any* input — so stop() never actually
        stopped anything. The fix uses ``stop-job`` with a ``{jobId}`` body.
        """
        # 1st get: running-jobs shows the job RUNNING with a jobId.
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [{"jobId": "999", "jobName": "p", "jobStatus": "RUNNING", "errorMsg": None}]
        # stop-job POST response.
        stop_resp = MagicMock(spec=Response)
        stop_resp.status_code = 200
        stop_resp.text = '{"jobId":"999"}'
        # 2nd get (post-stop status): running empty, finished has CANCELED.
        post_running_resp = MagicMock(spec=Response)
        post_running_resp.status_code = 200
        post_running_resp.json.return_value = []
        post_finished_resp = MagicMock(spec=Response)
        post_finished_resp.status_code = 200
        post_finished_resp.json.return_value = [
            {"jobId": "999", "jobName": "p", "jobStatus": "CANCELED", "errorMsg": None}
        ]
        mock_client.get.side_effect = [
            running_resp,  # initial get_job_status (running)
            post_running_resp,  # post-stop get_job_status (running)
            post_finished_resp,  # post-stop get_job_status (finished)
        ]
        mock_client.post.return_value = stop_resp
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.stop("p")

        assert status.state == "CANCELED"
        # The POST must target stop-job (not cancel-job) with a jobId body.
        mock_client.post.assert_awaited_once()
        call = mock_client.post.await_args
        assert call.args[0] == "/hazelcast/rest/maps/stop-job"
        assert call.kwargs["json"] == {"jobId": "999", "isStopWithSavePoint": False}

    @pytest.mark.asyncio
    async def test_stop_already_terminal_skips_post(self, mock_client):
        """stop() on an already-terminal job is a no-op (no POST)."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = [{"jobId": "1", "jobName": "p", "jobStatus": "FINISHED", "errorMsg": None}]
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.stop("p")

        assert status.state == "FINISHED"
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_unknown_job_is_noop(self, mock_client):
        """stop() on a job not in running nor finished returns UNKNOWN, no POST."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = []
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.stop("never_existed")

        assert status.state == "UNKNOWN"
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_treats_non_200_as_already_stopped(self, mock_client):
        """A non-200 from stop-job is logged but not fatal (idempotent stop)."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [{"jobId": "555", "jobName": "p", "jobStatus": "RUNNING", "errorMsg": None}]
        stop_resp = MagicMock(spec=Response)
        stop_resp.status_code = 400
        stop_resp.text = '{"status":"fail","message":"already stopped"}'
        post_running_resp = MagicMock(spec=Response)
        post_running_resp.status_code = 200
        post_running_resp.json.return_value = []
        post_finished_resp = MagicMock(spec=Response)
        post_finished_resp.status_code = 200
        post_finished_resp.json.return_value = [
            {"jobId": "555", "jobName": "p", "jobStatus": "CANCELED", "errorMsg": None}
        ]
        mock_client.get.side_effect = [running_resp, post_running_resp, post_finished_resp]
        mock_client.post.return_value = stop_resp
        engine = SeaTunnelEngine(client=mock_client)

        status = await engine.stop("p")

        # non-200 is tolerated; final status still resolved.
        assert status.state == "CANCELED"


class TestGetJobsStatusBatch:
    """Batch status lookup — replaces N+1 per-task get_job_status calls.

    Each batch makes exactly 2 SeaTunnel calls (running-jobs + finished-jobs)
    regardless of how many names are queried.
    """

    @pytest.mark.asyncio
    async def test_empty_names_returns_empty_dict_without_api_calls(self, mock_client):
        """No names → no SeaTunnel calls."""
        engine = SeaTunnelEngine(client=mock_client)
        result = await engine.get_jobs_status_batch(set())
        assert result == {}
        mock_client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_matches_running_and_finished_in_two_calls(self, mock_client):
        """3 names split across running + finished → 2 calls, all matched."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [
            {"jobId": "1", "jobName": "p_running", "jobStatus": "RUNNING",
             "metrics": {"TableSourceReceivedCount": {"s": "100"}}},
        ]
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = [
            {"jobId": "2", "jobName": "p_done", "jobStatus": "FINISHED", "errorMsg": None},
            {"jobId": "3", "jobName": "p_failed", "jobStatus": "FAILED", "errorMsg": "boom"},
        ]
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine.get_jobs_status_batch({"p_running", "p_done", "p_failed"})

        assert mock_client.get.await_count == 2  # exactly 2 calls, not 2×3
        assert result["p_running"].state == "RUNNING"
        assert result["p_running"].records_processed == 100
        assert result["p_done"].state == "FINISHED"
        assert result["p_failed"].state == "FAILED"
        assert result["p_failed"].error_msg == "boom"

    @pytest.mark.asyncio
    async def test_batch_unknown_for_absent_names(self, mock_client):
        """Names absent from both lists map to UNKNOWN."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = []
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine.get_jobs_status_batch({"ghost"})

        assert result["ghost"].state == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_batch_running_error_degrades_all_to_unknown(self, mock_client):
        """running-jobs unreachable → every name gets UNKNOWN, no exception."""
        mock_client.get.side_effect = Exception("SeaTunnel down")
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine.get_jobs_status_batch({"a", "b"})

        assert result["a"].state == "UNKNOWN"
        assert result["b"].state == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_batch_finished_error_keeps_running_matches(self, mock_client):
        """finished-jobs unreachable → running matches kept, rest get UNKNOWN."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = [
            {"jobId": "1", "jobName": "p_running", "jobStatus": "RUNNING"},
        ]
        mock_client.get.side_effect = [running_resp, Exception("finished-jobs down")]
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine.get_jobs_status_batch({"p_running", "p_lost"})

        assert result["p_running"].state == "RUNNING"
        assert result["p_lost"].state == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_batch_picks_latest_finish_for_re_runs(self, mock_client):
        """Multiple finished entries for the same name → latest finishTime wins."""
        running_resp = MagicMock(spec=Response)
        running_resp.status_code = 200
        running_resp.json.return_value = []
        finished_resp = MagicMock(spec=Response)
        finished_resp.status_code = 200
        finished_resp.json.return_value = [
            {"jobId": "1", "jobName": "p", "jobStatus": "FAILED", "errorMsg": "old",
             "finishTime": "2026-01-01 00:00:00"},
            {"jobId": "2", "jobName": "p", "jobStatus": "FINISHED", "errorMsg": None,
             "finishTime": "2026-06-01 00:00:00"},
        ]
        mock_client.get.side_effect = [running_resp, finished_resp]
        engine = SeaTunnelEngine(client=mock_client)

        result = await engine.get_jobs_status_batch({"p"})

        assert result["p"].state == "FINISHED"  # newer finishTime
        assert result["p"].job_id == "2"
