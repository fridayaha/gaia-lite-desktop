"""Agent 配置 / 人设 / 技能 API — /api/controller/agents/{id}/config|persona|skills*

从 router.py 拆出，路径不变。配置渲染、envs、heal、pod 枚举等跨域 helper 在 _common。
"""

import json
import logging
import re
import shlex
import uuid

from app.models import SkillCredential
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.database import get_db as get_manager_db

from ._common import (
    build_engine_envs as _build_engine_envs,
)
from ._common import (
    build_profile_config_yaml as _build_profile_config_yaml,
)
from ._common import (
    heal_profile_runtime_config as _heal_profile_runtime_config,
)
from ._common import (
    iter_agent_target_pods as _iter_agent_target_pods,
)
from ._common import (
    load_agent_configs as _load_agent_configs,
)
from ._common import (
    load_instance_config as _load_instance_config,
)
from ._common import (
    shared_skill_dir as _shared_skill_dir,
)
from .k8s_manager import k8s_manager
from .minio_archiver import archiver

router = APIRouter()

logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────


class PersonaSyncRequest(BaseModel):
    pass  # 占位，便于未来扩展；当前从 DB 读 system_prompt


class SkillInstallRequest(BaseModel):
    skill_name: str
    zip_b64: str


class SkillSecretsRequest(BaseModel):
    skill_name: str
    credentials_encrypted: str


# ── 技能 fan-out helpers ────────────────────────────────────


_SKILL_VAR_RE = re.compile(r"\$\{config\.([a-zA-Z_]\w*)\s*\}")


def _stringify_config_value(val) -> str:
    """类型化值 → SKILL.md 友好字符串。bool 用 Python True/False（变量常出现在 execute_code
    的 Python 块，小写 true/false 会 NameError）。"""
    if isinstance(val, bool):
        return "True" if val else "False"
    return str(val)


def _build_substitution_map(skill_record: dict) -> dict[str, str]:
    """构造 ${config.param} → 值 映射。仅非 secret；值优先级 config > default > 不进映射
    （不进映射则 token 保留原样，safe_substitute 语义）。"""
    mapping: dict[str, str] = {}
    for p in skill_record.get("config_params") or []:
        if p.get("secret"):
            continue  # secret 强制走 sidecar，不进模板
        name = p.get("name")
        if not name:
            continue
        val = (skill_record.get("config") or {}).get(name)
        if val in (None, ""):
            val = p.get("default")  # 激活此前闲置的 default 字段作兜底
        if val is None:
            continue  # 无值 → token 保留原样
        mapping[name] = _stringify_config_value(val)
    return mapping


def _substitute_skill_md_body(content: str, mapping: dict[str, str]) -> str:
    """仅替换 body（frontmatter 之后），保护 name/version 元数据解析；未知 token 原样保留；
    re.sub 单次扫描不递归（替换出的文本不会再被扫，无注入）。"""
    if not mapping:
        return content

    def repl(m: re.Match) -> str:
        return mapping.get(m.group(1), m.group(0))  # 未知 token 原样返回

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return "---" + parts[1] + "---" + _SKILL_VAR_RE.sub(repl, parts[2])
    return _SKILL_VAR_RE.sub(repl, content)


async def _load_definition_skill_record(
    db: AsyncSession, definition_id: str, skill_name: str
) -> dict | None:
    """按 definition_id 读 definition 层 skill_config，取 name 匹配的 skill record。

    读 definition 层（草稿）而非 version 快照：load_instance_config 读 v.skill_config（publish
    时的不可变快照），改完 config 不 publish 读不到新值。非 secret config 写在 definition 层，
    fan-out 必须读 definition 才能拿到最新值（草稿即生效）。
    """
    row = await db.execute(
        text("SELECT skill_config FROM agent_definitions WHERE id = :did"),
        {"did": str(definition_id)},
    )
    sc = row.mappings().first()
    if not sc:
        return None
    skills = (sc.get("skill_config") or {}).get("skills") or []
    return next((s for s in skills if s.get("name") == skill_name), None)


def _zip_to_tar_strip_top(
    zip_bytes: bytes, dest_prefix: str, skill_md_substitute: dict[str, str] | None = None
) -> bytes:
    """zip → tar.gz；剥离单一顶层目录；tar 成员路径前缀为 dest_prefix。

    路径安全过滤（拒绝 .. / 绝对路径 / 反斜杠）。dest_prefix 形如
    /root/.hermes/skills/demo 或 /opt/data/profiles/{pn}/skills/demo。
    """
    import io
    import tarfile
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        files = [m for m in zf.namelist() if not m.endswith("/")]
        files = [m for m in files if not (m.startswith("/") or ".." in m.split("/") or "\\" in m)]
        top_dirs = {m.split("/")[0] for m in files if "/" in m}
        prefix = ""
        if len(top_dirs) == 1:
            td = next(iter(top_dirs))
            if all(m.startswith(td + "/") for m in files):
                prefix = td + "/"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for member in files:
                rel = member[len(prefix) :] if prefix and member.startswith(prefix) else member
                if not rel:
                    continue
                data = zf.read(member)
                # SKILL.md body 变量替换（${config.param} → 值）；解码失败保留原样不影响解压
                if skill_md_substitute and rel.endswith("SKILL.md"):
                    try:
                        data = _substitute_skill_md_body(
                            data.decode("utf-8"), skill_md_substitute
                        ).encode("utf-8")
                    except Exception:
                        pass
                ti = tarfile.TarInfo(name=f"{dest_prefix}/{rel}")
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
        return buf.getvalue()


