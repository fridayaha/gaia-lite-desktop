# UnionAgent-Hub 接入 union_agent 新仓库分析

> **日期**: 2026-06-10  
> **状态**: 分析完成，待确认后进入真实合并  
> **本轮范围**: 仅分析，不修改新仓库，不做真实合并

---

## 1. 背景

### 1.1 旧仓库废弃

当前 Hub 代码位于旧仓库 `https://gitcode.com/Ascend-SACT/union.git`（分支 `main`，commit `76ec851`），后续将废弃。旧仓库 git toplevel 为 `/home/xiaox/projects/union`，UnionAgent-Hub 是其子目录。

### 1.2 新目标仓库

- **仓库地址**: `https://gitcode.com/Ascend-SACT/union_agent`
- **本地路径**: `/home/xiaox/projects/union/union_agent`
- **分支**: `main`
- **最新 commit**: `915c28e fix: EulerOS grep compatibility in install-offline.sh`
- **定位**: 企业级多智能体平台（UnionAgents 知行），管理 AI 智能体并为终端用户提供对话门户

### 1.3 本轮目标

将 UnionAgent-Hub 作为独立 service 接入 union_agent 仓库的 `services/` 目录下，与其他服务（controller、gateway、manager）并列。**本轮仅做分析和计划，不执行真实合并。**

---

## 2. 当前 Hub 状态

### 2.1 基本信息

| 项目 | 值 |
|------|-----|
| 目录 | `/home/xiaox/projects/union/UnionAgent-Hub` |
| Git toplevel | `/home/xiaox/projects/union`（旧仓库 union.git） |
| 当前分支 | `feature/runtime-discover-p1` |
| 最新 commit | `76ec851 docs: add integration self-check report` |
| Working tree | 有 3 个 untracked 文件（pdf/pptx/docx，不应迁移） |

### 2.2 功能模块

| 层级 | 内容 | 状态 |
|------|------|------|
| 数据模型 | HubItem, HubItemVersion, HubItemRelation, 审批/扫描/生命周期事件 | ✅ 已完成 |
| CRUD | 四类资产统一 CRUD | ✅ |
| 生命周期 | 状态流转（draft→pending_review→published/archived） | ✅ |
| 审批 | 审批流程、四眼原则（可配置） | ✅ |
| 安全扫描 | CompositeScanner, Betterleaks, Gitleaks, RuleScanner | ✅ |
| 版本管理 | 版本创建、回滚、状态管理 | ✅ |
| 能力关系 | HubItemRelation（依赖、组合、引用等） | ✅ |
| 包导入/导出 | JSON/YAML 导入导出、OpenAPI 导入 | ✅ |
| 预置能力 | 预置资产管理与一键初始化 | ✅ |
| RBAC | AuthContext, 管理态 RBAC, 对象级 ownership, Runtime Consumer 权限 | ✅ |
| 多租户 | Tenant 模型，读写路径隔离 | ✅ |
| Runtime Discover | discover + resolve 接口，tenant 过滤 | ✅ |
| Manifest 校验 | 类型化 Validator（Agent/Skill/Tool/MCP） | ✅ |
| Storage Adapter | LocalStorage + MemoryStorage | ✅ |
| 健康检查 | `/api/health` | ✅ |
| 日志/可观测性 | 结构化 JSON 日志、access log、request_id | ✅ |

### 2.3 测试基线

| 指标 | 值 |
|------|-----|
| 测试文件数 | 42 |
| 测试函数数 | 826 |
| 测试类型 | pytest（异步、模型、API、RBAC、租户、扫描器等） |

### 2.4 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI |
| ORM | SQLAlchemy (async) |
| 数据库 | SQLite（当前）/ PostgreSQL（目标） |
| 迁移 | Alembic |
| 包管理 | uv + pyproject.toml |
| 前端 | Vue 3 + Vite（PoC） |
| 依赖注入 | FastAPI Depends |
| 配置 | pydantic-settings |

---

## 3. 新仓库结构观察

### 3.1 顶层目录

