"""引擎级系统配置 API — /api/manager/engine-configs

v1 只实现 Dify 引擎 + 全局配置（group_id NULL）。
外接模式下 admin_email/admin_password 可选：
  - 配了 → 走 Console API（下拉选应用）
  - 没配 → 走 Service API（手填 base_url+api_key，校验 /v1/info）
"""

from datetime import UTC, datetime
from uuid import UUID

from app.core.auth import require_platform_admin
from app.core.crypto import decrypt_credential, encrypt_credential
from app.core.dify_console_client import (
    DifyConsoleClient,
    DifyConsoleError,
    map_dify_mode_to_app_type,
)
from app.models import DifyEngineMode, EngineConfig, EngineType, User
from app.schemas import (
    DifyAppOption,
    DifyAppSelectResult,
    EngineConfigResponse,
    EngineConfigUpsert,
    TestConnectionResult,
    TestLangfuseResult,
)
from app.services.audit_service import log_operation
from app.services.dify_usage_collector import build_langfuse_config
from app.services.langfuse_client import list_traces
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/engine-configs", tags=["engine-configs"])


def _build_client(cfg: EngineConfig) -> DifyConsoleClient:
    """从 EngineConfig 构造 DifyConsoleClient。要求 EXTERNAL + 配了 admin 账号。"""
    if cfg.mode != DifyEngineMode.EXTERNAL:
        raise HTTPException(status_code=400, detail="仅 EXTERNAL 模式支持调用 Dify Console API")
    if not cfg.base_url:
        raise HTTPException(status_code=400, detail="base_url 未配置")
    if not cfg.admin_email or not cfg.admin_password_encrypted:
        raise HTTPException(status_code=400, detail="管理员账号未配置，无法调用 Console API")
    password = decrypt_credential(cfg.admin_password_encrypted)
    return DifyConsoleClient(base_url=cfg.base_url, email=cfg.admin_email, password=password)


