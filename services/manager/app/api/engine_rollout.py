"""引擎镜像滚动发布 API — /api/manager/engine-rollout

发版后存量引擎 Deployment 的 image 不随 manager 的 UA_ENGINE_IMAGE 更新，本接口触发
后台分批把所有引擎 Deployment 的 image 滚到目标镜像（替代手动 kubectl set image）。

平台管理员可用。文案不暴露 K8s 实现细节（CLAUDE.md）。
"""

from uuid import UUID

from app.core.auth import require_platform_admin
from app.models import User
from app.services import engine_rollout_service
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/engine-rollout", tags=["engine-rollout"])


class RolloutCreate(BaseModel):
    engine_type: str = Field(..., description="引擎类型（如 HERMES）；不同类型镜像不同，需分别发起")
    target_image: str | None = Field(default=None, description="目标镜像；缺省取当前引擎运行时配置")
    batch_size: int = Field(default=5, ge=1, le=50, description="并发滚动引擎数")
    force_repull: bool = Field(default=False, description="复用同一 tag 时强制重新拉取镜像")
    dry_run: bool = Field(default=False, description="只预览对比，不落库不执行")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_engine_rollout(
    body: RolloutCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """触发引擎镜像滚动发布（dry_run 只预览）。

    返回字段文案：running=待升级并等待就绪，suspended=待升级（休眠中，下次恢复生效），
    skipped=跳过（无资源或外部实例）。
    """
    try:
        result = await engine_rollout_service.create_rollout(
            db,
            engine_type=body.engine_type,
            target_image=body.target_image,
            batch_size=body.batch_size,
            force_repull=body.force_repull,
            dry_run=body.dry_run,
            triggered_by=user.id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e) or "参数校验失败")
    except Exception:
        raise HTTPException(status_code=500, detail="升级任务创建失败，请重试")


@router.get("/{rollout_id}")
async def get_engine_rollout(
    rollout_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """查询某次滚动发布进度（含每个引擎的处理明细）。"""
    result = await engine_rollout_service.get_rollout(db, rollout_id)
    if result is None:
        raise HTTPException(status_code=404, detail="升级任务不存在")
    return result


@router.get("")
async def list_engine_rollouts(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """查询历史滚动发布记录。"""
    return await engine_rollout_service.list_rollouts(db, limit=limit)
