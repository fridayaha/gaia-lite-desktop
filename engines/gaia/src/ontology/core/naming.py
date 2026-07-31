"""Centralized physical-resource naming for ontology namespace isolation.

Per ``docs/design/ontology-namespace-isolation-and-cleanup.md`` §4.4, the
physical resources owned by an ObjectType carry the owning Ontology's
api_name as a prefix so that two ontologies may both define an ObjectType
named ``asset`` without their Doris index tables / SeaTunnel INDEX
pipelines colliding.

Naming rules (decision 5 / 10 / 12):

+-------------------+-------------------------------------------+-----------+
| Resource          | Pattern                                   | has ont?  |
+-------------------+-------------------------------------------+-----------+
| Dataset api_name  | user-defined (snake_case), globally UNIQUE | no       |
| Iceberg table     | == Dataset api_name (snake_case)          | no        |
| Doris idx table   | ``idx_{ontology}__{type}`` (snake_case)   | yes       |
| SeaTunnel INDEX   | ``index_{ontology}__{type}`` (snake_case) | yes       |
| SeaTunnel SYNC    | ``sync_{dataset_api_name}``               | no        |
| RustFS S3 path    | ``s3://ontology-warehouse/{ont}/{type}/`` | yes       |
+-------------------+-------------------------------------------+-----------+

All physical identifiers use **snake_case** (all-lower, word boundaries
preserved) via :func:`_to_snake`, not ``.lower()`` flattening — e.g.
``FlightStatusLog`` → ``flight_status_log`` (readable) rather than
``flightstatuslog`` (word boundaries lost). Trino's iceberg REST case-
folding issue concerns letter case only; ``_`` is a valid identifier
char, so snake_case is safe in every layer.

Iceberg table names are NOT generated here — they are the Dataset's own
api_name (user-defined, globally UNIQUE), which is what guarantees cross-
ontology isolation at the dataset layer without encoding ontology info
(decision 5). Doris / SeaTunnel INDEX / S3 do encode ontology because
their consumers are ObjectType-scoped and would otherwise collide when
two ontologies reuse the same ObjectType api_name (e.g. ``asset`` shared
across 20 ontologies).

All generated identifiers pass ``_validate_identifier`` (matches Doris /
SeaTunnel / S3 key naming: ``[A-Za-z_][A-Za-z0-9_]*``). api_name inputs
are already validated upstream by pydantic schemas, so there is no
injection risk; the guard here is a defensive fail-fast.
"""

from __future__ import annotations

import re

# Graph / GeoTime 物理资源命名分隔符。Neo4j 标签/关系类型用 PascalCase（Neo4j 约定），
# PostGIS 空间表 / TimescaleDB 超表用 snake_case（PG 约定，与现有 doris_index_table 一致）。
# 二者均带本体前缀，因为 ObjectType api_name 仅在本体内唯一，跨本体同名类型会冲突。

__all__ = [
    "sync_pipeline",
    "derive_api_name",
    "doris_index_table",
    "index_pipeline",
    "index_backfill_pipeline",
    "index_stream_pipeline",
    "iceberg_s3_location",
    "managed_dataset_api_name",
    # Graph / GeoTime 物理资源命名 (graph-reasoning-design.md §3.3, §5.2, §5.3):
    "graph_label",
    "graph_relationship_type",
    "geo_table",
    "timeseries_hypertable",
    # Pipeline Builder (ADR-018) physical-resource naming:
    "kestra_flow_id",
    "kestra_namespace",
    "validate_identifier",
    # apiName pattern constants (Gaia decision, see reference-palantir-ontology.md):
    "PROPERTY_API_NAME_PATTERN",  # ^[a-z][a-zA-Z0-9]{0,99}$  camelCase
    "DATASET_API_NAME_PATTERN",  # ^[a-z][a-z0-9_]{0,99}$  snake_case (物理表名)
    "OBJECT_TYPE_API_NAME_PATTERN",  # ^[A-Z][a-zA-Z0-9]{0,99}$  PascalCase
    "ONTOLOGY_API_NAME_PATTERN",  # same as ObjectType — namespace is user-named
    "SOURCE_PATTERN",  # ^[A-Za-z][A-Za-z0-9 _-]{0,99}$  derivation source
]

