"""Worker 进程内直调 facade —— 替代原 `services/controller_client.py` 的 HTTP 封装。

controller 并入 manager 后，manager 不再经 HTTP 调 controller，而是同进程直调
worker router 的 handler。本模块保留与原 controller_client 完全一致的 17 个函数名
与签名，调用点（agent_instances/agent_skills/dashboard/resource_pools/metrics_service）
仅改 import：`from app.worker import client as controller_client`，函数名 / 签名 /
`controller_client.ControllerError` 捕获全部不变。

翻译约定（对齐原 HTTP facade 的返回契约）：
  - handler 抛 HTTPException → 转 ControllerError(message=detail, status_code)
  - handler 返回 Pydantic 模型 → model_dump(mode="json") 成 dict
  - list_instance_pod_metrics：保留原 {name:{cpu,memory}} 重塑；501/404 → None
  - list_engine_skills：保留原 404/502/503 → {"engine_deployed":False,"items":[]}
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from pkg.common.database import get_db

from . import router as _r
from .errors import ControllerError

__all__ = ["ControllerError"]


# ── 内部工具 ──────────────────────────────────────────────


def _to_dict(result: Any) -> Any:
    """Pydantic 模型 → dict（对齐原 HTTP facade 返回 JSON dict 的行为）。"""
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result


async def _call_with_db(handler: Any, **kwargs: Any) -> Any:
    """调需要 DB 的 worker handler：新建 session 注入 db，翻译异常与返回值。"""
    agen = get_db()
    db = await agen.__anext__()
    try:
        try:
            result = await handler(db=db, **kwargs)
        except HTTPException as e:
            raise ControllerError(str(e.detail), e.status_code) from e
        return _to_dict(result)
    finally:
        await agen.aclose()


async def _call_no_db(handler: Any, **kwargs: Any) -> Any:
    """调不需要 DB 的 worker handler（如 get_pod_logs），翻译异常与返回值。"""
    try:
        result = await handler(**kwargs)
    except HTTPException as e:
        raise ControllerError(str(e.detail), e.status_code) from e
    return _to_dict(result)


# ── Pod 观测 ──────────────────────────────────────────────


async def list_instance_pods(instance_id: str) -> dict[str, Any]:
    """GET /api/controller/engine-instances/{id}/pods — 返回 {items, total}。"""
    return await _call_with_db(_r.get_instance_pods, instance_id=instance_id)


async def get_pod_logs(instance_id: str, pod_name: str, tail_lines: int = 200) -> dict[str, Any]:
    """GET /api/controller/engine-instances/{id}/pods/{pod}/logs — 返回 {pod_name, logs}。"""
    return await _call_no_db(
        _r.get_pod_logs, instance_id=instance_id, pod_name=pod_name, tail_lines=tail_lines
    )


async def get_profile_gateway_logs(
    instance_id: str, pod_name: str, profile: str, tail_lines: int = 200
) -> dict[str, Any]:
    """GET .../pods/{pod}/logs?source=gateway&profile=... — 返回 {pod_name, source, profile, logs}。"""
    return await _call_no_db(
        _r.get_pod_logs,
        instance_id=instance_id,
        pod_name=pod_name,
        tail_lines=tail_lines,
        source="gateway",
        profile=profile,
    )


async def list_pod_log_sources(instance_id: str, pod_name: str) -> dict[str, Any]:
    """GET .../pods/{pod}/logs/sources — 返回 {engine, profiles}。"""
    return await _call_no_db(
        _r.get_pod_log_sources, instance_id=instance_id, pod_name=pod_name
    )


async def get_pod_metrics(instance_id: str, pod_name: str) -> dict[str, Any] | None:
    """原 HTTP facade 命中不存在的 /pods/{pod}/metrics 端点（404→None）。

    worker 仅有聚合端点 /pods/metrics（无 per-pod），且本函数无调用点，
    保留原 None 语义以维持 facade 契约不变。
    """
    return None


async def list_instance_pod_metrics(
    instance_id: str,
) -> dict[str, dict[str, str]] | None:
    """GET /api/controller/engine-instances/{id}/pods/metrics — 返回 {pod_name: {cpu, memory}}。

    metrics-server 未部署时返回 None（调用方降级为空）。
    """
    try:
        data = await _call_with_db(_r.get_instance_pods_metrics, instance_id=instance_id)
    except ControllerError as e:
        if e.status_code in (404, 501):
            return None
        raise
    items = data.get("items", []) if isinstance(data, dict) else []
    return {
        it["name"]: {"cpu": it.get("cpu", ""), "memory": it.get("memory", "")}
        for it in items
        if it.get("name")
    }


# ── 引擎生命周期（V3：参数 instance_id 语义，端点路径沿用 /agents/{id}/*）──


async def deploy_instance(
    instance_id: str,
    scope_type: str = "ALL",
    scope_target_id: str | None = None,
) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/deploy — 创建/恢复引擎部署。"""
    return await _call_with_db(
        _r.deploy_agent,
        agent_id=instance_id,
        body=_r.DeployRequest(scope_type=scope_type, scope_target_id=scope_target_id),
    )


