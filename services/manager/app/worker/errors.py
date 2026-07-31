"""Worker 异常类型。

controller 并入 manager 后，原 `services/controller_client.ControllerError` 迁移到此。
manager 调用点（agent_instances/agent_skills/dashboard/resource_pools/metrics_service）
仍以 `controller_client.ControllerError` 捕获，facade（client.py）re-export 本类。
"""


class ControllerError(Exception):
    """Worker 调用异常，message 即返回给前端的 detail。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
