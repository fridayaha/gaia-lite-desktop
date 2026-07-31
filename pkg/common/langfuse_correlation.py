"""Langfuse trace 软关联工具。

Gateway 写外层 trace，Hermes 插件写内层 trace，两端通过
``session_id`` + ``last_user_message_hash`` 软关联（admin 监控中心做查询匹配）。

本模块提供共享算法：
  - ``hash_text_16(text)`` — sha256 前 16 字符（去首尾空白）
  - ``hash_last_user_message(obj)`` — 从请求体提取最后一条 user 消息并哈希
  - ``hermes_session_trace_id(session_id)`` — 复算 Hermes 插件的确定性 trace_id

接受多种输入形式：
  - ``bytes``：JSON 编码的请求体（Gateway 收到的原始 body）
  - ``dict``：
    - OpenAI 格式 ``{"messages": [...]}``
    - Hermes runs body ``{"input": "...", "conversation_history": [...]}``：
      ``input`` 是新用户消息文本，直接哈希
    - 单条 message dict ``{"role":"user","content":"..."}``（Hermes
      langfuse 插件写的 trace.input 形式）
  - ``list``：messages 数组本身
  - ``str``：顶层裸字符串
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def hermes_session_trace_id(session_id: str) -> str:
    """复算 Hermes langfuse 插件为某 session 生成的确定性 trace_id。

    插件 ``client.create_trace_id(seed=f"{session_id}::{task_id}")``；/v1/runs
    handler 里 ``effective_task_id = session_id or run_id``，客户端传了
    session_id 时 task_id == session_id。Langfuse SDK create_trace_id =
    sha256(seed) 前 16 字节 hex（32 字符）。

    用途：admin 关联查询按此 id 直取 trace。插件写的 trace 行 sessionId
    字段存在"错位一格"污染（长寿命 profile 进程残留上一 run 的会话上下文，
    trace 行创建时把上一 run 的 session 写进 sessionId），按 sessionId
    过滤除进程重启后首 run 外全部关联不上（2026-07-22 定位，种子哈希
    与真实 trace id 三次验证一致）。
    """
    seed = f"{session_id}::{session_id}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def hash_text_16(text: str) -> str:
    """Strip + sha256 前 16 字符。空串返回 ''，但调用方应先判空。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _extract_last_user_text(messages: list) -> str | None:
    """从 messages 数组反向找最后一条 role=user 的消息，返回归一化文本。

    支持 OpenAI 格式的两种 content：
    - 字符串：``{"role":"user","content":"hello"}``
    - 多模态列表：``{"role":"user","content":[{"type":"text","text":"hello"}]}``

    返回去首尾空白后的文本；无匹配 user 消息 / content 为空 → None。
    """
    if not isinstance(messages, list) or not messages:
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        text: str | None = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            if parts:
                text = "".join(parts)
        if text is None and content is not None:
            try:
                text = json.dumps(content, ensure_ascii=False, sort_keys=True)
            except Exception:
                continue
        if text is None:
            continue
        text = text.strip()
        if not text:
            continue
        return text
    return None


def hash_last_user_message(obj: Any) -> str | None:
    """从请求体对象提取最后一条 user 消息并哈希。

    接受 ``bytes`` / ``dict`` / ``list`` / ``str``：
    - ``bytes``：JSON 编码的请求体（Gateway 收到的原始 body）
    - ``dict``：
      - OpenAI 格式 ``{"messages": [...]}``
      - Hermes runs body ``{"input": "...", "conversation_history": [...]}``：
        ``input`` 是新用户消息文本，直接哈希（与 Hermes plugin 写的
        trace.input={"role":"user","content":"..."} 的哈希对齐）；
        ``input`` 为空时回退到 ``conversation_history`` 最后一条 user
      - 单条 message dict ``{"role":"user","content":"..."}``（Hermes
        plugin 写的 trace.input 形式）
    - ``list``：messages 数组本身
    - ``str``：顶层裸字符串（直接哈希，少见）

    返回 sha256 前 16 字符，或 None（无 messages / 无 user 消息 / 解析失败）。
    """
    if obj is None:
        return None
    # bytes → 解析为 dict
    if isinstance(obj, (bytes, bytearray)):
        try:
            obj = json.loads(obj.decode("utf-8", errors="replace"))
        except Exception:
            return None
    if isinstance(obj, str):
        text = obj.strip()
        return hash_text_16(text) if text else None

    messages: list | None = None
    if isinstance(obj, list):
        messages = obj
    elif isinstance(obj, dict):
        # OpenAI 格式：{"messages": [...]}
        if isinstance(obj.get("messages"), list):
            messages = obj["messages"]
        # Hermes runs body：{"input": "...", "conversation_history": [...]}
        elif "input" in obj:
            inp = obj["input"]
            if isinstance(inp, str):
                text = inp.strip()
                if text:
                    return hash_text_16(text)
                # input 空串 → 回退到 conversation_history 最后一条 user
                if isinstance(obj.get("conversation_history"), list):
                    messages = obj["conversation_history"]
            elif isinstance(inp, list):
                messages = inp
            elif isinstance(inp, dict) and inp.get("role") == "user":
                messages = [inp]
        # Hermes langfuse 插件写的 trace.input 是单条 message dict
        # ({"role":"user","content":"..."})，按单元素 list 处理
        elif obj.get("role") == "user":
            messages = [obj]
    if not messages:
        return None
    text = _extract_last_user_text(messages)
    if text is None:
        return None
    return hash_text_16(text)
