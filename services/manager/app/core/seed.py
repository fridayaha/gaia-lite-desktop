"""角色/权限幂等 seed。

不新增表/列，仅在现有 roles/permissions 表中写入权限点与角色绑定，供角色管理 UI 展示/分配。
**不新增 endpoint 级强制校验**（V3 router 仅 get_current_user；权限种子用于 UI 可视化分配）。

权限分组（resource_type）：
  - system            系统管理（用户/角色/用户组/引擎配置）
  - litellm           模型网关（model/key）
  - agent_definition  智能体定义层（元数据/版本/技能）
  - resource_pool     运行资源池
  - agent_instance    智能体实例层（运行时 + 概览/监控/记忆/渠道）
  - monitoring        监控中心（链路追踪/资源/服务健康/用量/调用/操作记录/服务日志/告警）
  - hub               能力中心（查看/创建版本/提交审核/审核/发布）

预置角色（按角色定义刷新默认权限）：
  - 系统管理员：拥有所有权限（系统最高权限角色）
  - 平台管理员：拥有除系统管理（user/role/user_group/engine_config）外的全部权限
  - 组管理员：管理用户组相关权限（user_group:manage + user:manage）
  - 运维人员：监控中心全部（含告警规则管理）+ 智能体实例管理全部 CRUD
  - 终端用户：admin 后台无权限（占位角色，终端用户走 enduser-portal 独立前端）

Bootstrap：若系统中尚无任何用户拥有「系统管理员」角色，则把该角色授予第一个用户
（按 created_at），避免升级后无人可管理系统。
"""
from __future__ import annotations

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User, Role, Permission, AlertRule, user_roles, role_permissions
from pkg.common.database import async_session

