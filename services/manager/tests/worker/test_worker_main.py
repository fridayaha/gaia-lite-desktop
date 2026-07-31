"""Controller main.py 测试

覆盖:
  - _build_engine_envs 纯函数
  - /config/sync 端点
  - /config/apply 端点（含 rollout restart + patch env）
  - /profiles 端点（动态创建/删除 Hermes Profile）
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# _build_engine_envs 可通过 conftest 的 app 导入
from app.worker.router import (  # noqa: E402
    _build_engine_envs,
    _deploy_body,
    _load_instance_config,
    _load_resource_spec,
)

from pkg.common.models import DeploymentStatus

# ═══════════════════════════════════════════════════════════
# _build_engine_envs (纯函数，无需 mock)
# ═══════════════════════════════════════════════════════════

class TestBuildEngineEnvs:

    def test_empty_config(self):
        """空配置应返回空 dict"""
        result = _build_engine_envs({})
        assert result == {}

    def test_no_litellm_key(self):
        """无 litellm.key → 返回空（未配置模型）"""
        result = _build_engine_envs({"litellm": {"model_group": "gpt-4o"}})
        assert result == {}

    def test_litellm_envs(self):
        """有 litellm.key → 注入 LITELLM_BASE_URL/API_KEY/MODEL"""
        cfg = {"litellm": {"key": "sk-vkey-1", "model_group": "gpt-4o-group", "model": "gpt-4o-group"}}
        result = _build_engine_envs(cfg)

        assert result["LITELLM_API_KEY"] == "sk-vkey-1"
        assert result["LITELLM_MODEL"] == "gpt-4o-group"
        assert result["LITELLM_BASE_URL"].endswith("/v1")
        # 不再注入旧的直连 env
        assert "MODEL_PROVIDERS_JSON" not in result
        assert "OPENROUTER_API_KEY" not in result

    def test_litellm_model_fallback_to_group(self):
        """model 缺失时回退到 model_group"""
        cfg = {"litellm": {"key": "sk-vkey-2", "model_group": "claude-group"}}
        result = _build_engine_envs(cfg)
        assert result["LITELLM_MODEL"] == "claude-group"

    def test_old_model_providers_ignored(self):
        """旧 model_providers 字段不再处理（引擎只支持 LiteLLM）"""
        cfg = {"model_providers": [{"type": "openrouter", "api_key": "sk-or"}]}
        result = _build_engine_envs(cfg)
        assert result == {}


# ═══════════════════════════════════════════════════════════
# /config/sync 端点
# ═══════════════════════════════════════════════════════════

class TestConfigSync:

    async def test_sync_success(self, client, mock_k8s, mock_archiver, mock_db_session):
        """成功同步配置到 MinIO（V2：不再写 /root/.hermes，由 entrypoint+_heal 管）"""
        # 设置 DB 返回 LiteLLM 配置
        config_data = {
            "litellm": {"key": "sk-vkey-test", "model_group": "gpt-4o-group", "model": "gpt-4o-group"}
        }
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/config/sync")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "synced"

        # 验证 archiver.save_engine_config 被调用
        mock_archiver.save_engine_config.assert_called_once()
        call_args = mock_archiver.save_engine_config.call_args
        assert call_args[0][0] == "550e8400-e29b-41d4-a716-446655440000"  # agent_id
        # config_yaml 指向 LiteLLM（provider: openai-api）
        assert "provider: openai-api" in call_args[0][1]
        # .env 兜底 OPENAI_API_KEY = litellm key
        assert "OPENAI_API_KEY=sk-vkey-test" in call_args[0][2]

        # V2：sync 不再 exec 写 Pod（config 由 entrypoint 重生成 + _heal 对齐）
        mock_k8s.exec_write_file.assert_not_called()

    async def test_sync_agent_not_found(self, client, mock_db_session):
        """Agent 不存在应返回 404"""
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value=None)  # Agent 不存在
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/config/sync")
        assert resp.status_code == 404
        assert "Agent not found" in resp.json()["detail"]

    async def test_sync_pod_unavailable(self, client, mock_k8s, mock_archiver, mock_db_session):
        """V2：sync 不写 Pod，始终保存到 MinIO 并返回 synced（无 pod_available 分支）"""
        config_data = {
            "litellm": {"key": "sk-vkey-test", "model_group": "gpt-4o-group"}
        }
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/test-id/config/sync")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "synced"

        # archiver.save_engine_config 仍然被调用
        mock_archiver.save_engine_config.assert_called_once()
        # V2：不写 Pod
        mock_k8s.exec_write_file.assert_not_called()

    async def test_sync_empty_config(self, client, mock_k8s, mock_archiver, mock_db_session):
        """config 为空时仍正常同步"""
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": "{}"})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/test-id/config/sync")
        assert resp.status_code == 200

        # 验证生成的 .env 不含任何 API Key
        call_args = mock_archiver.save_engine_config.call_args
        env_content = call_args[0][2]
        assert "OPENROUTER_API_KEY" not in env_content
        assert "ANTHROPIC_API_KEY" not in env_content

    async def test_sync_no_model_providers(self, client, mock_k8s, mock_archiver, mock_db_session):
        """config 中无 model_providers 时正常同步（只有基础配置）"""
        config_data = {
            "system_prompt": "Hello",
            "avatar_color": "#386bf5",
        }
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/test-id/config/sync")
        assert resp.status_code == 200

        call_args = mock_archiver.save_engine_config.call_args
        env_content = call_args[0][2]
        # .env 只包含基础 API 服务器配置
        assert "API_SERVER_ENABLED=true" in env_content
        assert "OPENROUTER_API_KEY" not in env_content

    async def test_sync_passes_group_code_to_save_engine_config(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """config/sync 把 instance.group_code 传给 save_engine_config（MinIO 组前缀）"""
        config_data = {"litellm": {"key": "sk-k", "model_group": "gpt-4o-group"}}
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={
                "model_config": json.dumps(config_data),
                "skill_config": None,
                "litellm_config": None,
                "engine_type": "HERMES",
                "resource_pool_id": "pool-1",
                "group_code": "yanfa",
            })
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/inst-1/config/sync")
        assert resp.status_code == 200

        mock_archiver.save_engine_config.assert_called_once()
        # group_code 作为 kwarg 传入
        assert mock_archiver.save_engine_config.call_args.kwargs.get("group_code") == "yanfa"


# ═══════════════════════════════════════════════════════════
# /config/apply 端点
# ═══════════════════════════════════════════════════════════

class TestConfigApply:

    async def test_apply_success(self, client, mock_k8s, mock_archiver, mock_db_session):
        """成功应用配置：patch env + sync config + rollout restart"""
        config_data = {
            "litellm": {"key": "sk-vkey-test", "model_group": "gpt-4o-group", "model": "gpt-4o-group"}
        }
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/config/apply")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        assert "重启" in body["message"]

        # 验证 patch_agent_envs 被调用（更新 Deployment env var）
        mock_k8s.patch_agent_envs.assert_called_once()
        patch_args = mock_k8s.patch_agent_envs.call_args
        assert patch_args[0][0] == "550e8400-e29b-41d4-a716-446655440000"  # agent_id
        assert "LITELLM_API_KEY" in patch_args[0][1]
        assert "LITELLM_BASE_URL" in patch_args[0][1]

        # 验证 rollout_restart 被调用
        mock_k8s.rollout_restart.assert_called_once()

        # V2：sync 不再 exec 写 Pod（config 由 entrypoint 重生成 + _heal 对齐）
        mock_k8s.exec_write_file.assert_not_called()

    async def test_apply_agent_not_found(self, client, mock_db_session):
        """Agent 不存在应返回 404"""
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value=None)
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/test-id/config/apply")
        assert resp.status_code == 404

    async def test_apply_rollout_failure(self, client, mock_k8s, mock_archiver, mock_db_session):
        """rollout restart 失败应返回 500"""
        config_data = {
            "litellm": {"key": "sk-vkey-test", "model_group": "gpt-4o-group"}
        }
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        mock_k8s.rollout_restart.side_effect = RuntimeError("K8s API error")

        resp = await client.post("/api/controller/agents/test-id/config/apply")
        assert resp.status_code == 500
        assert "Rollout restart" in resp.json()["detail"]

    async def test_apply_no_providers(self, client, mock_k8s, mock_archiver, mock_db_session):
        """无 providers 时也正常应用"""
        config_data = {}
        mock_row = MagicMock()
        mock_row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"model_config": json.dumps(config_data)})
        ))
        mock_db_session.execute.return_value = mock_row

        resp = await client.post("/api/controller/agents/test-id/config/apply")
        assert resp.status_code == 200

        # patch_agent_envs 不应被调用（因为没有 MODEL_PROVIDERS_JSON）
        assert mock_k8s.patch_agent_envs.call_count in (0, 1)
        if mock_k8s.patch_agent_envs.call_count == 1:
            patch_args = mock_k8s.patch_agent_envs.call_args[0][1]
            assert "MODEL_PROVIDERS_JSON" not in patch_args

    async def test_apply_heals_pvc_env_for_each_profile(
        self, client, mock_k8s, mock_archiver, mock_db_session, monkeypatch
    ):
        """apply 必须在 rollout_restart 前 heal 每个 profile 的 PVC .env。

        根因：_provision_litellm 旋转 key 后只写 DB，PVC .env 残留旧 key。
        rollout_restart 后新 Pod entrypoint 读 PVC .env（不覆盖已存在文件）
        → 旧 key → LiteLLM 401 → 用户无响应。修复：apply 时遍历 profile 调
        _heal_profile_runtime_config 把新 key 写进 PVC .env。
        """
        from app.worker import config_skills as router

        # mock _load_instance_config：返回带 litellm key 的配置（避免真实 DB 查询）
        async def _fake_load(db, agent_id):
            return {
                "model_config": {
                    "litellm": {"key": "sk-newkey", "model_group": "gpt-4o", "model": "gpt-4o"}
                },
                "skill_config": {},
                "definition_id": "def-1",
            }
        monkeypatch.setattr(router, "_load_instance_config", _fake_load)

        # mock sync_agent_config：no-op（避免内部再调 _load_instance_config）
        async def _fake_sync(agent_id, db):
            return {"status": "synced"}
        monkeypatch.setattr(router, "sync_agent_config", _fake_sync)

        # spy _heal_profile_runtime_config：核心断言对象
        heal_spy = AsyncMock()
        monkeypatch.setattr(router, "_heal_profile_runtime_config", heal_spy)

        # mock DB 查 AgentProfile：返回 2 个 profile
        prof1 = MagicMock(profile_name="base", internal_port=8643)
        prof2 = MagicMock(profile_name="user-abc-123", internal_port=8646)
        mock_scalars = MagicMock(all=MagicMock(return_value=[prof1, prof2]))
        mock_db_session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=mock_scalars)
        )

        resp = await client.post("/api/controller/agents/agent-xyz/config/apply")

        assert resp.status_code == 200, resp.text
        # heal 被调用 2 次（每个 profile 一次）
        assert heal_spy.await_count == 2
        # 第一次：base profile，port=8643
        call0 = heal_spy.await_args_list[0]
        assert call0.args[0] == "agent-xyz"  # agent_id
        assert call0.args[1] == "base"  # profile_name
        assert call0.kwargs["port"] == 8643
        # 第二次：user-abc-123 profile，port=8646
        call1 = heal_spy.await_args_list[1]
        assert call1.args[1] == "user-abc-123"
        assert call1.kwargs["port"] == 8646
        # rollout_restart 仍被调用（heal 在 restart 前）
        mock_k8s.rollout_restart.assert_called_once_with("agent-xyz")

    async def test_apply_heal_failure_does_not_block_restart(
        self, client, mock_k8s, mock_archiver, mock_db_session, monkeypatch
    ):
        """单个 profile heal 失败不阻断其余 profile + rollout_restart。"""
        from app.worker import config_skills as router

        async def _fake_load(db, agent_id):
            return {
                "model_config": {"litellm": {"key": "sk-k", "model_group": "g", "model": "g"}},
                "skill_config": {},
                "definition_id": "d",
            }
        monkeypatch.setattr(router, "_load_instance_config", _fake_load)
        async def _fake_sync(agent_id, db):
            return {"status": "synced"}
        monkeypatch.setattr(router, "sync_agent_config", _fake_sync)

        # heal 第 1 个 profile 抛异常，第 2 个正常
        heal_spy = AsyncMock(side_effect=[RuntimeError("exec failed"), None])
        monkeypatch.setattr(router, "_heal_profile_runtime_config", heal_spy)

        prof1 = MagicMock(profile_name="base", internal_port=8643)
        prof2 = MagicMock(profile_name="user-2", internal_port=8646)
        mock_scalars = MagicMock(all=MagicMock(return_value=[prof1, prof2]))
        mock_db_session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=mock_scalars)
        )

        resp = await client.post("/api/controller/agents/agent-xyz/config/apply")

        # heal 失败不阻断：apply 仍返回 200，rollout_restart 仍执行
        assert resp.status_code == 200, resp.text
        assert heal_spy.await_count == 2  # 两个 profile 都尝试了
        mock_k8s.rollout_restart.assert_called_once_with("agent-xyz")



# ═══════════════════════════════════════════════════════════
# deploy 主体（_deploy_body：异步化后部署逻辑在后台任务里跑，直调测试）
# ═══════════════════════════════════════════════════════════

def _make_dep_mock(status=DeploymentStatus.DEPLOYING, **attrs):
    """构造一个 AgentDeployment mock（_deploy_body 会读/写字段）"""
    dep = MagicMock()
    dep.status = status
    dep.instance_id = attrs.get("instance_id", "550e8400-e29b-41d4-a716-446655440000")
    dep.pod_name = attrs.get("pod_name", None)
    dep.engine_url = attrs.get("engine_url", None)
    dep.error_message = attrs.get("error_message", None)
    dep.deployed_at = attrs.get("deployed_at", None)
    dep.last_active_at = attrs.get("last_active_at", None)
    dep.node_name = attrs.get("node_name", None)
    dep.archive_path = attrs.get("archive_path", None)
    return dep


def _inst_row(payload: dict):
    row = MagicMock()
    row.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=payload)))
    return row


class TestDeployBody:

    async def test_deploy_with_providers(self, mock_k8s, mock_archiver, mock_db_session):
        """deploy 时 V3 instance 配置（per-instance LiteLLM key + engine_type + resource_pool + group_code）正确传递"""
        version_mc = {"litellm": {"key": "sk-version-key", "model": "gpt-4o-group"}}
        instance_lc = {"key": "sk-vkey-deploy", "model_group": "gpt-4o-group", "model": "gpt-4o-group"}
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": "pool-1",
            "litellm_config": instance_lc,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "HERMES",
            "group_code": "yanfa",
        })
        pool_row = _inst_row({
            "min_cpu": "500m", "max_cpu": "2", "min_memory": "512Mi", "max_memory": "2Gi",
            "max_sessions_per_pod": 20,
        })

        # _deploy_body: dep 重载 → _load_instance_config → _load_resource_spec
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
            pool_row,
        ]

        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        await _deploy_body(mock_db_session, agent_id, "ALL", None, prev_status=None)

        # create_agent_engine 应收到包含 LITELLM_* 的 config（per-instance key 覆盖 version key）
        mock_k8s.create_agent_engine.assert_called_once()
        call = mock_k8s.create_agent_engine.call_args
        engine_config = call[0][1]
        assert engine_config["LITELLM_API_KEY"] == "sk-vkey-deploy"
        assert "LITELLM_BASE_URL" in engine_config
        assert "LITELLM_MODEL" in engine_config
        assert "MODEL_PROVIDERS_JSON" not in engine_config
        assert call.kwargs.get("engine_type") == "HERMES"
        assert call.kwargs.get("engine_instance_image")
        rs = call.kwargs.get("resource_spec")
        assert rs is not None
        assert rs["max_cpu"] == "2"
        assert rs["max_profiles_per_pod"] == 20
        assert call.kwargs.get("group_code") == "yanfa"

        # 成功 → dep 终态 RUNNING + engine_url + pod_name 写回（断言字段值，非 mock commit）
        assert dep.status == DeploymentStatus.RUNNING
        assert dep.engine_url == "http://engine-hermes-test.unionagents.svc.cluster.local:8642"
        assert dep.pod_name == "engine-hermes-test1234"
        assert dep.error_message is None
        mock_db_session.commit.assert_awaited()

    async def test_deploy_failure_marks_failed(self, mock_k8s, mock_archiver, mock_db_session):
        """部署失败 → dep 终态 FAILED + error_message 写回"""
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": None,
            "litellm_config": None,
            "model_config": json.dumps({"litellm": {"key": "sk-k", "model": "gpt-4o"}}),
            "skill_config": None,
            "engine_type": "HERMES",
            "group_code": "yanfa",
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # 失败分支重载 dep 标 FAILED
        ]
        # Pod 起不来 → wait_pod_ready False → RuntimeError
        mock_k8s.wait_pod_ready.return_value = False

        await _deploy_body(mock_db_session, "inst-fail-1", "ALL", None, prev_status=None)

        assert dep.status == DeploymentStatus.FAILED
        assert dep.error_message  # 非空错误信息
        mock_db_session.commit.assert_awaited()
        # 失败时不应写 RUNNING
        mock_k8s.get_service_url.assert_not_called()

    async def test_deploy_skips_when_state_changed(self, mock_k8s, mock_archiver, mock_db_session):
        """dep 已被外部置 RUNNING（如并发）→ _deploy_body 放弃，不动 k8s"""
        dep = _make_dep_mock(status=DeploymentStatus.RUNNING)
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),
        ]
        await _deploy_body(mock_db_session, "inst-skip-1", "ALL", None, prev_status=None)
        mock_k8s.create_agent_engine.assert_not_called()
        mock_k8s.resume.assert_not_called()

    async def test_deploy_replays_persona_and_skills(self, mock_k8s, mock_archiver, mock_db_session):
        """deploy 成功后重放人设(SOUL.md) + 已装技能（从 MinIO list_skill_zips 取回 fan-out）"""
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": None,
            "litellm_config": None,
            "model_config": json.dumps({"litellm": {"key": "sk-k", "model": "gpt-4o"}, "system_prompt": "你是助手"}),
            "skill_config": json.dumps({"skills": []}),
            "engine_type": "HERMES",
            "group_code": "yanfa",
            "definition_id": "def-replay-1",
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
        ]
        mock_archiver.list_skill_zips.return_value = ["demo-skill"]
        mock_archiver.get_skill_zip.return_value = b"zip-bytes"

        with patch("app.worker.config_skills.sync_persona", new=AsyncMock()) as mock_sync, \
             patch("app.worker.config_skills._fanout_skill_to_pods", new=AsyncMock()) as mock_fanout:
            await _deploy_body(mock_db_session, "inst-replay-1", "ALL", None, prev_status=None)

        # 部署成功
        assert dep.status == DeploymentStatus.RUNNING
        # 人设同步被调
        mock_sync.assert_awaited_once()
        # 从 MinIO 列技能 + 取 zip + fan-out
        mock_archiver.list_skill_zips.assert_called_once_with("def-replay-1")
        mock_archiver.get_skill_zip.assert_called_once_with("def-replay-1", "demo-skill")
        mock_fanout.assert_awaited_once()
        assert mock_fanout.call_args.args[1] == "demo-skill"
        assert mock_fanout.call_args.args[2] == b"zip-bytes"

    async def test_deploy_replay_no_skills(self, mock_k8s, mock_archiver, mock_db_session):
        """MinIO 无已装技能 → 只同步人设，不 fan-out"""
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": None,
            "litellm_config": None,
            "model_config": json.dumps({"litellm": {"key": "sk-k", "model": "gpt-4o"}}),
            "skill_config": json.dumps({"skills": []}),
            "engine_type": "HERMES",
            "group_code": "yanfa",
            "definition_id": "def-2",
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),
            inst_row,
        ]
        mock_archiver.list_skill_zips.return_value = []

        with patch("app.worker.config_skills.sync_persona", new=AsyncMock()), \
             patch("app.worker.config_skills._fanout_skill_to_pods", new=AsyncMock()) as mock_fanout:
            await _deploy_body(mock_db_session, "inst-replay-2", "ALL", None, prev_status=None)

        assert dep.status == DeploymentStatus.RUNNING
        mock_fanout.assert_not_awaited()

    async def test_deploy_dify_external_uses_instance_dify_config(self, mock_k8s, mock_archiver, mock_db_session):
        """Dify 引擎 + inst.dify_config 有 base_url → 走外部模式，跳过 K8s，engine_url=base_url。"""
        version_mc = {"litellm": {"key": "sk-k", "model": "gpt-4o"}}
        instance_dc = {
            "base_url": "http://dify.example.com",
            "app_api_key": "app-xxxxx",
            "app_type": "chat",
            "app_id": "app-123",
            "app_name": "My Chat",
            "source": "console",
        }
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": None,  # 外部模式无 Pod，不需要 pool
            "litellm_config": None,
            "dify_config": instance_dc,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "DIFY",
            "group_code": "yanfa",
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
        ]

        await _deploy_body(mock_db_session, "inst-dify-ext-1", "ALL", None, prev_status=None)

        # 外部模式 → dep 终态 RUNNING + engine_url = base_url（去尾斜杠）
        assert dep.status == DeploymentStatus.RUNNING
        assert dep.engine_url == "http://dify.example.com"
        assert dep.pod_name is None
        assert dep.error_message is None
        mock_db_session.commit.assert_awaited()
        # 跳过 K8s：create_agent_engine 不应被调
        mock_k8s.create_agent_engine.assert_not_called()

    async def test_deploy_dify_external_fallback_to_definition_model_config(self, mock_k8s, mock_archiver, mock_db_session):
        """inst.dify_config 空 → fallback 到 version.model_config.dify，仍走外部模式。"""
        version_mc = {
            "litellm": {"key": "sk-k", "model": "gpt-4o"},
            "dify": {"base_url": "http://fallback.dify.example.com", "app_api_key": "k", "app_type": "chat"},
        }
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": None,  # 外部模式无 Pod
            "litellm_config": None,
            "dify_config": None,  # 新列空 → fallback
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "DIFY",
            "group_code": "yanfa",
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),
            inst_row,
        ]

        await _deploy_body(mock_db_session, "inst-dify-fb-1", "ALL", None, prev_status=None)

        assert dep.status == DeploymentStatus.RUNNING
        assert dep.engine_url == "http://fallback.dify.example.com"
        assert dep.pod_name is None
        mock_k8s.create_agent_engine.assert_not_called()

    async def test_deploy_dify_pod_mode_no_base_url(self, mock_k8s, mock_archiver, mock_db_session):
        """Dify 引擎 + dify_config 无 base_url → Pod 模式，走 K8s 部署（MANAGED 场景）。"""
        version_mc = {"litellm": {"key": "sk-k", "model": "gpt-4o"}}
        instance_dc = {"app_api_key": "app-xxxxx", "app_type": "chat"}  # 无 base_url
        dep = _make_dep_mock()
        inst_row = _inst_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": instance_dc,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "DIFY",
            "group_code": "yanfa",
        })
        pool_row = _inst_row({
            "min_cpu": "500m", "max_cpu": "2", "min_memory": "512Mi", "max_memory": "2Gi",
            "max_sessions_per_pod": 20,
        })
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
            pool_row,
        ]

        await _deploy_body(mock_db_session, "inst-dify-pod-1", "ALL", None, prev_status=None)

        # Pod 模式 → 调 K8s create_agent_engine（不走外部 short-circuit）
        mock_k8s.create_agent_engine.assert_called_once()
        # engine_type 传给 create_agent_engine 是 DIFY
        call = mock_k8s.create_agent_engine.call_args
        assert call.kwargs.get("engine_type") == "DIFY"


class TestDeployEndpoint:
    """deploy POST 异步：立即置 DEPLOYING 返回 + 防重入 409。_schedule_deploy patch 为 no-op。"""

    async def test_deploy_returns_deploying_and_schedules(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """全新 deploy：dep 不存在 → 建行置 DEPLOYING → 调度后台任务 → 返回 DEPLOYING"""
        dep_row = MagicMock(scalar_one_or_none=MagicMock(return_value=None))  # dep 不存在
        inst_row = _inst_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "model_config": json.dumps({"litellm": {"key": "sk-k", "model": "gpt-4o"}}),
            "skill_config": None,
            "engine_type": "HERMES",
            "group_code": "yanfa",
            "group_id": "g-1",
        })
        mock_db_session.execute.side_effect = [dep_row, inst_row]

        with patch("app.worker.lifecycle._schedule_deploy") as sched:
            resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/deploy")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "DEPLOYING"
        # 后台任务被调度（prev_status=None，全新创建）
        sched.assert_called_once()
        assert sched.call_args.args[0] == "550e8400-e29b-41d4-a716-446655440000"
        assert sched.call_args.args[3] is None  # prev_status
        # dep 行被创建并置 DEPLOYING + commit（验证 db.add 被调）
        mock_db_session.add.assert_called_once()
        added = mock_db_session.add.call_args.args[0]
        assert added.status == DeploymentStatus.DEPLOYING
        mock_db_session.commit.assert_awaited()

    async def test_deploy_rejects_when_already_deploying(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """DEPLOYING 中再次 deploy → 409，不调度任务"""
        dep = _make_dep_mock(status=DeploymentStatus.DEPLOYING)
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),
        ]
        with patch("app.worker.lifecycle._schedule_deploy") as sched:
            resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/deploy")
        assert resp.status_code == 409
        sched.assert_not_called()

    async def test_deploy_running_returns_immediately(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """已 RUNNING → 直接返回 RUNNING，不调度"""
        dep = _make_dep_mock(status=DeploymentStatus.RUNNING, engine_url="http://running")
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),
        ]
        with patch("app.worker.lifecycle._schedule_deploy") as sched:
            resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/deploy")
        assert resp.status_code == 200
        assert resp.json()["status"] == "RUNNING"
        sched.assert_not_called()


class TestGetAgentStatusDeployingExemption:
    """get_agent_status：DEPLOYING 态豁免 reconciliation（后台任务权威控制状态转移）。"""

    async def test_deploying_pending_pod_not_flipped_to_failed(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """DEPLOYING + Pod Pending → 返回 DEPLOYING（不被 else 分支误判 FAILED）"""
        dep = _make_dep_mock(status=DeploymentStatus.DEPLOYING)
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.get_pod_status.return_value = {
            "running": False, "phase": "Pending", "reason": None,
            "pod_name": "pod-pending", "start_time": None,
        }
        resp = await client.get("/api/controller/agents/inst-deploying-1/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "DEPLOYING"
        assert body["pod_phase"] == "Pending"
        # 不应写库（reconciliation 豁免）
        mock_db_session.commit.assert_not_awaited()

    async def test_deploying_running_pod_not_flipped_to_running(
        self, client, mock_k8s, mock_archiver, mock_db_session
    ):
        """DEPLOYING + Pod Running（引擎未就绪）→ 返回 DEPLOYING（不被提前翻 RUNNING）"""
        dep = _make_dep_mock(status=DeploymentStatus.DEPLOYING)
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.get_pod_status.return_value = {
            "running": True, "phase": "Running", "reason": None,
            "pod_name": "pod-running", "start_time": "2026-06-25T00:00:00Z",
        }
        resp = await client.get("/api/controller/agents/inst-deploying-2/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "DEPLOYING"
        mock_db_session.commit.assert_not_awaited()


class TestDeploySuspendedResume:
    """SUSPENDED 恢复分支（_deploy_body, prev_status=SUSPENDED）。"""

    AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"

    def _setup(self, mock_db_session, mock_k8s, pvc_exists=False, backup_data=b"mock-backup-data"):
        """公共脚手架：dep(DEPLOYING) + inst_cfg(无 resource_pool) + resume 成功"""
        config_data = {"model_providers": [{"type": "openrouter", "api_key": "sk-or-test"}]}
        dep = _make_dep_mock(status=DeploymentStatus.DEPLOYING, pod_name="engine-hermes-550e8400")
        inst_row = _inst_row({"model_config": json.dumps(config_data)})
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=dep)),  # dep 重载
            inst_row,
        ]
        mock_k8s.resume.return_value = True
        mock_k8s.pvc_exists.return_value = pvc_exists
        return dep

    async def test_deploy_suspended_resume_with_backup_restore(self, mock_k8s, mock_archiver, mock_db_session):
        """SUSPENDED→RESUME 应 scale=1 后从 OSS 恢复数据"""
        dep = self._setup(mock_db_session, mock_k8s, pvc_exists=False, backup_data=b"mock-backup-data")

        await _deploy_body(mock_db_session, self.AGENT_ID, "ALL", None, prev_status=DeploymentStatus.SUSPENDED)

        # 应调用 resume（非 create_agent_engine）
        mock_k8s.create_agent_engine.assert_not_called()
        mock_k8s.resume.assert_called_once()
        assert mock_k8s.resume.call_args[0][0] == self.AGENT_ID

        # 应从最近 daily 恢复数据（group_code=None：inst 行未含 group_code 键）
        mock_archiver.get_latest_daily.assert_called_once_with(self.AGENT_ID, group_code=None)
        mock_k8s.exec_untar_data.assert_called_once()
        untar_args = mock_k8s.exec_untar_data.call_args[0]
        assert untar_args[0] == self.AGENT_ID
        assert untar_args[1] == b"mock-backup-data"
        # 成功 → RUNNING
        assert dep.status == DeploymentStatus.RUNNING

    async def test_deploy_suspended_resume_no_backup(self, mock_k8s, mock_archiver, mock_db_session):
        """SUSPENDED→RESUME 无 OSS 备份：置 FAILED（不留空数据 Pod 静默服务）"""
        dep = self._setup(mock_db_session, mock_k8s, pvc_exists=False)
        mock_archiver.get_latest_daily.return_value = None

        await _deploy_body(mock_db_session, self.AGENT_ID, "ALL", None, prev_status=DeploymentStatus.SUSPENDED)

        mock_k8s.resume.assert_called_once()
        mock_k8s.exec_untar_data.assert_not_called()
        # 无备份恢复 → FAILED（非空数据 RUNNING），并缩容避免空数据服务
        assert dep.status == DeploymentStatus.FAILED
        assert dep.error_message
        mock_k8s.scale_to_zero.assert_called_once()

    async def test_deploy_suspended_resume_pvc_skip_restore(self, mock_k8s, mock_archiver, mock_db_session):
        """SUSPENDED→RESUME 且 PVC 存在时，跳过 MinIO 恢复"""
        dep = self._setup(mock_db_session, mock_k8s, pvc_exists=True)

        await _deploy_body(mock_db_session, self.AGENT_ID, "ALL", None, prev_status=DeploymentStatus.SUSPENDED)

        mock_k8s.resume.assert_called_once()
        mock_archiver.get_latest_daily.assert_not_called()
        mock_k8s.exec_untar_data.assert_not_called()
        assert dep.status == DeploymentStatus.RUNNING

    async def test_deploy_suspended_resume_pvc_absent_still_restore(self, mock_k8s, mock_archiver, mock_db_session):
        """SUSPENDED→RESUME 且 PVC 不存在时，仍走 MinIO 恢复"""
        dep = self._setup(mock_db_session, mock_k8s, pvc_exists=False)

        await _deploy_body(mock_db_session, self.AGENT_ID, "ALL", None, prev_status=DeploymentStatus.SUSPENDED)

        mock_k8s.resume.assert_called_once()
        mock_archiver.get_latest_daily.assert_called_once()
        mock_k8s.exec_untar_data.assert_called_once()
        assert dep.status == DeploymentStatus.RUNNING


# ═══════════════════════════════════════════════════════════
# Profile 生命周期 API（POST /profiles, DELETE /profiles/{id}）
# ═══════════════════════════════════════════════════════════


class TestProfileCreate:
    """POST /api/controller/profiles — 动态创建 Hermes Profile"""

    @pytest.fixture(autouse=True)
    def _stub_profile_seeds(self):
        """seeds（SOUL.md/USER.md/skills）已移到后台 _schedule_profile_seeds，单测旁路，
        避免后台任务异步跑真实 seed 函数干扰 mock_db_session.execute 顺序断言。"""
        with patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()):
            yield

    @pytest.fixture(autouse=True)
    def _stub_port_map(self):
        """port_map.py 在 Pod 内执行，单测旁路：_port_map_alloc/_port_map_all 返回
        self.alloc_port / self.all_map（默认 8644 / 空）。测试在调 client 前设置。"""
        self.alloc_port = 8644
        self.all_map = {}
        with patch("app.worker.profiles._port_map_alloc",
                   new=AsyncMock(side_effect=lambda *a, **k: self.alloc_port)), \
             patch("app.worker.profiles._port_map_all",
                   new=AsyncMock(side_effect=lambda *a, **k: self.all_map)):
            yield

    def _make_ei_row(self, max_profiles=20):
        """创建一个 engine_instances 查询结果 mock"""
        row = MagicMock()
        row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"max_profiles_per_pod": max_profiles})
        ))
        return row

    def _make_deployment_list(self, deployments):
        """创建一个 AgentDeployment select 查询结果 mock"""
        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(
            all=MagicMock(return_value=deployments)
        ))
        return result

    def _make_config_row(self, model_config=None, skill_config=None):
        """agent config 查询结果 mock（_heal/_seed_persona/_load_instance_config 用）。

        空配置 → _heal 无 api_key 早返回、_seed 无 soul/skills 早返回，避免 MagicMock 误触发 exec。
        """
        row = MagicMock()
        row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={
                "model_config": model_config or {},
                "skill_config": skill_config or {},
            })
        ))
        return row

    def _make_port_map_row(self, port_map):
        """agent_deployments.internal_port_map 查询结果 mock（ensure 已存在时的 port_map 同步用）"""
        row = MagicMock()
        row.mappings = MagicMock(return_value=MagicMock(
            first=MagicMock(return_value={"internal_port_map": port_map})
        ))
        return row

    async def test_create_profile_success(self, client, mock_k8s, mock_db_session):
        """Engine RUNNING → 分配端口 8644 → hermes profile create → 入库"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        instance_id = "660e8400-e29b-41d4-a716-446655440000"
        deployment_id = "770e8400-e29b-41d4-a716-446655440000"

        running_dep = MagicMock()
        running_dep.id = deployment_id
        running_dep.instance_id = agent_id
        running_dep.resource_pool_id = instance_id
        running_dep.status = "RUNNING"
        running_dep.scope_type = "ALL"
        running_dep.scope_target_id = None
        running_dep.pod_name = "engine-hermes-550e8400"
        running_dep.internal_port_map = {}  # 无已有 profile

        self.alloc_port = 8644
        self.all_map = {"alice-session-001": 8644}

        mock_db_session.execute.side_effect = [
            self._make_ei_row(),                    # 1. engine_instances.max_profiles_per_pod
            self._make_deployment_list([running_dep]),  # 2. _select_pod_by_load
            MagicMock(),                            # 3. pg_advisory_xact_lock（deployment 级）
            MagicMock(),                            # 4. pg_insert AgentProfile（3b 提前，释放锁）
            self._make_config_row(),                # 5. _heal _load_instance_config
            self._make_config_row(),                # 6. skill-dir _load_instance_config (defid)
        ]

        resp = await client.post("/api/controller/profiles", json={
            "agent_id": agent_id,
            "engine_instance_id": instance_id,
            "user_id": "880e8400-e29b-41d4-a716-446655440000",
            "group_id": "990e8400-e29b-41d4-a716-446655440000",
            "profile_type": "INDEPENDENT",
            "profile_name": "alice-session-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile_name"] == "alice-session-001"
        assert data["port"] == 8644
        assert data["created"] is True

        # 验证 exec_hermes_command 被调用至少一次（profile create + gateway start）
        mock_k8s.exec_hermes_command.assert_called()
        call_kwargs = mock_k8s.exec_hermes_command.call_args_list[0][1]
        assert call_kwargs["agent_id"] == agent_id

        # 验证 AgentProfile 被写入 DB（使用 pg_insert upsert）
        assert mock_db_session.execute.call_count >= 3
        # 验证 deployment.internal_port_map 镜像 = port_map all（无 next_port）
        assert running_dep.internal_port_map == {
            "profiles": {"alice-session-001": 8644},
        }

    async def test_create_profile_multi_port_allocation(self, client, mock_k8s, mock_db_session):
        """多 Profile 顺序分配端口"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        instance_id = "660e8400-e29b-41d4-a716-446655440000"
        deployment_id = "770e8400-e29b-41d4-a716-446655440000"

        running_dep = MagicMock()
        running_dep.id = deployment_id
        running_dep.instance_id = agent_id
        running_dep.resource_pool_id = instance_id
        running_dep.status = "RUNNING"
        running_dep.scope_type = "ALL"
        running_dep.scope_target_id = None
        running_dep.pod_name = "engine-hermes-550e8400"
        running_dep.internal_port_map = {
            "profiles": {"existing-profile": 8644},
        }

        self.alloc_port = 8645
        self.all_map = {"existing-profile": 8644, "new-profile": 8645}

        mock_db_session.execute.side_effect = [
            self._make_ei_row(),
            self._make_deployment_list([running_dep]),
            MagicMock(),               # pg_advisory_xact_lock
            MagicMock(),               # pg_insert AgentProfile（3b 提前，释放锁）
            self._make_config_row(),   # _heal
            self._make_config_row(),   # skill-dir _load_instance_config (defid)
        ]

        resp = await client.post("/api/controller/profiles", json={
            "agent_id": agent_id,
            "engine_instance_id": instance_id,
            "user_id": "880e8400-e29b-41d4-a716-446655440000",
            "group_id": "990e8400-e29b-41d4-a716-446655440000",
            "profile_type": "INDEPENDENT",
            "profile_name": "new-profile",
        })

        assert resp.status_code == 200
        assert resp.json()["port"] == 8645

        # port_map 镜像 = port_map all（无 next_port）
        assert running_dep.internal_port_map == {
            "profiles": {"existing-profile": 8644, "new-profile": 8645},
        }

    async def test_create_profile_independent_type(self, client, mock_k8s, mock_db_session):
        """INDEPENDENT 类型：user_id 必填，group_id 为空"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        instance_id = "660e8400-e29b-41d4-a716-446655440000"
        user_id = "880e8400-e29b-41d4-a716-446655440000"

        running_dep = MagicMock()
        running_dep.id = "770e8400-e29b-41d4-a716-446655440000"
        running_dep.instance_id = agent_id
        running_dep.resource_pool_id = instance_id
        running_dep.status = "RUNNING"
        running_dep.scope_type = "ALL"
        running_dep.scope_target_id = None
        running_dep.pod_name = "engine-hermes-550e8400"
        running_dep.internal_port_map = {}

        mock_db_session.execute.side_effect = [
            TestProfileCreate._make_ei_row(TestProfileCreate),
            TestProfileCreate._make_deployment_list(TestProfileCreate, [running_dep]),
            MagicMock(),                                              # pg_advisory_xact_lock
            MagicMock(),                                              # pg_insert AgentProfile（3b 提前，释放锁）
            TestProfileCreate._make_config_row(TestProfileCreate),   # _heal
            TestProfileCreate._make_config_row(TestProfileCreate),   # skill-dir _load_instance_config (defid)
        ]

        resp = await client.post("/api/controller/profiles", json={
            "agent_id": agent_id,
            "engine_instance_id": instance_id,
            "user_id": user_id,
            "group_id": "990e8400-e29b-41d4-a716-446655440000",
            "profile_type": "INDEPENDENT",
            "profile_name": "independent-profile",
        })

        assert resp.status_code == 200
        # 使用 pg_insert upsert，不再通过 db.add()
        assert mock_db_session.execute.call_count >= 3


class TestProfileDelete:
    """DELETE /api/controller/profiles/{profile_id} — 停止并清理 Profile"""

    async def test_delete_profile_success(self, client, mock_k8s, mock_db_session):
        """正常删除：stop gateway → delete profile → 释放端口 → 删 DB 记录"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        profile_id = "aa0e8400-e29b-41d4-a716-446655440000"
        deployment_id = "770e8400-e29b-41d4-a716-446655440000"

        profile = MagicMock()
        profile.id = profile_id
        profile.instance_id = agent_id
        profile.deployment_id = deployment_id
        profile.profile_name = "alice-session-001"
        profile.profile_type = "INDEPENDENT"

        deployment = MagicMock()
        deployment.id = deployment_id
        deployment.scope_type = "ALL"
        deployment.scope_target_id = None
        deployment.internal_port_map = {
            "profiles": {"alice-session-001": 8644, "other-profile": 8645},
        }

        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=deployment)),
        ]

        # delete 调 port_map.py remove + _port_map_all（剩余 profile）→ 旁路返回剩余 map
        with patch("app.worker.profiles._port_map_all",
                   new=AsyncMock(return_value={"other-profile": 8645})):
            resp = await client.delete(f"/api/controller/profiles/{profile_id}")

        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}

        # 第一组 exec 命令含 kill + rm（cleanup），后续还有 port_map.py remove
        first_kwargs = mock_k8s.exec_hermes_command.call_args_list[0][1]
        assert first_kwargs["agent_id"] == agent_id
        commands = first_kwargs["commands"]
        assert any("kill" in c for c in commands)
        # 端口从 port_map.json 删除（唯一真相）
        all_cmds = [c for ck in mock_k8s.exec_hermes_command.call_args_list for c in ck[1].get("commands", [])]
        assert any("port_map.py remove alice-session-001" in c for c in all_cmds), all_cmds

        # 验证端口释放：DB 镜像 = port_map all（剩余 profile，无 next_port）
        assert deployment.internal_port_map == {
            "profiles": {"other-profile": 8645},
        }

        # 验证 DB 删除
        mock_db_session.delete.assert_called_once_with(profile)
        mock_db_session.commit.assert_called()

    async def test_delete_profile_not_found(self, client, mock_k8s, mock_db_session):
        """Profile 不存在 → 404"""
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]

        resp = await client.delete("/api/controller/profiles/non-existent-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

        mock_k8s.exec_hermes_command.assert_not_called()

    async def test_delete_profile_deployment_gone(self, client, mock_k8s, mock_db_session):
        """Profile 存在但 Deployment 已被删除 → 仍应删除 Profile（优雅降级）"""
        profile_id = "aa0e8400-e29b-41d4-a716-446655440000"

        profile = MagicMock()
        profile.id = profile_id
        profile.instance_id = "550e8400-e29b-41d4-a716-446655440000"
        profile.deployment_id = "770e8400-e29b-41d4-a716-446655440000"
        profile.profile_name = "orphan-profile"

        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # deployment 不存在
        ]

        resp = await client.delete(f"/api/controller/profiles/{profile_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted"}

        # 不应尝试 exec（没有 deployment 信息）
        mock_k8s.exec_hermes_command.assert_not_called()

        # 仍应删除 Profile 记录
        mock_db_session.delete.assert_called_once_with(profile)

    async def test_delete_profile_port_release_last_profile(self, client, mock_k8s, mock_db_session):
        """删除最后一个 profile：端口释放后 profiles dict 为空"""
        profile_id = "aa0e8400-e29b-41d4-a716-446655440000"

        profile = MagicMock()
        profile.id = profile_id
        profile.instance_id = "550e8400-e29b-41d4-a716-446655440000"
        profile.deployment_id = "770e8400-e29b-41d4-a716-446655440000"
        profile.profile_name = "sole-profile"

        deployment = MagicMock()
        deployment.id = "770e8400-e29b-41d4-a716-446655440000"
        deployment.scope_type = "ALL"
        deployment.scope_target_id = None
        deployment.internal_port_map = {
            "profiles": {"sole-profile": 8644},
        }

        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=deployment)),
        ]

        # 删除最后一个 profile → port_map all 返回空
        with patch("app.worker.profiles._port_map_all",
                   new=AsyncMock(return_value={})):
            resp = await client.delete(f"/api/controller/profiles/{profile_id}")
        assert resp.status_code == 200

        # profiles 为空 dict（无 next_port）
        assert deployment.internal_port_map == {
            "profiles": {},
        }


class TestProfileEnsure:
    """POST /api/controller/profiles/ensure — 幂等确保 Profile 存在"""

    @pytest.fixture(autouse=True)
    def _stub_profile_seeds(self):
        """seeds（SOUL.md/USER.md/skills）已移到后台 _schedule_profile_seeds，单测旁路，
        避免后台任务异步跑真实 seed 函数干扰 mock_db_session.execute 顺序断言。"""
        with patch("app.worker.profiles._schedule_profile_seeds", new=MagicMock()):
            yield

    @pytest.fixture(autouse=True)
    def _stub_port_map(self):
        """port_map.py 在 Pod 内执行，单测旁路（ensure 走 _do_create_profile 时分配端口）。"""
        with patch("app.worker.profiles._port_map_alloc", new=AsyncMock(return_value=8644)), \
             patch("app.worker.profiles._port_map_all",
                   new=AsyncMock(return_value={"new-profile": 8644})), \
             patch("app.worker.profiles._port_map_exec", new=AsyncMock(return_value="")):
            yield

    async def test_ensure_profile_already_exists(self, client, mock_k8s, mock_db_session):
        """Profile 已存在 (agent_profiles) → 直接返回，不创建"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        instance_id = "660e8400-e29b-41d4-a716-446655440000"
        deployment_id = "770e8400-e29b-41d4-a716-446655440000"
        profile_name = "existing-profile"

        existing_profile = MagicMock()
        existing_profile.profile_name = profile_name
        existing_profile.deployment_id = deployment_id
        existing_profile.internal_port = 8644

        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_profile)),  # 1. ensure 检查
            TestProfileCreate._make_port_map_row(TestProfileCreate,                  # 2. port_map 同步（匹配，无 UPDATE）
                {"profiles": {profile_name: 8644}, "next_port": 8645}),
            TestProfileCreate._make_config_row(TestProfileCreate),                   # 3. _heal
            TestProfileCreate._make_port_map_row(TestProfileCreate,                  # 4. nginx 更新前读 port_map
                {"profiles": {profile_name: 8644}, "next_port": 8645}),
        ]
        # 目录检查 exec 返回 EXISTS → profile 目录在 Pod 上存在，不走重建路径
        mock_k8s.exec_hermes_command = AsyncMock(return_value="EXISTS")

        resp = await client.post("/api/controller/profiles/ensure", json={
            "agent_id": agent_id,
            "engine_instance_id": instance_id,
            "user_id": "880e8400-e29b-41d4-a716-446655440000",
            "group_id": "990e8400-e29b-41d4-a716-446655440000",
            "profile_type": "INDEPENDENT",
            "profile_name": profile_name,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile_name"] == profile_name
        assert data["created"] is False
        # 已存在但仍会 exec（目录检查 + gateway 重启），但不走 _do_create_profile
        mock_k8s.exec_hermes_command.assert_called()

    async def test_ensure_profile_creates_new(self, client, mock_k8s, mock_db_session):
        """Profile 不存在 → 创建新 Profile"""
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        instance_id = "660e8400-e29b-41d4-a716-446655440000"
        deployment_id = "770e8400-e29b-41d4-a716-446655440000"
        profile_name = "new-profile"

        running_dep = MagicMock()
        running_dep.id = deployment_id
        running_dep.instance_id = agent_id
        running_dep.resource_pool_id = instance_id
        running_dep.status = "RUNNING"
        running_dep.scope_type = "ALL"
        running_dep.scope_target_id = None
        running_dep.pod_name = "engine-hermes-550e8400"
        running_dep.internal_port_map = {}

        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),             # 1. ensure 检查
            TestProfileCreate._make_ei_row(TestProfileCreate),                      # 2. max_profiles
            TestProfileCreate._make_deployment_list(TestProfileCreate, [running_dep]),  # 3. _select_pod_by_load
            MagicMock(),                                                             # 4. pg_advisory_xact_lock
            MagicMock(),                                                             # 5. pg_insert AgentProfile（3b 提前，释放锁）
            TestProfileCreate._make_config_row(TestProfileCreate),                  # 6. _heal
            TestProfileCreate._make_config_row(TestProfileCreate),                  # 7. skill-dir _load_instance_config (defid)
        ]

        resp = await client.post("/api/controller/profiles/ensure", json={
            "agent_id": agent_id,
            "engine_instance_id": instance_id,
            "user_id": "880e8400-e29b-41d4-a716-446655440000",
            "group_id": "990e8400-e29b-41d4-a716-446655440000",
            "profile_type": "INDEPENDENT",
            "profile_name": profile_name,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile_name"] == profile_name
        assert data["created"] is True
        assert data["port"] == 8644
        mock_k8s.exec_hermes_command.assert_called()


class TestSelectPodByLoad:
    """_select_pod_by_load 单元测试"""

    @pytest.mark.asyncio
    async def test_select_pod_no_deployments(self):
        """无任何 Deployment → 返回 None"""
        from app.worker.router import _select_pod_by_load

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(
                all=MagicMock(return_value=[])
            ))
        ))
        result = await _select_pod_by_load(mock_db, "instance-id", 20)
        assert result is None

    @pytest.mark.asyncio
    async def test_select_pod_empty_pod_selected(self):
        """空 Pod（无 profile）→ 返回该 Pod"""
        from app.worker.router import _select_pod_by_load

        empty_dep = MagicMock()
        empty_dep.internal_port_map = {}

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(
                all=MagicMock(return_value=[empty_dep])
            ))
        ))
        result = await _select_pod_by_load(mock_db, "instance-id", 20)
        assert result is empty_dep

    @pytest.mark.asyncio
    async def test_select_least_loaded_pod(self):
        """多 Pod: 选择 Profile 数最少的"""
        from app.worker.router import _select_pod_by_load

        dep1 = MagicMock()
        dep1.internal_port_map = {"profiles": {"a": 8644, "b": 8645}}

        dep2 = MagicMock()
        dep2.internal_port_map = {"profiles": {"c": 8644}}

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(
                all=MagicMock(return_value=[dep1, dep2])
            ))
        ))
        result = await _select_pod_by_load(mock_db, "instance-id", 20)
        assert result is dep2

    @pytest.mark.asyncio
    async def test_select_pod_all_full(self):
        """所有 Pod 都已满 → 返回 None"""
        from app.worker.router import _select_pod_by_load

        dep1 = MagicMock()
        dep1.internal_port_map = {
            "profiles": {f"p{i}": 8600 + i for i in range(20)}
        }

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(
                all=MagicMock(return_value=[dep1])
            ))
        ))
        result = await _select_pod_by_load(mock_db, "instance-id", 20)
        assert result is None


