"""LiteLLM 模型管理 pricing 展示 + 编辑接口测试。

覆盖：
- litellm_client.list_models 注入 input_cost_per_1m_tokens / output_cost_per_1m_tokens（per token → per 1M 转换）
- litellm_client.update_model 接受 pricing kwarg 写进 model_info（per 1M → per token 转换）
- litellm_client.update_model 不传 pricing 时向后兼容（payload 只含 model_info.id）
- API endpoint PUT /models/{id}/price 路由 + 权限 + 参数传递 + LiteLLM 异常透传
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import litellm_client
from app.services.litellm_client import LitellmError


# ── litellm_client.list_models pricing 注入 ────────────────────


@pytest.mark.asyncio
async def test_list_models_injects_pricing_from_per_token(monkeypatch):
    """LiteLLM 返回 input_cost_per_token=0.00000028 → manager 注入 input_cost_per_1m_tokens=0.28。"""
    mock_response = {
        "data": [
            {
                "model_name": "deepseek-v4-flash",
                "litellm_params": {"model": "deepseek/deepseek-chat"},
                "model_info": {
                    "id": "abc-123",
                    "input_cost_per_token": 0.00000028,
                    "output_cost_per_token": 0.00000042,
                },
            }
        ]
    }
    monkeypatch.setattr(litellm_client, "_request", AsyncMock(return_value=mock_response))

    result = await litellm_client.list_models()

    assert len(result) == 1
    dep = result[0]
    assert dep["input_cost_per_1m_tokens"] == 0.28
    assert dep["output_cost_per_1m_tokens"] == 0.42
    # 原始 model_info 字段保留（不破坏既有调用方）
    assert dep["model_info"]["input_cost_per_token"] == 0.00000028


@pytest.mark.asyncio
async def test_list_models_pricing_none_when_not_configured(monkeypatch):
    """LiteLLM 返回 None（自定义 model 名查不到 pricing）→ manager 注入 None。"""
    mock_response = {
        "data": [
            {
                "model_name": "custom-model",
                "litellm_params": {"model": "custom-model"},
                "model_info": {"id": "abc-456"},  # 无 input_cost_per_token 字段
            }
        ]
    }
    monkeypatch.setattr(litellm_client, "_request", AsyncMock(return_value=mock_response))

    result = await litellm_client.list_models()

    dep = result[0]
    assert dep["input_cost_per_1m_tokens"] is None
    assert dep["output_cost_per_1m_tokens"] is None


@pytest.mark.asyncio
async def test_list_models_partial_pricing(monkeypatch):
    """只有 input 价格（output 未配置）→ input 非 None，output None。"""
    mock_response = {
        "data": [
            {
                "model_name": "partial",
                "model_info": {
                    "id": "x",
                    "input_cost_per_token": 0.000001,
                    "output_cost_per_token": None,
                },
            }
        ]
    }
    monkeypatch.setattr(litellm_client, "_request", AsyncMock(return_value=mock_response))

    result = await litellm_client.list_models()

    dep = result[0]
    assert dep["input_cost_per_1m_tokens"] == 1.0
    assert dep["output_cost_per_1m_tokens"] is None


@pytest.mark.asyncio
async def test_list_models_empty_deployments(monkeypatch):
    """空 deployment 列表 → 返回空 list，不抛异常。"""
    monkeypatch.setattr(litellm_client, "_request", AsyncMock(return_value={"data": []}))
    result = await litellm_client.list_models()
    assert result == []


@pytest.mark.asyncio
async def test_list_models_zero_pricing_preserved(monkeypatch):
    """明确设为 0（免费 model）→ 注入 0.0，不是 None。"""
    mock_response = {
        "data": [
            {
                "model_name": "free-model",
                "model_info": {
                    "id": "free-1",
                    "input_cost_per_token": 0.0,
                    "output_cost_per_token": 0.0,
                },
            }
        ]
    }
    monkeypatch.setattr(litellm_client, "_request", AsyncMock(return_value=mock_response))
    result = await litellm_client.list_models()
    dep = result[0]
    assert dep["input_cost_per_1m_tokens"] == 0.0
    assert dep["output_cost_per_1m_tokens"] == 0.0


# ── litellm_client.update_model pricing payload ────────────────


@pytest.mark.asyncio
async def test_update_model_with_pricing_converts_per_1m_to_per_token(monkeypatch):
    """传 input_cost_per_1m_tokens=0.28 → PATCH /model/{id}/update payload.litellm_params.input_cost_per_token=0.00000028。

    pricing 必须放在 litellm_params 里（不是 model_info）—— LiteLLM /model/info 返回的
    model_info.input_cost_per_token 是从 litellm_params 镜像出来的，写 model_info
    虽然返回 200 但 /model/info 看不到更新。
    """
    captured: dict = {}

    async def _fake_request(method, path, *, json=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = json
        return {"id": "abc-123"}

    monkeypatch.setattr(litellm_client, "_request", _fake_request)

    await litellm_client.update_model(
        "abc-123",
        input_cost_per_1m_tokens=0.28,
        output_cost_per_1m_tokens=0.42,
    )

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/model/abc-123/update"
    # pricing 字段在 litellm_params 里，不在 model_info
    assert abs(captured["payload"]["litellm_params"]["input_cost_per_token"] - 0.00000028) < 1e-15
    assert abs(captured["payload"]["litellm_params"]["output_cost_per_token"] - 0.00000042) < 1e-15
    assert "model_info" not in captured["payload"]


@pytest.mark.asyncio
async def test_update_model_without_pricing_backward_compatible(monkeypatch):
    """现有调用方只传 model_id + litellm_params → POST /model/update，向后兼容。"""
    captured: dict = {}

    async def _fake_request(method, path, *, json=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = json
        return {"id": "x"}

    monkeypatch.setattr(litellm_client, "_request", _fake_request)

    await litellm_client.update_model(
        "model-id-1",
        {"model": "deepseek/deepseek-chat", "api_base": "https://example.com"},
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/model/update"
    assert captured["payload"]["model_info"] == {"id": "model-id-1"}
    assert captured["payload"]["litellm_params"]["model"] == "deepseek/deepseek-chat"
    assert captured["payload"]["litellm_params"]["api_base"] == "https://example.com"
    # 没有 pricing 键
    assert "input_cost_per_token" not in captured["payload"]["model_info"]
    assert "output_cost_per_token" not in captured["payload"]["model_info"]


@pytest.mark.asyncio
async def test_update_model_with_params_and_pricing_combined(monkeypatch):
    """同时传 litellm_params + pricing → pricing 走 PATCH，litellm_params 路径未使用。

    当前实现：pricing 走 PATCH /model/{id}/update（pricing 字段放 litellm_params），
    调用方传入的 litellm_params 字段被忽略（若要同时更新上游参数和 pricing，需分别调两次）。
    """
    captured: dict = {}

    async def _fake_request(method, path, *, json=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = json
        return {"id": "x"}

    monkeypatch.setattr(litellm_client, "_request", _fake_request)

    await litellm_client.update_model(
        "model-1",
        {"api_key": "sk-xxx"},
        input_cost_per_1m_tokens=1.5,
        output_cost_per_1m_tokens=6.0,
    )

    # pricing 走 PATCH
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/model/model-1/update"
    # pricing 字段在 litellm_params 里
    assert captured["payload"]["litellm_params"]["input_cost_per_token"] == 1.5e-6
    assert captured["payload"]["litellm_params"]["output_cost_per_token"] == 6.0e-6
    # 调用方传入的 api_key 字段被忽略（pricing 路径不传用户 litellm_params）
    assert "api_key" not in captured["payload"]["litellm_params"]
    assert "model_info" not in captured["payload"]


@pytest.mark.asyncio
async def test_update_model_pricing_zero_explicit(monkeypatch):
    """传 0 表示明确设为免费 → payload.litellm_params.input_cost_per_token=0.0（不是不传）。"""
    captured: dict = {}

    async def _fake_request(method, path, *, json=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = json
        return {"id": "x"}

    monkeypatch.setattr(litellm_client, "_request", _fake_request)

    await litellm_client.update_model("m1", input_cost_per_1m_tokens=0.0)

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/model/m1/update"
    assert captured["payload"]["litellm_params"]["input_cost_per_token"] == 0.0
    # output 未传 → 不写入
    assert "output_cost_per_token" not in captured["payload"]["litellm_params"]


@pytest.mark.asyncio
async def test_update_model_pricing_only_auto_fills_litellm_params_model(monkeypatch):
    """[回归保护] pricing 走 PATCH /model/{id}/update 路径不触发 GET /model/info。

    切到 PATCH 路径后不再需要 GET /model/info 补全 litellm_params.model，
    pricing 字段直接放 litellm_params 即可，LiteLLM 不要求 model 字段。
    """
    captured: dict = {}

    async def _fake_request(method, path, *, json=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = json
        if method == "GET":
            raise AssertionError("PATCH pricing path should not trigger GET /model/info")
        return {"id": "abc-789"}

    monkeypatch.setattr(litellm_client, "_request", _fake_request)

    await litellm_client.update_model(
        "abc-789", input_cost_per_1m_tokens=0.28, output_cost_per_1m_tokens=0.42
    )

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/model/abc-789/update"
    assert abs(captured["payload"]["litellm_params"]["input_cost_per_token"] - 0.00000028) < 1e-15
    assert abs(captured["payload"]["litellm_params"]["output_cost_per_token"] - 0.00000042) < 1e-15


@pytest.mark.asyncio
async def test_update_model_no_params_no_pricing_raises():
    """既不传 litellm_params 也不传 pricing → 报 ValueError（避免误调）。"""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="需至少传"):
        await litellm_client.update_model("x")


# ── API endpoint PUT /models/{id}/price ────────────────────────


def _make_platform_admin_user():
    """构造 mock user，is_platform_admin 返回 True（含 litellm:model:manage 权限）。"""
    user = MagicMock()
    user.id = "00000000-0000-0000-0000-000000000001"
    user.username = "admin"
    perm = MagicMock()
    perm.code = "litellm:model:manage"
    role = MagicMock()
    role.permissions = [perm]
    role.name = "平台管理员"
    user.roles = [role]
    return user


@pytest.mark.asyncio
async def test_update_model_price_endpoint_passes_pricing_to_client(monkeypatch):
    """PUT /models/{id}/price → litellm_client.update_model 接收 per_1m 参数 + 写 operation_log。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user, is_platform_admin
    from pkg.common.database import get_db

    user = _make_platform_admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    # is_platform_admin 在 require_permission 内通过 user_permission_codes 判断，mock user 含权限码即可
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda u: True)

    captured: dict = {}

    async def _fake_update_model(model_id, litellm_params=None, *, input_cost_per_1m_tokens=None, output_cost_per_1m_tokens=None):
        captured["model_id"] = model_id
        captured["input"] = input_cost_per_1m_tokens
        captured["output"] = output_cost_per_1m_tokens
        captured["litellm_params"] = litellm_params
        return {"id": model_id, "updated": True}

    monkeypatch.setattr(litellm_client, "update_model", _fake_update_model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/manager/litellm/models/abc-123/price",
            json={"input_cost_per_1m_tokens": 0.28, "output_cost_per_1m_tokens": 0.42},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert captured["model_id"] == "abc-123"
    assert captured["input"] == 0.28
    assert captured["output"] == 0.42
    assert captured["litellm_params"] is None  # pricing 接口不传 litellm_params


@pytest.mark.asyncio
async def test_update_model_price_endpoint_partial_pricing(monkeypatch):
    """只传 input_cost_per_1m_tokens → output 为 None（不变）。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    user = _make_platform_admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda u: True)

    captured: dict = {}

    async def _fake_update_model(model_id, litellm_params=None, *, input_cost_per_1m_tokens=None, output_cost_per_1m_tokens=None):
        captured["input"] = input_cost_per_1m_tokens
        captured["output"] = output_cost_per_1m_tokens
        return {"id": model_id}

    monkeypatch.setattr(litellm_client, "update_model", _fake_update_model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/manager/litellm/models/m1/price",
            json={"input_cost_per_1m_tokens": 1.5},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert captured["input"] == 1.5
    assert captured["output"] is None


@pytest.mark.asyncio
async def test_update_model_price_endpoint_no_permission_returns_403(monkeypatch):
    """非平台管理员且无 litellm:model:manage 权限 → 403。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    # 无权限的普通用户
    user = MagicMock()
    user.id = "00000000-0000-0000-0000-000000000002"
    user.roles = []  # 无任何角色
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/manager/litellm/models/m1/price",
            json={"input_cost_per_1m_tokens": 1.0},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_model_price_endpoint_litellm_error_propagates(monkeypatch):
    """LiteLLM 返回错误（如 model_id 不存在）→ HTTPException 透传。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    user = _make_platform_admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda u: True)

    async def _fake_update_model(*a, **kw):
        raise LitellmError("model not found", 404)

    monkeypatch.setattr(litellm_client, "update_model", _fake_update_model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.put(
            "/api/manager/litellm/models/non-existent/price",
            json={"input_cost_per_1m_tokens": 1.0},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 404
    assert "model not found" in resp.json()["detail"]


# ── POST /models — operation_log target_id 修复回归 ────────────


@pytest.mark.asyncio
async def test_create_model_endpoint_writes_model_id_as_target_id(monkeypatch):
    """成功创建 deployment → log_operation.target_id 应为 LiteLLM 返回的 model_id (UUID)，
    不能传 model_name（model_name 非 UUID 格式时 operation_logs.target_id UUID 列会 500）。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    user = _make_platform_admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda u: True)

    captured: dict = {}

    async def _fake_create_model(model_name, params, model_info=None):
        captured["model_name"] = model_name
        captured["model_info"] = model_info
        return {"model_id": "cce12b50-efee-470f-a4c5-c57820f84b06", "model_name": model_name}

    async def _fake_log_operation(db, *, actor_id, action, target_type, target_id, **kw):
        captured["target_id"] = target_id
        captured["action"] = action

    async def _fake_commit():
        return None

    db_mock = AsyncMock()
    db_mock.commit = _fake_commit

    monkeypatch.setattr(litellm_client, "create_model", _fake_create_model)
    monkeypatch.setattr("app.api.litellm.log_operation", _fake_log_operation)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/manager/litellm/models",
            json={"model_name": "test-pricing-224008", "model": "openai/gpt-4o",
                  "api_key": "sk-fake", "api_base": "http://x", "custom_llm_provider": "openai"},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 201
    # 关键断言：target_id 是 LiteLLM 返回的 UUID，不是用户输入的 model_name
    assert captured["target_id"] == "cce12b50-efee-470f-a4c5-c57820f84b06"
    assert captured["target_id"] != captured["model_name"]


@pytest.mark.asyncio
async def test_create_model_endpoint_failure_writes_none_target_id(monkeypatch):
    """LiteLLM 创建失败 → log_operation.target_id=None（拿不到 model_id），
    detail 里仍带 model_name + error 供审计回溯。"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    user = _make_platform_admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda u: True)

    captured: dict = {}

    async def _fake_create_model(model_name, params, model_info=None):
        raise LitellmError("upstream error", 400)

    async def _fake_log_operation(db, *, actor_id, action, target_type, target_id, **kw):
        captured["target_id"] = target_id
        captured["detail"] = kw.get("detail")
        captured["status"] = kw.get("status")

    monkeypatch.setattr(litellm_client, "create_model", _fake_create_model)
    monkeypatch.setattr("app.api.litellm.log_operation", _fake_log_operation)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/manager/litellm/models",
            json={"model_name": "any-name", "model": "openai/gpt-4o",
                  "api_key": "sk-fake", "custom_llm_provider": "openai"},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert captured["target_id"] is None
    assert captured["status"] == "failure"
    assert captured["detail"]["model_name"] == "any-name"
    assert "error" in captured["detail"]