# (code, name, description, resource_type)
_PERMISSIONS = [
    # ── 模型网关 LiteLLM ──
    ("litellm:model:manage", "模型组管理", "管理全局上游模型供应商/模型组", "litellm"),
    ("litellm:key:manage", "API Key 管理", "管理所属用户组的 LiteLLM 虚拟 Key", "litellm"),
    # ── V3 智能体定义层 ──
    ("agent_definition:view", "查看定义", "查看智能体定义与版本", "agent_definition"),
    ("agent_definition:create", "创建定义", "创建智能体定义", "agent_definition"),
    ("agent_definition:update", "编辑定义", "编辑智能体定义配置", "agent_definition"),
    ("agent_definition:delete", "删除定义", "删除智能体定义", "agent_definition"),
    ("agent_definition:publish", "发布版本", "发布定义版本快照", "agent_definition"),
    ("agent_definition:manage_skills", "技能管理", "安装/开关/排序/卸载技能", "agent_definition"),
    # ── V3 资源池 ──
    ("resource_pool:view", "查看资源池", "查看运行资源池", "resource_pool"),
    ("resource_pool:create", "创建资源池", "创建运行资源池", "resource_pool"),
    ("resource_pool:update", "编辑资源池", "编辑运行资源池规格", "resource_pool"),
    ("resource_pool:delete", "删除资源池", "删除运行资源池", "resource_pool"),
    ("resource_pool:clone", "克隆资源池", "克隆运行资源池", "resource_pool"),
    # ── V3 智能体实例层 ──
    ("agent_instance:view", "查看实例", "查看智能体实例", "agent_instance"),
    ("agent_instance:create", "创建实例", "创建智能体实例", "agent_instance"),
    ("agent_instance:update", "编辑实例", "编辑智能体实例配置", "agent_instance"),
    ("agent_instance:delete", "删除实例", "删除智能体实例", "agent_instance"),
    ("agent_instance:clone", "克隆实例", "克隆智能体实例", "agent_instance"),
    ("agent_instance:publish", "上线实例", "上线实例（对终端可见）", "agent_instance"),
    ("agent_instance:offline", "停用实例", "停用实例", "agent_instance"),
    ("agent_instance:deploy", "部署实例", "部署实例引擎", "agent_instance"),
    ("agent_instance:suspend", "挂起实例", "挂起实例（scale 0）", "agent_instance"),
    ("agent_instance:resume", "恢复实例", "恢复挂起实例", "agent_instance"),
    ("agent_instance:restart", "重启实例", "滚动重启实例", "agent_instance"),
    ("agent_instance:destroy", "销毁实例", "销毁实例引擎并归档", "agent_instance"),
    ("agent_instance:switch_version", "切换版本", "切换实例绑定版本", "agent_instance"),
    ("agent_instance:manage_channel", "渠道管理", "管理实例 IM 渠道", "agent_instance"),
    ("agent_instance:manage_api_keys", "实例 API Key 管理", "管理实例 OpenAI 兼容 API Key", "agent_instance"),
    ("agent_instance:view_overview", "查看概览", "查看实例概览数据", "agent_instance"),
    ("agent_instance:view_metrics", "查看监控", "查看实例监控指标", "agent_instance"),
    ("agent_instance:view_memory", "查看记忆", "查看实例记忆", "agent_instance"),
    ("agent_instance:view_logs", "查看日志", "查看实例 Pod 日志", "agent_instance"),
    # ── 系统管理（用户/角色/用户组/引擎配置）──
    ("user:manage", "用户管理", "管理系统用户账号与角色绑定", "system"),
    ("role:manage", "角色管理", "管理系统角色与权限分配", "system"),
    ("user_group:manage", "用户组管理", "管理用户组与成员", "system"),
    ("engine_config:manage", "引擎配置管理", "管理系统级 Dify 引擎配置（平台/外接模式 + 管理员凭据）", "system"),
    # ── 监控中心 ──
    ("monitoring:trace:view", "链路追踪查看", "查看 Langfuse 链路追踪与 trace 详情", "monitoring"),
    ("monitoring:resource:view", "资源监控查看", "查看集群/节点/Pod 资源使用率", "monitoring"),
    ("monitoring:service_health:view", "服务健康查看", "查看核心服务状态与 p95/可用率", "monitoring"),
    ("monitoring:usage:view", "用量分析查看", "查看 token 用量与成本趋势", "monitoring"),
    ("monitoring:call_analysis:view", "调用分析查看", "查看调用成功率与延迟分布", "monitoring"),
    ("monitoring:operation_log:view", "操作记录查看", "查看监控中心操作记录", "monitoring"),
    ("monitoring:log_search:view", "服务日志查看", "查看服务日志检索", "monitoring"),
    ("monitoring:alert:view", "异常告警查看", "查看告警事件列表与统计", "monitoring"),
    ("monitoring:alert:manage", "告警规则管理", "管理告警规则阈值与启停、告警通道", "monitoring"),
    # ── 能力中心 Hub ──
    ("hub:view", "查看能力", "查看 Hub 能力项与版本", "hub"),
    ("hub:create_version", "创建版本", "为能力项创建新版本", "hub"),
    ("hub:submit_review", "提交审核", "提交能力版本进入审核流程", "hub"),
    ("hub:review", "审核版本", "审核通过或驳回能力版本", "hub"),
    ("hub:publish", "发布能力", "发布审核通过的能力版本", "hub"),
    # ── 社区（技术文章）──
    ("community:post", "发布文章", "发布与编辑自己的社区文章", "community"),
    ("community:audit", "审核文章", "审核社区文章发布", "community"),
    ("community:manage", "管理全部文章", "删除/下架任意文章", "community"),
]

_PLATFORM_ADMIN_ROLE = "平台管理员"
_SYS_ADMIN_ROLE = "系统管理员"
_GROUP_ADMIN_ROLE = "组管理员"
_OPERATOR_ROLE = "运维人员"
_END_USER_ROLE = "终端用户"