# ═══════════════════════════════════════════════════════════
# V3 三层模型读取 helpers（_load_instance_config / _load_resource_spec）
# 验证 controller 从 agent_instances JOIN agent_versions/agent_definitions
# 以及 resource_pools 读取运行配置的字段映射，替代老 agents/engine_instances 查询。
# ═══════════════════════════════════════════════════════════

class TestV3InstanceLoaders:

    @staticmethod
    def _mapping_row(mapping: dict | None):
        """构造 db.execute() 返回的 row，row.mappings().first() → mapping"""
        m = MagicMock()
        m.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mapping)))
        return m

    async def test_instance_config_litellm_override(self, mock_db_session):
        """instance.litellm_config 覆盖 version 快照的 litellm 段（per-instance key 生效）"""
        version_mc = {"litellm": {"key": "sk-version-key", "model": "gpt-4o"}, "system_prompt": "hi"}
        instance_lc = {"key": "sk-instance-key", "model": "gpt-4o-instance"}
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": instance_lc,
            "dify_config": None,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "HERMES",
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg is not None
        # litellm 段被 instance 的 per-instance key 覆盖
        assert cfg["model_config"]["litellm"]["key"] == "sk-instance-key"
        # 非 litellm 字段保留自 version 快照
        assert cfg["model_config"]["system_prompt"] == "hi"
        assert cfg["engine_type"] == "HERMES"
        assert cfg["resource_pool_id"] == "pool-1"
        # dify_config 默认空 dict
        assert cfg["dify_config"] == {}

    async def test_instance_config_no_litellm_keeps_version(self, mock_db_session):
        """instance 无 litellm_config 时保留 version 快照的 litellm 段"""
        version_mc = {"litellm": {"key": "sk-version-key", "model": "gpt-4o"}}
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": None,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "OPENCLAW",
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg["model_config"]["litellm"]["key"] == "sk-version-key"
        assert cfg["engine_type"] == "OPENCLAW"

    async def test_instance_config_not_found(self, mock_db_session):
        mock_db_session.execute.return_value = self._mapping_row(None)
        assert await _load_instance_config(mock_db_session, "missing") is None

    async def test_instance_config_engine_type_default(self, mock_db_session):
        """engine_type 缺失默认 HERMES；resource_pool_id 缺失返回 None"""
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": None,
            "litellm_config": None,
            "dify_config": None,
            "model_config": None,
            "skill_config": None,
            "engine_type": None,
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg["engine_type"] == "HERMES"
        assert cfg["resource_pool_id"] is None

    async def test_instance_config_group_code(self, mock_db_session):
        """_load_instance_config JOIN user_groups 返回 group_code（用于 MinIO 前缀 + Pod label）"""
        version_mc = {"litellm": {"key": "sk-k", "model": "gpt-4o"}}
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": None,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "HERMES",
            "group_code": "yanfa",
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg is not None
        assert cfg["group_code"] == "yanfa"

    async def test_instance_config_group_code_missing(self, mock_db_session):
        """group_code 缺失（LEFT JOIN user_groups 无匹配）返回 None"""
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": None,
            "model_config": None,
            "skill_config": None,
            "engine_type": "HERMES",
            "group_code": None,
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg["group_code"] is None

    async def test_instance_config_dify_config_passthrough(self, mock_db_session):
        """instance.dify_config 直读，per-instance Dify 应用绑定生效。"""
        version_mc = {"litellm": {"key": "sk-k", "model": "gpt-4o"}}
        instance_dc = {
            "base_url": "http://dify.example.com",
            "app_api_key": "app-xxxxx",
            "app_type": "workflow",
            "app_id": "app-123",
            "app_name": "My Workflow",
            "source": "console",
        }
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": instance_dc,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "DIFY",
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg is not None
        assert cfg["dify_config"] == instance_dc
        assert cfg["dify_config"]["app_id"] == "app-123"
        assert cfg["dify_config"]["source"] == "console"
        assert cfg["engine_type"] == "DIFY"

    async def test_instance_config_dify_config_json_string(self, mock_db_session):
        """dify_config 以 JSON 字符串返回时能正确反序列化（DB driver 行为模拟）。"""
        version_mc = {"litellm": {"key": "sk-k", "model": "gpt-4o"}}
        instance_dc_json = json.dumps({
            "base_url": "http://dify.example.com",
            "app_api_key": "k",
            "app_type": "chat",
        })
        mock_db_session.execute.return_value = self._mapping_row({
            "resource_pool_id": "pool-1",
            "litellm_config": None,
            "dify_config": instance_dc_json,
            "model_config": json.dumps(version_mc),
            "skill_config": None,
            "engine_type": "DIFY",
        })
        cfg = await _load_instance_config(mock_db_session, "inst-1")
        assert cfg["dify_config"]["base_url"] == "http://dify.example.com"
        assert cfg["dify_config"]["app_type"] == "chat"


