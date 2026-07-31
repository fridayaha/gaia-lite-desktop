# Hub 项目开发约束文档

## 项目概述

Hub 是能力资产治理中心 + Runtime Discover 服务，负责管理以下四类能力资产：

- **Agent** - 行业/场景 Agent 模板资产（非运行实例）
- **MCP** - MCP 配置资产（不托管 MCP Server）
- **Skill** - 任务能力包
- **Tool** - 工具定义资产（Hub 不负责执行调用）

### 当前阶段

- **Stage 0（PoC 核心闭环）**：已完成。包含数据模型、CRUD、生命周期、审批、安全扫描、版本回滚、包导入、前端 PoC。
- **当前**：进入 Stage 1+。Hub 定位从"管理态 PoC"升级为"能力资产治理中心 + Runtime Discover 服务"。
- Hub 不是 Runtime，不执行能力。

## Hub 负责范围

| 职责 | 说明 |
|------|------|
| 四类资产统一治理 | Agent / Skill / Tool / MCP 的统一管理 |
| 能力注册 | 资产注册与接入 |
| 预置能力管理 | 内置/预置能力维护 |
| 发布审批 | 能力发布审批流程 |
| 安全准入 | 能力安全审核 |
| 生命周期管理 | 状态流转（草稿→已发布→已下架等） |
| 版本管理 | 能力版本控制 |
| 回滚 | 版本回滚 |
| 下架策略 | 能力下架规则 |
| 基础发现与搜索 | 能力检索 |
| 能力关系管理 | 资产间关联关系（依赖、组合、引用等） |
| 类型化 Manifest / Schema 校验 | 按资产类型分别校验 Manifest 格式 |
| Runtime Discover / Resolve | 为 Runtime 提供能力发现与解析接口 |
| 下载和导出 | 能力包下载与导出 |

## Hub 不负责范围

以下能力不属于 Hub 职责，禁止引入相关逻辑：

- Agent 执行、Skill 执行、Tool 调用
- MCP Server 托管
- Runtime 编排
- 模型调用
- 完整计费系统
- 完整 IAM（身份与访问管理）
- 多租户治理
- 工作流编排
- 复杂前端产品化
- 推荐算法、评分评论、图谱可视化等非 MVP 能力

## 架构原则

采用"双入口 + 安全与规范 + 核心治理 + 工具支撑"的分层架构：

```
上传 / 导入入口          Discover / 查询入口
       │                        │
       ▼                        │
  能力契约与格式规范              │
  （类型化 Manifest 校验）        │
       │                        │
       ▼                        │
  安全扫描与风险准入              │
       │                        │
       ▼                        ▼
       核心治理层（统一治理）
       │
       ▼
  工具支撑层（存储、日志、审计、监控、外部扫描器）
```

- **上传 / 导入入口**：接收外部能力包（JSON/YAML/ZIP），经格式校验和安全扫描后进入治理层。
- **Discover / 查询入口**：Runtime 通过该入口查询和解析已发布能力。
- **能力契约与格式规范**：按资产类型使用对应的 Manifest Spec 进行校验，确保格式可解析。
- **安全扫描与风险准入**：所有上传内容须通过安全扫描，`blocking` 级别风险禁止发布。
- **核心治理层**：统一管理四类资产的版本、审批、发布、下架、回滚、关系。
- **工具支撑层**：提供存储、日志、审计、监控、外部扫描器等横向能力。

## 统一治理与类型化管理

Hub 对四类资产进行统一治理，但各类型使用独立的 Manifest / Schema 规范，不允许把所有类型差异都无限制塞入 `config_json`：

| 资产类型 | Manifest / Schema 规范 | 说明 |
|----------|------------------------|------|
| Agent | Agent Manifest | Agent 模板定义 |
| Skill | Skill Package Spec | 任务能力包定义 |
| Tool | Tool Schema | 工具接口定义 |
| MCP | MCP Config | MCP 连接配置 |

- 后续需要通过类型化 Validator 保证格式可解析。
- `config_json` 字段仅用于存储类型化 Manifest 校验通过后的合法内容，不作为兜底字段。

## Runtime Discover 术语约束

- 统一使用 **Runtime Discover** 作为术语，不再拆分为"Agent 自主搜索""Agent 运行时发现"等多个术语。
- Runtime Discover 指 Hub 向 Runtime 提供能力发现与解析（Resolve）的能力。
- 接口层面区分 `discover`（按条件查询可用的能力列表）和 `resolve`（按标识精确解析能力详情）两个操作。

## 开源路线结论

Hub 不基于任何外部开源框架搭建，仅将以下项目作为设计参考：

