"""V3 三层模型建表脚本：定义 / 版本 / 资源池 / 实例。

执行方式:
  cd services/manager
  python ../../scripts/migrate_to_v3.py

本脚本只创建 V3 新表（resource_pools / agent_definitions / agent_versions /
agent_instances / agent_instance_channels / agent_instance_user_access /
agent_instance_group_access），**不迁移数据、不下线老表**。

数据迁移（Agent→Definition+Version+Instance、EngineInstance→ResourcePool）与
老表下线在 service 切换后由后续脚本完成。这样 Batch 1 零破坏，现有代码继续运行。
"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, ".")

from pkg.common.config import settings
from app.models import Base

V3_TABLES = [
    "resource_pools",
    "agent_definitions",
    "agent_versions",
    "agent_instances",
    "agent_instance_channels",
    "agent_instance_user_access",
    "agent_instance_group_access",
]


async def migrate():
    engine = create_async_engine(settings.database_url)
    print("=" * 60)
    print("UnionAgents V3 三层模型建表")
    print("=" * 60)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print(f"\n[OK] 已确保 V3 表存在:\n  - " + "\n  - ".join(V3_TABLES))
    print("\n注意: 未迁移数据，未下线老表。待 service 切换后另行执行。")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
