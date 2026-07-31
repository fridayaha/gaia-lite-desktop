"""通用 JSON 日志 formatter — manager / gateway / hub 共享。

输出每行一个 JSON 对象，Promtail json stage 解析后入 Loki，可按 level / service /
request_id 切片查询。从 contextvars 读 request_id（asyncio task 独立 context）。

用法：
    from pkg.common.logging import setup_json_logger
    setup_json_logger("manager", level=settings.log_level)
"""

import json
import logging
import sys
from datetime import UTC, datetime

from pkg.common.request_id import get_request_id

# 标准 LogRecord 属性，透传 extra 时跳过这些
_LOG_RECORD_BUILTIN = frozenset({
    "args", "msg", "levelname", "name", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created",
    "msecs", "relativeCreated", "thread", "threadName", "processName",
    "process", "levelno", "message", "taskName",
})


class JsonFormatter(logging.Formatter):
    """通用 JSON 日志格式。

    - timestamp/level/service/logger/message 必出现
    - request_id 从 contextvars 读（未在请求上下文时为 ""）
    - record 上的 extra 字段全部透传（access_log 的 method/path/status_code 等）
    """

    def __init__(self, service_name: str = "unknown"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", self.service_name),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }

        # extra 字段透传（access_log 的 method/path/status_code/duration_ms 等）
        for k, v in record.__dict__.items():
            if k not in _LOG_RECORD_BUILTIN and k not in (
                "service", "request_id", "timestamp"
            ) and v is not None:
                entry[k] = v

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str, ensure_ascii=False)


def setup_json_logger(service_name: str, level: str = "INFO") -> None:
    """配置 root + uvicorn logger 使用 JSON formatter。

    manager / gateway 启动时调用（lifespan 内或模块加载时）。
    替换 uvicorn 默认 plain text formatter，让 access log 也走 JSON。
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(service_name))

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # uvicorn / uvicorn.access 也用 JSON，禁用 propagate 避免重复输出
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(log_level)
        lg.propagate = False
