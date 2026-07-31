"""人设 (SOUL.md) 与技能管理 endpoint 单元测试。

覆盖：
  - _build_profile_config_yaml / _disabled_skill_names / _short_agent 纯函数
  - /persona/sync（fan-out SOUL.md，不重启）
  - /skills/install（解压 + 软链 + 重生成 config + rollout）
  - /skills/config/sync（开关，重写 config.yaml，不重启）
  - /skills/{name} DELETE（卸载 + rollout）
  - get_agent_status 返回 pod_name/pod_start_time/pod_phase
"""

import base64
import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker._common import (
    build_profile_config_yaml as _build_profile_config_yaml,
    config_template_path as _config_template_path,
    disabled_skill_names as _disabled_skill_names,
    short_agent as _short_agent,
)


# ═══════════════════════════════════════════════════════════
# 纯函数
# ═══════════════════════════════════════════════════════════

class TestPureFunctions:

    def test_short_agent(self):
        assert _short_agent("550e8400-e29b-41d4-a716-446655440000") == "550e8400"

    def test_disabled_skill_names(self):
        sc = {"skills": [
            {"name": "a", "enabled": True},
            {"name": "b", "enabled": False},
            {"name": "c", "enabled": False},
            {"name": "d"},  # 默认 enabled
        ]}
        assert _disabled_skill_names(sc) == ["b", "c"]

    def test_disabled_skill_names_empty(self):
        assert _disabled_skill_names({}) == []
        assert _disabled_skill_names({"skills": []}) == []

    def test_build_config_yaml_includes_disabled(self):
        mc = {"litellm": {"model": "gpt-4o"}}
        sc = {"skills": [{"name": "x", "enabled": False}, {"name": "y", "enabled": True}]}
        yaml = _build_profile_config_yaml(mc, sc)
        assert "provider: openai-api" in yaml
        assert "default: gpt-4o" in yaml
        assert "skills:" in yaml
        assert "- x" in yaml
        assert "tirith_enabled: false" in yaml
        # y 未禁用，不应出现
        assert "- y" not in yaml

    def test_build_config_yaml_no_disabled(self):
        yaml = _build_profile_config_yaml({}, {}, None)
        assert "disabled: []" in yaml

    def test_build_config_yaml_model_fallback(self):
        mc = {"litellm": {"model_group": "claude-group"}}
        yaml = _build_profile_config_yaml(mc, {})
        assert "default: claude-group" in yaml

    def test_config_template_path_resolves(self):
        """模板文件能在仓库内定位到（dev 路径解析）"""
        assert _config_template_path().is_file()

    def test_config_yaml_from_template_has_permission_defaults(self):
        """渲染输出含官方『全部放通』权限默认配置：approvals.mode: off + cron_mode: approve"""
        mc = {"litellm": {"model": "gpt-4o"}}
        yaml = _build_profile_config_yaml(mc, {})
        # 既有段保留
        assert "provider: openai-api" in yaml
        assert "default: gpt-4o" in yaml
        assert "tirith_enabled: false" in yaml
        assert "user_profile_enabled: true" in yaml
        # 官方全部放通配置
        assert "mode: off" in yaml
        assert "cron_mode: approve" in yaml
        # platform_toolsets.api_server（对齐 upstream c8707c2，修 #51967 终端工具集丢失）
        assert "platform_toolsets:" in yaml
        assert "hermes-api-server" in yaml
        assert "- terminal" in yaml
        # 不应再出现无效的 tirith:/security_policies: 块（非 Hermes 官方 key）
        assert "security_policies:" not in yaml
        assert "allow_all_policy" not in yaml

    def test_config_yaml_skills_block_rendered(self):
        """skills 段：多禁用项渲染为列表，空渲染为 disabled: []"""
        mc = {"litellm": {"model": "m"}}
        # 多禁用
        sc = {"disabled": ["a", "b"]}
        yaml = _build_profile_config_yaml(mc, sc)
        assert "skills:" in yaml
        assert "  disabled:" in yaml
        assert "- a" in yaml and "- b" in yaml
        # 空
        yaml_empty = _build_profile_config_yaml(mc, {})
        assert "disabled: []" in yaml_empty

    def test_config_yaml_plugins_block_when_langfuse_configured(self, monkeypatch):
        """manager 配了 Langfuse 凭据 → 渲染 plugins.enabled 激活 observability/langfuse 插件。

        该插件 opt-in：config.yaml 无此段则引擎不加载 hooks，Hermes 内层 trace 不写 Langfuse。
        """
        from app.worker import _common

        monkeypatch.setattr(_common.settings, "langfuse_public_key", "pk-lf-test")
        monkeypatch.setattr(_common.settings, "langfuse_secret_key", "sk-lf-test")
        yaml = _build_profile_config_yaml({"litellm": {"model": "m"}}, {})
        assert "plugins:" in yaml
        assert "  enabled:" in yaml
        assert "- observability/langfuse" in yaml
        assert "disabled: []" in yaml

    def test_config_yaml_no_plugins_block_when_langfuse_unconfigured(self, monkeypatch):
        """manager 未配 Langfuse 凭据 → 不渲染 plugins 段（空凭据 Pod 不加载插件）"""
        from app.worker import _common

        monkeypatch.setattr(_common.settings, "langfuse_public_key", "")
        monkeypatch.setattr(_common.settings, "langfuse_secret_key", "")
        yaml = _build_profile_config_yaml({"litellm": {"model": "m"}}, {})
        assert "plugins:" not in yaml
        assert "- observability/langfuse" not in yaml
        # 缺其一也不渲染
        monkeypatch.setattr(_common.settings, "langfuse_public_key", "pk-only")
        yaml_partial = _build_profile_config_yaml({"litellm": {"model": "m"}}, {})
        assert "plugins:" not in yaml_partial


