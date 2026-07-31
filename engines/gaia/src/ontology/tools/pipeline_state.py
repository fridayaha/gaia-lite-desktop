"""Pipeline builder AG-UI shared state — request-scoped deps for the
pipeline-builder Agent (ADR-018 §14.5).

对标图探索的 ``AppState``（``tools/state.py``），但 ``state`` 字段是
``PipelineCanvasSnapshot``（管道 IR 画布），而非 ``CanvasSnapshot``。
pydantic-ai 的 ``dispatch_request`` 通过 ``StateHandler`` 协议把前端
发送的 state 注入 ``state`` 字段；pipeline_builder toolset 的工具读
``ctx.deps.state`` 获取当前画布、修改后返回 ``StateSnapshotEvent``。

与 ``AppState`` 分离的原因：``StateHandler`` 协议要求 ``state`` 是单一
字段，类型固定。图探索和管道构建器是两种不同的画布场景，state 结构
不同（objects/edges vs nodes/edges），不能共用一个 state 类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ontology.core.schemas.pipeline_canvas import PipelineCanvasSnapshot

if TYPE_CHECKING:
    from ontology.tools.executor import ToolExecutor


@dataclass
class PipelineAppState:
    """AG-UI run deps + shared state for the pipeline-builder Agent.

    Implements the ``StateHandler`` protocol (dataclass with a ``state``
    field). ``dispatch_request`` uses ``dataclasses.replace`` to inject the
    client-sent state into ``state``, leaving other fields untouched.

    Fields:
        state: AG-UI shared state (``PipelineCanvasSnapshot``). Tools write
            it by returning ``StateSnapshotEvent``; the Agent reads it each
            turn via ``ctx.deps.state``.
        thread_id: AG-UI thread id (for audit logging context).
        executor: Request-scoped ToolExecutor (for Service access + audit).
        pipeline_api_name: The pipeline api_name the user currently has open
            (forwarded via RunAgentInput.forwardedProps). Empty for new
            pipelines.
    """

    # NOTE: `state` MUST be the field named `state` and this class MUST be a
    # dataclass for the StateHandler protocol.
    state: PipelineCanvasSnapshot = field(default_factory=PipelineCanvasSnapshot)
    thread_id: str = ""
    executor: ToolExecutor | None = None
    pipeline_api_name: str = ""
