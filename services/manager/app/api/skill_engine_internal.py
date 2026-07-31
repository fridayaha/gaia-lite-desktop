"""Skill Engine 内部 API —— 供 skill-engine 服务调用的内部接口。

提供平台预制 LLM 凭证，skill-engine 启动引擎实例时获取 LiteLLM key。
校验 X-Internal-Token 头防止未授权访问。
"""
import logging

from fastapi import APIRouter, Header, HTTPException

from pkg.common.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skill-engine-internal"])


@router.get("/api/manager/internal/skill-engine/litellm-key")
async def get_litellm_key(
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
) -> dict:
    """获取平台预制的 LiteLLM API key 和 base URL。

    skill-engine 调用此接口获取 LLM 凭证，通过环境变量注入引擎实例，
    不落盘，实例停止即清除。
    """
    if not settings.internal_token or x_internal_token != settings.internal_token:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    return {
        "api_key": settings.litellm_master_key,
        "base_url": settings.litellm_base_url,
    }
