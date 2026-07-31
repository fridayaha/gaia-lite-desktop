"""
创建测试用户用于本地开发和调试：
- MengLiang（普通用户）
- LiaoQiWang（超级管理员）
- GuoRan（店长/运维人员）
- LiZhe（普通用户）
- YanHuaYiLeng（普通用户）

用法：在 Seed 脚本之后运行：
  python scripts/seed_test_users.py
"""
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from pkg.common.config import settings
from app.models import Base, User, Role
from app.core.auth import hash_password


TEST_USERS = [
    {"username": "MengLiang",  "email": "mengliang@unionagents.io",  "role_name": "终端用户"},
    {"username": "LiaoQiWang", "email": "liaoqiwang@unionagents.io", "role_name": "系统管理员"},
    {"username": "GuoRan",     "email": "guoran@unionagents.io",     "role_name": "运维人员"},
    {"username": "LiZhe",      "email": "lizhe@unionagents.io",      "role_name": "终端用户"},
    {"username": "YanHuaYiLeng", "email": "yanhuayileng@unionagents.io", "role_name": "终端用户"},
]


async def seed_test_users():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as db:
        # 获取所有角色
        result = await db.execute(select(Role))
        roles = {r.name: r for r in result.scalars().all()}
        print(f"Found roles: {list(roles.keys())}")

        if not roles:
            print("No roles found! Run scripts/seed.py first.")
            return

        created = 0
        skipped = 0
        for u in TEST_USERS:
            # 检查是否已存在
            result = await db.execute(select(User).where(User.username == u["username"]))
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  ⏭  {u['username']} already exists, skipping")
                skipped += 1
                continue

            role = roles.get(u["role_name"])
            if not role:
                print(f"  ✗  Role '{u['role_name']}' not found for {u['username']}")
                continue

            user = User(
                username=u["username"],
                email=u["email"],
                hashed_password=hash_password("88888888"),
                is_active=True,
            )
            user.roles = [role]
            db.add(user)
            created += 1

        await db.commit()

        print(f"\nCreated {created} test users, skipped {skipped}")
        for u in TEST_USERS:
            print(f"  {u['username']:15s} / 88888888  ({u['role_name']})")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_test_users())
