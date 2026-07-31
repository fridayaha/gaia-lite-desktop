"""Palantir Resource Identifier (RID) 规范实现。

RID 是 Palantir Foundry 的统一资源身份模型，格式::

    ri.<service>.<instance>.<type>.<locator>

四段点分隔，``ri.`` 前缀 + 四段。各段 regex 来自 Palantir 开源 spec
(https://github.com/palantir/resource-identifier):

+-------------+-----------+-----------------------------------+---------------------+
| 段          | 含义      | regex                             | Gaia 取值           |
+=============+===========+===================================+=====================+
| service     | 服务空间  | ``[a-z][a-z0-9\\-]*``             | ``ontology``        |
| instance    | 部署实例  | ``([a-z0-9][a-z0-9\\-]*)?`` (可空)| ``main`` (单实例)   |
| type        | 资源类型  | ``[a-z][a-z0-9\\-]*``             | ``object`` /        |
|             |           |                                   | ``virtual-object`` |
| locator     | 定位串    | ``[a-zA-Z0-9\\-\\._]+``           | UUID (MANAGED) /    |
|             |           |                                   | ont.ot.pk (VIRTUAL) |
+-------------+-----------+-----------------------------------+---------------------+

Gaia 用到的 RID 类型：

* **MANAGED 对象** (``ri.ontology.main.object.{uuid}``) — 落地的本体对象实例，
  系统在创建时分配 (CREATE_OBJECT mutation)，分配后稳定不变。
* **VIRTUAL 对象** (``ri.ontology.main.virtual-object.{ont}.{ot}.{pk}``) — 外部
  数据源联邦代理，不落地，无系统分配身份，按需合成。合成 rid 不保证稳定
  (外部源 PK 改了就变)，这是 VIRTUAL 的固有特性，与 MANAGED 的稳定性不同。

身份正交分离原则 (本模块的设计灵魂)
------------------------------------

RID 是**系统身份**，与**业务身份** (primary key) 正交分离、互不依赖：

* RID: 系统自动分配、跨服务寻址、稳定不变。
* primary key: 用户提供、业务语义、可变。

应用层判等用 ``(typeId, primaryKey)``，**不用** RID —— 因为新创建未持久化的对象
RID 可能 undefined (Palantir 官方说明)。把 primary key 当 locator 会破坏"RID 分配
后不变"的稳定性保证，故本模块 ``generate_object_rid`` 的 locator 用随机 UUID 而非
primary key。

locator 不用 primary key 的另一个原因: primary key 是业务属性，可能含 RID locator
regex 不允许的字符 (空格、中文等)，强行编码反而引入复杂度。

参考
----

* `Palantir resource-identifier spec <https://github.com/palantir/resource-identifier>`_
* `Functions on objects · Object identifiers
  <https://palantir.com/docs/foundry/functions/object-identifiers/>`_
* ``docs/architecture/handoff-rid-migration.md`` — 迁移决策权威源
* ``docs/research/three-scenarios-ontology-graph-federation.md`` §身份模型决策注
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

__all__ = [
    "SERVICE",
    "INSTANCE",
    "OBJECT_TYPE",
    "VIRTUAL_OBJECT_TYPE",
    "ResourceId",
    "generate_object_rid",
    "generate_virtual_rid",
    "parse_rid",
    "is_managed_rid",
    "is_virtual_rid",
    "parse_virtual_rid_pk",
]

# ── Gaia RID 常量 ──
# service / instance 固定 (单 ontology 服务、单实例部署)；type 段区分 MANAGED vs
# VIRTUAL 对象。未来若启用 ontology.rid，其 type 段为 ``ontology``，与 object 同名
# 不冲突 (Palantir 的做法是所有资源 RID 字段都叫 rid，靠 type 段区分)。
SERVICE = "ontology"
INSTANCE = "main"
OBJECT_TYPE = "object"
VIRTUAL_OBJECT_TYPE = "virtual-object"

# ── RID 各段 regex (来自 Palantir spec) ──
# instance 段可为空 (整个段连同前导点省略)，spec 用 ``([a-z0-9][a-z0-9\-]*)?``
# 表达"可选的非空 instance"。下方完整 pattern 允许 ``ri.ontology..object.xxx``
# (空 instance) 这种边界写法，与 spec 一致。
_SERVICE_RE = r"[a-z][a-z0-9\-]*"
_INSTANCE_RE = r"([a-z0-9][a-z0-9\-]*)?"
_TYPE_RE = r"[a-z][a-z0-9\-]*"
_LOCATOR_RE = r"[a-zA-Z0-9\-\._]+"

# 完整 RID regex。命名分组便于 parse_rid 取段。
_RID_PATTERN = re.compile(
    rf"^ri\.(?P<service>{_SERVICE_RE})\."
    rf"(?P<instance>{_INSTANCE_RE})\."
    rf"(?P<type>{_TYPE_RE})\."
    rf"(?P<locator>{_LOCATOR_RE})$"
)

# VIRTUAL rid locator 段格式: {ont}.{ot}.{pk} —— 用 split(".", 2) 解析，使 pk
# 内部允许出现点 (如版本号 "1.0.3") 而不被切断。但 pk 仍受 _LOCATOR_RE 约束
# (不允许空格/中文等)，generate_virtual_rid 会对非法字符做下划线替换。
# ont / ot 是 api_name，已由 pydantic 校验为合规标识符，无需在此重校验。
_VIRTUAL_LOCATOR_MIN_PARTS = 3  # ont / ot / pk 至少三段


@dataclass(frozen=True)
class ResourceId:
    """解析后的 RID 四段。

    ``instance`` 可为空串 (spec 允许)。``is_object`` / ``is_virtual_object``
    按 type 段判别，用于水合分流 (MANAGED → Doris 主源；VIRTUAL → Trino 联邦)。
    """

    service: str
    instance: str
    type: str
    locator: str

    @property
    def is_object(self) -> bool:
        """是否为 MANAGED 对象 RID (type == ``object``)。"""
        return self.type == OBJECT_TYPE

    @property
    def is_virtual_object(self) -> bool:
        """是否为 VIRTUAL 对象合成 RID (type == ``virtual-object``)。"""
        return self.type == VIRTUAL_OBJECT_TYPE


def generate_object_rid() -> str:
    """生成 MANAGED 对象 RID。

    格式: ``ri.ontology.main.object.{uuid}``，UUID 带连字符 (36 字符)，对齐
    Palantir 实际格式 (社区实例 ``ri.phonograph2-objects.main.object.48971f8a-...``)。

    locator 用随机 UUID 而非 primary key —— 见模块 docstring 的"身份正交分离"。
    RID 总长约 61 字符 (前缀 25 + UUID 36)，远小于 String(128) 上限。
    """
    return f"ri.{SERVICE}.{INSTANCE}.{OBJECT_TYPE}.{uuid.uuid4()}"


def generate_virtual_rid(
    ontology_api_name: str,
    object_type_api_name: str,
    pk_value: str,
) -> str:
    """合成 VIRTUAL 对象 RID。

    格式: ``ri.ontology.main.virtual-object.{ont}.{ot}.{pk}``

    locator 嵌入 ont/ot/pk 以便水合时 :func:`parse_virtual_rid_pk` 解析回 PK 查 Trino。
    ``type`` 段用 ``virtual-object`` 与 MANAGED 的 ``object`` 区分，水合时按 type
    分流 (见 handoff §3.4)。

    Args:
        ontology_api_name: 本体 api_name (PascalCase，已校验)。
        object_type_api_name: 对象类型 api_name (PascalCase，已校验)。
        pk_value: 业务主键值。非 ``[a-zA-Z0-9\\-\\._]`` 字符会被替换为 ``_`` 以
            满足 locator regex —— **注意**: 这会使 :func:`parse_virtual_rid_pk`
            返回的 pk 与原始 pk 不一致。若业务 PK 含特殊字符，需调用方自行编码
            (如 base64) 后再传入。MVP 假设 PK 是字母数字。

    Note:
        VIRTUAL rid 是合成的，不保证稳定 (外部源 PK 改了就变)。这是 VIRTUAL 的
        固有特性，与 MANAGED 的"rid 稳定不变"不同。
    """
    # pk_value 可能含 locator regex 不允许的字符 (空格/中文/斜杠等)，做基本清理。
    # 不对 ont/ot 做清理 —— 它们是 pydantic 校验过的 api_name，本应合规；若不合规
    # 则是上游 bug，应当暴露而非静默修正。
    safe_pk = re.sub(r"[^a-zA-Z0-9\-\.]", "_", str(pk_value))
    return f"ri.{SERVICE}.{INSTANCE}.{VIRTUAL_OBJECT_TYPE}.{ontology_api_name}.{object_type_api_name}.{safe_pk}"


def parse_rid(rid: str) -> ResourceId:
    """解析 RID 字符串为四段。

    Args:
        rid: RID 字符串。

    Returns:
        :class:`ResourceId` 四段。

    Raises:
        ValueError: ``rid`` 不符合 RID 规范 (格式/大小写/段数错误)。
    """
    match = _RID_PATTERN.match(rid)
    if not match:
        raise ValueError(f"Invalid RID format: {rid!r}")
    return ResourceId(
        service=match.group("service"),
        instance=match.group("instance") or "",
        type=match.group("type"),
        locator=match.group("locator"),
    )


def is_managed_rid(rid: str) -> bool:
    """是否为 MANAGED 对象 RID (type == ``object``)。

    非法格式的 rid 返回 False 而非抛异常 —— 用于水合分流时容忍脏输入
    (调用方应已校验，但防御性返回 False 避免单条坏数据中断整批水合)。
    """
    try:
        return parse_rid(rid).is_object
    except ValueError:
        return False


def is_virtual_rid(rid: str) -> bool:
    """是否为 VIRTUAL 对象合成 RID (type == ``virtual-object``)。

    非法格式的 rid 返回 False (同 :func:`is_managed_rid` 的容错策略)。
    """
    try:
        return parse_rid(rid).is_virtual_object
    except ValueError:
        return False


def parse_virtual_rid_pk(rid: str) -> tuple[str, str, str]:
    """从 VIRTUAL rid 解析出 (ontology, object_type, pk_value)。

    locator 格式 ``{ont}.{ot}.{pk}``，用 ``split(".", 2)`` 使 pk 内部允许出现点
    (如版本号 "1.0.3") 而不被切断。

    Args:
        rid: VIRTUAL 对象 RID。

    Returns:
        ``(ontology_api_name, object_type_api_name, pk_value)`` 三元组。

    Raises:
        ValueError: 不是 VIRTUAL rid，或 locator 段数不足三段。
    """
    parsed = parse_rid(rid)
    if not parsed.is_virtual_object:
        raise ValueError(f"Not a virtual object RID: {rid!r}")
    parts = parsed.locator.split(".", 2)
    if len(parts) != _VIRTUAL_LOCATOR_MIN_PARTS:
        raise ValueError(f"Cannot parse ont/ot/pk from locator: {parsed.locator!r}")
    return parts[0], parts[1], parts[2]
