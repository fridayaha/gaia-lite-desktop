"""Controller worker —— 并入 manager 的 K8s 生命周期 worker（进程内直调，无 HTTP）。

原 `services/controller` 独立服务（8001）已并入 manager：
  - router: /api/controller/* 路由（挂 manager app，路径不变，B gateway / C 前端直消费）
  - client: 进程内直调 facade（替代原 services/controller_client.py HTTP 封装）
  - background: recycle_scheduler / metric_sampler / 3 循环（挂 manager lifespan）
  - k8s_manager / minio_archiver / pod_manager: 引擎生命周期与归档实现
"""

from .errors import ControllerError

# 不在此 re-export `router`（APIRouter），否则同名属性会遮蔽 router 子模块，
# 使 background.py / client.py 中 `from . import router` 拿到 APIRouter 而非模块。
# main.py 直接 `from app.worker.router import router` 取 APIRouter。

__all__ = ["ControllerError"]
