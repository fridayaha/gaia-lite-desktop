"""Adapter registry — maps engine type strings to adapter classes."""

from .base import EngineAdapter

_registry: dict[str, type[EngineAdapter]] = {}

# 未知 engine_type 时的默认引擎（向后兼容 Repo2 仅 hermes 的现状）
DEFAULT_ENGINE_TYPE = "HERMES"


def register_adapter(engine_type: str, adapter_cls: type[EngineAdapter]) -> None:
    """注册 adapter 类（key 大写化）。"""
    _registry[engine_type.upper()] = adapter_cls


def get_adapter(engine_type: str, **kwargs) -> EngineAdapter:
    """按 engine_type 查找并实例化 adapter。

    未知 engine_type → 回退到 DEFAULT_ENGINE_TYPE（HERMES），保证缺省链路可用。
    """
    cls = _registry.get(engine_type.upper()) or _registry[DEFAULT_ENGINE_TYPE]
    return cls(**kwargs)


def known_engine_types() -> list[str]:
    """所有已注册 engine_type。"""
    return list(_registry.keys())
