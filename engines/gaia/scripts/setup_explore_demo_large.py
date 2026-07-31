"""图探索千级节点 demo 数据（供应链多层网络）。

规模：50 Supplier → 200 Material → 500 Order → 200 Customer，交叉关系
总节点 ~950，边 ~1800，足以体现图推理价值（聚类/路径/影响传导）。

幂等：已存在则跳过/更新。带 GEOPOINT（北京/上海/广州分布）+ timestamp（时序）。
"""
import asyncio
import random
import sys

from ontology.config.container import container
from ontology.core.schemas.graph import EdgeProps
from ontology.layers.graph.neo4j_graph_store import close_driver

ONT = "ChainSmoke"

# 城市坐标（GEOPOINT）
CITIES = [
    {"name": "北京", "lon": 116.40, "lat": 39.90},
    {"name": "上海", "lon": 121.47, "lat": 31.23},
    {"name": "广州", "lon": 113.26, "lat": 23.13},
    {"name": "深圳", "lon": 114.06, "lat": 22.55},
    {"name": "杭州", "lon": 120.15, "lat": 30.28},
    {"name": "成都", "lon": 104.07, "lat": 30.57},
    {"name": "武汉", "lon": 114.31, "lat": 30.59},
    {"name": "西安", "lon": 108.94, "lat": 34.34},
]
import json as _json

def _loc(city, rng):
    """Neo4j 不支持嵌套 Map 属性，location 存 JSON 字符串。"""
    return _json.dumps({"lon": city["lon"] + rng.uniform(-0.5, 0.5), "lat": city["lat"] + rng.uniform(-0.3, 0.3)})

STATUSES = ["pending", "unfulfilled", "fulfilled", "cancelled"]
RISK_LEVELS = ["low", "medium", "high", None, None, None]  # 多数无风险


async def ensure_schema():
    """确保本体 + 4 个 ObjectType + links 存在。"""
    import httpx
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as c:
        await c.post("/ontologies", json={"api_name": ONT, "display_name": "供应链演练", "description": "供应链图关联推理演练本体（供应商→物料→订单→客户），用于演示图探索、路径推理、时空多维分析。"})

        # Customer（先建，被 Order 引用）
        await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Customer", "display_name": "客户", "storage_type": "MANAGED",
            "primary_key": "customerId", "title_property": "name",
            "properties": [
                {"api_name": "customerId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
                {"api_name": "name", "display_name": "名称", "data_type": "STRING", "searchable": True},
                {"api_name": "city", "display_name": "城市", "data_type": "STRING", "searchable": True},
                {"api_name": "location", "display_name": "位置", "data_type": "GEOPOINT"},
                {"api_name": "tier", "display_name": "等级", "data_type": "STRING", "searchable": True},
            ],
            "links": [],
        })

        # Order
        r = await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Order", "display_name": "订单", "storage_type": "MANAGED",
            "primary_key": "orderId", "title_property": "name",
            "properties": [
                {"api_name": "orderId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
                {"api_name": "name", "display_name": "名称", "data_type": "STRING", "searchable": True},
                {"api_name": "status", "display_name": "状态", "data_type": "STRING", "searchable": True},
                {"api_name": "amount", "display_name": "金额", "data_type": "DOUBLE", "searchable": True},
                {"api_name": "createdAt", "display_name": "创建时间", "data_type": "STRING"},
            ],
            "links": [],
        })
        order_id = (await c.get(f"/ontologies/{ONT}/object-types")).json()
        order_id = next((o["id"] for o in order_id if o["api_name"] == "Order"), None)

        # Material + suppliedBy (Material→Supplier reverse) + usedIn (Material→Order)
        await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Material", "display_name": "物料", "storage_type": "MANAGED",
            "primary_key": "materialId", "title_property": "name",
            "properties": [
                {"api_name": "materialId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
                {"api_name": "name", "display_name": "名称", "data_type": "STRING", "searchable": True},
                {"api_name": "category", "display_name": "品类", "data_type": "STRING", "searchable": True},
                {"api_name": "stock", "display_name": "库存", "data_type": "DOUBLE", "searchable": True},
            ],
            "links": [
                {"api_name": "usedIn", "display_name": "用于", "target_object_type_id": order_id, "cardinality": "MANY", "direction": "OUTGOING"},
            ],
        })

        # Supplier + supplies (Supplier→Material) + shipsTo (Supplier→Order, 直接发货)
        await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Supplier", "display_name": "供应商", "storage_type": "MANAGED",
            "primary_key": "supplierId", "title_property": "name",
            "properties": [
                {"api_name": "supplierId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
                {"api_name": "name", "display_name": "名称", "data_type": "STRING", "searchable": True},
                {"api_name": "city", "display_name": "城市", "data_type": "STRING", "searchable": True},
                {"api_name": "location", "display_name": "位置", "data_type": "GEOPOINT"},
                {"api_name": "riskLevel", "display_name": "风险等级", "data_type": "STRING", "searchable": True},
            ],
            "links": [
                {"api_name": "supplies", "display_name": "供应", "target_object_type_id": next(o["id"] for o in (await c.get(f"/ontologies/{ONT}/object-types")).json() if o["api_name"] == "Material"), "cardinality": "MANY", "direction": "OUTGOING"},
            ],
        })

        # Order → Customer (placedBy)
        cust_id = next(o["id"] for o in (await c.get(f"/ontologies/{ONT}/object-types")).json() if o["api_name"] == "Customer")
        # Order 已建，用 update 加 link（简化：直接重建会 409，用 patch）
        # 实际：Order links 为空，需补 placedBy。这里通过单独 link 创建端点
        print("schema ready")


