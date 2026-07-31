"""邮件服务商配置 API — /api/manager/email-configs

multi-config CRUD：每行一个 provider 配置，全局一行 is_active=true（用作实际发码渠道）。
v1 支持 smtp + aliyun/tencent/huawei 4 个 provider；探活按 provider 调对应 SDK（app/services/email_providers/）。
v2 接入发码 endpoint 走 auth.py。
"""

import asyncio
import importlib
from uuid import UUID

from app.core.auth import require_platform_admin
from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import EmailConfig, User
from app.schemas import (
    EmailConfigCreate,
    EmailConfigResponse,
    EmailConfigUpdate,
    TestEmailResult,
)
from app.services.audit_service import log_operation
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/email-configs", tags=["email-configs"])


def _get_probe(provider: str):
    """按 provider 动态加载对应模块的 probe 函数（便于测试 monkeypatch 单个 provider 模块）。"""
    module_name = {
        "smtp": "smtp_provider",
        "aliyun": "aliyun_provider",
        "tencent": "tencent_provider",
        "huawei": "huawei_provider",
    }.get(provider)
    if not module_name:
        return None
    module = importlib.import_module(f"app.services.email_providers.{module_name}")
    return getattr(module, "probe", None)


def _to_response(cfg: EmailConfig) -> EmailConfigResponse:
    return EmailConfigResponse(
        id=cfg.id,
        provider=cfg.provider,
        is_active=cfg.is_active,
        smtp_host=cfg.smtp_host,
        smtp_port=cfg.smtp_port,
        encryption=cfg.encryption,
        username=cfg.username,
        password_configured=bool(cfg.password_encrypted),
        access_key_id_configured=bool(cfg.access_key_id_encrypted),
        access_key_secret_configured=bool(cfg.access_key_secret_encrypted),
        region=cfg.region,
        from_email=cfg.from_email,
        from_name=cfg.from_name,
        daily_limit=cfg.daily_limit,
        interval_seconds=cfg.interval_seconds,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=list[EmailConfigResponse])
async def list_email_configs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """列出所有邮件配置（active 行在最前，按更新时间倒序）。"""
    stmt = select(EmailConfig).order_by(
        EmailConfig.is_active.desc(),
        EmailConfig.updated_at.desc(),
    )
    cfgs = (await db.execute(stmt)).scalars().all()
    return [_to_response(c) for c in cfgs]


