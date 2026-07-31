"""Langfuse SDK wrapper for Gateway.

Initializes a singleton Langfuse client from env vars. Exposes helpers:
  - ``trace_chat()`` for sync /v1/chat/completions (single request)
  - ``trace_run_start/bind/get/append_output/end()`` for async /v1/runs
    (POST creates trace+generation, GET /v1/runs/{id}/events writes SSE
    output to the same generation via run_id → generation mapping)

Disabled gracefully (returns None) when env vars are unset, so the gateway
still runs without Langfuse configured.

Env vars:
  LANGFUSE_PUBLIC_KEY — e.g. pk-lf-...
  LANGFUSE_SECRET_KEY — e.g. sk-lf-...
  LANGFUSE_HOST       — e.g. http://langfuse.monitoring:3000
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from pkg.common.langfuse_correlation import hash_last_user_message as _hash_last_user_message

logger = logging.getLogger(__name__)

_langfuse: Any = None
_initialized = False

# /v1/runs 跨请求关联：run_id → (trace, generation, output_buffer, created_at, first_token_at)
# 单 gateway 实例内存映射；多副本部署需改 Redis 共享（当前 ECS 单 replica 够用）
_run_traces: dict[str, tuple[Any, Any, list[str], float, datetime | None]] = {}
_run_traces_lock = threading.Lock()
_RUN_TRACES_TTL: float = 600.0  # 10min 兜底清理，避免 run 中断后泄漏


def get_langfuse() -> Any | None:
    """Return the singleton Langfuse client, or None if not configured.

    Returns None (not raises) when env vars are missing or SDK import fails,
    so callers can no-op tracing without branching on Langfuse availability.
    """
    global _langfuse, _initialized
    if _initialized:
        return _langfuse if _langfuse is not False else None
    _initialized = True

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = os.getenv("LANGFUSE_HOST", "").strip()

    if not (public_key and secret_key and host):
        logger.info("Langfuse disabled (env vars not set)")
        _langfuse = False
        return None

    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        logger.warning("langfuse package not installed — tracing disabled")
        _langfuse = False
        return None

    try:
        _langfuse = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info(f"Langfuse client initialized (host={host})")
        return _langfuse
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")
        _langfuse = False
        return None


def trace_chat(
    *,
    agent_id: str,
    engine_type: str,
    path: str,
    method: str,
    model: str | None,
    input_body: bytes,
    session_id: str | None = None,
    enduser_id: str | None = None,
    channel_type: str | None = None,
    last_user_message_hash: str | None = None,
    gateway_request_time: float | None = None,
) -> tuple[Any, Any] | tuple[None, None]:
    """Create a Langfuse trace + generation for a /v1/chat/completions call.

    Returns (trace, generation) or (None, None) if Langfuse is disabled.
    Sets trace-level input (so UI top-level shows data, not just generation).
    Generation is left open — caller must call ``finalize_chat(trace, generation, output)``
    when the upstream response completes.

    session_id: 可选会话标识（如 X-Session-Id 头）。Langfuse 用此值把同一会话的
        多次调用聚合到一起，UI 的 Sessions 视图按 session_id 分组。
    enduser_id: 可选终端用户 ID（来自 OpenAI 请求体 user 字段，或 IM 渠道的 user_id）。
        写入 trace.metadata.enduser_id，供监控中心按终端用户过滤。trace.userId
        仍为 agent_id（Langfuse userId 维度=agent）。
    channel_type: 可选渠道类型（web / wecom / feishu / dingtalk / wecom_bot）。
        写入 trace.metadata.channel_type，供监控中心按渠道过滤。终端门户走
        proxy.py 传 "web"，IM 渠道走 dispatcher 传 event.channel_type。
    last_user_message_hash: 最后一条 user 消息的 sha256 前 16 字符。用于 admin
        监控中心做 Gateway trace ↔ Hermes 内部 trace 软关联（两端从同一份请求体
        取同一字段哈希后写入 metadata）。无 session_id 时该字段也无法做关联，
        但仍写入 metadata 供未来扩展（如按哈希反查）。
    gateway_request_time: Gateway 收到请求的 unix 时间戳（秒，float）。用于
        admin 关联查询时按时间窗口缩小 Hermes 候选 trace 范围，避免 session_id
        撞同 + 哈希碰撞导致误关联。
    """
    lf = get_langfuse()
    if lf is None:
        return None, None

    try:
        metadata: dict[str, Any] = {
            "engine_type": engine_type,
            "path": path,
            "method": method,
        }
        if enduser_id:
            metadata["enduser_id"] = enduser_id
        if channel_type:
            metadata["channel_type"] = channel_type
        if last_user_message_hash:
            metadata["last_user_message_hash"] = last_user_message_hash
        if gateway_request_time is not None:
            metadata["gateway_request_time"] = gateway_request_time
        trace_kwargs: dict[str, Any] = {
            "name": "chat_completion",
            "user_id": agent_id,
            "metadata": metadata,
        }
        if session_id:
            trace_kwargs["session_id"] = session_id
        trace = lf.trace(**trace_kwargs)
        parsed = _parse_body(input_body)
        try:
            trace.update(input=parsed)
        except Exception:
            pass
        # Dify 请求体没有 model 字段（模型配置在 Dify 应用本身，gateway 无法获取），
        # 用 "dify-app" 占位比 "unknown" 更明确，避免用户误以为模型解析失败
        fallback_model = "dify-app" if engine_type == "DIFY" else "unknown"
        generation = trace.generation(
            name="engine_proxy",
            model=model or fallback_model,
            input=parsed,
        )
        return trace, generation
    except Exception as e:
        logger.warning(f"Langfuse trace creation failed: {e}")
        return None, None


def finalize_chat(
    trace: Any,
    generation: Any,
    output: str | None,
    *,
    usage: dict[str, int] | None = None,
    end_time: Any = None,
    completion_start_time: Any = None,
) -> None:
    """Finalize a /v1/chat/completions trace — close generation + set trace-level output.

    usage: {prompt_tokens, completion_tokens, total_tokens} — 从上游 OpenAI 兼容响应提取。
    end_time: datetime — 上游响应完成时间，用于精确延迟计算。
    completion_start_time: datetime — 首 token 到达时间（TTFT），仅流式响应有意义；
        非流式响应不设（Langfuse 视为整个生成在同一时刻完成）。
    """
    # langfuse SDK 2.x 对字符串 output 有 bug：会把字符串误判为 LangfuseMedia 的 data-URI，
    # 处理失败后写入 "<Upload handling failed for LangfuseMedia of type None>" 作为占位。
    # 包装成 {"text": ...} dict 绕过 media 检测，UI 能正常显示文本。
    # 空字符串/None 仍传 None（让 SDK 不创建 media 字段）。
    if output and output.strip():
        safe_output: Any = {"text": output}
    else:
        safe_output = None
    end_kwargs: dict[str, Any] = {"output": safe_output}
    if usage:
        end_kwargs["usage"] = usage
    if end_time:
        end_kwargs["end_time"] = end_time
    if completion_start_time:
        end_kwargs["completion_start_time"] = completion_start_time
    if generation is not None:
        try:
            generation.end(**end_kwargs)
        except Exception as e:
            logger.warning(f"Langfuse generation.end failed: {e}")
    if trace is not None:
        try:
            trace.update(output=safe_output)
        except Exception as e:
            logger.warning(f"Langfuse trace.update failed: {e}")


def finalize_chat_from_sse(
    trace: Any,
    generation: Any,
    raw_sse: str | None,
    *,
    end_time: Any = None,
    completion_start_time: Any = None,
) -> None:
    """从 SSE 原始文本提取 text + usage，调 finalize_chat 关闭 trace。

    dispatcher 流式调用引擎时累积原始 SSE 行（``data: ...``），结束后传给本函数。
    内部复用 ``_extract_text_from_sse`` / ``_extract_usage_from_sse``。
    trace/generation 为 None 时（Langfuse 未配置或 trace 创建失败）no-op。
    """
    if trace is None and generation is None:
        return
    extracted = _extract_text_from_sse(raw_sse) if raw_sse else None
    usage = _extract_usage_from_sse(raw_sse) if raw_sse else None
    finalize_chat(
        trace,
        generation,
        extracted,
        usage=usage,
        end_time=end_time,
        completion_start_time=completion_start_time,
    )


def finalize_chat_from_body(
    trace: Any,
    generation: Any,
    response_body: bytes | None,
    *,
    end_time: Any = None,
) -> None:
    """从非流式 JSON 响应体提取 text + usage，调 finalize_chat 关闭 trace。

    dispatcher 非流式调用引擎时拿到完整响应体后传给本函数。
    内部复用 ``_extract_text_from_body`` / ``_extract_usage_from_body``。
    trace/generation 为 None 时 no-op。
    """
    if trace is None and generation is None:
        return
    extracted = _extract_text_from_body(response_body)
    usage = _extract_usage_from_body(response_body)
    finalize_chat(
        trace,
        generation,
        extracted,
        usage=usage,
        end_time=end_time,
    )


def trace_run_start(
    *,
    agent_id: str,
    engine_type: str,
    path: str,
    input_body: bytes,
    model: str | None = None,
    session_id: str | None = None,
    enduser_id: str | None = None,
    channel_type: str | None = None,
    last_user_message_hash: str | None = None,
    gateway_request_time: float | None = None,
) -> tuple[Any, Any] | tuple[None, None]:
    """POST /v1/runs — create trace + generation (input = request body).

    Returns (trace, generation). Caller must parse run_id from the POST
    response body and call ``trace_run_bind(run_id, trace, generation)`` to
    enable cross-request correlation with GET /v1/runs/{id}/events.

    session_id: 可选会话标识。/v1/runs 异步流里 Dify 后续会发 conversation_id，
        可在 adapter 里再 ``trace.update(session_id=...)`` 覆盖；此处先以请求头
        传入的 X-Session-Id 占位（若客户端有传）。
    enduser_id: 可选终端用户 ID（来自 OpenAI 请求体 user 字段）。写入
        trace.metadata.enduser_id，供监控中心按终端用户过滤。
    channel_type: 可选渠道类型（web / wecom / feishu / dingtalk / wecom_bot）。
        写入 trace.metadata.channel_type，供监控中心按渠道过滤。
    last_user_message_hash: 最后一条 user 消息的 sha256 前 16 字符。用于 admin
        监控中心做 Gateway trace ↔ Hermes 内部 trace 软关联。
    gateway_request_time: Gateway 收到请求的 unix 时间戳（秒，float）。
    """
    lf = get_langfuse()
    if lf is None:
        return None, None

    try:
        metadata: dict[str, Any] = {
            "engine_type": engine_type,
            "path": path,
            "method": "POST",
        }
        if enduser_id:
            metadata["enduser_id"] = enduser_id
        if channel_type:
            metadata["channel_type"] = channel_type
        if last_user_message_hash:
            metadata["last_user_message_hash"] = last_user_message_hash
        if gateway_request_time is not None:
            metadata["gateway_request_time"] = gateway_request_time
        trace_kwargs: dict[str, Any] = {
            "name": "run",
            "user_id": agent_id,
            "metadata": metadata,
        }
        if session_id:
            trace_kwargs["session_id"] = session_id
        trace = lf.trace(**trace_kwargs)
        parsed = _parse_body(input_body)
        try:
            trace.update(input=parsed)
        except Exception:
            pass
        # Dify 请求体没有 model 字段（模型配置在 Dify 应用本身），用 "dify-app" 占位
        fallback_model = "dify-app" if engine_type == "DIFY" else "unknown"
        generation = trace.generation(
            name="engine_proxy",
            model=model or fallback_model,
            input=parsed,
        )
        return trace, generation
    except Exception as e:
        logger.warning(f"Langfuse trace_run_start failed: {e}")
        return None, None


def trace_run_bind(
    run_id: str, trace: Any, generation: Any, *, agent_id: str | None = None
) -> None:
    """Bind run_id → (trace, generation, output_buffer, created_at, first_token_at) after POST /v1/runs response.

    Subsequent GET /v1/runs/{id}/events uses run_id to look up the same trace.
    同时把 run_id + agent_id 持久化到 trace.metadata，供 Langfuse API 反查。
    first_token_at 初始为 None，trace_run_append_output 首次写入时填入。
    """
    if not run_id or trace is None:
        return
    with _run_traces_lock:
        now = time.time()
        # 兜底清理过期项，避免 run 中断后泄漏
        stale = [k for k, v in _run_traces.items() if now - v[3] > _RUN_TRACES_TTL]
        for k in stale:
            _run_traces.pop(k, None)
        _run_traces[run_id] = (trace, generation, [], now, None)
    # 持久化 run_id + agent_id 到 trace.metadata，供 Langfuse API 查询过滤
    try:
        meta: dict[str, Any] = {"run_id": run_id}
        if agent_id:
            meta["agent_id"] = agent_id
        trace.update(metadata=meta)
    except Exception as e:
        logger.warning(f"Langfuse trace_run_bind metadata update failed: {e}")


def trace_run_get(run_id: str) -> tuple[Any, Any, list[str]] | None:
    """GET /v1/runs/{id}/events — look up (trace, generation, output_buffer) by run_id."""
    if not run_id:
        return None
    with _run_traces_lock:
        v = _run_traces.get(run_id)
        if v is None:
            return None
        return v[0], v[1], v[2]


def trace_run_append_output(run_id: str, chunk: str) -> None:
    """Append an SSE chunk to the run's output buffer (capped at 8KB per run).

    首次写入时记录 first_token_at（TTFT），供 trace_run_end 传给 generation.end 的
    completion_start_time。
    """
    if not run_id or not chunk:
        return
    with _run_traces_lock:
        v = _run_traces.get(run_id)
        if v is None:
            return
        trace, generation, buffer, created_at, first_token_at = v
        # 首次收到 SSE chunk → 记录首 token 时间（TTFT）
        if first_token_at is None and buffer == []:
            first_token_at = datetime.now(UTC)
        # 硬上限 8KB，避免超长 SSE 把 langfuse 撑爆
        if sum(len(s) for s in buffer) > 8192:
            _run_traces[run_id] = (trace, generation, buffer, created_at, first_token_at)
            return
        buffer.append(chunk)
        _run_traces[run_id] = (trace, generation, buffer, created_at, first_token_at)


def trace_run_end(
    run_id: str, *, usage: dict[str, int] | None = None, end_time: Any = None
) -> None:
    """SSE ended — finalize generation.end + trace.update with accumulated output.

    usage: 从 SSE 末尾 usage 事件解析的 {prompt_tokens, completion_tokens, total_tokens}。
        调用方未传时，自动从已累积的 output_buffer 提取（覆盖 OpenAI SSE 末尾 usage
        和 Dify message_end.metadata.usage 两种格式）。
    end_time: datetime — SSE 结束时间。
    completion_start_time: 从 _run_traces 取首次 append 时的 first_token_at（TTFT）。
    """
    if not run_id:
        return
    with _run_traces_lock:
        v = _run_traces.pop(run_id, None)
    if v is None:
        return
    trace, generation, output_buffer, _, first_token_at = v
    try:
        merged = "".join(output_buffer) if output_buffer else ""
        # 从 SSE chunks 提取 assistant 文本（避免 raw "data: ..." 触发 SDK base64 解析）
        extracted = _extract_text_from_sse(merged) if merged else None
        # 调用方未传 usage 时，从累积的 SSE 文本提取
        if usage is None and merged:
            usage = _extract_usage_from_sse(merged)
        # 包装成 dict 避免 SDK 2.x 把字符串 output 误判为 LangfuseMedia
        safe_output: Any = {"text": extracted} if extracted else None
        end_kwargs: dict[str, Any] = {"output": safe_output}
        if usage:
            end_kwargs["usage"] = usage
        if end_time:
            end_kwargs["end_time"] = end_time
        if first_token_at:
            end_kwargs["completion_start_time"] = first_token_at
        if generation is not None:
            generation.end(**end_kwargs)
        if trace is not None:
            trace.update(output=safe_output)
    except Exception as e:
        logger.warning(f"Langfuse trace_run_end failed: {e}")


def _extract_text_from_sse(raw: str | None) -> str | None:
    """从 SSE data: 行中提取 assistant 文本，拼成完整回复。

    支持两种 SSE 格式：
    - Hermes /v1/runs 的 message.delta：``data: {"event":"message.delta","delta":"text"}``
      delta 是字符串，直接取
    - OpenAI /v1/chat/completions 的 chat.completion.chunk：
      ``data: {"choices":[{"delta":{"content":"text"}}]}``，delta 在 choices[0].delta 里是 dict

    提取后只保留 assistant 文本，避免 raw SSE 字符串（开头是 ``data:``）触发
    langfuse SDK 2.x 的 base64 data-URI 误解析（"Upload handling failed"）。
    """
    if not raw:
        return None
    parts: list[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]" or not data.startswith("{"):
            continue
        try:
            evt = json.loads(data)
        except Exception:
            continue
        if not isinstance(evt, dict):
            continue
        # 1) Hermes message.delta：顶层 delta 是字符串
        d = evt.get("delta")
        if isinstance(d, str) and d:
            parts.append(d)
            continue
        # 2) OpenAI chunk：delta 是 dict（顶层或 choices[].delta）
        if isinstance(d, dict):
            dc = d.get("content")
            if isinstance(dc, str) and dc:
                parts.append(dc)
                continue
        choices = evt.get("choices")
        if isinstance(choices, list):
            for ch in choices:
                if not isinstance(ch, dict):
                    continue
                cd = ch.get("delta")
                if isinstance(cd, dict):
                    cdc = cd.get("content")
                    if isinstance(cdc, str) and cdc:
                        parts.append(cdc)
        # 3) 兼容顶层 content 字符串（message.completed 等）
        c = evt.get("content")
        if isinstance(c, str) and c:
            parts.append(c)
    if not parts:
        # 提取不到文本返回 None，让 finalize_chat 把 output 设为 None。
        # 不能返回 raw[:4000]——raw 以 "data: " 开头，会被 SDK 误判为 data-URI media。
        return None
    return "".join(parts)[:4000]


def _parse_body(body: bytes | None) -> dict:
    """Parse request body bytes to dict for langfuse input."""
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
        return parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:500]}
    except Exception:
        return {"raw": body.decode("utf-8", errors="replace")[:500]}


def _extract_usage_from_body(body: bytes | None) -> dict[str, int] | None:
    """从非流式 JSON 响应体提取 usage（OpenAI 兼容 /v1/chat/completions 格式）。

    返回 {prompt_tokens, completion_tokens, total_tokens} 或 None。
    """
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        return None
    return _normalize_usage(usage)


def _extract_text_from_body(body: bytes | None) -> str | None:
    """从非流式 JSON 响应体提取 assistant 文本（OpenAI 兼容 /v1/chat/completions）。

    解析 ``choices[0].message.content``，返回字符串或 None。
    非流式响应（非 SSE）走此路径；流式响应由 _extract_text_from_sse 处理。
    """
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content:
        return content[:4000]
    return None


def _extract_model_from_body(body: bytes | None) -> str | None:
    """从响应体提取 model 字段（Hermes/OpenAI 响应带真实 model 名，请求体可能没有）。"""
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    model = parsed.get("model")
    if isinstance(model, str) and model:
        return model
    return None


def _extract_usage_from_sse(raw: str) -> dict[str, int] | None:
    """从 SSE 流文本中提取 usage（最后一个含 usage 的事件）。

    覆盖两种格式：
    - OpenAI 兼容 SSE：``data: {"usage": {"prompt_tokens":..., "completion_tokens":...}}``
    - Dify 1.14+：``data: {"event": "message_end", "metadata": {"usage": {...}}}``
    """
    if not raw:
        return None
    last_usage: dict[str, int] | None = None
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]" or not data.startswith("{"):
            continue
        try:
            evt = json.loads(data)
        except Exception:
            continue
        if not isinstance(evt, dict):
            continue
        # OpenAI 兼容：顶层 usage 字段
        u = evt.get("usage")
        # Dify：metadata.usage
        if not isinstance(u, dict):
            meta = evt.get("metadata")
            if isinstance(meta, dict):
                u = meta.get("usage")
        if isinstance(u, dict):
            normalized = _normalize_usage(u)
            if normalized:
                last_usage = normalized
    return last_usage


def _normalize_usage(usage: dict) -> dict[str, int] | None:
    """把 LiteLLM/Dify/OpenAI 各种 usage 字段名归一化为 {prompt, completion, total}。"""
    try:
        prompt = int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("input") or 0
        )
        completion = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("output") or 0
        )
        total = int(usage.get("total_tokens") or usage.get("total") or (prompt + completion))
        if prompt or completion or total:
            return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
    except Exception:
        pass
    return None


# ── Dify agent_thought SPAN 上报 ──────────────────────────


def create_or_update_agent_thought_span(
    trace: Any,
    spans: dict[str, Any],
    *,
    position: int | None,
    thought: str | None,
    tool: str | None,
    tool_input: Any,
    observation: str | None,
) -> None:
    """为 Dify agent_thought 事件在当前 trace 上创建/更新 SPAN observation。

    Dify agent app 每个 reasoning 步骤发一条 agent_thought 事件，字段含：
      position（步骤序号）、thought（推理）、tool（工具名）、
      tool_input（JSON 串或对象）、observation（工具结果）。

    Dify 实际观测到的发射模式（2026-06-30 ECS 测试）：
      1. 第一次：position 有值，thought/tool/tool_input/observation 全空
      2. 第二次：同 position，thought 已填充（推理文本），observation 仍可能为空
      3. 工具调用场景下还会有第三次：补 observation（工具结果）
    因此 update 路径必须同时考虑 input（thought/tool/tool_input）和 output（observation），
    任何字段从空变非空都要写入，否则 span 会停留在第一次的空状态。
    """
    if trace is None:
        return
    pos_key = str(position) if position is not None else "default"

    # tool_input 可能是 JSON 字符串，尝试解析为对象便于 UI 展示
    parsed_tool_input: Any = tool_input
    if isinstance(tool_input, str) and tool_input.strip():
        try:
            parsed_tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, ValueError):
            pass  # 保留原始字符串

    existing = spans.get(pos_key)
    if existing is not None:
        # 同 position 再次到达：合并 input + output 的非空字段
        update_kwargs: dict[str, Any] = {}
        if thought:
            update_kwargs.setdefault("input", {})["thought"] = thought
        if tool:
            update_kwargs.setdefault("input", {})["tool"] = tool
        if parsed_tool_input is not None and parsed_tool_input != "":
            update_kwargs.setdefault("input", {})["tool_input"] = parsed_tool_input
        if observation:
            update_kwargs["output"] = observation
        if update_kwargs:
            try:
                existing.update(**update_kwargs)
            except Exception as e:
                logger.warning(f"Langfuse agent_thought span update failed: {e}")
        return

    try:
        span_name = f"agent step#{position}" if position is not None else "agent_thought"
        span_input: dict[str, Any] = {
            "thought": thought or "",
            "tool": tool or "",
        }
        if parsed_tool_input is not None:
            span_input["tool_input"] = parsed_tool_input
        span = trace.span(
            name=span_name,
            input=span_input,
        )
        if observation:
            span.update(output=observation)
        span.end()
        spans[pos_key] = span
    except Exception as e:
        logger.warning(f"Langfuse agent_thought span creation failed: {e}")


# ── Dify workflow node SPAN 上报 ──────────────────────────


def _unix_to_dt(ts: Any) -> datetime | None:
    """Unix 时间戳（秒，int 或 float）→ datetime（UTC）。None/异常返回 None。"""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def create_or_update_workflow_node_span(
    trace: Any,
    spans: dict[str, Any],
    *,
    node_id: str | None,
    title: str | None,
    node_type: str | None,
    inputs: Any,
    outputs: Any,
    elapsed_time: Any,
    status: str | None,
    error: Any,
    created_at: Any,
) -> None:
    """为 Dify workflow 的 node_started/node_finished 事件创建/更新 SPAN observation。

    Dify workflow 多节点场景下，每个节点发两次事件：
      1. node_started：data 含 node_id/inputs/created_at（无 title/node_type/outputs）
      2. node_finished：data 含 node_id/inputs/outputs/title/node_type/elapsed_time/status/error/created_at

    按 node_id 缓存 span：node_started 创建（name 先用 node_id 占位，start_time 取 created_at），
    node_finished 补 title/outputs/metadata/end_time。
    """
    if trace is None:
        return
    if not node_id:
        return  # 无 node_id 无法关联两阶段事件，丢弃

    existing = spans.get(node_id)
    if existing is not None:
        # node_finished（或同 node_id 二次到达）：补 title/outputs/metadata/end_time
        update_kwargs: dict[str, Any] = {}
        if title:
            update_kwargs["name"] = title
        if outputs is not None:
            update_kwargs["output"] = outputs
        metadata: dict[str, Any] = {}
        if node_type:
            metadata["node_type"] = node_type
        if elapsed_time is not None:
            metadata["elapsed_time"] = elapsed_time
        if status:
            metadata["status"] = status
        if error:
            metadata["error"] = error
        if metadata:
            update_kwargs["metadata"] = metadata
        end_dt = _unix_to_dt(created_at)
        if end_dt:
            update_kwargs["end_time"] = end_dt
        if update_kwargs:
            try:
                existing.update(**update_kwargs)
            except Exception as e:
                logger.warning(f"Langfuse workflow node span update failed: {e}")
        return

    try:
        start_dt = _unix_to_dt(created_at)
        span_kwargs: dict[str, Any] = {
            "name": title or f"node:{node_id[:12]}",
            "input": inputs,
        }
        if start_dt:
            span_kwargs["start_time"] = start_dt
        span = trace.span(**span_kwargs)
        # node_finished 可能同请求内到达（非流式）或永不到达（流式只发 node_started）
        # 立即 end() 占位，后续 node_finished 再 update 补 output/end_time
        span.end()
        spans[node_id] = span
    except Exception as e:
        logger.warning(f"Langfuse workflow node span creation failed: {e}")


# ── OpenAI tool_calls SPAN 上报（Hermes 透传分支用） ──────────────────────────


def _parse_tool_calls_from_sse_chunk(chunk: str) -> list[dict[str, Any]]:
    """从单个 SSE chunk 解析 OpenAI tool_calls delta。

    OpenAI 兼容 chat.completion.chunk 格式：
        data: {"choices":[{"delta":{"tool_calls":[
            {"index":0,"id":"call_abc","type":"function",
             "function":{"name":"get_weather","arguments":"{\\\"loc"}}
        ]}}]}

    arguments 在流式响应里跨多个 chunk 字符串拼接（首 chunk 带 function.name + id + 初始 arguments，
    后续 chunk 仅带 arguments 片段）。本函数只负责"本 chunk 出现了哪些 tool_call delta"，
    累积由 update_tool_call_span 用 spans dict 完成。

    返回 [{index, tool_call_id, function_name, arguments_delta}, ...]。
    非 data 行 / 无 tool_calls 字段 / JSON 解析失败 → 返回 []。
    """
    if not chunk:
        return []
    results: list[dict[str, Any]] = []
    for line in chunk.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]" or not data.startswith("{"):
            continue
        try:
            evt = json.loads(data)
        except Exception:
            continue
        if not isinstance(evt, dict):
            continue
        choices = evt.get("choices")
        if not isinstance(choices, list):
            continue
        for ch in choices:
            if not isinstance(ch, dict):
                continue
            cd = ch.get("delta")
            if not isinstance(cd, dict):
                continue
            tcs = cd.get("tool_calls")
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                results.append(
                    {
                        "index": tc.get("index"),
                        "tool_call_id": tc.get("id"),
                        "function_name": fn.get("name") if isinstance(fn, dict) else None,
                        "arguments_delta": fn.get("arguments") if isinstance(fn, dict) else None,
                    }
                )
    return results


def update_tool_call_span(
    trace: Any,
    spans: dict[str, Any],
    tool_call: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """更新一个 tool_call SPAN observation（累积 arguments）。

    OpenAI SSE 协议：tool_call 的 `id` 只在首个 chunk 出现，`index` 在每个 chunk 都有。
    因此 spans 的 key 用 index 字符串（保证跨 chunk 命中）。

    spans 由调用方维护：key=str(index)（index 缺失时回退到 tool_call_id），value=dict：
        {"span": <span_obj>, "args": <累积的 arguments 字符串>, "function_name": <首次见到的 name>}。
    首次见到该 index：trace.span(name="tool_call: {function_name}", input=arguments_delta,
                              metadata={"tool_call_id": id, "function_name": name}, start_time=now)。
                              不立即 end——tool_call 不会悬空（流结束统一 end）。
    后续同 index：span.update(input=累积 args) 不 end。
    trace 为 None 时 return。所有 langfuse 调用 try/except + logger.warning。
    """
    if trace is None:
        return
    tc_id = tool_call.get("tool_call_id")
    idx = tool_call.get("index")
    # 优先用 index 做 key（OpenAI 协议保证跨 chunk 一致）；index 缺失时回退到 id
    key = str(idx) if idx is not None else (tc_id or "default")

    existing = spans.get(key)
    if existing is not None:
        # 后续 chunk：累积 arguments + 不 end
        args_delta = tool_call.get("arguments_delta")
        if isinstance(args_delta, str) and args_delta:
            existing["args"] += args_delta
            try:
                existing["span"].update(input=existing["args"])
            except Exception as e:
                logger.warning(f"Langfuse tool_call span update failed: {e}")
        return

    # 首次见到该 index：创建 span（不 end，等流结束统一 end）
    fn_name = tool_call.get("function_name") or "unknown"
    args_delta = tool_call.get("arguments_delta") or ""
    metadata: dict[str, Any] = {
        "tool_call_id": tc_id or key,
        "function_name": fn_name,
    }
    if idx is not None:
        metadata["index"] = idx
    span_kwargs: dict[str, Any] = {
        "name": f"tool_call: {fn_name}",
        "input": args_delta,
        "metadata": metadata,
    }
    if now is not None:
        span_kwargs["start_time"] = now
    try:
        span = trace.span(**span_kwargs)
        spans[key] = {"span": span, "args": args_delta, "function_name": fn_name}
    except Exception as e:
        logger.warning(f"Langfuse tool_call span creation failed: {e}")


def end_tool_call_spans(spans: dict[str, Any], *, end_time: datetime | None = None) -> None:
    """流结束时统一 end 所有 tool_call SPAN。

    创建时不 end，本函数在每个 tool_call 的最后一次 update 后关闭。
    end_time 反映 SSE 流结束时刻，让 Langfuse UI 的 span latency 包含
    整个 tool_call 累积周期（首 chunk 到流结束）。

    跳过已 end 的 SPAN（Hermes "completed" 事件已主动 end 过的）。
    """
    for v in spans.values():
        if v.get("ended"):
            continue
        span = v.get("span")
        if span is None:
            continue
        try:
            final_args = v.get("args") or ""
            update_kwargs: dict[str, Any] = {}
            if final_args:
                update_kwargs["input"] = final_args
            if end_time is not None:
                update_kwargs["end_time"] = end_time
            if update_kwargs:
                span.update(**update_kwargs)
            span.end(end_time=end_time)
            v["ended"] = True
        except Exception as e:
            logger.warning(f"Langfuse tool_call span final end failed: {e}")


# ── Hermes tool.progress SPAN 上报 ──────────────────────────


def _parse_hermes_tool_progress_from_chunk(chunk: str) -> list[dict[str, Any]]:
    """从单个 SSE chunk 解析 Hermes `event: hermes.tool.progress` 事件。

    Hermes 不发 OpenAI 标准 `delta.tool_calls`，而是发自定义 event：
        event: hermes.tool.progress
        data: {"tool":"skill_view","label":"customer-profile-update",
               "toolCallId":"call_xxx","status":"running"}

    每 tool 调用产生 2 个事件：status=running（开始）+ status=completed（结束）。

    识别策略：解析每个 `data: ` 行的 JSON，若含 `toolCallId` 字段则视为 Hermes
    tool.progress 事件（普通 chat.completion.chunk 的 data: 行 JSON 含 `choices`
    不含 `toolCallId`，互不干扰）。

    返回 [{tool_call_id, status, tool, label}, ...]。
    非 data 行 / 无 toolCallId 字段 / JSON 解析失败 → 不加入结果。
    """
    if not chunk:
        return []
    results: list[dict[str, Any]] = []
    for line in chunk.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]" or not data.startswith("{"):
            continue
        try:
            evt = json.loads(data)
        except Exception:
            continue
        if not isinstance(evt, dict):
            continue
        tc_id = evt.get("toolCallId")
        if not tc_id:
            continue  # 普通 chat.completion.chunk 无此字段
        results.append(
            {
                "tool_call_id": tc_id,
                "status": evt.get("status"),
                "tool": evt.get("tool"),
                "label": evt.get("label"),
            }
        )
    return results


def update_hermes_tool_span(
    trace: Any,
    spans: dict[str, Any],
    event: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """更新一个 Hermes tool.progress SPAN observation。

    Hermes 的 tool 调用事件非流式累积，每事件含完整状态：
    - status="running"：创建 SPAN（name="tool_call: {label}", metadata 含
      tool_call_id/tool/label）。不立即 end——等 status=completed 或流结束。
    - status="completed"：立即 end SPAN 并标记 ended=True。
    - 其他 status：忽略。

    spans 与 OpenAI update_tool_call_span 共用同一 dict（key=tool_call_id），
    key 命名空间不冲突（OpenAI 用 str(index)）。
    trace 为 None 时 return。所有 langfuse 调用 try/except + logger.warning。
    """
    if trace is None:
        return
    tc_id = event.get("tool_call_id")
    if not tc_id:
        return
    status = event.get("status")
    tool = event.get("tool") or "unknown"
    label = event.get("label") or tool

    if status == "running":
        if tc_id in spans:
            return  # 重复 running 事件，不重建
        metadata: dict[str, Any] = {
            "tool_call_id": tc_id,
            "tool": tool,
            "label": label,
            "source": "hermes.tool.progress",
        }
        span_kwargs: dict[str, Any] = {
            "name": f"tool_call: {label}",
            "input": "",  # Hermes 事件无 arguments
            "metadata": metadata,
        }
        if now is not None:
            span_kwargs["start_time"] = now
        try:
            span = trace.span(**span_kwargs)
            spans[tc_id] = {"span": span, "args": "", "function_name": label}
        except Exception as e:
            logger.warning(f"Langfuse hermes tool span creation failed: {e}")
    elif status == "completed":
        existing = spans.get(tc_id)
        if existing is None:
            return  # 没见过 running 事件，忽略
        if existing.get("ended"):
            return  # 已 end（重复 completed 事件）
        span = existing.get("span")
        if span is None:
            return
        try:
            if now is not None:
                span.end(end_time=now)
            else:
                span.end()
            existing["ended"] = True
        except Exception as e:
            logger.warning(f"Langfuse hermes tool span end failed: {e}")
