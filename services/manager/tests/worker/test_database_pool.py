"""连接池配置单测。

create_async_engine 显式设 pool_size/max_overflow/timeout/recycle/pre_ping,
非全默认 5/10/30(防长 IO 期间连接池快速耗尽)。
"""

import importlib
from unittest.mock import patch


def test_settings_default_pool_values():
    from pkg.common.config import Settings

    s = Settings()
    assert s.pool_size == 20
    assert s.pool_max_overflow == 40
    assert s.pool_timeout == 60
    assert s.pool_recycle == 1800
    assert s.pool_pre_ping is True


def test_settings_pool_env_override(monkeypatch):
    monkeypatch.setenv("UA_POOL_SIZE", "50")
    monkeypatch.setenv("UA_POOL_MAX_OVERFLOW", "100")
    from pkg.common.config import Settings

    s = Settings()
    assert s.pool_size == 50
    assert s.pool_max_overflow == 100


def test_engine_uses_pool_settings():
    """create_async_engine 传入 pool 参数(非全默认 5/10/30)。"""
    import pkg.common.database as dbmod

    # 保存 reload 前的原始对象（engine/async_session/get_db/get_manager_db 等）。
    # reload 会重建这些对象，但 app 路由注册时绑定的是原始 get_db，conftest 后续
    # `from pkg.common.database import get_db` 拿到新对象 → dependency_overrides
    # 键不匹配 → override 失效 → 污染后续测试（404/500）。故 reload 后必须恢复原始引用。
    _saved = {k: getattr(dbmod, k) for k in dir(dbmod) if not k.startswith("__")}
    try:
        with patch("sqlalchemy.ext.asyncio.create_async_engine") as spy:
            importlib.reload(dbmod)
            kw = spy.call_args.kwargs
    finally:
        for k, v in _saved.items():
            setattr(dbmod, k, v)

    assert kw["pool_size"] == 20
    assert kw["max_overflow"] == 40
    assert kw["pool_timeout"] == 60
    assert kw["pool_recycle"] == 1800
    assert kw["pool_pre_ping"] is True
