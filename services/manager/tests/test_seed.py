"""seed_roles 真 DB 集成测试 — 断言 V3 三类资源权限点写入 + 角色绑定。

seed 幂等，直接对 dev DB 运行；不清理（权限/角色为系统持久数据，重复执行无副作用）。
"""
import pytest

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import selectinload

from pkg.common.config import settings
from app.models import Permission, Role
from app.core.seed import (
    seed_roles,
    _PERMISSIONS,
    _PLATFORM_ADMIN_ROLE,
    _SYS_ADMIN_ROLE,
    _GROUP_ADMIN_ROLE,
    _OPERATOR_ROLE,
    _END_USER_ROLE,
    _SYS_MANAGEMENT_CODES,
    _GROUP_ADMIN_CODES,
    _OPERATOR_CODES,
    _END_USER_CODES,
    _ROLE_DESCRIPTIONS,
)


@pytest.fixture
async def db():
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    yield session
    await session.close()
    await engine.dispose()


async def test_seed_creates_v3_permissions(db):
    await seed_roles(db)
    res = await db.execute(select(Permission))
    perms = {p.code: p for p in res.scalars().all()}

    # 全部 _PERMISSIONS 权限点存在，resource_type 正确
    for code, name, desc, rtype in _PERMISSIONS:
        assert code in perms, f"missing permission {code}"
        assert perms[code].resource_type == rtype
        assert perms[code].name == name

    # V3 三类资源权限点齐全
    def codes_with_prefix(prefix: str) -> set[str]:
        return {c for c, *_ in _PERMISSIONS if c.startswith(prefix)}

    for prefix, rtype in [
        ("agent_definition:", "agent_definition"),
        ("resource_pool:", "resource_pool"),
        ("agent_instance:", "agent_instance"),
    ]:
        v3 = [p for p in perms.values() if p.resource_type == rtype]
        assert {p.code for p in v3} == codes_with_prefix(prefix)


async def test_seed_sys_admin_has_all_permissions(db):
    """系统管理员：拥有全部权限（系统最高权限角色）。"""
    await seed_roles(db)
    all_codes = {c for c, *_ in _PERMISSIONS}

    res = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.name == _SYS_ADMIN_ROLE)
    )
    role = res.scalar_one()
    res = await db.execute(select(Permission).where(Permission.id.in_([p.id for p in role.permissions])))
    codes = {p.code for p in res.scalars().all()}
    assert codes == all_codes


async def test_seed_platform_admin_excludes_system_management(db):
    """平台管理员：全部权限 - 系统管理类（user/role/user_group/engine_config）。"""
    await seed_roles(db)
    all_codes = {c for c, *_ in _PERMISSIONS}
    expected = all_codes - _SYS_MANAGEMENT_CODES

    res = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.name == _PLATFORM_ADMIN_ROLE)
    )
    role = res.scalar_one()
    res = await db.execute(select(Permission).where(Permission.id.in_([p.id for p in role.permissions])))
    codes = {p.code for p in res.scalars().all()}
    assert codes == expected
    # 不含系统管理类
    for c in _SYS_MANAGEMENT_CODES:
        assert c not in codes
    # 但含其他全部权限
    for c in ["litellm:model:manage", "agent_definition:create", "agent_instance:deploy",
              "monitoring:alert:manage", "hub:publish", "resource_pool:create"]:
        assert c in codes


async def test_seed_group_admin_has_user_group_management(db):
    """组管理员：管理用户组相关权限（user_group:manage + user:manage）。"""
    await seed_roles(db)
    expected = {c for c, *_ in _PERMISSIONS if c in _GROUP_ADMIN_CODES}

    res = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.name == _GROUP_ADMIN_ROLE)
    )
    role = res.scalar_one()
    res = await db.execute(select(Permission).where(Permission.id.in_([p.id for p in role.permissions])))
    codes = {p.code for p in res.scalars().all()}
    assert codes == expected
    assert "user_group:manage" in codes
    assert "user:manage" in codes
    # 不含其他权限
    assert "agent_instance:deploy" not in codes
    assert "monitoring:alert:view" not in codes
    assert "engine_config:manage" not in codes


