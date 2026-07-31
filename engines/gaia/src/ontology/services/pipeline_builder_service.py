"""PipelineBuilderService — business orchestration for Pipeline Builder.

Design (ADR-018):
  - Pipeline CRUD (with version management, Airflow-3-style version table)
  - Schema validation (delegates to SchemaInferenceEngine)
  - Deploy (IR → Kestra Flow translation, D2)
  - Build (trigger Kestra execution, D6)
  - Execution monitoring (poll Kestra state → map to PG)
  - Rollback (logical version switch + data snapshot switch)

This Service is the single entry point for all pipeline-related operations.
It does NOT directly call Kestra/DuckDB/SeaTunnel — that's the KestraEngine's
job (created separately per Phase 1 step 3).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func as sa_func
from sqlalchemy import select

from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.models.datasource import DatasetGovernanceModel
from ontology.core.models.defaults import new_uuid, utcnow
from ontology.core.models.pipeline import (
    PipelineExecutionModel,
    PipelineModel,
    PipelineNodeRunModel,
    PipelineScheduleModel,
    PipelineStateHistoryModel,
    PipelineVersionModel,
)
from ontology.core.schemas.pipeline_builder import (
    BuildDetailResponse,
    BuildRequest,
    BuildResponse,
    DeployRequest,
    DeployResponse,
    IRNode,
    PipelineCreate,
    PipelineIR,
    PipelineResponse,
    PipelineUpdate,
    PipelineVersionResponse,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    Schema,
    SchemaField,
    ValidationResponse,
)
from ontology.layers.pipeline.kestra_engine import KestraEngine, KestraUnavailableError
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.schema_inference_engine import SchemaInferenceEngine

try:  # Optional dependencies — injected by container in production, None in tests.
    from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
    from ontology.layers.dataset.iceberg_store import IcebergStore
except ImportError:  # pragma: no cover — defensive
    GravitinoRegistry = None  # type: ignore[assignment,misc]
    IcebergStore = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from ontology.core.schemas.dataset import DatasetSchema

_log = logging.getLogger(__name__)


class PipelineBuilderService(MetadataOwnerMixin):
    """Pipeline Builder business orchestration.

    Inherits ``MetadataOwnerMixin`` for ``aclose()`` + ``transaction()``
    (project convention: multi-step writes wrapped in
    ``async with self.transaction():`` so IntegrityError → ConflictError
    and rollback is automatic).

    Operations are grouped by entity:
      - Pipeline CRUD
      - Versions
      - Validation
      - Deploy / Build / Execution monitoring
      - Schedules
    """

    def __init__(
        self,
        metadata: Any,  # PostgresMetaStore (typed as Any to avoid import cycle)
        schema_engine: SchemaInferenceEngine | None = None,
        kestra_engine: KestraEngine | None = None,
        dataset: IcebergStore | None = None,
        catalog: GravitinoRegistry | None = None,
    ) -> None:
        # PostgresMetaStore owns the AsyncSession; MetadataOwnerMixin
        # uses self._metadata for aclose() + transaction().
        self._metadata = metadata
        self._session = metadata._session  # noqa: SLF001 — pipeline tables are
        # not yet exposed as PostgresMetaStore methods; direct session access
        # is the pragmatic choice until a dedicated layer is warranted.
        self._schema_engine = schema_engine or SchemaInferenceEngine()
        # KestraEngine is stateless (REST client + translator) — safe to share.
        # Injected by container; tests can pass a mock.
        self._kestra_engine = kestra_engine
        # Dataset (IcebergStore) + Catalog (GravitinoRegistry) are used to
        # resolve Source node output schemas during validate_pipeline, so
        # downstream column dropdowns (Join/etc.) populate during editing.
        # Optional: when None, Source schemas stay empty (tests / offline).
        self._dataset = dataset
        self._catalog = catalog

    # ═══════════════════════════════════════════════════════════════
    # Pipeline CRUD
    # ═══════════════════════════════════════════════════════════════

    async def create_pipeline(self, data: PipelineCreate) -> PipelineResponse:
        """Create a pipeline with an initial version (graph saved).

        Single transaction: pipeline row + initial version + link
        ``current_version_id``. IntegrityError (duplicate api_name) →
        ConflictError via ``transaction()``.
        """
        # Check api_name uniqueness (fast-fail before transaction)
        existing = await self._session.execute(select(PipelineModel).where(PipelineModel.api_name == data.api_name))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Pipeline '{data.api_name}' already exists")

        now = utcnow()
        pipeline_id = new_uuid()

        async with self.transaction():
            # Create the pipeline record (no current_version_id yet)
            pipeline = PipelineModel(
                id=pipeline_id,
                api_name=data.api_name,
                display_name=data.display_name,
                description=data.description,
                status="DRAFT",
                write_mode=data.write_mode,
                sink_dataset_api_name=data.sink_dataset_api_name,
                created_at=now,
                updated_at=now,
            )
            self._session.add(pipeline)
            await self._session.flush()

            # Create the initial version
            version = PipelineVersionModel(
                id=new_uuid(),
                pipeline_id=pipeline_id,
                version_number=1,
                graph=data.graph.model_dump(mode="json") if data.graph else {"nodes": [], "edges": []},
                inferred_schema=None,
                change_summary=data.change_summary if data.change_summary else "Initial version",
                created_at=now,
            )
            self._session.add(version)
            await self._session.flush()

            # Link current_version_id
            pipeline.current_version_id = version.id

        return self._pipeline_to_response(pipeline, version_number=1)

    async def list_pipelines(
        self,
        project_id: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[PipelineResponse], int]:
        """List pipelines with optional filters."""
        query = select(PipelineModel).where(PipelineModel.deleted_at.is_(None))

        if project_id:
            query = query.where(PipelineModel.project_id == project_id)
        if status:
            query = query.where(PipelineModel.status == status)

        # Count total
        count_query = select(sa_func.count()).select_from(query.subquery())
        total: int = (await self._session.execute(count_query)).scalar() or 0

        # Fetch page
        query = query.order_by(PipelineModel.updated_at.desc()).offset(offset).limit(limit)
        rows = await self._session.execute(query)
        pipelines = rows.scalars().all()

        # Batch-load version numbers (avoid N+1: one query for all pipelines)
        version_ids = [p.current_version_id for p in pipelines if p.current_version_id]
        version_map: dict[str, int] = {}
        if version_ids:
            vn_rows = await self._session.execute(
                select(PipelineVersionModel.id, PipelineVersionModel.version_number).where(
                    PipelineVersionModel.id.in_(version_ids)
                )
            )
            version_map = {vid: vnum for vid, vnum in vn_rows.all()}

        responses = [
            self._pipeline_to_response(p, version_number=version_map.get(p.current_version_id)) for p in pipelines
        ]
        return responses, total

    async def get_pipeline(self, api_name: str, include_deleted: bool = False) -> PipelineResponse:
        """Get pipeline by api_name."""
        pipeline = await self._get_pipeline_or_raise(api_name, include_deleted)
        vn = await self._get_version_number(pipeline.current_version_id)
        return self._pipeline_to_response(pipeline, version_number=vn)

    async def update_pipeline(self, api_name: str, data: PipelineUpdate) -> PipelineResponse:
        """Update pipeline — creates a new version when graph changes.

        Returns the updated pipeline with the new current version.
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        now = utcnow()

        # Update scalar fields
        changed = False
        if data.display_name is not None:
            pipeline.display_name = data.display_name
            changed = True
        if data.description is not None:
            pipeline.description = data.description
            changed = True
        if data.write_mode is not None:
            pipeline.write_mode = data.write_mode
            changed = True
        if data.sink_dataset_api_name is not None:
            pipeline.sink_dataset_api_name = data.sink_dataset_api_name
            changed = True

        # If graph is supplied, create a new version
        if data.graph is not None:
            # Get current max version number
            max_v = await self._session.execute(
                select(sa_func.max(PipelineVersionModel.version_number)).where(
                    PipelineVersionModel.pipeline_id == pipeline.id
                )
            )
            current_max = max_v.scalar() or 0

            version = PipelineVersionModel(
                id=new_uuid(),
                pipeline_id=pipeline.id,
                version_number=current_max + 1,
                graph=data.graph.model_dump(mode="json"),
                inferred_schema=None,  # Will be filled on validation
                change_summary=data.change_summary or "",
                created_at=now,
            )
            self._session.add(version)
            await self._session.flush()
            pipeline.current_version_id = version.id
            changed = True

        if changed:
            pipeline.updated_at = now
            async with self.transaction():
                # All updates (scalar fields + new version) committed atomically
                pass  # updates already applied to session; transaction commits them

        vn = await self._get_version_number(pipeline.current_version_id)
        return self._pipeline_to_response(pipeline, version_number=vn)

    async def delete_pipeline(self, api_name: str) -> None:
        """Soft-delete pipeline."""
        pipeline = await self._get_pipeline_or_raise(api_name)
        pipeline.deleted_at = utcnow()
        async with self.transaction():
            pass  # soft-delete committed atomically

    # ═══════════════════════════════════════════════════════════════
    # Versions
    # ═══════════════════════════════════════════════════════════════

    async def list_versions(self, api_name: str) -> list[PipelineVersionResponse]:
        """List all versions of a pipeline."""
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineVersionModel)
            .where(PipelineVersionModel.pipeline_id == pipeline.id)
            .order_by(PipelineVersionModel.version_number.desc())
        )
        versions = result.scalars().all()
        return [self._version_to_response(v) for v in versions]

    async def get_version(self, api_name: str, version_number: int) -> PipelineVersionResponse:
        """Get a specific version."""
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineVersionModel).where(
                PipelineVersionModel.pipeline_id == pipeline.id,
                PipelineVersionModel.version_number == version_number,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundError("PipelineVersion", str(version_number))
        return self._version_to_response(version)

    async def rollback_version(self, api_name: str, version_number: int) -> PipelineResponse:
        """Rollback to a specific version (switch current_version_id + redeploy).

        Does NOT trigger a data build — user decides when to build.
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineVersionModel).where(
                PipelineVersionModel.pipeline_id == pipeline.id,
                PipelineVersionModel.version_number == version_number,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundError("PipelineVersion", str(version_number))

        async with self.transaction():
            pipeline.current_version_id = version.id
            pipeline.updated_at = utcnow()

        return await self.get_pipeline(api_name)

    # ═══════════════════════════════════════════════════════════════
    # Validation
    # ═══════════════════════════════════════════════════════════════

    async def validate_pipeline(
        self,
        graph: PipelineIR | dict[str, Any] | None = None,
        api_name: str | None = None,
        sink_dataset_api_name: str | None = None,
    ) -> ValidationResponse:
        """Validate a pipeline IR — synchronous schema inference.

        If graph is None and api_name is given, loads the current version's graph.
        """
        if graph is None and api_name:
            pipeline = await self._get_pipeline_or_raise(api_name)
            if pipeline.current_version_id:
                version = await self._session.get(PipelineVersionModel, pipeline.current_version_id)
                if version and version.graph:
                    graph = PipelineIR(**version.graph)

        if graph is None:
            return ValidationResponse(valid=False, contracts=[])

        # Convert IR to nodes + edges for the inference engine
        nodes: list[IRNode] = []
        edges: list[dict[str, str]] = []
        if isinstance(graph, dict):
            graph_obj = PipelineIR(**graph)
        else:
            graph_obj = graph

        nodes = graph_obj.nodes
        for edge in graph_obj.edges:
            edges.append({"source_id": edge.source_id, "target_id": edge.target_id})

        # Pre-fetch Source node output schemas from Iceberg/Gravitino so
        # downstream nodes (Join, Filter, etc.) populate column dropdowns.
        # Best-effort: failures are logged and yield an empty schema, never
        # blocking validation (editing must stay responsive when a dataset's
        # physical table doesn't exist yet — e.g. sync never ran).
        source_schemas = await self._fetch_source_schemas(nodes)

        # edges is list[dict[str,str]] which is compatible with list[IREdge | dict[str,str]]
        return self._schema_engine.validate_pipeline(
            nodes=nodes,
            edges=edges,  # type: ignore[arg-type]
            sink_dataset_api_name=sink_dataset_api_name,
            source_schemas=source_schemas,
        )

    async def _fetch_source_schemas(self, nodes: list[IRNode]) -> dict[str, Schema]:
        """Resolve output schemas for Source nodes from dataset metadata.

        Reads each Source node's ``config.extra.dataset`` (dataset api_name)
        and fetches its physical schema (MANAGED → Iceberg, VIRTUAL →
        Gravitino). Returns a {node_id → Schema} map. Missing dataset /
        unavailable store / malformed config → empty schema (best-effort).
        """
        result: dict[str, Schema] = {}
        if self._dataset is None:
            return result
        for node in nodes:
            if (node.type != "Source" and node.operator_type != "Source") or node.id in result:
                continue
            dataset_api_name = node.config.extra.get("dataset") if node.config.extra is not None else None
            if not dataset_api_name:
                continue
            try:
                schema = await self._resolve_dataset_schema(dataset_api_name)
                result[node.id] = schema
            except Exception as exc:  # noqa: BLE001 — best-effort, never block editing
                _log.warning(
                    "Source node %s: could not resolve schema for dataset '%s': %s",
                    node.id,
                    dataset_api_name,
                    exc,
                )
        return result

    async def _resolve_dataset_schema(self, api_name: str) -> Schema:
        """Fetch a dataset's column schema and convert to pipeline Schema.

        Dispatches by dataset kind (MANAGED → IcebergStore, VIRTUAL →
        Gravitino), mirroring DataSourceService.get_dataset_schema but
        using this service's own session + injected stores to keep the
        per-request session boundary intact.
        """
        from ontology.core.schemas.dataset import DatasetSchema

        ds = await self._metadata.get_dataset(api_name)
        dataset_schema: DatasetSchema
        if ds.kind == "MANAGED":
            dataset_schema = await self._dataset.get_schema(api_name)  # type: ignore[union-attr]
        elif ds.kind == "VIRTUAL" and self._catalog is not None:
            dataset_schema = await self._fetch_virtual_schema(ds.storage_location)
        else:
            dataset_schema = DatasetSchema(columns=[])
        # Convert DatasetSchema (columns: ColumnDef) → pipeline Schema (fields: SchemaField)
        return Schema(
            fields=[SchemaField(name=c.name, data_type=c.type, nullable=c.nullable) for c in dataset_schema.columns]
        )

    async def _fetch_virtual_schema(self, storage_location: str) -> DatasetSchema:
        """Fetch columns for a VIRTUAL dataset via Gravitino.

        storage_location = "catalog.schema.table" (three parts). Malformed
        → empty schema. Uses GravitinoRegistry.get_table_columns which
        returns a list of column dicts ({name, type, nullable}).
        """
        from ontology.core.schemas.dataset import ColumnDef, DatasetSchema

        parts = storage_location.split(".")
        if len(parts) != 3:
            _log.warning(
                "Virtual table storage_location malformed: '%s'; returning empty schema",
                storage_location,
            )
            return DatasetSchema(columns=[])
        catalog_name, schema_name, table_name = parts
        try:
            grav_cols = await self._catalog.get_table_columns(  # type: ignore[union-attr]
                catalog_name, schema_name, table_name
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Gravitino get_table_columns failed for '%s': %s",
                storage_location,
                exc,
            )
            return DatasetSchema(columns=[])
        columns = [
            ColumnDef(
                name=str(col.get("name", "")),
                type=GravitinoRegistry._format_gravitino_column_type(col.get("type", "unknown"))
                if GravitinoRegistry is not None
                else str(col.get("type", "unknown")),
                nullable=bool(col.get("nullable", True)),
            )
            for col in (grav_cols or [])
        ]
        return DatasetSchema(columns=columns)

    # ═══════════════════════════════════════════════════════════════
    # Deploy & Build
    # ═══════════════════════════════════════════════════════════════

    async def deploy_pipeline(self, api_name: str, request: DeployRequest | None = None) -> DeployResponse:
        """Deploy a pipeline: DRAFT → PUBLISHED + translate IR → Kestra Flow.

        Calls KestraEngine.deploy() to upsert the Kestra Flow. On Kestra
        unavailability, the pipeline status is still updated but a warning
        is logged (deploy is idempotent — next deploy retries the Flow upsert).
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        version_id = request.version_id if request and request.version_id else pipeline.current_version_id

        if version_id is None:
            raise ValidationError("No version to deploy — create a version first")

        # Verify version exists
        version = await self._session.get(PipelineVersionModel, version_id)
        if version is None or version.pipeline_id != pipeline.id:
            raise NotFoundError("PipelineVersion", version_id)

        # Translate IR → Kestra Flow and upsert (best-effort: Kestra may be down)
        # External call is OUTSIDE the DB transaction (Kestra failure must not
        # roll back the pipeline status update — deploy is idempotent, next
        # deploy retries the Flow upsert).
        kestra_flow_id: str | None = None
        kestra_namespace: str | None = None
        if self._kestra_engine is not None:
            try:
                ir = PipelineIR(**version.graph) if version.graph else PipelineIR()
                flow_meta = await self._kestra_engine.deploy(
                    ir=ir,
                    pipeline_api_name=api_name,
                    project_api_name="pipelines",
                    namespace="gaia.pipelines",
                )
                kestra_flow_id = flow_meta.get("id", f"pipeline_{api_name}")
                kestra_namespace = flow_meta.get("namespace", "gaia.pipelines")
            except KestraUnavailableError as e:
                _log.warning("Kestra unavailable during deploy of %s: %s", api_name, e)
            except Exception as e:
                _log.warning("Kestra deploy failed for %s: %s", api_name, e)

        async with self.transaction():
            pipeline.status = "PUBLISHED"
            pipeline.current_version_id = version_id
            pipeline.updated_at = utcnow()

        return DeployResponse(
            api_name=api_name,
            status="PUBLISHED",
            deployed_version_id=version_id,
            deployed_version_number=version.version_number,
            kestra_flow_id=kestra_flow_id,
            kestra_namespace=kestra_namespace,
            deployed_at=utcnow(),
        )

    async def deprecate_pipeline(self, api_name: str) -> DeployResponse:
        """Deprecate a pipeline (PUBLISHED → DEPRECATED).

        Also removes the Kestra Flow (best-effort) so no new builds trigger.
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        if pipeline.status != "PUBLISHED":
            raise ValidationError(f"Cannot deprecate pipeline in '{pipeline.status}' state")

        version_id = pipeline.current_version_id
        version = await self._session.get(PipelineVersionModel, version_id) if version_id else None

        # Best-effort: remove Kestra Flow (outside DB transaction)
        if self._kestra_engine is not None:
            try:
                await self._kestra_engine.undeploy(api_name, namespace="gaia.pipelines")
            except Exception as e:
                _log.warning("Kestra undeploy failed for %s: %s", api_name, e)

        async with self.transaction():
            pipeline.status = "DEPRECATED"
            pipeline.updated_at = utcnow()

        return DeployResponse(
            api_name=api_name,
            status="DEPRECATED",
            deployed_version_id=version_id or "",
            deployed_version_number=version.version_number if version else 0,
            kestra_flow_id=None,
            kestra_namespace=None,
            deployed_at=utcnow(),
        )

    async def trigger_build(self, api_name: str, request: BuildRequest | None = None) -> BuildResponse:
        """Trigger a pipeline build (data materialisation).

        Two-phase commit pattern:
        1. DB transaction: insert execution (PENDING) + initial state_history
           (PENDING). Commits immediately so the build is auditable even if
           Kestra is unreachable.
        2. External call: trigger Kestra execution (best-effort, outside tx).
        3. DB transaction: update execution state (RUNNING or FAILED) +
           state_history transition.

        On Kestra failure, the execution row is kept (state=FAILED) with a
        state_history record for audit — never left dangling in PENDING.
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        version_id = request.version_id if request and request.version_id else pipeline.current_version_id

        if version_id is None:
            raise ValidationError("No version to build — create and deploy a version first")

        version = await self._session.get(PipelineVersionModel, version_id)
        if version is None or version.pipeline_id != pipeline.id:
            raise NotFoundError("PipelineVersion", version_id)

        now = utcnow()
        execution = PipelineExecutionModel(
            id=new_uuid(),
            pipeline_id=pipeline.id,
            version_id=version_id,
            trigger_type="MANUAL",
            current_state="PENDING",
            state_started_at=now,
            created_at=now,
        )
        # Phase 1: insert execution + initial state_history atomically
        async with self.transaction():
            self._session.add(execution)
            await self._session.flush()
            self._session.add(
                PipelineStateHistoryModel(
                    id=new_uuid(),
                    execution_id=execution.id,
                    from_state=None,
                    to_state="PENDING",
                    reason="build_triggered",
                    changed_at=now,
                )
            )

        # Phase 2: trigger Kestra execution (best-effort, outside DB tx)
        if self._kestra_engine is not None:
            try:
                kestra_meta = await self._kestra_engine.trigger_build(
                    pipeline_api_name=api_name,
                    namespace="gaia.pipelines",
                )
                # Phase 3a: update to RUNNING + state_history
                async with self.transaction():
                    execution.kestra_execution_id = kestra_meta.get("id")
                    execution.kestra_flow_id = kestra_meta.get("flowId")
                    execution.kestra_namespace = kestra_meta.get("namespace")
                    execution.current_state = "RUNNING"
                    execution.started_at = utcnow()
                    self._session.add(
                        PipelineStateHistoryModel(
                            id=new_uuid(),
                            execution_id=execution.id,
                            from_state="PENDING",
                            to_state="RUNNING",
                            reason="kestra_triggered",
                            changed_at=utcnow(),
                        )
                    )
            except Exception as e:
                _log.warning("Kestra trigger failed for %s: %s", api_name, e)
                # Phase 3b: update to FAILED + state_history (audit trail)
                async with self.transaction():
                    execution.current_state = "FAILED"
                    execution.error_message = f"Kestra trigger failed: {e}"
                    execution.finished_at = utcnow()
                    self._session.add(
                        PipelineStateHistoryModel(
                            id=new_uuid(),
                            execution_id=execution.id,
                            from_state="PENDING",
                            to_state="FAILED",
                            reason=f"kestra_trigger_failed: {e}",
                            changed_at=utcnow(),
                        )
                    )

        return BuildResponse(
            build_id=execution.id,
            pipeline_api_name=api_name,
            version_id=version_id,
            version_number=version.version_number,
            status=execution.current_state,  # type: ignore[arg-type]
            trigger_type="MANUAL",
            created_at=now,
        )

    async def cancel_build(self, api_name: str, build_id: str) -> BuildResponse:
        """Cancel a running/pending build."""
        execution = await self._get_execution_or_raise(api_name, build_id)
        if execution.current_state not in ("PENDING", "RUNNING"):
            raise ValidationError(f"Cannot cancel build in '{execution.current_state}' state")

        now = utcnow()
        prev_state = execution.current_state

        # Best-effort: kill Kestra execution (outside DB tx)
        if self._kestra_engine is not None and execution.kestra_execution_id:
            try:
                await self._kestra_engine.cancel_build(execution.kestra_execution_id)
            except Exception as e:
                _log.warning("Kestra kill failed for %s: %s", execution.kestra_execution_id, e)

        async with self.transaction():
            execution.current_state = "CANCELLED"
            execution.finished_at = now
            if execution.state_started_at:
                execution.duration_ms = int((now - execution.state_started_at).total_seconds() * 1000)

            # Record state transition
            self._session.add(
                PipelineStateHistoryModel(
                    id=new_uuid(),
                    execution_id=execution.id,
                    from_state=prev_state,
                    to_state="CANCELLED",
                    reason="user_cancelled",
                    changed_at=now,
                )
            )

        return await self._execution_to_response(execution, api_name)

    async def data_rollback(self, api_name: str, build_id: str) -> dict[str, Any]:
        """Data rollback: switch the output dataset's current_snapshot_id.

        Uses the build's output_snapshot_id as the rollback target.
        If no output_snapshot_id, returns an error.

        Concurrency: checks ``dataset.write_lock`` — if another build holds
        the lock, raises ValidationError (prevents snapshot switch during an
        in-flight write, per ADR-018 D5).
        """
        pipeline = await self._get_pipeline_or_raise(api_name)
        execution = await self._get_execution_or_raise(api_name, build_id)

        if not execution.output_snapshot_id:
            raise ValidationError(f"Build {build_id} has no output snapshot — cannot data-rollback")

        # Find the sink dataset
        result = await self._session.execute(
            select(DatasetGovernanceModel).where(DatasetGovernanceModel.api_name == pipeline.sink_dataset_api_name)
        )
        dataset = result.scalar_one_or_none()
        if dataset is None:
            raise NotFoundError("Dataset", pipeline.sink_dataset_api_name)

        # Concurrency guard: refuse if a write lock is held by another build
        if dataset.write_lock and dataset.write_lock != build_id:
            raise ValidationError(
                f"Dataset '{pipeline.sink_dataset_api_name}' is locked by build "
                f"'{dataset.write_lock}' — cannot rollback until it completes"
            )

        async with self.transaction():
            # Switch snapshot + release lock (if we held it)
            dataset.current_snapshot_id = execution.output_snapshot_id
            if dataset.write_lock == build_id:
                dataset.write_lock = None
            dataset.updated_at = utcnow()

        return {
            "dataset_api_name": pipeline.sink_dataset_api_name,
            "new_snapshot_id": execution.output_snapshot_id,
            "rolled_back_at": utcnow().isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════
    # Execution monitoring
    # ═══════════════════════════════════════════════════════════════

    async def list_builds(
        self,
        api_name: str,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[BuildResponse], int]:
        """List builds for a pipeline (batch-loads version_number to avoid N+1)."""
        pipeline = await self._get_pipeline_or_raise(api_name)
        query = select(PipelineExecutionModel).where(PipelineExecutionModel.pipeline_id == pipeline.id)

        if status:
            query = query.where(PipelineExecutionModel.current_state == status)

        count_query = select(sa_func.count()).select_from(query.subquery())
        total: int = (await self._session.execute(count_query)).scalar() or 0

        query = query.order_by(PipelineExecutionModel.created_at.desc()).offset(offset).limit(limit)
        rows = await self._session.execute(query)
        executions = rows.scalars().all()

        # Batch-load version_numbers (avoid N+1: one query for all executions)
        version_ids = [e.version_id for e in executions if e.version_id]
        version_map: dict[str, int] = {}
        if version_ids:
            vn_rows = await self._session.execute(
                select(PipelineVersionModel.id, PipelineVersionModel.version_number).where(
                    PipelineVersionModel.id.in_(version_ids)
                )
            )
            version_map = {vid: vnum for vid, vnum in vn_rows.all()}

        builds = [self._execution_to_response_sync(e, api_name, version_map.get(e.version_id, 0)) for e in executions]
        return builds, total

    async def get_build_detail(self, api_name: str, build_id: str) -> BuildDetailResponse:
        """Get a single build with node runs + state history (for detail view)."""
        from ontology.core.schemas.pipeline_builder import (
            NodeRunResponse,
            StateHistoryResponse,
        )

        execution = await self._get_execution_or_raise(api_name, build_id)

        # Load node runs
        nr_rows = await self._session.execute(
            select(PipelineNodeRunModel)
            .where(PipelineNodeRunModel.execution_id == execution.id)
            .order_by(PipelineNodeRunModel.started_at.asc())
        )
        node_runs = [
            NodeRunResponse(
                node_id=nr.node_id,
                node_type=nr.node_type,
                engine=nr.engine,
                status=nr.current_state,  # type: ignore[arg-type]
                started_at=nr.started_at,
                finished_at=nr.finished_at,
                duration_ms=nr.duration_ms,
                error_message=nr.error_message,
                attempt=nr.attempt,
                rows_in=nr.rows_in,
                rows_out=nr.rows_out,
                bytes_processed=nr.bytes_processed,
            )
            for nr in nr_rows.scalars().all()
        ]

        # Load state history
        sh_rows = await self._session.execute(
            select(PipelineStateHistoryModel)
            .where(PipelineStateHistoryModel.execution_id == execution.id)
            .order_by(PipelineStateHistoryModel.changed_at.asc())
        )
        state_history = [
            StateHistoryResponse(
                from_state=sh.from_state,
                to_state=sh.to_state,
                reason=sh.reason,
                changed_by=sh.changed_by,
                changed_at=sh.changed_at,
            )
            for sh in sh_rows.scalars().all()
        ]

        version = await self._session.get(PipelineVersionModel, execution.version_id)
        return BuildDetailResponse(
            build_id=execution.id,
            pipeline_api_name=api_name,
            version_id=execution.version_id,
            version_number=version.version_number if version else 0,
            status=execution.current_state,
            trigger_type=execution.trigger_type,
            triggered_by=execution.triggered_by,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            duration_ms=execution.duration_ms,
            error_message=execution.error_message,
            output_snapshot_id=execution.output_snapshot_id,
            execution_meta=execution.execution_meta,
            node_runs=node_runs,
            state_history=state_history,
            created_at=execution.created_at,
        )

    async def get_build(self, api_name: str, build_id: str) -> BuildResponse:
        """Get a single build (lightweight — no node_runs/state_history)."""
        execution = await self._get_execution_or_raise(api_name, build_id)
        return await self._execution_to_response(execution, api_name)

    # ═══════════════════════════════════════════════════════════════
    # Schedules
    # ═══════════════════════════════════════════════════════════════

    async def create_schedule(self, api_name: str, data: ScheduleCreate) -> ScheduleResponse:
        """Create a schedule for a pipeline."""
        pipeline = await self._get_pipeline_or_raise(api_name)

        existing = await self._session.execute(
            select(PipelineScheduleModel).where(
                PipelineScheduleModel.pipeline_id == pipeline.id,
                PipelineScheduleModel.api_name == data.api_name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Schedule '{data.api_name}' already exists for pipeline '{api_name}'")

        now = utcnow()
        schedule = PipelineScheduleModel(
            id=new_uuid(),
            pipeline_id=pipeline.id,
            api_name=data.api_name,
            display_name=data.display_name,
            trigger=data.trigger.model_dump(mode="json"),
            action_config=data.action_config.model_dump(mode="json"),
            enabled=data.enabled,
            created_at=now,
            updated_at=now,
        )
        async with self.transaction():
            self._session.add(schedule)

        return self._schedule_to_response(schedule, api_name)

    async def list_schedules(self, api_name: str) -> list[ScheduleResponse]:
        """List schedules for a pipeline."""
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineScheduleModel)
            .where(PipelineScheduleModel.pipeline_id == pipeline.id)
            .order_by(PipelineScheduleModel.created_at.desc())
        )
        schedules = result.scalars().all()
        return [self._schedule_to_response(s, api_name) for s in schedules]

    async def update_schedule(self, api_name: str, schedule_api_name: str, data: ScheduleUpdate) -> ScheduleResponse:
        """Update a schedule."""
        schedule = await self._get_schedule_or_raise(api_name, schedule_api_name)

        if data.display_name is not None:
            schedule.display_name = data.display_name
        if data.trigger is not None:
            schedule.trigger = data.trigger.model_dump(mode="json")
        if data.action_config is not None:
            schedule.action_config = data.action_config.model_dump(mode="json")
        if data.enabled is not None:
            schedule.enabled = data.enabled

        schedule.updated_at = utcnow()
        async with self.transaction():
            pass  # updates applied to session; transaction commits atomically

        return self._schedule_to_response(schedule, api_name)

    async def delete_schedule(self, api_name: str, schedule_api_name: str) -> None:
        """Delete a schedule."""
        schedule = await self._get_schedule_or_raise(api_name, schedule_api_name)
        async with self.transaction():
            await self._session.delete(schedule)

    async def toggle_schedule(self, api_name: str, schedule_api_name: str, enabled: bool) -> ScheduleResponse:
        """Enable or disable a schedule."""
        schedule = await self._get_schedule_or_raise(api_name, schedule_api_name)
        schedule.enabled = enabled
        schedule.updated_at = utcnow()
        async with self.transaction():
            pass  # update committed atomically
        return self._schedule_to_response(schedule, api_name)

    # ═══════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════

    async def _get_pipeline_or_raise(self, api_name: str, include_deleted: bool = False) -> PipelineModel:
        query = select(PipelineModel).where(PipelineModel.api_name == api_name)
        if not include_deleted:
            query = query.where(PipelineModel.deleted_at.is_(None))
        result = await self._session.execute(query)
        pipeline = result.scalar_one_or_none()
        if pipeline is None:
            raise NotFoundError("Pipeline", api_name)
        return pipeline

    async def _get_version_number(self, version_id: str | None) -> int | None:
        if version_id is None:
            return None
        result = await self._session.execute(
            select(PipelineVersionModel.version_number).where(PipelineVersionModel.id == version_id)
        )
        return result.scalar_one_or_none()

    async def _get_execution_or_raise(self, api_name: str, build_id: str) -> PipelineExecutionModel:
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineExecutionModel).where(
                PipelineExecutionModel.id == build_id,
                PipelineExecutionModel.pipeline_id == pipeline.id,
            )
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise NotFoundError("PipelineExecution", f"{api_name}/{build_id}")
        return execution

    async def _get_schedule_or_raise(self, api_name: str, schedule_api_name: str) -> PipelineScheduleModel:
        pipeline = await self._get_pipeline_or_raise(api_name)
        result = await self._session.execute(
            select(PipelineScheduleModel).where(
                PipelineScheduleModel.pipeline_id == pipeline.id,
                PipelineScheduleModel.api_name == schedule_api_name,
            )
        )
        schedule = result.scalar_one_or_none()
        if schedule is None:
            raise NotFoundError("PipelineSchedule", f"{api_name}/{schedule_api_name}")
        return schedule

    # ── Response builders ──

    def _pipeline_to_response(self, model: PipelineModel, version_number: int | None = None) -> PipelineResponse:
        return PipelineResponse(
            api_name=model.api_name,
            display_name=model.display_name,
            description=model.description or "",
            status=model.status,  # type: ignore[arg-type]
            current_version_id=model.current_version_id,
            current_version_number=version_number,
            write_mode=model.write_mode,  # type: ignore[arg-type]
            sink_dataset_api_name=model.sink_dataset_api_name,
            owner_id=model.owner_id,
            project_id=model.project_id,
            deleted_at=model.deleted_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _version_to_response(self, model: PipelineVersionModel) -> PipelineVersionResponse:
        return PipelineVersionResponse(
            id=model.id,
            pipeline_id=model.pipeline_id,
            version_number=model.version_number,
            graph=PipelineIR(**model.graph) if model.graph else PipelineIR(),
            inferred_schema=model.inferred_schema,
            change_summary=model.change_summary or "",
            created_by=model.created_by,
            created_at=model.created_at,
        )

    async def _execution_to_response(self, model: PipelineExecutionModel, pipeline_api_name: str) -> BuildResponse:
        """Convert execution to BuildResponse (resolves version_number).

        No ``try/except`` — a DB failure here is a real error (not a test
        artifact). Tests should use a real session or mock the query result.
        """
        version_number = 0
        if model.version_id:
            vn = await self._session.execute(
                select(PipelineVersionModel.version_number).where(PipelineVersionModel.id == model.version_id)
            )
            vn_val = vn.scalar()
            if isinstance(vn_val, int):
                version_number = vn_val
        return self._execution_to_response_sync(model, pipeline_api_name, version_number)

    def _execution_to_response_sync(
        self,
        model: PipelineExecutionModel,
        pipeline_api_name: str,
        version_number: int,
    ) -> BuildResponse:
        """Synchronous response builder (no DB access — caller provides version_number)."""
        return BuildResponse(
            build_id=model.id,
            pipeline_api_name=pipeline_api_name,
            version_id=model.version_id,
            version_number=version_number,
            status=model.current_state,  # type: ignore[arg-type]
            trigger_type=model.trigger_type,  # type: ignore[arg-type]
            triggered_by=model.triggered_by,
            started_at=model.started_at,
            finished_at=model.finished_at,
            duration_ms=model.duration_ms,
            error_message=model.error_message,
            created_at=model.created_at,
        )

    def _schedule_to_response(self, model: PipelineScheduleModel, pipeline_api_name: str) -> ScheduleResponse:
        from ontology.core.schemas.pipeline_builder import ActionConfig, TriggerConfig

        trigger_data = model.trigger or {}
        action_data = model.action_config or {}
        return ScheduleResponse(
            id=model.id,
            pipeline_api_name=pipeline_api_name,
            api_name=model.api_name,
            display_name=model.display_name or "",
            trigger=TriggerConfig(**trigger_data) if trigger_data.get("type") else TriggerConfig(type="time"),
            action_config=ActionConfig(**action_data) if action_data else ActionConfig(),
            enabled=model.enabled,
            created_by=model.created_by,
            project_id=model.project_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
