"""litellm_client 模型组 context_length 透传/回读测试。

context_length 存于 LiteLLM model_info（create/update 写入、/model/info 回返回），
Agent 选用模型组时继承，最终写入引擎 config.yaml 跳过 hermes model_metadata 探针。
"""
from unittest.mock import AsyncMock

import pytest
from app.services import litellm_client


@pytest.mark.asyncio
async def test_list_model_groups_extracts_context_length(monkeypatch):
    """list_model_groups 从 model_info 回读 context_length。"""
    deployments = [
        {
            "model_name": "deepseek-v4-flash",
            "litellm_params": {"model": "openai/deepseek-v4-flash"},
            "model_info": {"id": "x", "context_length": 1000000, "db_model": True},
        },
        {
            "model_name": "gpt-4o",
            "litellm_params": {"model": "openai/gpt-4o"},
            "model_info": {"id": "y", "db_model": True},  # 无 context_length
        },
    ]
    monkeypatch.setattr(litellm_client, "list_models", AsyncMock(return_value=deployments))

    groups = await litellm_client.list_model_groups()
    by_name = {g["model_group"]: g for g in groups}
    assert by_name["deepseek-v4-flash"]["context_length"] == 1000000
    assert by_name["gpt-4o"]["context_length"] is None


@pytest.mark.asyncio
async def test_create_model_passes_model_info(monkeypatch):
    """create_model 把 context_length 放进 model_info 传给 LiteLLM。"""
    captured: dict = {}

    async def fake_request(method, path, *, params=None, json=None, timeout=30.0):
        captured.update(method=method, path=path, json=json)
        return {"ok": True}

    monkeypatch.setattr(litellm_client, "_request", fake_request)

    await litellm_client.create_model(
        "grp", {"model": "openai/x"}, model_info={"context_length": 200000}
    )
    assert captured["json"]["model_info"] == {"context_length": 200000}


@pytest.mark.asyncio
async def test_create_model_omits_model_info_when_none(monkeypatch):
    """未传 model_info 时 payload 不含 model_info 键。"""
    captured: dict = {}

    async def fake_request(method, path, *, params=None, json=None, timeout=30.0):
        captured["json"] = json
        return {"ok": True}

    monkeypatch.setattr(litellm_client, "_request", fake_request)

    await litellm_client.create_model("grp", {"model": "openai/x"})
    assert "model_info" not in captured["json"]


@pytest.mark.asyncio
async def test_update_model_merges_model_info_with_id(monkeypatch):
    """update_model 把 context_length 与定位用 id 合并进 model_info。"""
    captured: dict = {}

    async def fake_request(method, path, *, params=None, json=None, timeout=30.0):
        captured["json"] = json
        return {"ok": True}

    monkeypatch.setattr(litellm_client, "_request", fake_request)

    await litellm_client.update_model(
        "model-id-123", {"model": "openai/x"}, model_info={"context_length": 1000000}
    )
    assert captured["json"]["model_info"] == {"id": "model-id-123", "context_length": 1000000}