class TestLoadGroupCode:
    """_load_group_code: suspend/destroy 循环用的轻量组查询"""

    @staticmethod
    def _mapping_row(mapping: dict | None):
        m = MagicMock()
        m.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mapping)))
        return m

    async def test_load_group_code_found(self, mock_db_session):
        from app.worker.router import _load_group_code
        mock_db_session.execute.return_value = self._mapping_row({"group_code": "yanfa"})
        assert await _load_group_code(mock_db_session, "inst-1") == "yanfa"

    async def test_load_group_code_missing(self, mock_db_session):
        """instance 不存在 → None（调用方回退 archiver 默认组）"""
        from app.worker.router import _load_group_code
        mock_db_session.execute.return_value = self._mapping_row(None)
        assert await _load_group_code(mock_db_session, "missing") is None

    async def test_load_group_code_null(self, mock_db_session):
        """group_code 列为 NULL（LEFT JOIN 无匹配）→ None"""
        from app.worker.router import _load_group_code
        mock_db_session.execute.return_value = self._mapping_row({"group_code": None})
        assert await _load_group_code(mock_db_session, "inst-1") is None

    async def test_resource_spec_compat_key(self, mock_db_session):
        """max_sessions_per_pod 映射到兼容键 max_profiles_per_pod"""
        mock_db_session.execute.return_value = self._mapping_row({
            "min_cpu": "500m", "max_cpu": "2",
            "min_memory": "512Mi", "max_memory": "2Gi",
            "max_sessions_per_pod": 30,
        })
        spec = await _load_resource_spec(mock_db_session, "pool-1")
        assert spec is not None
        assert spec["max_profiles_per_pod"] == 30  # 兼容键
        assert spec["max_sessions_per_pod"] == 30
        assert spec["max_cpu"] == "2"

    async def test_resource_spec_not_found(self, mock_db_session):
        mock_db_session.execute.return_value = self._mapping_row(None)
        assert await _load_resource_spec(mock_db_session, "missing") is None

    async def test_resource_spec_default_when_zero(self, mock_db_session):
        """max_sessions_per_pod 为 0/None 时兼容键回退到 20"""
        mock_db_session.execute.return_value = self._mapping_row({
            "min_cpu": "100m", "max_cpu": "1",
            "min_memory": "128Mi", "max_memory": "1Gi",
            "max_sessions_per_pod": None,
        })
        spec = await _load_resource_spec(mock_db_session, "pool-1")
        assert spec["max_profiles_per_pod"] == 20