def _to_response(cfg: EngineConfig) -> EngineConfigResponse:
    return EngineConfigResponse(
        id=cfg.id,
        engine_type=cfg.engine_type,
        mode=cfg.mode,
        base_url=cfg.base_url,
        admin_email=cfg.admin_email,
        admin_password_configured=bool(cfg.admin_password_encrypted),
        langfuse_host=cfg.langfuse_host,
        langfuse_public_key=cfg.langfuse_public_key,
        langfuse_secret_key_configured=bool(cfg.langfuse_secret_key_encrypted),
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=EngineConfigResponse | None)
async def get_engine_config(
    engine_type: EngineType = EngineType.DIFY,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """获取全局引擎配置（v1 全局唯一）。"""
    stmt = select(EngineConfig).where(
        EngineConfig.engine_type == engine_type,
        EngineConfig.group_id.is_(None),
    )
    cfg = (await db.execute(stmt)).scalar_one_or_none()
    if not cfg:
        return None
    return _to_response(cfg)


@router.post("", response_model=EngineConfigResponse)
async def upsert_engine_config(
    data: EngineConfigUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """创建或更新全局引擎配置（upsert）。"""
    if data.engine_type != EngineType.DIFY:
        raise HTTPException(status_code=400, detail="v1 仅支持 DIFY 引擎配置")

    # EXTERNAL 模式必须填 base_url；MANAGED 模式清空 base_url + admin 凭据
    if data.mode == DifyEngineMode.EXTERNAL and not data.base_url:
        raise HTTPException(status_code=400, detail="EXTERNAL 模式 base_url 必填")

    stmt = select(EngineConfig).where(
        EngineConfig.engine_type == data.engine_type,
        EngineConfig.group_id.is_(None),
    )
    cfg = (await db.execute(stmt)).scalar_one_or_none()

    if cfg is None:
        cfg = EngineConfig(
            engine_type=data.engine_type,
            mode=data.mode,
            base_url=data.base_url if data.mode == DifyEngineMode.EXTERNAL else None,
            admin_email=data.admin_email if data.mode == DifyEngineMode.EXTERNAL else None,
            admin_password_encrypted=(
                encrypt_credential(data.admin_password)
                if data.mode == DifyEngineMode.EXTERNAL and data.admin_password
                else None
            ),
            langfuse_host=data.langfuse_host if data.mode == DifyEngineMode.EXTERNAL else None,
            langfuse_public_key=(
                data.langfuse_public_key if data.mode == DifyEngineMode.EXTERNAL else None
            ),
            langfuse_secret_key_encrypted=(
                encrypt_credential(data.langfuse_secret_key)
                if data.mode == DifyEngineMode.EXTERNAL and data.langfuse_secret_key
                else None
            ),
            created_by=user.id,
        )
        db.add(cfg)
    else:
        cfg.mode = data.mode
        if data.mode == DifyEngineMode.MANAGED:
            cfg.base_url = None
            cfg.admin_email = None
            cfg.admin_password_encrypted = None
            cfg.cached_access_token_encrypted = None
            cfg.cached_token_expires_at = None
            cfg.langfuse_host = None
            cfg.langfuse_public_key = None
            cfg.langfuse_secret_key_encrypted = None
        else:
            cfg.base_url = data.base_url
            if data.admin_email is not None:
                cfg.admin_email = data.admin_email
            if data.admin_password:
                cfg.admin_password_encrypted = encrypt_credential(data.admin_password)
                # 密码改了，缓存的 token 失效
                cfg.cached_access_token_encrypted = None
                cfg.cached_token_expires_at = None
            # Langfuse 配置（留空不修改）
            if data.langfuse_host is not None:
                cfg.langfuse_host = data.langfuse_host
            if data.langfuse_public_key is not None:
                cfg.langfuse_public_key = data.langfuse_public_key
            if data.langfuse_secret_key:
                cfg.langfuse_secret_key_encrypted = encrypt_credential(data.langfuse_secret_key)

    is_new = cfg.id is None or not cfg.id
    await db.flush()
    await log_operation(
        db,
        actor_id=user.id,
        action="engine_config.upsert",
        target_type="engine_config",
        target_id=cfg.id,
        detail={
            "engine_type": cfg.engine_type.value,
            "mode": cfg.mode.value if hasattr(cfg.mode, "value") else str(cfg.mode),
            "base_url": cfg.base_url,
            "is_new": is_new,
        },
    )
    await db.commit()
    await db.refresh(cfg)
    return _to_response(cfg)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_engine_config(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    cfg = await db.get(EngineConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="引擎配置不存在")
    await log_operation(
        db,
        actor_id=user.id,
        action="engine_config.delete",
        target_type="engine_config",
        target_id=config_id,
        detail={"engine_type": cfg.engine_type.value, "mode": cfg.mode.value if hasattr(cfg.mode, "value") else str(cfg.mode)},
    )
    await db.delete(cfg)
    await db.commit()


@router.post("/{config_id}/test-connection", response_model=TestConnectionResult)
async def test_connection(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """测试 Dify 平台连接：login + list_apps 探活。"""
    cfg = await db.get(EngineConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="引擎配置不存在")

    if cfg.mode != DifyEngineMode.EXTERNAL or not cfg.base_url:
        return TestConnectionResult(ok=False, error="托管模式或 base_url 未配置，无法测试连接")

    if not cfg.admin_email or not cfg.admin_password_encrypted:
        # 未配管理员账号 → 走 Service API /info 探活
        return TestConnectionResult(
            ok=False,
            error="未配管理员账号，无法测试 Console 连接；请在智能体定义编辑页用'校验'按钮测试 API Key",
        )

    try:
        client = _build_client(cfg)
        token, expires_at = await client.login()
        # 缓存 token 到 DB（避免每次都登录）
        cfg.cached_access_token_encrypted = encrypt_credential(token)
        cfg.cached_token_expires_at = expires_at
        await db.commit()
        apps = await client.list_apps()
        return TestConnectionResult(ok=True, apps_count=len(apps))
    except DifyConsoleError as e:
        return TestConnectionResult(ok=False, error=str(e))
    except Exception as e:
        return TestConnectionResult(ok=False, error=f"未知错误：{e}")


@router.post("/{config_id}/test-langfuse", response_model=TestLangfuseResult)
async def test_langfuse_connection(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """测试 Langfuse 连接：用 EngineConfig 的 Langfuse 凭据调 list_traces(limit=1) 探活。

    返回近 30 天 trace 总数（meta.totalItems）作为连通性 + 数据存在的双重验证。
    """
    cfg = await db.get(EngineConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="引擎配置不存在")

    lf_config = build_langfuse_config(cfg)
    if lf_config is None:
        return TestLangfuseResult(
            ok=False,
            error="未配置 Langfuse 凭据（host/public_key/secret_key 需配齐）",
        )

    from datetime import timedelta
    now = datetime.now(UTC)
    try:
        resp = await list_traces(
            from_ts=(now - timedelta(days=30)).isoformat(),
            to_ts=now.isoformat(),
            limit=1,
            offset=0,
            config=lf_config,
        )
        if resp is None:
            return TestLangfuseResult(ok=False, error="Langfuse 返回空响应")
        meta = resp.get("meta") or {}
        total = meta.get("totalItems")
        # totalItems 可能 None（老版本 Langfuse），降级用 data 长度
        count = int(total) if total is not None else len(resp.get("data") or [])
        return TestLangfuseResult(ok=True, trace_count=count)
    except Exception as e:
        return TestLangfuseResult(ok=False, error=f"Langfuse 调用失败：{e}")


@router.get("/{config_id}/dify-apps", response_model=list[DifyAppOption])
async def list_dify_apps(
    config_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """列出 Dify 平台所有可用应用（过滤掉 completion 模式）。前端下拉用。"""
    cfg = await db.get(EngineConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="引擎配置不存在")

    client = _build_client(cfg)

    # 用缓存的 token（如有）
    cached_token = None
    cached_expires_at = None
    if cfg.cached_access_token_encrypted and cfg.cached_token_expires_at:
        try:
            cached_token = decrypt_credential(cfg.cached_access_token_encrypted)
            cached_expires_at = cfg.cached_token_expires_at
        except Exception:
            cached_token = None

    try:
        await client.ensure_token(cached_token, cached_expires_at)
        apps = await client.list_apps()
    except DifyConsoleError as e:
        # 401 时 ensure_token 内部已重试过一次 login，仍然失败才抛这里
        if e.is_auth_error and cfg.admin_password_encrypted:
            # 重新登录并缓存新 token
            try:
                new_token, new_expires = await client.login()
                cfg.cached_access_token_encrypted = encrypt_credential(new_token)
                cfg.cached_token_expires_at = new_expires
                await db.commit()
                apps = await client.list_apps()
            except DifyConsoleError as e2:
                raise HTTPException(status_code=400, detail=f"调用 Dify 失败：{e2}") from e2
        else:
            raise HTTPException(status_code=400, detail=f"调用 Dify 失败：{e}") from e

    return [
        DifyAppOption(
            id=str(a.get("id")),
            name=a.get("name") or "",
            mode=a.get("mode") or "",
            description=a.get("description"),
        )
        for a in apps
    ]


@router.post("/{config_id}/dify-apps/{app_id}/select", response_model=DifyAppSelectResult)
async def select_dify_app(
    config_id: UUID,
    app_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """选中 Dify 应用 → 拿到 api_key + app_type + app_name，返回完整 dify 配置。

    流程：
      1. 调 Console API 拉应用列表，找到 app_id 对应的 name + mode
      2. 调 GET /console/api/apps/{app_id}/api-keys 拿现有 key
      3. 没有则 POST 创建一个
    """
    cfg = await db.get(EngineConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="引擎配置不存在")

    client = _build_client(cfg)
    cached_token = None
    cached_expires_at = None
    if cfg.cached_access_token_encrypted and cfg.cached_token_expires_at:
        try:
            cached_token = decrypt_credential(cfg.cached_access_token_encrypted)
            cached_expires_at = cfg.cached_token_expires_at
        except Exception:
            cached_token = None

    try:
        await client.ensure_token(cached_token, cached_expires_at)
        apps = await client.list_apps()
        target = next((a for a in apps if str(a.get("id")) == app_id), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"未在 Dify 工作区找到应用 {app_id}")

        app_type = map_dify_mode_to_app_type(target.get("mode"))
        if not app_type:
            raise HTTPException(status_code=400, detail=f"应用 mode {target.get('mode')!r} 不支持")

        # 拉应用已有 api-key
        keys = await client.get_app_api_keys(app_id)
        if keys:
            api_key = keys[0].get("token")
            if not api_key:
                raise HTTPException(status_code=500, detail="Dify 返回的 api-key 缺少 token 字段")
        else:
            # 没有则创建
            new_key = await client.create_app_api_key(app_id)
            api_key = new_key.get("token")
            if not api_key:
                raise HTTPException(status_code=500, detail="Dify 创建 api-key 失败")
    except DifyConsoleError as e:
        raise HTTPException(status_code=400, detail=f"调用 Dify 失败：{e}") from e

    return DifyAppSelectResult(
        base_url=cfg.base_url or "",
        app_id=app_id,
        app_name=target.get("name") or "",
        app_type=app_type,
        app_api_key=api_key,
    )
