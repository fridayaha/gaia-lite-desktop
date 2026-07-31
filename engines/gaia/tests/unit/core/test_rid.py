"""RID 生成器单元测试 (PR 1: Palantir RID 规范实现)。

覆盖：
- generate_object_rid 格式/唯一性/长度
- generate_virtual_rid 格式/特殊字符清理/长度
- parse_rid 正常路径 + 异常路径 (非法格式/大小写/空串/空 instance)
- is_managed_rid / is_virtual_rid 容错
- parse_virtual_rid_pk 正常 + 异常 + pk 含点的边界

迁移权威源: docs/architecture/handoff-rid-migration.md §五 PR 1。
"""

from __future__ import annotations

import re

import pytest

from ontology.core.rid import (
    INSTANCE,
    OBJECT_TYPE,
    SERVICE,
    VIRTUAL_OBJECT_TYPE,
    ResourceId,
    generate_object_rid,
    generate_virtual_rid,
    is_managed_rid,
    is_virtual_rid,
    parse_rid,
    parse_virtual_rid_pk,
)

# ── 常量 ──
_OBJECT_RID_PREFIX = f"ri.{SERVICE}.{INSTANCE}.{OBJECT_TYPE}."
_VIRTUAL_RID_PREFIX = f"ri.{SERVICE}.{INSTANCE}.{VIRTUAL_OBJECT_TYPE}."
# 标准 UUID 格式 (带连字符, 36 字符) — 对齐 Palantir 实际格式。
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class TestGenerateObjectRid:
    def test_prefix_and_uuid_locator(self):
        rid = generate_object_rid()
        assert rid.startswith(_OBJECT_RID_PREFIX)
        locator = rid[len(_OBJECT_RID_PREFIX) :]
        assert re.fullmatch(_UUID_RE, locator)

    def test_unique_across_many_calls(self):
        rids = {generate_object_rid() for _ in range(1000)}
        assert len(rids) == 1000

    def test_length_well_under_128(self):
        # 前缀 25 + UUID 36 = 61，远小于 String(128) 上限。
        assert len(generate_object_rid()) < 128

    def test_parseable_as_managed(self):
        # 生成 → 解析往返: 生成出的 rid 必须能被 parse_rid 接受且判为 MANAGED。
        rid = generate_object_rid()
        parsed = parse_rid(rid)
        assert parsed.is_object is True
        assert parsed.is_virtual_object is False
        assert (parsed.service, parsed.instance, parsed.type) == (SERVICE, INSTANCE, OBJECT_TYPE)


class TestGenerateVirtualRid:
    def test_format_basic(self):
        rid = generate_virtual_rid("supplychain", "Order", "ORD001")
        assert rid == f"{_VIRTUAL_RID_PREFIX}supplychain.Order.ORD001"

    def test_type_segment_distinguishes_from_managed(self):
        # virtual-object 与 object 区分，水合按 type 分流的依据。
        v = generate_virtual_rid("ont", "OT", "pk1")
        m = generate_object_rid()
        assert parse_rid(v).type == VIRTUAL_OBJECT_TYPE
        assert parse_rid(m).type == OBJECT_TYPE
        assert parse_rid(v).is_virtual_object is True
        assert parse_rid(v).is_object is False

    def test_pk_with_spaces_and_slash_sanitized(self):
        # locator regex 不允许空格/斜杠，替换为下划线。
        rid = generate_virtual_rid("ont", "OT", "pk with space/slash")
        assert " " not in rid
        assert "/" not in rid
        # 解析回来的 pk 是清理后的版本 (与原始不一致 — 文档已警告)。
        _, _, pk = parse_virtual_rid_pk(rid)
        assert pk == "pk_with_space_slash"

    def test_pk_with_dots_preserved(self):
        # pk 内部的点被保留 (split(".", 2) 不切断)，如版本号。
        rid = generate_virtual_rid("ont", "OT", "1.0.3")
        _, _, pk = parse_virtual_rid_pk(rid)
        assert pk == "1.0.3"

    def test_pk_with_chinese_sanitized(self):
        # 中文非 [a-zA-Z0-9\-\.]，每个中文字符替换为 1 个下划线。
        # "订单001" → "__001" (订→_, 单→_, 001 保留)。
        rid = generate_virtual_rid("ont", "OT", "订单001")
        # 解析回来 pk 已失真 — 调用方需自行编码 (如 base64)，MVP 假设 PK 是字母数字。
        _, _, pk = parse_virtual_rid_pk(rid)
        assert pk == "__001"

    def test_numeric_pk(self):
        rid = generate_virtual_rid("ont", "OT", "12345")
        assert rid.endswith(".12345")

    def test_length_well_under_128(self):
        # 典型 VIRTUAL rid ≈ 50~70 字符。
        rid = generate_virtual_rid("supplychain", "Order", "ORD001")
        assert len(rid) < 128

    def test_pk_value_coerced_to_str(self):
        # int 输入也应工作 (str() 转换)。
        rid = generate_virtual_rid("ont", "OT", 42)  # type: ignore[arg-type]
        assert rid.endswith(".42")


