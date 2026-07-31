"""LiteLLM 模型网关管理路由。

管理台 → Manager(master key) → LiteLLM Admin API。
权限收敛：
  - 全局模型组管理：需 litellm:model:manage（平台管理员）
  - Key/用量管理：平台管理员不限范围；组管理员仅限所属 UserGroup 对应 Team
"""
from __future__ import annotations

from uuid import UUID

from app.core.auth import get_current_user, is_platform_admin, require_permission
from app.models import AgentInstance, User, UserGroup, user_group_members
from app.schemas import (
    LiteLLMKeyCreate,
    LiteLLMKeyUpdate,
    LiteLLMModelCreate,
    LiteLLMModelPriceUpdate,
    LiteLLMModelUpdate,
)
from app.services import litellm_client
from app.services.audit_service import log_operation
from app.services.litellm_client import normalize_spend_dates
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/litellm", tags=["litellm"])


def _to_cny(usd) -> float:
    """LiteLLM 原生 USD 花费 → 人民币展示值（按配置汇率，保留 4 位）。"""
    try:
        return round(float(usd or 0) * settings.spend_usd_to_cny, 4)
    except (TypeError, ValueError):
        return 0.0


# ── 范围收敛辅助 ────────────────────────────────────────


async def _assert_group_scope(db: AsyncSession, user: User, group_id: UUID) -> UserGroup:
    """平台管理员可访问任意 group；否则必须是该 group 成员。返回 UserGroup。"""
    res = await db.execute(select(UserGroup).where(UserGroup.id == group_id))
    group = res.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户组不存在")

    if is_platform_admin(user):
        return group

    res = await db.execute(
        select(user_group_members).where(
            user_group_members.c.user_id == user.id,
            user_group_members.c.group_id == group_id,
        )
    )
    if res.first() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理该用户组的 Key")
    return group


async def _user_group_ids(db: AsyncSession, user: User) -> list[UUID]:
    """当前用户所属的全部 UserGroup id（组管理员的可见范围）。"""
    res = await db.execute(
        select(user_group_members.c.group_id).where(user_group_members.c.user_id == user.id)
    )
    return [r[0] for r in res.all()]


async def _resolve_group_filter(
    db: AsyncSession, user: User, group_id: str | None
) -> str | None:
    """把前端 group_id（可能是 UUID、'default' 保留值、或空）解析为 LiteLLM team_id。

    - 空 → None（调用方按 platform admin 全量 / 组 admin 取首个组处理）
    - == 平台默认 team_id → 仅平台管理员放行返回该 id；组管理员 403（不应见平台级用量）
    - 其余 → 按 UUID 校验归属，返回 str(group_id)
    """
    if not group_id:
        return None
    if group_id == settings.litellm_default_team_id:
        if not is_platform_admin(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看平台默认组用量")
        return settings.litellm_default_team_id
    try:
        gid = UUID(group_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="无效的组 ID")
    await _assert_group_scope(db, user, gid)
    return str(gid)


# ── 模型组（全局上游供应商） ────────────────────────────


@router.get("/model-groups")
async def get_model_groups(_: User = Depends(get_current_user)):
    """供 Agent 表单选择的全局模型组列表。"""
    items = await litellm_client.list_model_groups()
    return {"items": items, "total": len(items)}


@router.get("/models")
async def list_models(user: User = Depends(get_current_user)):
    """模型组/部署详情列表（模型管理页）。"""
    items = await litellm_client.list_models()
    return {"items": items, "total": len(items)}


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(
    data: LiteLLMModelCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("litellm:model:manage")),
):
    params: dict = {"model": data.model, "api_key": data.api_key}
    if data.api_base:
        params["api_base"] = data.api_base
    if data.custom_llm_provider:
        params["custom_llm_provider"] = data.custom_llm_provider
    # context_length 存入 LiteLLM model_info，Agent 选用此模型组时继承写入 config.yaml
    model_info: dict = {}
    if data.context_length is not None:
        model_info["context_length"] = data.context_length
    try:
        result = await litellm_client.create_model(
            data.model_name, params, model_info=model_info or None
        )
        model_id = result.get("model_id") if isinstance(result, dict) else None
        await log_operation(
            db, actor_id=user.id, action="litellm_model.create",
            target_type="litellm_model", target_id=model_id,
            detail={"model": data.model, "model_name": data.model_name,
                    "context_length": data.context_length},
        )
        await db.commit()
        return result
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_model.create",
            target_type="litellm_model", target_id=None,
            status="failure", detail={"model": data.model, "model_name": data.model_name, "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("litellm:model:manage")),
):
    try:
        await litellm_client.delete_model(model_id)
        await log_operation(
            db, actor_id=user.id, action="litellm_model.delete",
            target_type="litellm_model", target_id=model_id,
        )
        await db.commit()
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_model.delete",
            target_type="litellm_model", target_id=model_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.put("/models/{model_id}")
