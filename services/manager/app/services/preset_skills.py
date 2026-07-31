"""出厂预制 skill（platform preset）。

每个智能体模版创建时自动预填一份预制 skill 到 skill_config（source="preset", builtin=true），
开发者可在模版编辑页开关。SKILL.md 本体位于 app/data/preset_skills/<name>/，
创建模版后（拿到 definition_id）打包成 zip 存入 MinIO（按 definition_id 隔离），
与用户上传 skill 走同一 fan-out 管线：新建 AgentProfile 时 _seed_skills 从 MinIO
取回解压到该 profile 的 skills/ 目录。

Hermes 0 skill 也能正常运行；此预制集是「开箱即用最小可用集」（plan/searxng-search/
concept-diagrams/fastmcp/one-three-one-rule），境内可用、无 API key、跨行业通用。
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

# app/data/ 目录（本文件所在 services/manager/app/services/ → 上两级 app/ → data/）
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PRESETS_JSON = _DATA_DIR / "platform_presets.json"
_PRESET_SKILLS_DIR = _DATA_DIR / "preset_skills"


def _load_presets_meta() -> list[dict]:
    """读取 platform_presets.json，返回 presets 列表。失败返回空列表（不阻断创建）。"""
    try:
        raw = _PRESETS_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        presets = data.get("presets") if isinstance(data, dict) else None
        return presets if isinstance(presets, list) else []
    except Exception:
        logger.warning("load platform_presets.json failed", exc_info=True)
        return []


def build_preset_zip(skill_name: str) -> bytes | None:
    """把 preset_skills/<name>/ 打包成 zip（保留顶层目录 <name>/SKILL.md）。

    与用户上传 zip 同构：_parse_zip 期望根或单层目录下有 SKILL.md。
    返回 None 表示 asset 缺失（不阻断，跳过该 preset）。
    """
    src = _PRESET_SKILLS_DIR / skill_name
    if not src.is_dir():
        logger.warning("preset skill asset missing: %s", skill_name)
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                arcname = f"{skill_name}/{p.relative_to(src).as_posix()}"
                zf.write(p, arcname)
    return buf.getvalue()


def _preset_records() -> list[dict]:
    """构建预制 skill 的 skill_config 记录（不触碰 MinIO）。"""
    records: list[dict] = []
    for meta in _load_presets_meta():
        name = (meta.get("name") or "").strip()
        if not name:
            continue
        # asset 必须存在才预填，否则 _seed_skills 取不到 zip
        if not (_PRESET_SKILLS_DIR / name).is_dir():
            logger.warning("preset skill asset missing, skip: %s", name)
            continue
        records.append(
            {
                "id": f"preset-{name}",
                "name": name,
                "description": meta.get("description", ""),
                "icon": meta.get("icon", "ri:apps-2-line"),
                "enabled": bool(meta.get("enabled_default", True)),
                "version": meta.get("version", "1.0.0"),
                "author": meta.get("author", "platform-preset"),
                "config_params": [],
                "config": {},
                "source": "preset",
                "builtin": True,
                "installed_at": datetime.now(UTC).isoformat(),
                "usage_count": 0,
                "engine": ["HERMES"],
            }
        )
    return records


def prefill_skill_config(skill_config: dict | None) -> dict:
    """把预制 skill 注入 skill_config，返回新 dict（不就地改入参）。

    已存在的同名 skill 不覆盖（尊重用户/历史数据）。order 追加 preset id。
    不存 MinIO（调用方在拿到 definition_id 后调 save_preset_zips）。
    """
    sc = skill_config or {}
    if isinstance(sc, str):
        sc = json.loads(sc) if sc else {}
    if not isinstance(sc, dict):
        sc = {}
    skills = list(sc.get("skills") or [])
    order = list(sc.get("order") or [])
    disabled = list(sc.get("disabled") or [])

    existing_names = {(s.get("name") or "").strip().lower() for s in skills if isinstance(s, dict)}
    for rec in _preset_records():
        if rec["name"].lower() in existing_names:
            continue
        skills.append(rec)
        if rec["id"] not in order:
            order.append(rec["id"])
        if not rec["enabled"] and rec["name"] not in disabled:
            disabled.append(rec["name"])
    return {"skills": skills, "order": order, "disabled": disabled}


def save_preset_zips(definition_id: UUID) -> int:
    """把预制 skill zip 存入 MinIO（按 definition_id 隔离）。返回成功写入数。

    在 create_definition commit 后调用（此时 definition_id 已分配）。best-effort：
    MinIO 不可用仅告警不抛，_seed_skills 取不到 zip 会跳过，不影响模版创建。
    """
    from app.worker.minio_archiver import archiver  # 局部导入避免循环

    did = str(definition_id)
    written = 0
    for meta in _load_presets_meta():
        name = (meta.get("name") or "").strip()
        if not name:
            continue
        zip_bytes = build_preset_zip(name)
        if zip_bytes is None:
            continue
        try:
            archiver.save_skill_zip(did, name, zip_bytes)
            written += 1
        except Exception:
            logger.warning("save_skill_zip(preset) %s/%s failed", did[:8], name, exc_info=True)
    return written


# 平台禁用 skill 黑名单（安全风险 / 越狱），catalog 接口层过滤
BANNED_SKILL_NAMES = {"godmode", "obliteratus"}
