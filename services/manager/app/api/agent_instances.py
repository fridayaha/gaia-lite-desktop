"""V3 智能体实例 API — /api/manager/agent-instances

实例层：定义×版本×资源池×访问范围关联 + 业务生命周期（上线/停用/版本切换/克隆）
+ 运行时生命周期（部署/暂停/恢复/销毁/重启，代理调 controller）
+ 详情页子资源（deployment-status/pods/logs/metrics/overview/channels）
+ Dify 应用对接配置（per-instance，校验 + verify-service-api 端点）。
"""

import json
import logging
from pathlib import Path
from uuid import UUID

import httpx
from app.core.auth import decode_token, get_current_user, is_platform_admin, user_or_internal
from app.core.group_scope import assert_group_writable, get_current_group_ids
from app.models import EngineConfig, EngineType, User, user_group_members
from app.schemas import (
    AccessibleInstanceResponse,
    AgentApiKeyCreate,
    AgentApiKeyCreateResponse,
    AgentApiKeyListResponse,
    AgentApiKeyResponse,
    AgentInstanceChannelCreate,
    AgentInstanceChannelListResponse,
    AgentInstanceChannelResponse,
    AgentInstanceChannelUpdate,
    AgentInstanceCreate,
    AgentInstanceListResponse,
    AgentInstanceResponse,
    AgentInstanceUpdate,
)
from app.services import (
    api_key_service,
    channel_service,
    definition_service,
    instance_service,
    metrics_service,
    profile_service,
    workspace_files,
)
from app.services.audit_service import log_operation
from app.worker import client as controller_client
from app.worker.k8s_manager import k8s_manager
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/agent-instances", tags=["agent-instances"])

logger = logging.getLogger(__name__)


# Dify app_type 合法值（dify_config.app_type）
_DIFY_VALID_APP_TYPES = frozenset({"chat", "agent", "workflow"})


def _validate_dify_config(engine_type: str | None, dify_cfg: dict | None) -> None:
    """Dify 引擎配置基础字段校验（不查 DB）。

    分三种情况（DB 上下文校验在 _validate_dify_config_async）：
      - MANAGED 模式：不要求 base_url/app_api_key（平台 Pod 部署）
      - EXTERNAL + 配了管理员账号：要求 app_id（app_api_key 由 select-app 接口拿）
      - EXTERNAL + 未配管理员账号：要求 base_url + app_api_key（手填）
    """
    if not engine_type:
        return
    try:
        et = str(engine_type).upper()
    except Exception:
        return
    if et != "DIFY":
        return

    dify = dify_cfg or {}
    if not isinstance(dify, dict):
        raise HTTPException(status_code=400, detail="dify_config 必须是对象")

    # base_url 格式校验（如果有值）
    base_url = dify.get("base_url")
    if base_url:
        url = str(base_url).strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(
                status_code=400,
                detail="Dify base_url 必须是 http(s):// 开头的合法 URL",
            )

    # app_type 格式校验（如果有值）
    app_type = dify.get("app_type")
    if app_type and str(app_type).lower() not in _DIFY_VALID_APP_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Dify app_type 必须为 chat/agent/workflow 之一，got: {app_type!r}",
        )


async def _validate_dify_config_async(
    engine_type: str | None,
    dify_cfg: dict | None,
    db: AsyncSession,
) -> None:
    """Dify 引擎配置完整校验（查 EngineConfig 决定严格校验逻辑）。"""
    if not engine_type:
        return
    try:
        et = str(engine_type).upper()
    except Exception:
        return
    if et != "DIFY":
        return

    # 先做字段格式校验
    _validate_dify_config(engine_type, dify_cfg)

    # 查全局 Dify 引擎配置
    from app.models import DifyEngineMode
    stmt = select(EngineConfig).where(
        EngineConfig.engine_type == EngineType.DIFY,
        EngineConfig.group_id.is_(None),
    )
    cfg = (await db.execute(stmt)).scalar_one_or_none()

    dify = dify_cfg or {}
    has_admin = bool(cfg and cfg.admin_email and cfg.admin_password_encrypted)

    if cfg and cfg.mode == DifyEngineMode.MANAGED:
        # 托管模式：不要求任何字段（平台 Pod 部署，base_url + api_key 由 Pod 内部提供）
        return

    # EXTERNAL 模式（无论是否配 admin）
    if has_admin:
        # 配了管理员账号 → 用户应通过 select-app 拿到完整配置，dify_config 应有 app_id
        if not dify.get("app_id"):
            raise HTTPException(
                status_code=400,
                detail="Dify 引擎配置了管理员账号，应通过'选择应用'下拉选择 Dify 应用（app_id 缺失）",
            )
        if not dify.get("app_api_key"):
            raise HTTPException(
                status_code=400,
                detail="Dify app_api_key 缺失，请重新选择应用以同步 API Key",
            )
        if not dify.get("app_type"):
            raise HTTPException(
                status_code=400,
                detail="Dify app_type 缺失，请重新选择应用",
            )
        if not dify.get("base_url") and cfg and cfg.base_url:
            # 前端可能没回填 base_url，自动注入
            dify["base_url"] = cfg.base_url
    else:
        # 未配管理员账号 → 保持现状，手填 base_url + app_api_key + app_type
        if not dify.get("base_url"):
            raise HTTPException(
                status_code=400,
                detail="Dify base_url 必填（未配置管理员账号，需手填）",
            )
        if not dify.get("app_api_key") or not str(dify.get("app_api_key")).strip():
            raise HTTPException(
                status_code=400,
                detail="Dify 引擎必须配置 dify_config.app_api_key",
            )
        if not dify.get("app_type"):
            raise HTTPException(
                status_code=400,
                detail="Dify app_type 必填（chat/agent/workflow）",
            )