async def update_model(
    model_id: str,
    data: LiteLLMModelUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("litellm:model:manage")),
):
    """更新模型组上游参数（model_name 组名不可改；留空字段=不变）。"""
    params: dict = {}
    if data.model is not None:
        params["model"] = data.model
    if data.api_key:
        params["api_key"] = data.api_key
    if data.api_base:
        params["api_base"] = data.api_base
    if data.custom_llm_provider:
        params["custom_llm_provider"] = data.custom_llm_provider
    model_info: dict = {}
    if data.context_length is not None:
        model_info["context_length"] = data.context_length
    try:
        result = await litellm_client.update_model(
            model_id, params, model_info=model_info or None
        )
        await log_operation(
            db, actor_id=user.id, action="litellm_model.update",
            target_type="litellm_model", target_id=model_id,
            detail={"fields": [k for k in data.model_fields_set if getattr(data, k)]},
        )
        await db.commit()
        return result
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_model.update",
            target_type="litellm_model", target_id=model_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.put("/models/{model_id}/price")
async def update_model_price(
    model_id: str,
    data: LiteLLMModelPriceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("litellm:model:manage")),
):
    """更新 deployment 的 pricing（USD / 1M tokens，留空=不变，传 0=明确免费）。

    单位转换由 litellm_client 处理（÷1M → per token 写回 LiteLLM）。
    补充价格后立即生效，仅影响新调用，历史 spend_logs 不回填。
    """
    try:
        result = await litellm_client.update_model(
            model_id,
            input_cost_per_1m_tokens=data.input_cost_per_1m_tokens,
            output_cost_per_1m_tokens=data.output_cost_per_1m_tokens,
        )
        await log_operation(
            db, actor_id=user.id, action="litellm_model.update_price",
            target_type="litellm_model", target_id=model_id,
            detail={
                "input_cost_per_1m_tokens": data.input_cost_per_1m_tokens,
                "output_cost_per_1m_tokens": data.output_cost_per_1m_tokens,
            },
        )
        await db.commit()
        return result
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_model.update_price",
            target_type="litellm_model", target_id=model_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


# ── Team 同步（UserGroup ↔ LiteLLM Team） ───────────────