# 角色描述（与定义保持一致，seed 时同步刷新已存在角色的 description）
_ROLE_DESCRIPTIONS = {
    _SYS_ADMIN_ROLE: "系统最高权限角色，拥有系统全部权限（含用户/角色/用户组、引擎配置、LiteLLM、Hub、监控、智能体定义与实例全生命周期）",
    _PLATFORM_ADMIN_ROLE: "平台管理员，拥有除系统管理（用户/角色/用户组/引擎配置）外的全部权限，负责智能体业务运营",
    _GROUP_ADMIN_ROLE: "用户组管理员，管理用户组与组内成员（user_group:manage + user:manage）",
    _OPERATOR_ROLE: "运维工程师，负责监控中心全部（含告警规则管理）+ 智能体实例管理全部 CRUD（部署/挂起/恢复/重启/销毁/创建/删除/编辑等）",
    _END_USER_ROLE: "终端用户占位角色，admin 后台无权限（终端用户走 enduser-portal 独立前端，不走 admin 权限体系）",
}

# 系统管理类权限（user/role/user_group/engine_config）——平台管理员排除
_SYS_MANAGEMENT_CODES = {
    "user:manage", "role:manage", "user_group:manage", "engine_config:manage",
}

# 系统管理员：全部权限（系统最高）
# role_specs 中直接用 all_codes

# 平台管理员：全部权限 - 系统管理类
_PLATFORM_ADMIN_CODES = None  # 在 seed_roles 中用 all_codes - _SYS_MANAGEMENT_CODES 动态计算

# 组管理员：管理用户组相关权限
_GROUP_ADMIN_CODES = {
    "user_group:manage", "user:manage",
}

# 运维人员：监控中心全部（含告警规则管理）+ 智能体实例管理全部 CRUD
_OPERATOR_CODES = {
    # monitoring 全部 9 项
    "monitoring:trace:view", "monitoring:resource:view", "monitoring:service_health:view",
    "monitoring:usage:view", "monitoring:call_analysis:view",
    "monitoring:operation_log:view", "monitoring:log_search:view",
    "monitoring:alert:view", "monitoring:alert:manage",
    # agent_instance 全部 19 项
    "agent_instance:view", "agent_instance:create", "agent_instance:update",
    "agent_instance:delete", "agent_instance:clone", "agent_instance:publish",
    "agent_instance:offline", "agent_instance:deploy", "agent_instance:suspend",
    "agent_instance:resume", "agent_instance:restart", "agent_instance:destroy",
    "agent_instance:switch_version", "agent_instance:manage_channel",
    "agent_instance:manage_api_keys",
    "agent_instance:view_overview", "agent_instance:view_metrics",
    "agent_instance:view_memory", "agent_instance:view_logs",
    # community 发布权限（运营可发文章，审核需平台管理员）
    "community:post",
}

# 终端用户：admin 后台无权限（占位角色）
_END_USER_CODES: set[str] = set()


