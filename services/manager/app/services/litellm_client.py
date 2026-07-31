"""LiteLLM Proxy Admin REST API 客户端封装。

全系统唯一模型网关，Manager 持 master key 代理调用 LiteLLM 管理 API。
命名约定（消除映射表/列）：
  - team_id = str(user_group.id)   （UserGroup ↔ LiteLLM Team 1:1）
  - user_id = str(user.id)
  - per-agent key metadata 带 agent_id / group_id
"""
from __future__ import annotations

from typing import Any

import httpx

from pkg.common.config import settings


class LitellmError(Exception):
    """LiteLLM API 调用异常，message 即返回给前端的 detail。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _base() -> str:
    return settings.litellm_base_url.rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.litellm_master_key}",
        "Content-Type": "application/json",
    }


def normalize_spend_dates(start_date: str | None, end_date: str | None) -> tuple[str | None, str | None]:
    """前端 date-only(YYYY-MM-DD) → LiteLLM /spend/logs 接受的 date-only，end_date +1 天。

    LiteLLM /spend/logs 把 end_date 解析为当天 00:00:00，date-only end_date 会排除当日数据；
    且该端点不接受「YYYY-MM-DD HH:MM:SS」时间后缀（报 unconverted data remains）。
    故统一用 date-only，end_date +1 天以包含当日。start 取前 10 位（兼容 date-only 与 datetime 入参）。
    """
    from datetime import datetime, timedelta

    def _day(s: str | None) -> str | None:
        return s[:10] if s else None

    s = _day(start_date)
    e = _day(end_date)
    if e:
        try:
            e = (datetime.strptime(e, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s, e


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, params=params, json=json, headers=_headers())
    except httpx.RequestError as e:
        raise LitellmError(f"LiteLLM 不可达: {e}") from e

    if resp.status_code >= 400:
        # 404 视为「不存在」，交给调用方判断
        detail = resp.text
        try:
            detail = resp.json().get("error", detail)
        except Exception:  # noqa: BLE001
            pass
        raise LitellmError(f"LiteLLM {method} {path} 失败 ({resp.status_code}): {detail}", resp.status_code)

    if resp.status_code == 204 or not resp.text:
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text


# ── 模型组（全局上游供应商） ─────────────────────────────


async def list_models() -> list[dict[str, Any]]:
    """GET /model/info — 返回所有 deployment（含 model_name 组名与上游参数）。

    每个 deployment 上注入 input_cost_per_1m_tokens / output_cost_per_1m_tokens
    （USD per 1M tokens，易读单位；None 表示未配置）。
    """
    data = await _request("GET", "/model/info")
    deployments = data.get("data", []) if isinstance(data, dict) else (data or [])
    for dep in deployments:
        if not isinstance(dep, dict):
            continue
        info = dep.get("model_info") or {}
        if not isinstance(info, dict):
            info = {}
        in_per_tok = info.get("input_cost_per_token")
        out_per_tok = info.get("output_cost_per_token")
        dep["input_cost_per_1m_tokens"] = (
            round(float(in_per_tok) * 1_000_000, 6) if in_per_tok is not None else None
        )
        dep["output_cost_per_1m_tokens"] = (
            round(float(out_per_tok) * 1_000_000, 6) if out_per_tok is not None else None
        )
    return deployments


async def list_model_prices() -> dict[str, dict[str, float]]:
    """GET /v1/model/info — 返回 {model_name: {input_cost_per_token, output_cost_per_token}}。

    用于 Dify 外接模式用量反查：从 Langfuse observation 拿 model + promptTokens/completionTokens，
    查此表算成本 = prompt_tokens * input_cost + completion_tokens * output_cost（USD）。
    调用方按 settings.spend_usd_to_cny 折算 CNY。

    返回空 dict 表示 LiteLLM 不可用或无 model 配置。
    """
    try:
        data = await _request("GET", "/v1/model/info")
    except LitellmError:
        return {}
    deployments = data.get("data", []) if isinstance(data, dict) else (data or [])
    prices: dict[str, dict[str, float]] = {}
    for d in deployments:
        name = d.get("model_name")
        info = d.get("model_info") or {}
        if not name or not isinstance(info, dict):
            continue
        input_cost = info.get("input_cost_per_token")
        output_cost = info.get("output_cost_per_token")
        if input_cost is None and output_cost is None:
            continue
        # 同一 model_name 可能有多 deployment（多上游），取第一个有价格的
        if name not in prices:
            prices[name] = {
                "input_cost_per_token": float(input_cost or 0),
                "output_cost_per_token": float(output_cost or 0),
            }
    return prices


async def list_model_groups() -> list[dict[str, Any]]:
    """返回去重后的模型组（model_name）列表，供 Agent 表单选择。"""
    deployments = await list_models()
    seen: dict[str, dict[str, Any]] = {}
    for d in deployments:
        name = d.get("model_name")
        if not name:
            continue
        if name not in seen:
            info = d.get("model_info") or {}
            seen[name] = {
                "model_group": name,
                "model": d.get("litellm_params", {}).get("model", name),
                "provider": d.get("litellm_params", {}).get("model", "").split("/")[0] if "/" in d.get("litellm_params", {}).get("model", "") else "",
                # context_length 存于 LiteLLM model_info，create/update 时写入
                "context_length": info.get("context_length"),
            }
    return list(seen.values())


async def create_model(
    model_name: str, litellm_params: dict[str, Any], model_info: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST /model/new — 新增上游供应商 deployment（BETA）。

    model_info 用于存自定义元数据（如 context_length），LiteLLM 持久化并在 /model/info 回返回。
    """
    payload: dict[str, Any] = {"model_name": model_name, "litellm_params": litellm_params}
    if model_info:
        payload["model_info"] = {k: v for k, v in model_info.items() if v is not None}
    return await _request("POST", "/model/new", json=payload)