async def _regen_homes_config(
    pod_name: str, agent_id: str, homes: list[str], db: AsyncSession
) -> None:
    """用统一生成函数重写各 home 的 config.yaml（反映最新 skills.external_dirs + disabled）。

    每个 home 按 profile_name 独立渲染：browser_sandbox 取 agent runtime_config 开关，
    cdp_url 按 profile_name 算 per-profile browser Pod DNS。base 是 entrypoint 创建的
    默认 profile（无独立 browser Pod），不注 browser 段。与 heal_profile_runtime_config
    对齐——旧实现只渲染一份无 browser 的 config 写到所有 home，skill sync 时会覆盖 heal
    写入的 browser toolset + cdp_url，致 browser 工具集体失效。
    """
    inst_cfg = await _load_instance_config(db, agent_id)
    if inst_cfg is None:
        return
    model_config = inst_cfg["model_config"]
    skill_config = inst_cfg["skill_config"]
    definition_id = inst_cfg.get("definition_id")
    runtime_config = inst_cfg.get("runtime_config") or {}
    browser_sandbox = bool((runtime_config.get("browser_sandbox") or {}).get("enabled"))
    for home in homes:
        pn = home.rstrip("/").split("/")[-1]
        # base 无独立 browser Pod，不注 browser 段；其余 profile 按 agent 开关 + per-profile DNS
        sb = browser_sandbox and pn != "base"
        config_yaml = _build_profile_config_yaml(
            model_config,
            skill_config,
            definition_id,
            agent_id=agent_id,
            profile_name=pn,
            browser_sandbox=sb,
        )
        try:
            await k8s_manager.exec_write_file_in_pod(pod_name, f"{home}/config.yaml", config_yaml)
        except Exception as e:
            logger.warning("regen config.yaml on %s:%s failed: %s", pod_name[:30], home, e)


def _skill_group_name(definition_id: str) -> str:
    """definition_id → 共享 skill 补充组名。须与 profile_isolation.py 的同名函数一致
    （manager 建 group + profile_isolation 加组成员，两边名字必须匹配）。"""
    safe = "".join(c if c.isalnum() else "-" for c in definition_id).strip("-")
    if not safe or not safe[0].isalpha():
        safe = "d" + safe
    return f"skills-{safe[:24]}"


def _ensure_shared_skill_dir_shell(definition_id: str) -> str:
    """生成幂等 shell：确保 /opt/data/skills/{definition_id}/ 存在，属 root:{skill_gid}
    chmod 2750，补充组按 st_gid 重建（PVC 持久，容器重启 /etc/group 丢失后恢复）。

    真相源 = 目录 stat 的 st_gid：新建时 groupadd auto 分配 GID 落在 dir 上；后续按
    st_gid 重建同名组。同 definition 的 profile UID 加进该组 → 可读；跨 definition
    GID 不同 → 互不可读（多租户隔离）。
    """
    gname = _skill_group_name(definition_id)
    sdir = _shared_skill_dir(definition_id)
    # heredoc 风格的复合命令：用 sh -c 包裹，避免 exec 转义问题。
    return (
        f'_d="{sdir}"; _g="{gname}"; '
        f'chmod 755 "$(dirname "$_d")" 2>/dev/null || true; '
        f'if [ -d "$_d" ]; then '
        f'  _gid=$(stat -c %g "$_d" 2>/dev/null || echo 0); '
        f'  if [ "$_gid" != "0" ]; then groupadd -g "$_gid" -f "$_g" 2>/dev/null || true; '
        f'  else groupadd -r -f "$_g" 2>/dev/null || true; '
        f'  _gid=$(getent group "$_g" | cut -d: -f3); '
        f'    chown root:"$_gid" "$_d" 2>/dev/null || true; fi; '
        f'  chmod 2750 "$_d" 2>/dev/null || true; '
        f'else groupadd -r -f "$_g" 2>/dev/null || true; '
        f'  _gid=$(getent group "$_g" | cut -d: -f3); '
        f'  mkdir -p "$_d"; chown root:"$_gid" "$_d"; chmod 2750 "$_d"; fi'
    )


async def _ensure_shared_skill_dir(pod_name: str, definition_id: str) -> None:
    """在 Pod 上确保共享 skill 目录 + 补充组就绪（install/replay/profile 创建前调）。"""
    if not pod_name or not definition_id:
        return
    try:
        await k8s_manager.exec_command_in_pod(
            pod_name, [_ensure_shared_skill_dir_shell(definition_id)]
        )
    except Exception as e:
        logger.warning(
            "ensure shared skill dir %s on %s failed: %s",
            definition_id[:8],
            pod_name[:30],
            e,
        )