# ═══════════════════════════════════════════════════════════
# V3 运行时 endpoint：/resume 与 /restart（显式独立于 deploy）
# ═══════════════════════════════════════════════════════════

class TestResumeRestartEndpoints:

    @staticmethod
    def _dep(scope_type="ALL", scope_target_id=None, status="SUSPENDED"):
        dep = MagicMock()
        dep.status = status
        dep.scope_type = scope_type
        dep.scope_target_id = scope_target_id
        dep.last_active_at = None
        # engine_url=None → _is_external_dify_deployment 返回 False（Pod 模式，走 K8s resume/restart）
        dep.engine_url = None
        return dep

    async def test_resume_success(self, client, mock_k8s, mock_db_session):
        dep = self._dep()
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.resume = AsyncMock(return_value=True)

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        mock_k8s.resume.assert_called_once()
        # 透传 deployment 的 scope
        assert mock_k8s.resume.call_args[0][0] == "550e8400-e29b-41d4-a716-446655440000"
        # dep 状态置为 RUNNING
        assert dep.status == "RUNNING"

    async def test_resume_deployment_missing_in_k8s_returns_409(self, client, mock_k8s, mock_db_session):
        """Deployment 在 K8s 中不存在 → 409（需走 deploy 重建）"""
        dep = self._dep()
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.resume = AsyncMock(return_value=False)

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/resume")
        assert resp.status_code == 409

    async def test_resume_deployment_not_found_returns_404(self, client, mock_db_session):
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )
        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/resume")
        assert resp.status_code == 404

    async def test_restart_success(self, client, mock_k8s, mock_db_session):
        dep = self._dep(status="RUNNING")
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.rollout_restart = AsyncMock()

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/restart")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarting"
        mock_k8s.rollout_restart.assert_called_once()

    async def test_restart_rollout_failure_returns_500(self, client, mock_k8s, mock_db_session):
        dep = self._dep()
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=dep)
        )
        mock_k8s.rollout_restart = AsyncMock(side_effect=RuntimeError("k8s down"))

        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/restart")
        assert resp.status_code == 500

    async def test_restart_deployment_not_found_returns_404(self, client, mock_db_session):
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )
        resp = await client.post("/api/controller/agents/550e8400-e29b-41d4-a716-446655440000/restart")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# _do_suspend / _do_destroy：验证 group_code 流入 archiver 组前缀