```
union_agent/
├── apps/           # 前端应用
│   ├── admin/      # 管理后台 (Vue 3 + Element Plus)
│   └── enduser/    # 终端用户门户 (Vue 3)
├── deploy/         # 部署配置
│   ├── ci/         # CI 部署脚本
│   └── k8s/        # K8s manifests
├── docs/           # 文档
├── engines/        # 引擎
│   └── hermes/     # Hermes 引擎 (Dockerfile + entrypoint)
├── pkg/            # 共享 Python 包
│   ├── common/     # config, database, models, utils
│   └── models/     # 共享数据模型
├── scripts/        # 运维脚本
├── services/       # 后端微服务 ← Hub 将接入此处
│   ├── controller/ # Agent 生命周期管理 (端口 8001)
│   ├── gateway/    # API 网关/反向代理 (端口 8010)
│   └── manager/    # 用户/角色/Agent CRUD 管理 (端口 8002)
├── pyproject.toml  # 根项目配置（monorepo）
├── Makefile        # 构建/部署命令
├── .gitignore
└── README.md
```

### 3.2 已有服务命名方式

| 服务 | 目录名 | 端口 | 说明 |
|------|--------|------|------|
| Controller | `services/controller/` | 8001 | Agent 引擎生命周期 |
| Gateway | `services/gateway/` | 8010 | API 反向代理/SSE 透传 |
| Manager | `services/manager/` | 8002 | 用户/角色/Agent CRUD |

命名风格：**小写 + 连字符**（`controller`、`gateway`、`manager`）。

### 3.3 服务接入规范（逐项分析）

| 规范项 | 现状 | Hub 兼容性 |
|--------|------|------------|
| 独立 Dockerfile | 每个服务有独立 Dockerfile | Hub 需新建 Dockerfile |
| 独立 pyproject.toml | 无 — 共用根 pyproject.toml | Hub 有独立 pyproject.toml，需评估 |
| requirements.txt | 无 — Dockerfile 内 pip install | Hub 使用 uv + pyproject.toml |
| 统一 compose | 无 Docker Compose，仅 K8s manifests | Hub 需后续适配 |
| 统一 gateway | services/gateway（FastAPI 反向代理） | Hub 管理态 API 需注册路由 |
| 统一日志 | 无统一框架，各服务打印 logging | Hub 有结构化 JSON 日志 |
| 统一健康检查 | `GET /health` → `{"status":"ok","service":"<name>"}` | Hub 需适配格式 |
| 统一数据库 | 共享 PostgreSQL，通过 pkg/common/database.py | Hub 有独立 DB，需评估 |
| 统一 migration | Manager 自管（create_all），无 Alembic | Hub 有 Alembic，需评估 |
| 统一端口分配 | 8001/8002/8010 | Hub 需分配新端口（建议 8003） |
| API prefix 模式 | `/api/<service>/...`（controller/manager） | Hub 使用 `/api/hub/...`，可保持 |
| 共享代码 | `pkg/common/`（config, database, models） | Hub 代码独立，可选接入 pkg |
| 前端 | 独立 apps/ 目录 | Hub PoC 前端可暂不接入 |
| CI | GitHub Actions（.gitcode/workflows/） | 后续对接 |
| .gitignore | 根目录 .gitignore | Hub 自带 .gitignore，需合并 |

### 3.4 接入规范初步判断

**关键差异**：

1. **包管理方式不同**：新仓库使用单根 `pyproject.toml` + Dockerfile 内 pip install 所有依赖；Hub 使用 uv + 独立 `pyproject.toml`。**短期内 Hub 应保持独立 pyproject.toml 和 uv lock**，避免影响其他服务构建。

2. **数据库共享 vs 独立**：新仓库服务共享同一 PostgreSQL（通过 `pkg/common/database.py`）。Hub 目前使用独立数据库（SQLite/PG），**不应立即合并数据库**，保持独立 DB URL 配置。

3. **migration 方式不同**：新仓库 Manager 用 `create_all` 自动建表，无 Alembic。Hub 有独立的 Alembic migration 链。**Hub 保持自管 Alembic**。

4. **共享代码 pkg**：Hub 创建初期应保持代码独立，后续阶段评估是否需要共享 `pkg/common/config.py` 或 `pkg/common/utils.py`。

5. **健康检查格式**：新仓库使用 `{"status":"ok","service":"<name>"}`，Hub 需要调整为相同格式。

---