async def delete_model(model_id: str) -> None:
    """POST /model/delete — 删除 deployment（BETA）。"""
    await _request("POST", "/model/delete", json={"id": model_id})


async def update_model(
    model_id: str,
    litellm_params: dict[str, Any] | None = None,
    model_info: dict[str, Any] | None = None,
    *,
    input_cost_per_1m_tokens: float | None = None,
    output_cost_per_1m_tokens: float | None = None,
) -> dict[str, Any]:
    """更新 deployment 的上游参数、自定义元数据和/或 pricing。

    - pricing（input/output_cost_per_token）：PATCH /model/{id}/update，pricing 字段
      放 litellm_params 里（不是 model_info！LiteLLM /model/info 返回的
      model_info.input_cost_per_token 是从 litellm_params 镜像出来的，所以必须写
      litellm_params 才能让 /model/info 和后续 spend 计算看到更新）。
    - 上游参数（litellm_params）+ 自定义元数据（model_info，如 context_length）：
      POST /model/update，通过 model_info.id 定位 deployment，litellm_params 与
      model_info 的非 None 字段一并写入（model_name 组名不可改）。

    pricing 单位 USD / 1M tokens，÷1M 转 per token 写回 LiteLLM。
    """
    if input_cost_per_1m_tokens is not None or output_cost_per_1m_tokens is not None:
        # pricing 走 PATCH /model/{id}/update，pricing 字段放 litellm_params
        params: dict[str, Any] = {}
        if input_cost_per_1m_tokens is not None:
            params["input_cost_per_token"] = input_cost_per_1m_tokens / 1_000_000
        if output_cost_per_1m_tokens is not None:
            params["output_cost_per_token"] = output_cost_per_1m_tokens / 1_000_000
        return await _request("PATCH", f"/model/{model_id}/update", json={"litellm_params": params})

    # 无 pricing：走 POST /model/update（上游参数 + 自定义元数据更新路径）
    if not litellm_params and not model_info:
        raise ValueError("update_model 需至少传 litellm_params / model_info / pricing 之一")
    mi: dict[str, Any] = {"id": model_id}
    if model_info:
        mi.update({k: v for k, v in model_info.items() if v is not None})
    payload = {
        "model_info": mi,
        "litellm_params": {k: v for k, v in (litellm_params or {}).items() if v is not None},
    }
    return await _request("POST", "/model/update", json=payload)


