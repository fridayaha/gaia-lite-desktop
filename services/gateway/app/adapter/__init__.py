"""Engine adapter package — 注册三引擎 adapter。

运行时按 ``X-Engine-Type`` 头选 adapter（``registry.get_adapter``）。
"""

from .base import ENGINE_PORTS, EngineAdapter, build_engine_dns
from .dify import DifyAdapter
from .hermes import HermesAdapter
from .openclaw import OpenClawAdapter
from .registry import get_adapter, known_engine_types, register_adapter

# 注册三引擎
register_adapter("HERMES", HermesAdapter)
register_adapter("OPENCLAW", OpenClawAdapter)
register_adapter("DIFY", DifyAdapter)

__all__ = [
    "EngineAdapter",
    "build_engine_dns",
    "ENGINE_PORTS",
    "register_adapter",
    "get_adapter",
    "known_engine_types",
    "HermesAdapter",
    "OpenClawAdapter",
    "DifyAdapter",
]
