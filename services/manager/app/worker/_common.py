"""worker 共享基础设施 — 跨域 helper + 响应模型（无路由）。

从 router.py 抽出的横切件：DB 配置读取、agent 锁、port_map、外部 Dify 判定、
响应 schema。依赖方向：域模块 → _common；_common 不得 import 任何域模块（避免循环）。
"""

import json
import logging
from pathlib import Path
from string import Template

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.models import AgentDeployment, AgentProfile, DeploymentStatus

from .k8s_manager import k8s_manager

logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────


class AgentStatusResponse(BaseModel):
    agent_id: str
    status: DeploymentStatus
    engine_url: str | None = None
    last_active_at: str | None = None
    error_message: str | None = None
    # 实时 Pod 信息（供前端判定 rollout 重启完成：pod 名/启动时间变化 + Running）
    pod_name: str | None = None
    pod_start_time: str | None = None
    pod_phase: str | None = None


class PodInfo(BaseModel):
    name: str
    status: str
    node: str
    cpu: str
    memory: str
    restarts: int
    age: str
    agent_id: str
    deployment_id: str


class PodListResponse(BaseModel):
    items: list[PodInfo]
    total: int


# ── V3 三层模型读取 helpers ────────────────────────────────


async def load_instance_config(db: AsyncSession, instance_id: str) -> dict | None:
    """V3: 按 instance_id 读取实例运行配置（JOIN agent_instances + agent_versions +
    agent_definitions + user_groups）。

    controller 端点入参沿用 `agent_id` 命名，但语义即 instance_id（迁移保留老 id，
    故 agent_deployments.instance_id 天然 == agent_instances.id）。

    返回:
      model_config: dict（litellm/system_prompt 等；instance.litellm_config 覆盖
        version 快照的 litellm 段，确保 per-instance key 生效）
      skill_config: dict
      engine_type: str（取自 definition，用于查 ENGINE_RUNTIMES 取镜像/端口）
      resource_pool_id: str | None（语义即老 engine_instance_id）
      group_code: str | None（取自 user_groups.code，用于 MinIO 组前缀 + Pod label）
      definition_id: str | None（取自 agent_definitions.id，用于 MinIO skill zip store 定位）
    instance 不存在返回 None。
    """
    row = await db.execute(
        text(
            "SELECT i.group_id, i.resource_pool_id, i.litellm_config, "
            "i.dify_config, i.runtime_config, "
            "v.model_config, v.skill_config, v.persona_config, "
            "d.engine_type, d.id AS definition_id, "
            "ug.code AS group_code "
            "FROM agent_instances i "
            "JOIN agent_versions v ON v.id = i.version_id "
            "JOIN agent_definitions d ON d.id = i.definition_id "
            "LEFT JOIN user_groups ug ON ug.id = i.group_id "
            "WHERE i.id = :id"
        ),
        {"id": instance_id},
    )
    data = row.mappings().first()
    if not data:
        return None
    mc = data.get("model_config") or {}
    if isinstance(mc, str):
        mc = json.loads(mc)
    sc = data.get("skill_config") or {}
    if isinstance(sc, str):
        sc = json.loads(sc)
    lc = data.get("litellm_config") or {}
    if isinstance(lc, str):
        lc = json.loads(lc)
    dc = data.get("dify_config") or {}
    if isinstance(dc, str):
        dc = json.loads(dc)
    rc = data.get("runtime_config") or {}
    if isinstance(rc, str):
        rc = json.loads(rc)
    pc = data.get("persona_config") or {}
    if isinstance(pc, str):
        pc = json.loads(pc)
    if lc:
        # per-instance LiteLLM key 覆盖版本快照里的 litellm 段；
        # 但 context_length 是模型属性（非 per-instance key），从版本快照继承，
        # 避免实例覆盖块（仅 team_id/key_id/key/model_group）丢失 context_length
        # → 引擎 config.yaml 无 context_length → model_metadata 探针空跑。
        mc = dict(mc)
        merged = dict(lc)
        version_litellm = mc.get("litellm") or {}
        if "context_length" not in merged and version_litellm.get("context_length") is not None:
            merged["context_length"] = version_litellm["context_length"]
        mc["litellm"] = merged
    return {
        "model_config": mc,
        "skill_config": sc,
        "persona_config": pc or {},
        "dify_config": dc or {},
        "runtime_config": rc or {},
        "engine_type": data.get("engine_type") or "HERMES",
        "resource_pool_id": str(data["resource_pool_id"]) if data.get("resource_pool_id") else None,
        "group_id": str(data["group_id"]) if data.get("group_id") else None,
        "group_code": data.get("group_code") or None,
        "definition_id": str(data["definition_id"]) if data.get("definition_id") else None,
    }


