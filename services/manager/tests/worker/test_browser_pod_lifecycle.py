"""浏览器沙箱 Pod 生命周期 helper 测试（_common.ensure/suspend/delete/resume_browser_pods_*）。

mock k8s_manager + load_instance_config，验证：
- ensure：按 runtime_config.browser_sandbox.enabled 决定是否建 Pod + 写 internal_port_map["browsers"]
- suspend/delete/resume：遍历 deployment 的 browsers map 调对应 k8s 方法
- _set_browser_pod_in_port_map：set/remove 不破坏 ["profiles"] 端口映射
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _dep(port_map=None):
    return SimpleNamespace(internal_port_map=port_map or {"profiles": {"alice": 8644}})


class TestSetBrowserPodInPortMap:
    def test_set_writes_browsers_key_preserves_profiles(self):
        from app.worker._common import _set_browser_pod_in_port_map

        dep = _dep({"profiles": {"alice": 8644}})
        _set_browser_pod_in_port_map(dep, "alice", "browser-x-abc123")
        assert dep.internal_port_map["profiles"] == {"alice": 8644}  # 端口映射不受影响
        assert dep.internal_port_map["browsers"] == {"alice": "browser-x-abc123"}

    def test_set_multiple_profiles(self):
        from app.worker._common import _set_browser_pod_in_port_map

        dep = _dep()
        _set_browser_pod_in_port_map(dep, "alice", "browser-a")
        _set_browser_pod_in_port_map(dep, "bob", "browser-b")
        assert dep.internal_port_map["browsers"] == {"alice": "browser-a", "bob": "browser-b"}

    def test_remove_pod_none(self):
        from app.worker._common import _set_browser_pod_in_port_map

        dep = _dep({"profiles": {"alice": 8644}, "browsers": {"alice": "browser-x"}})
        _set_browser_pod_in_port_map(dep, "alice", None)
        assert dep.internal_port_map["browsers"] == {}
        assert dep.internal_port_map["profiles"] == {"alice": 8644}


class TestEnsureBrowserPodForProfile:
    async def test_enabled_creates_pod_and_writes_port_map(self):
        from app.worker._common import ensure_browser_pod_for_profile

        dep = _dep()
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value={
            "runtime_config": {"browser_sandbox": {"enabled": True}},
            "group_code": "yanfa",
        })):
            with patch("app.worker._common.k8s_manager.create_browser_pod",
                       new=AsyncMock(return_value={
                           "name": "browser-550e8400-abc123", "vnc_pw": "pw-secret",
                       })) as mk:
                ret = await ensure_browser_pod_for_profile("agent-x", "alice", dep, db=object())
                assert ret == "browser-550e8400-abc123"
                mk.assert_called_once_with("agent-x", "alice", "yanfa")
                assert dep.internal_port_map["browsers"]["alice"] == {
                    "pod": "browser-550e8400-abc123", "vnc_pw": "pw-secret",
                }

    async def test_disabled_returns_none_no_create(self):
        from app.worker._common import ensure_browser_pod_for_profile

        dep = _dep()
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value={
            "runtime_config": {},
        })):
            with patch("app.worker._common.k8s_manager.create_browser_pod",
                       new=AsyncMock()) as mk:
                ret = await ensure_browser_pod_for_profile("agent-x", "alice", dep, db=object())
                assert ret is None
                mk.assert_not_called()
                assert "browsers" not in (dep.internal_port_map or {})

    async def test_create_failure_returns_none_no_port_map_write(self):
        from app.worker._common import ensure_browser_pod_for_profile

        dep = _dep()
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value={
            "runtime_config": {"browser_sandbox": {"enabled": True}},
            "group_code": None,
        })):
            with patch("app.worker._common.k8s_manager.create_browser_pod",
                       new=AsyncMock(side_effect=RuntimeError("k8s down"))):
                ret = await ensure_browser_pod_for_profile("agent-x", "alice", dep, db=object())
                assert ret is None
                assert "browsers" not in (dep.internal_port_map or {})

    async def test_instance_not_found_returns_none(self):
        from app.worker._common import ensure_browser_pod_for_profile

        dep = _dep()
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value=None)):
            with patch("app.worker._common.k8s_manager.create_browser_pod",
                       new=AsyncMock()) as mk:
                ret = await ensure_browser_pod_for_profile("agent-x", "alice", dep, db=object())
                assert ret is None
                mk.assert_not_called()


class TestSuspendDeleteResumeBrowserPods:
    async def test_suspend_scales_all_profiles_to_zero(self):
        from app.worker._common import suspend_browser_pods_for_deployment

        dep = _dep({"profiles": {}, "browsers": {"alice": "b-a", "bob": "b-b"}})
        with patch("app.worker._common.k8s_manager.scale_browser_to_zero",
                   new=AsyncMock()) as mk:
            await suspend_browser_pods_for_deployment(dep, "agent-x")
            assert mk.call_count == 2
            called_profiles = {c.args[1] for c in mk.call_args_list}
            assert called_profiles == {"alice", "bob"}

    async def test_delete_deletes_all_profiles(self):
        from app.worker._common import delete_browser_pods_for_deployment

        dep = _dep({"profiles": {}, "browsers": {"alice": "b-a", "bob": "b-b"}})
        with patch("app.worker._common.k8s_manager.delete_browser_pod",
                   new=AsyncMock()) as mk:
            await delete_browser_pods_for_deployment(dep, "agent-x")
            assert mk.call_count == 2
            called_profiles = {c.args[1] for c in mk.call_args_list}
            assert called_profiles == {"alice", "bob"}

    async def test_resume_resumes_all_profiles(self):
        from app.worker._common import resume_browser_pods_for_deployment

        dep = _dep({"profiles": {}, "browsers": {"alice": "b-a"}})
        with patch("app.worker._common.k8s_manager.resume_browser_pod",
                   new=AsyncMock(return_value=True)) as mk:
            await resume_browser_pods_for_deployment(dep, "agent-x")
            mk.assert_called_once_with("agent-x", "alice")

    async def test_suspend_no_browsers_noop(self):
        from app.worker._common import suspend_browser_pods_for_deployment

        dep = _dep({"profiles": {"alice": 8644}})  # 无 browsers 键
        with patch("app.worker._common.k8s_manager.scale_browser_to_zero",
                   new=AsyncMock()) as mk:
            await suspend_browser_pods_for_deployment(dep, "agent-x")
            mk.assert_not_called()

    async def test_delete_partial_failure_continues(self):
        """一个 profile 删除失败不阻断其余"""
        from app.worker._common import delete_browser_pods_for_deployment

        dep = _dep({"profiles": {}, "browsers": {"alice": "b-a", "bob": "b-b"}})
        with patch("app.worker._common.k8s_manager.delete_browser_pod",
                   new=AsyncMock(side_effect=[RuntimeError("boom"), None])) as mk:
            await delete_browser_pods_for_deployment(dep, "agent-x")  # 不抛
            assert mk.call_count == 2


class TestCreateBrowserPodIdempotent:
    def test_existing_deployment_patches_replicas_only_not_selector(self, monkeypatch):
        """已存在 Deployment(409)→仅 patch replicas=1，不传 selector/template。

        回归：旧实现 patch 时传整个 V1Deployment（含 spec.selector），selector immutable
        → 422。老 browser Deployment 的 selector↔template↔Service 标签自洽，patch 任一会
        破坏匹配，故已存在时只 scale up。
        """
        import kubernetes.client.exceptions as k8s_exc
        from unittest.mock import MagicMock
        from app.worker.k8s_manager import k8s_manager

        monkeypatch.setattr(k8s_manager, "_create_browser_vnc_secret", lambda *a, **k: "pw-test")
        monkeypatch.setattr(k8s_manager, "_ensure_browser_network_policy", lambda *a, **k: None)

        apps = MagicMock()
        core = MagicMock()
        # conftest 把 ApiException 换成无 .status 的 fake，手动设 status（生产真实异常带 http_resp 有 .status）
        def _conflict():
            e = k8s_exc.ApiException()
            e.status = 409
            return e
        apps.create_namespaced_deployment.side_effect = _conflict()
        core.create_namespaced_persistent_volume_claim.side_effect = _conflict()
        core.create_namespaced_service.side_effect = _conflict()
        patch_calls: list = []
        apps.patch_namespaced_deployment.side_effect = (
            lambda name, namespace, body: patch_calls.append(body)
        )

        monkeypatch.setattr(k8s_manager, "apps_v1", apps)
        monkeypatch.setattr(k8s_manager, "core_v1", core)

        info = k8s_manager._create_browser_pod_sync("a" * 32, "profile-x", "default")

        assert info["name"].startswith("browser-")
        assert info["vnc_pw"] == "pw-test"
        # patch 恰好一次，body 只含 replicas（不含 selector/template，否则 422 immutable）
        assert len(patch_calls) == 1
        assert patch_calls[0] == {"spec": {"replicas": 1}}

    def test_new_deployment_creates_full_spec(self, monkeypatch):
        """新 Deployment(无 409)→create 全量 spec（含 selector），不调 patch。"""
        from unittest.mock import MagicMock
        from app.worker.k8s_manager import k8s_manager

        monkeypatch.setattr(k8s_manager, "_create_browser_vnc_secret", lambda *a, **k: "pw-test")
        monkeypatch.setattr(k8s_manager, "_ensure_browser_network_policy", lambda *a, **k: None)

        apps = MagicMock()
        core = MagicMock()
        created: list = []
        apps.create_namespaced_deployment.side_effect = lambda ns, dep: created.append(dep)
        monkeypatch.setattr(k8s_manager, "apps_v1", apps)
        monkeypatch.setattr(k8s_manager, "core_v1", core)

        info = k8s_manager._create_browser_pod_sync("b" * 32, "profile-y", None)

        assert info["vnc_pw"] == "pw-test"
        apps.patch_namespaced_deployment.assert_not_called()
        assert len(created) == 1