async def _verify_dify_service_api(base_url: str, app_api_key: str) -> dict:
    """调 Service API /info 校验 API_KEY 有效性 + 拿应用 name/mode。

    成功返回 {name, mode, description}，失败抛 HTTPException(400)。
    """
    url = f"{base_url.rstrip('/')}/v1/info"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {app_api_key}"})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"调用 Dify 失败：网络错误 {e}") from e

    if r.status_code == 401:
        raise HTTPException(status_code=400, detail="Dify app_api_key 无效（401）")
    if r.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Dify 返回 HTTP {r.status_code}：{r.text[:200]}",
        )
    try:
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Dify 响应解析失败：{e}") from e

    return {
        "name": data.get("name") or "",
        "mode": data.get("mode") or "",
        "description": data.get("description") or "",
    }


def _to_response(inst) -> AgentInstanceResponse:
    definition_current_version_id = (
        inst.definition.current_version_id if inst.definition else None
    )
    has_newer_version = bool(
        definition_current_version_id
        and inst.version_id
        and inst.version_id != definition_current_version_id
    )
    return AgentInstanceResponse(
        id=inst.id,
        name=inst.name,
        description=inst.description,
        definition_id=inst.definition_id,
        definition_name=inst.definition.name if inst.definition else "",
        version_id=inst.version_id,
        version_no=inst.version.version_no if inst.version else None,
        definition_current_version_id=definition_current_version_id,
        has_newer_version=has_newer_version,
        resource_pool_id=inst.resource_pool_id,
        resource_pool_name=inst.resource_pool.name if inst.resource_pool else "",
        engine_type=inst.definition.engine_type.value if inst.definition else None,
        group_id=inst.group_id,
        group_name=inst.group.name if inst.group else "",
        status=inst.status,
        litellm_config=inst.litellm_config or {},
        dify_config=_build_dify_config(inst),
        runtime_config=inst.runtime_config or {},
        created_by=inst.created_by,
        creator_name=inst.creator.username if inst.creator else "",
        created_at=inst.created_at,
        updated_at=inst.updated_at,
        published_at=inst.published_at,
    )


def _is_external_dify(inst) -> bool:
    """Dify 外部对接实例（base_url 非空）判定。Pod 模式 Dify（base_url 空）返回 False。"""
    if not inst.definition or inst.definition.engine_type != EngineType.DIFY:
        return False
    dify = _get_dify_cfg(inst)
    return bool(dify.get("base_url"))


def _get_dify_cfg(inst) -> dict:
    """统一取 Dify 配置：优先 inst.dify_config（新列），空则回退 definition.model_config.dify。

    fallback 让历史快照数据（迁移前 definition.model_config.dify）继续可用，
    cleanup PR 再删。
    """
    cfg = inst.dify_config or {}
    if not cfg and inst.definition:
        cfg = (inst.definition.model_config or {}).get("dify") or {}
    return cfg


def _build_dify_config(inst) -> dict:
    """从 inst.dify_config 抽取 Dify 对接配置，app_api_key 掩码。
    非 DIFY 实例或无 dify 配置时返回 {}。"""
    from app.core.secrets import mask_secret

    if not inst.definition or inst.definition.engine_type != EngineType.DIFY:
        return {}
    dify = _get_dify_cfg(inst)
    if not dify:
        return {}
    base_url = dify.get("base_url") or ""
    return {
        "base_url": base_url,
        "app_type": dify.get("app_type") or "",
        "app_api_key": mask_secret(dify.get("app_api_key")),
        "app_id": dify.get("app_id") or "",
        "app_name": dify.get("app_name") or "",
        "source": dify.get("source") or "",
        "external": bool(base_url),
    }