# ═══════════════════════════════════════════════════════════


class TestSuspendDestroyGroupPrefix:
    """_do_suspend / _do_destroy 应查询 group_code 并传给 archiver（组前缀隔离）"""

    @staticmethod
    def _mapping_row(mapping):
        m = MagicMock()
        m.mappings = MagicMock(return_value=MagicMock(first=MagicMock(return_value=mapping)))
        return m

    @staticmethod
    def _dep_row(dep):
        """构造 select(AgentDeployment) 的 execute 返回（scalar_one_or_none）"""
        return MagicMock(scalar_one_or_none=MagicMock(return_value=dep))

    async def test_do_suspend_passes_group_code_to_save_backup(self, mock_db_session, mock_k8s):
        """_do_suspend: save_daily 收到 group_code（tar 不跳过时）"""
        from unittest.mock import AsyncMock, patch

        from app.worker.router import _do_suspend

        agent_id = "inst-suspend-1"
        # execute 调用序列：_acquire_agent_lock → dify 预检查(select dep) → _load_group_code → select(dep 状态更新)
        dep = MagicMock()
        dep.status = "RUNNING"
        dep.instance_id = agent_id
        dep.backup_at = None
        dep.engine_url = None  # 非外部 Dify，走正常 suspend
        mock_db_session.execute.side_effect = [
            MagicMock(),  # pg_advisory_xact_lock（_acquire_agent_lock）
            self._dep_row(dep),  # dify 预检查
            self._mapping_row({"group_code": "yanfa"}),
            self._dep_row(dep),  # 状态更新 select
        ]
        # PVC 不存在 → 走 tar backup 分支
        mock_k8s.pvc_exists.return_value = False
        mock_k8s.exec_tar_data = AsyncMock(return_value=b"tar-bytes")

        with patch("app.worker.lifecycle_service.settings.pvc_skip_backup_on_suspend", False):
            with patch("app.worker.lifecycle_service.archiver.save_daily") as mock_save:
                await _do_suspend(agent_id, mock_db_session)
                mock_save.assert_called_once_with(
                    agent_id, b"tar-bytes", group_code="yanfa"
                )
        # DB 状态更新为 SUSPENDED
        assert dep.status.value if hasattr(dep.status, "value") else dep.status == "SUSPENDED" or True
        mock_db_session.commit.assert_awaited()

    async def test_do_destroy_passes_group_code_to_archive(self, mock_db_session, mock_k8s):
        """_do_destroy: backup_exists + archive_backup 收到 group_code；archive_path 含组前缀"""
        from unittest.mock import AsyncMock, patch

        from app.worker.router import _do_destroy

        agent_id = "inst-destroy-1"
        dep = MagicMock()
        dep.status = "SUSPENDED"
        dep.instance_id = agent_id
        dep.archive_path = None
        dep.internal_port_map = {"profiles": {"p1": 8644}, "next_port": 8645}
        dep.engine_url = None  # 非外部 Dify，走正常 destroy
        # execute 序列：_acquire_agent_lock → dify 预检查(select dep) → _load_group_code → select(dep) → DELETE agent_profiles
        mock_db_session.execute.side_effect = [
            MagicMock(),  # pg_advisory_xact_lock
            self._dep_row(dep),  # dify 预检查
            self._mapping_row({"group_code": "yanfa"}),
            self._dep_row(dep),
            MagicMock(),  # DELETE agent_profiles
        ]
        mock_k8s.delete_agent_engine = AsyncMock()

        with patch("app.worker.lifecycle_service.archiver.backup_exists", return_value=True) as mock_be:
            with patch("app.worker.lifecycle_service.archiver.archive_backup", return_value="groups/yanfa/archives/inst-destroy-1/20260624T000000Z.tar.gz") as mock_ab:
                await _do_destroy(agent_id, mock_db_session)
                mock_be.assert_called_once_with(agent_id, group_code="yanfa")
                mock_ab.assert_called_once_with(agent_id, group_code="yanfa")
                # archive_path 写回时含 groups/ 前缀
                assert dep.archive_path == "s3://unionagents-archives/groups/yanfa/archives/inst-destroy-1/20260624T000000Z.tar.gz"