async def seed_roles(db: AsyncSession | None = None) -> None:
    own_session = db is None
    if own_session:
        db = async_session()
    try:
        # 1. 幂等写入权限（含已存在权限的 name/description/resource_type 同步刷新）
        perm_map: dict[str, Permission] = {}
        for code, name, desc, rtype in _PERMISSIONS:
            res = await db.execute(select(Permission).where(Permission.code == code))
            perm = res.scalar_one_or_none()
            if perm is None:
                perm = Permission(name=name, code=code, description=desc, resource_type=rtype)
                db.add(perm)
                await db.flush()
            else:
                # 同步刷新 name/description/resource_type（V1→V3 迁移或权限归类调整）
                if perm.name != name or perm.description != desc or perm.resource_type != rtype:
                    perm.name = name
                    perm.description = desc
                    perm.resource_type = rtype
            perm_map[code] = perm

        # 2. 幂等写入角色并绑定权限
        all_codes = [c for c, *_ in _PERMISSIONS]
        role_specs = {
            _SYS_ADMIN_ROLE: all_codes,  # 系统管理员：全部权限
            _PLATFORM_ADMIN_ROLE: [c for c in all_codes if c not in _SYS_MANAGEMENT_CODES],
            _GROUP_ADMIN_ROLE: [c for c in all_codes if c in _GROUP_ADMIN_CODES],
            _OPERATOR_ROLE: [c for c in all_codes if c in _OPERATOR_CODES],
            _END_USER_ROLE: [c for c in all_codes if c in _END_USER_CODES],
        }
        for role_name, codes in role_specs.items():
            res = await db.execute(
                select(Role).options(selectinload(Role.permissions)).where(Role.name == role_name)
            )
            role = res.scalar_one_or_none()
            wanted = {perm_map[c].id for c in codes}
            if role is None:
                role = Role(
                    name=role_name,
                    description=_ROLE_DESCRIPTIONS.get(role_name, role_name),
                )
                db.add(role)
                await db.flush()
                existing: set = set()  # 新建角色无权限
            else:
                existing = {p.id for p in role.permissions}  # 已通过 selectinload 加载
                # 同步刷新 description（V1 遗留描述可能与新定义不符）
                expected_desc = _ROLE_DESCRIPTIONS.get(role_name, role_name)
                if role.description != expected_desc:
                    role.description = expected_desc
            if existing != wanted:
                # 直接操作 role_permissions 关联表，避免 ORM relationship set 触发懒加载（async greenlet）
                await db.execute(delete(role_permissions).where(role_permissions.c.role_id == role.id))
                if wanted:
                    await db.execute(
                        role_permissions.insert(),
                        [{"role_id": role.id, "permission_id": pid} for pid in wanted],
                    )

        # 2.5 清理遗留权限点：删除不在 _PERMISSIONS 中且无任何角色引用的权限
        # （V1 遗留的 agent:create/role:create/system:edit/menu:* 等，随 V3 seed 一并清理）
        valid_codes = set(all_codes)
        res = await db.execute(select(Permission))
        for perm in res.scalars().all():
            if perm.code in valid_codes:
                continue
            # 检查是否被任何角色引用
            ref_res = await db.execute(
                select(role_permissions.c.permission_id).where(
                    role_permissions.c.permission_id == perm.id
                ).limit(1)
            )
            if ref_res.first() is not None:
                continue  # 仍被引用（用户自定义角色），保留
            await db.execute(delete(Permission).where(Permission.id == perm.id))

        await db.commit()

        # 3. Bootstrap：无系统管理员时，授予第一个用户（系统最高权限角色）
        res = await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .join(user_roles, user_roles.c.user_id == User.id)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(Role.name == _SYS_ADMIN_ROLE)
            .limit(1)
        )
        if res.scalar_one_or_none() is not None:
            return  # 已有系统管理员

        res = await db.execute(
            select(User).options(selectinload(User.roles)).order_by(User.created_at).limit(1)
        )
        first_user = res.scalar_one_or_none()
        if first_user is None:
            return  # 暂无用户，等首用户创建后下次启动再 bootstrap

        res = await db.execute(select(Role).where(Role.name == _SYS_ADMIN_ROLE))
        admin_role = res.scalar_one_or_none()
        if admin_role is None:
            return
        if admin_role not in (first_user.roles or []):
            first_user.roles = list(first_user.roles or []) + [admin_role]
            await db.commit()
    finally:
        if own_session:
            await db.close()


# 向后兼容旧调用名
seed_litellm_roles = seed_roles


