"""V3 三层模型单元测试：模型字段默认值、ENGINE_RUNTIMES 系统配置、schemas 校验。

Batch 1 仅引入新模型/表（与老表并存），service/API 切换在后续 batch。
本测试不依赖真实 DB —— 只验证 ORM 模型实例化默认值与 Pydantic schema 校验。
"""

import uuid

import pytest
from pydantic import ValidationError

from pkg.common.config import ENGINE_RUNTIMES, get_engine_runtime
from app.models import (
    AgentDefinition,
    AgentInstance,
    AgentStatus,
    AgentVersion,
    DefinitionStatus,
    EngineType,
    MarketplaceStatus,
    ResourcePool,
)
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionResponse,
    AgentInstanceCreate,
    AgentInstanceChannelCreate,
    ResourcePoolCreate,
    ResourcePoolUpdate,
)


USER_ID = uuid.uuid4()


# =========================================
# ENGINE_RUNTIMES 系统配置
# =========================================


def test_engine_runtimes_has_known_types():
    assert "HERMES" in ENGINE_RUNTIMES
    assert "OPENCLAW" in ENGINE_RUNTIMES
    for code, rt in ENGINE_RUNTIMES.items():
        assert "image" in rt and "port" in rt
        assert rt["port"] > 0


def test_get_engine_runtime_default_hermes():
    rt = get_engine_runtime("HERMES")
    assert rt["image"] == ENGINE_RUNTIMES["HERMES"]["image"]
    assert rt["port"] == 8642


def test_get_engine_runtime_none_falls_back_to_hermes():
    rt = get_engine_runtime(None)
    assert rt["port"] == 8642


def test_get_engine_runtime_unknown_falls_back_to_hermes():
    rt = get_engine_runtime("UNKNOWN_ENGINE")
    assert rt["port"] == 8642


def test_get_engine_runtime_global_env_override(monkeypatch):
    monkeypatch.setenv("UA_ENGINE_IMAGE", "registry.example.com/hermes:v1.2")
    rt = get_engine_runtime("HERMES")
    assert rt["image"] == "registry.example.com/hermes:v1.2"
    assert rt["port"] == 8642


def test_get_engine_runtime_per_engine_env_override(monkeypatch):
    monkeypatch.setenv("UA_ENGINE_IMAGE", "global-image:latest")
    monkeypatch.setenv("UA_HERMES_IMAGE", "hermes-specific:v2")
    rt = get_engine_runtime("HERMES")
    # 按引擎覆盖优先于全局
    assert rt["image"] == "hermes-specific:v2"


def test_get_engine_runtime_does_not_mutate_global(monkeypatch):
    monkeypatch.setenv("UA_ENGINE_IMAGE", "temp:latest")
    get_engine_runtime("HERMES")
    # 全局常量不被污染
    assert ENGINE_RUNTIMES["HERMES"]["image"] != "temp:latest"


# =========================================
# 模型默认值
# =========================================


def test_resource_pool_defaults():
    # Column.default 是 INSERT 时服务端默认（实例化后字段为 None，与现有 EngineInstance 一致）
    assert ResourcePool.min_cpu.default.arg == "100m"
    assert ResourcePool.max_cpu.default.arg == "2"
    assert ResourcePool.min_memory.default.arg == "256Mi"
    assert ResourcePool.max_memory.default.arg == "2Gi"
    assert ResourcePool.min_replicas.default.arg == 1
    assert ResourcePool.max_replicas.default.arg == 5
    assert ResourcePool.max_sessions_per_pod.default.arg == 20
    assert ResourcePool.auto_recycle.default.arg is True
    assert ResourcePool.idle_suspend_minutes.default.arg == 30
    assert ResourcePool.idle_destroy_hours.default.arg == 24