@router.post("", response_model=EmailConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_email_config(
    data: EmailConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """创建新邮件配置（不自动 activate）。一个 provider 只能建一条记录。"""
    existing = (
        await db.execute(
            select(EmailConfig.id).where(EmailConfig.provider == data.provider)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
        )

    cfg = EmailConfig(
        provider=data.provider,
        smtp_host=data.smtp_host,
        smtp_port=data.smtp_port,
        encryption=data.encryption,
        username=data.username,
        access_key_id_encrypted=encrypt_credential(data.access_key_id) if data.access_key_id else None,
        access_key_secret_encrypted=encrypt_credential(data.access_key_secret) if data.access_key_secret else None,
        region=data.region,
        from_email=data.from_email,
        from_name=data.from_name,
        daily_limit=data.daily_limit,
        interval_seconds=data.interval_seconds,
        created_by=user.id,
    )
    if data.provider == "smtp":
        # schema model_validator 已保证 password 非空
        cfg.password_encrypted = encrypt_credential(data.password)
    # cloud provider 必填字段已在 schema model_validator 完成

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
        action="email_config.create",
        target_type="email_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.put("/{config_id}", response_model=EmailConfigResponse)
async def update_email_config(
    config_id: UUID,
    data: EmailConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """更新邮件配置。password/access_key_* 留空不修改。改 provider 时校验唯一性。"""
    cfg = await db.get(EmailConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="邮件配置不存在")

    if cfg.provider != data.provider:
        existing = (
            await db.execute(
                select(EmailConfig.id).where(
                    EmailConfig.provider == data.provider,
                    EmailConfig.id != config_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="provider_in_use"
            )

    cfg.provider = data.provider
    cfg.smtp_host = data.smtp_host
    cfg.smtp_port = data.smtp_port
    cfg.encryption = data.encryption
    cfg.username = data.username
    if data.password:
        cfg.password_encrypted = encrypt_credential(data.password)
    if data.access_key_id:
        cfg.access_key_id_encrypted = encrypt_credential(data.access_key_id)
    if data.access_key_secret:
        cfg.access_key_secret_encrypted = encrypt_credential(data.access_key_secret)
    cfg.region = data.region
    cfg.from_email = data.from_email
    cfg.from_name = data.from_name
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
        action="email_config.update",
        target_type="email_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.post("/{config_id}/activate", response_model=EmailConfigResponse)
async def activate_email_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """标记为 active（同事务内 deactivate 其他行）。partial unique index 保证全局仅一行 active。"""
    cfg = await db.get(EmailConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="邮件配置不存在")
    # 先 deactivate 所有行
    await db.execute(update(EmailConfig).values(is_active=False))
    cfg.is_active = True
    await db.flush()
    await log_operation(
        db,
        actor_id=user.id,
        action="email_config.activate",
        target_type="email_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.post("/{config_id}/deactivate", response_model=EmailConfigResponse)
async def deactivate_email_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """取消 active（partial unique index 允许 0 行 active，deactivate 后全局无发码渠道）。
    幂等：对已 inactive 的行调用直接返回当前状态，不写审计日志。"""
    cfg = await db.get(EmailConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="邮件配置不存在")
    if not cfg.is_active:
        return _to_response(cfg)
    cfg.is_active = False
    await db.flush()
    await log_operation(
        db,
        actor_id=user.id,
        action="email_config.deactivate",
        target_type="email_config",
        target_id=cfg.id,
        detail={"provider": cfg.provider},
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_email_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    cfg = await db.get(EmailConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="邮件配置不存在")
    await log_operation(
        db,
        actor_id=user.id,
        action="email_config.delete",
        target_type="email_config",
        target_id=config_id,
        detail={"provider": cfg.provider, "was_active": cfg.is_active},
    )
    await db.delete(cfg)
    await db.commit()


@router.post("/{config_id}/test", response_model=TestEmailResult)
async def test_email_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """按 provider 调对应 SDK 真实探活（只读 list API，不实际发邮件）。

    同步 SDK 调用通过 asyncio.to_thread 跑在线程池，避免阻塞 event loop。
    """
    cfg = await db.get(EmailConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="邮件配置不存在")

    # 准备 secrets（解密）
    secrets = {}
    try:
        if cfg.provider == "smtp":
            if not cfg.password_encrypted:
                return TestEmailResult(ok=False, error="SMTP 密码未配置")
            if not cfg.smtp_host or not cfg.username:
                return TestEmailResult(ok=False, error="SMTP 主机 / 用户名未配置")
            secrets["password"] = decrypt_credential(cfg.password_encrypted)
        else:
            if not cfg.access_key_id_encrypted or not cfg.access_key_secret_encrypted:
                return TestEmailResult(ok=False, error="AK/SK 未配置")
            secrets["access_key_id"] = decrypt_credential(cfg.access_key_id_encrypted)
            secrets["access_key_secret"] = decrypt_credential(cfg.access_key_secret_encrypted)
    except Exception as e:
        return TestEmailResult(ok=False, error=f"密钥解密失败：{e}")

    probe = _get_probe(cfg.provider)
    if not probe:
        return TestEmailResult(ok=False, error=f"不支持的 provider：{cfg.provider}")

    try:
        # 同步 SDK 调用放到线程池，避免阻塞 event loop
        await asyncio.to_thread(probe, cfg, secrets)
    except Exception as e:
        msg = str(e)
        # 常见错误信息分类（不暴露内部堆栈给前端）
        if "Authentication" in msg or "auth" in msg.lower() or "Unauthorized" in msg:
            return TestEmailResult(ok=False, error="认证失败（AK/SK 错误或无权限）")
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            return TestEmailResult(ok=False, error="连接超时（请检查 region/网络）")
        if "Connection" in msg or "connect" in msg.lower() or "UnknownEndpoint" in msg:
            return TestEmailResult(ok=False, error="连接失败（请检查 region/主机）")
        return TestEmailResult(ok=False, error=f"探活失败：{msg}")

    return TestEmailResult(ok=True, error=None)
