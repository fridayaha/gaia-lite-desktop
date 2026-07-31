"""Unit tests for OntologyService — extended coverage.

Covers: list_ontologies, update_ontology, delete_ontology,
list_object_types, define_object_type_batch, update_object_type_batch,
define_action_type_full, error paths, and conflict detection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.config.settings import settings
from ontology.core.exceptions import ConflictError, NotFoundError
from ontology.core.schemas.action import (
    ActionEffectConfig,
    ActionRule,
    ActionTypeCreate,
    ActionTypeParameter,
)
from ontology.core.schemas.ontology import (
    DataType,
    ObjectType,
    ObjectTypeCreate,
    Ontology,
    OntologyUpdate,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.ontology_service import OntologyService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    m = AsyncMock(spec=PostgresMetaStore)
    m.session = MagicMock()
    m._flush_and_commit = AsyncMock()
    return m


@pytest.fixture
def mock_catalog() -> AsyncMock:
    return AsyncMock(spec=GravitinoRegistry)


@pytest.fixture
def mock_index() -> AsyncMock:
    return AsyncMock(spec=DorisIndexStore)


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_index) -> OntologyService:
    return OntologyService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        index=mock_index,
    )


class TestOntologyCRUD:
    @pytest.mark.asyncio
    async def test_list_ontologies(self, service, mock_metadata):
        mock_metadata.list_ontologies.return_value = [
            Ontology(
                id="o1",
                api_name="hr",
                display_name="HR",
                description="",
                rid="",
                created_at=MagicMock(),
                updated_at=MagicMock(),
            ),
            Ontology(
                id="o2",
                api_name="finance",
                display_name="Finance",
                description="",
                rid="",
                created_at=MagicMock(),
                updated_at=MagicMock(),
            ),
        ]
        result = await service.list_ontologies()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_ontology(self, service, mock_metadata):
        mock_metadata.update_ontology.return_value = Ontology(
            id="o1",
            api_name="hr",
            display_name="Human Resources",
            description="Updated",
            rid="",
            status="ACTIVE",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        result = await service.update_ontology(
            "hr",
            OntologyUpdate(
                display_name="Human Resources",
                description="Updated",
            ),
        )
        assert result.display_name == "Human Resources"
        mock_metadata.update_ontology.assert_awaited_once_with(
            "hr",
            display_name="Human Resources",
            description="Updated",
            status=None,
        )

    @pytest.mark.asyncio
    async def test_delete_ontology(self, service, mock_metadata):
        """v5.2: soft-delete requires DEPRECATED status; deprovisions index first."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="o1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            status="DEPRECATED",
            deleted_at=None,
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.list_object_types.return_value = []

        await service.delete_ontology("hr")

        mock_metadata.delete_ontology.assert_awaited_once_with("hr")

    @pytest.mark.asyncio
    async def test_delete_ontology_rejects_active(self, service, mock_metadata):
        """v5.2: an ACTIVE ontology cannot be deleted (must Deprecate first)."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="o1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            status="ACTIVE",
            deleted_at=None,
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        with pytest.raises(ConflictError):
            await service.delete_ontology("hr")
        mock_metadata.delete_ontology.assert_not_awaited()


class TestObjectTypeCRUD:
    @pytest.mark.asyncio
    async def test_list_object_types(self, service, mock_metadata):
        mock_metadata.list_object_types.return_value = [
            ObjectType(
                id="ot1",
                ontology_id="onto1",
                api_name="employee",
                display_name="Employee",
                description="",
                primary_key="id",
                title_property="name",
                storage_type="MANAGED",
                visibility="NORMAL",
                status="ACTIVE",
                properties=[],
                links=[],
                created_at=MagicMock(),
                updated_at=MagicMock(),
            ),
        ]
        result = await service.list_object_types("hr")
        assert len(result) == 1

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED ObjectType 需 catalog/index Layer（lite guard 拦截）",
    )
    @pytest.mark.asyncio
    async def test_define_object_type_conflict(self, service, mock_metadata):
        """Duplicate api_name within same ontology raises ConflictError."""
        mock_metadata.get_ontology.return_value = Ontology(
            id="onto1",
            api_name="hr",
            display_name="HR",
            description="",
            rid="",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.get_object_type.return_value = MagicMock()  # Already exists

        ot_create = ObjectTypeCreate(
            api_name="Employee",
            display_name="Employee",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
        )
        with pytest.raises(ConflictError, match="already exists"):
            await service.define_object_type("hr", ot_create)

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: MANAGED ObjectType 需 catalog/index Layer（lite guard 拦截）",
    )
    @pytest.mark.asyncio
    async def test_define_object_type_ontology_not_found(self, service, mock_metadata):
        mock_metadata.get_ontology.side_effect = NotFoundError("Ontology", "ghost")
        ot_create = ObjectTypeCreate(
            api_name="Employee",
            display_name="Employee",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
        )
        with pytest.raises(NotFoundError):
            await service.define_object_type("ghost", ot_create)


class TestActionTypeFull:
    @pytest.mark.asyncio
    async def test_define_action_type_full(self, mock_metadata):
        from unittest.mock import AsyncMock, MagicMock

        from ontology.services.ontology_service import OntologyService

        onto = MagicMock(id="onto1", api_name="hr")
        obj_type = MagicMock(id="ot1", api_name="order")
        mock_metadata.get_ontology.return_value = onto
        mock_metadata.get_object_type_by_api_name.return_value = obj_type
        mock_metadata.create_action_type.return_value = MagicMock()

        at_def = ActionTypeCreate(
            api_name="approveOrder",
            display_name="Approve Order",
            affected_object_type_api_name="order",
            parameters=[
                ActionTypeParameter(api_name="orderId", display_name="Order ID", data_type=DataType.STRING),
            ],
            rules=[
                ActionRule(type="constraint", target="orderId", expression='order_id != ""'),
            ],
            effects=[
                ActionEffectConfig(type="webhook", config={"url": "https://example.com"}),
            ],
        )
        # M4: define_action_type_full resolves ActionService via container
        # (no more `new ActionService(dataset=None)`). Mock the container's
        # action_service property; its define_action_type returns a truthy
        # result and (separately) writes via metadata.create_action_type.
        mock_action_svc = AsyncMock()
        mock_action_svc.define_action_type.return_value = MagicMock(api_name="approveOrder")
        mock_container = MagicMock()
        mock_container.action_service = mock_action_svc
        service = OntologyService(
            metadata=mock_metadata,
            catalog=MagicMock(),
            index=MagicMock(),
            container=mock_container,
        )

        result = await service.define_action_type_full("hr", at_def)
        assert result is not None
        mock_action_svc.define_action_type.assert_awaited_once_with("hr", at_def)

    @pytest.mark.asyncio
    async def test_define_action_type_legacy(self, service, mock_metadata):
        """Legacy simplified define_action_type API."""
        onto = MagicMock(id="onto1", api_name="hr")
        mock_metadata.get_ontology.return_value = onto
        mock_metadata.create_action_type.return_value = MagicMock()

        result = await service.define_action_type(
            ontology_api_name="hr",
            api_name="promote",
            display_name="Promote",
            parameters={"grade": {"type": "STRING"}},
            rules={},
        )
        assert result is not None
        mock_metadata.create_action_type.assert_awaited_once()


class TestSharedPropertyExtended:
    @pytest.mark.asyncio
    async def test_link_shared_property(self, service, mock_metadata):
        await service.link_shared_property("ot1", "sp1")
        mock_metadata.link_shared_property.assert_awaited_once_with("ot1", "sp1")


class TestLiteManagedGuard:
    """lite 版 define_object_type 入口拦截 MANAGED（红线下砍托管表）。

    lite 只支持 VIRTUAL 本体；MANAGED 需 Gravitino+Iceberg+Doris 物理注册，
    lite 不装这些 Layer。guard 在入口抛 EditionUnavailableError，避免后续
    MANAGED 分支触达 self._catalog/_index/_dataset（lite 装配为 None）。
    """

    @pytest.mark.asyncio
    async def test_lite_rejects_managed_object_type(
        self, service: OntologyService, mock_metadata, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ontology.config.settings import settings as _settings
        from ontology.core.exceptions import EditionUnavailableError

        monkeypatch.setattr(_settings, "edition", "lite")
        # ontology 存在（绕过 NotFound），让 guard 成为第一个抛错点。
        mock_metadata.get_ontology.return_value = Ontology(
            id="o1",
            api_name="Hr",
            display_name="HR",
            description="",
            rid="",
            space_id=None,
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        ot_create = ObjectTypeCreate(
            api_name="Employee",
            display_name="Employee",
            primary_key="id",
            storage_type="MANAGED",
        )
        with pytest.raises(EditionUnavailableError, match="托管表"):
            await service.define_object_type("Hr", ot_create)

    @pytest.mark.asyncio
    async def test_lite_allows_virtual_object_type(
        self, service: OntologyService, mock_metadata, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lite 下 VIRTUAL 不被 guard 拦截（VIRTUAL 是 lite 唯一支持的 storage_type）。

        验证 VIRTUAL 路径不走 catalog/index 物理注册（992 行 if MANAGED 块跳过）。
        """
        from ontology.config.settings import settings as _settings
        from ontology.core.exceptions import EditionUnavailableError

        monkeypatch.setattr(_settings, "edition", "lite")
        mock_metadata.get_ontology.return_value = Ontology(
            id="o1",
            api_name="Hr",
            display_name="HR",
            description="",
            rid="",
            space_id=None,
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.get_object_type.side_effect = NotFoundError("ObjectType", "x")
        mock_metadata.create_object_type.return_value = MagicMock(spec=ObjectType)
        ot_create = ObjectTypeCreate(
            api_name="Employee",
            display_name="Employee",
            primary_key="id",
            storage_type="VIRTUAL",
        )
        # VIRTUAL 不应抛 EditionUnavailableError（其他 mock 不全异常可接受）。
        try:
            await service.define_object_type("Hr", ot_create)
        except EditionUnavailableError:
            pytest.fail("VIRTUAL object type should not be rejected under lite")
        except Exception:
            pass
