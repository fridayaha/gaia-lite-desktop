"""Worker 测试共享 fixtures。

kubernetes / minio 的 sys.modules mock 由父 conftest（tests/conftest.py）在导入 app
前统一注入，本 conftest 仅提供 worker 测试专用 fixtures。
"""

from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import ExitStack
from typing import AsyncGenerator
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport

# app 已由父 conftest 导入（含 worker router）；此处导入 worker 内部符号供测试断言
from app.main import app
from app.worker._common import build_engine_envs as _build_engine_envs
from app.worker.k8s_manager import k8s_manager
from app.worker.minio_archiver import archiver


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def mock_db_session():
    """模拟 async DB session，execute() 返回可控结果"""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def override_get_db(mock_db_session):
    """覆盖 FastAPI 的 get_manager_db 依赖"""
    async def _override():
        yield mock_db_session
    app.dependency_overrides.clear()
    from pkg.common.database import get_db
    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_k8s():
    """mock k8s_manager 的所有测试方法。

    k8s_manager 被 router/_common/engine_pods 等多个 worker 模块各自 import，
    须把所有引用处替换为同一个 MagicMock，否则端点迁出 router 后 patch 不生效。
    """
    mk = MagicMock()
    mk.exec_write_file = AsyncMock()
    mk.rollout_restart = AsyncMock()
    mk.patch_agent_envs = AsyncMock()
    mk.create_agent_engine = AsyncMock(return_value="engine-hermes-test1234")
    mk.get_pod_status = AsyncMock(return_value={
        "running": True,
        "phase": "Running",
        "reason": None,
        "pod_name": "pod-test",
        "pod_ip": "10.0.0.1",
        "start_time": "2026-06-06T00:00:00Z",
    })
    mk.wait_pod_ready = AsyncMock(return_value=True)
    mk.wait_engine_ready = AsyncMock(return_value=True)
    mk.get_service_url = AsyncMock(return_value="http://engine-hermes-test.unionagents.svc.cluster.local:8642")
    mk.scale_to_zero = AsyncMock()
    mk.resume = AsyncMock(return_value=True)
    mk.delete_agent_engine = AsyncMock()
    mk.exec_tar_data = AsyncMock(return_value=b"mock-tar-data")
    mk.exec_tar_data_by_pod = AsyncMock(return_value=b"mock-tar-data")
    mk.exec_untar_data = AsyncMock()
    mk.exec_hermes_command = AsyncMock(return_value="ready")
    mk.update_nginx_config = AsyncMock()
    mk.pvc_exists = MagicMock(return_value=False)  # 默认无 PVC
    mk._pvc_name = MagicMock(return_value="engine-data-test")
    mk.remove_finalizer = AsyncMock()
    mk.remove_finalizer_from_agent_pods = AsyncMock()
    mk.list_terminating_engine_pods = AsyncMock(return_value=[])
    # 容器默认在运行 → _reconcile_finalizers 走备份路径（非跳过/已死分支）
    mk.is_pod_container_running = AsyncMock(return_value=True)
    # 所有引用 k8s_manager 的 worker 模块统一替换为同一个 mk
    targets = [
        "app.worker._common.k8s_manager",
        "app.worker.config_skills.k8s_manager",
        "app.worker.engine_pods.k8s_manager",
        "app.worker.lifecycle.k8s_manager",
        "app.worker.lifecycle_service.k8s_manager",
        "app.worker.profiles.k8s_manager",
        "app.worker.scheduler.k8s_manager",
    ]
    with ExitStack() as stack:
        for t in targets:
            stack.enter_context(patch(t, new=mk))
        yield mk


@pytest.fixture
def mock_archiver():
    """mock archiver 的所有测试方法（router + config_skills 共享同一 mock）。"""
    ma = MagicMock()
    with ExitStack() as stack:
        for t in ("app.worker.config_skills.archiver", "app.worker.lifecycle.archiver", "app.worker.lifecycle_service.archiver", "app.worker.scheduler.archiver"):
            stack.enter_context(patch(t, new=ma))
        ma.save_engine_config = MagicMock()
        ma.get_engine_config = MagicMock(return_value={
            "config_yaml": "model:\n  provider: auto\n",
            "env": "OPENAI_API_KEY=sk-test\n",
        })
        ma.config_exists = MagicMock(return_value=True)
        ma.save_backup = MagicMock(return_value="backups/test/latest.tar.gz")
        ma.save_daily = MagicMock(return_value="backups/test/daily-20260629.tar.gz")
        ma.backup_exists = MagicMock(return_value=True)
        ma.archive_backup = MagicMock(return_value="archives/test/20260606T000000Z.tar.gz")
        ma.get_archive = MagicMock(return_value=b"mock-archive-data")
        ma.get_backup = MagicMock(return_value=b"mock-backup-data")
        ma.get_latest_daily = MagicMock(return_value=b"mock-backup-data")
        ma.delete_engine_config = MagicMock(return_value=0)
        ma.delete_daily_older_than = MagicMock(return_value=0)
        yield ma


@pytest.fixture
def client(override_get_db):
    """FastAPI 测试客户端"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def sample_agent_config():
    """标准的 LiteLLM 模型配置"""
    return {
        "system_prompt": "You are a helpful assistant.",
        "avatar_color": "#386bf5",
        "litellm": {
            "team_id": "default",
            "key_id": "k-1",
            "key": "sk-vkey-test123",
            "model_group": "gpt-4o-group",
            "model": "gpt-4o-group",
        },
    }


@pytest.fixture
def mock_query_result(mock_db_session):
    """设置 mock_db_session 的 execute 返回代理配置

    调用此 fixture 后，可以通过 mock_query_result.set_config(config_dict) 设定返回值
    """
    def _set_config(config_dict: dict):
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"config": __import__("json").dumps(config_dict) if config_dict else "{}"})
        ))
        mock_db_session.execute.return_value = mock_row

    _set_config.called_with = None

    def _track(*args, **kwargs):
        _set_config.called_with = (args, kwargs)
        return _set_config

    # 默认返回空配置
    _set_config({})
    return mock_db_session
