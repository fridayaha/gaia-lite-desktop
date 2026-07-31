"""E2E 测试共享 fixtures

前置条件：
  1. k3s 集群运行中（make k8s-infra）
  2. MinIO 可访问（默认 http://localhost:9000，需 port-forward 或 in-cluster）
  3. 测试环境有 kubectl 权限

这些测试会创建真实的 K8s 资源，请勿在生产集群运行。
"""

import os
import pytest
from uuid import uuid4

from app.worker.k8s_manager import k8s_manager
from app.worker.minio_archiver import MinioArchiver


# ── E2E 测试开关 ────────────────────────────────────────

RUN_E2E = os.environ.get("RUN_E2E_TESTS", "").lower() in ("1", "true", "yes")


def pytest_collection_modifyitems(items):
    """默认跳过 e2e 标记的测试，除非 RUN_E2E_TESTS=1"""
    if RUN_E2E:
        return  # 允许所有测试
    for item in items:
        if item.get_closest_marker("e2e"):
            item.add_marker(pytest.mark.skip(
                reason="E2E tests disabled (set RUN_E2E_TESTS=1 to enable)"
            ))


# ── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def agent_id() -> str:
    """每个测试一个唯一 agent_id"""
    return str(uuid4())


@pytest.fixture
def archiver_client() -> MinioArchiver:
    """连接到部署环境 MinIO 的 archiver"""
    archiver = MinioArchiver()
    archiver._bucket_ensured = False
    return archiver


@pytest.fixture
async def k8s_client():
    """提供 K8sManager 实例（直接使用应用层的单例）"""
    return k8s_manager


@pytest.fixture(autouse=True)
async def cleanup_pods(agent_id: str):
    """测试结束后清理 K8s 资源（含 finalizer 移除，避免 Pod 卡 Terminating）"""
    yield
    try:
        # e2e 不跑 reconcile 循环：先移除所有 Pod（含 Terminating）的 finalizer，
        # 再删 Deployment，否则级联终止的 Pod 会因 finalizer 卡住。
        await k8s_manager.remove_finalizer_from_all_pods(agent_id)
        await k8s_manager.delete_agent_engine(agent_id)
        # 删除触发的级联终止 Pod 再清一次 finalizer 放行
        await k8s_manager.remove_finalizer_from_all_pods(agent_id)
    except Exception:
        pass  # 清理失败不影响其他测试
