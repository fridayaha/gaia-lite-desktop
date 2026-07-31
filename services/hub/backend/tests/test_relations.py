from fastapi.testclient import TestClient


def _create_item(client: TestClient, name: str, item_type: str) -> dict:
    resp = client.post(
        "/api/hub/items",
        json={"name": name, "type": item_type},
    )
    return resp.json()


def _create_relation(
    client: TestClient,
    source_item_id: str,
    target_item_id: str,
    relation_type: str,
    relation_scope: str = "management",
    required: bool = False,
    description: str | None = None,
) -> dict:
    data = {
        "source_item_id": source_item_id,
        "target_item_id": target_item_id,
        "relation_type": relation_type,
        "relation_scope": relation_scope,
        "required": required,
    }
    if description is not None:
        data["description"] = description
    return client.post("/api/hub/relations", json=data)


class TestCreateRelation:
    def test_create_relation_success(self, client: TestClient):
        agent = _create_item(client, "TestAgent", "agent")
        skill = _create_item(client, "TestSkill", "skill")
        resp = _create_relation(
            client, agent["id"], skill["id"], "uses",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_item_id"] == agent["id"]
        assert data["target_item_id"] == skill["id"]
        assert data["relation_type"] == "uses"
        assert data["relation_scope"] == "management"
        assert data["required"] is False
        assert data["source_item"]["id"] == agent["id"]
        assert data["source_item"]["name"] == "TestAgent"
        assert data["source_item"]["type"] == "agent"
        assert data["target_item"]["id"] == skill["id"]
        assert data["target_item"]["name"] == "TestSkill"
        assert data["target_item"]["type"] == "skill"

    def test_create_relation_runtime_scope(self, client: TestClient):
        agent = _create_item(client, "AgentR", "agent")
        tool = _create_item(client, "ToolR", "tool")
        resp = _create_relation(
            client, agent["id"], tool["id"], "invokes",
            relation_scope="runtime", required=True,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["relation_scope"] == "runtime"
        assert data["required"] is True

    def test_create_relation_missing_source(self, client: TestClient):
        skill = _create_item(client, "SkillX", "skill")
        resp = _create_relation(
            client,
            "00000000-0000-0000-0000-000000000000",
            skill["id"],
            "uses",
        )
        assert resp.status_code == 404

    def test_create_relation_missing_target(self, client: TestClient):
        agent = _create_item(client, "AgentX", "agent")
        resp = _create_relation(
            client,
            agent["id"],
            "00000000-0000-0000-0000-000000000000",
            "uses",
        )
        assert resp.status_code == 404

    def test_create_relation_self_reference(self, client: TestClient):
        agent = _create_item(client, "AgentS", "agent")
        resp = _create_relation(
            client, agent["id"], agent["id"], "uses",
        )
        assert resp.status_code == 400

    def test_create_relation_duplicate(self, client: TestClient):
        agent = _create_item(client, "AgentD", "agent")
        skill = _create_item(client, "SkillD", "skill")
        r1 = _create_relation(client, agent["id"], skill["id"], "uses")
        assert r1.status_code == 201
        r2 = _create_relation(client, agent["id"], skill["id"], "uses")
        assert r2.status_code == 409

    def test_create_relation_different_scope_not_duplicate(self, client: TestClient):
        agent = _create_item(client, "AgentDS", "agent")
        skill = _create_item(client, "SkillDS", "skill")
        r1 = _create_relation(
            client, agent["id"], skill["id"], "uses", relation_scope="management",
        )
        assert r1.status_code == 201
        r2 = _create_relation(
            client, agent["id"], skill["id"], "uses", relation_scope="runtime",
        )
        assert r2.status_code == 201
        assert r1.json()["relation_scope"] == "management"
        assert r2.json()["relation_scope"] == "runtime"

    def test_create_relation_invalid_type_combo(self, client: TestClient):
        mcp = _create_item(client, "MCPInv", "mcp")
        agent = _create_item(client, "AgentInv", "agent")
        resp = _create_relation(
            client, mcp["id"], agent["id"], "invokes",
        )
        assert resp.status_code == 400

    def test_create_all_six_valid_combos(self, client: TestClient):
        agent = _create_item(client, "AgentAll", "agent")
        skill = _create_item(client, "SkillAll", "skill")
        tool = _create_item(client, "ToolAll", "tool")
        mcp = _create_item(client, "MCPAll", "mcp")

        combos = [
            (agent["id"], skill["id"], "uses"),
            (agent["id"], tool["id"], "invokes"),
            (agent["id"], mcp["id"], "depends_on"),
            (skill["id"], tool["id"], "invokes"),
            (skill["id"], mcp["id"], "depends_on"),
            (mcp["id"], tool["id"], "provides"),
        ]
        for src, tgt, rtype in combos:
            resp = _create_relation(client, src, tgt, rtype)
            assert resp.status_code == 201, f"Failed: {rtype}"


class TestGetRelation:
    def test_get_relation_with_embedded_items(self, client: TestClient):
        agent = _create_item(client, "AgentG", "agent")
        skill = _create_item(client, "SkillG", "skill")
        resp = _create_relation(client, agent["id"], skill["id"], "uses")
        rel_id = resp.json()["id"]

        resp = client.get(f"/api/hub/relations/{rel_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == rel_id
        assert data["source_item"]["name"] == "AgentG"
        assert data["target_item"]["name"] == "SkillG"

    def test_get_relation_not_found(self, client: TestClient):
        resp = client.get(
            "/api/hub/relations/00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code == 404


class TestListRelationsByItem:
    def test_list_relations_empty(self, client: TestClient):
        agent = _create_item(client, "AgentEmpty", "agent")
        resp = client.get(f"/api/hub/items/{agent['id']}/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["outgoing"] == []
        assert data["incoming"] == []

    def test_list_relations_outgoing_only(self, client: TestClient):
        agent = _create_item(client, "AgentOut", "agent")
        skill = _create_item(client, "SkillOut", "skill")
        _create_relation(client, agent["id"], skill["id"], "uses")
        resp = client.get(f"/api/hub/items/{agent['id']}/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["outgoing"]) == 1
        assert len(data["incoming"]) == 0
        assert data["outgoing"][0]["relation_type"] == "uses"

    def test_list_relations_incoming_only(self, client: TestClient):
        agent = _create_item(client, "AgentIn", "agent")
        skill = _create_item(client, "SkillIn", "skill")
        _create_relation(client, agent["id"], skill["id"], "uses")
        resp = client.get(f"/api/hub/items/{skill['id']}/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["incoming"]) == 1
        assert len(data["outgoing"]) == 0
        assert data["incoming"][0]["relation_type"] == "uses"

    def test_list_relations_both_directions(self, client: TestClient):
        agent = _create_item(client, "AgentBoth", "agent")
        skill = _create_item(client, "SkillBoth", "skill")
        tool = _create_item(client, "ToolBoth", "tool")
        _create_relation(client, agent["id"], skill["id"], "uses")
        _create_relation(client, skill["id"], tool["id"], "invokes")

        resp = client.get(f"/api/hub/items/{skill['id']}/relations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["outgoing"]) == 1
        assert data["outgoing"][0]["relation_type"] == "invokes"
        assert len(data["incoming"]) == 1
        assert data["incoming"][0]["relation_type"] == "uses"

    def test_list_relations_item_not_found(self, client: TestClient):
        resp = client.get(
            "/api/hub/items/00000000-0000-0000-0000-000000000000/relations"
        )
        assert resp.status_code == 404


class TestDeleteRelation:
    def test_delete_relation_success(self, client: TestClient):
        agent = _create_item(client, "AgentDel", "agent")
        skill = _create_item(client, "SkillDel", "skill")
        resp = _create_relation(client, agent["id"], skill["id"], "uses")
        rel_id = resp.json()["id"]

        resp = client.delete(f"/api/hub/relations/{rel_id}")
        assert resp.status_code == 204

        resp = client.get(f"/api/hub/relations/{rel_id}")
        assert resp.status_code == 404

    def test_delete_relation_not_found(self, client: TestClient):
        resp = client.delete(
            "/api/hub/relations/00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code == 404