# ── Team（UserGroup ↔ Team） ────────────────────────────


async def create_team(team_id: str, alias: str) -> dict[str, Any]:
    """POST /team/new — 显式传 team_id（按命名约定=str(group.id)）。"""
    return await _request("POST", "/team/new", json={"team_id": team_id, "team_alias": alias})


async def team_info(team_id: str) -> dict[str, Any] | None:
    """GET /team/info — 不存在返回 None（404）。"""
    try:
        return await _request("GET", "/team/info", params={"team_id": team_id})
    except LitellmError as e:
        if e.status_code == 404:
            return None
        raise


async def list_teams() -> list[dict[str, Any]]:
    data = await _request("GET", "/team/list")
    return data if isinstance(data, list) else (data.get("teams", []) if isinstance(data, dict) else [])


async def ensure_team(team_id: str, alias: str) -> dict[str, Any]:
    """确保 team 存在，不存在则创建（懒创建）。"""
    info = await team_info(team_id)
    if info:
        return info
    return await create_team(team_id, alias)


async def team_member_add(team_id: str, user_id: str, role: str = "user") -> None:
    await _request("POST", "/team/member_add", json={"team_id": team_id, "member": {"user_id": user_id, "role": role}})


async def team_member_delete(team_id: str, user_id: str) -> None:
    await _request("POST", "/team/member_delete", json={"team_id": team_id, "user_id": user_id})


# ── User ────────────────────────────────────────────────


async def create_user(user_id: str, user_alias: str = "", roles: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"user_id": user_id}
    if user_alias:
        payload["user_alias"] = user_alias
    if roles:
        payload["roles"] = roles
    return await _request("POST", "/user/new", json=payload)


async def user_info(user_id: str) -> dict[str, Any] | None:
    try:
        return await _request("GET", "/user/info", params={"user_id": user_id})
    except LitellmError as e:
        if e.status_code == 404:
            return None
        raise


async def ensure_user(user_id: str, user_alias: str = "") -> dict[str, Any]:
    info = await user_info(user_id)
    if info:
        return info
    return await create_user(user_id, user_alias)


# ── Virtual Key ─────────────────────────────────────────