async def load_resource_spec(db: AsyncSession, resource_pool_id: str) -> dict | None:
    """V3: 按 resource_pool_id 读取资源规格，返回兼容老 engine_instances 字段的 dict。

    含 min_cpu/max_cpu/min_memory/max_memory + max_profiles_per_pod（=max_sessions_per_pod
    的兼容键，下游 _ensure_pod_exists/_do_create_profile 读此键）。
    镜像不再来自表（走 ENGINE_RUNTIMES[engine_type]），故不含 engine_image。
    """
    row = await db.execute(
        text(
            "SELECT min_cpu, max_cpu, min_memory, max_memory, max_sessions_per_pod "
            "FROM resource_pools WHERE id = :id"
        ),
        {"id": resource_pool_id},
    )
    data = row.mappings().first()
    if not data:
        return None
    spec = dict(data)
    # 兼容键：下游读 max_profiles_per_pod
    spec["max_profiles_per_pod"] = spec.get("max_sessions_per_pod") or 20
    return spec


async def load_group_code(db: AsyncSession, instance_id: str) -> str | None:
    """按 instance_id 读取所属 UserGroup 的 code（用于 MinIO 组前缀 + Pod label）。

    suspend/destroy 后台循环不经过 load_instance_config，故单独提供此轻量查询。
    instance 不存在或 group_code 缺失时返回 None（调用方回退到 archiver 默认组）。
    """
    row = await db.execute(
        text(
            "SELECT ug.code AS group_code "
            "FROM agent_instances i "
            "LEFT JOIN user_groups ug ON ug.id = i.group_id "
            "WHERE i.id = :id"
        ),
        {"id": instance_id},
    )
    data = row.mappings().first()
    if not data:
        return None
    return data.get("group_code") or None


# ── 浏览器沙箱 Pod 生命周期 helper ────────────────────────
#
# browser Pod 是 per-profile 独立 Pod（kasmweb/chrome + cdp-proxy sidecar），
# 与引擎 Pod 解耦。pod_name 存 deployment.internal_port_map["browsers"][profile_name]
# （与 ["profiles"] 端口映射并列，不改端口 int 结构），供 gateway 直查取。


def _set_browser_pod_in_port_map(deployment, profile_name: str, pod_name: str | None) -> None:
    """把 browser pod_name 写入/移出 internal_port_map["browsers"][profile_name]。

    整体重赋 internal_port_map（非原地改）以触发 SQLAlchemy JSON 列变更检测。
    """
    port_map = dict(deployment.internal_port_map or {})
    browsers = dict(port_map.get("browsers") or {})
    if pod_name:
        browsers[profile_name] = pod_name
    else:
        browsers.pop(profile_name, None)
    port_map["browsers"] = browsers
    deployment.internal_port_map = port_map