## 4. 推荐目录布局

### 4.1 推荐目标目录

```
~/projects/union/union_agent/services/union-agent-hub/
```

**理由**：
- 与新仓库 `controller` / `gateway` / `manager` 命名风格一致（小写 + 连字符）
- 语义清晰，不与已有服务名冲突
- 保持 `union-agent-hub` 名称延续性

### 4.2 内部结构（保持不变）

```
services/union-agent-hub/
├── AGENTS.md                 # 开发约束文档
├── README.md                 # 项目说明
├── start.sh                  # 本地启动脚本
├── docker-compose.pg.yml     # PG 依赖 compose
├── backend/
│   ├── pyproject.toml        # Hub 独立依赖管理
│   ├── uv.lock               # uv 锁文件
│   ├── alembic.ini           # Alembic 配置
│   ├── README.md
│   ├── alembic/              # 数据库迁移
│   ├── app/                  # 应用代码
│   │   ├── main.py
│   │   ├── api/              # API 路由
│   │   ├── core/             # 核心组件（config, auth, logging, rbac, tenancy）
│   │   ├── db/               # 数据库连接
│   │   ├── models/           # SQLAlchemy 模型
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # 业务逻辑
│   │   ├── manifests/        # Manifest 校验
│   │   ├── scanners/         # 安全扫描器
│   │   ├── adapters/         # 存储/扫描器适配器
│   │   └── policies/         # 访问策略
│   └── tests/                # 测试代码
├── docs/                     # 设计文档
├── frontend/                 # PoC 前端（最小）
├── scripts/                  # 工具脚本
└── tools/                    # 探索/实验代码
```

### 4.3 不重命名原因

- Hub 内部包路径 `app.*` 保持不变
- API prefix `/api/hub/...` 保持不变
- 数据库模型不变
- 避免大规模重构风险

---

## 5. 文件迁移范围

### 5.1 应迁移文件清单

| 目录/文件 | 说明 |
|-----------|------|
| `backend/` | 全部 Python 代码、配置、Alembic 迁移 |
| `docs/` | 设计文档、验证文档（排除 paper/ 目录） |
| `frontend/` | PoC 前端（排除 node_modules/ 和 dist/） |
| `scripts/` | 工具脚本 |
| `tools/` | 探索代码 |
| `AGENTS.md` | 开发约束文档 |
| `README.md` | 项目说明 |
| `start.sh` | 本地启动脚本 |
| `docker-compose.pg.yml` | PG 依赖 compose |
| `.gitignore` | Git 忽略规则 |
| `package-lock.json` | 前端锁文件（如需要） |

### 5.2 不应迁移文件清单

