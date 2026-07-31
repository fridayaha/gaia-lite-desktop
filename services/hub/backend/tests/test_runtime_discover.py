import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


def _create_item(client: TestClient, name: str, item_type: str) -> str:
    resp = client.post(
        "/api/hub/items",
        json={"name": name, "type": item_type},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_version(
    client: TestClient, item_id: str, version: str = "1.0.0", **extra
) -> str:
    payload = {"hub_item_id": item_id, "version": version, **extra}
    resp = client.post(
        f"/api/hub/items/{item_id}/versions", json=payload
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _publish(client: TestClient, item_id: str, version_id: str):
    resp = client.post(
        f"/api/hub/versions/{version_id}/submit-review",
        json={"operator": "dev"},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/hub/versions/{version_id}/approve",
        json={"operator": "approver", "comment": "ok"},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/hub/versions/{version_id}/publish",
        json={"operator": "approver"},
    )
    assert resp.status_code == 200


def _create_relation(
    client: TestClient,
    source_item_id: str,
    target_item_id: str,
    relation_type: str,
    relation_scope: str = "management",
    required: bool = False,
) -> str:
    resp = client.post(
        "/api/hub/relations",
        json={
            "source_item_id": source_item_id,
            "target_item_id": target_item_id,
            "relation_type": relation_type,
            "relation_scope": relation_scope,
            "required": required,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _setup_published_item(
    client: TestClient, name: str, item_type: str = "agent"
) -> tuple[str, str]:
    item_id = _create_item(client, name, item_type)
    vid = _create_version(client, item_id, "1.0.0")
    _publish(client, item_id, vid)
    return item_id, vid


class TestDiscover:
    def test_discover_returns_published_discoverable(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "DAgent")
        resp = client.get("/api/runtime/capabilities/discover")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        ids = [i["id"] for i in data["items"]]
        assert item_id in ids

    def test_discover_excludes_draft(self, client: TestClient):
        agent = _create_item(client, "DraftAgent", "agent")
        _create_version(client, agent, "1.0.0")
        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert agent not in ids

    def test_discover_excludes_disabled(self, client: TestClient):
        item_id, vid = _setup_published_item(client, "DisAgent")
        client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )
        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert item_id not in ids

    def test_discover_excludes_archived(self, client: TestClient):
        item_id, vid = _setup_published_item(client, "ArchAgent")
        client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )
        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert item_id not in ids

    def test_discover_excludes_blocking_item(self, client: TestClient):
        item_id, vid = _setup_published_item(client, "BlockAgent")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"risk_level": "blocking"},
        )
        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert item_id not in ids

    def test_discover_excludes_version_blocking(
        self, client: TestClient, db_session: Session
    ):
        item_id, vid = _setup_published_item(client, "VBlockAgent")
        from app.core.enums import RiskLevel

        version = db_session.get(HubItemVersion, uuid.UUID(vid))
        version.risk_level = RiskLevel.blocking
        db_session.commit()

        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert item_id not in ids

    def test_discover_excludes_unpublished_version(self, client: TestClient):
        item_id = _create_item(client, "UnpubV", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        client.post(
            f"/api/hub/items/{item_id}/submit",
            json={"operator": "dev"},
        )
        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        ids = [i["id"] for i in data["items"]]
        assert item_id not in ids

    def test_discover_total_excludes_invalid_version(
        self, client: TestClient, db_session: Session
    ):
        from app.core.enums import HubItemStatus, RiskLevel

        _setup_published_item(client, "ValidAgent")

        bad_id = uuid.uuid4()
        bad_vid = uuid.uuid4()
        bad_item = HubItem(
            id=bad_id,
            name="BadAgent",
            type="agent",
            status=HubItemStatus.published,
            discoverable=True,
            force_disabled=False,
            current_version_id=bad_vid,
            risk_level=RiskLevel.low,
        )
        db_session.add(bad_item)
        db_session.commit()

        resp = client.get("/api/runtime/capabilities/discover")
        data = resp.json()
        assert data["total"] == 1

    def test_discover_filter_by_type(self, client: TestClient):
        _setup_published_item(client, "AgentA", "agent")
        _setup_published_item(client, "SkillA", "skill")
        resp = client.get(
            "/api/runtime/capabilities/discover?type=agent"
        )
        data = resp.json()
        for item in data["items"]:
            assert item["type"] == "agent"
        assert data["total"] == 1

    def test_discover_filter_by_keyword(self, client: TestClient):
        _setup_published_item(client, "UniqueAgent", "agent")
        _setup_published_item(client, "UniqueSkill", "skill")
        resp = client.get(
            "/api/runtime/capabilities/discover?keyword=UniqueAg"
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "UniqueAgent"

    def test_discover_pagination(self, client: TestClient):
        for i in range(5):
            _setup_published_item(client, f"PageAgent{i}", "agent")
        resp = client.get(
            "/api/runtime/capabilities/discover?limit=2&offset=0"
        )
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5

    def test_discover_risk_level_max(self, client: TestClient):
        _setup_published_item(client, "LowAgent", "agent")
        mid_id, mid_vid = _setup_published_item(
            client, "MidAgent", "agent"
        )
        client.put(
            f"/api/hub/items/{mid_id}",
            json={"risk_level": "medium"},
        )
        resp = client.get(
            "/api/runtime/capabilities/discover?risk_level_max=low"
        )
        data = resp.json()
        for item in data["items"]:
            assert item["risk_level"] == "low"


class TestResolve:
    def test_resolve_published_capability(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "ResAgent", "agent")
        resp = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "ResAgent"
        assert data["type"] == "agent"
        assert data["status"] == "published"
        assert data["version"] == "1.0.0"
        assert data["risk_level"] == "low"

    def test_resolve_not_found(self, client: TestClient):
        resp = client.get(
            f"/api/runtime/capabilities/{uuid.uuid4()}/resolve"
        )
        assert resp.status_code == 404

    def test_resolve_draft_returns_404(self, client: TestClient):
        item_id = _create_item(client, "DraftRes", "agent")
        _create_version(client, item_id, "1.0.0")
        resp = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        assert resp.status_code == 404

    def test_resolve_disabled_returns_404(self, client: TestClient):
        item_id, vid = _setup_published_item(client, "DisRes", "agent")
        client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )
        resp = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        assert resp.status_code == 404

    def test_resolve_blocking_returns_404(self, client: TestClient):
        item_id, vid = _setup_published_item(client, "BlkRes", "agent")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"risk_level": "blocking"},
        )
        resp = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        assert resp.status_code == 404

    def test_resolve_includes_runtime_relations(
        self, client: TestClient
    ):
        agent_id, _ = _setup_published_item(client, "AgentRel", "agent")
        skill_id, _ = _setup_published_item(client, "SkillRel", "skill")
        _create_relation(
            client,
            agent_id,
            skill_id,
            "uses",
            relation_scope="runtime",
            required=False,
        )
        resp = client.get(
            f"/api/runtime/capabilities/{agent_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["relations"]) == 1
        assert data["relations"][0]["relation_type"] == "uses"
        assert data["relations"][0]["target_item"]["name"] == "SkillRel"
        assert data["relations"][0]["required"] is False

    def test_resolve_excludes_management_relations(
        self, client: TestClient
    ):
        agent_id, _ = _setup_published_item(client, "AgentMgmt", "agent")
        skill_id, _ = _setup_published_item(
            client, "SkillMgmt", "skill"
        )
        _create_relation(
            client,
            agent_id,
            skill_id,
            "uses",
            relation_scope="management",
            required=False,
        )
        resp = client.get(
            f"/api/runtime/capabilities/{agent_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["relations"]) == 0

    def test_resolve_required_dependency_unavailable_409(
        self, client: TestClient
    ):
        agent_id, _ = _setup_published_item(client, "Agent409", "agent")
        skill_id = _create_item(client, "Skill409", "skill")
        _create_version(client, skill_id, "1.0.0")
        _create_relation(
            client,
            agent_id,
            skill_id,
            "uses",
            relation_scope="runtime",
            required=True,
        )
        resp = client.get(
            f"/api/runtime/capabilities/{agent_id}/resolve"
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "required dependency not available" in data["detail"]

    def test_resolve_409_does_not_leak_target_name(
        self, client: TestClient
    ):
        agent_id, _ = _setup_published_item(client, "Agent409b", "agent")
        skill_id = _create_item(client, "SecretSkill", "skill")
        _create_version(client, skill_id, "1.0.0")
        _create_relation(
            client,
            agent_id,
            skill_id,
            "uses",
            relation_scope="runtime",
            required=True,
        )
        resp = client.get(
            f"/api/runtime/capabilities/{agent_id}/resolve"
        )
        assert resp.status_code == 409
        assert "SecretSkill" not in resp.json()["detail"]

    def test_resolve_optional_dependency_unavailable_warns_at_depth_1(
        self, client: TestClient
    ):
        agent_id, _ = _setup_published_item(client, "AgentOpt", "agent")
        skill_id = _create_item(client, "SkillOpt", "skill")
        _create_version(client, skill_id, "1.0.0")
        _create_relation(
            client,
            agent_id,
            skill_id,
            "uses",
            relation_scope="runtime",
            required=False,
        )
        resp = client.get(
            f"/api/runtime/capabilities/{agent_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["relations"]) == 0
        assert len(data["dependencies"]) == 0
        warnings = data["dependency_warnings"]
        assert len(warnings) >= 1
        assert warnings[0]["warning_type"] == "optional_unavailable"

    def test_resolve_returns_manifest_and_schema(
        self, client: TestClient
    ):
        item_id, vid = _setup_published_item(client, "FullAgent", "agent")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "manifest_json" in data
        assert "config_json" in data
        assert "input_schema" in data
        assert "output_schema" in data
        assert "permission_json" in data
        assert "runtime_compatibility" in data


class TestBadFilter:
    def test_bad_type_422(self, client: TestClient):
        resp = client.get(
            "/api/runtime/capabilities/discover?type=badtype"
        )
        assert resp.status_code == 422

    def test_bad_risk_422(self, client: TestClient):
        resp = client.get(
            "/api/runtime/capabilities/discover?risk_level_max=critical"
        )
        assert resp.status_code == 422


class TestResolveDepth:
    def test_depth_default_1_backward_compat(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "DepAgent", "agent")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "dependencies" in data
        assert "dependency_warnings" in data
        assert data["dependencies"] == []
        assert data["dependency_warnings"] == []

    def test_depth_1_returns_one_layer_dependencies(
        self, client: TestClient
    ):
        a_id, _ = _setup_published_item(client, "D1LA", "agent")
        b_id, _ = _setup_published_item(client, "D1LB", "skill")
        c_id, _ = _setup_published_item(client, "D1LC", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "invokes",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=1"
        )
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["relations"]) == 1
        assert len(data["dependencies"]) == 1
        dep = data["dependencies"][0]
        assert dep["item"]["name"] == "D1LB"
        assert dep["depth"] == 1
        assert dep["available"] is True
        assert "D1LC" not in [d["item"]["name"] for d in data["dependencies"]]

    def test_depth_2_expands_two_levels(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "Depth2A", "agent")
        b_id, _ = _setup_published_item(client, "Depth2B", "skill")
        c_id, _ = _setup_published_item(client, "Depth2C", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "invokes",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 200
        data = resp.json()

        deps = data["dependencies"]
        assert len(deps) == 2

        b_dep = next(d for d in deps if d["item"]["name"] == "Depth2B")
        assert b_dep["depth"] == 1
        assert str(b_dep["source_item_id"]) == a_id
        assert b_dep["relation_type"] == "uses"
        assert b_dep["available"] is True

        c_dep = next(d for d in deps if d["item"]["name"] == "Depth2C")
        assert c_dep["depth"] == 2
        assert str(c_dep["source_item_id"]) == b_id
        assert c_dep["relation_type"] == "invokes"
        assert c_dep["available"] is True

    def test_depth_3_expands_three_levels(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "Depth3A", "agent")
        b_id, _ = _setup_published_item(client, "Depth3B", "skill")
        c_id, _ = _setup_published_item(client, "Depth3C", "mcp")
        d_id, _ = _setup_published_item(client, "Depth3D", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "depends_on",
                         relation_scope="runtime", required=False)
        _create_relation(client, c_id, d_id, "provides",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=3"
        )
        assert resp.status_code == 200
        data = resp.json()

        depths = {d["item"]["name"]: d["depth"] for d in data["dependencies"]}
        assert depths.get("Depth3B") == 1
        assert depths.get("Depth3C") == 2
        assert depths.get("Depth3D") == 3

    def test_depth_0_returns_422(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "Depth0A", "agent")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve?depth=0"
        )
        assert resp.status_code == 422

    def test_depth_4_returns_422(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "Depth4A", "agent")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve?depth=4"
        )
        assert resp.status_code == 422

    def test_depth_1_relations_unchanged(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "Rel1A", "agent")
        b_id, _ = _setup_published_item(client, "Rel1B", "skill")
        c_id, _ = _setup_published_item(client, "Rel1C", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "invokes",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["relations"]) == 1
        assert data["relations"][0]["target_item"]["name"] == "Rel1B"

    def test_max_depth_reached_warning(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "MaxDA", "agent")
        b_id, _ = _setup_published_item(client, "MaxDB", "skill")
        c_id, _ = _setup_published_item(client, "MaxDC", "mcp")
        d_id, _ = _setup_published_item(client, "MaxDD", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "depends_on",
                         relation_scope="runtime", required=False)
        _create_relation(client, c_id, d_id, "provides",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 200
        data = resp.json()

        warnings = data["dependency_warnings"]
        max_depth_warnings = [
            w for w in warnings
            if w["warning_type"] == "max_depth_reached"
        ]
        assert len(max_depth_warnings) >= 1
        for w in max_depth_warnings:
            assert "max depth reached" in w["detail"].lower()


class TestResolveCycle:
    def test_cycle_detected_no_infinite_loop(
        self, client: TestClient, db_session: Session
    ):
        a_id, _ = _setup_published_item(client, "CycleA", "agent")
        b_id, _ = _setup_published_item(client, "CycleB", "skill")

        from app.models.hub_item_relation import HubItemRelation

        rel1 = HubItemRelation(
            source_item_id=uuid.UUID(a_id),
            target_item_id=uuid.UUID(b_id),
            relation_type="uses",
            relation_scope="runtime",
            required=False,
        )
        rel2 = HubItemRelation(
            source_item_id=uuid.UUID(b_id),
            target_item_id=uuid.UUID(a_id),
            relation_type="depends_on",
            relation_scope="runtime",
            required=False,
        )
        db_session.add_all([rel1, rel2])
        db_session.commit()

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=3"
        )
        assert resp.status_code == 200
        data = resp.json()

        cycle_warnings = [
            w for w in data["dependency_warnings"]
            if w["warning_type"] == "cycle_detected"
        ]
        assert len(cycle_warnings) >= 1
        for w in cycle_warnings:
            assert "cycle detected" in w["detail"].lower()

    def test_diamond_dependency_no_false_cycle(
        self, client: TestClient
    ):
        a_id, _ = _setup_published_item(client, "DiaA", "agent")
        b_id, _ = _setup_published_item(client, "DiaB", "skill")
        c_id, _ = _setup_published_item(client, "DiaC", "skill")
        d_id, _ = _setup_published_item(client, "DiaD", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, a_id, c_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, d_id, "invokes",
                         relation_scope="runtime", required=False)
        _create_relation(client, c_id, d_id, "invokes",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=3"
        )
        assert resp.status_code == 200
        data = resp.json()

        cycle_warnings = [
            w for w in data["dependency_warnings"]
            if w["warning_type"] == "cycle_detected"
        ]
        assert len(cycle_warnings) == 0

        d_deps = [
            d for d in data["dependencies"]
            if d["item"]["name"] == "DiaD"
        ]
        assert len(d_deps) >= 2


class TestResolveWarnings:
    def test_optional_unavailable_warning_at_depth_2(
        self, client: TestClient
    ):
        a_id, _ = _setup_published_item(client, "OptWarnA", "agent")
        b_id = _create_item(client, "OptWarnB", "skill")
        _create_version(client, b_id, "1.0.0")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 200
        data = resp.json()

        warnings = data["dependency_warnings"]
        assert len(warnings) >= 1
        warning = warnings[0]
        assert warning["warning_type"] == "optional_unavailable"

        deps = data["dependencies"]
        dep_names = [d["item"]["name"] for d in deps]
        assert "OptWarnB" not in dep_names

    def test_required_unavailable_still_409(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "Req409A", "agent")
        b_id = _create_item(client, "Req409B", "skill")
        _create_version(client, b_id, "1.0.0")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=True)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 409

    def test_management_scope_not_in_dependencies(
        self, client: TestClient
    ):
        a_id, _ = _setup_published_item(client, "MgmtA", "agent")
        b_id, _ = _setup_published_item(client, "MgmtB", "skill")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="management", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 200
        data = resp.json()

        dep_names = [d["item"]["name"] for d in data["dependencies"]]
        assert "MgmtB" not in dep_names
        assert len(data["relations"]) == 0

    def test_relations_field_only_root_level(self, client: TestClient):
        a_id, _ = _setup_published_item(client, "RootRelA", "agent")
        b_id, _ = _setup_published_item(client, "RootRelB", "skill")
        c_id, _ = _setup_published_item(client, "RootRelC", "tool")

        _create_relation(client, a_id, b_id, "uses",
                         relation_scope="runtime", required=False)
        _create_relation(client, b_id, c_id, "invokes",
                         relation_scope="runtime", required=False)

        resp = client.get(
            f"/api/runtime/capabilities/{a_id}/resolve?depth=2"
        )
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["relations"]) == 1
        assert data["relations"][0]["target_item"]["name"] == "RootRelB"

        dep_names = [d["item"]["name"] for d in data["dependencies"]]
        assert "RootRelB" in dep_names
        assert "RootRelC" in dep_names