# Doris table names, SeaTunnel job names, and S3 object-key segments all
# accept the same identifier charset. We keep this strict (no dots/hyphens)
# so a generated name is valid in every layer without per-layer escaping.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# RustFS/S3 bucket is fixed by deployment (see architecture_plan.md §1.3).
# Kept as a module constant so the bucket name is not scattered across
# call sites; a future config override can replace it in one place.
_S3_BUCKET = "ontology-warehouse"

# Neo4j 标签/关系类型名不允许下划线开头以外的特殊字符，且惯例用 PascalCase。
# 这里统一生成 PascalCase 以保持与 Neo4j 社区惯例一致，并避免与 Neo4j 内置标签
# (Node/Relationship) 冲突——加本体前缀 + PascalCase 化的 ObjectType api_name。
# 例: ontology=SupplyChain, type=Supplier → 标签 `SupplyChainSupplier`；
#     link=supplies → 关系类型 `SupplyChainSupplies`。
# 迁移口子 C1: vid 用 object_state.id，标签/关系类型可由 schema 重建。


def validate_identifier(name: str) -> str:
    """Fail-fast guard for a generated identifier.

    Returns ``name`` unchanged when valid, raises ``ValueError`` otherwise.
    Callers feed in api_name values already vetted by pydantic, so a failure
    here indicates an upstream schema gap rather than user input — hence
    the explicit, loud error instead of silent sanitization.
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid identifier {name!r}: must match {_IDENTIFIER_RE.pattern}. "
            "Ontology/ObjectType/Dataset api_name should be validated upstream."
        )
    return name


def _to_snake(name: str) -> str:
    """PascalCase/camelCase → snake_case (全小写, 保词界).

    物理资源名(Doris 表 / S3 路径 / Iceberg 表 / SeaTunnel job)用 snake_case
    而非 ``.lower()`` 全小写——后者丢失词界(FlightStatusLog→flightstatuslog),
    前者保留(FlightStatusLog→flight_status_log),可读性与调试性更佳。
    Trino iceberg REST 的大小写折叠问题仅与大小写有关,与下划线无关(``_`` 是
    合法标识符字符),故 snake_case 在各层均安全。

    连续大写缩写词(URL/HTTP/ID)作为整词处理:URLShortener→url_shortener,
    而非 u_r_l_shortener。
    """
    # 先把「连续大写 + 后跟小写」中的最后一组大写与后续小写合并前插下划线:
    #   URLShortener → URL_Shortener ; HTTPServer → HTTP_Server
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    # 再在「小写/数字 + 大写」处插下划线: flightStatus → flight_Status
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def sync_pipeline(dataset_api_name: str) -> str:
    """SYNC pipeline name: ``sync_{dataset}`` (source → Iceberg).

    The SYNC pipeline's consumer is a Dataset, and Dataset api_name is
    globally UNIQUE (decision 5), so no ontology prefix is needed — two
    ontologies cannot bind the same Dataset, and one Dataset has exactly
    one ingestion pipeline.
    """
    validate_identifier(dataset_api_name)
    return f"sync_{dataset_api_name}"


def doris_index_table(ontology_api_name: str, object_type_api_name: str) -> str:
    """Doris index table name: ``idx_{ontology}__{type}`` (snake_case).

    The Doris idx table is ObjectType-scoped, and ObjectType api_name is
    only unique *within* an ontology — so the ontology prefix is mandatory
    to prevent cross-ontology collisions (e.g. ``idx_ont1__asset`` vs
    ``idx_ont2__asset``). Both api_name inputs are converted to snake_case:
    physical resource names follow the snake_case convention (Doris table
    names, S3 keys), preserving word boundaries for readability, while the
    PascalCase/camelCase api_name is a business identifier that must not
    leak into physical naming.
    """
    ont = _to_snake(ontology_api_name)
    typ = _to_snake(object_type_api_name)
    validate_identifier(ont)
    validate_identifier(typ)
    return f"idx_{ont}__{typ}"


def index_pipeline(ontology_api_name: str, object_type_api_name: str) -> str:
    """INDEX pipeline name: ``index_{ontology}__{type}`` (Iceberg → Doris).

    Same rationale as :func:`doris_index_table`: the INDEX pipeline writes
    into the per-ObjectType Doris idx table, which is ontology-namespaced,
    so the pipeline name must match to keep stop/restart targeting correct
    (this also fixes the prior deprovision bug that stopped ``sync_{type}``
    instead of the INDEX pipeline). Inputs are converted to snake_case to
    match the physical Doris table naming and preserve word boundaries.

    Note: since ADR-008 "模式选择评估" (2026-07), the INDEX sync is split
    into two SeaTunnel jobs with distinct lifecycles — a one-shot BATCH
    backfill and a long-running STREAMING incremental. Each needs its own
    jobName so they can be stopped/started independently; use
    :func:`index_backfill_pipeline` and :func:`index_stream_pipeline` for
    the concrete job names. This function remains as the logical group
    name (e.g. for logging/DB rows that track "the OT's index sync").
    """
    ont = _to_snake(ontology_api_name)
    typ = _to_snake(object_type_api_name)
    validate_identifier(ont)
    validate_identifier(typ)
    return f"index_{ont}__{typ}"


def index_backfill_pipeline(ontology_api_name: str, object_type_api_name: str) -> str:
    """BATCH backfill job name: ``index_{ontology}__{type}__backfill``.

    A one-shot SeaTunnel BATCH job that does a full-snapshot Iceberg → Doris
    upsert to populate the index table from empty (used on provision/rebuild).
    Distinct from :func:`index_stream_pipeline` so the two jobs (different
    lifecycles: FINISHED vs long-running) never collide on jobName and can
    be stopped/started independently. See ADR-008 "模式选择评估".
    """
    return f"{index_pipeline(ontology_api_name, object_type_api_name)}__backfill"


def index_stream_pipeline(ontology_api_name: str, object_type_api_name: str) -> str:
    """STREAMING incremental job name: ``index_{ontology}__{type}__stream``.

    A long-running SeaTunnel STREAMING job (``stream_scan_strategy =
    FROM_LATEST_SNAPSHOT``) that tails Iceberg snapshot appends and upserts
    them into the Doris index table. Distinct from
    :func:`index_backfill_pipeline` so the two jobs never collide on jobName.
    See ADR-008 "模式选择评估".
    """
    return f"{index_pipeline(ontology_api_name, object_type_api_name)}__stream"


def iceberg_s3_location(ontology_api_name: str, object_type_api_name: str) -> str:
    """RustFS/S3 storage path: ``s3://ontology-warehouse/{ont}/{type}/``.

    The S3 path is ObjectType-scoped (one Iceberg table per MANAGED
    ObjectType), so it carries the ontology prefix for directory-level
    isolation. The trailing slash marks it as a table *location* (Iceberg
    expects a directory, not a single object). Inputs are converted to
    snake_case to match physical resource naming conventions and preserve
    word boundaries in directory names.
    """
    ont = _to_snake(ontology_api_name)
    typ = _to_snake(object_type_api_name)
    validate_identifier(ont)
    validate_identifier(typ)
    return f"s3://{_S3_BUCKET}/{ont}/{typ}/"


def graph_label(ontology_api_name: str, object_type_api_name: str) -> str:
    """Neo4j 节点标签: ``{Ontology}{ObjectType}`` (PascalCase, 带本体前缀)。

    Neo4j 标签惯例 PascalCase。ObjectType api_name 仅在本体内唯一，故拼接
    本体 api_name 作前缀防跨本体同名冲突（与 doris_index_table 同理的命名空间
    隔离，但用 PascalCase 而非 snake_case 以贴合 Neo4j 惯例）。
    例: ``graph_label("SupplyChain", "Supplier")`` → ``SupplyChainSupplier``。

    迁移口子（C1）: 标签/关系类型可由本体 schema 全量重建（rebuild_graph），
    Neo4j 内部 id 不被外部使用（vid=object_state.id）。
    """
    # api_name 已是 PascalCase（OBJECT_TYPE_API_NAME_PATTERN），直接拼接即可。
    # 此处不做大小写转换，保留原 PascalCase 词界。
    # 分别校验两个输入均为 PascalCase，避免“本体合规 + OT 小写”这类拼接后
    # 整体首字母仍大写但 OT 部分非法的情况漏网。
    if not _OBJECT_TYPE_RE.match(ontology_api_name):
        raise ValueError(f"Invalid ontology api_name {ontology_api_name!r}: expected PascalCase.")
    if not _OBJECT_TYPE_RE.match(object_type_api_name):
        raise ValueError(f"Invalid object_type api_name {object_type_api_name!r}: expected PascalCase.")
    label = f"{ontology_api_name}{object_type_api_name}"
    return label


def graph_relationship_type(ontology_api_name: str, link_type_api_name: str) -> str:
    """Neo4j 关系类型: ``{Ontology}{LinkType}`` (PascalCase, 首字母大写化)。

    LinkType api_name 是 camelCase（PROPERTY_API_NAME_PATTERN），Neo4j 关系类型
    惯例 PascalCase，故首字母大写化后拼接本体前缀。
    例: ``graph_relationship_type("SupplyChain", "supplies")`` → ``SupplyChainSupplies``。
    """
    if not link_type_api_name:
        raise ValueError("link_type_api_name must not be empty")
    if not _OBJECT_TYPE_RE.match(ontology_api_name):
        raise ValueError(f"Invalid ontology api_name {ontology_api_name!r}: expected PascalCase.")
    # LinkType api_name 是 camelCase（首字母小写），校验其合规。
    if not _PROPERTY_RE.match(link_type_api_name):
        raise ValueError(f"Invalid link_type api_name {link_type_api_name!r}: expected camelCase.")
    rel = f"{ontology_api_name}{link_type_api_name[0].upper()}{link_type_api_name[1:]}"
    return rel


def geo_table(ontology_api_name: str, object_type_api_name: str) -> str:
    """PostGIS 静态空间表名: ``geo_{ont}__{type}`` (snake_case, 带本体前缀)。

    与 doris_index_table 同构（本体前缀 + 双下划线分隔），只是前缀从 ``idx_``
    换成 ``geo_``。snake_case 保词界，PG 表名惯例全小写。
    例: ``geo_table("SupplyChain", "Supplier")`` → ``geo_supply_chain__supplier``。
    """
    ont = _to_snake(ontology_api_name)
    typ = _to_snake(object_type_api_name)
    validate_identifier(ont)
    validate_identifier(typ)
    return f"geo_{ont}__{typ}"


def timeseries_hypertable(ontology_api_name: str, object_type_api_name: str, series_property_api_name: str) -> str:
    """TimescaleDB 超表名: ``timeseries_{ont}__{type}__{series}`` (snake_case)。

    一个对象的每个 GEOTEMPORAL_SERIES / TIME_SERIES 属性对应一张超表（series_id
    列区分不同对象实例的序列）。三段式命名: 本体前缀 + 对象类型 + 序列属性名，
    全部 snake_case，双下划线分隔（与 geo_table / doris_index_table 风格一致）。
    例: ``timeseries_hypertable("Logistics", "Vehicle", "track")``
        → ``timeseries_logistics__vehicle__track``。
    """
    ont = _to_snake(ontology_api_name)
    typ = _to_snake(object_type_api_name)
    series = _to_snake(series_property_api_name)
    validate_identifier(ont)
    validate_identifier(typ)
    validate_identifier(series)
    return f"timeseries_{ont}__{typ}__{series}"


# ── Pipeline Builder (ADR-018) 命名 ──
# Kestra Flow ID / namespace / Iceberg 表引用统一走 naming，避免手拼注入。
# pipeline_api_name 已由 pydantic 校验 ^[a-z][a-z0-9_]*$，但 dataset_name
# 来自节点 config.extra.dataset（用户选择），需 validate_identifier 兜底。


def kestra_flow_id(pipeline_api_name: str) -> str:
    """Kestra Flow ID: ``pipeline_{api_name}``.

    Kestra Flow ID 必须匹配 ``[a-zA-Z][a-zA-Z0-9_-]*``。pipeline_api_name
    已是 ``^[a-z][a-z0-9_]*$``（pydantic 校验），加 ``pipeline_`` 前缀
    既满足 Kestra 命名要求，又避免与用户手建 Flow 冲突。
    """
    validate_identifier(pipeline_api_name)
    return f"pipeline_{pipeline_api_name}"


def kestra_namespace(project_api_name: str = "pipelines") -> str:
    """Kestra namespace: ``gaia.{project}``.

    project_api_name 经 validate_identifier 校验后拼接到 ``kestra_namespace_prefix``
    （默认 ``gaia``））下。MVP 单 project ``pipelines``，Phase 2 多项目隔离。
    """
    validate_identifier(project_api_name)
    return f"gaia.{project_api_name}"


def managed_dataset_api_name(object_type_api_name: str) -> str:
    """Derive the snake_case ``api_name`` of a MANAGED ObjectType's own dataset.

    A MANAGED ObjectType owns an Iceberg-backed dataset. The dataset api_name
    doubles as the physical Iceberg table name (per the naming rules above:
    Iceberg table name == dataset api_name), and Iceberg table names in this
    deployment are **lower-cased**: the SeaTunnel SYNC/INDEX pipelines sink
    and source via snake_case conversion of ``dataset_api_name`` /
    ``object_type_api_name`` (see ``sea_tunnel_engine._build_sync_pipeline`` /
    ``create_index_pipeline``), because Trino's iceberg REST client lower-
    cases identifiers on lookup while the REST server preserves the declared
    case — a mixed-case table name is therefore unreachable from Trino.
    Keeping the dataset api_name in snake_case (all-lower, word boundaries
    preserved) makes the PG governance record, the ``backing_mapping`` refs,
    the Iceberg table, and the SeaTunnel pipelines all agree on one
    identifier, while remaining readable.

    So ``Flight`` → ``flight``, ``FlightStatusLog`` → ``flight_status_log``,
    ``CustomerOrder`` → ``customer_order``. The result satisfies the dataset
    api_name pattern ``DATASET_API_NAME_PATTERN`` (``^[a-z][a-z0-9_]{0,99}$``).

    Raises ``ValueError`` if the input is empty or not a valid PascalCase
    ObjectType api_name (defensive — callers normally pass a schema-validated
    ObjectType api_name).
    """
    if not object_type_api_name or not object_type_api_name[0].isupper():
        raise ValueError(f"expected a PascalCase ObjectType api_name, got {object_type_api_name!r}")
    return _to_snake(object_type_api_name)


# ── apiName 自动推导(对标 Palantir Foundry) ──
# 推导优先级(决策):
#   1. displayName 满足 SOURCE_PATTERN → 从 displayName 推导
#   2. backingColumn 满足 SOURCE_PATTERN → 从 backingColumn 推导
#   3. 兜底 prefixN(property0/ObjectType0/...)
# 用 pattern 校验代替“分词有无”判断:中文 displayName 不满足 SOURCE_PATTERN
# (首字符非 ASCII 字母),自动回退到 backingColumn。
# apiName 一旦生成即永久固化,后续修改 displayName/backingColumn 不影响。
#
# pattern 规则(决策):
#   - 属性 apiName: ^[a-z][a-zA-Z0-9]{0,99}$ (camelCase, 首词小写)
#   - 对象 apiName: ^[A-Z][a-zA-Z0-9]{0,99}$ (PascalCase, 首字母大写)
#   - displayName/backingColumn 作推导源: ^[A-Za-z][A-Za-z0-9 _-]{0,99}$
# 对象 apiName 对外统一大写开头;物理资源命名(Doris 表等)内部转全小写 snake_case。

# 推导源 pattern:ASCII 字母开头,允许字母数字空格下划线连字符。
_SOURCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,99}$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# apiName pattern(决策):
#   - 属性/Link/Action/参数 apiName: camelCase, 首词小写
#   - 对象类型 apiName: PascalCase, 首字母大写
#   - Ontology apiName: PascalCase (与 ObjectType 同 — namespace is user-named,
#     对外统一大写开头)
#   - 对外统一用 apiName;物理资源命名(Doris 表等)内部转全小写 snake_case。
PROPERTY_API_NAME_PATTERN = r"^[a-z][a-zA-Z0-9]{0,99}$"
OBJECT_TYPE_API_NAME_PATTERN = r"^[A-Z][a-zA-Z0-9]{0,99}$"
# 编译版，供 naming 内部快速校验 PascalCase 标识符（graph_label 等）。
_OBJECT_TYPE_RE = re.compile(OBJECT_TYPE_API_NAME_PATTERN)
_PROPERTY_RE = re.compile(PROPERTY_API_NAME_PATTERN)
# Ontology apiName 复用 ObjectType 的 PascalCase pattern(语义别名)。
ONTOLOGY_API_NAME_PATTERN = OBJECT_TYPE_API_NAME_PATTERN
# Dataset apiName: snake_case(全小写保词界)。用于系统生成的托管数据集
# (managed_dataset_api_name)及手动登记的数据集 api_name——它兼任物理 Iceberg
# 表名,需全小写以规避 Trino iceberg REST 大小写折叠问题,且保词界以保证可读性。
DATASET_API_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,99}$"
# 推导源(displayName/backingColumn) pattern。
SOURCE_PATTERN = r"^[A-Za-z][A-Za-z0-9 _-]{0,99}$"


def _to_api_case(words: list[str], *, pascal: bool) -> str:
    """单词列表 → camelCase(pascal=False,首词小写)或 PascalCase(pascal=True)。"""
    if not words:
        return ""
    if pascal:
        return "".join(w.capitalize() for w in words)
    return words[0].lower() + "".join(w.capitalize() for w in words[1:])


def derive_api_name(
    display_name: str,
    *,
    backing_column: str | None = None,
    fallback_prefix: str = "property",
    existing_count: int = 0,
    pascal: bool = False,
) -> str:
    """从 displayName 推导 apiName。

    优先级: displayName(SOURCE_PATTERN 合规) > backingColumn(合规) > 兜底 prefixN。

    Args:
        display_name: 展示名。仅当满足 SOURCE_PATTERN(ASCII 字母开头)时参与推导。
        backing_column: 底层物理列名。仅当 displayName 不合规时用。
        fallback_prefix: 兜底前缀(property/ObjectType/actionType/linkType)。
            PascalCase 实体应传首字母大写前缀(如 'ObjectType'),
            使兜底名本身满足 ^[A-Z] pattern。
        existing_count: 已有同名兜底数量,生成唯一 N。
        pascal: True → PascalCase(对象 apiName,首字母大写);
                False → camelCase(属性 apiName,首词小写)。
    """
    # 1. displayName 满足 SOURCE_PATTERN
    if display_name and _SOURCE_RE.match(display_name):
        words = _WORD_RE.findall(display_name)
        if words:
            return _to_api_case(words, pascal=pascal)
    # 2. backingColumn 满足 SOURCE_PATTERN
    if backing_column and _SOURCE_RE.match(backing_column):
        words = _WORD_RE.findall(backing_column)
        if words:
            return _to_api_case(words, pascal=pascal)
    # 3. 兜底 prefixN
    return f"{fallback_prefix}{existing_count}"