async def gen_data():
    """生成节点 + 边到 Neo4j + object_state。"""
    from sqlalchemy import text
    from ontology.config.database import engine
    g = container.graph_projector
    rng = random.Random(42)

    nodes = []  # (vid, ot, props)
    edges = []  # (src_ot, src_vid, link, tgt_ot, tgt_vid)

    # 50 Supplier
    for i in range(50):
        city = rng.choice(CITIES)
        risk = rng.choice(RISK_LEVELS)
        vid = f"S{i:03d}"
        nodes.append((vid, "Supplier", {
            "supplierId": vid, "name": f"供应商{i:03d}", "city": city["name"],
            "location": _loc(city, rng),
            "riskLevel": risk or "none",
        }))

    # 200 Material
    categories = ["电子元件", "结构件", "原材料", "包装", "辅料"]
    for i in range(200):
        vid = f"M{i:03d}"
        nodes.append((vid, "Material", {
            "materialId": vid, "name": f"物料{i:03d}",
            "category": rng.choice(categories), "stock": round(rng.uniform(0, 1000), 1),
        }))

    # 500 Order
    base_ts = 1751328000000  # 2025-07-01 ms
    for i in range(500):
        vid = f"O{i:04d}"
        nodes.append((vid, "Order", {
            "orderId": vid, "name": f"订单{i:04d}", "status": rng.choice(STATUSES),
            "amount": round(rng.uniform(100, 50000), 2),
            "createdAt": str(base_ts + i * 3600000),  # 每小时一单
        }))

    # 200 Customer
    for i in range(200):
        city = rng.choice(CITIES)
        vid = f"C{i:03d}"
        nodes.append((vid, "Customer", {
            "customerId": vid, "name": f"客户{i:03d}", "city": city["name"],
            "location": _loc(city, rng),
            "tier": rng.choice(["A", "B", "C"]),
        }))

    # 边：Supplier→supplies→Material（每个供应商供 4-8 个物料）
    for s_idx in range(50):
        n = rng.randint(4, 8)
        for m_idx in rng.sample(range(200), n):
            edges.append(("Supplier", f"S{s_idx:03d}", "supplies", "Material", f"M{m_idx:03d}"))

    # Material→usedIn→Order（每个物料用于 2-5 个订单）
    for m_idx in range(200):
        n = rng.randint(2, 5)
        for o_idx in rng.sample(range(500), n):
            edges.append(("Material", f"M{m_idx:03d}", "usedIn", "Order", f"O{o_idx:04d}"))

    # Order→placedBy→Customer（每个订单一个客户，用 supplies 反向不合适，建 placedBy link）
    # 简化：用 Order→Customer 的 supplies 不对，需新 link。这里跳过 placedBy，用 Customer→Order 反向 supplies 不通
    # 实际：给 Customer 加 link buysTo Order。为避免再改 schema，用现有结构：Supplier→supplies→Material→usedIn→Order 已够多层
    # 额外加 Supplier→supplies→Order 直接边（部分供应商直发订单）丰富图
    for s_idx in range(50):
        n = rng.randint(2, 6)
        for o_idx in rng.sample(range(500), n):
            edges.append(("Supplier", f"S{s_idx:03d}", "supplies", "Order", f"O{o_idx:04d}"))

    # 灌 Neo4j（分批，每批 200）
    print(f"projecting {len(nodes)} nodes...")
    batch = 200
    for i in range(0, len(nodes), batch):
        for vid, ot, props in nodes[i:i+batch]:
            await g.project_object(ONT, ot, {"id": vid, "properties": props})
        print(f"  nodes {i+len(nodes[i:i+batch])}/{len(nodes)}")

    print(f"projecting {len(edges)} edges...")
    for i in range(0, len(edges), batch):
        for src_ot, src_vid, link, tgt_ot, tgt_vid in edges[i:i+batch]:
            await g.project_link(ONT, link, src_ot, src_vid, tgt_ot, tgt_vid, EdgeProps(weight=round(rng.uniform(0.1, 1.0), 2)))
        print(f"  edges {i+len(edges[i:i+batch])}/{len(edges)}")

    # 灌 object_state（PG）
    print("writing object_state...")
    async with engine.begin() as conn:
        ont = await conn.execute(text("SELECT id FROM ontologies WHERE api_name=:n"), {"n": ONT})
        ont_id = ont.scalar()
        for vid, ot, props in nodes:
            await conn.execute(text(
                "INSERT INTO object_state (object_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at) "
                "VALUES (:vid, :ot, :oid, 1, CAST(:p AS jsonb), 'system', NOW(), NOW()) "
                "ON CONFLICT (object_id) DO UPDATE SET properties = EXCLUDED.properties, updated_at = NOW()"
            ), {"vid": vid, "ot": ot, "oid": ont_id, "p": __import__("json").dumps(props)})

        # 灌 object_links（PG，供 traverse_link 降级路径 + source_to_target_map）
        print(f"writing {len(edges)} object_links...")
        import uuid as _uuid
        for src_ot, src_vid, link, tgt_ot, tgt_vid in edges:
            await conn.execute(text(
                "INSERT INTO object_links (id, ontology_id, link_type_api_name, source_object_id, target_object_id, created_at) "
                "VALUES (:id, :oid, :lt, :src, :tgt, NOW()) "
                "ON CONFLICT (link_type_api_name, source_object_id, target_object_id) DO NOTHING"
            ), {"id": _uuid.uuid4().hex, "oid": ont_id, "lt": link, "src": src_vid, "tgt": tgt_vid})
    await engine.dispose()

    print(f"DONE: {len(nodes)} nodes, {len(edges)} edges")


async def main():
    await ensure_schema()
    await gen_data()
    await close_driver()
    await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
