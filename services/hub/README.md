# UnionAgent-Hub

能力资产治理中心 + Runtime Discover 服务（MVP RC）。

管理 Agent / Skill / Tool / MCP 四类能力资产的注册、版本、审批、安全扫描、生命周期、关系、下载导出，
并提供 Runtime Discover / Resolve 接口。

**Hub 不执行能力。**

## 项目结构

```
UnionAgent-Hub/
├── backend/         # FastAPI + SQLAlchemy + Alembic
│   └── app/
│       ├── api/         # API 路由
│       ├── models/      # 数据模型
│       ├── services/    # 业务逻辑
│       ├── manifests/   # Manifest Spec v0.1 校验
│       ├── scanners/    # 安全扫描器
│       ├── adapters/    # 外部扫描器接入接口 / 存储适配器（待实现）
│       ├── policies/    # 访问策略接口
│       └── core/        # 配置 / 枚举 / 身份上下文
├── frontend/        # Vue 3 + Element Plus（PoC 展示）
├── docs/            # 设计与验收文档
├── AGENTS.md        # 开发约束
├── start.sh         # 一键启动
└── README.md
```

## 本地启动

```bash
./start.sh
```

启动后：

| 服务 | 地址 |
|------|------|
| 后端 API | http://localhost:8000/api |
| 前端管理页 | http://localhost:5173/items |
| Runtime 调试 | http://localhost:5173/runtime |
| API 文档 | `docs/03_api_design.md` |

## 演示流程

详见：
- `docs/00_docs_index.md` — 文档索引
- `docs/02_solution_design.md` — 方案设计
- `docs/03_platform_integration.md` — 平台集成部署设计
- `docs/08_roadmap_workload.md` — Roadmap 与工作量
- `docs/12_observability_logging_design.md` — Stage 7A+7B 可观测性与结构化日志设计
- `docs/07_rbac_approval_design.md` — RBAC 与审批设计
- `docs/13_rbac_auth_integration_plan.md` — RBAC / 身份认证对接方案
- `docs/14_rbac_decision_record.md` — RBAC 策略决策记录
- `docs/19_storage_adapter_design.md` — 对象存储适配器设计
- `docs/demo_samples/` — 四类资产演示样例（Agent/Skill/Tool/MCP valid + warning + blocking）

**AI Runtime Discover 当前暂不接入**，Discover 是确定性过滤，不是 AI 检索。AI 增强作为后续方向。

## 运行测试

```bash
cd backend && pytest tests/ -v
```

**826 passed，0 failed。** PostgreSQL 冒烟验证已通过（12 项核心 API，`HUB_AUTH_MODE=dev`）。

## 安全管理

- **管理态最小 RBAC 已实现**（8 角色 × 24 权限矩阵，全部 26 个管理态 API 已加 `require_permission`）
- **RBAC 阶段进度**：RBAC-0~2、RBAC-3B、RBAC-3C-0、RBAC-3C（四眼原则）、RBAC-3D-1/2（对象级 ownership）、RBAC-4（Runtime Consumer role/scope + ScopedCapabilityAccessPolicy）已完成；RBAC-5（OIDC/Gateway）待实施
- **安全扫描**：CompositeScanner 框架已完成（P2-1）；SecretScannerProvider scaffold 已完成（P2-2B-lite）；BetterleaksScannerAdapter 已实现（P2-2B，默认 `HUB_BETTERLEAKS_ENABLED=false`）；GitleaksScannerAdapter fallback 已实现（P2-2C，默认 `HUB_GITLEAKS_ENABLED=false`）
- **对象存储**：LocalStorageAdapter 已实现（P1）；上传原始包保存 + 导出包缓存 + OpenAPI spec 保存；S3/MinIO 未接入（P2）
- 支持 `HUB_AUTH_MODE=dev`（默认，本地开发）/ `header`（Gateway 注入）/ `none`（紧急禁用）
- 真实 IAM / OIDC / JWT 待后续接入（RBAC-5）
- Runtime API 已有独立 role/scope 入口权限检查 + 资产级 ScopedCapabilityAccessPolicy，不纳入管理态 RBAC
- workspace DB 过滤未实现，需后续 migration；manifest/tool-definition scope 当前兼容 `capability:resolve`，后续可收紧
- **多租户**：MT-1.1 写入 + MT-2 管理态全部 + MT-3A helper + MT-3B Runtime Discover tenant filtering 已完成（`docs/24_multi_tenancy_design.md`），826 tests。Resolve/Storage 未实现。MT-3C~MT-5 待后续。

## 当前不做

| 项 | 原因 |
|----|------|
| Agent / Skill / Tool 执行 | 超出 Hub 职责 |
| MCP Server 托管 | 超出 Hub 职责 |
| 工作流编排 | 超出 Hub 职责 |
| 完整 IAM / 多租户全量 | MT-1 数据模型已落地；MT-2~MT-5 后续阶段 |
| 完整计费系统 | PoC 不做 |
| 推荐算法 / 评分评论 / 图谱可视化 | 非 MVP |
| 复杂前端产品化 | 后端和 API 优先 |
| Celery / Redis / OpenSearch / Harbor / OPA | 明确禁止 |