| 参考项目 | 参考范围 |
|----------|----------|
| AgentRegistry | Runtime Discover / Registry 接口设计参考 |
| SkillHub | Skill Package Spec 参考 |
| MCP Registry | MCP Config Schema 参考 |

- 不为适配外部框架牺牲 Hub 的统一治理模型。
- Hub 的资产模型、生命周期、审批流、安全准入均为自建。

## 前端约束

- 当前前端只做 PoC 展示和流程验证。
- 不做复杂前端工作流。
- 后续正式前端由前端同学接入 Hub API，本阶段后端和 API 优先。
- 前端代码保持简单，不引入状态管理库、复杂组件库等。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python |
| Web 框架 | FastAPI |
| 数据库 | SQLite（当前 PoC）/ PostgreSQL（准生产目标） |
| ORM | SQLAlchemy |
| 数据库迁移 | Alembic |
| 测试框架 | pytest |
| 前端（后续） | Vue 3 + Element Plus（基础展示） |

## 禁止引入的技术

以下技术在 MVP / Stage 1+ 开发阶段明确禁止引入，选择方案时注意规避：

- Django、Java、Celery、Redis
- OpenSearch / Elasticsearch
- Harbor、Cosign、OPA、Kyverno

以上限制适用于当前 MVP / Stage 1+ 开发阶段；后续如进入准生产阶段需要 Redis、Celery、对象存储、网关等组件，必须先经过专项方案评审，不得自行引入。

## 目录约束

- 项目根目录为 `UnionAgent-Hub/`
- 所有开发文件（后端代码、前端代码、测试代码、文档、配置）必须创建在 `UnionAgent-Hub/` 及其子目录内
- 禁止在 `UnionAgent-Hub/` 目录外（如 `/home/xiaox/projects/union/`）创建任何文件
- 禁止在 `UnionAgent-Hub/` 目录外创建 `backend`、`frontend`、`docs`、`README` 等目录或文件

## 开发规则

### 1. 先 Plan，再 Build

对于复杂任务（涉及多文件修改、新增模块、架构调整），必须先输出计划，经确认后再编码。简单任务（单文件小改、修复拼写等）可跳过。

### 2. 修改前说明

每次修改代码前，必须说明：
- 修改目的
- 涉及的文件列表

### 3. 目录约束

严格遵守目录约束，所有文件必须落在 `UnionAgent-Hub/` 目录内。

### 4. 技术栈约束

严格遵守技术栈选型，不引入禁止列表中的技术。如需引入新依赖，需说明理由。

### 5. 阶段定位

Stage 0 PoC 已完成，当前进入 Stage 1+。Hub 定位为"能力资产治理中心 + Runtime Discover 服务"。代码保持简单、清晰、可测试，避免过度设计。

### 6. 文档同步

每个功能实现后，同步更新 `docs/` 目录下的相关文档。

### 7. 代码风格

- 代码保持简单、清晰、可测试
- 遵循 Python 社区惯例（PEP 8）
- 类型注解优先
- 函数/方法保持单一职责

### 8. Git 约束

以下文件不得提交到 Git：
- `opencode.json`（已有）
- `.env` 及任何含环境变量的文件
- API key、Token、密钥等敏感信息
- 上传文件、临时文件

### 9. 功能复盘与能力证据

每个重要功能完成后，建议按 `docs/engineering_evidence/feature_evidence_template.md` 补充能力证据材料。小修小改可跳过。材料放入 `docs/engineering_evidence/{feature-key}/` 目录。

## 资产定义

### HubItemType

| 类型 | 标识 | 说明 |
|------|------|------|
| Agent | `agent` | 行业/场景 Agent 模板资产（非运行实例） |
| MCP | `mcp` | MCP 配置资产（不托管 MCP Server） |
| Skill | `skill` | 任务能力包 |
| Tool | `tool` | 工具定义资产（Hub 不负责执行调用） |

### SourceType

| 来源 | 标识 | 说明 |
|------|------|------|
| 预置 | `preset` | 内置/预置能力 |
| 手动 | `manual` | 手动注册 |
| 上传 | `upload` | 上传包导入 |

## 生命周期状态

### HubItemStatus（资产状态）

| 状态 | 标识 | 说明 |
|------|------|------|
| 草稿 | `draft` | 初始编辑态 |
| 待审核 | `pending_review` | 已提交，等待审核 |
| 已发布 | `published` | 审核通过，对外可用 |
| 已驳回 | `rejected` | 审核未通过 |
| 已禁用 | `disabled` | 被管理员禁用 |
| 已归档 | `archived` | 历史归档 |

### HubItemVersionStatus（版本状态）

