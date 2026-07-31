import json
import logging
from datetime import datetime, timezone

from app.core.auth_middleware import get_auth_context
from app.core.request_id import get_request_id

_event_logger = logging.getLogger("hub.event")

_EVENT_FIELDS = (
    "actor_id",
    "actor_type",
    "workspace_id",
    "organization_id",
    "item_id",
    "item_type",
    "version_id",
    "operation",
    "result",
    "status_code",
    "duration_ms",
    "error_code",
    "detail",
    "depth",
    "result_count",
    "result_total",
    "dependency_count",
    "warning_count",
    "warning_type",
    "risk_level",
    "total_findings",
    "blocking_count",
    "scanner_version",
    "spec_title",
    "spec_version",
    "operation_count",
    "tools_created",
    "warnings_count",
    "failed_count",
    "action",
    "from_status",
    "to_status",
    "storage_key",
    "storage_backend",
    "cache_hit",
    "reason",
    "backend",
)


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "request_id": getattr(record, "request_id", get_request_id()),
        }
        for field in _EVENT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value
        return json.dumps(log_entry, default=str)


def _setup() -> None:
    _event_logger.setLevel(logging.INFO)
    _event_logger.propagate = False
    if not _event_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonEventFormatter())
        _event_logger.addHandler(handler)


def log_event(event: str, **fields) -> None:
    ctx = get_auth_context()
    extra = {
        "event": event,
        "request_id": get_request_id(),
        "actor_id": ctx.actor_id,
        "actor_type": ctx.actor_type,
        "workspace_id": ctx.workspace_id,
        "organization_id": ctx.organization_id,
    }
    for key, value in fields.items():
        if value is not None:
            extra[key] = value
    _event_logger.info("", extra=extra)


_setup()
