"""Chat 仪表盘 + 模型列表 API — /api/controller/chat/* 与 /agents/{id}/models

只读、无状态端点，从 router.py 拆出，路径不变。
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.database import get_db as get_manager_db

from ._common import load_instance_config

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/api/controller/agents/{agent_id}/models")
async def get_agent_models(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """返回 Agent 可用模型列表。

    引擎只走 LiteLLM：用 Agent 的 virtual key 调 LiteLLM /v1/models，
    返回该 key 有权访问的模型组（即 model_config.litellm.model_group）。
    """
    inst_cfg = await load_instance_config(db, agent_id)
    if not inst_cfg:
        raise HTTPException(status_code=404, detail="Agent instance not found")

    model_config = inst_cfg["model_config"]
    litellm = model_config.get("litellm") or {}
    api_key = litellm.get("key")
    if not api_key:
        return {"object": "list", "data": []}

    base = settings.litellm_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return {"object": "list", "data": data}
    except Exception as e:
        logger.warning(f"Failed to list models from LiteLLM for agent {agent_id}: {e}")
        # 兜底：返回配置的 model_group
        mg = litellm.get("model_group") or litellm.get("model")
        fallback = [{"id": mg, "object": "model"}] if mg else []
        return {"object": "list", "data": fallback}


@router.get("/api/controller/chat/dashboard/config")
async def chat_dashboard_config():
    """前端仪表盘配置（探活）"""
    return {
        "default_workspace": "/workspace",
        "onboarding_completed": True,
        "version": "0.6.2",
        "extensions": {"enabled": False, "script_urls": [], "stylesheet_urls": []},
    }


@router.get("/api/controller/chat/dashboard/status")
async def chat_dashboard_status():
    """仪表盘状态"""
    return {
        "agent_available": True,
        "gateway_connected": True,
        "agent_version": "0.5.1",
    }


@router.get("/api/controller/chat/settings")
async def chat_settings():
    """会话设置"""
    return {
        "default_workspace": "/workspace",
        "onboarding_completed": True,
    }


@router.get("/api/controller/chat/models")
async def chat_models():
    """返回可用模型列表"""
    return {"object": "list", "data": [{"id": "hermes-agent", "object": "model"}]}