# ═══════════════════════════════════════════════════════════
# /persona/sync
# ═══════════════════════════════════════════════════════════

AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"


def _pods_fixture():
    """模拟 _iter_agent_target_pods 返回：1 个 V2 pod，home=/opt/data/profiles/base。"""
    return [{
        "pod_name": "pod-aaa",
        "owner_agent_id": AGENT_ID,
        "scope_type": "ALL",
        "scope_target_id": None,
        "homes": ["/opt/data/profiles/base"],
    }]


def _v2_pods_fixture():
    """模拟 V2 多 profile pod：homes 含 base + 2 profile 目录。"""
    return [{
        "pod_name": "pod-aaa",
        "owner_agent_id": AGENT_ID,
        "scope_type": "ALL",
        "scope_target_id": None,
        "homes": ["/opt/data/profiles/prof-1", "/opt/data/profiles/prof-2", "/opt/data/profiles/base"],
    }]


class TestPersonaSync:

    async def test_sync_writes_soul_to_base_and_profiles(self, client, mock_k8s):
        mk = mock_k8s
        mk.exec_write_file_in_pod = AsyncMock()

        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value={
            "persona_config": {"system_prompt": "你是代码助手"},
        })), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/persona/sync")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "synced"
        assert body["synced"] == 1  # 1 个 V1 home
        paths = [c.args[1] for c in mk.exec_write_file_in_pod.call_args_list]
        assert "/opt/data/profiles/base/SOUL.md" in paths
        # 不重启
        mk.rollout_restart.assert_not_called()

    async def test_sync_empty_soul_clears(self, client, mock_k8s):
        """人设清空 → 写空 SOUL.md 覆盖旧人设（不跳过），返回 cleared。"""
        mk = mock_k8s
        mk.exec_write_file_in_pod = AsyncMock()
        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value={
            "persona_config": {"system_prompt": ""},
        })), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/persona/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"
        # 写空 SOUL.md（覆盖旧人设），不是跳过
        mk.exec_write_file_in_pod.assert_called_once()
        _pod, path, content = mk.exec_write_file_in_pod.call_args.args
        assert path.endswith("SOUL.md")
        assert content == ""

    async def test_sync_no_pods(self, client, mock_k8s):
        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value={
            "persona_config": {"system_prompt": "x"},
        })), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=[])):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/persona/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_pods"

    async def test_sync_no_agent(self, client, mock_k8s):
        """实例不存在 → no_agent。"""
        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value=None)):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/persona/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_agent"

    async def test_sync_v2_multi_home(self, client, mock_k8s):
        """V2 多 profile：fan-out 写入 base + 每个 profile 目录。"""
        mk = mock_k8s
        mk.exec_write_file_in_pod = AsyncMock()
        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value={
            "persona_config": {"system_prompt": "人设"},
        })), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_v2_pods_fixture())):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/persona/sync")
        assert resp.status_code == 200
        assert resp.json()["synced"] == 3  # 3 个 home
        paths = [c.args[1] for c in mk.exec_write_file_in_pod.call_args_list]
        assert "/opt/data/profiles/base/SOUL.md" in paths
        assert "/opt/data/profiles/prof-1/SOUL.md" in paths