async def ensure_browser_pod_for_profile(
    agent_id: str, profile_name: str, deployment, db: AsyncSession
) -> str | None:
    """实例启用浏览器沙箱时，为该 profile 确保 browser Pod 存在；返回 pod_name。

    未启用 / 加载失败 / 创建失败时返回 None（best-effort，不阻断 profile 创建）。
    幂等：browser Pod 已存在则 k8s 409 复用，pod_name 仍写回 internal_port_map。
    """
    try:
        inst_cfg = await load_instance_config(db, agent_id)
    except Exception:
        return None
    if not inst_cfg:
        return None
    rc = inst_cfg.get("runtime_config") or {}
    # 严格判定：仅认显式 {"browser_sandbox": {"enabled": True}}，避免 mock/缺省值误判启用
    bs = rc.get("browser_sandbox") if isinstance(rc, dict) else None
    if not (isinstance(bs, dict) and bs.get("enabled") is True):
        return None
    group_code = inst_cfg.get("group_code")
    try:
        info = await k8s_manager.create_browser_pod(agent_id, profile_name, group_code)
    except Exception as e:
        logger.warning("create_browser_pod for profile %s failed: %s", profile_name[:16], e)
        return None
    pod_name = info["name"] if isinstance(info, dict) else info
    vnc_pw = info.get("vnc_pw") if isinstance(info, dict) else None
    _set_browser_pod_in_port_map(deployment, profile_name, {"pod": pod_name, "vnc_pw": vnc_pw})
    return pod_name


async def suspend_browser_pods_for_deployment(deployment, agent_id: str) -> None:
    """SUSPEND：该 deployment 所有 profile 的 browser Pod scale=0（留 PVC 登录态）。best-effort。"""
    browsers = ((deployment.internal_port_map or {}).get("browsers")) or {}
    for pn in list(browsers.keys()):
        try:
            await k8s_manager.scale_browser_to_zero(agent_id, pn)
        except Exception as e:
            logger.warning("suspend browser pod %s failed: %s", pn[:16], e)


async def resume_browser_pods_for_deployment(deployment, agent_id: str) -> None:
    """RESUME：该 deployment 所有 profile 的 browser Pod scale=1（从 SUSPEND scale=0 恢复）。best-effort。

    仅 scale=1；browser Pod 被 idle-kill 删除的场景（resume_browser_pod 返回 False）暂不重建，
    由后续 profile 重建/ reconcile 兜底（v1 idle-kill 未启用，SUSPEND→RESUME 必有 scale=0 的 Pod）。
    """
    browsers = ((deployment.internal_port_map or {}).get("browsers")) or {}
    for pn in list(browsers.keys()):
        try:
            await k8s_manager.resume_browser_pod(agent_id, pn)
        except Exception as e:
            logger.warning("resume browser pod %s failed: %s", pn[:16], e)


async def delete_browser_pods_for_deployment(deployment, agent_id: str) -> None:
    """DESTROY：删该 deployment 所有 profile 的 browser Pod + PVC + Secret + NetPol。best-effort。"""
    browsers = ((deployment.internal_port_map or {}).get("browsers")) or {}
    for pn in list(browsers.keys()):
        try:
            await k8s_manager.delete_browser_pod(agent_id, pn)
        except Exception as e:
            logger.warning("delete browser pod %s failed: %s", pn[:16], e)


