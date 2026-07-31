"""图探索端到端测试数据 setup（ChainSmoke 供应链场景）。

造：Ontology ChainSmoke + Supplier/Order ObjectType + supplies LinkType +
Neo4j 节点(S001/S002/O1/O2) + 边(S001→O1, S001→O2) + object_state。
幂等：已存在则跳过/更新。
"""
import asyncio
import sys

from ontology.config.container import container
from ontology.core.schemas.graph import EdgeProps
from ontology.layers.graph.neo4j_graph_store import close_driver

ONT = "ChainSmoke"


async def ensure_ontology():
    """通过 API 确保本体 + OT + Link 存在（幂等）。"""
    import httpx
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as c:
        # 本体
        r = await c.post("/ontologies", json={"api_name": ONT, "display_name": "供应链演练", "description": "供应链图关联推理演练本体"})
        if r.status_code == 409 or "already" in r.text:
            print(f"ontology {ONT} exists")
        else:
            print(f"ontology {ONT}: {r.status_code}")

        # Order（先建，link 需要 target id）
        r = await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Order", "display_name": "Order", "storage_type": "MANAGED",
            "primary_key": "orderId", "title_property": "name",
            "properties": [
                {"api_name": "orderId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
                {"api_name": "status", "display_name": "Status", "data_type": "STRING", "searchable": True},
            ],
            "links": [],
        })
        print(f"Order: {r.status_code}")

        # 拿 Order id
        r = await c.get(f"/ontologies/{ONT}/object-types")
        ots = r.json()
        order_id = next((o["id"] for o in ots if o["api_name"] == "Order"), None)
        print(f"Order id: {order_id}")

        # Supplier + supplies link
        r = await c.post(f"/ontologies/{ONT}/object-types/create", json={
            "api_name": "Supplier", "display_name": "Supplier", "storage_type": "MANAGED",
            "primary_key": "supplierId", "title_property": "name",
            "properties": [
                {"api_name": "supplierId", "display_name": "ID", "data_type": "STRING", "is_primary_key": True},
            ],
            "links": [
                {"api_name": "supplies", "display_name": "supplies", "target_object_type_id": order_id, "cardinality": "MANY", "direction": "OUTGOING"},
            ],
        })
        print(f"Supplier: {r.status_code}")


async def ensure_neo4j():
    """Neo4j 节点 + 边（幂等 upsert）。"""
    g = container.graph_projector
    nodes = [
        ("S001", "Supplier", {"supplierId": "S001", "name": "Acme"}),
        ("S002", "Supplier", {"supplierId": "S002", "name": "Beta"}),
        ("O1", "Order", {"orderId": "O1", "name": "Ord1", "status": "unfulfilled"}),
        ("O2", "Order", {"orderId": "O2", "name": "Ord2", "status": "fulfilled"}),
    ]
    for vid, ot, props in nodes:
        await g.project_object(ONT, ot, {"id": vid, "properties": props})
    await g.project_link(ONT, "supplies", "Supplier", "S001", "Order", "O1", EdgeProps(weight=0.9))
    await g.project_link(ONT, "supplies", "Supplier", "S001", "Order", "O2", EdgeProps(weight=0.5))
    print("neo4j nodes + edges upserted")


async def ensure_object_state():
    """object_state 水合数据（PG 直写，幂等）。"""
    from sqlalchemy import text
    from ontology.config.database import engine

    async with engine.begin() as conn:
        ont = await conn.execute(text("SELECT id FROM ontologies WHERE api_name = :n"), {"n": ONT})
        ont_id = ont.scalar()
        if not ont_id:
            print("ontology not found in PG, skipping object_state")
            return
        rows = [
            ("S001", "Supplier", '{"supplierId":"S001","name":"Acme"}'),
            ("S002", "Supplier", '{"supplierId":"S002","name":"Beta"}'),
            ("O1", "Order", '{"orderId":"O1","name":"Ord1","status":"unfulfilled"}'),
            ("O2", "Order", '{"orderId":"O2","name":"Ord2","status":"fulfilled"}'),
        ]
        for vid, ot, props in rows:
            await conn.execute(text(
                "INSERT INTO object_state (object_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at) "
                "VALUES (:vid, :ot, :oid, 1, CAST(:p AS jsonb), 'system', NOW(), NOW()) "
                "ON CONFLICT (object_id) DO UPDATE SET properties = EXCLUDED.properties, updated_at = NOW()"
            ), {"vid": vid, "ot": ot, "oid": ont_id, "p": props})
    print("object_state upserted")

    # object_links（PG，供 traverse 降级路径）
    import uuid as _uuid
    link_rows = [
        ("supplies", "S001", "O1"),
        ("supplies", "S001", "O2"),
    ]
    for lt, src, tgt in link_rows:
        await conn.execute(text(
            "INSERT INTO object_links (id, ontology_id, link_type_api_name, source_object_id, target_object_id, created_at) "
            "VALUES (:id, :oid, :lt, :src, :tgt, NOW()) "
            "ON CONFLICT (link_type_api_name, source_object_id, target_object_id) DO NOTHING"
        ), {"id": _uuid.uuid4().hex, "oid": ont_id, "lt": lt, "src": src, "tgt": tgt})
    print("object_links upserted")


async def main():
    await ensure_ontology()
    await ensure_neo4j()
    await ensure_object_state()
    await close_driver()
    await container.aclose()
    print("DONE - data ready")


if __name__ == "__main__":
    asyncio.run(main())