async def suspend_instance(instance_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/suspend — 存档 + scale=0。"""
    return await _call_with_db(_r.suspend_agent, agent_id=instance_id)


async def resume_instance(instance_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/resume — SUSPENDED→RUNNING（scale=1）。"""
    return await _call_with_db(_r.resume_agent, agent_id=instance_id)


async def restart_instance(instance_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/restart — 滚动重启（配置/技能/人设变更生效）。"""
    return await _call_with_db(_r.restart_agent, agent_id=instance_id)


async def destroy_instance(instance_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/destroy — 销毁引擎（归档+清理 K8s）。"""
    return await _call_with_db(_r.destroy_agent, agent_id=instance_id)


async def destroy_agent(agent_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/destroy — 供 manager 删除智能体前 best-effort 清理。"""
    return await _call_with_db(_r.destroy_agent, agent_id=agent_id)


async def get_agent_status(agent_id: str) -> dict[str, Any]:
    """GET /api/controller/agents/{id}/status — 引擎部署状态（含 K8s pod 存活校验）。"""
    return await _call_with_db(_r.get_agent_status, agent_id=agent_id)


async def get_agent_models(agent_id: str) -> dict[str, Any]:
    """GET /api/controller/agents/{id}/models — 引擎可用模型列表。"""
    return await _call_with_db(_r.get_agent_models, agent_id=agent_id)


async def stream_deploy_events(agent_id: str) -> AsyncGenerator[bytes, None]:
    """GET /api/controller/agents/{id}/deploy/events — SSE 部署事件流（透传 handler）。"""
    resp = await _r.stream_deploy_events(agent_id)
    async for chunk in resp.body_iterator:
        yield chunk.encode() if isinstance(chunk, str) else chunk


# ── 人设 (SOUL.md) 与技能同步 ────────────────────────────


async def sync_persona(agent_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/persona/sync — fan-out SOUL.md（不重启）。"""
    return await _call_with_db(_r.sync_persona, agent_id=agent_id)


async def apply_agent_config(agent_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/config/apply — patch env + regen config + 滚动重启。

    仅当 litellm.model_group 变更（per-instance key 重生成）时用：新 key 需 patch 进
    Deployment env 才能在 Pod 启动时生效，故需一次轻量 rollout restart（~30-60s，
    非重型 deploy）。model_group 不变的版本升级走 sync_skills_config 热路径，不调此函数。
    """
    return await _call_with_db(_r.apply_agent_config, agent_id=agent_id)


async def install_skill(agent_id: str, skill_name: str, zip_b64: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/skills/install — 解压技能 + 建软链 + 重启。"""
    return await _call_with_db(
        _r.install_skill,
        agent_id=agent_id,
        req=_r.SkillInstallRequest(skill_name=skill_name, zip_b64=zip_b64),
    )


async def list_engine_skills(agent_id: str) -> dict[str, Any]:
    """GET /api/controller/agents/{id}/skills/list — 扫描引擎 Pod skills/ 目录。

    未部署时返回 {"engine_deployed": False, "items": []}。
    """
    try:
        return await _call_with_db(_r.list_engine_skills, agent_id=agent_id)
    except ControllerError as e:
        if e.status_code in (404, 502, 503):
            return {"engine_deployed": False, "items": []}
        raise


async def sync_skills_config(agent_id: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/skills/config/sync — 重写 profile config.yaml（不重启）。"""
    return await _call_with_db(_r.sync_skills_config, agent_id=agent_id)


async def uninstall_skill(agent_id: str, skill_name: str) -> dict[str, Any]:
    """DELETE /api/controller/agents/{id}/skills/{name} — 删软链 + 本体 + 重启。"""
    return await _call_with_db(_r.uninstall_skill, agent_id=agent_id, skill_name=skill_name)


async def write_skill_secrets(agent_id: str, skill_name: str, credentials_encrypted: str) -> dict[str, Any]:
    """POST /api/controller/agents/{id}/skills/secrets/write — 把加密 secret 写到各 Pod（sidecar 解密用）。"""
    return await _call_with_db(
        _r.write_skill_secrets,
        agent_id=agent_id,
        req=_r.SkillSecretsRequest(skill_name=skill_name, credentials_encrypted=credentials_encrypted),
    )
