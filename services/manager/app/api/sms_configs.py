"""短信服务商配置 API — multi-config CRUD + 按 provider 真实 SDK 探活。

v1 支持 aliyun/tencent/huawei 3 个 provider；每行一个配置，
全局一行 is_active=true（用作实际发码渠道）。AK/SK 用 Fernet 加密存。
"""

import asyncio
from uuid import UUID

from app.core.auth import require_platform_admin
from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import SmsConfig, User
from app.schemas import (
    SmsConfigCreate,
    SmsConfigResponse,
    SmsConfigUpdate,
    TestSmsResult,
)
from app.services.audit_service import log_operation
from app.services.sms_providers import get_probe
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/sms-configs", tags=["sms-configs"])


def _to_response(cfg: SmsConfig) -> SmsConfigResponse:
    return SmsConfigResponse(
        id=cfg.id,
        provider=cfg.provider,
        is_active=cfg.is_active,
        sign_name=cfg.sign_name,
        template_code=cfg.template_code,
        access_key_id_configured=bool(cfg.access_key_id_encrypted),
        access_key_secret_configured=bool(cfg.access_key_secret_encrypted),
        sdk_app_id=cfg.sdk_app_id,
        region=cfg.region,
        daily_limit=cfg.daily_limit,
        interval_seconds=cfg.interval_seconds,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=list[SmsConfigResponse])
async def list_sms_configs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """列出所有短信配置（active 行在最前）。"""
    stmt = select(SmsConfig).order_by(
        SmsConfig.is_active.desc(), SmsConfig.updated_at.desc()
    )
    cfgs = (await db.execute(stmt)).scalars().all()
    return [_to_response(c) for c in cfgs]


@router.post("", response_model=SmsConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_sms_config(
    data: SmsConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """创建新短信配置（不自动 activate）。一个 provider 只能建一条记录。"""
    existing = (
        await db.execute(select(SmsConfig.id).where(SmsConfig.provider == data.provider))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
        )

    cfg = SmsConfig(
        provider=data.provider,
        sign_name=data.sign_name,
        template_code=data.template_code,
        access_key_id_encrypted=(
            encrypt_credential(data.access_key_id) if data.access_key_id else None
        ),
        access_key_secret_encrypted=(
            encrypt_credential(data.access_key_secret)
            if data.access_key_secret
            else None
        ),
        sdk_app_id=data.sdk_app_id,
        region=data.region,
        daily_limit=data.daily_limit,
        interval_seconds=data.interval_seconds,
        created_by=user.id,
    )
    db.add(cfg)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
        )
    await log_operation(
        db,
        actor_id=user.id,
        action="sms_config.create",
        target_type="sms_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.put("/{config_id}", response_model=SmsConfigResponse)
async def update_sms_config(
    config_id: UUID,
    data: SmsConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """更新短信配置。access_key_* 留空不修改。改 provider 时校验唯一性。"""
    cfg = await db.get(SmsConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="短信配置不存在")

    if cfg.provider != data.provider:
        existing = (
            await db.execute(
                select(SmsConfig.id).where(
                    SmsConfig.provider == data.provider,
                    SmsConfig.id != config_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
            )

    cfg.provider = data.provider
    cfg.sign_name = data.sign_name
    cfg.template_code = data.template_code
    if data.access_key_id:
        cfg.access_key_id_encrypted = encrypt_credential(data.access_key_id)
    if data.access_key_secret:
        cfg.access_key_secret_encrypted = encrypt_credential(data.access_key_secret)
    cfg.sdk_app_id = data.sdk_app_id
    cfg.region = data.region
    cfg.daily_limit = data.daily_limit
    cfg.interval_seconds = data.interval_seconds

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
        )
    await log_operation(
        db,
        actor_id=user.id,
        action="sms_config.update",
        target_type="sms_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.post("/{config_id}/activate", response_model=SmsConfigResponse)
async def activate_sms_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """标记为 active（同事务内 deactivate 其他行）。partial unique index 保证全局仅一行 active。"""
    cfg = await db.get(SmsConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="短信配置不存在")
    # 先 deactivate 所有行
    await db.execute(update(SmsConfig).values(is_active=False))
    cfg.is_active = True
    await db.flush()
    await log_operation(
        db,
        actor_id=user.id,
        action="sms_config.activate",
        target_type="sms_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.post("/{config_id}/deactivate", response_model=SmsConfigResponse)
async def deactivate_sms_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """取消 active（partial unique index 允许 0 行 active，deactivate 后全局无发码渠道）。
    幂等：对已 inactive 的行调用直接返回当前状态，不写审计日志。"""
    cfg = await db.get(SmsConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="短信配置不存在")
    if not cfg.is_active:
        return _to_response(cfg)
    cfg.is_active = False
    await db.flush()
    await log_operation(
        db,
        actor_id=user.id,
        action="sms_config.deactivate",
        target_type="sms_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sms_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    cfg = await db.get(SmsConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="短信配置不存在")
    await log_operation(
        db,
        actor_id=user.id,
        action="sms_config.delete",
        target_type="sms_config",
        target_id=config_id,
        detail={"provider": cfg.provider},
    )
    await db.delete(cfg)
    await db.commit()


@router.post("/{config_id}/test", response_model=TestSmsResult)
async def test_sms_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """按 provider 调对应 SDK 真实探活（只读 list 模板 API，不实际发短信）。"""
    cfg = await db.get(SmsConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="短信配置不存在")
    if not cfg.access_key_id_encrypted or not cfg.access_key_secret_encrypted:
        return TestSmsResult(ok=False, error="AK/SK 未配置")

    try:
        secrets = {
            "access_key_id": decrypt_credential(cfg.access_key_id_encrypted),
            "access_key_secret": decrypt_credential(cfg.access_key_secret_encrypted),
        }
    except Exception as e:
        return TestSmsResult(ok=False, error=f"密钥解密失败：{e}")

    probe = get_probe(cfg.provider)
    if not probe:
        return TestSmsResult(ok=False, error=f"不支持的 provider：{cfg.provider}")

    try:
        await asyncio.to_thread(probe, cfg, secrets)
    except Exception as e:
        msg = str(e)
        if (
            "Authentication" in msg
            or "auth" in msg.lower()
            or "Unauthorized" in msg
            or "401" in msg
        ):
            return TestSmsResult(ok=False, error="认证失败（AK/SK 错误或无权限）")
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            return TestSmsResult(ok=False, error="连接超时（请检查 region/网络）")
        if (
            "Connection" in msg
            or "connect" in msg.lower()
            or "UnknownEndpoint" in msg
        ):
            return TestSmsResult(ok=False, error="连接失败（请检查 region/主机）")
        return TestSmsResult(ok=False, error=f"探活失败：{msg}")

    return TestSmsResult(ok=True, error=None)
