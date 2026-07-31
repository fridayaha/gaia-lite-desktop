"""Profile-aware routing resolver.

根据 (user_id, agent_id, channel_type) 解析:
- 目标 Profile 名称
- 目标 Pod（Deployment）
- Profile 类型（恒为 INDEPENDENT，SHARED 已下线）

安全约束:
- Profile 名由服务端计算，不信任客户端传入
- 验证用户对 Agent 的访问权限
- 验证用户对 Profile 的所有权
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import text

from pkg.common.database import async_session
from app.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class ResolvedTarget:
    """路由解析结果"""
    profile_name: str
    profile_type: str          # always INDEPENDENT (SHARED retired)
    deployment_id: str
    engine_instance_id: str    # 所属 EngineInstance，用于创建 Profile
    resolved_user_id: str      # 解析后的用户 UUID（IM 映射后）
    pod_name: str
    engine_url: str
    internal_port: int | None  # Pod 内 Hermes 进程端口（nginx 路由用）
    scope_type: str
    scope_target_id: str | None
    was_cold: bool = False     # profile 冷启动（port_map 无缓存，调了 controller ensure）
    # 当前用户身份（非 PII 摘要，来自 manager /user-context 端点，60s 缓存）。
    # None=未拉取/拉取失败/开关关闭 → 不注入 ephemeral user hint。
    user_context: dict | None = None


class ProfileNotFound(Exception):
    """无法解析 Profile"""
    pass


class AccessDenied(ProfileNotFound):
    """用户无权限访问此智能体"""
    pass


class NotBound(ProfileNotFound):
    """IM 用户未绑定平台账号（im_user_bindings 无记录）"""
    pass


class ProfileResolver:
    """Profile 路由解析器"""

    def __init__(self):
        self._cache: dict[str, tuple[ResolvedTarget, float]] = {}
        self._cache_ttl: float = 60.0  # 60s 成功结果缓存
        # 负缓存：拒绝结果（NotBound/AccessDenied/ProfileNotFound）短缓存，
        # 防止未授权用户刷消息重复打 DB。value 为 (异常类型, 过期时间)。
        self._neg_cache: dict[str, tuple[type, float]] = {}
        self._neg_cache_ttl: float = 10.0  # 10s 负缓存
        self._deploy_lock = asyncio.Lock()
        # 记录每个 agent 最近的 pod_name，用于检测 Pod 重启后缓存失效
        self._last_pod_name: dict[str, str] = {}
        # profile_name → (user_context, expires) 并行缓存：dispatcher 注入点按 profile_name
        # 查用户身份（避免重复调 resolve 的 pod_name DB 校验）。与 _cache 同 TTL 同步写入。
        self._user_context_cache: dict[str, tuple[dict | None, float]] = {}
        # 502/503 自愈标记：profile_name -> 过期时间戳。命中则强制 re-ensure
        # （即使 port_map 有 cached_port），否则死 gateway 永不复活。
        self._failed_profiles: dict[str, float] = {}
        self._failed_ttl: float = 30.0  # 30s：足够 controller 探测+重启，超时自动恢复

    # ── Public API ──────────────────────────────────────────

    async def resolve(
        self, user_id: str, agent_id: str, channel_type: str = "http", is_admin: bool | None = None
    ) -> ResolvedTarget:
        """主入口: 根据用户+智能体+渠道解析路由目标"""
        cache_key = f"{user_id}:{agent_id}:{channel_type}"

        # 检查缓存（含 pod_name 变化检测：Pod 重启后 pod_name 变化 → 缓存失效）
        cached = self._cache.get(cache_key)
        if cached:
            target, expires = cached
            if time.time() < expires:
                # 验证缓存的 pod_name 是否与 DB 当前一致（防 Pod 重启后 stale）
                current_pod = await self._get_deployment_pod_name(agent_id)
                if current_pod and target.pod_name and current_pod != target.pod_name:
                    logger.info("Pod changed for %s (%s -> %s), invalidating cache",
                                agent_id[:8], target.pod_name[:24], current_pod[:24])
                    self._cache.pop(cache_key, None)
                else:
                    return target

        # 0. IM 用户 ID 映射：将 IM 平台用户 ID 转为内部 UUID
        #    企业微信/飞书等的用户 ID（FromUserName）不是 UUID，
        #    需要查 im_user_bindings 表转换为 UnionAgents 用户 UUID。
        #    未绑定时立即拒绝：ALL scope 同样要求 binding（统一身份模型，
        #    避免非 UUID 的 IM 原始 ID 流入 _ensure_profile/Controller 污染数据）。
        original_user_id = user_id
        if channel_type != "http":
            resolved = await self._resolve_im_user(user_id, channel_type)
            if resolved:
                user_id = resolved  # 替换为 UUID
                logger.debug("Resolved IM user %s -> %s", original_user_id[:12], resolved[:8])
            else:
                raise NotBound(
                    f"IM user {original_user_id[:12]} not bound on channel {channel_type}"
                )

        # 1. 查询 Agent 基本信息（验证存在 + 获取 engine_instance_id）
        agent = await self._get_agent(agent_id)
        if not agent:
            raise ProfileNotFound(f"Agent {agent_id[:8]} not found")

        # 2. 验证用户访问权限
        if not await self._check_access(user_id, agent, is_admin):
            raise AccessDenied(f"User {user_id[:8]} has no access to agent {agent_id[:8]}")

        # 3. 查询 Channel（只检查存在性，不按 scope 匹配）
        channel = await self._match_channel(agent_id, channel_type)
        if not channel:
            raise ProfileNotFound(
                f"No channel found for agent={agent_id[:8]} type={channel_type}"
            )

        # 3b. 派生 scope（channel 显式配 INDEPENDENT 时走 USER 级独立 Profile）
        scope_type, scope_target_id, profile_type = await self._derive_scope(agent, user_id, channel)

        # 4. 构造 profile_name
        profile_name = self._build_profile_name(
            agent_id, scope_type, scope_target_id, user_id
        )

        # 5. 查找 Deployment（按 agent_id，UNIQUE 约束 1:1）
        deployment = await self._get_deployment(agent_id)
        if not deployment:
            raise ProfileNotFound(
                f"No deployment found for agent={agent_id[:8]}"
            )

        # 6. 确保 Profile 记录存在
        await self._ensure_profile(
            agent_id=agent_id,
            deployment_id=deployment["id"],
            profile_name=profile_name,
            profile_type=profile_type,
            user_id=user_id,
            scope_target_id=scope_target_id,
            group_id=agent.get("group_id"),
        )

        # 6b. 调 Controller 确保 Profile 在 Pod 上就绪（gateway 进程 + nginx 路由）
        # 幂等优化：deployment.internal_port_map 持久记录已建 profile 的端口，
        # 已有端口说明 profile 已就绪，直接复用，不调 controller ensure（避免每条
        # 消息都触发 k8s exec + gateway --replace 重启）。仅无端口时才 ensure。
        # 502 自愈：upstream 返回 502/503 时 proxy 调 invalidate(profile_name) 标记
        # 该 profile 为 failed → 此处命中 _failed_profiles → 即使有 cached_port 也
        # 强制 re-ensure，让 controller 健康探测发现死 gateway 再 --replace 重启。
        # 否则 cached_port 永远命中，死 gateway 永不复活，502 持续。
        port_map = deployment.get("internal_port_map") or {}
        profiles = port_map.get("profiles", {})
        cached_port = profiles.get(profile_name)
        force_ensure = self._is_profile_failed(profile_name)
        if cached_port and not force_ensure:
            internal_port = cached_port
            was_cold = False
        else:
            controller_port = await self._call_controller_ensure_profile(
                agent_id=agent_id,
                engine_instance_id=str(deployment.get("engine_instance_id", "")),
                user_id=user_id,
                profile_type=profile_type,
                profile_name=profile_name,
            )
            internal_port = controller_port or None
            was_cold = True  # 调了 controller ensure = profile 冷启动
            if controller_port:
                # 回填 port_map 使后续路由直接命中
                profiles[profile_name] = controller_port
                port_map["profiles"] = profiles
                deployment["internal_port_map"] = port_map
            # ensure 已调（成功或失败）→ 清除失败标记，下条消息恢复 fast path
            self._failed_profiles.pop(profile_name, None)

        # 7. 使用 internal_port

        # 8. 构造 upstream URL
        engine_url = self._build_engine_url(deployment.get("pod_name", ""))

        # 8b. 拉取当前用户身份（非 PII 摘要，供 ephemeral system 注入）。
        # best-effort：失败/开关关闭 → None（不注入，不阻断路由）。随 target 入 60s 缓存。
        user_context = await self._fetch_user_context(profile_name)

        target = ResolvedTarget(
            profile_name=profile_name,
            profile_type=profile_type,
            deployment_id=str(deployment["id"]),
            engine_instance_id=str(deployment.get("engine_instance_id", "")),
            resolved_user_id=user_id,
            pod_name=deployment.get("pod_name", ""),
            engine_url=engine_url,
            internal_port=internal_port,
            scope_type=scope_type,
            scope_target_id=scope_target_id,
            was_cold=was_cold,
            user_context=user_context,
        )

        # 写入缓存
        self._cache[cache_key] = (target, time.time() + self._cache_ttl)
        # 同步写 user_context 缓存（按 profile_name，供 dispatcher 注入点查）
        self._user_context_cache[profile_name] = (user_context, time.time() + self._cache_ttl)
        return target

    def get_user_context(self, profile_name: str) -> dict | None:
        """按 profile_name 查缓存的用户身份（供 dispatcher 注入点用）。

        与 _cache 同 TTL。命中返回 user_context（可能为 None=拉取失败/开关关闭）；
        过期返回 None。不触发任何 HTTP/DB——纯内存查。
        """
        entry = self._user_context_cache.get(profile_name)
        if entry and time.time() < entry[1]:
            return entry[0]
        return None

    async def check_access(
        self, user_id: str, agent_id: str, channel_type: str = "http", is_admin: bool | None = None
    ) -> None:
        """轻量权限闸门：校验 IM 映射 + 访问权限 + channel 存在性。

        仅做只读校验，**不触发** Controller ensure / `_ensure_profile` /
        deployment 查找等副作用，供 dispatcher 在启动引擎前调用。

        Raises:
            NotBound: IM 用户无 im_user_bindings 记录
            AccessDenied: 已映射但无权访问该 agent
            ProfileNotFound: agent 不存在/未发布/无匹配 channel
        """
        cache_key = f"{user_id}:{agent_id}:{channel_type}"

        # 负缓存命中 → 直接重抛对应异常类型
        neg = self._neg_cache.get(cache_key)
        if neg:
            exc_type, expires = neg
            if time.time() < expires:
                raise exc_type(f"cached denial for {cache_key}")
            del self._neg_cache[cache_key]

        # 0. IM 用户 ID 映射
        if channel_type != "http":
            resolved = await self._resolve_im_user(user_id, channel_type)
            if not resolved:
                self._cache_neg(cache_key, NotBound)
                raise NotBound(
                    f"IM user {user_id[:12]} not bound on channel {channel_type}"
                )
            user_id = resolved

        # 1. agent 存在且已发布
        agent = await self._get_agent(agent_id)
        if not agent:
            self._cache_neg(cache_key, ProfileNotFound)
            raise ProfileNotFound(f"Agent {agent_id[:8]} not found")

        # 2. 访问权限
        if not await self._check_access(user_id, agent, is_admin):
            self._cache_neg(cache_key, AccessDenied)
            raise AccessDenied(
                f"User {user_id[:8]} has no access to agent {agent_id[:8]}"
            )

        # 3. channel 存在性（只检查存在，不按 scope 匹配）
        channel = await self._match_channel(agent_id, channel_type)
        if not channel:
            self._cache_neg(cache_key, ProfileNotFound)
            raise ProfileNotFound(
                f"No channel found for agent={agent_id[:8]} type={channel_type}"
            )

    def _cache_neg(self, key: str, exc_type: type) -> None:
        """写入负缓存（拒绝结果）"""
        self._neg_cache[key] = (exc_type, time.time() + self._neg_cache_ttl)

    def invalidate(self, agent_id: str, user_id: str, profile_name: str | None = None) -> None:
        """失效某 (agent,user) 的所有 resolve 缓存（含跨渠道）。

        upstream 返回 502/503 时由 proxy 调用：说明缓存指向的 profile 端口可能已死
        （pod 重启 / gateway 进程挂）。除清内存缓存外，若提供 profile_name 还将其
        加入 _failed_profiles（30s TTL），强制下条消息重新调 controller ensure
        健康探测——否则 port_map 中 cached_port 仍命中，死 gateway 永不重启。
        """
        prefix = f"{user_id}:{agent_id}:"
        for k in [k for k in self._cache if k.startswith(prefix)]:
            self._cache.pop(k, None)
            logger.info("Invalidated resolve cache for %s (upstream 502)", k)
        if profile_name:
            self._failed_profiles[profile_name] = time.time() + self._failed_ttl
            logger.info("Marked profile %s as failed (force re-ensure on next resolve)",
                        profile_name[:16])

    def _is_profile_failed(self, profile_name: str) -> bool:
        """检查 profile 是否在最近失败集合中（含惰性过期清理）。"""
        expiry = self._failed_profiles.get(profile_name)
        if expiry is None:
            return False
        if time.time() >= expiry:
            self._failed_profiles.pop(profile_name, None)
            return False
        return True

    async def resolve_browser_target(
        self, user_id: str, agent_id: str, is_admin: bool | None = None,
    ) -> tuple[str, str, str]:
        """解析 VNC 接管目标，返回 (profile_name, browser_pod_name, vnc_pw)。

        复用 chat 路由的鉴权（_check_access）+ profile_name 构造（http/INDEPENDENT/USER scope，
        与 chat 一致），但不触发 controller ensure——VNC 仅连已存在的 browser Pod。
        browser Pod 由 manager 在 profile 创建时按 runtime_config.browser_sandbox 拉起，pod_name +
        vnc_pw 存 internal_port_map["browsers"][profile_name]（gateway 架构约束：不调 k8s API，
        经 DB 取）。

        无 browser Pod（沙箱未启用 / Pod 未建 / 用户无权）→ 抛 ProfileNotFound/AccessDenied。
        """
        agent = await self._get_agent(agent_id)
        if not agent:
            raise ProfileNotFound(f"Agent {agent_id[:8]} not found")
        if not await self._check_access(user_id, agent, is_admin):
            raise AccessDenied(f"User {user_id[:8]} has no access to agent {agent_id[:8]}")
        # http 渠道 INDEPENDENT profile_name（与 chat 路由 _derive_scope + _build_profile_name 一致）
        uid = str(user_id)
        profile_name = self._build_profile_name(agent_id, "USER", uid, uid)
        deployment = await self._get_deployment(agent_id)
        if not deployment:
            raise ProfileNotFound(f"No deployment for agent {agent_id[:8]}")
        browsers = (deployment.get("internal_port_map") or {}).get("browsers") or {}
        info = browsers.get(profile_name)
        if not isinstance(info, dict) or not info.get("pod"):
            raise ProfileNotFound(
                f"No browser pod for profile {profile_name[:16]} "
                "(sandbox not enabled or pod not created)"
            )
        return profile_name, info["pod"], info.get("vnc_pw") or ""

    # ── Private helpers ─────────────────────────────────────

    def _build_profile_name(
        self, agent_id: str, scope_type: str,
        scope_target_id: str | None, user_id: str,
    ) -> str:
        """构造 Hermes Profile 名称"""
        short_agent = agent_id.replace("-", "")[:8]
        shash = self._scope_hash(scope_type, scope_target_id)
        short_user = user_id.replace("-", "")[:8]
        return f"{short_agent}-{shash}-{short_user}"

    @staticmethod
    def _scope_hash(scope_type: str, scope_target_id: str | None) -> str:
        raw = f"{scope_type}:{scope_target_id or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:6]

    def _build_engine_url(self, pod_name: str) -> str:
        """根据 pod_name 构造 K8s Service DNS"""
        if not pod_name:
            return ""
        return f"http://{pod_name}.{settings.k8s_namespace}.svc.cluster.local:8642"

    async def _resolve_im_user(self, im_user_id: str, channel_type: str) -> str | None:
        """查询 im_user_bindings 表，将 IM 平台用户 ID 转为内部 UUID。"""
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT user_id FROM im_user_bindings "
                    "WHERE channel_type = :ct AND im_user_id = :uid"
                ),
                {"ct": channel_type, "uid": im_user_id},
            )
            row = result.mappings().first()
            if row:
                return str(row["user_id"])
        return None

    async def _get_agent(self, agent_id: str) -> dict | None:
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT id, group_id, resource_pool_id FROM agent_instances "
                    "WHERE id = :aid AND status = 'PUBLISHED'"
                ),
                {"aid": agent_id},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _is_platform_admin(self, user_id: str) -> bool:
        """查 DB 用户角色，判断是否平台管理员（系统管理员/平台管理员角色）。

        IM 渠道无 JWT，dispatcher 不传 is_admin 时由此补判。
        """
        try:
            async with async_session() as db:
                result = await db.execute(
                    text(
                        "SELECT r.name FROM users u "
                        "JOIN user_roles ur ON ur.user_id = u.id "
                        "JOIN roles r ON r.id = ur.role_id "
                        "WHERE u.id = :uid"
                    ),
                    {"uid": user_id},
                )
                names = {row[0] for row in result.all()}
        except Exception:
            return False
        return "系统管理员" in names or "平台管理员" in names

    async def _check_access(self, user_id: str, agent: dict, is_admin: bool | None = None) -> bool:
        """组隔离：平台管理员跨组；否则用户必须是实例所属组的成员才可访问。

        is_admin=None（dispatcher IM 渠道无 JWT）→ 查 DB 补判；
        is_admin=True/False（proxy 已从 JWT 判）→ 直接用，不查 DB。
        """
        if is_admin is None:
            is_admin = await self._is_platform_admin(user_id)
        if is_admin:
            return True
        group_id = agent.get("group_id")
        if not group_id:
            return False
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT 1 FROM user_group_members "
                    "WHERE user_id = :uid AND group_id = :gid"
                ),
                {"uid": user_id, "gid": str(group_id)},
            )
            return result.scalar() is not None

    async def _derive_scope(
        self, agent: dict, user_id: str, channel: dict | None = None,
    ) -> tuple[str, str | None, str]:
        """派生 (scope_type, scope_target_id, profile_type)。

        组共享 Profile（SHARED）已下线，恒为 USER 级 INDEPENDENT（profile_name 含 user
        hash）。channel.profile_type 不再生效——历史 SHARED 渠道由 cleanup 脚本归一。
        保留 channel 参数仅为调用方签名稳定。
        """
        return "USER", str(user_id), "INDEPENDENT"

    async def _match_channel(
        self, agent_id: str, channel_type: str,
    ) -> dict | None:
        """检查 Channel 是否存在（只检查存在性，不按 scope 匹配）。

        scope 控制已移至 Agent 的 access_scope（_derive_scope），
        渠道只是传输层，不应有独立权限控制。
        """
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT id, scope_type, scope_target_id, profile_type "
                    "FROM agent_instance_channels "
                    "WHERE instance_id = :aid AND channel_type = :ct AND enabled = true "
                    "LIMIT 1"
                ),
                {"aid": agent_id, "ct": channel_type},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _get_deployment(
        self, agent_id: str,
    ) -> dict | None:
        """查找 Deployment（按 agent_id，UNIQUE 约束保证 1:1）。

        不再按 scope_type/scope_target_id 匹配——部署 scope 可能在
        access_scope 变更后过时，按 agent_id 查找避免不一致。
        """
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT id, pod_name, status, engine_url, internal_port_map, "
                    "resource_pool_id AS engine_instance_id "
                    "FROM agent_deployments "
                    "WHERE instance_id = :aid"
                ),
                {"aid": agent_id},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _get_deployment_pod_name(self, agent_id: str) -> str | None:
        """快速查询 agent_deployments.pod_name（用于缓存失效检测）。"""
        try:
            async with async_session() as db:
                result = await db.execute(
                    text("SELECT pod_name FROM agent_deployments WHERE instance_id = :aid"),
                    {"aid": agent_id},
                )
                row = result.mappings().first()
                return row.get("pod_name") if row else None
        except Exception:
            return None

    async def _ensure_profile(
        self, agent_id: str, deployment_id: str, profile_name: str,
        profile_type: str, user_id: str, scope_target_id: str | None,
        group_id: str | None = None,
    ):
        """确保 AgentProfile 记录存在（upsert）

        注意：IM 渠道的用户 ID（如企业微信的 FromUserName）可能不是 UUID，
        而 agent_profiles.user_id 是 UUID 类型。如果插入失败（非致命），
        不影响消息路由，只是缺少一条 DB 记录。
        """
        try:
            async with async_session() as db:
                await db.execute(
                    text(
                        "INSERT INTO agent_profiles (id, instance_id, resource_pool_id, deployment_id, "
                        "profile_name, profile_type, user_id, group_id, hermes_home, is_active) "
                        "VALUES (gen_random_uuid(), :aid, "
                        "(SELECT resource_pool_id FROM agent_deployments WHERE id = :did), "
                        ":did, :pn, :pt, :uid, :gid, :home, true) "
                        "ON CONFLICT (instance_id, resource_pool_id, user_id) DO NOTHING"
                    ),
                    {
                        "aid": agent_id,
                        "did": deployment_id,
                        "pn": profile_name,
                        "pt": profile_type,
                        "uid": user_id,
                        # group_id 始终填 instance.group_id（agent_profiles.group_id NOT NULL）；
                        # profile_type 恒为 INDEPENDENT，不影响 group_id
                        "gid": str(group_id) if group_id else None,
                        "home": f"/opt/data/profiles/{profile_name}",
                    },
                )
                await db.commit()
        except Exception as e:
            logger.warning("Failed to ensure profile record for %s: %s (non-fatal)",
                          profile_name[:16], e)

    async def _call_controller_ensure_profile(
        self, agent_id: str, engine_instance_id: str,
        user_id: str, profile_type: str, profile_name: str,
    ) -> int | None:
        """调 Controller 在 Pod 上创建 Hermes Profile

        仅在 internal_port 不存在时调用（幂等，Controller 端会去重）。
        超时或失败时降级返回 None，后续消息通过 engine base profile 路由。
        """
        from app.settings import settings
        url = f"{settings.controller_url}/api/controller/profiles/ensure"
        payload = {
            "agent_id": agent_id,
            "engine_instance_id": engine_instance_id,
            "user_id": user_id if user_id else None,
            "group_id": None,
            "profile_type": profile_type,
            "profile_name": profile_name,
        }
        try:
            # 20s：_do_create_profile 串行多次 k8s exec（port alloc/clone/heal/launch/nginx），
            # 10s 在慢节点上会超时半途中断（profile 目录已建但 launch 没跑→root 属主半成品）。
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    port = data.get("port")
                    if port:
                        logger.info(
                            "Controller ensured profile %s on port %d",
                            profile_name[:16], port,
                        )
                        return port
                    logger.warning(
                        "Controller returned no port for profile %s", profile_name[:16],
                    )
                elif resp.status_code == 404:
                    # manager 返回 404 = user_id 不存在（用户已删）。
                    # 硬拒绝（AccessDenied → 403），**不回退 base profile**，
                    # 避免已删用户持旧 token 继续用智能体。
                    raise AccessDenied(
                        f"User {user_id[:8]} not found for profile {profile_name[:16]} (deleted?)"
                    )
                else:
                    logger.warning(
                        "Controller /profiles/ensure returned %d for %s: %s",
                        resp.status_code, profile_name[:16], resp.text[:100],
                    )
        except AccessDenied:
            # 用户已删（404）→ 必须上抛硬拒绝，不能被下面的 non-fatal 吞掉回退 base
            raise
        except httpx.TimeoutException:
            logger.warning("Controller timeout for profile %s (will retry next message)", profile_name[:16])
        except httpx.ConnectError:
            logger.warning("Controller unavailable for profile %s (will retry next message)", profile_name[:16])
        except Exception as e:
            logger.warning("Controller ensure profile failed for %s: %s (non-fatal)", profile_name[:16], e)
        return None

    async def _fetch_user_context(self, profile_name: str) -> dict | None:
        """拉取当前用户身份（非 PII 摘要），供 ephemeral system 注入。

        调 manager ``GET /api/controller/profiles/{profile_name}/user-context``，
        返回 ``{fields, business}``（serialize_user_context 输出）。best-effort：
        开关关闭 / 失败 / 非 200 → None（不注入，不阻断路由）。
        """
        from app.settings import settings

        if not settings.inject_user_context:
            return None
        url = f"{settings.controller_url}/api/controller/profiles/{profile_name}/user-context"
        headers = {}
        if settings.internal_token:
            headers["X-Internal-Token"] = settings.internal_token
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                logger.debug(
                    "user-context for %s returned %d: %s",
                    profile_name[:16], resp.status_code, resp.text[:80],
                )
        except Exception as e:
            logger.debug("user-context fetch failed for %s: %s (non-fatal)", profile_name[:16], e)
        return None


# Singleton
profile_resolver = ProfileResolver()