# ── AlertRule 默认规则 seed ────────────────────────────────
# 幂等：按 rule_type 去重，已存在的 rule_type 不重复插入；
# 新 rule_type（升级版本后新加的）会自动 seed，不影响用户已自定义的规则。
# 5 大类 16 子规则（与 ALERT_CATEGORY_RULE_TYPES 一致）。
_ALERT_RULE_SEEDS = [
    # ── tracing 链路追踪 ──
    {"name": "错误请求告警", "category": "tracing", "rule_type": "error_trace",
     "threshold": None, "enabled": True, "severity": "critical",
     "description": "Langfuse trace 状态为 error 时触发告警"},
    {"name": "高延迟告警", "category": "tracing", "rule_type": "high_latency",
     "threshold": 8000, "enabled": True, "severity": "warning",
     "description": "请求延迟超过阈值（毫秒）时触发告警"},
    {"name": "高 Token 告警", "category": "tracing", "rule_type": "high_tokens",
     "threshold": 30000, "enabled": False, "severity": "warning",
     "description": "请求 token 总数超过阈值时触发告警"},
    # ── resource 资源监控 ──
    {"name": "集群 CPU 高", "category": "resource", "rule_type": "high_cpu",
     "threshold": 85, "enabled": True, "severity": "warning",
     "description": "集群 CPU 使用率超过阈值（%）时触发告警"},
    {"name": "集群内存高", "category": "resource", "rule_type": "high_memory",
     "threshold": 90, "enabled": True, "severity": "warning",
     "description": "集群内存使用率超过阈值（%）时触发告警"},
    {"name": "节点磁盘高", "category": "resource", "rule_type": "high_disk",
     "threshold": 85, "enabled": True, "severity": "warning",
     "description": "任一节点磁盘使用率超过阈值（%）时触发告警"},
    {"name": "Pod 重启告警", "category": "resource", "rule_type": "pod_restart",
     "threshold": 5, "enabled": True, "severity": "warning",
     "description": "Pod 累计重启次数超过阈值时触发告警"},
    # ── service_health 服务健康 ──
    {"name": "服务下线告警", "category": "service_health", "rule_type": "service_down",
     "threshold": None, "enabled": True, "severity": "critical",
     "description": "任一核心服务状态为 down 时触发告警"},
    {"name": "服务 p95 延迟高", "category": "service_health", "rule_type": "high_p95_latency",
     "threshold": 2000, "enabled": True, "severity": "warning",
     "description": "任一服务 p95 延迟超过阈值（毫秒）时触发告警"},
    {"name": "服务可用性低", "category": "service_health", "rule_type": "low_uptime",
     "threshold": 99, "enabled": True, "severity": "warning",
     "description": "任一服务可用率低于阈值（%）时触发告警（反向比较）"},
    # ── usage 用量分析（默认关闭：成本阈值不同租户差异大，由用户按预算开启） ──
    {"name": "日 Token 告警", "category": "usage", "rule_type": "high_daily_tokens",
     "threshold": 1000000, "enabled": False, "severity": "warning",
     "description": "当日 token 总数超过阈值时触发告警"},
    {"name": "月费用告警", "category": "usage", "rule_type": "high_monthly_cost",
     "threshold": 100, "enabled": False, "severity": "warning",
     "description": "当月费用总和超过阈值（USD）时触发告警"},
    {"name": "智能体 Token 告警", "category": "usage", "rule_type": "high_agent_tokens",
     "threshold": 200000, "enabled": False, "severity": "warning",
     "description": "单个智能体 token 用量超过阈值时触发告警"},
    # ── call_analysis 调用分析 ──
    {"name": "成功率低告警", "category": "call_analysis", "rule_type": "low_success_rate",
     "threshold": 95, "enabled": True, "severity": "warning",
     "description": "整体调用成功率低于阈值（%）时触发告警（反向比较）"},
    {"name": "调用 p95 高告警", "category": "call_analysis", "rule_type": "high_p95_call_latency",
     "threshold": 8000, "enabled": True, "severity": "warning",
     "description": "调用 p95 延迟超过阈值（毫秒）时触发告警"},
    {"name": "均 Token 告警", "category": "call_analysis", "rule_type": "high_avg_tokens_per_request",
     "threshold": 20000, "enabled": True, "severity": "warning",
     "description": "平均每请求 token 数超过阈值时触发告警"},
]


async def seed_alert_rules(db: AsyncSession | None = None) -> None:
    """幂等插入 16 条默认告警规则。按 rule_type 去重，已存在的不覆盖；
    新 rule_type（升级后新增的）自动补 seed，不影响用户已自定义的规则。
    """
    own_session = db is None
    if own_session:
        db = async_session()
    try:
        existing = await db.execute(select(AlertRule.rule_type))
        existing_types = {r[0] for r in existing.all()}
        for spec in _ALERT_RULE_SEEDS:
            if spec["rule_type"] in existing_types:
                continue
            db.add(AlertRule(**spec))
        await db.commit()
    finally:
        if own_session:
            await db.close()