async def _replay_skill_secrets(
    agent_id: str, skill_name: str, definition_id: str, db: AsyncSession
) -> None:
    """若该 skill 有 SkillCredential，幂等 fan-out secrets.enc 到各 Pod。

    ``_fanout_skill_to_pods`` 装 skill 时 ``rm -rf {dest}`` 会擦掉已写的 secrets.enc，
    故 tar 后必须回写；本 helper 统一该回写逻辑（install/upgrade/backfill/replay 共用）。
    best-effort：失败不影响调用方（不应因写 secrets 中断 install/replay）。
    """
    try:
        cred = (
            await db.execute(
                select(SkillCredential).where(
                    SkillCredential.definition_id == definition_id,
                    SkillCredential.skill_name == skill_name,
                )
            )
        ).scalar_one_or_none()
        if cred and cred.credentials_encrypted:
            await write_skill_secrets(
                agent_id,
                SkillSecretsRequest(
                    skill_name=skill_name,
                    credentials_encrypted=cred.credentials_encrypted,
                ),
                db,
            )
    except Exception:
        logger.warning("replay secrets.enc for skill %s failed", skill_name, exc_info=True)


async def _fanout_skill_to_pods(
    agent_id: str, skill_name: str, zip_bytes: bytes, db: AsyncSession
) -> int:
    """把技能 zip 解压到 agent 各 Pod 的**共享 skill 目录**（external_dirs 模型）。

    每 Pod 写一次 `/opt/data/skills/{definition_id}/{skill_name}/`，同 definition 的所有
    profile 经 config.yaml external_dirs 共享读，无需 per-profile 复制。install 端点与
    deploy 重放共用。返回写入的 Pod 数。
    """
    configs = await _load_agent_configs(agent_id, db)
    if configs is None:
        return 0
    _, _, definition_id = configs
    if not definition_id:
        logger.warning("fanout skill %s: no definition_id for %s, skip", skill_name, agent_id[:8])
        return 0
    # 读 definition 层 skill record 构造 SKILL.md 变量映射（非 version 快照，草稿即生效）
    skill_record = await _load_definition_skill_record(db, definition_id, skill_name)
    sub_map = _build_substitution_map(skill_record or {})
    pods = await _iter_agent_target_pods(agent_id, db)
    if not pods:
        return 0
    sdir = _shared_skill_dir(definition_id)
    written = 0
    for p in pods:
        pod_name = p["pod_name"]
        if not pod_name:
            continue
        await _ensure_shared_skill_dir(pod_name, definition_id)
        dest = f"{sdir}/{skill_name}"
        # 原子换入：先解压到 {dest}.new.{uuid}，再 rm -rf {dest} && mv 原子替换（同 PVC 内
        # rename(2)）。中途失败只清理 {dest_new}，旧 {dest} 原样保留（技能保持旧版可用），
        # 不留空目录（修旧 rm -rf 先删后 tar 失败留空目录的坑）。sdir 已 setgid(2750)，
        # tar 建的 dest_new 继承补充组 → mv 后 profile UID 仍可读。
        dest_new = f"{dest}.new.{uuid.uuid4().hex}"
        try:
            tar_data = _zip_to_tar_strip_top(zip_bytes, dest_new, skill_md_substitute=sub_map)
            await k8s_manager.exec_untar_to_in_pod(pod_name, "/", tar_data)
            await k8s_manager.exec_command_in_pod(
                pod_name, [f"rm -rf {dest} && mv {dest_new} {dest}"]
            )
            written += 1
        except Exception as e:
            logger.warning(
                "fanout skill %s on %s:%s failed: %s", skill_name, pod_name[:30], dest, e
            )
            try:
                await k8s_manager.exec_command_in_pod(pod_name, [f"rm -rf {dest_new}"])
            except Exception:
                pass
        try:
            await _regen_homes_config(pod_name, agent_id, p["homes"], db)
        except Exception as e:
            logger.warning("regen config after fanout on %s failed: %s", pod_name[:30], e)

        # 删 skill 快照让 gateway 下次消息时重建（snapshot 在 home 级，仍 per-home）
        for home in p["homes"]:
            try:
                await k8s_manager.exec_command_in_pod(
                    pod_name, [f"rm -f {home}/.skills_prompt_snapshot.json"]
                )
            except Exception:
                pass
    # tar 完成（rm -rf 已擦掉旧 secrets.enc）后回写凭证，确保 install/upgrade/backfill
    # 后 secrets.enc 不缺（否则 sidecar 404 → skill auth_fail）。幂等、best-effort：
    # 写 secrets 失败绝不阻断 install（_replay_skill_secrets 内已 catch，此处再兜底）。
    try:
        await _replay_skill_secrets(agent_id, skill_name, definition_id, db)
    except Exception:
        logger.warning("fanout replay secrets for %s failed", skill_name, exc_info=True)
    return written