async def acquire_agent_lock(db: AsyncSession, agent_id: str) -> None:
    """以 agent_id 为键获取 PostgreSQL 事务级咨询锁，序列化 suspend/destroy/deploy。

    避免 resume(deploy) 与后台 destroy 竞态删刚拉起的 Pod/PVC、或并发 suspend 冲突。
    事务级锁随当前事务 commit/rollback 自动释放，无需 schema 迁移。hashtext 返回
    int4，隐式转 bigint 适配 pg_advisory_xact_lock(bigint)。
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:aid))"), {"aid": agent_id})


# ── 多 Profile port_map 调度 ────────────────────────────────


async def port_map_exec(
    agent_id: str, args: str, scope_type: str, scope_target_id: str | None
) -> str:
    """在 Pod 内调 port_map.py（profile→port 唯一真相），返回 strip 后的 stdout。

    失败（Pod 重启中 / exec 超时）抛 RuntimeError，调用方决定降级路径。
    """
    out = await k8s_manager.exec_hermes_command(
        agent_id=agent_id,
        commands=[f"python3 /opt/scripts/port_map.py {args}"],
        scope_type=scope_type,
        scope_target_id=scope_target_id,
    )
    return (out or "").strip()


async def port_map_alloc(
    agent_id: str, profile_name: str, scope_type: str, scope_target_id: str | None
) -> int:
    """从 port_map.json 分配端口（幂等）。失败抛 HTTPException 503。"""
    try:
        out = await port_map_exec(agent_id, f"alloc {profile_name}", scope_type, scope_target_id)
        return int(out)
    except Exception as e:
        logger.error("port_map alloc failed for %s: %s", profile_name[:16], e)
        raise HTTPException(status_code=503, detail=f"port allocation failed: {e}")


async def port_map_all(
    agent_id: str, scope_type: str, scope_target_id: str | None
) -> dict[str, int]:
    """读 port_map.json 全量 {name: port}（真相）。失败返回空 dict（不抛，降级）。"""
    try:
        out = await port_map_exec(agent_id, "all", scope_type, scope_target_id)
        return json.loads(out or "{}")
    except Exception as e:
        logger.warning("port_map all read failed for %s: %s", agent_id[:8], e)
        return {}


async def sync_deployment_port_map_mirror(
    db: AsyncSession, deployment_id, profiles: dict[str, int]
) -> None:
    """把 port_map.json 真相同步到 agent_deployments.internal_port_map 镜像（去掉 next_port）。

    仅更新 profiles 子键，保留其他子键（尤其 browsers——VNC 接管靠它解析 browser Pod）。
    旧实现整体赋 `{"profiles": ...}` 会把 browsers 抹掉，每次 ensure（每条消息）都跑 →
    profile 一收到消息 browser Pod 记录就清空 → VNC 接管 403。
    用裸 SQL 写 JSON 列，确保 ORM 检测到变更。
    """
    row = (
        await db.execute(
            text("SELECT internal_port_map FROM agent_deployments WHERE id = :did"),
            {"did": str(deployment_id)},
        )
    ).fetchone()
    existing = row[0] if row else None
    merged: dict = dict(existing) if isinstance(existing, dict) else {}
    merged["profiles"] = dict(profiles)
    await db.execute(
        text(
            "UPDATE agent_deployments SET internal_port_map = CAST(:pm AS json) WHERE id = :did"
        ),
        {"pm": json.dumps(merged), "did": str(deployment_id)},
    )


# ── 通用 helper ────────────────────────────────────────────


def short_agent(agent_id: str) -> str:
    """与 profile 命名一致的 agent 短标识，用作技能存储的隔离命名空间。"""
    return agent_id.replace("-", "")[:8]


def is_external_dify_deployment(dep: AgentDeployment | None) -> bool:
    """判断 dep 是否为 Dify 外部实例模式（无 Pod，engine_url 指向外部 URL）。

    外部实例特征：engine_url 存在且非集群 DNS（不含 .svc.cluster.local）。
    Pod 模式 engine_url 形如 http://engine-dify-xxx.ns.svc.cluster.local:8080。
    """
    if not dep or not dep.engine_url:
        return False
    return ".svc.cluster.local" not in dep.engine_url


# ── 引擎环境变量 / 配置渲染（跨 config_skills / profiles / lifecycle 共用）──


def build_engine_envs(model_config: dict) -> dict:
    """从 agent model_config 中提取引擎环境变量。

    引擎只支持 LiteLLM 接入：注入 LITELLM_BASE_URL / LITELLM_API_KEY / LITELLM_MODEL，
    引擎 entrypoint 据此把 Hermes 指向 LiteLLM 的 OpenAI 兼容端点。
    """
    litellm = (model_config or {}).get("litellm") or {}
    if not litellm.get("key"):
        return {}  # 未配置 LiteLLM 模型，引擎启动后无模型可用
    base = settings.litellm_base_url.rstrip("/") + "/v1"
    return {
        "LITELLM_BASE_URL": base,
        "LITELLM_API_KEY": litellm["key"],
        "LITELLM_MODEL": litellm.get("model") or litellm.get("model_group") or "",
    }


async def load_agent_configs(
    agent_id: str, db: AsyncSession
) -> tuple[dict, dict, str | None] | None:
    """读取 instance 的 model_config / skill_config / definition_id（JSON 字段，统一解包）。

    V3: agent_id 语义 = instance_id，经 load_instance_config 取 version 快照配置。
    instance 不存在时返回 None（与「config 为空」区分，后者是合法状态）。
    definition_id 用于渲染 skills.external_dirs（共享 skill 目录）。
    """
    cfg = await load_instance_config(db, agent_id)
    if cfg is None:
        return None
    return cfg["model_config"], cfg["skill_config"], cfg.get("definition_id")


def disabled_skill_names(skill_config: dict) -> list[str]:
    """从 skill_config 提取被禁用的技能名列表（disabled 覆盖 builtin+installed，
    对齐 Hermes config.yaml）。

    兼容旧结构 {skills:[{name,enabled}]}：若没有 disabled 列表，回退从 skills[].enabled=False 提取。
    """
    sc = skill_config or {}
    disabled = sc.get("disabled")
    if disabled is not None:
        return [str(n) for n in (disabled or []) if n]
    # 旧结构回退
    skills = sc.get("skills") or []
    return [s.get("name") for s in skills if not s.get("enabled", True) and s.get("name")]


_CONFIG_TEMPLATE_CACHE: str | None = None


def config_template_path() -> Path:
    """定位 engines/hermes/config/config.yaml.tmpl。

    从 _common.py 向上查找，兼容 dev（仓库根）与 manager 容器（/app/engines/...）。
    """
    for parent in Path(__file__).resolve().parents:
        p = parent / "engines" / "hermes" / "config" / "config.yaml.tmpl"
        if p.is_file():
            return p
    raise RuntimeError("config.yaml.tmpl not found under engines/hermes/config/")


def load_config_template() -> str:
    """加载并缓存 config.yaml 模板（首次调用读取，之后复用）。"""
    global _CONFIG_TEMPLATE_CACHE
    if _CONFIG_TEMPLATE_CACHE is None:
        _CONFIG_TEMPLATE_CACHE = config_template_path().read_text(encoding="utf-8")
    return _CONFIG_TEMPLATE_CACHE


def shared_skill_dir(definition_id: str) -> str:
    """共享 skill 目录路径（external_dirs 模型）：每 (Pod, definition) 一个。"""
    return f"/opt/data/skills/{definition_id}"


def render_skills_block(disabled: list[str], definition_id: str | None = None) -> str:
    """渲染 skills 段：external_dirs（指向共享 skill 目录）+ disabled（per-profile 开关）。

    external_dirs 模型：skill 文件放共享目录 /opt/data/skills/{definition_id}/，所有 profile
    经 config.yaml 指过去共享读；disabled 各 profile 不同（开关隔离）。definition_id 缺省
    （base profile / 兼容旧调用）时不写 external_dirs。
    """
    lines = ["skills:"]
    if definition_id:
        lines.append("  external_dirs:")
        lines.append(f"    - {shared_skill_dir(definition_id)}")
    if not disabled:
        lines.append("  disabled: []")
    else:
        lines.append("  disabled:")
        lines += [f"    - {n}" for n in disabled]
    return "\n".join(lines)


def render_plugins_block() -> str:
    """渲染 plugins 段：manager 侧配了 Langfuse 凭据时激活 observability/langfuse 插件。

    该插件 opt-in——profile config.yaml 无 plugins.enabled 段则引擎不加载 hooks，
    Hermes 内层 trace（"Hermes turn"）不会写入 Langfuse，链路追踪关联不到内部调用。
    凭据本身由 Pod env（HERMES_LANGFUSE_*）注入，此处只负责开关；插件 fail open，
    env 缺失时 hooks 静默 no-op。克隆自 base 的 config 会被本函数渲染结果整体覆盖，
    所以插件段必须由模板侧输出，不能依赖克隆继承。
    """
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return ""
    return "plugins:\n  enabled:\n    - observability/langfuse\n  disabled: []\n"


def build_profile_config_yaml(
    model_config: dict,
    skill_config: dict,
    definition_id: str | None = None,
    *,
    agent_id: str | None = None,
    profile_name: str | None = None,
    browser_sandbox: bool = False,
) -> str:
    """基于 engines/hermes/config/config.yaml.tmpl 渲染 Hermes profile 的 config.yaml。

    模板内含静态默认段（provider/security/approvals/tirith/security_policies/memory）
    与动态占位符 $model_default、$skills_block、$plugins_block。多处调用（sync_agent_config、
    heal_profile_runtime_config、regen_homes_config）共用，避免任一处整体覆盖时
    擦掉 skills.disabled / plugins.enabled。要改全局默认直接编辑模板文件。

    definition_id 用于渲染 skills.external_dirs（共享 skill 目录）；None 时不写 external_dirs。

    browser_sandbox：启用浏览器沙箱时，platform_toolsets.api_server 追加 `browser` 工具集
    + 注 `browser.cdp_url`（指向 per-profile browser Pod 的 CDP 代理端口）。需 agent_id +
    profile_name 计算 browser Pod DNS（命名确定性保证 cdp_url 跨 Pod 重建稳定）。
    """
    litellm = (model_config or {}).get("litellm") or {}
    model_name = litellm.get("model") or litellm.get("model_group") or ""
    # context_length：显式写入 config.yaml 可让 hermes 的 model_metadata 在 step 0 直接
    # return，跳过对 litellm 端点的探测（GET /v1/models/<model> 401 + 本地服务探针 404），
    # 省 ~80ms/轮 + 消除 litellm ERROR 噪音。非正整数（含 bool）省略，回退 hermes 自动探测。
    ctx = litellm.get("context_length")
    ctx_int: int | None = None
    if not isinstance(ctx, bool):
        try:
            v = int(ctx)
            if v > 0:
                ctx_int = v
        except (TypeError, ValueError):
            ctx_int = None
    context_length_line = f"  context_length: {ctx_int}\n" if ctx_int else ""
    disabled = disabled_skill_names(skill_config)

    # 浏览器沙箱：启用则追加 browser toolset + cdp_url 段
    # cdp_url 指向 browser Pod 的 CDP 代理（chrome 强制绑 127.0.0.1，Pod 内 sidecar 代理
    # 暴露 0.0.0.0:9222）。命名确定性 → cdp_url 跨 browser Pod 重建保持稳定，无需重写 config。
    browser_toolset_line = ""
    browser_block = ""
    if browser_sandbox and agent_id and profile_name:
        from app.worker.k8s_manager import _browser_name
        browser_dns = f"{_browser_name(agent_id, profile_name)}.{settings.k8s_namespace}.svc.cluster.local"
        cdp_url = f"http://{browser_dns}:{settings.browser_cdp_proxy_port}"
        browser_toolset_line = "    - browser\n"
        browser_block = (
            f"browser:\n  cdp_url: \"{cdp_url}\"\n"
            # dialog_policy 默认 must_respond、dialog_timeout_s 默认 300，无需显式设（P0 实测）
        )

    tpl = Template(load_config_template())
    return tpl.safe_substitute(
        model_default=model_name,
        context_length_line=context_length_line,
        skills_block=render_skills_block(disabled, definition_id),
        browser_toolset_line=browser_toolset_line,
        browser_block=browser_block,
        plugins_block=render_plugins_block(),
    )


async def iter_agent_target_pods(agent_id: str, db: AsyncSession) -> list[dict]:
    """枚举承载该 agent 引擎的宿主 Pod 及其「home 目录」列表（人设/技能写入目标）。

    自适应 V2 多 profile 布局：profile 目录在 /opt/data/profiles/{pn}，
    base 始终存在（entrypoint-v2 创建）。无 AgentProfile 记录时写 base，
    后续 clone 的 profile 自动继承。

    跨 agent 共享 Pod 时，一个 agent 的 profile 可能宿主在别的 agent 名下的 Pod，
    故通过 AgentDeployment 解析 owner_agent_id + scope，再用 get_pod_status 取实际 Pod 名。

    返回 [{pod_name, owner_agent_id, scope_type, scope_target_id, homes: [str]}]
    """
    dep_rows = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    deps = list(dep_rows.scalars().all())
    if not deps:
        return []

    # 该 agent 的所有 profile，按 deployment 分组
    prof_rows = await db.execute(select(AgentProfile).where(AgentProfile.instance_id == agent_id))
    profiles_by_dep: dict[str, list[str]] = {}
    for prof in prof_rows.scalars().all():
        profiles_by_dep.setdefault(str(prof.deployment_id), []).append(prof.profile_name)

    result: list[dict] = []
    for dep in deps:
        dep_id = str(dep.id)
        owner = str(dep.instance_id)
        scope_type = dep.scope_type
        scope_target_id = str(dep.scope_target_id) if dep.scope_target_id else None
        profs = profiles_by_dep.get(dep_id, [])
        # V2：profile 目录在 /opt/data/profiles/{pn}，base 始终存在（entrypoint 创建）。
        # 无 profile 记录时写 base，后续 clone 的 profile 自动继承。
        homes = [f"/opt/data/profiles/{pn}" for pn in profs] + ["/opt/data/profiles/base"]
        # 解析实际 Pod 名（按 owner + scope label）
        pod_name = None
        try:
            st = await k8s_manager.get_pod_status(owner, scope_type, scope_target_id)
            pod_name = st.get("pod_name")
        except Exception as e:
            logger.warning("resolve pod for dep %s failed: %s", dep_id[:8], e)
        result.append(
            {
                "pod_name": pod_name,
                "owner_agent_id": owner,
                "scope_type": scope_type,
                "scope_target_id": scope_target_id,
                "homes": homes,
            }
        )
    return result


async def heal_profile_runtime_config(
    agent_id: str,
    profile_name: str,
    db: AsyncSession,
    scope_type: str = "ALL",
    scope_target_id: str | None = None,
    port: int | None = None,
) -> None:
    """把 agent 当前 LiteLLM 配置写入指定 profile 的 config.yaml + .env。

    修复 PVC 持久化的 stale profile：克隆 profile 在 LiteLLM 改造前从旧 base 克隆出来，
    .env 残留 DEEPSEEK_API_KEY、config.yaml provider=auto → hermes 直连 DeepSeek 绕过 LiteLLM，
    导致 spend_logs 不归因、概览停滞。base 每次 Pod 启动已被 entrypoint 刷新，但克隆 profile
    的 .env/config.yaml 不会自动更新，故在此强制对齐。

    - config.yaml 整体覆盖（无 profile 专属字段）：provider=openai-api, default=model
    - .env patch：删 DEEPSEEK_API_KEY/旧 OPENAI_API_KEY/OPENAI_BASE_URL，追加 OPENAI_API_KEY +
      OPENAI_BASE_URL(litellm)，保留 API_SERVER_KEY 等
    - API_SERVER_PORT：旧 profile（早期创建、.env 无此行或缺省 8642）Pod 重启后 gateway 绑
      8642 与 nginx 冲突 → 502。已知端口（创建时分配或 AgentProfile.internal_port）时强制对齐。
    """
    cfg = await load_instance_config(db, agent_id)
    if cfg is None:
        return
    model_config = cfg["model_config"]
    skill_config = cfg["skill_config"]
    definition_id = cfg.get("definition_id")
    runtime_config = cfg.get("runtime_config") or {}
    browser_sandbox = bool((runtime_config.get("browser_sandbox") or {}).get("enabled"))
    litellm = model_config.get("litellm") or {}
    api_key = litellm.get("key", "")
    if not api_key:
        # 无 per-agent key 不写，避免清空已有可用配置
        return

    # 端口：显式传入优先（do_create_profile 已分配），否则查 AgentProfile.internal_port
    if port is None:
        r = await db.execute(
            select(AgentProfile).where(
                AgentProfile.instance_id == agent_id,
                AgentProfile.profile_name == profile_name,
            )
        )
        prof = r.scalar_one_or_none()
        if prof:
            port = prof.internal_port

    base = settings.litellm_base_url.rstrip("/") + "/v1"
    prof_dir = f"/opt/data/profiles/{profile_name}"

    # 1. config.yaml 整体覆盖（统一生成函数，含 skills.external_dirs + disabled + memory.user_profile_enabled）
    config_yaml = build_profile_config_yaml(
        model_config, skill_config, definition_id,
        agent_id=agent_id, profile_name=profile_name, browser_sandbox=browser_sandbox,
    )
    try:
        await k8s_manager.exec_write_file(
            agent_id,
            f"{prof_dir}/config.yaml",
            config_yaml,
            scope_type=scope_type,
            scope_target_id=scope_target_id,
        )
    except Exception as e:
        logger.warning("heal config.yaml for profile %s failed: %s", profile_name[:16], e)

    # 1.5 一次性迁移：external_dirs 启用后，旧 per-home skills/ 目录变冗余（Hermes 会同时扫
    # local + external 重复加载）。清空 {prof_dir}/skills/* 并写 marker，仅执行一次。
    # marker 在 PVC 上，Pod 重启不重做；新 profile skills/ 本就为空，rm 无副作用。
    if definition_id:
        marker = f"{prof_dir}/.skills-migrated"
        try:
            await k8s_manager.exec_hermes_command(
                agent_id,
                [(
                    f'test -e {marker} || '
                    f'(rm -rf {prof_dir}/skills/* 2>/dev/null; mkdir -p {prof_dir}/skills; '
                    f'touch {marker})'
                )],
                scope_type=scope_type,
                scope_target_id=scope_target_id,
            )
        except Exception as e:
            logger.warning("migrate per-home skills for %s failed: %s", profile_name[:16], e)

    # 2. .env patch：key 经环境变量注入避免 shell 转义；删旧 key 行 + 追加 OPENAI_*；
    #    已知端口时同步 API_SERVER_PORT（drop 旧值 + 追加正确值，缺失亦补上）
    port_env = f"UA_PORT={port} " if port else ""
    patch_cmd = (
        f'cd {prof_dir} && UA_KEY={api_key} UA_BASE={base} {port_env}python3 -c "'
        "import os; p='" + prof_dir + "/.env'; "
        "lines=open(p).read().splitlines() if os.path.exists(p) else []; "
        "drop=('DEEPSEEK_API_KEY=','OPENAI_API_KEY=','OPENAI_BASE_URL='); "
        "lines=[l for l in lines if not l.startswith(drop)]; "
        "lines+=['OPENAI_API_KEY='+os.environ['UA_KEY'],'OPENAI_BASE_URL='+os.environ['UA_BASE']]; "
        # UA_PORT 已知 → drop 旧 API_SERVER_PORT 行并追加正确值；未知 → 原样保留（and 短路）。
        # 用条件表达式而非 `if:` —— `;` 后不能接复合语句（SyntaxError）。
        "lines=(os.environ.get('UA_PORT') and [l for l in lines "
        "if not l.startswith('API_SERVER_PORT=')]+"
        "['API_SERVER_PORT='+os.environ['UA_PORT']]) or lines; "
        "open(p,'w').write('\\n'.join(lines)+'\\n')\""
    )
    try:
        await k8s_manager.exec_hermes_command(
            agent_id,
            [patch_cmd],
            scope_type=scope_type,
            scope_target_id=scope_target_id,
        )
    except Exception as e:
        logger.warning("heal .env for profile %s failed: %s", profile_name[:16], e)