class TestParseRid:
    def test_parse_managed(self):
        rid = generate_object_rid()
        parsed = parse_rid(rid)
        assert isinstance(parsed, ResourceId)
        assert parsed.service == SERVICE
        assert parsed.instance == INSTANCE
        assert parsed.type == OBJECT_TYPE
        assert parsed.is_object is True
        assert parsed.is_virtual_object is False

    def test_parse_virtual(self):
        rid = f"{_VIRTUAL_RID_PREFIX}supplychain.Order.ORD001"
        parsed = parse_rid(rid)
        assert parsed.type == VIRTUAL_OBJECT_TYPE
        assert parsed.is_object is False
        assert parsed.is_virtual_object is True

    def test_parse_ontology_resource_rid(self):
        # ontology 资源的 RID (与 object 同名 rid 字段不冲突，靠 type 段区分)。
        # 本次迁移不改 ontology.rid，但模块须能解析此类 rid。
        parsed = parse_rid("ri.ontology.main.ontology.abc123")
        assert parsed.type == "ontology"
        assert parsed.is_object is False
        assert parsed.is_virtual_object is False

    def test_empty_instance_allowed(self):
        # instance 段可为空 (spec 允许)。
        parsed = parse_rid("ri.ontology..object.xxx")
        assert parsed.instance == ""
        assert parsed.is_object is True

    def test_invalid_not_a_rid(self):
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("not-a-rid")

    def test_invalid_uppercase_segments(self):
        # service/instance/type 必须 [a-z...]，大写不合法。
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("ri.Ontology.Main.Object.xxx")

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("")

    def test_invalid_missing_prefix(self):
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("ontology.main.object.xxx")

    def test_invalid_too_few_segments(self):
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("ri.ontology.main.object")

    def test_invalid_locator_empty(self):
        # locator 不能为空 (regex [a-zA-Z0-9\-._]+ 要求至少一字符)。
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_rid("ri.ontology.main.object.")

    def test_resource_id_is_frozen(self):
        # ResourceId 是 frozen dataclass，不可变 (哈希安全)。
        parsed = parse_rid(generate_object_rid())
        with pytest.raises(Exception):  # FrozenInstanceError ( AttributeError 子类 )
            parsed.service = "other"  # type: ignore[misc]


class TestIsManagedRid:
    def test_true_for_generated_object_rid(self):
        assert is_managed_rid(generate_object_rid()) is True

    def test_false_for_virtual_rid(self):
        assert is_managed_rid(f"{_VIRTUAL_RID_PREFIX}ont.ot.pk") is False

    def test_false_for_invalid_string(self):
        # 容错: 非法格式返回 False 而非抛异常 (防御性，避免单条坏数据中断整批水合)。
        assert is_managed_rid("invalid") is False
        assert is_managed_rid("") is False
        assert is_managed_rid("ri.Ontology.Main.Object.xxx") is False

    def test_false_for_other_resource_types(self):
        assert is_managed_rid("ri.ontology.main.ontology.abc") is False