# ═══════════════════════════════════════════════════════════
# /skills/install
# ═══════════════════════════════════════════════════════════

def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestSkillInstall:

    async def test_install_success(self, client, mock_k8s):
        mk = mock_k8s
        mk.exec_untar_to_in_pod = AsyncMock()
        mk.exec_command_in_pod = AsyncMock()
        mk.exec_write_file_in_pod = AsyncMock()
        mk.rollout_restart = AsyncMock()

        zip_bytes = _make_zip({
            "SKILL.md": "# demo skill",
            "manifest.json": json.dumps({"name": "demo", "version": "1.2.0"}),
        })
        zip_b64 = base64.b64encode(zip_bytes).decode()

        with patch("app.worker.config_skills._load_agent_configs", new=AsyncMock(return_value=(
            {"litellm": {"model": "gpt-4o"}}, {"skills": []}, "def-test"
        ))), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())), patch("app.worker.config_skills._load_definition_skill_record", new=AsyncMock(return_value=None)):
            resp = await client.post(
                f"/api/controller/agents/{AGENT_ID}/skills/install",
                json={"skill_name": "demo", "zip_b64": zip_b64},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "installed"
        # external_dirs 模型：解压到共享目录（per-Pod 一次，非 per-home）
        mk.exec_untar_to_in_pod.assert_called_once()
        # 原子换入：rm -rf {dest} && mv {dest_new} {dest}（替代旧 rm -rf; mkdir -p）。
        # 第一次 exec 是 _ensure_shared_skill_dir（groupadd/mkdir 共享父目录）。
        all_cmds = [c for call in mk.exec_command_in_pod.call_args_list for c in call.args[1]]
        assert any(
            c.startswith("rm -rf /opt/data/skills/def-test/demo && mv ")
            and "/opt/data/skills/def-test/demo.new." in c
            for c in all_cmds
        ), all_cmds
        # Hermes 热扫描，不重启
        mk.rollout_restart.assert_not_called()

    async def test_install_invalid_name(self, client, mock_k8s):
        zip_b64 = base64.b64encode(_make_zip({"SKILL.md": "x"})).decode()
        resp = await client.post(
            f"/api/controller/agents/{AGENT_ID}/skills/install",
            json={"skill_name": "../evil", "zip_b64": zip_b64},
        )
        assert resp.status_code == 400

    async def test_install_strips_top_dir(self, client, mock_k8s):
        """zip 内单一顶层目录应被剥离，SKILL.md 落在 store 根。"""
        mk = mock_k8s
        mk.exec_untar_to_in_pod = AsyncMock()
        mk.exec_command_in_pod = AsyncMock()
        mk.exec_write_file_in_pod = AsyncMock()
        mk.rollout_restart = AsyncMock()

        zip_bytes = _make_zip({
            "demo/SKILL.md": "# demo",
            "demo/manifest.json": json.dumps({"name": "demo", "version": "1.0.0"}),
        })
        zip_b64 = base64.b64encode(zip_bytes).decode()

        with patch("app.worker.config_skills._load_agent_configs", new=AsyncMock(return_value=({"litellm": {"model": "m"}}, {}, "def-test"))), \
             patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())), \
             patch("app.worker.config_skills._load_definition_skill_record", new=AsyncMock(return_value=None)):
            resp = await client.post(
                f"/api/controller/agents/{AGENT_ID}/skills/install",
                json={"skill_name": "demo", "zip_b64": zip_b64},
            )
        assert resp.status_code == 200
        # external_dirs 模型 + 原子换入：tar 内路径前缀为 {dest}.new.{uuid}
        # （顶层 demo/ 已剥离，无双重 demo/demo；运行时 rm -rf {dest} && mv 落到 {dest}）
        tar_arg = mk.exec_untar_to_in_pod.call_args.args[2]
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(tar_arg), mode="r:gz") as tf:
            names = tf.getnames()
        assert any(
            n.startswith("/opt/data/skills/def-test/demo.new.") and n.endswith("/SKILL.md")
            for n in names
        ), names
        assert not any("demo/demo" in n for n in names)


