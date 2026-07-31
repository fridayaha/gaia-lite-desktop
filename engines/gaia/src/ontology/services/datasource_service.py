"""DataSourceService — data source management orchestration.

Coordinates Metadata (PG), Catalog (Gravitino), Engine (Trino),
Pipeline (SeaTunnel), and Dataset (Iceberg) layers for end-to-end
data source lifecycle management.

Per the architecture: this is a pure coordination layer. It does not
directly connect to external data sources — all exploration goes
through Trino (via Gravitino Connector), and all sync goes through
SeaTunnel.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from ontology.config.settings import settings
from ontology.core.exceptions import (
    CatalogNotRegisteredError,
    DataSourceUnreachableError,
    GravitinoUnavailableError,
    IcebergUnavailableError,
    NotFoundError,
    OntologyError,
    ValidationError,
)
from ontology.core.models.defaults import utcnow
from ontology.core.schemas.dataset import ColumnDef, DatasetSchema, DatasetSnapshot
from ontology.core.schemas.datasource import (
    ColumnInfo,
    ConnectionTestResult,
    Credential,
    CredentialCreate,
    CredentialResponse,
    DatasetGovernance,
    DatasetGovernanceCreate,
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    ExploreResult,
    ImpactAnalysis,
    ImpactAnalysisRequest,
    ImpactItem,
    SyncTask,
    SyncTaskCreate,
    TableInfo,
)
from ontology.core.schemas.pipeline import PipelineStatus
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.ingestion_filter import IngestionFilter

if TYPE_CHECKING:
    # IcebergStore 仅类型注解；移入 TYPE_CHECKING 避免 lite 版拉 pyiceberg 重依赖
    # （A3）。engine 按 QueryEngine 契约注解（Trino/DuckDB 共实现，B2）。
    from ontology.layers.dataset.iceberg_store import IcebergStore
    from ontology.layers.engine.base import QueryEngine
    from ontology.layers.engine.duckdb_engine import DuckDBEngine
    from ontology.services.object_index_funnel import ObjectIndexFunnel


class DataSourceService(MetadataOwnerMixin):
    """Data source management orchestration service.

    Coordinates five layers to provide:
      - DataSource lifecycle (create, read, update, delete)
      - Credential management
      - Schema exploration (via Trino through Gravitino)
      - Sync task management (via SeaTunnel)
      - Dataset governance metadata (in PG)
      - Impact analysis for destructive operations
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry | None,
        engine: "QueryEngine",
        pipeline: SeaTunnelEngine | None,
        dataset: "IcebergStore | None",
        ingestion_filter: "IngestionFilter | None" = None,
        object_index_funnel: "ObjectIndexFunnel | None" = None,
    ) -> None:
        self.metadata = metadata
        # catalog/pipeline/dataset lite 装配传 None（lite 走 DuckDB 分支不触达）。
        # 用 property（下方）窄化回非 None 类型，让 full 路径调用点 mypy 不报
        # union-attr；lite 下访问这些 property 即走错路径，assert 兜底抛错。
        self._catalog = catalog
        self.engine = engine
        self._pipeline = pipeline
        self._dataset = dataset
        # IngestionFilter rewrites incremental ingestion queries to exclude
        # rows Gaia wrote back to the source (feedback-loop prevention).
        # Optional for backward compat / isolated tests.
        self._ingestion_filter = ingestion_filter or IngestionFilter()
        # ADR-021 §3.1: register_virtual_table 后异步触发 VIRTUAL 图投影。
        # Optional — 测试/未接线环境为 None 时跳过。
        self._object_index_funnel = object_index_funnel

    @property
    def catalog(self) -> GravitinoRegistry:
        """Gravitino catalog。lite 装配为 None——访问即说明走了 full 路径
        （lite 分支应提前 return），断言兜底。"""
        assert self._catalog is not None, "catalog 未装配（lite 版不应触达 Gravitino 路径）"
        return self._catalog

    @catalog.setter
    def catalog(self, value: GravitinoRegistry | None) -> None:
        self._catalog = value

    @property
    def pipeline(self) -> SeaTunnelEngine:
        """SeaTunnel 引擎。lite 装配为 None——访问即走错路径，断言兜底。"""
        assert self._pipeline is not None, "pipeline 未装配（lite 版不应触达 SeaTunnel 路径）"
        return self._pipeline

    @pipeline.setter
    def pipeline(self, value: SeaTunnelEngine | None) -> None:
        self._pipeline = value

    @property
    def dataset(self) -> "IcebergStore":
        """IcebergStore。lite 装配为 None——访问即走错路径，断言兜底。"""
        assert self._dataset is not None, "dataset 未装配（lite 版不应触达 Iceberg 路径）"
        return self._dataset

    @dataset.setter
    def dataset(self, value: "IcebergStore | None") -> None:
        self._dataset = value

    # ═════════════════════════════════════════════════════════════
    # Credential Management
    # ═════════════════════════════════════════════════════════════

    async def create_credential(self, cred: CredentialCreate) -> CredentialResponse:
        """Create a new credential."""
        result = await self.metadata.create_credential(cred)
        return CredentialResponse(
            id=result.id,
            api_name=result.api_name,
            credential_type=result.credential_type,
            secret_data="***",
            created_at=result.created_at,
        )

    async def list_credentials(self) -> list[CredentialResponse]:
        """List all credentials (secrets masked)."""
        creds = await self.metadata.list_credentials()
        return [
            CredentialResponse(
                id=c.id,
                api_name=c.api_name,
                credential_type=c.credential_type,
                secret_data="***",
                created_at=c.created_at,
            )
            for c in creds
        ]

    async def get_credential(self, api_name: str) -> Credential:
        """Get a credential by api_name (full data, for internal use)."""
        return await self.metadata.get_credential(api_name)

    async def delete_credential(self, api_name: str) -> None:
        """Delete a credential."""
        await self.metadata.delete_credential(api_name)

    # ═════════════════════════════════════════════════════════════
    # DataSource Lifecycle
    # ═════════════════════════════════════════════════════════════

    async def _resolve_credentials(self, ds: DataSourceCreate | DataSource) -> tuple[str, str]:
        """Resolve (username, password) for a datasource.

        Secrets live in the linked Credential record (``secret_data``), not in
        ``connector_config`` — callers (benchmark setup, API clients) attach a
        credential_id and keep connector_config free of secrets. This resolves
        the credential first, falling back to any username/password stashed in
        connector_config for legacy/back-compat. Returns ("", "") if neither is
        available, which will surface as a clear auth failure downstream.
        """
        cred_id = getattr(ds, "credential_id", None)
        if cred_id:
            try:
                cred = await self.metadata.get_credential_by_id(cred_id)
                secret = cred.secret_data or {}
                # username_password / basic credential types store {username, password}
                username = str(secret.get("username", secret.get("user", "")))
                password = str(secret.get("password", ""))
                if username or password:
                    return username, password
            except Exception:
                # Best-effort: fall through to connector_config.
                pass
        cfg = ds.connector_config or {}
        return str(cfg.get("username", "")), str(cfg.get("password", ""))

    async def create_datasource(self, ds: DataSourceCreate) -> DataSource:
        """Create a data source and register it.

        1. PG: Store DataSource record
        2. full: Gravitino REST 注册 catalog（Trino 自动加载）；
           lite (B4): connector.to_duckdb_attach → DuckDBEngine.attach 注册 src_<api_name>
        3. Compute capabilities from connector_type
        """
        # Step 1: PG
        record = await self.metadata.create_datasource(ds)

        # Step 2: 外部源注册
        try:
            if settings.edition == "lite":
                await self._register_lite_datasource(ds)
            else:
                await self._register_datasource_catalog(ds)
        except Exception as exc:
            # 注册失败 — 更新状态但不阻断 PG 记录
            await self.metadata.update_datasource(ds.api_name, {"status": "ERROR"})
            raise OntologyError(
                f"Failed to register data source"
                f"{' in Gravitino/Trino' if settings.edition != 'lite' else ' in DuckDB'}: {exc}"
            ) from exc

        # 注册成功 — 标记为已连接（catalog 可用即代表连通性验证通过）
        record = await self.metadata.update_datasource(ds.api_name, {"status": "CONNECTED"})
        record.capabilities = self._compute_capabilities(record.connector_type)
        return record

    async def _register_lite_datasource(self, ds: DataSourceCreate | DataSource) -> None:
        """lite 版（B4）：经 connector 生成 DuckDB ATTACH/导入语句 → engine.attach/execute。

        跳过 Gravitino/SeaTunnel（lite 不装）。CSV 走主库表（CREATE TABLE AS SELECT），
        其余走 ATTACH 外部 catalog（src_<api_name>）。凭据从 Credential 解析。
        """
        from ontology.plugins.connectors import ConnectorRegistry

        credentials = await self._resolve_credentials(ds)
        connector = ConnectorRegistry().create(ds.connector_type, ds.connector_config, credentials)
        attach_sql = connector.to_duckdb_attach(ds.api_name)
        # CSV connector 返回 CREATE TABLE（无 ATTACH），用 execute；其余用 attach。
        # 统一走 execute（attach 内部也调 execute + 记录别名），CSV 不记 _attached。
        if connector.connector_type in ("csv", "csv_file"):
            await self._duckdb.execute(attach_sql)
        else:
            await self._duckdb.attach(connector.attach_alias(ds.api_name), attach_sql)

    async def _register_datasource_catalog(self, ds: DataSourceCreate | DataSource) -> None:
        """Register the appropriate Gravitino catalog for a data source.

        Routing by connector_type (§9.1):
          - JDBC with a provider (mysql/pg/国产 PG/MySQL 兼容库): jdbc catalog via
            Gravitino REST API (``register_jdbc_catalog``). Trino Gravitino connector
            auto-discovers the new catalog within ~10s.
          - JDBC with provider=None (达梦 / generic_jdbc): skip — MANAGED-only.
          - File/object storage (s3/minio/oss/hdfs): Fileset catalog.
          - Lakehouse federation (hive/delta/hudi/paimon): lakehouse catalog.
          - Kafka: kafka catalog (topic metadata only).
          - ES / cloud-warehouse non-PG / others: skip catalog (MANAGED-only).

        Failures are surfaced to the caller (create_datasource marks the
        record ERROR); catalog removal on delete is already best-effort.
        """
        ct = ds.connector_type.lower()

        # JDBC providers (relational)
        jdbc_provider = self._JDBC_CONNECTOR_MAP.get(ct)
        if jdbc_provider is not None:
            driver = self._resolve_driver(ct, ds.connector_config)
            jdbc_user, jdbc_password = await self._resolve_credentials(ds)
            jdbc_url = self._build_jdbc_url(ct, ds.connector_config, include_database=False)
            jdbc_url = self._apply_catalog_host_override(jdbc_url)
            await self.catalog.register_jdbc_catalog(
                catalog_name=ds.api_name,
                provider=jdbc_provider,
                jdbc_url=jdbc_url,
                jdbc_database=ds.connector_config.get("database", ""),
                jdbc_user=jdbc_user,
                jdbc_password=jdbc_password,
                jdbc_driver=driver,
            )
            return

        # provider=None JDBC types (达梦/generic_jdbc): no catalog — MANAGED only
        if jdbc_provider is None and ct in self._JDBC_CONNECTOR_MAP:
            return

        # Fileset catalog (s3/minio/oss/hdfs)
        fileset_provider = self._FILESET_PROVIDER_MAP.get(ct)
        if fileset_provider is not None:
            ak, sk = await self._resolve_access_keys(ds)
            cfg = ds.connector_config
            fileset_props: dict[str, str] = {
                "location": f"s3://{cfg.get('bucket', '')}/{cfg.get('path', '')}".rstrip("/"),
                "s3-endpoint": str(cfg.get("endpoint", "")),
                "s3-access-key-id": ak,
                "s3-secret-access-key": sk,
            }
            await self.catalog.register_fileset_catalog(
                catalog_name=ds.api_name,
                provider=fileset_provider,
                properties=fileset_props,
            )
            return

        # Lakehouse federation catalog (hive/delta/hudi/paimon)
        lakehouse_provider = self._LAKEHOUSE_PROVIDER_MAP.get(ct)
        if lakehouse_provider is not None:
            await self.catalog.register_lakehouse_catalog(
                catalog_name=ds.api_name,
                provider=lakehouse_provider,
                properties={str(k): str(v) for k, v in (ds.connector_config or {}).items()},
            )
            return

        # Kafka catalog (topic metadata)
        if ct == "kafka":
            cfg = ds.connector_config
            await self.catalog.register_kafka_catalog(
                catalog_name=ds.api_name,
                bootstrap_servers=str(cfg.get("bootstrap_servers", "")),
            )
            return

        # ES / cloud-warehouse non-PG / unknown: no catalog registration

    async def _resolve_access_keys(self, ds: DataSourceCreate | DataSource) -> tuple[str, str]:
        """Resolve (access_key, secret_key) for a file/object-storage datasource.

        Mirrors ``_resolve_credentials`` but for access_key/secret_key pairs
        stored in the linked Credential (preferred) or connector_config (legacy).
        """
        cred_id = getattr(ds, "credential_id", None)
        if cred_id:
            try:
                cred = await self.metadata.get_credential_by_id(cred_id)
                secret = cred.secret_data or {}
                ak = str(secret.get("access_key", secret.get("access_key_id", "")))
                sk = str(secret.get("secret_key", secret.get("secret_access_key", "")))
                if ak or sk:
                    return ak, sk
            except Exception:
                pass
        cfg = ds.connector_config or {}
        return str(cfg.get("access_key", "")), str(cfg.get("secret_key", ""))

    async def get_datasource(self, api_name: str) -> DataSource:
        """Get a data source by api_name."""
        record = await self.metadata.get_datasource(api_name)
        record.capabilities = self._compute_capabilities(record.connector_type)
        return record

    async def list_datasources(self) -> list[DataSource]:
        """List all data sources."""
        records = await self.metadata.list_datasources()
        for r in records:
            r.capabilities = self._compute_capabilities(r.connector_type)
        return records

    async def update_datasource(self, api_name: str, updates: DataSourceUpdate) -> DataSource:
        """Update a data source."""
        update_dict = updates.model_dump(exclude_none=True)
        return await self.metadata.update_datasource(api_name, update_dict)

    async def delete_datasource(self, api_name: str) -> None:
        """Delete a data source and its Gravitino catalog.

        Also cascades to all associated sync tasks (PG ON DELETE CASCADE).
        Also removes all datasets associated with this data source.

        Transaction: all PG operations (dataset cleanup + datasource delete)
        happen in a single transaction. Gravitino catalog removal is done
        before the PG transaction (external system, can't roll back).

        Raises:
            OntologyError: If Gravitino catalog removal fails unexpectedly.
                           (Does NOT raise if catalog was already removed/404.)
        """
        import logging

        _log = logging.getLogger(__name__)

        if settings.edition == "lite":
            # B4: lite 版 best-effort DETACH 外部 catalog（CSV 无 catalog，跳过）。
            try:
                ds_record = await self.metadata.get_datasource(api_name)
                if ds_record.connector_type.lower() not in ("csv", "csv_file"):
                    await self._duckdb.detach(f"src_{api_name.lower()}")
            except Exception as exc:  # noqa: BLE001 — best-effort，不阻断 PG 删除
                _log.warning("Failed to detach DuckDB catalog '%s': %s", api_name, exc)
        else:
            # Remove Gravitino catalog (external system, best-effort).
            # Trino auto-detects the removal via its Gravitino connector refresh loop (~10s).
            try:
                await self.catalog.remove_catalog(api_name)
            except Exception as exc:
                _log.warning(
                    "Failed to remove Gravitino catalog '%s': %s. Proceeding with PG-level delete.",
                    api_name,
                    exc,
                )

        # PG operations in a single transaction
        try:
            # Remove datasets associated with this data source
            datasets = await self.metadata.list_datasets()
            for ds in datasets:
                if ds.data_source_api_name == api_name:
                    try:
                        await self.metadata.delete_dataset(ds.api_name, auto_commit=False)
                    except Exception:
                        pass  # dataset may already be deleted

            await self.metadata.delete_datasource(api_name, auto_commit=False)
            await self.metadata.commit_transaction()
        except Exception as exc:
            await self.metadata.rollback_transaction()
            _log.error(
                "Failed to delete data source '%s', transaction rolled back: %s",
                api_name,
                exc,
            )
            raise

    async def test_connection(self, api_name: str) -> ConnectionTestResult:
        """Test connectivity to a data source via Trino.

        Only JDBC sources with a Gravitino provider (VIRTUAL-able) can be
        tested via the Trino catalog. File/Kafka/NoSQL and provider=None
        JDBC sources (达梦/通用 JDBC) have no Trino catalog to dial —
        connectivity is implicitly verified on first sync.
        """
        ds = await self.metadata.get_datasource(api_name)
        if settings.edition == "lite":
            # B4: lite 版探活——CSV/SQLite 查文件存在性，PG/MySQL 经 engine.test_connection
            # 复查 ATTACH 别名是否在 duckdb_databases()（ATTACH 成功即源可达）。
            from ontology.plugins.connectors import ConnectorRegistry

            registry = ConnectorRegistry()
            if not registry.is_supported(ds.connector_type):
                return ConnectionTestResult(
                    success=False,
                    message=f"桌面版不支持数据源类型 {ds.connector_type}",
                )
            try:
                connector = registry.create(ds.connector_type, ds.connector_config, await self._resolve_credentials(ds))
                if not await connector.test_connection():
                    await self.metadata.update_datasource(api_name, {"status": "ERROR"})
                    return ConnectionTestResult(success=False, message="数据源不可达")
                ok = await self.engine.test_connection(
                    connector.attach_alias(ds.api_name)
                    if connector.connector_type not in ("csv", "csv_file")
                    else "main"
                )
            except Exception as exc:  # noqa: BLE001 — 探活失败统一报错
                await self.metadata.update_datasource(api_name, {"status": "ERROR"})
                return ConnectionTestResult(success=False, message=f"连接测试失败：{exc}")
            if ok:
                await self.metadata.update_datasource(api_name, {"status": "CONNECTED"})
                return ConnectionTestResult(success=True, message="Connection successful")
            await self.metadata.update_datasource(api_name, {"status": "ERROR"})
            return ConnectionTestResult(success=False, message="Connection failed")
        if self._JDBC_CONNECTOR_MAP.get(ds.connector_type) is None:
            return ConnectionTestResult(
                success=False,
                message=f"Connection test not supported for {ds.connector_type}",
            )

        ok = await self.engine.test_connection(ds.gravitino_catalog_name or ds.api_name)
        if ok:
            await self.metadata.update_datasource(api_name, {"status": "CONNECTED"})
            return ConnectionTestResult(success=True, message="Connection successful")
        else:
            await self.metadata.update_datasource(api_name, {"status": "ERROR"})
            return ConnectionTestResult(success=False, message="Connection failed")

    async def reconcile_catalogs(self) -> int:
        """Re-register Gravitino catalogs missing for JDBC data sources.

        Gravitino catalogs are stateless configuration (provider + JDBC URL +
        creds) registered at data-source create time. They can be lost when
        Gravitino is rebuilt/upgraded (its PG-backed metadata reset) while
        Gaia's ``data_sources`` table still records them as present — leaving
        the two stores out of sync and every explore/query failing with
        ``CATALOG_NOT_FOUND``.

        This method reconciles the two: for every JDBC data source whose
        ``gravitino_catalog_name`` is missing from Gravitino's live catalog
        list, it re-runs ``_register_datasource_catalog`` (idempotent) and
        flips status back to ``CONNECTED``. Non-JDBC sources (File/Kafka/
        provider=None JDBC) have no catalog and are skipped. Best-effort —
        individual re-register failures are logged and counted, never raised.

        Returns the number of catalogs successfully re-registered.
        """
        _log = logging.getLogger(__name__)
        try:
            live_catalogs = await self.catalog.list_catalogs()
        except Exception as exc:  # noqa: BLE001 — Gravitino 不可用不 crash
            _log.warning("reconcile_catalogs: cannot list Gravitino catalogs: %s", exc)
            return 0
        live_names = {c.get("name") for c in live_catalogs}

        try:
            datasources = await self.metadata.list_datasources()
        except Exception as exc:  # noqa: BLE001
            _log.warning("reconcile_catalogs: cannot list datasources: %s", exc)
            return 0

        healed = 0
        for ds in datasources:
            # 非 JDBC（File/Kafka/NoSQL/provider=None）无 Gravitino catalog，跳过
            if self._JDBC_CONNECTOR_MAP.get(ds.connector_type) is None:
                continue
            catalog_name = ds.gravitino_catalog_name or ds.api_name
            if catalog_name in live_names:
                continue
            _log.info(
                "reconcile_catalogs: catalog %s missing for datasource %s — re-registering",
                catalog_name,
                ds.api_name,
            )
            try:
                await self._register_datasource_catalog(ds)
                healed += 1
                # 重注册成功，顺便把状态刷新为 CONNECTED（可能已被探活标成 ERROR）
                if ds.status != "CONNECTED":
                    await self.metadata.update_datasource(ds.api_name, {"status": "CONNECTED"})
            except Exception as exc:  # noqa: BLE001 — 单个失败不中断其他源
                _log.warning(
                    "reconcile_catalogs: re-register failed for %s: %s",
                    ds.api_name,
                    exc,
                )
        if healed:
            _log.info("reconcile_catalogs: re-registered %d missing catalog(s)", healed)
        return healed

    async def run_health_check_loop(self, interval: int = 60) -> None:
        """Background health probe — auto-recover stale ERROR/DISCONNECTED status.

        两个职责：
        1. **catalog 一致性保障**：每隔 ``interval`` 秒调 ``reconcile_catalogs``，
           对 Gravitino 里缺失的 catalog 自动重注册。引擎重建/升级后 catalog
           丢失时，这里会自愈——不依赖用户手动点「重试」。
        2. **源连通性探活**：对非 CONNECTED 的 JDBC 数据源调
           ``test_connection``，成功刷新为 CONNECTED（自愈）。CONNECTED 的
           数据源不重复探（catalog 一致性已校验，查询路径隐式验证连通性）。

        单次探活异常不 crash 循环（只 log），下一 tick 重试。
        """
        _log = logging.getLogger(__name__)
        while True:
            try:
                # 先做 catalog 一致性保障（含 Gravitino 重建后的自愈）
                await self.reconcile_catalogs()

                datasources = await self.metadata.list_datasources()
                # 只探活非 CONNECTED 的 JDBC 数据源
                candidates = [
                    ds
                    for ds in datasources
                    if ds.status != "CONNECTED" and self._JDBC_CONNECTOR_MAP.get(ds.connector_type) is not None
                ]
                for ds in candidates:
                    try:
                        await self.test_connection(ds.api_name)
                    except Exception as exc:  # noqa: BLE001 — 后台任务容错
                        _log.warning(
                            "Health check for %s failed (non-fatal): %s",
                            ds.api_name,
                            exc,
                        )
            except Exception as exc:  # noqa: BLE001 — list_datasources 失败不 crash
                _log.warning("Health check loop iteration failed (non-fatal): %s", exc)
            await asyncio.sleep(interval)

    # ═════════════════════════════════════════════════════════════
    # Schema Exploration
    # ═════════════════════════════════════════════════════════════

    def _enrich_unreachable(self, exc: DataSourceUnreachableError, ds: Any) -> DataSourceUnreachableError:
        """Re-raise a DataSourceUnreachableError with user-facing context.

        The engine layer only sees the catalog name + the raw Trino error;
        it can't name the data source. Here we know the DataSource record,
        so we prepend a human-readable prefix (connector type + configured
        host + port) so the UI can show *which* source is down and where,
        not just “探索失败”.
        """
        cfg = ds.connector_config or {}
        host = cfg.get("host", "")
        port = cfg.get("port", "")
        loc = f"{ds.connector_type}@{host}" + (f":{port}" if port else "")
        return DataSourceUnreachableError(
            f"无法连接到数据源 {loc}：{exc}",
            code="DATASOURCE_UNREACHABLE",
        )

    @property
    def _duckdb(self) -> "DuckDBEngine":
        """lite 版 engine 必为 DuckDBEngine；cast 让 mypy 看到 attach/execute/detach。

        仅在 ``settings.edition == 'lite'`` 分支调用（lite 装配保证 engine 是
        DuckDBEngine，见 container.engine）。
        """
        return cast("DuckDBEngine", self.engine)

    async def _lite_catalog_and_schema(self, ds: Any, database: str) -> tuple[str, str]:
        """lite 版（B4）：推导 DuckDB catalog 名 + 默认 schema。

        catalog = ``src_<api_name>``（ATTACH 别名，PG/MySQL/SQLite）；CSV 走主库表
        （``CREATE TABLE AS SELECT`` 导入），catalog = 主库 ``database_name``——即
        DuckDB 文件名 stem（如 ``warehouse.duckdb`` → ``'warehouse'``），由
        ``engine.current_database()`` 查得。**不是** ``'main'``：``'main'`` 是 DuckDB
        默认 schema 名，``duckdb_tables()`` 的 ``database_name`` 列对主库表是文件
        stem，硬编码 ``'main'`` 会致 ``list_tables('main', ...)`` 查空。
        schema 由 connector.default_schema() 推导（PG=public、MySQL=配置 database、
        CSV/SQLite=main），database 参数覆盖。
        """
        from ontology.plugins.connectors import ConnectorRegistry

        ct = ds.connector_type.lower()
        if ct in ("csv", "csv_file"):
            # CSV 走主库表（无 src_ 前缀），catalog=主库 database_name（文件 stem）。
            # 经 self._duckdb（cast 到 DuckDBEngine）访问专属 current_database()。
            catalog_name = await self._duckdb.current_database()
        else:
            # 其余走 ATTACH 别名 src_<api_name>。
            catalog_name = f"src_{ds.api_name.lower()}"
        if database:
            schema = database
        else:
            try:
                connector = ConnectorRegistry().create(ds.connector_type, ds.connector_config)
                schema = connector.default_schema()
            except Exception:
                schema = ""
        return catalog_name, schema

    async def explore(self, api_name: str, database: str = "") -> ExploreResult:
        """Explore a data source's schema.

        full 版经 Trino（Gravitino catalog）；lite 版（B4）经 DuckDBEngine 查
        ATTACH 后的 src_<api_name> catalog（CSV 走主库表）。返回表列表，列按需 describe。
        """
        ds = await self.metadata.get_datasource(api_name)
        if settings.edition == "lite":
            # B4: lite 版经 DuckDBEngine 查 ATTACH 后的 src_<api_name> catalog
            # （CSV 走主库表）。catalog/schema 由 connector 推导，无 Gravitino 自愈。
            catalog_name, schema = await self._lite_catalog_and_schema(ds, database)
            try:
                table_names = await self.engine.list_tables(catalog_name, schema)
            except DataSourceUnreachableError as exc:
                raise self._enrich_unreachable(exc, ds) from exc
            return ExploreResult(
                database=schema,
                tables=[
                    TableInfo(
                        name=full_name.rsplit(".", 1)[-1] if "." in full_name else full_name,
                        schema=full_name.rsplit(".", 1)[0] if "." in full_name else schema,
                        columns=[],
                    )
                    for full_name in (table_names or [])
                ],
            )
        catalog_name = ds.gravitino_catalog_name or ds.api_name
        # 默认 schema 按 connector_type / 配置区分：
        # - PostgreSQL 系："public"（PG 默认 schema，业务表所在）
        # - MySQL/MariaDB：用 connector_config.database（mysql 的 schema = database，
        #   即用户创建数据源时填的数据库名）。未配置 database 时退化为空串枚举。
        if database:
            schema = database
        elif ds.connector_type.lower() in ("postgresql", "postgres"):
            # PG：优先用 connector_config.schema（用户指定的探索起始命名空间），
            # 默认 public（PG 默认 schema，业务表所在）。
            schema = ds.connector_config.get("schema") or "public"
        else:
            # MySQL：用 connector_config.database（mysql 的 schema = database，
            # 即用户创建数据源时填的默认库）。未配置时退化为空串枚举实例所有库。
            schema = ds.connector_config.get("database", "") or ""

        try:
            table_names = await self.engine.list_tables(catalog_name, schema)
        except CatalogNotRegisteredError as exc:
            # Gravitino catalog 丢失（引擎重建/升级/手动清理后常见）。
            # 自动重注册一次再重试——catalog 是无状态配置，重注册幂等且不丢源数据。
            # 失败则把原始错误透出，前端提示用户「连接已失效，正在恢复」。
            _log = logging.getLogger(__name__)
            _log.warning(
                "Catalog %s missing for datasource %s — attempting self-heal re-register",
                catalog_name,
                api_name,
            )
            try:
                await self._register_datasource_catalog(ds)
            except Exception as reg_exc:  # noqa: BLE001 — 自愈失败不掩盖根因
                _log.warning("Self-heal re-register failed for %s: %s", api_name, reg_exc)
                raise exc from reg_exc
            # 重注册成功，重试一次查询
            table_names = await self.engine.list_tables(catalog_name, schema)
        except DataSourceUnreachableError as exc:
            raise self._enrich_unreachable(exc, ds) from exc
        if table_names is None:
            return ExploreResult(database=schema, tables=[])

        tables: list[TableInfo] = []
        for full_name in table_names:
            parts = full_name.rsplit(".", 1)
            if len(parts) == 2:
                tbl_schema, tbl_name = parts
            else:
                tbl_schema = schema
                tbl_name = full_name

            tables.append(
                TableInfo(
                    name=tbl_name,
                    schema=tbl_schema,
                    columns=[],  # columns loaded on demand — see describe_table()
                )
            )

        return ExploreResult(database=schema, tables=tables)

    async def describe_table(self, api_name: str, database: str, table: str) -> TableInfo:
        """Describe a single table's columns — lazy-loaded on user click.

        Path selection (see CLAUDE.md 通用错误模式 #13 + 列名大小写调研):

        - **JDBC data sources** (Gravitino-managed: PostgreSQL / MySQL /
          Oracle / ...): prefer **Gravitino REST API**
          (``get_table_metadata``). Rationale: Trino's Gravitino Connector
          + ``trino-base-jdbc`` normalizes identifiers to lowercase (Trino
          canonical form, to unify PG/Oracle/Hive dialect differences and
          disambiguate same-name-different-case objects). This destroys
          the original column-name casing (``modelId`` -> ``modelid``),
          and ``DESCRIBE`` cannot recover it. Gravitino REST preserves the
          original casing and additionally exposes primary-key info (the
          Trino path does not). One REST round-trip returns columns +
          indexes + table comment.
        - **Non-JDBC / Gravitino-REST failure**: fall back to Trino
          ``DESCRIBE``. Columns come back lowercased and without PK, but
          this is the only path for sources Gravitino doesn't manage
          (e.g. fileset-only catalogs) or when the REST endpoint is down.

        Data preview (``sample_data``) still goes through Trino and will
        show lowercased headers — that's an accepted inconsistency (方案 B):
        column info favours fidelity, data preview favours the only
        available engine.
        """
        import logging

        _log = logging.getLogger(__name__)
        ds = await self.metadata.get_datasource(api_name)
        if settings.edition == "lite":
            # B4: lite 版经 DuckDB DESCRIBE（duckdb_engine.describe_table 返回
            # column_name/column_type/null）。无 PK 信息（DuckDB DESCRIBE 不暴露）。
            catalog_name, schema = await self._lite_catalog_and_schema(ds, database)
            try:
                rows = await self.engine.describe_table(catalog_name, schema, table)
            except DataSourceUnreachableError as exc:
                raise self._enrich_unreachable(exc, ds) from exc
            return TableInfo(
                name=table,
                schema=schema,
                columns=[
                    ColumnInfo(
                        name=str(r.get("column_name", "")),
                        data_type=str(r.get("column_type", "unknown")),
                        nullable=str(r.get("null", "YES")).upper() == "YES",
                        is_primary_key=False,
                        comment="",
                    )
                    for r in rows
                ],
            )
        catalog_name = ds.gravitino_catalog_name or ds.api_name

        columns: list[ColumnInfo] = []
        table_comment = ""

        # ── Preferred path: Gravitino REST (JDBC sources only) ──
        # _JDBC_CONNECTOR_MAP 列出的 connector_type 都是 Gravitino JDBC catalog
        # 纳管的，REST 能拿到原始大小写列名 + PK + 表注释（一次请求）。
        is_gravitino_jdbc = self._JDBC_CONNECTOR_MAP.get(ds.connector_type.lower()) is not None
        if is_gravitino_jdbc:
            try:
                meta = await self.catalog.get_table_metadata(catalog_name, database, table)
                pk_names = self._extract_pk_column_names(meta.get("indexes", []))
                columns = [
                    ColumnInfo(
                        name=str(col.get("name", "")),
                        data_type=GravitinoRegistry._format_gravitino_column_type(col.get("type", "unknown")),
                        nullable=bool(col.get("nullable", True)),
                        is_primary_key=str(col.get("name", "")) in pk_names,
                        comment=str(col.get("comment", "")),
                    )
                    for col in meta.get("columns", [])
                ]
                table_comment = meta.get("comment", "")
            except DataSourceUnreachableError as exc:
                # 数据源本身连不上——Trino fallback 同样要连该数据源，必失败，
                # 直接报可读错误，避免静默返回空 columns。
                raise self._enrich_unreachable(exc, ds) from exc
            except NotFoundError:
                # 表在 Gravitino 元数据里不存在——不降级到 Trino（Trino 也查不到），
                # 直接返回空 columns 让上层按 404 处理。
                return TableInfo(name=table, schema=database, columns=[], comment="")
            except Exception as exc:
                # Gravitino REST 其他失败（如服务不可用）→ 降级到 Trino DESCRIBE
                _log.warning(
                    "Gravitino REST get_table_metadata failed for %s.%s.%s, falling back to Trino DESCRIBE: %s",
                    catalog_name,
                    database,
                    table,
                    exc,
                )

        # ── Fallback: Trino DESCRIBE (non-JDBC sources, or REST failure) ──
        if not columns:
            try:
                cols_raw = await self.engine.describe_table(catalog_name, database, table)
                columns = [
                    ColumnInfo(
                        name=str(
                            c.get(
                                "Column",
                                c.get("COLUMN_NAME", c.get("Field", c.get("column_name", c.get("name", "")))),
                            )
                        ),
                        data_type=str(
                            c.get(
                                "Type",
                                c.get("TYPE_NAME", c.get("DATA_TYPE", c.get("type", c.get("data_type", "")))),
                            )
                        ),
                        nullable=c.get("Null", c.get("IS_NULLABLE", c.get("nullable", "YES")))
                        in (
                            "YES",
                            "true",
                            True,
                            1,
                            "1",
                        ),
                        is_primary_key="PRI" in str(c.get("Key", c.get("COLUMN_KEY", ""))),
                        comment=str(
                            c.get(
                                "Comment",
                                c.get(
                                    "Extra",
                                    c.get(
                                        "COMMENT",
                                        c.get("REMARKS", c.get("remarks", c.get("comment", ""))),
                                    ),
                                ),
                            )
                        ),
                    )
                    for c in cols_raw
                ]
            except DataSourceUnreachableError as exc:
                raise self._enrich_unreachable(exc, ds) from exc
            except Exception as exc:
                _log.warning(
                    "Trino DESCRIBE also failed for %s.%s.%s: %s",
                    catalog_name,
                    database,
                    table,
                    exc,
                )

            # Trino path 不返回表注释，best-effort 单独取一次（与旧行为一致）
            if not table_comment:
                try:
                    table_comment = await self.catalog.get_table_comment(catalog_name, database, table)
                except Exception:
                    pass

        return TableInfo(name=table, schema=database, columns=columns, comment=table_comment)

    @staticmethod
    def _extract_pk_column_names(indexes: list[dict[str, Any]]) -> set[str]:
        """Extract primary-key column names from a Gravitino ``indexes`` list.

        Gravitino represents PK as an index entry with ``indexType ==
        "PRIMARY_KEY"`` and ``fieldNames`` being a list of column-name
        paths, e.g. ``[["modelId"]]`` for a single-column PK or
        ``[["a"], ["b"]]`` for a composite PK. We flatten to a set of
        leaf names for O(1) membership lookup during ColumnInfo build.
        """
        pk_names: set[str] = set()
        for idx in indexes:
            if str(idx.get("indexType", "")).upper() != "PRIMARY_KEY":
                continue
            field_names = idx.get("fieldNames", [])
            if not isinstance(field_names, list):
                continue
            for path in field_names:
                # path is a list[str] (column-name segments); take the leaf
                if isinstance(path, list) and path:
                    pk_names.add(str(path[-1]))
                elif isinstance(path, str):
                    pk_names.add(path)
        return pk_names

    async def sample_data(self, api_name: str, database: str, table: str, limit: int = 10) -> list[dict[str, Any]]:
        """Sample rows from a data source table via Trino.

        Data always comes from Trino (Gravitino has no row-data API). Trino
        normalizes column identifiers to lowercase, so the returned dict
        keys are lowercased (``modelId`` → ``modelid``). The frontend
        ``PreviewTable`` joins row data with column metadata using the
        lowercased name as key while displaying the original casing from
        ``describe_table`` (Gravitino REST) in the header — no key
        remapping needed here.

        Tries ``SELECT *`` first. If that fails because the table contains
        a column whose type Gravitino maps to ``external(...)`` (e.g.
        PostgreSQL ``jsonb`` / ``uuid`` / ``inet``), retries with an
        explicit column list excluding those unresolvable types — so the
        user still sees the bulk of the data rather than a hard 500.
        """
        import logging

        _log = logging.getLogger(__name__)
        ds = await self.metadata.get_datasource(api_name)
        if settings.edition == "lite":
            # B4: lite 版经 DuckDB SELECT * ... LIMIT（duckdb_engine.sample_data）。
            # DuckDB 原生支持各源类型，无 external(...) 不可解析列问题，直接 SELECT *。
            catalog_name, schema = await self._lite_catalog_and_schema(ds, database)
            try:
                return await self.engine.sample_data(catalog_name, schema, table, limit)
            except DataSourceUnreachableError as exc:
                raise self._enrich_unreachable(exc, ds) from exc
        catalog_name = ds.gravitino_catalog_name or ds.api_name
        try:
            return await self.engine.sample_data(catalog_name, database, table, limit)
        except DataSourceUnreachableError as exc:
            raise self._enrich_unreachable(exc, ds) from exc
        except Exception as exc:
            # Gravitino connector 把 PG jsonb/uuid/inet 等映射为 external(...)
            # 类型，SELECT * 会报 GRAVITINO_UNSUPPORTED_GRAVITINO_DATATYPE。
            # 退化为只查可读列。
            msg = str(exc)
            if "UNSUPPORTED_GRAVITINO_DATATYPE" not in msg and "external(" not in msg:
                raise
            _log.info(
                "SELECT * on %s.%s.%s hit unsupported datatype; retrying with readable columns only",
                catalog_name,
                database,
                table,
            )
            # Trino DESCRIBE 同样会因 external(...) 类型失败，必须走 Gravitino
            # REST API（它能识别 external 类型并返回结构化 type 信息）。
            try:
                grav_cols = await self.catalog.get_table_columns(catalog_name, database, table)
            except Exception:
                raise
            # 只选类型不为 external(...) 的列；保留原顺序
            readable: list[str] = []
            for col in grav_cols:
                col_name = str(col.get("name", ""))
                if not col_name:
                    continue
                col_type = GravitinoRegistry._format_gravitino_column_type(col.get("type", ""))
                if col_type.startswith("external(") or col_type == "external":
                    continue
                readable.append(col_name)
            if not readable:
                raise
            try:
                return await self.engine.sample_data_columns(catalog_name, database, table, readable, limit)
            except Exception:
                # Trino-Gravitino connector 在 query planning 阶段就会校验
                # 整表 schema，即使只选安全列也会因 external(jsonb) 失败。
                # 这种情况下无法通过 Trino 预览数据，报可读错误。
                raise ValidationError(
                    "该表含有 Gravitino 无法解析的列类型（如 jsonb/uuid/inet），"
                    "暂时无法预览数据。可先创建同步任务将数据落入 Iceberg 后再查看。"
                )

    # Sync Task Management
    # ═════════════════════════════════════════════════════════════

    async def create_sync_task(self, task: SyncTaskCreate) -> SyncTask:
        """Create a sync task and deploy to SeaTunnel.

        1. PG: Store SyncTask record
        2. Gravitino: create/reconcile the managed Iceberg table with full
           physical metadata (Catalog First — primary key, comments, NULL
           constraints registered into Iceberg, not duplicated in PG).
           Best-effort: a failure is logged but does not block task creation;
           SeaTunnel's sink (schema_save_mode=IGNORE) will surface a clear
           error if the table is missing when the job runs.
        3. SeaTunnel: Generate and submit pipeline configuration (best-effort)
        4. PG: Record pipeline_name if SeaTunnel succeeded

        SeaTunnel deployment is best-effort during creation — if it fails,
        the task is saved with pipeline_name=null and the pipeline is
        re-submitted on start_sync(). Creation must not hard-fail just
        because SeaTunnel is transiently unavailable.
        """
        import logging

        _log = logging.getLogger(__name__)

        # Step 1: PG
        record = await self.metadata.create_sync_task(task)

        # Step 2: Gravitino managed-table provisioning (Catalog First)
        try:
            await self._provision_managed_table_for_sync(record)
        except Exception as exc:
            _log.warning(
                "Managed table provisioning deferred for sync task '%s': %s",
                record.api_name,
                exc,
            )

        # Step 3: Generate SeaTunnel pipeline (best-effort)
        try:
            pipeline_name = await self._submit_sync_pipeline(record)
            # Step 4: Update record with pipeline info
            await self.metadata.update_sync_task(
                record.api_name,
                {"pipeline_name": pipeline_name, "status": "DRAFT"},
            )
        except Exception as exc:
            _log.warning(
                "SeaTunnel pipeline creation deferred for sync task '%s': %s",
                record.api_name,
                exc,
            )

        # Refresh and return latest state
        return await self.metadata.get_sync_task(record.api_name)

    async def _provision_managed_table_for_sync(self, task: SyncTask) -> None:
        """Create/reconcile the managed Iceberg table for a sync task.

        Catalog First: Gaia registers the managed Iceberg table via the
        Gravitino/Iceberg catalog with full physical metadata (primary-key
        identifier, column comments, NOT-NULL), so SeaTunnel only writes
        data (schema_save_mode=IGNORE). The table's provenance is recorded
        as Iceberg table properties (``gaia.source-datasource`` /
        ``gaia.source-table`` / ``comment``).

        Pulls the source table's schema via :meth:`describe_table` (which
        already returns per-column ``is_primary_key``/``comment``/``nullable``
        + table ``comment``), converts to :class:`ManagedTableSchema`, and
        delegates to :meth:`IcebergStore.create_managed_table`. An existing
        table is reconciled (additive schema evolution) — never dropped.
        """
        import logging

        from ontology.core.naming import _to_snake
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        _log = logging.getLogger(__name__)

        ds = await self.metadata.get_datasource_by_id(task.data_source_id)
        source_table = task.source_config.get("table")
        if not source_table:
            _log.warning(
                "provision_managed_table: sync task '%s' has no source table in source_config; skipping",
                task.api_name,
            )
            return

        # database may be absent in source_config — describe_table resolves
        # the datasource default when given "". Some sources store the schema
        # under "schema" (PG) and others under "database" (MySQL); accept both.
        database = str(task.source_config.get("database") or task.source_config.get("schema") or "")
        try:
            info = await self.describe_table(ds.api_name, database, source_table)
        except Exception as exc:
            _log.warning(
                "provision_managed_table: describe_table('%s','%s','%s') failed: %s — skipping table creation",
                ds.api_name,
                database,
                source_table,
                exc,
            )
            return

        # Iceberg table name == snake_case(dataset api_name), matching the
        # SeaTunnel sink's table naming (see _build_sync_pipeline).
        iceberg_table = _to_snake(task.target_dataset_api_name.split(".")[-1])

        schema = ManagedTableSchema(
            columns=[
                ManagedColumnDef(
                    name=c.name,
                    type=c.data_type,
                    nullable=c.nullable,
                    comment=c.comment,
                    is_primary_key=c.is_primary_key,
                )
                for c in info.columns
            ],
            table_comment=info.comment or "",
        )
        properties = {
            "gaia.source-datasource": ds.api_name,
            "gaia.source-table": source_table,
        }
        await self.dataset.create_managed_table(iceberg_table, schema, properties=properties)

    async def _submit_sync_pipeline(self, task: SyncTask) -> str:
        """Render and submit the SeaTunnel MAIN pipeline for a sync task.

        Centralizes source-config assembly so create_sync_task (initial
        deploy) and start_sync (re-deploy) produce identical configs.

        Returns:
            The pipeline_name (== SeaTunnel jobName) that was submitted.

        Raises:
            OntologyError: if SeaTunnel rejects the job (caller decides
                whether to swallow, as in create_sync_task, or surface,
                as in start_sync).
        """
        ds = await self.metadata.get_datasource_by_id(task.data_source_id)
        target_table = task.target_dataset_api_name

        # SeaTunnel's Iceberg sink expects the target namespace to exist;
        # its CREATE_SCHEMA_WHEN_NOT_EXIST save mode is unreliable against
        # the Gravitino Iceberg REST server. Create it up-front so submit
        # doesn't fail with NoSuchNamespaceException.
        try:
            await self.dataset.ensure_namespace("ontology")
        except Exception:
            # ensure_namespace is already best-effort internally; never
            # let namespace pre-creation block the submit.
            pass

        # Catalog First: the managed Iceberg table is created/reconciled by
        # Gaia (create_sync_task → _provision_managed_table_for_sync) with
        # full physical metadata (PK, comments, NULL). SeaTunnel only writes
        # data (schema_save_mode=IGNORE). We must NOT drop the table here —
        # dropping would discard both the registered schema and the table's
        # snapshot history. full_snapshot semantics are achieved via SeaTunnel's
        # overwrite data-save mode, not drop+recreate.

        jdbc_config = self._build_jdbc_url(ds.connector_type, ds.connector_config)
        driver = self._resolve_driver(ds.connector_type, ds.connector_config)

        # Resolve credentials from the linked Credential record — the
        # DataSource.connector_config intentionally stores no secrets; they
        # live in Credential.secret_data. SeaTunnel's Jdbc source needs
        # explicit user/password, so inject them here.
        cred_username, cred_password = await self._resolve_credentials(ds)

        source_table = task.source_config.get("table", "")
        # Source query: let SeaTunnel use its default ``SELECT * FROM <table>``
        # (rendered by the HOCON template when ``query`` is None). PG expands
        # ``*`` using the real column names internally, so mixed-case column
        # names (e.g. camelCase ``opId``) are read correctly without quoting —
        # unlike a hand-written ``SELECT opId, ...`` which PG would fold to
        # lowercase. SeaTunnel 2.3.13 handles all common PG types natively
        # (text/bigint/boolean/numeric, timestamp NTZ, jsonb/json, uuid, inet,
        # array, bytea, interval — all written to Iceberg as strings via JDBC
        # ResultSet). The ONLY unsupported type is ``timestamptz`` (Jackson
        # OffsetDateTime serialization fails with missing jsr310 module);
        # sources must use NTZ ``timestamp`` instead. Full type matrix + live
        # test details: seatunnel-sync-safe-query-unquoted-identifier.md §5.3.
        safe_query: str | None = None

        # IngestionFilter — feedback-loop prevention for incremental syncs.
        # full_snapshot syncs re-read the whole source so filtering is moot;
        # incremental syncs exclude rows Gaia wrote back (tagged with
        # gaia_sync_tx) so they aren't re-ingested. last_sync_tx is carried
        # in source_config by the caller / future scheduler.
        if task.sync_mode == "incremental":
            last_sync_tx = task.source_config.get("last_sync_tx")
            base_sql = f"SELECT * FROM {source_table}"
            safe_query = self._ingestion_filter.rewrite_incremental_query(base_sql, last_sync_tx)

        source_config_full = {
            "driver": driver,
            "url": self._rewrite_source_host_for_seatunnel(jdbc_config, ds.connector_type),
            "user": cred_username or ds.connector_config.get("username", ""),
            "password": cred_password or ds.connector_config.get("password", ""),
            "table": source_table,
            "query": safe_query,
            "connector_type": ds.connector_type,
        }

        # full_snapshot syncs are finite — run as BATCH so the job
        # terminates after the source is exhausted. incremental syncs
        # would use STREAMING, but MAIN pipelines today are all
        # full_snapshot (sync_mode is stored on the task but the MAIN
        # template is shared; BATCH is the safe default for one-shot).
        job_mode = "BATCH" if task.sync_mode == "full_snapshot" else "STREAMING"

        pipeline_def = await self.pipeline.create_sync_pipeline(
            connector_type=ds.connector_type,
            source_config=source_config_full,
            target_dataset=target_table,
            transforms=[],
            job_mode=job_mode,
            transaction_type=task.transaction_type,
        )
        return pipeline_def.name

    async def get_sync_task(self, api_name: str) -> SyncTask:
        """Get a sync task."""
        return await self.metadata.get_sync_task(api_name)

    async def list_sync_tasks(self, datasource_api_name: str) -> list[SyncTask]:
        """List all sync tasks for a data source."""
        ds = await self.metadata.get_datasource(datasource_api_name)
        return await self.metadata.list_sync_tasks_for_datasource(ds.id)

    async def start_sync(self, api_name: str) -> SyncTask:
        """Start a sync task by (re-)submitting its SeaTunnel pipeline.

        This is the ONLY place a job is actually submitted to SeaTunnel for
        a full-snapshot sync task. The lifecycle is:

          1. If a job with this name is already RUNNING in SeaTunnel, do
             nothing — return its real status.
          2. Otherwise render the config from the stored DataSource +
             SyncTask and submit it. If SeaTunnel rejects the config,
             persist ``status=FAILED`` (with last_run_at) and re-raise so
             the API returns a 500 with a readable message — never mark
             the task RUNNING without a real job behind it.
          3. On success, persist ``status=RUNNING`` + last_run_at.

        ``pipeline_name`` is populated lazily here when it was missing
        (e.g. SeaTunnel was down at create_sync_task time).
        """
        task = await self.metadata.get_sync_task(api_name)

        # If we already have a pipeline name and it's running, short-circuit.
        if task.pipeline_name:
            try:
                current = await self.pipeline.get_job_status(task.pipeline_name)
            except Exception:
                # SeaTunnel unreachable — fall through to re-submit path,
                # which will surface a concrete error.
                current = None
            if current is not None and current.state == "RUNNING":
                return await self.metadata.update_sync_task(
                    api_name,
                    {"status": "RUNNING", "last_run_at": utcnow()},
                )

        # (Re-)submit. This also establishes pipeline_name if missing.
        try:
            pipeline_name = await self._submit_sync_pipeline(task)
        except OntologyError:
            await self.metadata.update_sync_task(
                api_name,
                {"status": "FAILED", "last_run_at": utcnow()},
            )
            raise

        # Reconcile with SeaTunnel's true state after submission — the job
        # may have run and already terminated (FINISHED/FINISHED within a
        # few seconds for small tables).  Blindly setting RUNNING here masks
        # terminal states from the UI until the next manual refresh.
        updates: dict[str, Any] = {
            "pipeline_name": pipeline_name,
            "last_run_at": utcnow(),
        }
        try:
            real = await self.pipeline.get_job_status(pipeline_name)
            mapped = _map_seatunnel_state(real.state)
            if mapped != "_KEEP_":
                updates["status"] = mapped
            else:
                updates["status"] = "RUNNING"
        except Exception:
            # SeaTunnel unreachable — fall back to RUNNING as best-effort.
            updates["status"] = "RUNNING"

        return await self.metadata.update_sync_task(api_name, updates)

    async def start_cdc_sync(
        self,
        datasource_api_name: str,
        source_table: str,
        target_dataset_api_name: str,
        cdc_config: dict[str, Any],
        primary_keys: list[str] | None = None,
        task_api_name: str | None = None,
    ) -> SyncTask:
        """Start an external-source CDC → Iceberg sync (§7.3, post-spike).

        Creates a SyncTask with ``sync_mode="cdc"`` and submits the
        external CDC pipeline to SeaTunnel. Distinguished from
        ``start_sync`` (batch): the source is an external business DB
        (MySQL/PG/OpenGauss/TiDB) and the pipeline is STREAMING.

        Requires the spike (§7.3) to have validated the CDC → Iceberg →
        Doris path. ``primary_keys`` is strongly recommended to avoid
        append-only CDC data loss (SeaTunnel #10747).

        Args:
            datasource_api_name: DataSource api_name (CDC source).
            source_table: Source table name (``db.table``).
            target_dataset_api_name: Target Iceberg dataset api_name.
            cdc_config: CDC source config (cdc_connector, hostname, port,
                username, password, database_name, table_name, ...).
            primary_keys: PK columns for upsert (avoids #10747).
            task_api_name: Optional SyncTask api_name (defaults derived).
        """
        import logging

        _log = logging.getLogger(__name__)

        ds = await self.metadata.get_datasource(datasource_api_name)
        source_config = dict(cdc_config)
        source_config["table_name"] = source_table.split(".")[-1]
        source_config.setdefault("database_name", source_table.split(".")[0] if "." in source_table else "")
        if primary_keys:
            source_config["primary_keys"] = primary_keys

        # Build & submit the external CDC pipeline (STREAMING).
        from ontology.core.naming import _to_snake

        bare_table = _to_snake(target_dataset_api_name.split(".")[-1])
        try:
            await self.pipeline.create_external_cdc_pipeline(
                source_config=source_config,
                target_dataset=bare_table,
            )
        except Exception as exc:
            _log.error("External CDC pipeline submit failed for '%s': %s", datasource_api_name, exc)
            raise

        pipeline_name = f"ext_cdc_{bare_table}"
        task_name = task_api_name or f"cdc{bare_table.replace('_', '')}"
        await self.metadata.create_sync_task(
            SyncTaskCreate(
                api_name=task_name,
                data_source_id=ds.id,
                sync_type="table",
                source_config={**source_config, "sync_mode": "cdc"},
                target_dataset_api_name=target_dataset_api_name,
                sync_mode="incremental",
                transaction_type="append",
            )
        )
        return await self.metadata.update_sync_task(
            task_name,
            {"pipeline_name": pipeline_name, "status": "RUNNING", "last_run_at": utcnow()},
        )

    async def start_timeseries_sync(
        self,
        datasource_api_name: str,
        kafka_topic: str,
        target_hypertable: str,
        schema_fields: dict[str, str],
        primary_keys: list[str] | None = None,
        consumer_group: str | None = None,
        task_api_name: str | None = None,
    ) -> SyncTask:
        """Start a Kafka → TimescaleDB hypertable streaming sync (§5.3, C3).

        动态时序数据走流式独立链路（不经 Iceberg/Action/object_state）。
        超表必须由 GeoTimeStore.create_timeseries_hypertable 预建。创建
        SyncTask（sync_mode=incremental, transaction_type=append）并提交
        Kafka→TimescaleDB pipeline 到 SeaTunnel。

        Args:
            datasource_api_name: Kafka DataSource api_name。
            kafka_topic: Kafka topic 名。
            target_hypertable: TimescaleDB 超表名（naming.timeseries_hypertable）。
            schema_fields: 列名→类型映射（对齐超表列，如
                {"series_id":"string","timestamp":"timestamp","speed":"double"}）。
            primary_keys: 超表标识列（通常 series_id+timestamp）。
            consumer_group: Kafka 消费组（默认 gaia_timeseries_ingest）。
            task_api_name: SyncTask api_name。
        """
        import logging

        _log = logging.getLogger(__name__)

        ds = await self.metadata.get_datasource(datasource_api_name)
        source_config: dict[str, Any] = {
            "topic": kafka_topic,
            "bootstrap_servers": settings.seatunnel_kafka_bootstrap_servers,
            "format": "json",
            "schema_fields": schema_fields,
        }
        if consumer_group:
            source_config["consumer_group"] = consumer_group
        if primary_keys:
            source_config["primary_keys"] = primary_keys

        try:
            await self.pipeline.create_kafka_timeseries_pipeline(
                source_config=source_config,
                target_hypertable=target_hypertable,
            )
        except Exception as exc:
            _log.error("Kafka→TimescaleDB pipeline submit failed: %s", exc)
            raise

        pipeline_name = f"kafka_ts_{target_hypertable}"
        task_name = task_api_name or f"ts{target_hypertable.replace('_', '')}"

        await self.metadata.create_sync_task(
            SyncTaskCreate(
                api_name=task_name,
                data_source_id=ds.id,
                sync_type="table",
                source_config={**source_config, "sync_mode": "timeseries"},
                target_dataset_api_name=target_hypertable,
                sync_mode="incremental",
                transaction_type="append",
            )
        )
        return await self.metadata.update_sync_task(
            task_name,
            {"pipeline_name": pipeline_name, "status": "RUNNING", "last_run_at": utcnow()},
        )

    async def stop_sync(self, api_name: str) -> SyncTask:
        """Stop a sync task by cancelling its SeaTunnel job.

        Records the real SeaTunnel state after cancel. If the job is
        unknown to SeaTunnel (never ran / aged out), we still mark the
        task STOPPED locally — the desired end state holds.
        """
        task = await self.metadata.get_sync_task(api_name)
        if task.pipeline_name:
            try:
                await self.pipeline.stop(task.pipeline_name)
            except OntologyError:
                # SeaTunnel unreachable; still mark local state as STOPPED
                # so the UI reflects user intent.
                pass
        return await self.metadata.update_sync_task(api_name, {"status": "STOPPED"})

    async def refresh_sync_status(self, api_name: str) -> SyncTask:
        """Reconcile a SyncTask's stored status with SeaTunnel's truth.

        Polls SeaTunnel for the job's real state and persists it back to
        the SyncTask row. Used by the UI to show "已完成 / 失败 / 运行中"
        based on what SeaTunnel actually reports, instead of the stale
        value written at start_sync time. Terminal states (FINISHED /
        CANCELED / FAILED) update ``last_run_at`` to the job's finish
        time when available so the UI's "上次" timestamp is meaningful.

        If the task has no pipeline_name or SeaTunnel is unreachable,
        the row is left unchanged and the current (possibly stale) record
        is returned — refresh is best-effort by design.
        """
        task = await self.metadata.get_sync_task(api_name)
        if not task.pipeline_name:
            return task

        try:
            status = await self.pipeline.get_job_status(task.pipeline_name)
        except Exception:
            # SeaTunnel unreachable — leave stored state untouched.
            return task

        mapped = _map_seatunnel_state(status.state)
        if mapped == "_KEEP_":
            # UNKNOWN (aged out of history, etc.) — don't overwrite a
            # legitimate stored status with guesswork.
            return task

        updates: dict[str, Any] = {"status": mapped}
        if status.state in {"FINISHED", "CANCELED", "FAILED"}:
            updates["last_run_at"] = utcnow()
        return await self.metadata.update_sync_task(api_name, updates)

    async def refresh_all_sync_status(self, datasource_api_name: str) -> list[SyncTask]:
        """Batch-reconcile all of a datasource's sync tasks in **2 SeaTunnel calls**.

        Replaces the N+1 anti-pattern where the UI calls
        ``refresh_sync_status`` once per task (each call re-fetching the
        full SeaTunnel running + finished lists). This method fetches both
        lists once via :meth:`get_jobs_status_batch` and updates every task
        in a single pass.

        Semantics match :meth:`refresh_sync_status`:
          - Terminal SeaTunnel states (FINISHED/CANCELED/FAILED) are
            persisted with ``last_run_at=utcnow()``.
          - UNKNOWN (aged out of history) leaves the stored status
            untouched — best-effort by design.
          - Tasks with no ``pipeline_name`` are skipped (nothing to reconcile).
          - SeaTunnel being unreachable leaves all rows untouched (the PG-
            stored status is returned as-is).

        Args:
            datasource_api_name: data source whose sync tasks to reconcile.

        Returns:
            The refreshed tasks (PG-truth after reconcile), in the same
            order :meth:`list_sync_tasks` returns them.
        """
        ds = await self.metadata.get_datasource(datasource_api_name)
        tasks = await self.metadata.list_sync_tasks_for_datasource(ds.id)
        if not tasks:
            return []

        # Only tasks with a pipeline_name have anything to reconcile.
        to_reconcile = {t.pipeline_name: t for t in tasks if t.pipeline_name}
        if not to_reconcile:
            return tasks

        statuses: dict[str, PipelineStatus] = {}
        try:
            statuses = await self.pipeline.get_jobs_status_batch(set(to_reconcile.keys()))
        except Exception:
            # SeaTunnel unreachable — return PG-stored tasks untouched.
            return tasks

        for pipeline_name, task in to_reconcile.items():
            status = statuses.get(pipeline_name)
            if status is None:
                continue
            mapped = _map_seatunnel_state(status.state)
            if mapped == "_KEEP_":
                # UNKNOWN — don't overwrite a legitimate stored status.
                continue
            updates: dict[str, Any] = {"status": mapped}
            if status.state in {"FINISHED", "CANCELED", "FAILED"}:
                updates["last_run_at"] = utcnow()
            try:
                await self.metadata.update_sync_task(task.api_name, updates)
            except Exception:
                # Per-task update failure must not abort the whole batch.
                continue

        # Return the fresh PG-truth for every task (including the ones we
        # just updated and the ones we skipped).
        return await self.metadata.list_sync_tasks_for_datasource(ds.id)

    async def delete_sync_task(self, api_name: str) -> None:
        """Delete a sync task. Stop pipeline first.

        Also removes the associated dataset governance record to
        prevent orphaned datasets from accumulating.

        Transaction: Both sync_task deletion and dataset deletion
        happen in a single PG transaction via auto_commit=False.
        """
        import logging

        _log = logging.getLogger(__name__)
        task = await self.metadata.get_sync_task(api_name)
        if task.pipeline_name:
            try:
                await self.pipeline.stop(task.pipeline_name)
            except Exception:
                pass

        # Delete sync_task and dataset in one transaction
        try:
            deleted_model = await self.metadata.delete_sync_task(api_name, auto_commit=False)
            target_dataset = deleted_model.target_dataset_api_name
            try:
                await self.metadata.delete_dataset(target_dataset, auto_commit=False)
            except Exception:
                pass  # dataset may not exist
            await self.metadata.commit_transaction()
        except Exception:
            await self.metadata.rollback_transaction()
            raise

    # ═════════════════════════════════════════════════════════════
    # Dataset Governance Metadata
    # ═════════════════════════════════════════════════════════════

    async def register_virtual_table(
        self,
        datasource_api_name: str,
        database: str,
        table: str,
        api_name: str | None = None,
        display_name: str = "",
    ) -> DatasetGovernance:
        """Register an external table as a kind=VIRTUAL dataset.

        Orchestration (per dataset-ontology-binding.md §3.2):
          1. Validate the data source exists.
          2. describe_table the external table to confirm it is reachable
             and to surface its columns. An empty/unreachable table is a
             422 (ValidationError).
          3. Build the three-part locator catalog.schema.table and persist
             a DatasetGovernance(kind=VIRTUAL) record. No physical table is
             created — Trino federates to the source at query time.
          4. api_name uniqueness collisions surface as ConflictError → 409
             (raised by metadata.create_dataset's idempotency check returns
             the existing record rather than 409; explicit 409 only fires
             when an explicit name was supplied and matched a different table).
        """
        ds = await self.metadata.get_datasource(datasource_api_name)
        if settings.edition == "lite":
            # B4: lite 版 catalog = src_<ds>（DuckDB ATTACH 别名，CSV 用 main）。
            ct = ds.connector_type.lower()
            catalog_name = "main" if ct in ("csv", "csv_file") else f"src_{ds.api_name.lower()}"
        else:
            catalog_name = ds.gravitino_catalog_name or ds.api_name

        # Confirm the external table is reachable and has columns.
        table_info = await self.describe_table(datasource_api_name, database, table)
        if not table_info.columns:
            raise ValidationError(f"External table {database}.{table} has no columns or is unreachable")

        # Virtual table api_name 兼任治理标识与 Trino 联邦查询的表名引用,需满足
        # dataset snake_case pattern。用户显式传入时直接用(前端应保证 snake_case);
        # 否则从外部表名归一化(Orders → orders, CustomerOrder → customer_order)。
        from ontology.core.naming import _to_snake

        final_api_name = api_name or _to_snake(table)
        locator = f"{catalog_name}.{database}.{table}"

        created = await self.metadata.create_dataset(
            DatasetGovernanceCreate(
                api_name=final_api_name,
                display_name=display_name or table,
                storage_location=locator,
                data_source_api_name=datasource_api_name,
                kind="VIRTUAL",
                is_view=False,
            )
        )
        # Auto-back-fill row_count_estimate via Trino federation. Best-effort:
        # the VIRTUAL dataset is usable without it; a stale/None count just
        # shows "—" until refreshed. run in the same call so the caller sees
        # an accurate count immediately after registration.
        await self.refresh_row_count(final_api_name)
        # ADR-021 §3.1: 异步触发 VIRTUAL 图投影（best-effort，不阻塞 register）。
        # 仅在该 dataset 已被某 VIRTUAL ObjectType 引用时才投影；首次 register
        # 通常尚未绑定 ObjectType，此任务会 no-op 跳过。用户后续在 admin rebuild
        # 或 ObjectType link_dataset 后触发首次投影。
        self._maybe_trigger_virtual_projection(final_api_name)
        return created

    def _maybe_trigger_virtual_projection(self, dataset_api_name: str) -> None:
        """ADR-021 §3.1: register_virtual_table 后异步触发 VIRTUAL 图投影。

        fire-and-forget：创建后台任务查绑定该 dataset 的 VIRTUAL ObjectType，
        有则逐个调 project_for_virtual_object_type。失败仅记日志（不阻塞
        register 调用方，符合 partial 降级语义）。

        首次 register 通常尚未绑定 ObjectType，此任务 no-op 跳过；真正首次投影
        由 ObjectType link_dataset 后的触发或 admin rebuild 完成。
        """
        if self._object_index_funnel is None:
            return

        _log = logging.getLogger(__name__)

        async def _run() -> None:
            try:
                ots = await self.metadata.get_virtual_object_types_by_dataset(dataset_api_name)
                if not ots:
                    return
                for ont_api, ot_api in ots:
                    try:
                        result = await self._object_index_funnel.project_for_virtual_object_type(  # type: ignore[union-attr]
                            ontology_api_name=ont_api,
                            object_type_api_name=ot_api,
                        )
                        _log.info(
                            "VIRTUAL 图投影完成 %s.%s: nodes=%s edges=%s cleaned=%s partial=%s",
                            ont_api,
                            ot_api,
                            result.get("nodes"),
                            result.get("edges"),
                            result.get("cleaned"),
                            result.get("partial"),
                        )
                    except Exception as exc:
                        _log.warning("VIRTUAL 图投影失败 %s.%s: %s", ont_api, ot_api, exc)
            except Exception as exc:
                _log.warning("VIRTUAL 图投影触发链路异常 %s: %s", dataset_api_name, exc)

        asyncio.create_task(_run())

    async def register_dataset(self, ds: DatasetGovernanceCreate) -> DatasetGovernance:
        """Register a dataset in PG governance metadata."""
        return await self.metadata.create_dataset(ds)

    async def refresh_row_count(self, dataset_api_name: str) -> int | None:
        """Refresh a dataset's row_count_estimate via Trino and persist it.

        Row count source (always Trino, never Doris — the dataset layer is
        Iceberg/Trino, Doris is only the index acceleration layer):
        - MANAGED: ``SELECT COUNT(*) FROM iceberg.<namespace>.<dataset_api_name>``
          (the Iceberg table backing the managed dataset).
        - VIRTUAL: ``SELECT COUNT(*) FROM <storage_location>`` where
          storage_location is the Trino three-part locator
          ``catalog.schema.table`` persisted at register_virtual_table time.

        Best-effort: a Trino timeout/error returns None without raising —
        the dataset is still usable, only its row count stays stale.

        Returns the refreshed row count, or None if it could not be determined.
        """
        import logging

        from ontology.config.settings import settings

        _log = logging.getLogger(__name__)
        try:
            ds = await self.metadata.get_dataset(dataset_api_name)
        except Exception as exc:
            _log.warning("refresh_row_count: dataset %s not found: %s", dataset_api_name, exc)
            return None

        if ds.kind == "VIRTUAL":
            if not ds.storage_location:
                _log.warning("refresh_row_count: VIRTUAL %s has no storage_location", dataset_api_name)
                return None
            # storage_location is the Trino three-part locator catalog.schema.table.
            sql = f"SELECT COUNT(*) AS c FROM {ds.storage_location}"
        else:
            # MANAGED → Iceberg table iceberg.<namespace>.<dataset_api_name>.
            sql = f"SELECT COUNT(*) AS c FROM iceberg.{settings.iceberg_namespace}.{dataset_api_name}"
        try:
            rows = await self.engine.query(sql)
            count = int(rows[0]["c"]) if rows else 0
        except Exception as exc:
            _log.warning("refresh_row_count: Trino query failed for %s: %s", dataset_api_name, exc)
            return None
        try:
            await self.metadata.update_dataset_stats(dataset_api_name, count)
        except Exception as exc:
            _log.warning("refresh_row_count: could not persist count for %s: %s", dataset_api_name, exc)
            return None
        _log.info("refresh_row_count: %s = %d rows", dataset_api_name, count)
        return count

    async def get_dataset(self, api_name: str) -> DatasetGovernance:
        """Get dataset governance metadata."""
        return await self.metadata.get_dataset(api_name)

    async def list_datasets(self) -> list[DatasetGovernance]:
        """List all datasets."""
        return await self.metadata.list_datasets()

    async def list_datasets_paginated(
        self,
        *,
        page: int,
        page_size: int,
        search: str = "",
        type_filter: str = "",
        ontology_api_name: str = "",
    ) -> tuple[list[DatasetGovernance], int]:
        """Paginated, filtered dataset list — delegates to metadata layer."""
        return await self.metadata.list_datasets_paginated(
            page=page,
            page_size=page_size,
            search=search,
            type_filter=type_filter,
            ontology_api_name=ontology_api_name,
        )

    async def get_dataset_ontology_map(self) -> dict[str, list[dict[str, str]]]:
        """Reverse-lookup: which ontologies reference each dataset.

        Delegates to PostgresMetaStore (single SQL over PropertyDef joined
        to ObjectType + Ontology). Used by the datasets list page to show
        "归属本体" and filter by ontology without N+1 ObjectType detail
        fetches on the client.

        Returns ``{dataset_api_name: [{ontology_id, ontology_api_name,
        object_type_api_name}, ...]}``.
        """
        return await self.metadata.get_dataset_ontology_map()

    async def update_dataset(self, api_name: str, updates: dict[str, Any]) -> DatasetGovernance:
        """Update dataset governance metadata fields."""
        return await self.metadata.update_dataset(api_name, updates)

    async def delete_dataset(self, api_name: str) -> None:
        """Delete dataset governance metadata from PG."""
        await self.metadata.delete_dataset(api_name)

    async def get_dataset_schema(self, api_name: str) -> DatasetSchema:
        """Get the physical schema for a dataset, dispatched by kind.

        - kind=MANAGED: reads the Iceberg table's columns via IcebergStore.
          If the Iceberg table doesn't exist yet (e.g. sync task has never
          run) returns an empty schema. Other failures (catalog unreachable,
          auth) propagate as ``IcebergUnavailableError`` so the UI can
          distinguish "no data" from "metadata service down".
        - kind=VIRTUAL: parses the three-part storage_location
          (catalog.schema.table) and federates to the external table via
          Gravitino to fetch its columns. Failures surface as
          ``GravitinoUnavailableError``.

        See dataset-ontology-binding.md §3.3.
        """
        import logging

        _log = logging.getLogger(__name__)
        ds = await self.metadata.get_dataset(api_name)

        if ds.kind == "MANAGED":
            try:
                return await self.dataset.get_schema(api_name)
            except NotFoundError:
                return DatasetSchema(columns=[])
            except Exception as exc:
                _log.warning(
                    "Iceberg metadata unavailable for '%s': %s",
                    api_name,
                    exc,
                )
                raise IcebergUnavailableError(f"Iceberg metadata unavailable for '{api_name}': {exc}") from exc

        if ds.kind == "VIRTUAL":
            # storage_location = "catalog.schema.table"
            parts = ds.storage_location.split(".")
            if len(parts) != 3:
                _log.warning(
                    "Virtual table '%s' has malformed storage_location '%s'; returning empty schema",
                    api_name,
                    ds.storage_location,
                )
                return DatasetSchema(columns=[])
            catalog, schema, table = parts
            # Prefer describe_table's two-tier resolution (Trino first, then
            # Gravitino REST) when we know the source datasource. This keeps
            # catalog-name resolution consistent with explore/describe_table:
            # Trino's Gravitino Connector and Gravitino REST may expose the
            # same source under different catalog names (e.g. 'pgnative' in
            # Trino vs 'pg' in Gravitino), so routing through the datasource
            # api_name avoids NoSuchCatalog errors from the storage_location
            # catalog segment. Fall back to the direct Gravitino REST call
            # using the storage_location locator when no datasource is linked.
            columns: list[ColumnDef] = []
            if ds.data_source_api_name:
                try:
                    table_info = await self.describe_table(ds.data_source_api_name, schema, table)
                    columns = [
                        ColumnDef(
                            name=c.name,
                            type=c.data_type or "unknown",
                            nullable=bool(c.nullable),
                        )
                        for c in table_info.columns
                    ]
                except NotFoundError:
                    return DatasetSchema(columns=[])
                except Exception as exc:
                    _log.warning(
                        "Schema unavailable for virtual table '%s' via datasource '%s': %s",
                        api_name,
                        ds.data_source_api_name,
                        exc,
                    )
                    raise GravitinoUnavailableError(f"Virtual table '{api_name}' schema unavailable: {exc}") from exc
            else:
                try:
                    grav_cols = await self.catalog.get_table_columns(catalog, schema, table)
                except NotFoundError:
                    return DatasetSchema(columns=[])
                except Exception as exc:
                    _log.warning(
                        "Gravitino schema unavailable for virtual table '%s': %s",
                        api_name,
                        exc,
                    )
                    raise GravitinoUnavailableError(f"Virtual table '{api_name}' schema unavailable: {exc}") from exc
                columns = [
                    ColumnDef(
                        name=str(col.get("name", "")),
                        type=GravitinoRegistry._format_gravitino_column_type(col.get("type", "unknown")),
                        nullable=bool(col.get("nullable", True)),
                    )
                    for col in grav_cols
                ]
            return DatasetSchema(columns=columns)

        # Defensive: unknown kind → empty schema rather than crashing.
        return DatasetSchema(columns=[])

    async def get_dataset_snapshots(self, api_name: str) -> list[DatasetSnapshot]:
        """Get the Iceberg snapshot history for a dataset.

        Returns all snapshots in reverse chronological order.

        If the Iceberg table doesn't exist yet, returns empty list.
        Other failures propagate as ``IcebergUnavailableError``.
        """
        import logging

        _log = logging.getLogger(__name__)
        try:
            return await self.dataset.get_snapshots(api_name)
        except NotFoundError:
            return []
        except Exception as exc:
            _log.warning(
                "Iceberg metadata unavailable for '%s': %s",
                api_name,
                exc,
            )
            raise IcebergUnavailableError(f"Iceberg metadata unavailable for '{api_name}': {exc}") from exc

    # ═════════════════════════════════════════════════════════════
    # Impact Analysis
    # ═════════════════════════════════════════════════════════════

    async def analyze_impact(self, request: ImpactAnalysisRequest) -> ImpactAnalysis:
        """Analyze the impact of a destructive operation.

        Used by the frontend to determine the confirmation level
        (LOW→dialog, MEDIUM→list impacts, HIGH→type name).
        """
        impacts: list[ImpactItem] = []

        if request.target_type == "datasource" and request.action == "delete":
            ds = await self.metadata.get_datasource(request.target_api_name)
            tasks = await self.metadata.list_sync_tasks_for_datasource(ds.id)
            for t in tasks:
                impacts.append(
                    ImpactItem(
                        resource_type="sync_task",
                        api_name=t.api_name,
                        effect="CASCADE_DELETE",
                    )
                )

        elif request.target_type == "dataset" and request.action == "delete":
            try:
                object_types = await self.metadata.get_object_types_for_dataset(request.target_api_name)
            except Exception:
                object_types = []
            for ot in object_types:
                impacts.append(
                    ImpactItem(
                        resource_type="object_type",
                        api_name=ot.api_name,
                        effect="ORPHANED",
                    )
                )

        elif request.target_type == "sync_task" and request.action == "delete":
            impacts.append(
                ImpactItem(
                    resource_type="sync_task",
                    api_name=request.target_api_name,
                    effect="CASCADE_DELETE",
                )
            )

        # Determine severity
        if not impacts:
            severity = "LOW"
        elif len(impacts) <= 3 and all(i.effect != "ORPHANED" for i in impacts):
            severity = "MEDIUM"
        else:
            severity = "HIGH"

        return ImpactAnalysis(
            severity=severity,
            action=request.action,
            target_api_name=request.target_api_name,
            target_type=request.target_type,
            impacts=impacts,
        )

    # ═════════════════════════════════════════════════════════════
    # Helpers
    # ═════════════════════════════════════════════════════════════

    # Gravitino provider 映射。None 表示该品类无 Gravitino provider——
    # 仅支持 SeaTunnel 落地（MANAGED），不走 VIRTUAL 联邦。
    # 国产库 PG/MySQL 兼容的走对应 provider（§6.1.3）。
    _JDBC_CONNECTOR_MAP: dict[str, str | None] = {
        "mysql": "jdbc-mysql",
        "mariadb": "jdbc-mysql",
        "postgresql": "jdbc-postgresql",
        "postgres": "jdbc-postgresql",
        # 国产库：PG/MySQL 兼容的走对应 provider
        "opengauss": "jdbc-postgresql",  # Gravitino 用 PG provider
        "gaussdb": "jdbc-postgresql",
        "gaussdb_dws": "jdbc-postgresql",  # PG 内核云数仓
        "analyticdb_pg": "jdbc-postgresql",  # PG 内核云数仓
        "kingbase": "jdbc-postgresql",
        "tidb": "jdbc-mysql",  # MySQL 协议
        "oceanbase": "jdbc-mysql",  # MySQL 模式
        "starrocks": "jdbc-starrocks",  # Gravitino 原生 starrocks provider
        # 达梦无 Gravitino provider，仅 MANAGED 落地
        "dameng": None,
        # 通用 JDBC 兜底（任意 JDBC 兼容库）—— 无 Gravitino catalog
        "generic_jdbc": None,
    }

    # JDBC driver 类名。国产库用独立类名驱动包，避免与官方 PG/MySQL 驱动
    # 同名类冲突（§6.1.2 避坑：openGauss/GaussDB 的 gsjdbc4.jar 内含完整
    # org.postgresql.Driver.class，与 postgresql.jar 同名注册导致
    # "Protocol error. Session setup failed"）。
    _JDBC_DRIVER_MAP: dict[str, str] = {
        "mysql": "com.mysql.cj.jdbc.Driver",
        "mariadb": "com.mysql.cj.jdbc.Driver",
        "postgresql": "org.postgresql.Driver",
        "postgres": "org.postgresql.Driver",
        # 国产库：独立类名驱动
        "opengauss": "com.huawei.opengauss.jdbc.Driver",
        "gaussdb": "com.huawei.opengauss.jdbc.Driver",  # opengaussjdbc 统一驱动（gsjdbc200 不在公网）
        "gaussdb_dws": "com.huawei.opengauss.jdbc.Driver",  # 同上
        "kingbase": "com.kingbase8.Driver",
        "tidb": "com.mysql.cj.jdbc.Driver",  # MySQL 协议
        "oceanbase": "com.oceanbase.jdbc.Driver",
        "starrocks": "com.mysql.cj.jdbc.Driver",  # MySQL 协议
        "dameng": "dm.jdbc.driver.DmDriver",
        # 通用 JDBC 兜底：driver 由用户在 connector_config.driver 指定
        "generic_jdbc": "",
        # 云数仓（PG 内核，复用标准 PG 驱动；GaussDB-DWS 用独立类名避冲突）
        "analyticdb_pg": "org.postgresql.Driver",
    }

    # JDBC URL scheme。国产库需独立 URL scheme（§6.1.3）。
    # 未列出的 connector_type 退化为 type_lower 本身（mysql/postgresql 等）。
    _JDBC_URL_SCHEME: dict[str, str] = {
        "opengauss": "opengauss",
        "gaussdb": "gaussdb",
        "gaussdb_dws": "gaussdb",
        "kingbase": "kingbase8",
        "tidb": "mysql",  # MySQL 协议
        "oceanbase": "oceanbase",
        "starrocks": "mysql",  # MySQL 协议（FE 9030 兼容 MySQL)
        "dameng": "dm",
        "analyticdb_pg": "postgresql",  # PG 内核
        # generic_jdbc 不在此表——URL 由用户在 connector_config.url 直接提供
    }

    # Gravitino Fileset provider 映射（§6.3）。s3/minio/oss 走 s3 provider
    # （S3 兼容协议更稳定，SeaTunnel OssFile 不支持 MinIO），hdfs 走 hdfs。
    # Gravitino Fileset catalog provider 映射（§6.3）。Gravitino 1.3.0 的 fileset
    # catalog provider 统一为 "fileset"（live 验证），存储后端由 location 的 scheme
    # 决定（s3a:// / hdfs:// 等），不是 provider 名。设计文档初版写 provider="s3" 有误。
    _FILESET_PROVIDER_MAP: dict[str, str] = {
        "s3": "fileset",
        "minio": "fileset",
        "oss": "fileset",  # 走 S3 兼容协议（s3a:// + endpoint）
        "hdfs": "fileset",  # location 用 hdfs://
    }

    # Gravitino 湖仓 catalog provider 映射（§6.2）。Delta/Hudi/Paimon 用
    # Generic Lakehouse Catalog（Gravitino 1.2.0+ #9647）统一纳管。
    _LAKEHOUSE_PROVIDER_MAP: dict[str, str] = {
        "hive": "hive",
        "delta": "lakehouse-delta",
        "hudi": "lakehouse-hudi",
        "paimon": "lakehouse-paimon",
    }

    @staticmethod
    def _compute_capabilities(connector_type: str) -> list[str]:
        """Derive capability list from connector_type.

        lite 版用 LITE_CAPABILITY_MAP（四类源，能力收窄为 explore+virtual_table）；
        full 版用 CAPABILITY_MAP（全量，含国产库/湖仓/对象存储）。
        """
        from ontology.config.settings import settings
        from ontology.core.schemas.datasource import CAPABILITY_MAP, LITE_CAPABILITY_MAP

        type_lower = connector_type.lower()
        cap_map = LITE_CAPABILITY_MAP if settings.edition == "lite" else CAPABILITY_MAP
        return cap_map.get(type_lower, ["explore"])

    @staticmethod
    def _resolve_driver(connector_type: str, connector_config: dict[str, Any]) -> str:
        """Resolve the JDBC driver class for a connector type.

        generic_jdbc has no static mapping — the driver class is supplied by
        the user in ``connector_config.driver`` (SeaTunnel GenericDialect).
        All other JDBC types look up ``_JDBC_DRIVER_MAP``.
        """
        type_lower = connector_type.lower()
        if type_lower == "generic_jdbc":
            return str(connector_config.get("driver", ""))
        return DataSourceService._JDBC_DRIVER_MAP.get(type_lower, "")

    @staticmethod
    def _apply_catalog_host_override(url: str) -> str:
        """Rewrite the host in a JDBC URL to the Gravitino/Trino container's view.

        Mirror of :meth:`_rewrite_source_host_for_seatunnel` but for catalogs:
        the jdbc-url stored on a Gravitino catalog is consumed *inside* the
        Gravitino/Trino container, so a datasource host of ``localhost`` (the
        backend's view) is unreachable there. When
        ``settings.catalog_jdbc_host_override`` is set, swap the host portion
        so Gravitino can dial the source DB (e.g. "benchmark-mysql" on the
        shared docker network). Empty = leave the URL untouched.
        """
        override = settings.catalog_jdbc_host_override
        if not override:
            return url
        import re

        m = re.match(r"(jdbc:[^:]+://)([^:/]+)(.*)$", url)
        if not m:
            return url
        return f"{m.group(1)}{override}{m.group(3)}"

    @staticmethod
    def _rewrite_source_host_for_seatunnel(url: str, connector_type: str) -> str:
        """Rewrite the host in a JDBC URL to SeaTunnel's view.

        The backend builds JDBC URLs from the datasource record using its
        own host view (``localhost`` when the backend runs on the host).
        SeaTunnel runs in a container where ``localhost`` is itself, so a
        source URL pointing at localhost is unreachable from SeaTunnel.
        When ``settings.seatunnel_source_host_override`` is set, swap the
        host portion of the URL so SeaTunnel can dial the source DB
        (e.g. via a container name on the shared docker network).
        """
        override = settings.seatunnel_source_host_override
        if not override:
            return url
        # jdbc:<proto>://host:port/db?extra → swap host only.
        import re

        m = re.match(r"(jdbc:[^:]+://)([^:/]+)(.*)$", url)
        if not m:
            return url
        return f"{m.group(1)}{override}{m.group(3)}"

    @staticmethod
    def _build_jdbc_url(connector_type: str, config: dict[str, Any], *, include_database: bool = True) -> str:
        """Build JDBC URL from connector config.

        include_database=False 时 URL 不含 database——用于 Gravitino Trino connector
        注册 mysql catalog：底层 Trino mysql connector 要求 URL 无 database
        （``MySqlJdbcConfig.urlWithoutDatabase``），否则报 ``Database (catalog)
        must not be specified in JDBC URL``，catalog 加载失败。database 由 Gravitino
        catalog 的 jdbc-database 属性单独指定。

        SeaTunnel Jdbc source 反而需要 URL 带 database（MySqlCatalog.getTable 查
        元数据依赖 URL 里的库），故默认 include_database=True。

        PostgreSQL 的 URL 始终带 database（PG connector 允许，且 SeaTunnel/Gravitino
        均无 URL 无库的要求）。
        """
        host = config.get("host", "localhost")
        port = config.get("port", "")
        database = config.get("database", "")
        extra = config.get("extra_params", "")

        type_lower = connector_type.lower()

        # generic_jdbc：URL 由用户在 connector_config.url 直接提供（完整 JDBC URL）
        if type_lower == "generic_jdbc":
            url = str(config.get("url", ""))
            if not url:
                raise ValidationError("generic_jdbc data source requires 'url' in connector_config")
            return url

        scheme = DataSourceService._JDBC_URL_SCHEME.get(type_lower, type_lower)
        # MySQL/MariaDB/TiDB(oceanbase 走 oceanbase scheme)：不带 database 的 URL
        # 仅 mysql/mariadb/tidb（mysql 协议）需要（Trino mysql connector 要求 URL 无库）
        is_mysql_proto = type_lower in ("mysql", "mariadb", "tidb", "starrocks")
        if not port:
            port = DataSourceService._default_port(type_lower)
        if is_mysql_proto and not include_database:
            url = f"jdbc:{scheme}://{host}:{port}"
        elif is_mysql_proto:
            url = f"jdbc:{scheme}://{host}:{port}/{database}"
        else:
            # PG 系（postgresql/opengauss/gaussdb/kingbase/analyticdb_pg）始终带 database
            url = f"jdbc:{scheme}://{host}:{port}/{database}"

        if extra:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{extra}"

        return url

    @staticmethod
    def _default_port(connector_type: str) -> str:
        """Default port for a connector type (empty if unknown)."""
        ports: dict[str, str] = {
            "mysql": "3306",
            "mariadb": "3306",
            "tidb": "4000",
            "oceanbase": "2883",
            "starrocks": "9030",
            "postgresql": "5432",
            "postgres": "5432",
            "opengauss": "5432",
            "gaussdb": "25308",
            "gaussdb_dws": "8000",
            "kingbase": "54321",
            "analyticdb_pg": "5432",
            "dameng": "5236",
        }
        return ports.get(connector_type.lower(), "")


# ═══════════════════════════════════════════════════════════════════
# SeaTunnel → SyncTask status mapping
# ═══════════════════════════════════════════════════════════════════


def _map_seatunnel_state(state: str) -> str:
    """Map a PipelineStatus.state onto the SyncTask.status vocabulary.

    SyncTask.status is the DB column
    (DRAFT | RUNNING | FINISHED | STOPPED | CANCELED | FAILED).

    Preserves SeaTunnel's native terminal-state semantics so the UI can
    distinguish three distinct outcomes:
      - FINISHED: job completed successfully
      - CANCELED: user cancelled the job
      - STOPPED:  job stopped programmatically (e.g. rebuild)

    UNKNOWN is intentionally NOT mapped to FAILED: an unknown job may
    simply have aged out of SeaTunnel's finished-jobs window while the
    task was legitimately finished. We preserve the existing stored
    status by returning the sentinel "_KEEP_", which the caller handles
    by skipping the update.
    """
    mapping = {
        "RUNNING": "RUNNING",
        "FINISHED": "FINISHED",
        "CANCELED": "CANCELED",
        "STOPPED": "STOPPED",
        "FAILED": "FAILED",
    }
    return mapping.get(state, "_KEEP_")