class TestIsVirtualRid:
    def test_true_for_virtual_rid(self):
        assert is_virtual_rid(f"{_VIRTUAL_RID_PREFIX}ont.ot.pk") is True

    def test_false_for_managed_rid(self):
        assert is_virtual_rid(generate_object_rid()) is False

    def test_false_for_invalid_string(self):
        assert is_virtual_rid("invalid") is False
        assert is_virtual_rid("") is False


class TestParseVirtualRidPk:
    def test_basic(self):
        ont, ot, pk = parse_virtual_rid_pk(f"{_VIRTUAL_RID_PREFIX}supplychain.Order.ORD001")
        assert (ont, ot, pk) == ("supplychain", "Order", "ORD001")

    def test_pk_with_dots_not_split(self):
        # split(".", 2) 只切前两段，pk 内部的点保留。
        _, _, pk = parse_virtual_rid_pk(f"{_VIRTUAL_RID_PREFIX}ont.ot.1.0.3")
        assert pk == "1.0.3"

    def test_raises_for_managed_rid(self):
        with pytest.raises(ValueError, match="Not a virtual object RID"):
            parse_virtual_rid_pk(generate_object_rid())

    def test_raises_for_invalid_rid(self):
        with pytest.raises(ValueError, match="Invalid RID format"):
            parse_virtual_rid_pk("not-a-rid")

    def test_raises_for_locator_missing_pk(self):
        # locator 只有 ont.ot 两段，不足三段。
        rid = f"{_VIRTUAL_RID_PREFIX}ont.ot"
        with pytest.raises(ValueError, match="Cannot parse ont/ot/pk"):
            parse_virtual_rid_pk(rid)

    def test_raises_for_locator_only_ont(self):
        rid = f"{_VIRTUAL_RID_PREFIX}ont"
        with pytest.raises(ValueError, match="Cannot parse ont/ot/pk"):
            parse_virtual_rid_pk(rid)

    def test_roundtrip_with_generate(self):
        # 生成 → 解析往返 (假设 pk 为字母数字，无特殊字符)。
        rid = generate_virtual_rid("myOnt", "MyType", "PK123")
        ont, ot, pk = parse_virtual_rid_pk(rid)
        assert (ont, ot, pk) == ("myOnt", "MyType", "PK123")


class TestRealWorldPalantirExample:
    """对齐 Palantir 社区实际 RID 实例，确保格式兼容。"""

    def test_phonograph2_objects_example(self):
        # 来自 Palantir 文档的社区实例 (service=phonograph2-objects)。
        # 验证本模块解析器对非 ontology service 的 RID 也能正确解析。
        rid = "ri.phonograph2-objects.main.object.48971f8a-fdff-4157-9cf4-aa3e98163be4"
        parsed = parse_rid(rid)
        assert parsed.service == "phonograph2-objects"
        assert parsed.instance == "main"
        assert parsed.type == "object"
        assert parsed.is_object is True
        # locator 是标准 UUID (Palantir 实际格式)。
        assert re.fullmatch(_UUID_RE, parsed.locator)

    def test_gaia_managed_matches_palantir_shape(self):
        # Gaia 生成的 MANAGED rid 与 Palantir 实例结构一致 (仅 service 不同)。
        gaia_rid = generate_object_rid()
        palantir_rid = "ri.phonograph2-objects.main.object.48971f8a-fdff-4157-9cf4-aa3e98163be4"
        g = parse_rid(gaia_rid)
        p = parse_rid(palantir_rid)
        assert g.instance == p.instance == "main"
        assert g.type == p.type == "object"
        # locator 均为 UUID 格式。
        assert re.fullmatch(_UUID_RE, g.locator)
        assert re.fullmatch(_UUID_RE, p.locator)