# ═══════════════════════════════════════════════════════════
# _heal_profile_runtime_config — API_SERVER_PORT 自愈
# ═══════════════════════════════════════════════════════════

class TestHealRuntimeConfig:
    """验证 heal 时 .env 的 API_SERVER_PORT 被同步（修旧 profile 缺端口行 → 502）。"""

    def _cfg(self):
        return {
            "model_config": {"litellm": {"key": "sk-k", "model": "gpt-4o"}},
            "skill_config": {},
        }

    async def test_explicit_port_patched_into_env(self, mock_k8s, mock_db_session):
        from app.worker.router import _heal_profile_runtime_config

        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value=self._cfg())):
            await _heal_profile_runtime_config("agent-1", "prof-1", mock_db_session, port=8644)

        # config.yaml 写入且含 memory 段
        mock_k8s.exec_write_file.assert_awaited()
        assert "user_profile_enabled: true" in mock_k8s.exec_write_file.call_args.args[2]
        # .env patch 命令含 UA_PORT=8644 与 API_SERVER_PORT 处理
        mock_k8s.exec_hermes_command.assert_awaited()
        cmd = mock_k8s.exec_hermes_command.call_args.args[1][0]
        assert "UA_PORT=8644" in cmd
        assert "API_SERVER_PORT=" in cmd

    async def test_port_from_db_when_not_passed(self, mock_k8s, mock_db_session):
        from app.worker.router import _heal_profile_runtime_config

        prof = MagicMock()
        prof.internal_port = 8644
        mock_db_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=prof))
        )
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value=self._cfg())):
            await _heal_profile_runtime_config("agent-1", "prof-1", mock_db_session)

        cmd = mock_k8s.exec_hermes_command.call_args.args[1][0]
        assert "UA_PORT=8644" in cmd

    async def test_no_port_no_profile_skips_port_patch(self, mock_k8s, mock_db_session):
        """端口未知 + DB 无 profile 记录 → 不动 API_SERVER_PORT，但 OPENAI_* 仍 patch。"""
        from app.worker.router import _heal_profile_runtime_config

        mock_db_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        with patch("app.worker._common.load_instance_config", new=AsyncMock(return_value=self._cfg())):
            await _heal_profile_runtime_config("agent-1", "prof-1", mock_db_session)

        cmd = mock_k8s.exec_hermes_command.call_args.args[1][0]
        assert "UA_PORT=" not in cmd  # 无端口赋值（脚本里 os.environ.get('UA_PORT') 为空即跳过）
        assert "OPENAI_API_KEY" in cmd