| 状态 | 标识 | 说明 |
|------|------|------|
| 草稿 | `draft` | 版本初始编辑态 |
| 待审核 | `pending_review` | 版本已提交审核 |
| 审核通过 | `approved` | 审核通过，待发布 |
| 已驳回 | `rejected` | 审核未通过 |
| 已发布 | `published` | 当前生效版本 |
| 已弃用 | `deprecated` | 已被新版本替代 |
| 已归档 | `archived` | 历史归档 |

### RiskLevel（风险等级）

| 等级 | 标识 | 说明 |
|------|------|------|
| 低 | `low` | 低风险 |
| 中 | `medium` | 中风险 |
| 高 | `high` | 高风险 |
| 阻断 | `blocking` | 阻断级风险，默认禁止发布 |

## 业务规则

- 预置内容和普通内容走相同的生命周期流程
- 版本更新默认需要重新审批
- 只有审批通过的版本才能发布
- 回滚必须记录操作人、原因和时间
- 下架后不允许新发现/新引用，但允许已有引用继续使用
- `blocking` 风险等级默认禁止发布
- Runtime Discover 接口（discover / resolve）为 P1 重点，后续实现
- 支持回滚到历史已发布版本

## 开发优先级

### Stage 0（已完成）

- HubItem / HubItemVersion 数据模型
- CRUD
- 生命周期管理（状态流转）
- 发布审批
- 安全扫描（RiskLevel）
- 预置能力管理
- 包导入
- 版本回滚
- 基础前端 PoC
- 一键启动
- 94 tests passed

### Stage 1（当前优先）

- HubItemRelation 能力关系管理
- 关系 API
- 关系 Service
- 关系测试
- 关系文档
- 前端只做最小展示或暂不处理

### Stage 2（后续）

- Manifest Spec v0.1
- Agent Manifest / Skill Package Spec / Tool Schema / MCP Config 的最小校验框架
- 类型化 Validator
- 导入 warning / error 机制

### Stage 3（后续）

- Runtime Discover / Resolve P0
- 只返回 published / discoverable / non-blocking 能力
- Resolve 返回 manifest、schema、permission、relations 等运行态元数据
- 不执行能力

### Stage 4（后续）

- manifest 下载
- 版本能力包下载
- 管理态导出

### Stage 5（工程化）

- PostgreSQL + Alembic 迁移
- 对象存储预留
- 外部扫描器 Adapter 预留
- 鉴权上下文预留

### Stage RBAC（当前优先，部分已完成）

- RBAC-0：文档设计（`docs/07_rbac_approval_design.md` + `docs/13_rbac_auth_integration_plan.md`）✅
- RBAC-1：AuthContext 标准化 + actor_id 日志 ✅
- RBAC-2：管理态 RBAC 中间件 / Depends ✅
- RBAC-3B：ApprovalPolicy 接口 + AllowAll ✅
- RBAC-3C-0：operator → actor_id 迁移 ✅
- RBAC 决策：策略决策收敛（`docs/14_rbac_decision_record.md`）✅
- RBAC-3C：四眼原则（`HUB_FOUR_EYES_REQUIRED`，默认关闭）✅
- RBAC-3D-1：created_by 写入端修复（AuditContext.actor_id 优先）✅
- RBAC-3D-2：对象级 ownership 策略 ✅
- RBAC-4：Runtime Consumer 权限 + ScopedCapabilityAccessPolicy ✅
- RBAC-5：OIDC / Gateway 对接（P2）
- MT-0：多租户设计（`docs/24_multi_tenancy_design.md`）✅
- MT-1 ~ MT-5：多租户实现（待 Build）

### 暂不做

- Runtime 执行
- MCP Server 托管
- 工作流编排
- 完整 IAM
- 推荐算法
- 评分评论
- 图谱可视化
- 复杂前端产品化

## 项目结构

```
UnionAgent-Hub/
├── backend/
│   ├── app/
│   │   ├── api/            # API 路由
│   │   ├── core/           # 配置、依赖注入、request_id、logging、auth/rbac 等
│   │   ├── db/             # 数据库连接、session 管理
│   │   ├── models/         # SQLAlchemy/SQLModel 模型
│   │   ├── schemas/        # Pydantic 请求/响应模型
│   │   ├── services/       # 业务逻辑层
│   │   ├── manifests/      # 类型化 Manifest / Schema 校验
│   │   ├── scanners/       # 安全扫描器
│   │   ├── adapters/       # 外部扫描器接入接口
│   │   └── policies/       # 访问策略接口（RBAC / Capability / Approval）
│   ├── tests/              # 测试代码
│   ├── alembic/            # Alembic 迁移
│   └── pyproject.toml      # 后端项目配置与依赖
├── frontend/               # 前端（后续）
├── docs/                   # 文档
├── AGENTS.md               # 本文件
├── README.md
└── .gitignore
```
