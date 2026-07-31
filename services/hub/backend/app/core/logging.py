import json
import logging
import time
from datetime import datetime, timezone

from app.core.request_id import get_request_id


class JsonAccessFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "request_id": getattr(record, "request_id", get_request_id()),
        }

        for field in (
            "method",
            "path",
            "status_code",
            "duration_ms",
            "result",
            "error_code",
            "actor_id",
            "actor_type",
            "workspace_id",
            "organization_id",
        ):
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value

        return json.dumps(log_entry, default=str)


def setup_json_access_logger() -> logging.Logger:
    logger = logging.getLogger("hub.access")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonAccessFormatter())
        logger.addHandler(handler)
    return logger


_access_logger = logging.getLogger("hub.access")


def log_access(
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    request_id: str = "",
    error_code: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    workspace_id: str | None = None,
    organization_id: str | None = None,
) -> None:
    extra = {
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "request_id": request_id,
        "event": "hub.http.request",
        "result": "error" if status_code >= 400 else "ok",
    }
    if error_code:
        extra["error_code"] = error_code
    if actor_id:
        extra["actor_id"] = actor_id
    if actor_type:
        extra["actor_type"] = actor_type
    if workspace_id:
        extra["workspace_id"] = workspace_id
    if organization_id:
        extra["organization_id"] = organization_id
    _access_logger.info("", extra=extra)
