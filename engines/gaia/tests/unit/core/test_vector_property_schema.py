"""Schema tests for VECTOR property + VectorPropertyConfig (§14.4 语义检索).

验证:
- VectorPropertyConfig 字段默认值 (dimension=384, similarity_function=cosine)
- PropertyDef 从 ORM constraints JSONB → vector_config 转换 (validator)
- PropertyInput 透传 vector_config
- 非 VECTOR 属性 vector_config 为 None
"""

from datetime import datetime

from ontology.core.schemas.ontology import (
    DataType,
    PropertyDef,
    PropertyInput,
    VectorPropertyConfig,
)


class TestVectorPropertyConfig:
    def test_defaults(self):
        vc = VectorPropertyConfig()
        assert vc.dimension == 384
        assert vc.similarity_function == "cosine"
        assert vc.source_expression == []

    def test_with_source_expression(self):
        vc = VectorPropertyConfig(source_expression=["name", "description"])
        assert vc.source_expression == ["name", "description"]

    def test_l2_similarity(self):
        vc = VectorPropertyConfig(similarity_function="l2", dimension=768)
        assert vc.similarity_function == "l2"
        assert vc.dimension == 768


class TestPropertyDefVectorConfigCoercion:
    """PropertyDef._coerce_backing_mapping 把 constraints JSONB → vector_config."""

    def test_dict_with_constraints_becomes_vector_config(self):
        """dict 构造时 constraints 字段 → vector_config (VECTOR 属性)."""
        now = datetime(2026, 7, 13)
        prop = PropertyDef(
            id="p1",
            object_type_id="ot1",
            api_name="profileEmbedding",
            display_name="Profile Embedding",
            data_type=DataType.VECTOR,
            indexed=True,
            backing_catalog="iceberg",
            backing_schema="ontology",
            backing_table="doc",
            backing_column="profile_embedding",
            constraints={
                "dimension": 384,
                "similarity_function": "cosine",
                "source_expression": ["name", "description"],
            },
            created_at=now,
            updated_at=now,
        )
        assert prop.vector_config is not None
        assert prop.vector_config.dimension == 384
        assert prop.vector_config.source_expression == ["name", "description"]

    def test_dict_without_constraints_has_no_vector_config(self):
        """非 VECTOR 属性 (constraints 空) → vector_config=None."""
        now = datetime(2026, 7, 13)
        prop = PropertyDef(
            id="p1",
            object_type_id="ot1",
            api_name="name",
            display_name="Name",
            data_type=DataType.STRING,
            backing_catalog="iceberg",
            backing_schema="ontology",
            backing_table="doc",
            backing_column="name",
            created_at=now,
            updated_at=now,
        )
        assert prop.vector_config is None

    def test_orm_mock_with_constraints(self):
        """ORM 模型实例 (有 backing_catalog + constraints) → vector_config 提升."""
        from unittest.mock import MagicMock

        now = datetime(2026, 7, 13)
        orm = MagicMock()
        orm.id = "p1"
        orm.object_type_id = "ot1"
        orm.api_name = "profileEmbedding"
        orm.display_name = "Profile"
        orm.description = ""
        orm.data_type = "VECTOR"
        orm.is_primary_key = False
        orm.is_title_property = False
        orm.nullable = True
        orm.indexed = True
        orm.backing_catalog = "iceberg"
        orm.backing_schema = "ontology"
        orm.backing_table = "doc"
        orm.backing_column = "profile_embedding"
        orm.backing_dataset_api_name = "doc"
        orm.constraints = {
            "dimension": 384,
            "similarity_function": "cosine",
            "source_expression": ["title"],
        }
        orm.created_at = now
        orm.updated_at = now
        prop = PropertyDef.model_validate(orm)
        assert prop.vector_config is not None
        assert prop.vector_config.source_expression == ["title"]


class TestPropertyInputVectorConfig:
    """PropertyInput 透传 vector_config (batch create 路径)."""

    def test_property_input_with_vector_config(self):
        pi = PropertyInput(
            display_name="Profile Embedding",
            api_name="profileEmbedding",
            data_type="VECTOR",
            searchable=True,
            vector_config=VectorPropertyConfig(source_expression=["name", "desc"]),
        )
        assert pi.vector_config is not None
        assert pi.vector_config.source_expression == ["name", "desc"]

    def test_property_input_without_vector_config(self):
        pi = PropertyInput(
            display_name="Name",
            data_type="STRING",
        )
        assert pi.vector_config is None
