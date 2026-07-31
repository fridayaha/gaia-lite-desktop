"""Dify 对接配置 + /pods 短路逻辑的纯函数测试。

不依赖 DB——用 MagicMock 构造 inst 对象，验证 _build_dify_config / _is_external_dify
/ mask_secret 的分支逻辑。端点集成测试（走 FastAPI test client）在 test_v3_api.py。
"""
from unittest.mock import MagicMock

import pytest

from app.api.agent_instances import _build_dify_config, _is_external_dify
from app.core.secrets import mask_secret
from app.models import EngineType


# ── mask_secret ──


def test_mask_secret_long_value():
    assert mask_secret("sk-abcdef123456") == "sk-a****3456"


def test_mask_secret_default_prefix_suffix():
    # 默认 prefix=4, suffix=4
    assert mask_secret("0123456789abcdef") == "0123****cdef"


def test_mask_secret_short_value():
    # 长度 <= prefix+suffix → 全 ****
    assert mask_secret("short") == "****"
    assert mask_secret("12345678") == "****"  # 正好 8 = 4+4


def test_mask_secret_none_empty():
    assert mask_secret(None) == "—"
    assert mask_secret("") == "—"


def test_mask_secret_custom_prefix_suffix():
    assert mask_secret("sk-abcdef123456", prefix=2, suffix=4) == "sk****3456"


# ── _is_external_dify ──


def _make_inst(
    engine_type=EngineType.HERMES,
    dify=None,
    has_definition=True,
    inst_dify_config=None,
):
    """构造 mock inst。

    优先取 inst.dify_config（新列，per-instance）；
    若 inst_dify_config 为 None 则回退到 definition.model_config.dify（fallback 路径）。

    用 inst_dify_config={} 显式空 dict 表示"新列存在但为空"，触发 fallback；
    用 inst_dify_config=None 表示"新列未设值"，同样触发 fallback。
    """
    inst = MagicMock()
    # 控制 inst.dify_config 的值（默认 None 走 fallback）
    inst.dify_config = inst_dify_config

    if not has_definition:
        inst.definition = None
        return inst
    inst.definition = MagicMock()
    inst.definition.engine_type = engine_type
    model_config = {"dify": dify} if dify is not None else {}
    inst.definition.model_config = model_config
    return inst


def test_is_external_dify_true_for_dify_with_base_url():
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        inst_dify_config={"base_url": "http://dify.example.com", "app_api_key": "k"},
    )
    assert _is_external_dify(inst) is True


def test_is_external_dify_false_for_dify_pod_mode():
    # base_url 缺省 = Pod 模式
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        inst_dify_config={"app_api_key": "k", "app_type": "chat"},
    )
    assert _is_external_dify(inst) is False


def test_is_external_dify_false_for_hermes():
    inst = _make_inst(engine_type=EngineType.HERMES)
    assert _is_external_dify(inst) is False


def test_is_external_dify_false_for_no_definition():
    inst = _make_inst(has_definition=False)
    assert _is_external_dify(inst) is False


def test_is_external_dify_false_for_dify_no_dify_section():
    # engine_type=DIFY 但 inst.dify_config 空 + definition.model_config 也无 dify
    inst = _make_inst(engine_type=EngineType.DIFY, dify=None)
    assert _is_external_dify(inst) is False


def test_is_external_dify_fallback_to_definition_model_config():
    """新列 inst.dify_config 空，回退到 definition.model_config.dify。"""
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        dify={"base_url": "http://fallback.example.com", "app_api_key": "k"},
        inst_dify_config=None,  # 新列未设值
    )
    assert _is_external_dify(inst) is True


# ── _build_dify_config ──


def test_build_dify_config_external_dify_masks_api_key():
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        inst_dify_config={
            "base_url": "http://dify.example.com",
            "app_api_key": "app-secret-key-12345",
            "app_type": "workflow",
            "app_id": "app-123",
            "app_name": "My Workflow",
            "source": "console",
        },
    )
    cfg = _build_dify_config(inst)
    assert cfg["base_url"] == "http://dify.example.com"
    assert cfg["app_type"] == "workflow"
    assert cfg["app_api_key"] == "app-****2345"  # 掩码
    assert cfg["app_id"] == "app-123"
    assert cfg["app_name"] == "My Workflow"
    assert cfg["source"] == "console"
    assert cfg["external"] is True


def test_build_dify_config_pod_mode_dify():
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        inst_dify_config={"app_api_key": "app-secret-key-12345", "app_type": "chat"},
    )
    cfg = _build_dify_config(inst)
    assert cfg["base_url"] == ""
    assert cfg["app_type"] == "chat"
    assert cfg["app_api_key"] == "app-****2345"
    assert cfg["external"] is False  # Pod 模式


def test_build_dify_config_hermes_returns_empty():
    inst = _make_inst(engine_type=EngineType.HERMES)
    assert _build_dify_config(inst) == {}


def test_build_dify_config_no_definition_returns_empty():
    inst = _make_inst(has_definition=False)
    assert _build_dify_config(inst) == {}


def test_build_dify_config_dify_without_dify_section_returns_empty():
    inst = _make_inst(engine_type=EngineType.DIFY, dify=None)
    assert _build_dify_config(inst) == {}


def test_build_dify_config_empty_app_api_key():
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        inst_dify_config={"base_url": "http://x", "app_api_key": "", "app_type": "agent"},
    )
    cfg = _build_dify_config(inst)
    assert cfg["app_api_key"] == "—"  # mask_secret 对空值返回 —
    assert cfg["external"] is True


def test_build_dify_config_fallback_to_definition():
    """inst.dify_config 空 → 回退到 definition.model_config.dify。

    覆盖历史快照数据：升级后未跑 backfill 也能继续工作。
    """
    inst = _make_inst(
        engine_type=EngineType.DIFY,
        dify={
            "base_url": "http://fallback.example.com",
            "app_api_key": "fallback-key-12345",
            "app_type": "chat",
        },
        inst_dify_config=None,  # 新列未设值 → 触发 fallback
    )
    cfg = _build_dify_config(inst)
    assert cfg["base_url"] == "http://fallback.example.com"
    assert cfg["app_type"] == "chat"
    assert cfg["app_api_key"] == "fall****2345"  # 仍走掩码
    assert cfg["external"] is True
