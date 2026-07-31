"""智能体技能管理 API — 列表/预览/安装/开关/卸载/排序 + 技能市场（预留）。

V3：技能属于定义层。元数据存于 agent_definitions.skill_config JSON（{skills:[...], order:[...]}）；
技能文件本体由 controller fan-out 到该定义各实例的 Pod /opt/data/skills/{short_instance}/{name}/。
开关热生效（改 config.yaml 的 skills.disabled），安装/卸载触发 Pod 滚动重启。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from app.api.agent_definitions import _require_definition
from app.core.auth import get_current_user
from app.core.crypto import decrypt_credentials_dict, encrypt_credentials_dict
from app.core.group_scope import get_current_group_ids
from app.models import AgentDefinition, AgentInstance, AgentStatus, SkillCredential, User
from app.services.audit_service import log_operation
from app.services.preset_skills import BANNED_SKILL_NAMES
from app.worker import client as controller_client
from app.worker.minio_archiver import archiver
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from pkg.common.config import settings
from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager", tags=["agent-skills"])

logger = logging.getLogger(__name__)


# ── 辅助 ──────────────────────────────────────────────────


def _normalize_skill_name(raw: str) -> str:
    """照搬 hermes-webui 校验：lower + 空格转 -，禁 / 与 ..。"""
    name = (raw or "").strip().lower().replace(" ", "-")
    if not name or "/" in name or ".." in name or "\\" in name:
        raise HTTPException(status_code=400, detail="无效的技能名称")
    return name


def _load_skill_config(agent) -> dict:
    """返回 definition.skill_config 的深拷贝供就地修改。

    必须深拷贝：若直接返回 ORM 属性引用，就地修改（s["enabled"]=False）会同时
    改写 SQLAlchemy 的 committed-state 基线（同一 dict 对象），导致 flush 时
    对比 current==committed 跳过 UPDATE，commit 不落库。
    """
    sc = agent.skill_config or {}
    if isinstance(sc, str):
        sc = json.loads(sc)
    if not isinstance(sc, dict):
        return {"skills": [], "order": []}
    # JSON 列可序列化，round-trip 得到独立深拷贝
    sc = json.loads(json.dumps(sc))
    sc.setdefault("skills", [])
    sc.setdefault("order", [])
    return sc


def _skill_view(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "description": s.get("description", ""),
        "icon": s.get("icon", "ri:apps-2-line"),
        "enabled": s.get("enabled", True),
        "version": s.get("version", "1.0.0"),
        "author": s.get("author", ""),
        "config": s.get("config", {}),
        "configParams": s.get("config_params", []),
        "usageCount": s.get("usage_count", 0),
        "engine": s.get("engine", ["HERMES"]),
        "builtin": bool(s.get("builtin", False)),
        "source": s.get("source", "local"),
        "installed": True,
    }


def _builtin_view(name: str, meta: dict) -> dict:
    """引擎扫描到、但 skill_config 无记录的内置技能视图。"""
    return {
        "id": f"builtin-{name}",
        "name": name,
        "description": meta.get("description", "") or "",
        "icon": "ri:apps-2-line",
        "enabled": True,  # 未在 skills.disabled 列表即启用
        "version": str(meta.get("version", "") or ""),
        "author": meta.get("author", "") or "",
        "config": {},
        "configParams": [],
        "usageCount": 0,
        "engine": ["HERMES"],
        "builtin": True,
        "source": "builtin",
        "installed": True,
    }


def _ordered_skills(sc: dict) -> list[dict]:
    skills = sc.get("skills", [])
    order = sc.get("order", [])
    by_id = {s["id"]: s for s in skills if s.get("id")}
    ordered = [by_id[i] for i in order if i in by_id]
    # 追加未在 order 中的
    ordered += [s for s in skills if s.get("id") and s["id"] not in order]
    # 过滤平台禁用 skill（越狱/安全风险）
    ordered = [s for s in ordered if (s.get("name") or "").lower() not in BANNED_SKILL_NAMES]
    return [_skill_view(s) for s in ordered]


def _parse_skill_md_frontmatter(text: str) -> dict:
    """解析 SKILL.md 的 YAML frontmatter（首段 --- 之间），返回 dict。

    对齐 controller 引擎扫描脚本与 hermes-webui 做法。无 frontmatter 返回 {}。
    """
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fmtxt = parts[1]
    try:
        import yaml  # noqa: PLC0415

        return yaml.safe_load(fmtxt) or {}
    except Exception:  # noqa: BLE001
        # fallback：简单 key: value 行解析
        fm: dict = {}
        for line in fmtxt.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = (v or "").strip().strip('"').strip("'")
        return fm


def _normalize_engine(raw: object) -> list[str]:
    """把 manifest.engine 归一为大写数组形态（下游 view.engine 期望 list）。

    manifest canonical 写法是小写字符串（"hermes"/"openclaw"），但历史包可能写
    数组或大写。统一收敛：字符串 → [upper]；数组 → [upper(x) for x in 非
    空项]；缺省/非法 → ["HERMES"]。
    """
    if isinstance(raw, str) and raw.strip():
        return [raw.strip().upper()]
    if isinstance(raw, list) and raw:
        norm = [str(x).strip().upper() for x in raw if str(x).strip()]
        return norm or ["HERMES"]
    return ["HERMES"]


def _parse_zip(file_bytes: bytes) -> tuple[dict, list[str], bytes]:
    """解析技能 zip：返回 (manifest_view, warnings, 原始 bytes)。

    要求含 SKILL.md（必填），manifest.json 可选（提供元数据）。
    元数据优先级：manifest.json > SKILL.md frontmatter > 路径兜底。
    校验路径安全（无 .. / 绝对路径）。
    """
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的 zip 文件")

    names = zf.namelist()
    # 路径安全校验
    for n in names:
        if n.startswith("/") or ".." in n.split("/") or "\\" in n:
            raise HTTPException(status_code=400, detail=f"zip 含不安全路径: {n}")

    # 找 SKILL.md（根或单层目录下）
    skill_md_candidates = [n for n in names if n.endswith("SKILL.md")]
    if not skill_md_candidates:
        raise HTTPException(status_code=400, detail="zip 缺少 SKILL.md")

    # SKILL.md frontmatter（manifest.json 缺失时的主来源）
    skill_md_text = zf.read(skill_md_candidates[0]).decode("utf-8", errors="replace")
    fm = _parse_skill_md_frontmatter(skill_md_text)

    # manifest.json（可选，优先级高于 frontmatter）
    manifest = {}
    manifest_candidates = [n for n in names if n.endswith("manifest.json")]
    if manifest_candidates:
        try:
            manifest = json.loads(zf.read(manifest_candidates[0]))
        except Exception:  # noqa: BLE001
            warnings.append("manifest.json 解析失败，使用默认值")

    # name 优先级：manifest.json > SKILL.md frontmatter > SKILL.md 路径兜底
    raw_name = manifest.get("name") or fm.get("name") or ""
    if raw_name:
        name = _normalize_skill_name(raw_name)
    else:
        cand = skill_md_candidates[0]
        fallback = cand.split("/")[-2] if "/" in cand else "skill"
        name = _normalize_skill_name(fallback)

    view = {
        "id": str(uuid4()),
        "name": name,
        "description": manifest.get("description") or fm.get("description", "") or "",
        "icon": manifest.get("icon", "ri:apps-2-line"),
        "enabled": True,
        "version": manifest.get("version") or str(fm.get("version", "") or "") or "1.0.0",
        "author": manifest.get("author") or fm.get("author", "") or "",
        "config": {},
        "configParams": manifest.get("config_params", []),
        "usageCount": 0,
        "engine": _normalize_engine(manifest.get("engine")),
    }
    return view, warnings, file_bytes


# ── 定义层辅助：定位定义的实例（fan-out 目标）──────────────


async def _definition_instance_ids(db: AsyncSession, definition_id: UUID) -> list[str]:
    """定义下所有实例 id（技能 fan-out 目标）。

    当前迁移数据 1:1（一定义一实例）；多实例时 fan-out 到全部。
    仅返回 PUBLISHED 实例（草稿实例通常未部署 Pod）。
    """
    res = await db.execute(
        select(AgentInstance.id).where(
            AgentInstance.definition_id == definition_id,
            AgentInstance.status == AgentStatus.PUBLISHED,
        )
    )
    return [str(r) for r in res.scalars().all()]


async def _first_deployed_instance_id(db: AsyncSession, definition_id: UUID) -> str | None:
    """取定义下第一个实例 id，用于引擎 skills 目录扫描（list_engine_skills）。"""
    res = await db.execute(
        select(AgentInstance.id)
        .where(AgentInstance.definition_id == definition_id)
        .order_by(AgentInstance.created_at)
        .limit(1)
    )
    row = res.scalar_one_or_none()
    return str(row) if row else None


# ── 列表 ──────────────────────────────────────────────────


@router.get("/agent-definitions/{definition_id}/skills")
async def list_skills(
    definition_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """合并 skill_config 记录与引擎扫描结果。

    - 引擎已部署：引擎扫描到的(builtin+installed) 与 config 记录合并；
      config 有但引擎没扫到的标记 installed=False（fan-out 未完成的孤儿）。
    - 引擎未部署：只返回 config 中已安装记录，engine_deployed=False。
    """
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    config_skills = sc.get("skills", [])
    config_by_name = {s["name"]: s for s in config_skills if s.get("name")}

    # 调 controller 扫描第一个实例的引擎 skills/ 目录
    scan_id = await _first_deployed_instance_id(db, definition_id)
    if not scan_id:
        return {"engineDeployed": False, "items": _ordered_skills(sc)}
    try:
        engine_res = await controller_client.list_engine_skills(scan_id)
    except controller_client.ControllerError:
        engine_res = {"engine_deployed": False, "items": []}
    engine_deployed = bool(engine_res.get("engine_deployed"))
    engine_items = engine_res.get("items") or []

    if not engine_deployed:
        return {"engineDeployed": False, "items": _ordered_skills(sc)}

    # 已部署：以引擎扫描为准，按 skill_config order 排序
    order = sc.get("order", [])
    by_id = {s["id"]: s for s in config_skills if s.get("id")}

    merged_by_name: dict[str, dict] = {}
    for it in engine_items:
        name = it.get("name") or ""
        if not name:
            continue
        # 平台禁用 skill（godmode/obliteratus 等越狱/安全风险）不在 catalog 暴露
        if name.lower() in BANNED_SKILL_NAMES:
            continue
        rec = config_by_name.get(name)
        view = _skill_view(rec) if rec else _builtin_view(name, it)
        merged_by_name[name] = view

    # 孤儿：config 有记录但引擎未扫到（fan-out 未完成 / 文件丢失）
    for rec in config_skills:
        name = rec.get("name")
        if not name or name.lower() in BANNED_SKILL_NAMES:
            continue
        if name not in merged_by_name:
            view = _skill_view(rec)
            view["installed"] = False
            merged_by_name[name] = view

    # 排序：用户安装的自定义技能排前（按安装顺序倒序，最近安装的最前），内置技能按 name 排后
    order_index = {sid: i for i, sid in enumerate(order)}

    def _sort_key(v: dict) -> tuple:
        if v.get("builtin"):
            # 内置按 name 排序
            return (1, v.get("name", ""))
        # 自定义按 order 倒序（最近 append 的排最前）；不在 order 的兜底最前
        return (0, -order_index.get(v.get("id", ""), -1))

    ordered = sorted(merged_by_name.values(), key=_sort_key)
    return {"engineDeployed": True, "items": ordered}


# ── 预览（不安装） ────────────────────────────────────────


@router.post("/agent-definitions/{definition_id}/skills/preview")
async def preview_skill(
    definition_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_definition(db, definition_id, group_ids)
    raw = await file.read()
    view, warnings, _ = _parse_zip(raw)
    return {"manifest": view, "warnings": warnings, "safe": True}


# ── 安装 ──────────────────────────────────────────────────

async def _install_skill_bytes(
    db: AsyncSession,
    definition_id: UUID,
    definition: AgentDefinition,
    raw: bytes,
    user: User,
    source: str = "local",
    hub_ref: dict | None = None,
) -> dict:
    """把已拿到的 ZIP 字节装到模版上：写 skill_config + audit + fan-out + 持久化。

    install_skill（本地上传）与 install_from_hub（从能力中心拉包）复用此核心。
    """
    view, _warnings, _ = _parse_zip(raw)

    sc = _load_skill_config(definition)
    skills = sc["skills"]
    # 同名覆盖：移除旧的
    skills = [s for s in skills if s.get("name") != view["name"]]
    record = {
        "id": view["id"],
        "name": view["name"],
        "description": view["description"],
        "icon": view["icon"],
        "enabled": True,
        "version": view["version"],
        "author": view["author"],
        "config_params": view["configParams"],
        "config": {},
        "source": source,
        "installed_at": datetime.now(UTC).isoformat(),
        "usage_count": 0,
        "engine": view["engine"],
    }
    if hub_ref:
        record["hub_item_id"] = str(hub_ref.get("hub_item_id"))
        record["hub_version_id"] = str(hub_ref.get("version_id"))
    skills.append(record)
    sc["skills"] = skills
    if record["id"] not in sc["order"]:
        sc["order"].append(record["id"])
    definition.skill_config = dict(sc)  # 新 dict 触发 SQLAlchemy JSON 变更检测
    flag_modified(definition, "skill_config")  # JSON 字段需显式标记，否则 commit 不发 UPDATE
    audit_detail: dict = {"skill_name": view["name"], "version": view["version"], "source": source}
    if hub_ref:
        audit_detail["hub_item_id"] = str(hub_ref.get("hub_item_id"))
        audit_detail["hub_version_id"] = str(hub_ref.get("version_id"))
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.install",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail=audit_detail,
    )
    # 持久化 zip 到 MinIO（COS 为重放真相源；deploy/重装/destroy→redeploy 取回 fan-out）。
    # 必须在 db.commit 前存：COS 失败 → raise → session 回滚（skill_config + 审计一起回滚），
    # 不留"DB 有元数据但 COS 无 zip"的孤儿（否则下次 deploy replay 取不到 → 技能静默丢失）。
    try:
        archiver.save_skill_zip(str(definition_id), view["name"], raw)
    except Exception:
        logger.warning(
            "save_skill_zip failed for %s/%s", str(definition_id)[:8], view["name"], exc_info=True
        )
        raise HTTPException(status_code=503, detail="技能包持久化失败，请稍后重试")

    await db.commit()

    # fan-out 到该定义各实例的引擎 Pod（解压 + 重生成 config.yaml + 回写 secrets.enc）。
    # fan-out 失败可自愈：DB + COS 已落库，reconcile_skills 会重放。
    zip_b64 = base64.b64encode(raw).decode("ascii")
    instance_ids = await _definition_instance_ids(db, definition_id)
    for iid in instance_ids:
        try:
            await controller_client.install_skill(iid, view["name"], zip_b64)
        except controller_client.ControllerError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

    return _skill_view(record)


@router.post("/agent-definitions/{definition_id}/skills/install")
async def install_skill(
    definition_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    definition = await _require_definition(db, definition_id, group_ids)
    raw = await file.read()
    return await _install_skill_bytes(db, definition_id, definition, raw, user, source="local")


class InstallFromHubRequest(BaseModel):
    """从能力中心订阅技能到模版。"""
    hub_item_id: UUID
    version_id: UUID


@router.post("/agent-definitions/{definition_id}/skills/install-from-hub")
async def install_skill_from_hub(
    definition_id: UUID,
    body: InstallFromHubRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """从 hub 能力中心拉取指定版本的技能包，安装到智能体模版。

    订阅链路：hub capability → 模版 skill_config → fan-out 实例 Pod。
    安装后由模版 publish 新版本 + instance upgrade 完成版本化生效。
    """
    definition = await _require_definition(db, definition_id, group_ids)

    hub_ref = {"hub_item_id": body.hub_item_id, "version_id": body.version_id}
    upstream = (
        f"{settings.hub_base_url.rstrip('/')}/api/hub/exports/items/"
        f"{body.hub_item_id}/versions/{body.version_id}/package"
    )
    # manager 服务端注入身份头（与 hub_proxy.py 角色映射一致），hub 以 platform_admin 放行
    headers = {
        "X-Actor-ID": str(user.id),
        "X-Actor-Type": "user",
        "X-User-Name": user.username or "",
        "X-Roles": "platform_admin",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(upstream, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"hub not available: {e}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="hub 能力或版本不存在")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"hub 拉包失败: {resp.text[:200]}",
        )
    raw = resp.content
    return await _install_skill_bytes(
        db, definition_id, definition, raw, user, source="hub", hub_ref=hub_ref
    )


# ── 排序（必须在 /skills/{skill_id} 之前注册，避免 "order" 被当作 skill_id） ──


@router.put("/agent-definitions/{definition_id}/skills/order")
async def update_order(
    definition_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    sc["order"] = list(body.get("skill_ids", []))
    definition.skill_config = dict(sc)
    flag_modified(definition, "skill_config")
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.reorder",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail={"order_count": len(sc["order"])},
    )
    await db.commit()
    return {"ok": True}


# ── 开关（热生效，不重启） ────────────────────────────────


@router.put("/agent-definitions/{definition_id}/skills/{skill_id}")
async def toggle_skill(
    definition_id: UUID,
    skill_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    enabled = bool(body.get("enabled"))
    definition = await _require_definition(db, definition_id, group_ids)

    sc = _load_skill_config(definition)
    target = None
    for s in sc["skills"]:
        if s.get("id") == skill_id:
            s["enabled"] = enabled
            target = s
            break
    if not target:
        # 内置技能首次开关：在 skill_config 创建记录，sync 时写入 skills.disabled
        if skill_id.startswith("builtin-"):
            name = skill_id[len("builtin-") :]
            target = {
                "id": skill_id,
                "name": name,
                "description": body.get("description", "") or "",
                "icon": "ri:apps-2-line",
                "enabled": enabled,
                "version": body.get("version", "") or "",
                "author": body.get("author", "") or "",
                "config": {},
                "config_params": [],
                "usage_count": 0,
                "engine": ["HERMES"],
                "builtin": True,
            }
            sc["skills"].append(target)
            if skill_id not in sc["order"]:
                sc["order"].append(skill_id)
        else:
            raise HTTPException(status_code=404, detail="Skill not found")
    definition.skill_config = dict(sc)
    flag_modified(definition, "skill_config")
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.toggle",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail={"skill_id": skill_id, "skill_name": target["name"], "enabled": enabled},
    )
    await db.commit()

    # 重写各实例 profile config.yaml 的 skills.disabled（热生效，不重启）
    for iid in await _definition_instance_ids(db, definition_id):
        try:
            await controller_client.sync_skills_config(iid)
        except controller_client.ControllerError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

    return _skill_view(target)


# ── 卸载 ──────────────────────────────────────────────────


@router.delete("/agent-definitions/{definition_id}/skills/{skill_id}")
async def uninstall_skill(
    definition_id: UUID,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    definition = await _require_definition(db, definition_id, group_ids)

    sc = _load_skill_config(definition)
    target = next((s for s in sc["skills"] if s.get("id") == skill_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Skill not found")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail="内置技能不可卸载，可关闭开关停用")
    skill_name = target["name"]

    sc["skills"] = [s for s in sc["skills"] if s.get("id") != skill_id]
    sc["order"] = [i for i in sc["order"] if i != skill_id]
    definition.skill_config = dict(sc)
    flag_modified(definition, "skill_config")
    # 删 SkillCredential：避免残留旧 credentials_encrypted——重新安装后 save_skill_credentials
    # 会 decrypt 旧值，旧 key 加密的 token 用新 key 解不开 → 500
    await db.execute(
        delete(SkillCredential).where(
            SkillCredential.definition_id == definition_id,
            SkillCredential.skill_name == skill_name,
        )
    )
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.uninstall",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail={"skill_id": skill_id, "skill_name": skill_name},
    )
    await db.commit()

    for iid in await _definition_instance_ids(db, definition_id):
        try:
            await controller_client.uninstall_skill(iid, skill_name)
        except controller_client.ControllerError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

    # 清理 MinIO 里的 zip
    try:
        archiver.delete_skill_zip(str(definition_id), skill_name)
    except Exception:
        logger.warning(
            "delete_skill_zip failed for %s/%s", str(definition_id)[:8], skill_name, exc_info=True
        )

    return {"ok": True}


# ── 技能市场（预留） ─────────────────────────────────────


@router.get("/skills/marketplace")
async def marketplace_list(_: User = Depends(get_current_user)):
    """技能市场尚未开放，返回空列表。"""
    return {"items": []}


@router.post("/agent-definitions/{definition_id}/skills/marketplace/{skill_id}/install")
async def marketplace_install(
    definition_id: UUID,
    skill_id: str,
    _: User = Depends(get_current_user),
):
    raise HTTPException(status_code=501, detail="技能市场即将开放，敬请期待")


# ── 凭证管理（secret 参数加密存储，不回显明文） ───────────


class CredentialSaveRequest(BaseModel):
    credentials: dict[str, str]  # {param_name: value}，仅 manifest 声明的 secret 参数


@router.put("/agent-definitions/{definition_id}/skills/{skill_id}/credentials")
async def save_skill_credentials(
    definition_id: UUID,
    skill_id: str,
    body: CredentialSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """保存 skill 凭证（secret 参数加密存储）。

    仅接受 manifest config_params 中 secret:true 声明的参数；空值表示不修改（保留已有）。
    凭证 Fernet 加密后存 credentials_encrypted 列，明文绝不落库。
    """
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    target = next((s for s in sc["skills"] if s.get("id") == skill_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill_name = target["name"]

    # 校验提交的 key 确实是 manifest 声明的 secret 参数
    declared_secret_keys = {p["name"] for p in target.get("config_params", []) if p.get("secret")}
    for k in body.credentials:
        if k not in declared_secret_keys:
            raise HTTPException(status_code=400, detail=f"参数 {k} 非声明的 secret 参数")

    # 仅加密非空值（空值表示不修改）
    plain_creds = {k: v for k, v in body.credentials.items() if v}
    if not plain_creds:
        return {"ok": True}

    # upsert：已有则 merge，无则新建（credentials_encrypted 为 Text 列整体替换，无需 flag_modified）
    res = await db.execute(
        select(SkillCredential).where(
            SkillCredential.definition_id == definition_id,
            SkillCredential.skill_name == skill_name,
            SkillCredential.scope_type == "ALL",
        )
    )
    row = res.scalar_one_or_none()
    if row:
        existing = decrypt_credentials_dict(row.credentials_encrypted)
        existing.update(plain_creds)
        row.credentials_encrypted = encrypt_credentials_dict(existing)
    else:
        row = SkillCredential(
            definition_id=definition_id,
            skill_name=skill_name,
            scope_type="ALL",
            scope_target_id=None,
            credentials_encrypted=encrypt_credentials_dict(plain_creds),
            created_by=user.id,
        )
        db.add(row)
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.credentials_save",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail={"skill_id": skill_id, "skill_name": skill_name, "keys": list(plain_creds.keys())},
    )
    await db.commit()

    # fan-out 密文到各实例 Pod（sidecar 解密用，密文落盘 skills/{name}/secrets.enc）
    instance_ids = await _definition_instance_ids(db, definition_id)
    for iid in instance_ids:
        try:
            await controller_client.write_skill_secrets(iid, skill_name, row.credentials_encrypted)
        except controller_client.ControllerError as e:
            logger.warning("fan-out skill secrets to %s failed: %s", iid[:8], e)

    return {"ok": True}


@router.get("/agent-definitions/{definition_id}/skills/{skill_id}/credentials")
async def get_skill_credential_status(
    definition_id: UUID,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """返回已配置的 secret 参数名列表（不回显明文）+ target_base_url。"""
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    target = next((s for s in sc["skills"] if s.get("id") == skill_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Skill not found")
    res = await db.execute(
        select(SkillCredential).where(
            SkillCredential.definition_id == definition_id,
            SkillCredential.skill_name == target["name"],
            SkillCredential.scope_type == "ALL",
        )
    )
    row = res.scalar_one_or_none()
    if not row:
        return {"configured": [], "target_base_url": None}
    try:
        creds = decrypt_credentials_dict(row.credentials_encrypted)
    except ValueError:
        return {"configured": [], "target_base_url": row.target_base_url}
    return {"configured": list(creds.keys()), "target_base_url": row.target_base_url}


# ── 非 secret 配置管理（落 skill_config.skills[].config + SKILL.md 变量替换）────────


class SkillConfigSaveRequest(BaseModel):
    config: dict[str, Any]  # {param_name: value}，仅 manifest 声明的非 secret 参数


def _validate_param_value(param: dict, value) -> Any:
    """按 config_params 类型校验并归一化值，不符抛 400。"""
    ptype = param.get("type", "string")
    name = param["name"]
    if ptype == "number":
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"参数 {name} 需为数字")
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"参数 {name} 需为数字")
        return int(f) if f.is_integer() else f
    if ptype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise HTTPException(status_code=400, detail=f"参数 {name} 需为布尔值")
    if ptype == "select":
        options = param.get("options") or []
        if value not in options:
            raise HTTPException(status_code=400, detail=f"参数 {name} 需为 {options} 之一")
        return value
    s = str(value)
    if len(s) > 2000:
        raise HTTPException(status_code=400, detail=f"参数 {name} 长度超限（2000）")
    return s


async def _refanout_skill(definition_id: UUID, skill_name: str, db: AsyncSession) -> dict:
    """配置变更后重 fan-out：取 MinIO 原始 zip → 对各实例调 install_skill（含 SKILL.md 替换）。

    复用 install 路径（_fanout_skill_to_pods → _zip_to_tar_strip_top 已注入变量替换），无需新
    端点。best-effort：单实例失败不阻塞，返回成功/失败计数供前端感知（对齐 save_skill_credentials）。
    """
    raw = archiver.get_skill_zip(str(definition_id), skill_name)
    if not raw:
        return {"total": 0, "ok": 0, "failed": 0, "reason": "zip_missing"}
    zip_b64 = base64.b64encode(raw).decode("ascii")
    instance_ids = await _definition_instance_ids(db, definition_id)
    ok = failed = 0
    for iid in instance_ids:
        try:
            await controller_client.install_skill(iid, skill_name, zip_b64)
            ok += 1
        except controller_client.ControllerError as e:
            logger.warning("refanout skill %s to %s failed: %s", skill_name, iid[:8], e)
            failed += 1
    return {"total": len(instance_ids), "ok": ok, "failed": failed}


@router.put("/agent-definitions/{definition_id}/skills/{skill_id}/config")
async def save_skill_config(
    definition_id: UUID,
    skill_id: str,
    body: SkillConfigSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """保存非 secret 配置：类型校验 → 写 skill_config.skills[].config → fan-out 重替换 SKILL.md。

    仅接受 manifest config_params 中声明的非 secret 参数（secret 走凭证接口）。部分更新：
    只覆盖提交的 key。保存后 best-effort refanout，让 SKILL.md 的 ${config.param} 立即重渲染。
    """
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    target = next((s for s in sc["skills"] if s.get("id") == skill_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Skill not found")

    # 校验：每个 key 必须是声明的非 secret 参数，且类型匹配
    by_name = {p["name"]: p for p in target.get("config_params", []) if p.get("name")}
    normalized: dict[str, Any] = {}
    for k, v in body.config.items():
        p = by_name.get(k)
        if not p:
            raise HTTPException(status_code=400, detail=f"参数 {k} 非声明的配置参数")
        if p.get("secret"):
            raise HTTPException(status_code=400, detail=f"参数 {k} 为凭证参数，请走凭证接口")
        normalized[k] = _validate_param_value(p, v)

    # 合并写入（部分更新：只覆盖提交的 key）
    target.setdefault("config", {}).update(normalized)
    definition.skill_config = dict(sc)
    flag_modified(definition, "skill_config")
    await log_operation(
        db,
        actor_id=user.id,
        action="agent_skill.config_save",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=definition.group_id,
        detail={"skill_name": target["name"], "keys": list(normalized.keys())},
    )
    await db.commit()

    # fan-out：从 MinIO 取原始 zip，复用 install 路径重替换 SKILL.md（best-effort）
    fanout = await _refanout_skill(definition_id, target["name"], db)
    return {"ok": True, "fanout": fanout}


@router.get("/agent-definitions/{definition_id}/skills/{skill_id}/config")
async def get_skill_config(
    definition_id: UUID,
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """回填用：返回非 secret 参数的已存值（未填用 default 兜底）。非 secret 可回显。"""
    definition = await _require_definition(db, definition_id, group_ids)
    sc = _load_skill_config(definition)
    target = next((s for s in sc["skills"] if s.get("id") == skill_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Skill not found")
    config = target.get("config") or {}
    return {
        "values": {
            p["name"]: config.get(p["name"], p.get("default"))
            for p in target.get("config_params", [])
            if p.get("name") and not p.get("secret")
        }
    }
