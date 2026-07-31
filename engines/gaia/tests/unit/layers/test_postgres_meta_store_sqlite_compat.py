"""B1 acceptance test — PostgresMetaStore PG-specific constructs on real SQLite.

验证 lite 桌面版复用 PostgresMetaStore 的跨方言兼容性。这些方法用到 PG 专属
构造（`postgresql.insert.on_conflict_do_nothing`、`with_for_update(skip_locked)`、
`update.returning`、JSONB `.properties[k].as_string()`），现有 mock 测覆盖不到
真实 SQL 行为，本测试用真 SQLite 跑通以锁住兼容性（B1）。

跑在 tests/conftest.py 的 db_session fixture（内存 SQLite + create_all）上，
full / lite 两 edition 均收集（不依赖外部服务）。
"""

import pytest

from ontology.core.models.defaults import new_uuid, utcnow
from ontology.core.models.ontology import (
    ActionExecutionLogModel,
    InterfaceTypeModel,
    ObjectTypeInterfaceModel,
    ObjectTypeModel,
    OntologyModel,
    OutboxModel,
)
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store(db_session):
    return PostgresMetaStore(db_session)


async def _seed_ontology_and_type(store: PostgresMetaStore, db_session) -> tuple[str, str]:
    """Insert an Ontology + ObjectType directly via ORM; return (ontology_id, ot_api_name)."""
    ont = OntologyModel(
        id=new_uuid(),
        api_name="lite_ont",
        display_name="Lite Ont",
        status="ACTIVE",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(ont)
    await db_session.flush()
    ot = ObjectTypeModel(
        id=new_uuid(),
        ontology_id=ont.id,
        api_name="employee",
        display_name="Employee",
        primary_key="emp_id",
        title_property="name",
        storage_type="MANAGED",
        project_id="00000000000000000000000000000001",  # SQLite 不强制 FK
        capabilities={},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(ot)
    await db_session.commit()
    return ont.id, ot.api_name


class TestUpsertObjectState:
    """OCC upsert — postgresql.insert.on_conflict_do_nothing + update.returning."""

    async def test_create_returns_version_1(self, store, db_session):
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        rid = "ri.ontology.main.object.abc"

        version = await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E1", "name": "Alice", "status": "active"},
            expected_version=0,
        )
        await db_session.commit()
        assert version == 1

        state = await store.get_object_state(rid)
        assert state is not None
        assert state["version"] == 1
        assert state["properties"]["name"] == "Alice"

    async def test_create_duplicate_returns_zero(self, store, db_session):
        """on_conflict_do_nothing: re-CREATE with same rid → 0 (no row inserted)."""
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        rid = "ri.ontology.main.object.dup"
        kwargs = dict(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E2"},
            expected_version=0,
        )
        v1 = await store.upsert_object_state(**kwargs)
        await db_session.commit()
        v2 = await store.upsert_object_state(**kwargs)  # same rid, expected_version=0
        await db_session.commit()
        assert v1 == 1
        assert v2 == 0  # ON CONFLICT DO NOTHING — rowcount 0

    async def test_update_bumps_version(self, store, db_session):
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        rid = "ri.ontology.main.object.upd"
        await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E3", "status": "active"},
            expected_version=0,
        )
        await db_session.commit()

        new_version = await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E3", "status": "inactive"},
            expected_version=1,
        )
        await db_session.commit()
        assert new_version == 2

        state = await store.get_object_state(rid)
        assert state["version"] == 2
        assert state["properties"]["status"] == "inactive"

    async def test_update_stale_version_returns_zero(self, store, db_session):
        """OCC: UPDATE with stale expected_version → 0 (no row matched)."""
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        rid = "ri.ontology.main.object.occ"
        await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E4"},
            expected_version=0,
        )
        await db_session.commit()
        # Bump to v2.
        await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E4"},
            expected_version=1,
        )
        await db_session.commit()
        # Stale update (expected_version=1, but current is 2) → 0.
        result = await store.upsert_object_state(
            rid=rid,
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "E4"},
            expected_version=1,
        )
        await db_session.commit()
        assert result == 0


class TestObjectLink:
    """add_object_link — on_conflict_do_nothing(index_elements=[...]) idempotency."""

    async def test_add_link_idempotent(self, store, db_session):
        ont_id, _ = await _seed_ontology_and_type(store, db_session)
        created1 = await store.add_object_link(
            ontology_id=ont_id,
            link_type_api_name="reports_to",
            source_rid="ri.main.a",
            target_rid="ri.main.b",
        )
        await db_session.commit()
        created2 = await store.add_object_link(
            ontology_id=ont_id,
            link_type_api_name="reports_to",
            source_rid="ri.main.a",
            target_rid="ri.main.b",
        )
        await db_session.commit()
        assert created1 is True
        assert created2 is False  # ON CONFLICT DO NOTHING — second insert no-op


