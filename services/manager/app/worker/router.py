"""UnionAgents Controller — worker 路由聚合层。

各功能域已拆到独立模块（路径全不变，baked-in /api/controller）：
  chat_api / config_skills / engine_pods / profiles / lifecycle
共享 helper 在 _common；状态机核心（suspend/destroy/set_status）在 lifecycle_service；
后台调度循环在 scheduler（background.py 驱动）。本模块仅 include 子 router + re-export
handler/helper 名，供 client.py 的 _r.<func> 进程内直调与测试 from app.worker.router import 不变。
"""

from fastapi import APIRouter

router = APIRouter()

# ── 子 router 聚合（路径不变）──────────────────────────────
from . import (  # noqa: E402
    chat_api,
    config_skills,
    engine_pods,
    lifecycle,
    profiles,
)

router.include_router(chat_api.router)
router.include_router(config_skills.router)
router.include_router(engine_pods.router)
router.include_router(lifecycle.router)
router.include_router(profiles.router)

# ── handler/helper re-export（client.py _r.<func> + 测试 app.worker.router._xxx 不变）──
from ._common import (  # noqa: F401,E402
    build_engine_envs as _build_engine_envs,
    heal_profile_runtime_config as _heal_profile_runtime_config,
    load_group_code as _load_group_code,
    load_instance_config as _load_instance_config,
    load_resource_spec as _load_resource_spec,
)
from .chat_api import get_agent_models  # noqa: F401,E402
from .config_skills import (  # noqa: F401,E402
    PersonaSyncRequest,
    SkillInstallRequest,
    SkillSecretsRequest,
    apply_agent_config,
    install_skill,
    list_engine_skills,
    sync_agent_config,
    sync_persona,
    sync_skills_config,
    uninstall_skill,
    write_skill_secrets,
)
from .engine_pods import (  # noqa: F401,E402
    get_instance_pods,
    get_instance_pods_metrics,
    get_pod_log_sources,
    get_pod_logs,
)
from .lifecycle import (  # noqa: F401,E402
    DeployRequest,
    _deploy_body,
    _run_deploy,
    _schedule_deploy,
    deploy_agent,
    destroy_agent,
    get_agent_status,
    resume_agent,
    restart_agent,
    suspend_agent,
)
from .lifecycle_service import destroy as _do_destroy  # noqa: F401,E402
from .lifecycle_service import suspend as _do_suspend  # noqa: F401,E402
from .profiles import (  # noqa: F401,E402
    CreateProfileRequest,
    RegisterProfilesRequest,
    _do_create_profile,
    _run_profile_seeds,
    _schedule_profile_seeds,
    _seed_persona,
    _select_pod_by_load,
    create_profile,
    delete_profile,
    ensure_profile,
    register_profiles,
    teardown_profile,
)
