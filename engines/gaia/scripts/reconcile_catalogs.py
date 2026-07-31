#!/usr/bin/env python
"""一次性补注册缺失的 Gravitino catalog（救火 + 验证 reconcile_catalogs）。

背景：Gravitino 重建/升级后，PG ``data_sources`` 表记录的 catalog 可能在
Gravitino 侧丢失（metalake 被重置），导致 explore 报 ``CATALOG_NOT_FOUND``。
此脚本直接调 ``DataSourceService.reconcile_catalogs()``，把缺失的 catalog
补注册回来。幂等——catalog 已存在则跳过。

用法（在 api 容器或本地 .venv 里）：
    .venv/bin/python scripts/reconcile_catalogs.py
    .venv/bin/python scripts/reconcile_catalogs.py --ds xiaoling   # 只看单个

退出码：0 成功（含「无需修复」）；1 出错。
"""

import argparse
import asyncio
import sys

from ontology.config.container import container
from ontology.services.datasource_service import DataSourceService


async def main(ds_filter: str | None) -> int:
    service: DataSourceService = container.datasource_service
    if ds_filter:
        # 只校验单个数据源是否需要修复
        from ontology.core.exceptions import NotFoundError

        try:
            ds = await service.metadata.get_datasource(ds_filter)
        except NotFoundError:
            print(f"❌ 数据源 {ds_filter!r} 不存在", file=sys.stderr)
            return 1
        live = await service.catalog.list_catalogs()
        live_names = {c.get("name") for c in live}
        catalog_name = ds.gravitino_catalog_name or ds.api_name
        if catalog_name in live_names:
            print(f"✅ {ds_filter}: catalog {catalog_name!r} 已存在于 Gravitino，无需修复")
            return 0
        print(f"⚠ {ds_filter}: catalog {catalog_name!r} 缺失，正在补注册...")
        await service._register_datasource_catalog(ds)
        print(f"✅ {ds_filter}: catalog {catalog_name!r} 补注册成功")
        return 0

    # 全量 reconcile
    healed = await service.reconcile_catalogs()
    if healed:
        print(f"✅ 已补注册 {healed} 个缺失的 catalog")
    else:
        print("✅ 所有 JDBC 数据源的 catalog 均已存在于 Gravitino，无需修复")
    await container.aclose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补注册缺失的 Gravitino catalog")
    parser.add_argument(
        "--ds",
        help="只处理指定 api_name 的数据源（默认全量 reconcile）",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.ds)))