async def test_seed_operator_has_monitoring_and_agent_instance(db):
    """运维人员：监控全部（含告警规则管理）+ 智能体实例全部 CRUD。"""
    await seed_roles(db)
    expected = {c for c, *_ in _PERMISSIONS if c in _OPERATOR_CODES}

    res = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.name == _OPERATOR_ROLE)
    )
    role = res.scalar_one()
    res = await db.execute(select(Permission).where(Permission.id.in_([p.id for p in role.permissions])))
    codes = {p.code for p in res.scalars().all()}
    assert codes == expected
    # 监控全部（含 alert:manage）
    for c in ["monitoring:trace:view", "monitoring:alert:view", "monitoring:alert:manage"]:
        assert c in codes
    # 智能体实例全部 CRUD（含 create/delete）
    for c in ["agent_instance:view", "agent_instance:create", "agent_instance:delete",
              "agent_instance:deploy", "agent_instance:destroy", "agent_instance:restart"]:
        assert c in codes
    # 不含定义开发、Hub 审核/发布、用户管理、引擎配置
    for c in ["agent_definition:create", "hub:review", "hub:publish",
              "user:manage", "engine_config:manage", "litellm:model:manage"]:
        assert c not in codes


async def test_seed_end_user_has_no_permissions(db):
    """终端用户：admin 后台无权限（占位角色）。"""
    await seed_roles(db)
    res = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.name == _END_USER_ROLE)
    )
    role = res.scalar_one()
    codes = {p.code for p in role.permissions}
    assert codes == set(), f"end user should have no permissions, got {codes}"


async def test_seed_cleans_up_legacy_permissions(db):
    """遗留权限（不在 _PERMISSIONS 且无角色引用）应被清理。"""
    from app.models import Permission
    legacy = Permission(
        name="legacy_test_perm",
        code="legacy:test_perm",
        description="for cleanup test",
        resource_type="legacy",
    )
    db.add(legacy)
    await db.flush()

    # seed 后应被清理（因为不在 _PERMISSIONS 且无引用）
    await seed_roles(db)
    res = await db.execute(select(Permission).where(Permission.code == "legacy:test_perm"))
    assert res.scalar_one_or_none() is None


async def test_seed_preserves_referenced_legacy_permission(db):
    """被角色引用的遗留权限应保留（避免破坏用户自定义角色）。"""
    from app.models import Permission, Role, role_permissions
    legacy = Permission(
        name="legacy_referenced_perm",
        code="legacy:referenced_perm",
        description="referenced by custom role",
        resource_type="legacy",
    )
    db.add(legacy)
    await db.flush()

    custom_role = Role(name="custom_test_role", description="custom")
    db.add(custom_role)
    await db.flush()
    await db.execute(
        role_permissions.insert(),
        [{"role_id": custom_role.id, "permission_id": legacy.id}],
    )
    await db.commit()

    await seed_roles(db)
    res = await db.execute(select(Permission).where(Permission.code == "legacy:referenced_perm"))
    assert res.scalar_one_or_none() is not None

    # 清理
    await db.execute(delete(role_permissions).where(role_permissions.c.role_id == custom_role.id))
    await db.execute(delete(Role).where(Role.id == custom_role.id))
    await db.execute(delete(Permission).where(Permission.id == legacy.id))
    await db.commit()


async def test_seed_role_descriptions_synced(db):
    """预置角色的 description 应与 _ROLE_DESCRIPTIONS 保持一致（含已存在角色的刷新）。"""
    await seed_roles(db)
    for role_name, expected_desc in _ROLE_DESCRIPTIONS.items():
        res = await db.execute(select(Role).where(Role.name == role_name))
        role = res.scalar_one()
        assert role.description == expected_desc, (
            f"{role_name} description mismatch: got {role.description!r}, want {expected_desc!r}"
        )


async def test_seed_syncs_permission_resource_type(db):
    """已存在权限的 resource_type 应与 _PERMISSIONS 保持一致（V1→V3 归类调整后同步刷新）。"""
    from app.models import Permission
    await seed_roles(db)
    # 手动把一个权限的 resource_type 改成旧值
    res = await db.execute(select(Permission).where(Permission.code == "user:manage"))
    perm = res.scalar_one()
    perm.resource_type = "user"  # V1 旧值
    await db.commit()

    # seed 后应刷新回 _PERMISSIONS 里的值
    await seed_roles(db)
    await db.refresh(perm)
    expected_rtype = next(rtype for code, _name, _desc, rtype in _PERMISSIONS if code == "user:manage")
    assert perm.resource_type == expected_rtype


async def test_seed_is_idempotent(db):
    """重复执行不报错、权限数量不变。"""
    await seed_roles(db)
    res1 = await db.execute(select(Permission))
    count1 = len(res1.scalars().all())
    await seed_roles(db)
    res2 = await db.execute(select(Permission))
    count2 = len(res2.scalars().all())
    assert count1 == count2