@router.get("", response_model=AgentInstanceListResponse)
async def list_instances(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = None,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    items, total = await instance_service.list_instances(db, page, page_size, search, group_ids=group_ids)
    return AgentInstanceListResponse(
        items=[_to_response(i) for i in items], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=AgentInstanceResponse, status_code=status.HTTP_201_CREATED)
async def create_instance(
    data: AgentInstanceCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    assert_group_writable(data.group_id, group_ids)
    # Dify 引擎实例：校验 dify_config（按 EngineConfig 模式动态严格校验）
    d = await definition_service.get_definition(db, data.definition_id)
    if d and d.engine_type == EngineType.DIFY:
        _validate_dify_config("DIFY", data.dify_config)
        await _validate_dify_config_async("DIFY", data.dify_config, db)
    try:
        inst = await instance_service.create_instance(db, data, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _to_response(inst)


@router.post("/verify-dify-service-api", response_model=dict)
async def verify_dify_service_api(
    payload: dict,
    _: User = Depends(get_current_user),
):
    """校验用户手填的 Dify base_url + app_api_key，返回应用名称 + mode。

    用于智能体实例编辑页"校验"按钮，校验通过后前端自动回填 app_type + app_name。
    """
    base_url = (payload or {}).get("base_url", "").strip()
    app_api_key = (payload or {}).get("app_api_key", "").strip()
    if not base_url or not app_api_key:
        raise HTTPException(status_code=400, detail="base_url 和 app_api_key 都必填")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="base_url 必须是 http(s):// 开头")
    info = await _verify_dify_service_api(base_url, app_api_key)
    from app.core.dify_console_client import map_dify_mode_to_app_type
    app_type = map_dify_mode_to_app_type(info.get("mode"))
    return {
        "name": info["name"],
        "mode": info["mode"],
        "app_type": app_type,  # 不支持的 mode 时为 None
        "description": info["description"],
    }


@router.get("/accessible", response_model=list[AccessibleInstanceResponse])
async def list_accessible_instances(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """终端门户：当前用户可访问的已上线实例。平台管理员跨组可见，组用户仅见所属组。"""
    items = await instance_service.list_accessible_instances(db, user.id, is_platform_admin(user))
    return [
        AccessibleInstanceResponse(
            id=i.id,
            name=i.name,
            description=i.description,
            engine_type=i.definition.engine_type.value if i.definition else None,
            browser_sandbox_enabled=bool(
                (i.runtime_config or {}).get("browser_sandbox", {}).get("enabled")
            ),
        )
        for i in items
    ]


@router.get("/{instance_id}", response_model=AgentInstanceResponse)
async def get_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    inst = await _require_instance(db, instance_id, group_ids)
    return _to_response(inst)


@router.put("/{instance_id}", response_model=AgentInstanceResponse)
async def update_instance(
    instance_id: UUID,
    data: AgentInstanceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    inst = await _require_instance(db, instance_id, group_ids)
    # 改用户组时校验对新组的写权限
    if data.group_id is not None:
        assert_group_writable(data.group_id, group_ids)
    # Dify 引擎实例改 dify_config 时校验（按 EngineConfig 模式动态严格校验）
    if data.dify_config is not None and inst.definition and inst.definition.engine_type == EngineType.DIFY:
        _validate_dify_config("DIFY", data.dify_config)
        await _validate_dify_config_async("DIFY", data.dify_config, db)
    try:
        inst = await instance_service.update_instance(db, instance_id, data, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    if not await instance_service.delete_instance(db, instance_id, actor_id=user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")


# ── 业务生命周期 ────────────────────────────────────


@router.post("/{instance_id}/publish", response_model=AgentInstanceResponse)
async def publish_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """上线：DRAFT/OFFLINE→PUBLISHED，对终端可见。"""
    await _require_instance(db, instance_id, group_ids)
    inst = await instance_service.publish_instance(db, instance_id, actor_id=user.id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


@router.post("/{instance_id}/offline", response_model=AgentInstanceResponse)
async def offline_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """停用：PUBLISHED→OFFLINE，终端不可见（Pod 可保留）。"""
    await _require_instance(db, instance_id, group_ids)
    inst = await instance_service.offline_instance(db, instance_id, actor_id=user.id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


@router.post("/{instance_id}/switch-version", response_model=AgentInstanceResponse)
async def switch_version(
    instance_id: UUID,
    version_id: UUID = Query(..., description="目标版本 ID"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """切换实例绑定版本（升级/回滚）。运行时重启由 controller 接入后触发。"""
    await _require_instance(db, instance_id, group_ids)
    try:
        inst = await instance_service.switch_version(db, instance_id, version_id, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


@router.post("/{instance_id}/upgrade")
async def upgrade_version(
    instance_id: UUID,
    version_id: UUID = Query(..., description="目标版本 ID"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """切换版本并增量热推送到运行中 Pod（不重建 Pod）。

    按版本 diff 走热更新：人设/技能/模型名零重启；仅 litellm.model_group 变化时
    做一次轻量 rollout restart。未运行实例只更新 DB。返回 {applied, changed, restarted, message}。
    """
    await _require_instance(db, instance_id, group_ids)
    try:
        return await instance_service.upgrade_version(db, instance_id, version_id, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{instance_id}/litellm-key/reprovision", response_model=AgentInstanceResponse)
async def reprovision_instance_key(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """重新生成 per-instance LiteLLM key（key 丢失/老 key 统一/计费 Team 变更）。"""
    await _require_instance(db, instance_id, group_ids)
    try:
        inst = await instance_service.reprovision_instance_key(db, instance_id, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


@router.post("/{instance_id}/clone", response_model=AgentInstanceResponse, status_code=status.HTTP_201_CREATED)
async def clone_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    inst = await instance_service.clone_instance(db, instance_id, user.id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return _to_response(inst)


# ── 运行时生命周期（代理调 controller，作用在 AgentDeployment）──


async def _require_instance(
    db: AsyncSession, instance_id: UUID, group_ids: list[UUID] | None = None
):
    """取实例并校验组隔离：组用户只能访问所属组实例（跨组返回 404，不暴露存在性）。"""
    inst = await instance_service.get_instance(db, instance_id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    if group_ids is not None and inst.group_id not in group_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    return inst


@router.post("/{instance_id}/deploy")
async def deploy_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """部署/重新部署引擎：创建 Pod（Deployment PENDING→RUNNING）。

    controller 端点对 SUSPENDED/ARCHIVED 会走恢复分支，对 RUNNING 直接返回。
    """
    inst = await _require_instance(db, instance_id, group_ids)
    try:
        result = await controller_client.deploy_instance(str(instance_id))
        await log_operation(
            db, actor_id=user.id, action="agent_instance.deploy",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, detail={"name": inst.name},
        )
        await db.commit()
        return result
    except controller_client.ControllerError as e:
        await log_operation(
            db, actor_id=user.id, action="agent_instance.deploy",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, status="failure",
            detail={"name": inst.name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{instance_id}/suspend")
async def suspend_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """暂停引擎：存档数据 → scale=0（Deployment→SUSPENDED，保留 PVC）。"""
    inst = await _require_instance(db, instance_id, group_ids)
    try:
        result = await controller_client.suspend_instance(str(instance_id))
        await log_operation(
            db, actor_id=user.id, action="agent_instance.suspend",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, detail={"name": inst.name},
        )
        await db.commit()
        return result
    except controller_client.ControllerError as e:
        await log_operation(
            db, actor_id=user.id, action="agent_instance.suspend",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, status="failure",
            detail={"name": inst.name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{instance_id}/resume")
async def resume_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """恢复引擎：SUSPENDED→RUNNING（scale=1）。Deployment 不存在时返回 409。"""
    inst = await _require_instance(db, instance_id, group_ids)
    try:
        result = await controller_client.resume_instance(str(instance_id))
        await log_operation(
            db, actor_id=user.id, action="agent_instance.resume",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, detail={"name": inst.name},
        )
        await db.commit()
        return result
    except controller_client.ControllerError as e:
        await log_operation(
            db, actor_id=user.id, action="agent_instance.resume",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, status="failure",
            detail={"name": inst.name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{instance_id}/restart")
async def restart_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """重启引擎：滚动重启（配置/技能/人设变更生效）。"""
    inst = await _require_instance(db, instance_id, group_ids)
    try:
        result = await controller_client.restart_instance(str(instance_id))
        await log_operation(
            db, actor_id=user.id, action="agent_instance.restart",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, detail={"name": inst.name},
        )
        await db.commit()
        return result
    except controller_client.ControllerError as e:
        await log_operation(
            db, actor_id=user.id, action="agent_instance.restart",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, status="failure",
            detail={"name": inst.name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{instance_id}/destroy")
async def destroy_instance(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """销毁引擎：SUSPEND 存档后清 K8s 资源（Deployment→ARCHIVED，数据在 MinIO）。"""
    inst = await _require_instance(db, instance_id, group_ids)
    try:
        result = await controller_client.destroy_instance(str(instance_id))
        await log_operation(
            db, actor_id=user.id, action="agent_instance.destroy",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, detail={"name": inst.name},
        )
        await db.commit()
        return result
    except controller_client.ControllerError as e:
        await log_operation(
            db, actor_id=user.id, action="agent_instance.destroy",
            target_type="agent_instance", target_id=instance_id,
            group_id=inst.group_id, status="failure",
            detail={"name": inst.name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ── 详情页子资源（运行状态/监控/概览/渠道）──────────────────


def _pool_id_of(inst) -> str:
    """实例所属资源池 id（== 老 engine_instance_id，ID 保留）。

    controller list_instance_pods/get_pod_logs 按池 id 列 Pod。
    """
    if not inst.resource_pool_id:
        raise HTTPException(status_code=404, detail="该实例未绑定资源池")
    return str(inst.resource_pool_id)


@router.get("/{instance_id}/deployment-status")
async def get_instance_deployment_status(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """查询实例引擎部署状态（代理 controller，含 K8s pod 存活校验）。"""
    await _require_instance(db, instance_id, group_ids)
    try:
        return await controller_client.get_agent_status(str(instance_id))
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/{instance_id}/models")
async def get_instance_models(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """终端门户：实例可用模型列表（代理 controller，组隔离校验）。"""
    await _require_instance(db, instance_id, group_ids)
    try:
        return await controller_client.get_agent_models(str(instance_id))
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/{instance_id}/deploy/events")
async def stream_instance_deploy_events(
    instance_id: UUID,
    token: str = Query(..., description="JWT access token（EventSource 不支持 header，故用 query）"),
    db: AsyncSession = Depends(get_db),
):
    """终端门户：部署事件 SSE 流（代理 controller，query token 鉴权 + 组隔离）。

    EventSource 原生不支持自定义 header，故 token 经 query 传入。
    """
    try:
        payload = decode_token(token)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    roles = payload.get("roles") or []
    is_admin = "平台管理员" in roles or "系统管理员" in roles

    inst = await instance_service.get_instance(db, instance_id)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")
    if not is_admin:
        rows = await db.execute(
            select(user_group_members.c.group_id).where(user_group_members.c.user_id == user_id)
        )
        user_group_ids = {r[0] for r in rows.all()}
        if inst.group_id not in user_group_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实例不存在")

    async def event_gen():
        try:
            async for chunk in controller_client.stream_deploy_events(str(instance_id)):
                yield chunk
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/{instance_id}/pods")
async def get_instance_pods(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """列出该实例当前运行的 Pod（含 metrics-server 实时用量）。

    controller 按资源池返回整池 Pod，这里按 agent_id 过滤为本实例的 Pod，
    避免把同池其他实例的 Pod 一并展示给用户。
    """
    inst = await _require_instance(db, instance_id)
    pool_id = _pool_id_of(inst)

    # Dify 外部对接实例无 Pod，短路返回空（Pod 模式 Dify base_url 为空，仍走 controller）
    if _is_external_dify(inst):
        return {"items": [], "summary": {"running": 0, "stopped": 0, "abnormal": 0}}

    try:
        data = await controller_client.list_instance_pods(pool_id)
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    pods = data.get("items", []) if isinstance(data, dict) else []
    inst_id_str = str(instance_id)
    pods = [p for p in pods if p.get("agent_id") == inst_id_str]
    metrics_map = await controller_client.list_instance_pod_metrics(pool_id)
    items = []
    for p in pods:
        name = p.get("name", "")
        live = (metrics_map or {}).get(name, {})
        items.append({
            "name": name,
            "node": p.get("node", ""),
            "status": p.get("status", ""),
            "cpu": live.get("cpu") or p.get("cpu", ""),
            "memory": live.get("memory") or p.get("memory", ""),
            "restarts": p.get("restarts", 0),
            "age": p.get("age", ""),
            "created_at": "",
        })

    running = sum(1 for i in items if i["status"] == "Running")
    stopped = sum(1 for i in items if i["status"] in ("Pending", "Terminating", "Succeeded"))
    abnormal = sum(1 for i in items if i["status"] in ("CrashLoopBackOff", "Failed", "Unknown"))

    return {"items": items, "summary": {"running": running, "stopped": stopped, "abnormal": abnormal}}


@router.get("/{instance_id}/pods/{pod_name}/logs")
async def get_instance_pod_logs(
    instance_id: UUID,
    pod_name: str,
    tail_lines: int = Query(200, ge=10, le=5000),
    source: str = Query("engine", pattern="^(engine|gateway)$"),
    profile: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """获取指定 Pod 的日志（代理 controller）。

    source=engine：容器 stdout（nginx/启动日志）；source=gateway：某 profile 网关日志。
    """
    inst = await _require_instance(db, instance_id)
    pool_id = _pool_id_of(inst)
    try:
        if source == "gateway":
            if not profile:
                data = await controller_client.list_pod_log_sources(pool_id, pod_name)
                return await _enrich_instance_sources(data, db, instance_id)
            return await controller_client.get_profile_gateway_logs(
                pool_id, pod_name, profile, tail_lines
            )
        return await controller_client.get_pod_logs(pool_id, pod_name, tail_lines)
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


async def _enrich_instance_sources(data: dict, db: AsyncSession, instance_id: UUID) -> dict:
    """把 controller 返回的 {engine, profiles:[str]} 富化为带用户信息的 profiles 对象数组。"""
    profile_names = (data or {}).get("profiles", []) or []
    user_map = await profile_service.map_profiles_to_users(
        db, profile_names, instance_id=instance_id
    )
    return {
        "engine": (data or {}).get("engine", True),
        "profiles": profile_service.enrich_profiles(profile_names, user_map),
    }


@router.get("/{instance_id}/pods/{pod_name}/logs/sources")
async def get_instance_pod_log_sources(
    instance_id: UUID,
    pod_name: str,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """列出该 Pod 可用日志来源（引擎 stdout + 各 profile 网关，含用户信息）。"""
    inst = await _require_instance(db, instance_id)
    pool_id = _pool_id_of(inst)
    try:
        data = await controller_client.list_pod_log_sources(pool_id, pod_name)
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return await _enrich_instance_sources(data, db, instance_id)


@router.get("/{instance_id}/metrics")
async def get_instance_metrics(
    instance_id: UUID,
    range: str = Query("24h", pattern="^(1h|6h|24h|7d)$"),
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """实例监控指标：requests/tokens 来自 LiteLLM，cpu/memory 来自采样历史。"""
    inst = await _require_instance(db, instance_id)
    return await metrics_service.build_instance_metrics(db, inst, range)


# ── 工作区文件浏览（只读；F-END-030 落地到 manager）──────────────────


async def _resolve_workspace_pod(instance_id: UUID, deployment) -> str:
    """按 label 解析实际运行中的 Pod 名（deployment.pod_name 是 base 名，不含 ReplicaSet hash）。"""
    pod_status = await k8s_manager.get_pod_status(
        str(instance_id), deployment.scope_type, str(deployment.scope_target_id) if deployment.scope_target_id else None
    )
    pod_name = pod_status.get("pod_name")
    if not pod_name or not pod_status.get("running"):
        raise HTTPException(status_code=409, detail="引擎 Pod 未运行，无法读取工作区")
    return pod_name


@router.get("/{instance_id}/files")
async def list_instance_files(
    instance_id: UUID,
    path: str = Query(".", description="相对 profile 工作区根的子路径"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出用户在该实例 profile 工作区下的目录条目（只读）。

    鉴权：实例 PUBLISHED + 用户为组成员（或平台管理员）；profile 按 user_id/group_id 匹配。
    路径锚定到 profile home，拒 ``..``/绝对路径/敏感文件。
    """
    resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        return await workspace_files.list_files(k8s_manager, pod_name, workspace_root, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件列表失败: {e}")


@router.get("/{instance_id}/files/content")
async def read_instance_file_content(
    instance_id: UUID,
    path: str = Query(..., description="相对 profile 工作区根的文件路径"),
    auth: tuple[User | None, bool] = Depends(user_or_internal),
    db: AsyncSession = Depends(get_db),
):
    """读取该实例 profile 工作区下的文件内容（只读，超 400KB 截断）。

    鉴权：普通用户 JWT（按用户解析 profile）或 gateway 内部令牌（按 instance_id 解析，
    供 gateway 解析工作区图片用，sk- API Key 客户端无 JWT 走此路）。
    """
    user, is_internal = auth
    if is_internal:
        resolved = await workspace_files.resolve_instance_profile(db, instance_id)
    else:
        resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        return await workspace_files.read_file_content(k8s_manager, pod_name, workspace_root, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件内容失败: {e}")


@router.get("/{instance_id}/files/download")
async def download_instance_file(
    instance_id: UUID,
    path: str = Query(..., description="相对 profile 工作区根的文件路径"),
    auth: tuple[User | None, bool] = Depends(user_or_internal),
    db: AsyncSession = Depends(get_db),
):
    """下载该实例 profile 工作区下的文件（完整字节，无截断，带 Content-Disposition）。

    供前端点击 agent 回复里的文件链接下载、gateway 企微出站发 file msgtype 取字节用。
    与 /files/content 区别：不截断（上限 20MB）、返回原始二进制流而非 base64 JSON。
    鉴权同 /files/content：JWT 用户或 gateway 内部令牌。
    """
    user, is_internal = auth
    if is_internal:
        resolved = await workspace_files.resolve_instance_profile(db, instance_id)
    else:
        resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        result = await workspace_files.read_file_bytes(k8s_manager, pod_name, workspace_root, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载文件失败: {e}")
    if result.get("error"):
        if result["error"] == "too large":
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件过大，最大 {workspace_files.MAX_DOWNLOAD_BYTES // 1024 // 1024}MB",
            )
        if result["error"] in ("not a file", "not found"):
            raise HTTPException(status_code=404, detail="文件不存在")
        raise HTTPException(status_code=500, detail=f"下载文件失败: {result['error']}")
    name = result["name"]
    # Content-Disposition：HTTP header 值必须 latin-1 可编码。
    # legacy filename="..." 段放 ASCII fallback（非 ASCII 字符剔除），真实中文名走
    # RFC 5987 filename*。legacy 段直接塞中文会让 Starlette latin-1 编码抛
    # UnicodeEncodeError → 500（中文名文件下载必挂）。
    # 纯中文名剔除后只剩扩展名（如 ".pdf"）时用 download{suffix} 兜底，避免裸扩展名。
    from urllib.parse import quote

    suffix = Path(name).suffix or ""
    ascii_name = name.encode("ascii", "ignore").decode()
    if not ascii_name or ascii_name == suffix:
        ascii_name = f"download{suffix}"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(
        content=result["bytes"],
        media_type=result["mime"],
        headers={"Content-Disposition": disposition},
    )


@router.post("/{instance_id}/files/upload")
async def upload_instance_file(
    instance_id: UUID,
    file: UploadFile = File(...),
    path: str = Query("uploads", description="相对 profile 工作区根的目标目录"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文件到用户 profile 工作区的指定子目录（默认 uploads/）。

    返回 ``{filename, path, size, mime, is_image}``，前端用 path 拼进消息文本
    ``[Attached files: path]`` 供引擎读取。
    """
    resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    # 分块流读，超限即中止——避免 await file.read() 把超大 body 全量载入内存后才在
    # write_upload 查 20MB，并发上传下 OOM 杀掉 manager Pod。
    max_bytes = workspace_files.MAX_UPLOAD_BYTES
    chunks: list[bytes] = []
    total = 0
    while True:
        buf = await file.read(1024 * 1024)
        if not buf:
            break
        total += len(buf)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件过大，最大 {max_bytes // 1024 // 1024}MB",
            )
        chunks.append(buf)
    content = b"".join(chunks)
    try:
        return await workspace_files.write_upload(
            k8s_manager, pod_name, workspace_root, file.filename or "upload", content, rel_dir=path
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("upload_instance_file failed")
        raise HTTPException(status_code=500, detail="上传文件失败，请重试")


@router.post("/{instance_id}/files/upload-internal")
async def upload_instance_file_internal(
    instance_id: UUID,
    file: UploadFile = File(...),
    auth: tuple[User | None, bool] = Depends(user_or_internal),
    db: AsyncSession = Depends(get_db),
):
    """网关内部上传：把 IM 附件（企微图片/文件/视频等下载下来的字节）写入引擎
    profile 工作区的 uploads/ 子目录，返回 ``{filename, path, size, mime, is_image}``。

    鉴权：仅接受 gateway 内部令牌（X-Internal-Token，按 instance_id 解析 profile）。
    普通用户走 /files/upload；本端点对非 internal 调用一律 403。
    """
    user, is_internal = auth
    if not is_internal:
        raise HTTPException(status_code=403, detail="仅限内部调用")
    resolved = await workspace_files.resolve_instance_profile(db, instance_id)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    max_bytes = workspace_files.MAX_UPLOAD_BYTES
    chunks: list[bytes] = []
    total = 0
    while True:
        buf = await file.read(1024 * 1024)
        if not buf:
            break
        total += len(buf)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件过大，最大 {max_bytes // 1024 // 1024}MB",
            )
        chunks.append(buf)
    content = b"".join(chunks)
    try:
        return await workspace_files.write_upload(
            k8s_manager, pod_name, workspace_root, file.filename or "upload", content
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("upload_instance_file_internal failed")
        raise HTTPException(status_code=500, detail="上传文件失败")


@router.post("/{instance_id}/files/mkdir")
async def create_instance_folder(
    instance_id: UUID,
    path: str = Query(".", description="相对 profile 工作区根的父目录"),
    name: str = Body(..., embed=True, description="新建文件夹名称"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """在指定工作区目录下新建文件夹。

    若目标已存在（含被列表隐藏的内部敏感文件）返回 409。
    """
    resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        result = await workspace_files.create_folder(
            k8s_manager, pod_name, workspace_root, path, name
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("create_instance_folder failed")
        raise HTTPException(status_code=500, detail=f"创建文件夹失败: {e}")
    if result.get("error"):
        if result["error"] == "already exists":
            raise HTTPException(status_code=409, detail="文件名或文件夹名已存在")
        if result["error"] == "sensitive name":
            raise HTTPException(status_code=400, detail="该名称被系统保留，不可使用")
        raise HTTPException(status_code=500, detail=f"创建文件夹失败: {result['error']}")
    return {"ok": True}


@router.delete("/{instance_id}/files")
async def delete_instance_file(
    instance_id: UUID,
    path: str = Query(..., description="相对 profile 工作区根的文件/文件夹路径"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除工作区中的文件或文件夹。"""
    resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        result = await workspace_files.delete_entry(
            k8s_manager, pod_name, workspace_root, path
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("delete_instance_file failed")
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    if result.get("error"):
        if result["error"] == "not found":
            raise HTTPException(status_code=404, detail="文件不存在")
        raise HTTPException(status_code=500, detail=f"删除失败: {result['error']}")
    return {"ok": True}


@router.post("/{instance_id}/files/move")
async def move_instance_file(
    instance_id: UUID,
    from_path: str = Body(..., embed=True),
    to_path: str = Body(..., embed=True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """移动/重命名工作区中的文件或文件夹。"""
    resolved = await workspace_files.resolve_user_profile(db, instance_id, user)
    if not resolved:
        raise HTTPException(status_code=403, detail="无可访问的 profile")
    profile, deployment, _instance = resolved
    pod_name = await _resolve_workspace_pod(instance_id, deployment)
    workspace_root = Path(profile.hermes_home)
    try:
        result = await workspace_files.move_entry(
            k8s_manager, pod_name, workspace_root, from_path, to_path
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("move_instance_file failed")
        raise HTTPException(status_code=500, detail=f"移动失败: {e}")
    if result.get("error"):
        if result["error"] == "source not found":
            raise HTTPException(status_code=404, detail="源文件不存在")
        if result["error"] == "destination already exists":
            raise HTTPException(status_code=409, detail="目标位置已存在同名文件")
        raise HTTPException(status_code=500, detail=f"移动失败: {result['error']}")
    return {"ok": True}


@router.get("/{instance_id}/overview")
async def get_instance_overview(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """实例概览统计：对话数、Token、活跃用户、7d 对话趋势。"""
    inst = await _require_instance(db, instance_id)
    return await metrics_service.build_instance_overview(db, inst)


# ── 渠道（挂在实例层）──────────────────────────────────


@router.get("/{instance_id}/channels", response_model=AgentInstanceChannelListResponse)
async def list_instance_channels(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    channels, total = await channel_service.list_channels(db, instance_id)
    return AgentInstanceChannelListResponse(
        items=[AgentInstanceChannelResponse.model_validate(c) for c in channels],
        total=total,
    )


@router.post(
    "/{instance_id}/channels",
    response_model=AgentInstanceChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_instance_channel(
    instance_id: UUID,
    data: AgentInstanceChannelCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    try:
        channel = await channel_service.create_channel(db, instance_id, data, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return AgentInstanceChannelResponse.model_validate(channel)


@router.put(
    "/{instance_id}/channels/{channel_id}",
    response_model=AgentInstanceChannelResponse,
)
async def update_instance_channel(
    instance_id: UUID,
    channel_id: UUID,
    data: AgentInstanceChannelUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    channel = await channel_service.update_channel(db, channel_id, data, actor_id=user.id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return AgentInstanceChannelResponse.model_validate(channel)


@router.delete(
    "/{instance_id}/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_instance_channel(
    instance_id: UUID,
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_instance(db, instance_id, group_ids)
    if not await channel_service.delete_channel(db, channel_id, actor_id=user.id):
        raise HTTPException(status_code=404, detail="Channel not found")


# ── API Keys（OpenAI 兼容，挂在实例层）──────────────────────────

@router.get(
    "/{instance_id}/api-keys",
    response_model=AgentApiKeyListResponse,
)
async def list_instance_api_keys(
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """列出实例的所有 API Key（不含明文，只返回 key_prefix）。"""
    await _require_instance(db, instance_id, group_ids)
    keys, total = await api_key_service.list_keys(db, instance_id)
    return AgentApiKeyListResponse(
        items=[AgentApiKeyResponse.model_validate(k) for k in keys],
        total=total,
    )


@router.post(
    "/{instance_id}/api-keys",
    response_model=AgentApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_instance_api_key(
    instance_id: UUID,
    data: AgentApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """创建 API Key。响应包含明文 key，仅此一次返回——前端需提示用户立即复制保存。"""
    await _require_instance(db, instance_id, group_ids)
    try:
        key, plaintext = await api_key_service.create_key(
            db, instance_id, data.name, actor_id=user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该智能体下已存在同名 API Key",
        )
    return AgentApiKeyCreateResponse(
        id=str(key.id),
        name=key.name,
        key_prefix=key.key_prefix,
        key=plaintext,
        created_at=key.created_at,
    )


@router.delete(
    "/{instance_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_instance_api_key(
    instance_id: UUID,
    key_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """删除 API Key。删除后短期内（缓存过期前）可能仍可用，不保证立即失效。"""
    await _require_instance(db, instance_id, group_ids)
    if not await api_key_service.delete_key(db, key_id, actor_id=user.id):
        raise HTTPException(status_code=404, detail="API Key not found")