class TestToolDefinition:
    def _setup_published_tool(
        self, client: TestClient, name: str, **version_extra
    ) -> tuple[str, str]:
        item_id = _create_item(client, name, "tool")
        extra = {"input_schema": {"type": "object", "properties": {}}, **version_extra}
        vid = _create_version(client, item_id, "1.0.0", **extra)
        _publish(client, item_id, vid)
        return item_id, vid

    def test_published_tool_exports_successfully(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD1Tool")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "function"
        assert data["function"]["name"] == "td1tool"

    def test_function_name_equals_tool_name(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD2SimpleTool")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 200
        assert resp.json()["function"]["name"] == "td2simpletool"

    def test_function_description_equals_tool_description(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD3Described")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"description": "A test tool for TD3"},
        )
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 200
        assert resp.json()["function"]["description"] == "A test tool for TD3"

    def test_parameters_equal_normalized_input_schema(self, client: TestClient):
        item_id, _ = self._setup_published_tool(
            client, "TD4Params",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        )
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 200
        params = resp.json()["function"]["parameters"]
        assert params["type"] == "object"
        assert "x" in params["properties"]

    def test_non_tool_agent_returns_404(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "TD5Agent", "agent")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_non_tool_skill_returns_404(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "TD6Skill", "skill")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_non_tool_mcp_returns_404(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "TD7Mcp", "mcp")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_draft_tool_returns_404(self, client: TestClient):
        item_id = _create_item(client, "TD8Draft", "tool")
        _create_version(client, item_id, "1.0.0",
                        input_schema={"type": "object", "properties": {}})
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_blocking_tool_returns_404(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD9Block")
        client.put(
            f"/api/hub/items/{item_id}",
            json={"risk_level": "blocking"},
        )
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_disabled_tool_returns_404(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD10Dis")
        client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 404

    def test_missing_input_schema_returns_400(self, client: TestClient):
        item_id, _ = _setup_published_item(client, "TD11Null", "tool")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 400

    def test_input_schema_not_object_returns_400(self, client: TestClient):
        item_id = _create_item(client, "TD12Str", "tool")
        vid = _create_version(
            client, item_id, "1.0.0",
            input_schema={"type": "string"},
        )
        _publish(client, item_id, vid)
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 400

    def test_no_permission_json_in_response(self, client: TestClient):
        item_id, _ = self._setup_published_tool(client, "TD13Perm")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "permission" not in str(data).lower()
        assert "output_schema" not in data
        assert "runtime_compatibility" not in data

    def test_optional_policy_deny_returns_404(
        self, client: TestClient, db_session: Session
    ):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.policies.capability_access import CapabilityAccessPolicy
        from app.core.auth_context import AuthContext

        class DenyAllPolicy(CapabilityAccessPolicy):
            version = "deny"
            def can_discover(self, item, ver, ctx): return False
            def can_resolve(self, item, ver, ctx): return False

        item_id, _ = self._setup_published_tool(client, "TD14Policy")
        svc = RuntimeDiscoverService(db_session, policy=DenyAllPolicy())
        try:
            svc.build_tool_definition(uuid.UUID(item_id), AuthContext())
            assert False
        except Exception as e:
            assert "capability not available" in str(e)