# ═══════════════════════════════════════════════════════════
# /skills/config/sync (开关)
# ═══════════════════════════════════════════════════════════

class TestSkillConfigSync:

    async def test_config_sync_rewrites_config_yaml_no_restart(self, client, mock_k8s):
        mk = mock_k8s
        mk.exec_write_file_in_pod = AsyncMock()
        mk.rollout_restart = AsyncMock()

        sc = {"skills": [{"name": "x", "enabled": False}]}
        inst_cfg = {
            "model_config": {"litellm": {"model": "gpt-4o"}},
            "skill_config": sc,
            "definition_id": "def-test",
            "runtime_config": {},  # browser_sandbox 关闭
        }
        with patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value=inst_cfg)), \
             patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())):
            resp = await client.post(f"/api/controller/agents/{AGENT_ID}/skills/config/sync")

        assert resp.status_code == 200
        # V1 单 home = 1 次 config.yaml 写入
        assert mk.exec_write_file_in_pod.call_count == 1
        written = mk.exec_write_file_in_pod.call_args_list[0].args[2]
        assert "disabled" in written and "- x" in written
        # browser_sandbox 关闭 → 不注 browser 段
        assert "- browser" not in written and "cdp_url" not in written
        # 开关不重启
        mk.rollout_restart.assert_not_called()


# ═══════════════════════════════════════════════════════════
# /skills/{name} DELETE (卸载)
# ═══════════════════════════════════════════════════════════

class TestSkillUninstall:

    async def test_uninstall_removes_and_restarts(self, client, mock_k8s):
        mk = mock_k8s
        mk.exec_command_in_pod = AsyncMock()
        mk.exec_write_file_in_pod = AsyncMock()
        mk.rollout_restart = AsyncMock()

        with patch("app.worker.config_skills._load_agent_configs", new=AsyncMock(return_value=(
            {"litellm": {"model": "gpt-4o"}}, {}, "def-test"
        ))), patch("app.worker.config_skills._load_instance_config", new=AsyncMock(return_value={
            "model_config": {"litellm": {"model": "gpt-4o"}},
            "skill_config": {},
            "definition_id": "def-test",
            "runtime_config": {},
        })), patch("app.worker.config_skills._iter_agent_target_pods", new=AsyncMock(return_value=_pods_fixture())):
            resp = await client.delete(f"/api/controller/agents/{AGENT_ID}/skills/demo")

        assert resp.status_code == 200
        assert resp.json()["status"] == "uninstalled"
        # external_dirs 模型：删共享目录 /opt/data/skills/{defid}/{name}/
        cmds = mk.exec_command_in_pod.call_args_list[0].args[1]
        assert any("rm -rf /opt/data/skills/def-test/demo" in c for c in cmds)
        # 热生效，不重启
        mk.rollout_restart.assert_not_called()

    async def test_uninstall_invalid_name(self, client, mock_k8s):
        resp = await client.delete(f"/api/controller/agents/{AGENT_ID}/skills/bad..name")
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════
# get_agent_status 暴露 pod 字段
# ═══════════════════════════════════════════════════════════

class TestAgentStatusPodFields:

    async def test_status_returns_pod_fields(self, client, mock_k8s, mock_db_session):
        from pkg.common.models import AgentDeployment, DeploymentStatus
        dep = MagicMock()
        dep.agent_id = AGENT_ID
        dep.status = DeploymentStatus.RUNNING
        dep.engine_url = "http://engine-hermes-x.unionagents.svc.cluster.local:8642"  # Pod 模式（非外部 Dify）
        dep.last_active_at = None
        dep.error_message = None

        result = MagicMock()
        result.scalar_one_or_none.return_value = dep
        mock_db_session.execute.return_value = result

        resp = await client.get(f"/api/controller/agents/{AGENT_ID}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pod_name"] == "pod-test"
        assert body["pod_phase"] == "Running"
        assert body["pod_start_time"] == "2026-06-06T00:00:00Z"
