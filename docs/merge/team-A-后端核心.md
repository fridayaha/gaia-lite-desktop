# A 团队任务书 — 后端核心（manager + controller 并入）

> 关键路径团队。W1 必须冻结 `01-接口契约.md` 并落地基座，B/C 才能并行。
> 工作目录：Repo1 `develop` 分支。基座来源：Repo2(`/Users/friday/Projects/union-agents`)。

## 职责边界
- manager V3 三层 + LiteLLM + RBAC + dashboard
- **controller 以 worker 模式并入 manager**（K8s 生命周期 + 后台调度）
- 数据迁移脚本（V2→V3 复用 + V1→V3 新增）
- 冻结并维护 `01-接口契约.md`

不做：gateway 代理逻辑（B）、hub 业务（B，但 hub 的 K8s/CI 接入由 B 负责，A 仅配合 RBAC/UserGroup 对齐）、所有前端（C）。

## 任务清单

### A1. 基座落地（W1）
- 在 Repo1 `develop` 用 Repo2 覆盖 `services/manager`、`services/controller`、`services/gateway`、`apps/`、`pkg/`、`scripts/`、`deploy/`。
- 移除 Repo1 V1：`services/manager`(V1)、`services/controller`(V1)、`services/llm-gateway`、`services/channel-gateway`（细化逻辑按 B/C 任务书选择性移植，A 不保留）。
- 确保 manager 可启、DB 连通、seed 幂等（F-MGR-061：默认 admin@unionagents.io/admin123，修 startup seed greenlet bug）。
- **验收**：`make` 起 manager，`GET /health` 200；seed 跑两次结果幂等。

### A2. controller worker 并入（W1）
- Repo2 `services/controller/app/` → `services/manager/app/worker/`：
  - `k8s_manager.py`(1070) / `main.py`(2124) / `minio_archiver.py`(251) / `metric_sampler.py`(194) / `pod_manager.py`(87) / `recycle_scheduler.py`(96)
- 删 Repo2 manager 侧 `controller_client`（`services/manager/app/services/controller_client.py` 等）HTTP 封装，改进程内直调。
- `recycle_scheduler` / `metric_sampler` 挂 manager 启动事件（lifespan）作后台任务组；保持故障隔离（单独 task group，异常不拖垮主 API）。
- 保留 `/api/controller/*` 路由前缀，由 manager 挂载（前端/nginx 已依赖，路径不变）。
- **验收**：`/api/controller/agents/{id}/status` 经 manager 可达；recycle_scheduler 后台日志可见。

### A3. V3 三层模型与 API 核对（W2~3）
- 模型在 `services/manager/app/models/__init__.py`：AgentDefinition / AgentVersion / ResourcePool / AgentInstance / AgentInstanceChannel / AgentDeployment / AgentProfile / ResourceMetricSample。
- API 见契约 §2.1~2.3（F-MGR-001~013）。确保：组隔离（跨组 404）、access_scope（ALL/USER/USER_GROUP，F-MGR-044）、平台管理员旁路（`is_platform_admin()`，F-MGR-043）。
- **验收**：建定义→发布版本→建资源池→建实例→上线 全链路通；跨组访问 404；管理员旁路可见。

### A4. LiteLLM 集成（W2~3）
- 模型组 / 虚拟 Key / Team 同步 / 用量计费（F-MGR-020~023，契约 §2.4）。
- 每实例一 Key，归属 UserGroup 对应 Team；计费 Team 由 access_scope 派生。
- USD→CNY 汇率转换；组管理员仅见本组。
- **验收**：建实例自动签 LiteLLM Key；`/litellm/spend/*` 返回明细+汇总+趋势。

### A5. RBAC + UserGroup + IM 绑定（W2~3）
- F-MGR-030,040~044。Role-Permission 多对多（menu/api/button 三类）；V3 三类资源权限种子。
- UserGroup 自动生成机器码（MinIO 前缀 + Pod label）。
- **验收**：角色权限树配置生效；IM 绑定跨组限制正确。

### A6. dashboard + metrics 采样（W3）
- F-MGR-050,051；F-CTL-033（MetricSampler 每 60s 采样，7 天保留）。
- **验收**：`/dashboard/*` 各端点返回；metrics 按 1h/6h/24h/7d 聚合。

### A7. 知识库后端 — ❌ 本次不做（已决策）
- 核对发现 Repo1/Repo2 知识库均无后端（仅前端占位）。用户决策：**本次不做，保留 Repo2 占位页**。本任务取消，A 无需处理知识库。

### A8. 数据迁移脚本（W4）
- 复用 Repo2 `scripts/migrate_to_v3.py`、`migrate_to_v3_data.py`（V2→V3）。
- 新增 V1→V3 映射：Agent→Definition+Version+Instance；EngineInstance/AgentDeployment→ResourcePool；AgentChannel→AgentInstanceChannel；ApiKey→LiteLLM virtual key。
- **验收**：V1 旧库迁移演练，数据一致性核对（定义/版本/实例数对齐）。

### A9. 配置与人设/技能同步（W3）
- worker 内实现 F-CTL-040~043：MinIO 路径 `groups/{group_code}/engine-config/`、`backups/`、`archives/`。
- `_load_instance_config` JOIN 三层（F-CTL-041）；SOUL.md fan-out（F-CTL-042）；技能 zip→tar.gz 剥顶层 + 路径安全过滤（F-CTL-043）。
- **验收**：人设/技能变更 restart 后热生效；MinIO 路径组隔离正确。

## 交付物
- `01-接口契约.md`（W1 冻结，后续变更走变更通知）
- manager 可启 + worker 并入 + 三层/LiteLLM/RBAC/dashboard/迁移 全通
- V1→V3 迁移脚本 + 演练记录

## 关键依赖文件
- Repo2 manager 模型：`services/manager/app/models/__init__.py`
- Repo2 controller（并入 worker）：`services/controller/app/{k8s_manager,main,minio_archiver,metric_sampler,pod_manager,recycle_scheduler}.py`
- Repo2 manager `controller_client`（删除）：`services/manager/app/services/controller_client.py`、`services/manager/app/api/{agent_instances,agent_skills,dashboard}.py` 中的调用
- Repo2 迁移脚本：`scripts/migrate_to_v3*.py`