async def replay_persona_and_skills(agent_id: str, inst_cfg: dict, db: AsyncSession) -> None:
    """部署成功后把人设(SOUL.md) + 已装技能 fan-out 到 Pod。best-effort，失败不影响 RUNNING。

    - 人设：调 sync_persona 把 system_prompt 写成 SOUL.md 到 base + 各 profile home
      （修 sync_persona 死代码——每次 deploy 确保人设落到 Pod，而非 entrypoint 默认模板）。
    - 技能：以 MinIO skill zip store 为权威（install 存 / uninstall 删），list_skill_zips
      取回已装技能名，逐个取 zip fan-out 到各 home
      （修 _seed_skills no-op——destroy→redeploy 删 PVC 后技能自动恢复）。
      不用 version 快照 skill_config：技能装在 definition 层，version 快照是发布时的旧值，
      不含发布后新装的技能。
    """
    # 人设
    try:
        await sync_persona(agent_id, db)
    except Exception:
        logger.warning("sync_persona on deploy for %s failed", agent_id[:8], exc_info=True)

    # 技能重放
    definition_id = inst_cfg.get("definition_id")
    if not definition_id:
        return
    try:
        skill_names = archiver.list_skill_zips(definition_id)
    except Exception:
        logger.warning("list_skill_zips for %s failed", definition_id[:8], exc_info=True)
        return
    for name in skill_names:
        try:
            zip_bytes = archiver.get_skill_zip(definition_id, name)
            if zip_bytes:
                await _fanout_skill_to_pods(agent_id, name, zip_bytes, db)
            else:
                logger.info("deploy replay: skill %s zip missing, skip", name)
            # secrets.enc 由 _fanout_skill_to_pods 内 _replay_skill_secrets 回写（rm -rf 后补）
        except Exception:
            logger.warning("deploy replay skill %s failed", name, exc_info=True)


# ── 配置同步 / 应用 ────────────────────────────────────────