class TestJsonbQueries:
    """properties[key].as_string() — SQLAlchemy auto-dialectizes to json_extract on SQLite."""

    async def test_query_object_states_jsonb_filter(self, store, db_session):
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        for emp_id, status in [("E1", "active"), ("E2", "inactive"), ("E3", "active")]:
            await store.upsert_object_state(
                rid=f"ri.main.{emp_id}",
                object_type_api_name=ot_name,
                ontology_id=ont_id,
                ontology_api_name="lite_ont",
                properties={"emp_id": emp_id, "status": status},
                expected_version=0,
            )
        await db_session.commit()

        results = await store.query_object_states(
            object_type_api_name=ot_name,
            filters=[{"field": "status", "value": "active"}],
        )
        assert len(results) == 2
        assert {r["properties"]["emp_id"] for r in results} == {"E1", "E3"}

    async def test_get_object_states_by_pks(self, store, db_session):
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        await store.upsert_object_state(
            rid="ri.main.pk1",
            object_type_api_name=ot_name,
            ontology_id=ont_id,
            ontology_api_name="lite_ont",
            properties={"emp_id": "PK1", "status": "active"},
            expected_version=0,
        )
        await db_session.commit()

        rows = await store.get_object_states_by_pks(
            ontology_api_name="lite_ont",
            object_type_api_name=ot_name,
            pk_backing_column="emp_id",
            pk_values=["PK1", "MISSING"],
        )
        assert len(rows) == 1
        assert rows[0]["properties"]["emp_id"] == "PK1"


class TestInterfaceBinding:
    """add_interface_to_object_type — on_conflict_do_nothing(index_elements=[...])."""

    async def test_add_interface_idempotent(self, store, db_session):
        ont_id, ot_name = await _seed_ontology_and_type(store, db_session)
        # Resolve the ObjectType id + insert an InterfaceType.
        from sqlalchemy import select

        ot_id = (
            await db_session.execute(select(ObjectTypeModel.id).where(ObjectTypeModel.api_name == ot_name))
        ).scalar_one()
        iface = InterfaceTypeModel(
            id=new_uuid(),
            ontology_id=ont_id,
            api_name="auditable",
            display_name="Auditable",
            project_id="00000000000000000000000000000001",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db_session.add(iface)
        await db_session.commit()

        added1 = await store.add_interface_to_object_type(ot_id, iface.id)
        await db_session.commit()
        added2 = await store.add_interface_to_object_type(ot_id, iface.id)
        await db_session.commit()
        assert added1 is True
        assert added2 is False

        count = (
            (
                await db_session.execute(
                    select(ObjectTypeInterfaceModel).where(ObjectTypeInterfaceModel.object_type_id == ot_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1  # only one association row


class TestClaimPendingOutbox:
    """claim_pending_by_ontology — with_for_update(skip_locked=True) must not crash on SQLite."""

    async def test_claim_returns_pending_records(self, store, db_session):
        ont_id, _ = await _seed_ontology_and_type(store, db_session)
        # Outbox FK → action_execution_logs; insert a log first.
        log = ActionExecutionLogModel(
            id=new_uuid(),
            action_id=new_uuid(),
            action_type_api_name="act",
            object_type_api_name="employee",
            ontology_id=ont_id,
            idempotency_key=f"k-{new_uuid()}",
            parameters={},
            mutations=[],
            status="COMPLETED",
            performed_by="system",
            before_snapshot={},
            after_snapshot={},
            created_at=utcnow(),
        )
        db_session.add(log)
        await db_session.flush()
        db_session.add(
            OutboxModel(
                id=new_uuid(),
                action_execution_id=log.id,
                effect_type="ARCHIVE",
                target_ontology="lite_ont",
                effect_config={},
                payload={},
                status="PENDING",
                retry_count=0,
                max_retries=3,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await db_session.commit()

        claimed = await store.claim_pending_by_ontology("ARCHIVE", "lite_ont", batch_size=10)
        assert len(claimed) == 1
        assert claimed[0]["effect_type"] == "ARCHIVE"
        assert claimed[0]["target_ontology"] == "lite_ont"