async def generate_key(
    *,
    team_id: str,
    models: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    key_alias: str | None = None,
    max_budget: float | None = None,
    budget_duration: str | None = None,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    duration: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """POST /key/generate — 生成虚拟 key，归属 team。返回 {key, key_id, ...}。"""
    payload: dict[str, Any] = {"team_id": team_id}
    if models is not None:
        payload["models"] = models
    if metadata is not None:
        payload["metadata"] = metadata
    if key_alias is not None:
        payload["key_alias"] = key_alias
    if max_budget is not None:
        payload["max_budget"] = max_budget
    if budget_duration is not None:
        payload["budget_duration"] = budget_duration
    if rpm_limit is not None:
        payload["rpm_limit"] = rpm_limit
    if tpm_limit is not None:
        payload["tpm_limit"] = tpm_limit
    if duration is not None:
        payload["duration"] = duration
    if user_id is not None:
        payload["user_id"] = user_id
    return await _request("POST", "/key/generate", json=payload)


async def list_keys(team_id: str | None = None) -> list[dict[str, Any]]:
    """GET /key/list —— return_full_object=true 返回完整 key 对象（token/alias/spend/models/team_id）。"""
    params: dict[str, Any] = {"return_full_object": "true", "limit": 1000}
    if team_id:
        params["team_id"] = team_id
    data = await _request("GET", "/key/list", params=params)
    return data if isinstance(data, list) else (data.get("keys", []) if isinstance(data, dict) else [])


async def key_info(token_id: str) -> dict[str, Any] | None:
    """按 token 查单个 key 详情。/key/info 在当前版本不可用，改用 /key/list 过滤。"""
    keys = await list_keys()
    for k in keys:
        if k.get("token") == token_id:
            return k
    return None


async def update_key(
    *,
    key: str | None = None,
    models: list[str] | None = None,
    max_budget: float | None = None,
    budget_duration: str | None = None,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    duration: str | None = None,
    metadata: dict[str, Any] | None = None,
    key_alias: str | None = None,
) -> dict[str, Any]:
    """POST /key/update —— key 入参为 token（LiteLLM 接受 token 作为 key 标识）。"""
    payload: dict[str, Any] = {}
    if key is not None:
        payload["key"] = key
    if models is not None:
        payload["models"] = models
    if max_budget is not None:
        payload["max_budget"] = max_budget
    if budget_duration is not None:
        payload["budget_duration"] = budget_duration
    if rpm_limit is not None:
        payload["rpm_limit"] = rpm_limit
    if tpm_limit is not None:
        payload["tpm_limit"] = tpm_limit
    if duration is not None:
        payload["duration"] = duration
    if metadata is not None:
        payload["metadata"] = metadata
    if key_alias is not None:
        payload["key_alias"] = key_alias
    return await _request("POST", "/key/update", json=payload)


async def delete_key(key: str) -> None:
    """POST /key/delete —— 入参 token（LiteLLM 接受 token 作为 key 标识）。"""
    await _request("POST", "/key/delete", json={"keys": [key]})


async def delete_keys_by_instance(instance_id: str) -> int:
    """删除某实例的全部 per-instance key（按 metadata.instance_id 过滤）。

    实例删除时调用：不仅删 instance.litellm_config 里的当前 key，还清理历史孤儿
    （过往 commit 失败/老 key 删除失败残留的、仍带本 instance_id 的 key）。返回删除条数。
    best-effort：list_keys 失败抛 LitellmError 由调用方兜底；单个 key 删除失败不阻断其余。
    """
    keys = await list_keys()
    target = str(instance_id)
    doomed = [
        k.get("token")
        for k in keys
        if (k.get("metadata") or {}).get("instance_id") == target and k.get("token")
    ]
    deleted = 0
    for token in doomed:
        try:
            await delete_key(token)
            deleted += 1
        except LitellmError:
            continue  # 单个失败不阻断其余
    return deleted


async def block_key(key: str) -> None:
    await _request("POST", "/key/block", json={"key": key})


async def unblock_key(key: str) -> None:
    await _request("POST", "/key/unblock", json={"key": key})


# ── 用量 / 成本 ─────────────────────────────────────────


async def spend_logs(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    team_id: str | None = None,
    api_key: str | None = None,
    user: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if team_id:
        params["team_id"] = team_id
    if api_key:
        params["api_key"] = api_key
    if user:
        params["user"] = user
    return await _request("GET", "/spend/logs", params=params)


async def spend_teams() -> dict[str, Any]:
    """GET /global/spend/teams —— 返回 {total_spend_per_team: [{team_id(alias), total_spend}], ...}。"""
    return await _request("GET", "/global/spend/teams")


async def spend_keys() -> list[dict[str, Any]]:
    data = await _request("GET", "/spend/keys")
    return data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])


async def spend_team(team_id: str) -> dict[str, Any]:
    """单个 team 的用量汇总（从 /global/spend/teams 过滤）。team_id 此处为 alias。"""
    data = await spend_teams()
    per = data.get("total_spend_per_team", []) if isinstance(data, dict) else []
    for t in per:
        if t.get("team_id") == team_id:
            return t
    return {"team_id": team_id, "total_spend": 0.0}


async def spend_models(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """GET /global/spend/models — 按模型聚合的花费。返回 [{model, total_spend, ...}]。"""
    params: dict[str, Any] = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    data = await _request("GET", "/global/spend/models", params=params or None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("models") or []
    return []


async def spend_last_n_days(n: int = 30, team_id: str | None = None) -> list[dict[str, Any]]:
    """GET /global/spend/last_n_days — 按日花费趋势。返回 [{date, spend}, ...]。

    team_id 在部分 LiteLLM 版本不被该端点支持；不支持时返回全平台趋势。
    """
    params: dict[str, Any] = {"days": n}
    if team_id:
        params["team_id"] = team_id
    data = await _request("GET", "/global/spend/last_n_days", params=params)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("daily_spend") or []
    return []