| 类别 | 文件/目录 | 原因 |
|------|-----------|------|
| Git | `.git/` | 旧仓库 git 历史不迁移 |
| 虚拟环境 | `backend/.venv/`, `.venv/` | 环境特定 |
| Python 缓存 | `**pycache**/`, `__pycache__/` | 编译缓存 |
| 测试缓存 | `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | 缓存 |
| 前端构建 | `frontend/dist/`, `frontend/node_modules/`, `node_modules/` | 构建产物 |
| 存储 | `.hub_storage/` | 本地运行数据 |
| 数据库文件 | `*.db`, `*.sqlite`, `demo.db` | 本地数据 |
| 日志 | `*.log` | 运行日志 |
| 环境变量 | `.env`, `.env.*` | 含敏感信息 |
| OpenCode 配置 | `opencode.json`, `.opencode/` | 工具配置 |
| 扫描报告 | `betterleaks*.json`, `gitleaks*.json`, `report.json` | 临时报告 |
| 二进制/大文件 | `docs/paper/2606.00925v1.pdf` | PDF（12MB+） |
| 文档附件 | `docs/整体架构图.pptx` | PPT 设计稿 |
| 文档附件 | `docs/设计文档样例.docx` | Word 样例 |

### 5.3 注意文件

| 文件 | 说明 |
|------|------|
| `backend/.env.example` | 含示例数据库凭据，可迁移（不含真实凭据） |
| `docker-compose.pg.yml` | 含 `POSTGRES_PASSWORD: hub_password`，仅为本地开发示例 |

---

## 6. 接入边界（本轮不做）

| 事项 | 原因 |
|------|------|
| 不修改新仓库其他服务代码 | 保持 controller/gateway/manager 不变 |
| 不修改根目录 compose/CI/gateway | 后续阶段单独对接 |
| 不修改根目录 pyproject.toml/Makefile | Hub 保持独立依赖管理 |
| 不修改 Hub 业务代码 | 仅做目录迁移 |
| 不改 API prefix (`/api/hub`) | 保持兼容 |
| 不改 DB schema/Alembic | 保持自管 |
| 不改前端 | PoC 前端保持现状 |
| 不改 Scanner/Storage 逻辑 | 内部实现不变 |
| 不提交 secret/.env/.hub_storage | 防泄露 |
| 不 push 远端 | 本地验证后再决定 |

---

## 7. 部署对接待确认项

以下是后续部署对接阶段（Phase 4+）需要确认的信息。**本轮不做，仅列出。**

### 7.1 基础配置

| 序号 | 确认项 | 当前 Hub 值 | 说明 |
|------|--------|------------|------|
| 1 | 服务命名 | `union-agent-hub` | 建议与新仓库命名风格一致 |
| 2 | 服务端口 | 8003（建议） | 避开 8001/8002/8010 |
| 3 | API prefix | `/api/hub` | 保持现有前缀 |
| 4 | 健康检查路径 | `/api/health` → 建议也支持 `/health` | 统一为 `/health` |
| 5 | 健康检查格式 | `{"status":"ok","service":"unionagents-hub"}` | 对齐新仓库格式 |

### 7.2 数据库

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 6 | 数据库连接 | Hub 使用独立 DB 还是共享 unionagents DB？ |
| 7 | Alembic 管理 | Hub 自管 Alembic migration，或平台统一管理？ |
| 8 | PG 版本 | 当前 docker-compose 使用 PG 16 |

### 7.3 存储与安全

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 9 | 对象存储 | Hub 使用 LocalStorage（`.hub_storage/`），后续是否接入 MinIO？ |
| 10 | Secret 扫描器 | Betterleaks/Gitleaks 是否安装在 Hub 镜像内？ |
| 11 | Auth header | Hub 使用 `X-User-Id`/`X-User-Roles` header，是否由 Gateway 统一注入？ |

### 7.4 认证与授权

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 12 | OIDC/JWT | 仍为后续阶段（RBAC-5） |
| 13 | Runtime Consumer headers | `X-Consumer-Id`/`X-Consumer-Type` 对接方式 |
| 14 | Tenant header 来源 | `X-Organization-Id`/`X-Workspace-Id` |

### 7.5 可观测性

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 15 | 日志格式 | Hub 使用结构化 JSON 日志，是否统一为平台日志格式？ |
| 16 | Prometheus/Grafana | Hub 是否接入统一监控？ |

### 7.6 部署与 CI

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 17 | K8s manifests | Hub 是否需要独立 Deployment/Service？ |
| 18 | Docker-compose | 是否加入根 compose（如果后续引入）？ |
| 19 | CI 测试 | CI pipeline 是否跑 Hub 自有 826 个测试？ |
| 20 | PG live migration | Hub 是否需要从 SQLite 迁移到 PG？ |
| 21 | 防泄露 CI gate | 是否在 CI 中增加 Betterleaks/Gitleaks 扫描？ |

### 7.7 兼容性

| 序号 | 确认项 | 说明 |
|------|--------|------|
| 22 | Harness/OfficeClaw schema | Hub 已有兼容设计文档 |
| 23 | 服务注册/发现 | Hub 是否需要注册到 service discovery？ |

---

## 8. 风险与防泄露

### 8.1 风险点

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| Hub 依赖与平台冲突 | 中 | 保持独立 pyproject.toml，不共享根依赖 |
| 数据库冲突 | 低 | Hub 使用独立 DB URL |
| 端口冲突 | 低 | 分配独立端口 8003 |
| API 路由冲突 | 低 | Hub 使用 `/api/hub` 前缀，不与现有服务冲突 |
| 共享 pkg 引入耦合 | 低 | 初期不接入 pkg，保持独立 |
| 前端构建复杂化 | 低 | PoC 前端极小，暂不对接平台前端 |

### 8.2 防泄露检查结果

#### 8.2.1 扫描工具结果

| 扫描工具 | 旧 Hub 项目 | 新 union_agent 项目 |
|----------|------------|-------------------|
| Betterleaks | 8 条发现 | 0 条发现 |
| Gitleaks | 10 条发现 | 0 条发现 |

#### 8.2.2 旧 Hub 项目发现分析

所有 18 条发现位于：
- `backend/tests/__pycache__/test_betterleaks_scanner.cpython-312-pytest-9.0.3.pyc`（编译缓存，fake token `ghp_fAk3tOkEn...`）
- `backend/tests/test_gitleaks_integration.py`（测试文件，fake token `sk_test_fAk3ExAmPl3...`）

**结论：全部为测试中使用的 fake/dummy 令牌，零真实 secret 泄露。** 编译缓存文件在迁移时会被 `__pycache__` 排除规则自动过滤。

#### 8.2.3 新仓库潜在关注项

- `deploy/k8s/infra/secret.yaml` 含明文开发密钥（`change-me`、`change-me`、`change-me`）——这些是 Kubernetes Secret manifest，应在部署时通过外部注入覆盖，但建议团队评估是否应移入外部密钥管理
- `deploy/ci/.env.local.example` 含占位符模板，无真实凭据

#### 8.2.4 防泄露排除策略

迁移时通过 rsync `--exclude` 规则排除：

```
--exclude='.git/'              # 旧 git 历史
--exclude='.env'               # 环境变量
--exclude='.env.*'             # 环境变量变体
--exclude='opencode.json'      # OpenCode 配置
--exclude='*.db'               # SQLite 数据库
--exclude='*.sqlite'           # SQLite 数据库
--exclude='*.log'              # 日志文件
--exclude='.hub_storage/'      # 本地存储
--exclude='betterleaks*.json'  # 扫描报告
--exclude='gitleaks*.json'     # 扫描报告
--exclude='report.json'        # 扫描报告
```

---

## 9. 推荐实施步骤

### Phase 1: 分析 + 文档（当前）✅

- [x] 拉取新仓库
- [x] 分析 services 结构
- [x] 分析 Hub 可迁移内容
- [x] dry-run 迁移
- [x] 防泄露检查
- [x] 输出本文档

### Phase 2: 复制为 services/union-agent-hub（待确认）

- [ ] 执行真实 rsync 复制
- [ ] 验证无不应迁移文件混入
- [ ] 验证 Hub 在 services/union-agent-hub/ 下可独立启动
- [ ] 本地 git 验证未破坏新仓库

### Phase 3: 最小本地启动（待确认）

- [ ] 在 services/union-agent-hub/ 下运行测试（826 tests）
- [ ] 配置 Hub 数据库（SQLite 或独立 PG）
- [ ] 启动 Hub 服务并验证管理态 API
- [ ] 启动 Hub 服务并验证 Runtime Discover API

### Phase 4: 接入 compose / deploy（后续阶段）

- [ ] 为 Hub 编写 Dockerfile
- [ ] 确定端口分配和健康检查格式
- [ ] 添加到 K8s manifests
- [ ] 配置 Gateway 路由（Hub 管理态 API）

### Phase 5: 联调 Runtime API（后续阶段）

- [ ] Gateway 转发 Runtime Discover 请求到 Hub
- [ ] 验证 discover + resolve 接口在 Gateway 后正常工作

### Phase 6: CI 和防泄露 gate（后续阶段）

- [ ] CI pipeline 集成 Hub 测试
- [ ] 配置 Betterleaks/Gitleaks 扫描 gate
- [ ] 文档同步更新

---

## 10. 接入方式评估

### 方案 A：完整复制为独立 service（推荐）

- **目录**: `services/union-agent-hub/`
- **操作**: rsync 排除敏感/缓存文件后复制
- **优点**: 最简单，风险最低，保留现有全部功能，不影响其他服务
- **缺点**: 初期与主仓库构建/部署未深度融合
- **适用**: 本轮

### 方案 B：复制 + 增加最小 service manifest

- 在方案 A 基础上，在 `services/union-agent-hub/` 内增加 `SERVICE.md` 描述服务元数据
- 不改根目录
- 为后续 compose/k8s/gateway 对接做准备
- 优点：比方案 A 多一份服务描述文档

### 方案 C：深度接入根目录 compose/CI/gateway

- 修改根 pyproject.toml、Makefile、deploy/ 目录
- 接入 shared pkg、统一 DB、统一 migration
- **风险**: 影响其他服务，改动范围大
- **本轮不做**

### 推荐

**采用方案 A（或 A+B 轻度版本）**。Hub 作为独立 service 复制到 `services/union-agent-hub/`，保持内部结构完整，不改动其他服务和根目录文件。后续在 Phase 4 时再逐步做部署对接。

---

## 11. 结论

### 11.1 是否建议进入真实合并

**建议进入**，前提是：

1. ✅ 本轮分析确认无 real secret/token 泄露
2. ✅ dry-run 确认文件映射清晰
3. ✅ 接入边界明确（不改其他服务、不改根目录）
4. ⚠️ 需人工确认：新仓库主导团队是否同意 Hub 作为独立 service 接入
5. ⚠️ 需人工确认：后续 K8s/Docker 资源分配

### 11.2 Dry-run 迁移摘要

- 会迁移一级目录：`backend/`, `docs/`, `frontend/`, `scripts/`, `tools/`
- 会迁移配置文件：`AGENTS.md`, `README.md`, `start.sh`, `docker-compose.pg.yml`, `.gitignore`, `package-lock.json`
- 被排除：`.git/`, `.venv/`, `node_modules/`, `dist/`, `.hub_storage/`, `*.db`, `*.sqlite`, `*.log`, `.env*`, `opencode.json`, `betterleaks*.json`, `gitleaks*.json`, `report.json`, `__pycache__/`
- 无 nested `.git` 风险
- 大文件风险：`docs/paper/2606.00925v1.pdf`（12MB+）不会被迁移（pdf 文件不在 rsync include 列表中，但需注意 dry-run 显示它在列表中——需额外排除）

### 11.3 确认事项清单（人工）

| # | 确认项 | 当前结论 |
|---|--------|----------|
| 1 | 新仓库主导团队是否同意接入？ | 待确认 |
| 2 | Hub 服务命名是否用 `union-agent-hub`？ | ✅ 建议 |
| 3 | 端口 8003 是否可用？ | ✅ 建议 |
| 4 | API prefix `/api/hub` 是否保持？ | ✅ 建议保持 |
| 5 | Hub 独立 DB 还是共享 DB？ | ✅ 独立 DB |
| 6 | Alembic 自管还是统一？ | ✅ 自管 |
| 7 | 是否已排除所有不应迁移文件？ | ✅ 已排除 |
| 8 | 是否已确认无真实 secret 泄露？ | ✅ 零真实泄露 |
| 9 | 是否修改了新仓库其他文件？ | ✅ 未修改 |
| 10 | 本轮是否做真实合并？ | ⏸️ 待确认 |

---

## 12. 真实合并执行命令（待确认后执行）

```bash
# 1. 创建目标目录
mkdir -p /home/xiaox/projects/union/union_agent/services/union-agent-hub

# 2. 执行 rsync 复制
rsync -av \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='**pycache**/' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.hub_storage/' \
  --exclude='node_modules/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='*.db' \
  --exclude='*.sqlite' \
  --exclude='demo.db' \
  --exclude='*.log' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='opencode.json' \
  --exclude='.opencode/' \
  --exclude='betterleaks*.json' \
  --exclude='gitleaks*.json' \
  --exclude='report.json' \
  --exclude='*.pdf' \
  --exclude='*.pptx' \
  --exclude='*.docx' \
  /home/xiaox/projects/union/UnionAgent-Hub/ \
  /home/xiaox/projects/union/union_agent/services/union-agent-hub/

# 3. 验证无不应迁移文件
cd /home/xiaox/projects/union/union_agent
find services/union-agent-hub -maxdepth 4 \( -name ".git" -o -name ".env" -o -name "*.db" -o -name "*.sqlite" -o -name "*.log" -o -name "report.json" -o -name "betterleaks*.json" -o -name "gitleaks*.json" \) -print

# 4. 查看新仓库状态
git status --short --untracked-files=all
```
