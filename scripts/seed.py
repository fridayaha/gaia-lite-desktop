"""Initialize database with default roles, permissions, and admin user.

⚠️ 仅用于 dev/首次初始化。管理员口令通过 SEED_ADMIN_PASSWORD 环境变量传入；
未设置时随机生成并打印一次，避免遗留弱口令 admin123。
"""
import asyncio
import os
import secrets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from pkg.common.config import settings
from app.models import Base, User, Role, Permission, EngineInstance, EngineType, user_roles, role_permissions
from app.core.auth import hash_password


async def seed():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as db:
        # Check if already seeded
        result = await db.execute(select(Role).where(Role.name == "系统管理员"))
        if result.scalar_one_or_none():
            print("Database already seeded, skipping.")
            return

        # Create Permissions
        perms_data = [
            # Dashboard
            ("控制台", "dashboard:view", "查看控制台", "menu"),
            # Agent management
            ("查看智能体列表", "agent:list", "查看智能体列表", "menu"),
            ("创建智能体", "agent:create", "创建新智能体", "api"),
            ("编辑智能体", "agent:edit", "编辑智能体配置", "api"),
            ("发布智能体", "agent:publish", "发布/下架智能体", "api"),
            ("删除智能体", "agent:delete", "删除智能体", "api"),
            # User management
            ("查看用户列表", "user:list", "查看用户列表", "menu"),
            ("创建用户", "user:create", "创建新用户", "api"),
            ("编辑用户", "user:edit", "编辑用户信息", "api"),
            ("禁用用户", "user:disable", "禁用/启用用户", "api"),
            # Role management
            ("查看角色列表", "role:list", "查看角色列表", "menu"),
            ("创建角色", "role:create", "创建新角色", "api"),
            ("分配权限", "role:assign", "为角色分配权限", "api"),
            # System settings
            ("查看系统设置", "system:view", "查看系统设置", "menu"),
            ("修改系统设置", "system:edit", "修改系统配置", "api"),
        ]

        permissions = {}
        for name, code, desc, rtype in perms_data:
            perm = Permission(name=name, code=code, description=desc, resource_type=rtype)
            db.add(perm)
            permissions[code] = perm
        await db.flush()

        # Create Roles
        admin_role = Role(name="系统管理员", description="拥有系统全部权限")
        ops_role = Role(name="运维人员", description="智能体管理、系统监控")
        user_role = Role(name="终端用户", description="仅可对话和使用智能体")

        admin_role.permissions = list(permissions.values())
        ops_role.permissions = [
            permissions["dashboard:view"],
            permissions["agent:list"],
            permissions["agent:create"],
            permissions["agent:edit"],
            permissions["agent:publish"],
            permissions["system:view"],
        ]
        user_role.permissions = [
            permissions["dashboard:view"],
        ]

        db.add_all([admin_role, ops_role, user_role])
        await db.flush()

        # Create admin user — 口令来自 SEED_ADMIN_PASSWORD，未设置则随机生成
        admin_password = os.environ.get("SEED_ADMIN_PASSWORD")
        generated = False
        if not admin_password:
            admin_password = secrets.token_urlsafe(18)
            generated = True

        admin = User(
            username="admin",
            email="admin@unionagents.io",
            hashed_password=hash_password(admin_password),
            is_active=True,
        )
        admin.roles = [admin_role]
        db.add(admin)

        # Create default EngineInstance
        default_ei = EngineInstance(
            name="Hermes-标准版",
            description="默认 Hermes 引擎实例，适用于通用场景",
            engine_type=EngineType.HERMES,
            engine_image="unionagents/engine-hermes:latest",
            engine_port=8642,
            min_cpu="100m",
            max_cpu="2",
            min_memory="256Mi",
            max_memory="2Gi",
            min_replicas=1,
            max_replicas=5,
            auto_recycle=True,
            idle_suspend_minutes=30,
            idle_destroy_hours=24,
            max_profiles_per_pod=20,
            is_builtin=True,
            created_by=admin.id,
        )
        db.add(default_ei)

        await db.commit()
        print("Database seeded successfully!")
        if generated:
            print("Admin user: admin / <随机口令，仅显示一次>:", admin_password)
            print("⚠️ 请立即保存此口令。如需指定口令，设置 SEED_ADMIN_PASSWORD 环境变量后重新 seed。")
        else:
            print("Admin user: admin / <SEED_ADMIN_PASSWORD>")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