@router.post("/api/controller/agents/{agent_id}/config/sync")
async def sync_agent_config(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """将 Agent 的配置同步到 MinIO 并写入运行中 Pod 的 ~/.hermes/

    同步内容包括:
      - config.yaml（指向 LiteLLM 的 OpenAI 兼容端点）
      - .env（API Server + OPENAI_API_KEY 兜底）

    注意：此操作仅推送配置，不重启引擎。
    如需配置生效需调用 /config/apply 或 /deploy。
    """
    # 获取 agent 配置（V3: 经 instance 读取 version 快照 + per-instance litellm 覆盖 + group_code）
    inst_cfg = await _load_instance_config(db, agent_id)
    if inst_cfg is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    model_config = inst_cfg["model_config"]
    skill_config = inst_cfg["skill_config"]
    _group_code = inst_cfg.get("group_code")
    _definition_id = inst_cfg.get("definition_id")

    litellm = model_config.get("litellm") or {}

    base = settings.litellm_base_url.rstrip("/") + "/v1"
    api_key = litellm.get("key", "")

    # 生成 config.yaml（统一函数，含 skills.external_dirs + disabled，避免擦除开关状态）
    config_yaml = _build_profile_config_yaml(model_config, skill_config, _definition_id)

    # 生成 .env 内容
    env_lines = [
        "API_SERVER_ENABLED=true",
        "API_SERVER_HOST=0.0.0.0",
        "API_SERVER_PORT=8642",
        "GATEWAY_ALLOW_ALL_USERS=true",
    ]
    if api_key:
        env_lines.append(f"OPENAI_API_KEY={api_key}")
        env_lines.append(f"OPENAI_BASE_URL={base}")

    env_content = "\n".join(env_lines) + "\n"

    # 保存到 MinIO（按组前缀 groups/{group_code}/engine-config/...）
    try:
        archiver.save_engine_config(agent_id, config_yaml, env_content, group_code=_group_code)
    except Exception as e:
        logger.warning(f"Failed to save engine config to MinIO: {e}")

    # V2 多 profile：config 由 entrypoint-v2 从 env 生成、_heal_profile_runtime_config
    # 按 profile 对齐，不在此写 /root/.hermes（V2 未挂载，无效）。
    # apply_agent_config 会 rollout_restart 使 entrypoint 重生成生效。
    return {"status": "synced", "message": "配置已保存到 MinIO"}


@router.post("/api/controller/agents/{agent_id}/config/apply")
async def apply_agent_config(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """使新配置生效——更新 Deployment 环境变量 → 同步配置 → heal PVC .env → 滚动重启"""
    # 1. 获取最新配置（V3: 经 instance 读取 version 快照 + per-instance litellm 覆盖）
    inst_cfg = await _load_instance_config(db, agent_id)
    if not inst_cfg:
        raise HTTPException(status_code=404, detail="Agent instance not found")

    model_config = inst_cfg["model_config"]
    engine_config = _build_engine_envs(model_config)

    # 2. 更新 Deployment 环境变量（确保 Pod 重启后 entrypoint 能拿到新配置）
    pod_env_overrides = {}
    for key in ["LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL"]:
        if key in engine_config:
            pod_env_overrides[key] = engine_config[key]

    if pod_env_overrides:
        await k8s_manager.patch_agent_envs(agent_id, pod_env_overrides)
        logger.info(
            f"Patched Deployment env vars for agent {agent_id}: {list(pod_env_overrides.keys())}"
        )

    # 3. 同步配置到 MinIO 和运行中 Pod
    await sync_agent_config(agent_id, db)

    # 3.5 heal PVC .env：把新 LiteLLM key 写进每个 profile 的 .env
    # _provision_litellm 旋转 key 后只写 DB，PVC .env 残留旧 key；
    # rollout_restart 后新 Pod 读 PVC .env（entrypoint 不覆盖已存在文件）
    # → 旧 key → LiteLLM 401。故 restart 前 exec 进 Pod 改 .env。
    from pkg.common.models import AgentProfile

    prof_result = await db.execute(select(AgentProfile).where(AgentProfile.instance_id == agent_id))
    profiles = prof_result.scalars().all()
    healed = 0
    for prof in profiles:
        try:
            await _heal_profile_runtime_config(
                agent_id, prof.profile_name, db, port=prof.internal_port
            )
            healed += 1
        except Exception as e:
            logger.warning(
                "apply_agent_config: heal .env for profile %s failed: %s",
                prof.profile_name[:16],
                e,
            )
    logger.info(
        "apply_agent_config: healed PVC .env for %d/%d profiles of agent %s",
        healed,
        len(profiles),
        agent_id[:8],
    )

    # 4. 触发 rollout restart 使新配置在 Pod 启动时生效
    try:
        await k8s_manager.rollout_restart(agent_id)
    except Exception as e:
        logger.error(f"Failed to rollout restart for agent {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Rollout restart failed: {e}",
        )

    return {
        "status": "applied",
        "message": "配置已应用，引擎正在重启（约 30-60 秒）",
    }


# ── 人设 (SOUL.md) 同步 ────────────────────────────────


@router.post("/api/controller/agents/{agent_id}/persona/sync")
async def sync_persona(agent_id: str, db: AsyncSession = Depends(get_manager_db)):
    """把 agent 的 persona_config.system_prompt 作为 SOUL.md fan-out 到所有引擎 Pod 的 home 目录。

    Hermes 按会话读取 SOUL.md，写文件即生效，不重启。
    自适应 V1（/root/.hermes）与 V2（/opt/data/profiles/{name}）。

    读 persona_config（admin 改人设写的字段），不是 model_config.system_prompt（已弃用/可能空）。
    人设清空（system_prompt=""）时也写空 SOUL.md 覆盖旧人设——不跳过，否则旧人设残留。
    """
    cfg = await _load_instance_config(db, agent_id)
    if cfg is None:
        return {"status": "no_agent", "synced": 0}
    soul = (cfg.get("persona_config") or {}).get("system_prompt") or ""
    pods = await _iter_agent_target_pods(agent_id, db)
    if not pods:
        return {"status": "no_pods", "synced": 0}

    synced = 0
    for p in pods:
        pod_name = p["pod_name"]
        if not pod_name:
            continue
        for home in p["homes"]:
            try:
                await k8s_manager.exec_write_file_in_pod(pod_name, f"{home}/SOUL.md", soul)
                synced += 1
            except Exception as e:
                logger.warning("sync SOUL.md on %s:%s failed: %s", pod_name[:30], home, e)
    return {"status": "synced" if soul.strip() else "cleared", "synced": synced}


# ── 技能 secrets / install / config / list / uninstall ────────────────────────────────


@router.post("/api/controller/agents/{agent_id}/skills/secrets/write")
async def write_skill_secrets(
    agent_id: str, req: SkillSecretsRequest, db: AsyncSession = Depends(get_manager_db)
):
    """把加密的 secret 写到各 Pod 共享 skill 目录的 {name}/secrets.enc（密文落盘，sidecar 解密用）。

    external_dirs 模型：secrets.enc 放共享目录 /opt/data/skills/{definition_id}/{name}/，
    同 definition 的所有 profile 共享同一份凭证（与 skill_credentials.definition_id 一致）。
    sidecar 容器持 credential_encryption_key，运行时解密返回明文给 skill execute_code。
    """
    configs = await _load_agent_configs(agent_id, db)
    if configs is None or not configs[2]:
        return {"ok": False, "reason": "no definition_id"}
    sdir = _shared_skill_dir(configs[2])
    pods = await _iter_agent_target_pods(agent_id, db)
    for p in pods:
        pod_name = p["pod_name"]
        if not pod_name:
            continue
        await _ensure_shared_skill_dir(pod_name, configs[2])
        try:
            await k8s_manager.exec_write_file_in_pod(
                pod_name,
                f"{sdir}/{req.skill_name}/secrets.enc",
                req.credentials_encrypted,
                mode=0o640,
            )
        except Exception as e:
            logger.warning("write secrets to %s:%s failed: %s", pod_name[:30], sdir, e)
    return {"ok": True}


def _probe_pod_skills_shell(sdir: str, skill_names: list[str]) -> str:
    """生成单次 exec 的批量探活命令：检测各 skill 的 {sdir}/{name} 目录与 secrets.enc 存在性。

    输出 JSON 行：[{"name","dir","secret"}, ...]。1 exec/pod 控规模负载（1000 Pod × 30min
    ≈ 33 pod/min）。skill 名经 JSON + shlex.quote 安全转义。
    """
    script = (
        "import os,json\n"
        "sdir=os.environ['UA_SKILL_DIR']\n"
        "names=json.loads(os.environ['UA_SKILL_NAMES'])\n"
        "out=[]\n"
        "for n in names:\n"
        " d=os.path.join(sdir,n)\n"
        " out.append({'name':n,'dir':os.path.isdir(d),"
        "'secret':os.path.isfile(os.path.join(d,'secrets.enc'))})\n"
        "print(json.dumps(out))"
    )
    names_json = json.dumps(list(skill_names))
    return (
        f"UA_SKILL_DIR={shlex.quote(sdir)} UA_SKILL_NAMES={shlex.quote(names_json)} "
        f"python3 -c {shlex.quote(script)}"
    )


async def reconcile_skills(agent_id: str, db: AsyncSession) -> dict:
    """全链技能一致性对账 + 自愈：DB skill_config ↔ COS zip ↔ Pod 文件 ↔ secrets.enc。

    每.Pod 单次 exec 批量探活（_probe_pod_skills_shell），drift 时才自愈：
      - Pod 缺文件、COS 有 zip → get_skill_zip + _fanout_skill_to_pods（含 _replay_skill_secrets）
      - Pod 有文件、缺 secrets.enc、有 SkillCredential → write_skill_secrets
      - DB 有、COS 无 → 仅上报（不可自愈，需运维重传）
    幂等：_fanout 原子换入 + write_skill_secrets 覆盖，重复调用安全。best-effort，不抛。
    触发点：entrypoint pod 启动（/skills/secrets/reconcile）、resume_agent、30min 周期循环。
    """
    configs = await _load_agent_configs(agent_id, db)
    if configs is None or not configs[2]:
        return {"agent_id": agent_id, "ok": False, "reason": "no definition_id"}
    _, skill_config, definition_id = configs
    sdir = _shared_skill_dir(definition_id)

    try:
        cos_skills = set(archiver.list_skill_zips(definition_id))
    except Exception:
        logger.warning("reconcile: list_skill_zips for %s failed", definition_id[:8], exc_info=True)
        cos_skills = set()

    db_skills = {
        s.get("name") for s in (skill_config or {}).get("skills") or [] if s.get("name")
    }

    cred_rows = (
        (
            await db.execute(
                select(SkillCredential).where(SkillCredential.definition_id == definition_id)
            )
        )
        .scalars()
        .all()
    )
    cred_map = {
        c.skill_name: c.credentials_encrypted for c in cred_rows if c.credentials_encrypted
    }

    skills_to_probe = sorted(db_skills | cos_skills)
    pods = await _iter_agent_target_pods(agent_id, db)
    running_pods = [p for p in pods if p.get("pod_name")]
    details: list[dict] = []
    drift_any = False
    healed_any = False

    for p in running_pods:
        pod_name = p["pod_name"]
        probe: dict[str, dict] = {}
        if skills_to_probe:
            try:
                out = await k8s_manager.exec_command_in_pod(
                    pod_name, [_probe_pod_skills_shell(sdir, skills_to_probe)]
                )
                lines = (out or "").strip().splitlines()
                if lines:
                    probe = {row["name"]: row for row in json.loads(lines[-1])}
            except Exception:
                logger.warning("reconcile probe on %s failed", pod_name[:30], exc_info=True)

        for name in skills_to_probe:
            row = probe.get(name) or {"name": name, "dir": False, "secret": False}
            cos_present = name in cos_skills
            db_present = name in db_skills
            cred_present = name in cred_map
            dir_ok = bool(row.get("dir"))
            secret_ok = bool(row.get("secret"))
            healed = False
            drift = False

            if not dir_ok and cos_present:
                drift = True
                try:
                    zip_bytes = archiver.get_skill_zip(definition_id, name)
                    if zip_bytes:
                        await _fanout_skill_to_pods(agent_id, name, zip_bytes, db)
                        healed = True
                except Exception:
                    logger.warning(
                        "reconcile refanout %s on %s failed", name, pod_name[:30], exc_info=True
                    )
            elif not dir_ok and db_present and not cos_present:
                drift = True  # DB 有但 COS 无 → 不可自愈

            if dir_ok and not secret_ok and cred_present:
                drift = True
                try:
                    await write_skill_secrets(
                        agent_id,
                        SkillSecretsRequest(
                            skill_name=name, credentials_encrypted=cred_map[name]
                        ),
                        db,
                    )
                    healed = True
                except Exception:
                    logger.warning(
                        "reconcile secrets %s on %s failed", name, pod_name[:30], exc_info=True
                    )

            drift_any = drift_any or drift
            healed_any = healed_any or healed
            details.append(
                {
                    "pod_name": pod_name,
                    "skill": name,
                    "cos_present": cos_present,
                    "db_present": db_present,
                    "cred_present": cred_present,
                    "dir_present": dir_ok,
                    "secret_present": secret_ok,
                    "drift": drift,
                    "healed": healed,
                }
            )

    result = {
        "agent_id": agent_id,
        "definition_id": definition_id,
        "pods_scanned": len(running_pods),
        "drift": drift_any,
        "healed": healed_any,
        "details": details,
    }
    if drift_any:
        logger.info(
            "reconcile %s: drift detected, healed=%s, details=%s",
            agent_id[:8],
            healed_any,
            [d["skill"] for d in details if d["drift"]],
        )
    return result


@router.post("/api/controller/agents/{agent_id}/skills/secrets/reconcile")
async def reconcile_skill_secrets(agent_id: str, db: AsyncSession = Depends(get_manager_db)):
    """Pod 启动时调（entrypoint-v2.sh curl）：全链技能一致性对账 + 自愈。

    历史：原仅回写 secrets.enc；现升级为全链 reconcile（DB↔COS↔Pod 文件↔secrets.enc），
    Pod 缺文件也自动从 COS 重放。保留旧 URL 不改 entrypoint（无需引擎镜像 bump）。
    详见 reconcile_skills。
    """
    return await reconcile_skills(agent_id, db)


@router.post("/api/controller/agents/{agent_id}/skills/reconcile")
async def reconcile_skills_endpoint(agent_id: str, db: AsyncSession = Depends(get_manager_db)):
    """全链技能 reconcile 规范端点（resume/周期循环/manual 调用）。详见 reconcile_skills。"""
    return await reconcile_skills(agent_id, db)


@router.post("/api/controller/agents/{agent_id}/skills/install")
async def install_skill(
    agent_id: str, req: SkillInstallRequest, db: AsyncSession = Depends(get_manager_db)
):
    """安装技能：解压到各 Pod 的**共享 skill 目录** + 重生成 config.yaml。

    external_dirs 模型：写 /opt/data/skills/{definition_id}/{name}/ 一次（per-Pod），
    同 definition 的所有 profile 经 config.yaml external_dirs 共享读，无需 per-profile 复制。
    Hermes 热扫描 skills 目录（`hermes skills list` 即时识别），写文件即生效，不重启。
    Manager 已把技能元数据写入 skill_config；此处负责文件 fan-out。
    """
    import base64

    skill_name = req.skill_name.strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        raise HTTPException(status_code=400, detail="Invalid skill name")
    try:
        zip_bytes = base64.b64decode(req.zip_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid zip_b64")

    written = await _fanout_skill_to_pods(agent_id, skill_name, zip_bytes, db)
    if written == 0:
        return {"status": "no_pods", "installed": False}
    return {"status": "installed", "skill": skill_name}


@router.post("/api/controller/agents/{agent_id}/skills/config/sync")
async def sync_skills_config(agent_id: str, db: AsyncSession = Depends(get_manager_db)):
    """重写各 home 的 config.yaml 以反映最新 skills.disabled（开关用，热生效不重启）。

    Manager 已更新 skill_config.enabled，此处 fan-out 重生成 config.yaml。
    """
    pods = await _iter_agent_target_pods(agent_id, db)
    for p in pods:
        if not p["pod_name"]:
            continue
        try:
            await _regen_homes_config(p["pod_name"], agent_id, p["homes"], db)
        except Exception as e:
            logger.warning("sync skills config on %s failed: %s", p["pod_name"][:30], e)
    return {"status": "synced"}


# 扫描引擎 skills 目录的 Python 脚本（写入 Pod /tmp 后执行，避免 shell 引号转义）
# UA_HOME 指向共享 skill 目录 /opt/data/skills/{definition_id}，glob 其下所有 SKILL.md。
_SKILL_SCAN_SCRIPT = """import os, json, glob
try:
    import yaml
except Exception:
    yaml = None
home = os.environ.get('UA_HOME', '/opt/data/skills')
skills = []
for md in glob.glob(home + '/**/SKILL.md', recursive=True):
    try:
        text = open(md, encoding='utf-8', errors='replace').read()
        fm = {}
        if text.startswith('---'):
            parts = text.split('---', 2)
            if len(parts) >= 3:
                fmtxt = parts[1]
                if yaml:
                    fm = yaml.safe_load(fmtxt) or {}
                else:
                    for line in fmtxt.splitlines():
                        if ':' in line:
                            k, _, v = line.partition(':')
                            fm[k.strip()] = (v or '').strip().strip('"').strip("'")
        skills.append({
            'name': fm.get('name') or os.path.basename(os.path.dirname(md)),
            'description': fm.get('description', '') or '',
            'version': str(fm.get('version', '') or ''),
            'author': fm.get('author', '') or '',
        })
    except Exception:
        pass
print(json.dumps(skills, ensure_ascii=False))
"""

_SKILL_SCAN_PATH = "/tmp/ua_scan_skills.py"


@router.get("/api/controller/agents/{agent_id}/skills/list")
async def list_engine_skills(agent_id: str, db: AsyncSession = Depends(get_manager_db)):
    """扫描引擎 Pod 的**共享 skill 目录**，返回所有技能元数据。

    external_dirs 模型：skill 文件在 /opt/data/skills/{definition_id}/**/SKILL.md。
    递归解析 YAML frontmatter（name/description/version/author）。
    """
    configs = await _load_agent_configs(agent_id, db)
    if configs is None or not configs[2]:
        return {"engine_deployed": False, "items": []}
    sdir = _shared_skill_dir(configs[2])
    pods = await _iter_agent_target_pods(agent_id, db)
    if not pods:
        return {"engine_deployed": False, "items": []}
    for p in pods:
        pod_name = p["pod_name"]
        if not pod_name:
            continue
        try:
            await k8s_manager.exec_write_file_in_pod(pod_name, _SKILL_SCAN_PATH, _SKILL_SCAN_SCRIPT)
            out = await k8s_manager.exec_command_in_pod(
                pod_name, [f"UA_HOME={sdir} python3 {_SKILL_SCAN_PATH}"]
            )
            import json as _json

            last = (out or "").strip().splitlines()[-1] if out else ""
            items = _json.loads(last) if last else []
            return {"engine_deployed": True, "items": items}
        except Exception as e:
            logger.warning("scan skills on %s:%s failed: %s", pod_name[:30], sdir, e)
            continue
        break
    return {"engine_deployed": True, "items": []}


@router.delete("/api/controller/agents/{agent_id}/skills/{skill_name}")
async def uninstall_skill(
    agent_id: str, skill_name: str, db: AsyncSession = Depends(get_manager_db)
):
    """卸载技能：删各 Pod 共享目录下的 {name}/ + 重生成 config.yaml。热生效，不重启。"""
    name = skill_name.strip().lower().replace(" ", "-")
    if not name or "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid skill name")
    configs = await _load_agent_configs(agent_id, db)
    if configs is None or not configs[2]:
        return {"status": "uninstalled", "skill": name}
    sdir = _shared_skill_dir(configs[2])
    pods = await _iter_agent_target_pods(agent_id, db)
    for p in pods:
        pod_name = p["pod_name"]
        if not pod_name:
            continue
        try:
            await k8s_manager.exec_command_in_pod(pod_name, [f"rm -rf {sdir}/{name}"])
            await _regen_homes_config(pod_name, agent_id, p["homes"], db)
            # 删 skill 快照让 gateway 下次消息时重建（同 _fanout_skill_to_pods）
            for home in p["homes"]:
                try:
                    await k8s_manager.exec_command_in_pod(
                        pod_name, [f"rm -f {home}/.skills_prompt_snapshot.json"]
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("uninstall skill %s on %s failed: %s", name, pod_name[:30], e)
    return {"status": "uninstalled", "skill": name}


async def backfill_presets(db: AsyncSession) -> None:
    """启动时给已存智能体补装缺失的 preset skill（幂等，只追加不删）。

    preset 只在 create_definition 时注入；新增 preset 后已存智能体不会自动有。
    本函数扫描所有 definition，补缺失 preset：skill_config + MinIO zip + fan-out 到 running pod。
    best-effort，失败不阻断启动。
    """
    from app.models import AgentDefinition, AgentInstance
    from app.services.preset_skills import prefill_skill_config, save_preset_zips

    try:
        defs = (await db.execute(select(AgentDefinition))).scalars().all()
    except Exception:
        logger.warning("backfill_presets: load definitions failed", exc_info=True)
        return
    for d in defs:
        try:
            old_sc = d.skill_config or {}
            new_sc = prefill_skill_config(old_sc)
            if new_sc == old_sc:
                continue
            # 新增的 preset name（prefill 只追加不删，差集即新增）
            old_names = {(s.get("name") or "").lower() for s in (old_sc.get("skills") or [])}
            added = [
                s["name"]
                for s in new_sc["skills"]
                if (s.get("name") or "").lower() not in old_names
            ]
            d.skill_config = new_sc
            await db.commit()
            try:
                save_preset_zips(d.id)
            except Exception:
                logger.warning("backfill save_preset_zips %s failed", str(d.id)[:8], exc_info=True)
            # fan-out 新 preset 到 running pod
            insts = (
                (await db.execute(select(AgentInstance).where(AgentInstance.definition_id == d.id)))
                .scalars()
                .all()
            )
            for inst in insts:
                for name in added:
                    try:
                        zip_bytes = archiver.get_skill_zip(str(d.id), name)
                        if zip_bytes:
                            await _fanout_skill_to_pods(str(inst.id), name, zip_bytes, db)
                    except Exception:
                        logger.warning(
                            "backfill fanout %s/%s failed", str(d.id)[:8], name, exc_info=True
                        )
            logger.info("backfill presets for definition %s: added %s", str(d.id)[:8], added)
        except Exception:
            logger.warning("backfill_presets for %s failed", str(d.id)[:8], exc_info=True)