@router.get("/teams")
async def list_teams(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出 UA UserGroup 与 LiteLLM Team 的映射状态。"""
    res = await db.execute(select(UserGroup).order_by(UserGroup.created_at))
    groups = list(res.scalars().all())
    try:
        teams = await litellm_client.list_teams()
    except litellm_client.LitellmError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    team_ids = {t.get("team_id") for t in teams}
    items = [
        {
            "group_id": str(g.id),
            "name": g.name,
            "team_id": str(g.id),
            "synced": str(g.id) in team_ids,
        }
        for g in groups
    ] + [{"group_id": settings.litellm_default_team_id, "name": "平台默认", "team_id": settings.litellm_default_team_id, "synced": settings.litellm_default_team_id in team_ids}]
    return {"items": items, "total": len(items)}


@router.post("/teams/sync")
async def sync_teams(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("litellm:model:manage")),
):
    """全量同步：为每个 UserGroup 确保 LiteLLM Team 存在。"""
    res = await db.execute(select(UserGroup).order_by(UserGroup.created_at))
    groups = list(res.scalars().all())
    synced = []
    try:
        await litellm_client.ensure_team(settings.litellm_default_team_id, "平台默认")
        for g in groups:
            await litellm_client.ensure_team(str(g.id), g.name)
            synced.append(str(g.id))
        await log_operation(
            db, actor_id=user.id, action="litellm_team.sync",
            target_type="litellm_team", target_id=None,
            detail={"synced_count": len(synced)},
        )
        await db.commit()
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_team.sync",
            target_type="litellm_team", target_id=None,
            status="failure", detail={"error": e.message, "synced_count": len(synced)},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    return {"synced": synced, "count": len(synced)}


# ── Virtual Key 管理 ────────────────────────────────────


@router.get("/keys")
async def list_keys(
    group_id: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出虚拟 Key。平台管理员可指定任意 group（含 'default' 平台默认组）；组管理员仅本组。"""
    team_id = await _resolve_group_filter(db, user, group_id)

    try:
        if team_id:
            result = await litellm_client.list_keys(team_id=team_id)
        elif is_platform_admin(user):
            result = await litellm_client.list_keys()  # 全部
        else:
            # 组管理员未指定 → 其所有组
            result = []
            for gid in await _user_group_ids(db, user):
                result.extend(await litellm_client.list_keys(team_id=str(gid)))
        # 花费 USD → CNY 展示
        for k in result:
            if "spend" in k:
                k["spend"] = _to_cny(k.get("spend"))
        await _enrich_key_agent_names(db, result)
        return {"items": result, "total": len(result)}
    except litellm_client.LitellmError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


async def _enrich_key_agent_names(db: AsyncSession, keys: list[dict]) -> None:
    """per-instance key 的 metadata.instance_id → AgentInstance.name，补 agent_name/agent_id。

    管理台「所属智能体」列原本只能拿到 key_alias（如 `instance:abcd1234`），看不出具体智能体。
    """
    inst_ids = [(k.get("metadata") or {}).get("instance_id") for k in keys]
    inst_ids = [mid for mid in inst_ids if mid]
    agent_map: dict[str, str] = {}
    if inst_ids:
        try:
            ids = [UUID(mid) for mid in inst_ids]
        except (ValueError, TypeError):
            ids = []
        if ids:
            rows = await db.execute(
                select(AgentInstance.id, AgentInstance.name).where(AgentInstance.id.in_(ids))
            )
            agent_map = {str(rid): name for rid, name in rows.all()}
    for k in keys:
        mid = (k.get("metadata") or {}).get("instance_id")
        k["agent_id"] = mid or ""
        k["agent_name"] = agent_map.get(mid, "") if mid else ""


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(
    data: LiteLLMKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    group = await _assert_group_scope(db, user, data.group_id)
    try:
        await litellm_client.ensure_team(str(group.id), group.name)
        result = await litellm_client.generate_key(
            team_id=str(group.id),
            models=data.models,
            key_alias=data.key_alias,
            max_budget=data.max_budget,
            budget_duration=data.budget_duration,
            rpm_limit=data.rpm_limit,
            tpm_limit=data.tpm_limit,
            duration=data.duration,
            metadata={"group_id": str(group.id), "created_by": str(user.id)},
        )
        await log_operation(
            db, actor_id=user.id, action="litellm_key.create",
            target_type="litellm_key",
            target_id=result.get("key_id") or result.get("token") if isinstance(result, dict) else None,
            group_id=group.id,
            detail={"team_id": str(group.id), "models": data.models, "key_alias": data.key_alias},
        )
        await db.commit()
        return result
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_key.create",
            target_type="litellm_key", target_id=None, group_id=group.id,
            status="failure", detail={"team_id": str(group.id), "error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.put("/keys/{key_id}")
async def update_key(
    key_id: str,
    data: LiteLLMKeyUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _assert_key_scope(db, user, key_id)
    try:
        result = await litellm_client.update_key(
            key_id=key_id,
            models=data.models,
            max_budget=data.max_budget,
            budget_duration=data.budget_duration,
            rpm_limit=data.rpm_limit,
            tpm_limit=data.tpm_limit,
            duration=data.duration,
        )
        await log_operation(
            db, actor_id=user.id, action="litellm_key.update",
            target_type="litellm_key", target_id=key_id,
            detail={"fields": [k for k in data.model_fields_set if getattr(data, k) is not None]},
        )
        await db.commit()
        return result
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_key.update",
            target_type="litellm_key", target_id=key_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _assert_key_scope(db, user, key_id)
    try:
        await litellm_client.delete_key(key_id)
        await log_operation(
            db, actor_id=user.id, action="litellm_key.delete",
            target_type="litellm_key", target_id=key_id,
        )
        await db.commit()
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_key.delete",
            target_type="litellm_key", target_id=key_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.post("/keys/{key_id}/block")
async def block_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _assert_key_scope(db, user, key_id)
    try:
        await litellm_client.block_key(key_id)
        await log_operation(
            db, actor_id=user.id, action="litellm_key.block",
            target_type="litellm_key", target_id=key_id,
        )
        await db.commit()
        return {"blocked": key_id}
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_key.block",
            target_type="litellm_key", target_id=key_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


@router.post("/keys/{key_id}/unblock")
async def unblock_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _assert_key_scope(db, user, key_id)
    try:
        await litellm_client.unblock_key(key_id)
        await log_operation(
            db, actor_id=user.id, action="litellm_key.unblock",
            target_type="litellm_key", target_id=key_id,
        )
        await db.commit()
        return {"unblocked": key_id}
    except litellm_client.LitellmError as e:
        await log_operation(
            db, actor_id=user.id, action="litellm_key.unblock",
            target_type="litellm_key", target_id=key_id,
            status="failure", detail={"error": e.message},
        )
        await db.commit()
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


async def _assert_key_scope(db: AsyncSession, user: User, key_id: str) -> None:
    """校验当前用户对指定 key（token）的管理权限。

    平台管理员放行；否则列出用户所属组的全部 key，要求目标 token 在其中。
    """
    if is_platform_admin(user):
        return
    gids = await _user_group_ids(db, user)
    if not gids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理该 Key")
    try:
        for gid in gids:
            keys = await litellm_client.list_keys(team_id=str(gid))
            if any(k.get("token") == key_id for k in keys):
                return
    except litellm_client.LitellmError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理该 Key")


# ── 用量 / 成本 ─────────────────────────────────────────


@router.get("/spend")
async def get_spend(
    group_id: str = Query(None),
    key_id: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询用量。平台管理员可指定任意 group（含 'default'）；组管理员仅本组。"""
    team_id = await _resolve_group_filter(db, user, group_id)
    if team_id is None and not is_platform_admin(user):
        # 组管理员不指定 group 时聚合其所有组
        gids = await _user_group_ids(db, user)
        if not gids:
            return {"logs": [], "total_spend": 0}
        team_id = str(gids[0])

    start_date, end_date = normalize_spend_dates(start_date, end_date)
    try:
        logs = await litellm_client.spend_logs(
            start_date=start_date,
            end_date=end_date,
            team_id=team_id,
            api_key=key_id,
            limit=200,
        )
        # 花费 USD → CNY 展示
        for lg in (logs.get("data") or logs.get("logs") or []) if isinstance(logs, dict) else (logs or []):
            if "spend" in lg:
                lg["spend"] = _to_cny(lg.get("spend"))
        if isinstance(logs, dict) and "total_spend" in logs:
            logs["total_spend"] = _to_cny(logs.get("total_spend"))
        return logs
    except litellm_client.LitellmError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