def test_agent_definition_defaults():
    assert AgentDefinition.status.default.arg == DefinitionStatus.DRAFT
    assert AgentDefinition.marketplace_status.default.arg == MarketplaceStatus.PRIVATE
    assert AgentDefinition.engine_type.default.arg == EngineType.HERMES
    assert AgentDefinition.avatar_color.default.arg == "#6366f1"
    d = AgentDefinition(name="客服助手", engine_type=EngineType.HERMES, created_by=USER_ID)
    assert d.current_version_id is None
    assert d.published_at is None


def test_agent_version_fields():
    v = AgentVersion(
        definition_id=uuid.uuid4(),
        version_no="1.0.0",
        persona_config={"system_prompt": "你是一个助手"},
        model_config={"model_group": "gpt-4o"},
        skill_config={},
        memory_config={},
        engine_type=EngineType.HERMES,
        created_by=USER_ID,
    )
    assert v.version_no == "1.0.0"
    assert v.persona_config["system_prompt"] == "你是一个助手"
    assert v.engine_type == EngineType.HERMES


def test_agent_instance_defaults():
    assert AgentInstance.status.default.arg == AgentStatus.DRAFT
    inst = AgentInstance(
        name="客服-内部版",
        group_id=uuid.uuid4(),
        definition_id=uuid.uuid4(),
        resource_pool_id=uuid.uuid4(),
        created_by=USER_ID,
    )
    assert inst.litellm_config is None
    assert inst.version_id is None


def test_definition_status_enum_values():
    assert DefinitionStatus.DRAFT == "DRAFT"
    assert DefinitionStatus.PUBLISHED == "PUBLISHED"


def test_marketplace_status_enum_values():
    assert MarketplaceStatus.PRIVATE == "PRIVATE"
    assert MarketplaceStatus.LISTED == "LISTED"


# =========================================
# Schemas 校验
# =========================================


def test_resource_pool_create_defaults():
    payload = ResourcePoolCreate(name="标准池")
    assert payload.max_sessions_per_pod == 20
    assert payload.idle_suspend_minutes == 30
    assert payload.auto_recycle is True


def test_resource_pool_create_name_required():
    with pytest.raises(ValidationError):
        ResourcePoolCreate(name="")


def test_resource_pool_update_all_optional():
    upd = ResourcePoolUpdate()
    assert upd.name is None
    assert upd.max_cpu is None


def test_agent_definition_create_defaults():
    payload = AgentDefinitionCreate(name="助手", group_id=uuid.uuid4())
    assert payload.engine_type == EngineType.HERMES
    assert payload.persona_config is None


def test_agent_instance_create_requires_definition_and_pool():
    with pytest.raises(ValidationError):
        AgentInstanceCreate(name="x")  # 缺 group_id / definition_id / resource_pool_id


def test_agent_instance_create_accepts_minimal():
    payload = AgentInstanceCreate(
        name="inst",
        definition_id=uuid.uuid4(),
        resource_pool_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
    )
    assert payload.version_id is None


def test_agent_instance_create_requires_group_id():
    with pytest.raises(ValidationError):
        AgentInstanceCreate(
            name="inst",
            definition_id=uuid.uuid4(),
            resource_pool_id=uuid.uuid4(),
        )  # 缺 group_id


def test_agent_instance_channel_create_validates_type():
    with pytest.raises(ValidationError):
        AgentInstanceChannelCreate(channel_type="slack", config={})


def test_agent_definition_response_parses_json_string():
    """DB 可能返回 JSON 字符串；Response 需解析为 dict。"""
    resp = AgentDefinitionResponse(
        id=uuid.uuid4(),
        name="助手",
        engine_type=EngineType.HERMES,
        status=DefinitionStatus.DRAFT,
        group_id=uuid.uuid4(),
        created_by=USER_ID,
        created_at="2026-06-22T00:00:00+00:00",
        updated_at="2026-06-22T00:00:00+00:00",
        persona_config='{"system_prompt": "hi"}',
        model_settings="{}",
        skill_config="{}",
        memory_config="{}",
    )
    assert resp.persona_config == {"system_prompt": "hi"}
    assert resp.model_settings == {}
