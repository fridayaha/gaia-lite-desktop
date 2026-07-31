"""Extended integration tests for Ontology routes — full CRUD coverage."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import Container
from ontology.core.schemas.ontology import (
    LinkTypeDef,
    ObjectType,
    Ontology,
    PropertyDef,
)
from ontology.main import app

_now = datetime.now(UTC)


@pytest.fixture
def container() -> Container:
    c = Container()
    c._metadata = AsyncMock()
    c._catalog = AsyncMock()
    c._dataset = AsyncMock()
    c._index = AsyncMock()
    c._pipeline = AsyncMock()
    c._engine = AsyncMock()
    return c


@pytest.fixture
def client(container) -> TestClient:
    with patch("ontology.routes.ontology.container", container):
        with patch("ontology.routes.query.container", container):
            with patch("ontology.routes.action.container", container):
                with patch("ontology.routes.datasource.container", container):
                    yield TestClient(app)


def _service(container):
    svc = container.service_overrides.get("ontology_service")
    if svc is None:
        svc = AsyncMock()
        container.service_overrides["ontology_service"] = svc
    svc._metadata = MagicMock()
    svc._metadata.session = AsyncMock()
    return svc


class TestOntologyRoutesExtended:
    def test_create_ontology(self, client, container):
        svc = _service(container)
        svc.create_ontology = AsyncMock(
            return_value=Ontology(
                id="id1",
                api_name="sys_hr",
                display_name="HR",
                description="",
                rid="",
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.post(
            "/ontologies",
            json={
                "api_name": "sys_hr",
                "display_name": "HR",
                "description": "HR department",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["api_name"] == "sys_hr"

    def test_list_ontologies(self, client, container):
        svc = _service(container)
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        svc._metadata.session.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/ontologies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_ontology_success(self, client, container):
        svc = _service(container)
        svc.get_ontology = AsyncMock(
            return_value=Ontology(
                id="id1",
                api_name="sys_hr",
                display_name="HR",
                description="",
                rid="",
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.get("/ontologies/sys_hr")
        assert resp.status_code == 200
        assert resp.json()["api_name"] == "sys_hr"

    def test_patch_ontology(self, client, container):
        svc = _service(container)
        svc.update_ontology = AsyncMock(
            return_value=Ontology(
                id="id1",
                api_name="sys_hr",
                display_name="Human Resources",
                description="Updated",
                rid="",
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.patch(
            "/ontologies/sys_hr",
            json={
                "display_name": "Human Resources",
                "description": "Updated",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Human Resources"

    def test_delete_ontology_success(self, client, container):
        svc = _service(container)
        svc.delete_ontology = AsyncMock()

        resp = client.delete("/ontologies/sys_hr")
        assert resp.status_code == 204


class TestObjectTypeRoutes:
    def test_create_object_type(self, client, container):
        svc = _service(container)
        svc.define_object_type = AsyncMock(
            return_value=ObjectType(
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
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.post(
            "/ontologies/hr/object-types",
            json={
                "api_name": "employee",
                "display_name": "Employee",
                "primary_key": "id",
                "title_property": "name",
                "storage_type": "MANAGED",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["api_name"] == "employee"

    def test_list_object_types(self, client, container):
        svc = _service(container)
        svc.list_object_types = AsyncMock(return_value=[])

        resp = client.get("/ontologies/hr/object-types")
        assert resp.status_code == 200

    def test_list_object_types_summary(self, client, container):
        svc = _service(container)
        onto = Ontology(
            id="onto1", api_name="hr", display_name="HR", description="", rid="", created_at=_now, updated_at=_now
        )
        svc._metadata.get_ontology = AsyncMock(return_value=onto)

        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        svc._metadata.session.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/ontologies/hr/object-types/summary")
        assert resp.status_code == 200

    def test_get_object_type(self, client, container):
        svc = _service(container)
        svc.get_object_type = AsyncMock(
            return_value=ObjectType(
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
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.get("/ontologies/hr/object-types/employee")
        assert resp.status_code == 200
        assert resp.json()["api_name"] == "employee"


class TestPropertyRoutes:
    def test_add_property(self, client, container):
        svc = _service(container)
        ot = ObjectType(
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
            created_at=_now,
            updated_at=_now,
        )
        svc.get_object_type = AsyncMock(return_value=ot)
        svc.add_property_to_object_type = AsyncMock(
            return_value=PropertyDef(
                id="p1",
                object_type_id="ot1",
                api_name="age",
                display_name="Age",
                data_type="INTEGER",
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.post(
            "/ontologies/hr/object-types/employee/properties",
            json={
                "api_name": "age",
                "display_name": "Age",
                "data_type": "INTEGER",
            },
        )
        assert resp.status_code == 201

    def test_list_properties(self, client, container):
        svc = _service(container)
        ot = ObjectType(
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
            created_at=_now,
            updated_at=_now,
        )
        svc.get_object_type = AsyncMock(return_value=ot)
        svc._metadata.get_properties = AsyncMock(return_value=[])

        resp = client.get("/ontologies/hr/object-types/employee/properties")
        assert resp.status_code == 200


class TestLinkTypeRoutes:
    def test_create_link_type(self, client, container):
        svc = _service(container)
        svc.define_link_type = AsyncMock(
            return_value=LinkTypeDef(
                id="l1",
                ontology_id="onto1",
                api_name="emp_dept",
                display_name="Emp Dept",
                source_object_type_id="ot1",
                target_object_type_id="ot2",
                cardinality="MANY",
                direction="OUTGOING",
                created_at=_now,
                updated_at=_now,
            )
        )

        resp = client.post(
            "/ontologies/hr/link-types",
            json={
                "api_name": "emp_dept",
                "display_name": "Employee Department",
                "source_object_type_id": "ot1",
                "target_object_type_id": "ot2",
                "cardinality": "MANY",
                "direction": "OUTGOING",
            },
        )
        assert resp.status_code == 201

    def test_list_link_types(self, client, container):
        svc = _service(container)
        svc._metadata.get_link_types = AsyncMock(return_value=[])

        resp = client.get("/ontologies/hr/link-types")
        assert resp.status_code == 200


class TestActionTypeListRoute:
    def test_list_action_types(self, client, container):
        svc = _service(container)
        onto = Ontology(
            id="onto1", api_name="hr", display_name="HR", description="", rid="", created_at=_now, updated_at=_now
        )
        svc._metadata.get_ontology = AsyncMock(return_value=onto)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_result.scalars.return_value = mock_scalars
        svc._metadata.session.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/ontologies/hr/action-types")
        assert resp.status_code == 200


class TestDatasetLinkRoute:
    """A1 — PATCH/DELETE /object-types/{type}/dataset-link."""

    def _ot(self) -> ObjectType:
        return ObjectType(
            id="ot1",
            ontology_id="onto1",
            api_name="employee",
            display_name="Employee",
            primary_key="id",
            title_property="name",
            storage_type="MANAGED",
            properties=[
                PropertyDef(
                    id="p1",
                    object_type_id="ot1",
                    api_name="id",
                    display_name="Id",
                    data_type="STRING",
                    is_primary_key=True,
                    created_at=_now,
                    updated_at=_now,
                )
            ],
            created_at=_now,
            updated_at=_now,
        )

    def test_link_dataset_ok(self, client, container):
        svc = _service(container)
        svc.link_dataset = AsyncMock(return_value=self._ot())

        resp = client.patch(
            "/ontologies/hr/object-types/employee/dataset-link",
            json={
                "dataset_api_name": "hr_employee",
                "column_mappings": [{"property_api_name": "id", "column_name": "emp_id"}],
            },
        )
        assert resp.status_code == 200
        svc.link_dataset.assert_awaited_once()
        assert resp.json()["api_name"] == "employee"

    def test_link_dataset_validation_error_422(self, client, container):
        from ontology.core.exceptions import ValidationError

        svc = _service(container)
        svc.link_dataset = AsyncMock(side_effect=ValidationError("storage_type mismatch"))

        resp = client.patch(
            "/ontologies/hr/object-types/employee/dataset-link",
            json={"dataset_api_name": "x", "column_mappings": []},
        )
        assert resp.status_code == 422

    def test_link_dataset_not_found_404(self, client, container):
        from ontology.core.exceptions import NotFoundError

        svc = _service(container)
        svc.link_dataset = AsyncMock(side_effect=NotFoundError("Property", "x"))

        resp = client.patch(
            "/ontologies/hr/object-types/employee/dataset-link",
            json={"dataset_api_name": "x", "column_mappings": []},
        )
        assert resp.status_code == 404

    def test_unlink_dataset_ok(self, client, container):
        svc = _service(container)
        svc.unlink_dataset = AsyncMock(return_value=self._ot())

        resp = client.delete(
            "/ontologies/hr/object-types/employee/dataset-link?property_api_names=id&property_api_names=name"
        )
        assert resp.status_code == 200
        svc.unlink_dataset.assert_awaited_once()
        assert svc.unlink_dataset.await_args.args[2] == ["id", "name"]
