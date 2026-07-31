"""HTTP 接口访问日志 helper — 走 JSON logging → Promtail → Loki。

不写 DB（量大，Loki 查询足够）。middleware 拦截每个请求记 method/path/status/
duration_ms/request_id，error 时 status>=400 自动 result="error"。

用法：
    from pkg.common.access_log import setup_access_log, log_request
    setup_access_log("manager")
    # middleware 内：
    log_request("manager", request.method, request.url.path, status_code,
                duration_ms, request_id=request_id, user_id=user_id)
"""

import logging

_access_loggers: dict[str, logging.Logger] = {}


def setup_access_log(service_name: str) -> logging.Logger:
    """初始化 access logger。复用 root logger 的 JSON handler（setup_json_logger
    已配置），不另建 handler 也不设 propagate=False，避免输出走不到 JSON formatter。"""
    logger = logging.getLogger(f"{service_name}.access")
    logger.setLevel(logging.INFO)
    _access_loggers[service_name] = logger
    return logger


def log_request(
    service: str,
    method: str,
    path: str,
    status: int,
    duration_ms: int,
    *,
    request_id: str = "",
    user_id: str | None = None,
    error: str | None = None,
) -> None:
    """记录一条 HTTP 请求日志。在 middleware 内层调用。"""
    logger = _access_loggers.get(service) or setup_access_log(service)
    extra: dict = {
        "service": service,
        "method": method,
        "path": path,
        "status_code": status,
        "duration_ms": duration_ms,
        "request_id": request_id,
        "event": "http.request",
        "result": "error" if status >= 400 else "ok",
    }
    if user_id:
        extra["user_id"] = user_id
    if error:
        extra["error"] = error
    logger.info("", extra=extra)
