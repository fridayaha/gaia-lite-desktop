# Hub 能力市场整体方案设计文档

版本：v1.1 | 日期：2026-06-02 | 状态：BetterleaksScannerAdapter 已实现（v1.3.1）。Gitleaks fallback 已实现。MT-3A `can_runtime_access_item` helper 已完成（802 tests）。

---

## 版本说明

### 已实现

| 能力 | 说明 |
|------|------|
| 资产治理闭环 | CRUD / 生命周期 / 审批 / 回滚 / 归档 |
| Manifest Spec v0.1 | 四类型独立校验框架 |
| 安全与格式准入 P1 | 提交审核自动扫描 + blocking 阻断 |
| Runtime Discover / Resolve P1 | depth 递归展开 + 循环检测 + 依赖警告 |
| OpenAPI → Tool 导入 | OpenAPI 3.x → Hub Tool |
| Tool → Function Calling 导出 | 已治理 Tool → Function Calling 格式 |
| 下载与导出 | 版本包下载 + 管理态导出 |
| 能力关系 | 展示关系 + 运行依赖 |
| request_id + JSON access log | 自动生成/透传 + 结构化日志 |
| 业务事件日志 | 覆盖 Runtime、扫描、导入、生命周期 |
| 内置规则扫描器 | 契约完整性 + Tool/MCP 专项风险检查 |
| 外部扫描器接入接口 | Protocol 预留，供后续外部扫描器接入 |
| 身份上下文与策略扩展位 | AuthContext 数据类 + 访问策略 Protocol |
| RBAC-1：身份上下文标准化 | AuthContext 扩展至 14 字段，Header 注入，dev/header/none 模式 |
| RBAC-2：管理态最小权限门 | 8 角色 × 24 权限矩阵，全部 26 个管理态 API 已加 require_permission |
| RBAC-3B：ApprovalPolicy 接口 | ApprovalPolicy Protocol + AllowAll，Service + API 已接入 |
| RBAC-3C-0：operator→actor_id 迁移 | resolve_effective_operator + mismatch 事件日志 |
| RBAC 决策：策略决策收敛 | `docs/14_rbac_decision_record.md`，10 项决策已确认 |
| RBAC-3C：四眼原则 | `DefaultApprovalPolicy`，`HUB_FOUR_EYES_REQUIRED` 默认关闭 |
| RBAC-3D-1：created_by 写入端修复 | AuditContext.actor_id 优先 |
| RBAC-3D-2：对象级 ownership | 资产所有者只能操作自有资产 |
| RBAC-4：Runtime Consumer 权限 | 入口级 role/scope 检查 + ScopedCapabilityAccessPolicy 资产级过滤 |
| 外部扫描器框架 | CompositeScanner + ExternalFindingNormalizer + FakeExternalScanner（P2-1）；SecretScannerProvider scaffold（P2-2B-lite）；BetterleaksScannerAdapter primary（P2-2B，默认 `HUB_BETTERLEAKS_ENABLED=false`）；GitleaksScannerAdapter fallback（P2-2C，默认 `HUB_GITLEAKS_ENABLED=false`） |
| Alembic + PG 脚手架 | docker-compose.pg.yml 已准备 |
| 文档体系收敛 | 新主文档体系 |
| 测试基线 | **826 passed，0 failed** |

### 已完成设计，代码未落地

| 能力 | 状态 |
|------|:---:|
| Harness / OfficeClaw 兼容层 | 设计完成 |
| BetterleaksScannerAdapter（P2-2B） | ✅ 已实现并验证（v1.3.1，默认 disabled） |
| GitleaksScannerAdapter fallback（P2-2C） | ✅ 已实现并验证，默认 disabled |
| Semgrep CLI Adapter（P2-3） | 设计完成 |
| 多租户设计与实现（MT-0 ~ MT-5） | 📋 MT-0 设计 + MT-1.1 写入 + MT-2 管理态全部 + MT-3A helper + MT-3B Runtime Discover tenant filtering 已完成（826 tests）；MT-3C/3D + MT-4/MT-5 未实现 |
| PostgreSQL 实测方案 | ✅ 已通过冒烟验证（12 项核心 API，postgres:15-alpine） |
| 对象存储方案 | 存储适配器已实现（`docs/19_storage_adapter_design.md`）：LocalStorageAdapter + import/export 接入 + 584 tests |
| /metrics 方案 | 设计待落地 |

### 未完成

| 能力 | 状态 |
|------|:---:|
| 真实 IAM / OIDC / JWT 校验 | ❌ |
| 对象级资产 ownership（owner 只能操作自己资产） | ✅ 已实现，基于 created_by，12 端点已保护 |
| waiver 机制 | ❌ |
| 多租户隔离 | 📋 MT-1.1 写入路径 + MT-2 管理态 tenant 过滤全部完成。MT-3~MT-5 未实现（`docs/24_multi_tenancy_design.md`） |
| Runtime Consumer 权限收紧 | ✅ RBAC-4 已实现（ScopedCapabilityAccessPolicy） |
| 真实 PostgreSQL 环境冒烟 | ✅ 已通过（12 项核心 API 验证） |
| 对象存储接入 | 📋 设计已完成（`docs/19_storage_adapter_design.md`），P1 LocalStorageAdapter 待实现 |
| 外部扫描器实际接入 | 📋 Semgrep / OSV 未接入（Betterleaks + Gitleaks 已实现） |
| /metrics endpoint | ❌ |
| Prometheus / Grafana / OpenTelemetry | ❌ |
| AI Discover | ❌ |
| Harness / OfficeClaw 真实兼容 | ❌ |
| Runtime 执行能力 | ❌（超出 Hub 职责） |

### 方案结论

Hub 是开源能力资产 Hub / 能力市场底座，负责能力治理、准入、发现和解析。它不是 Runtime，不执行能力。部署应运行在统一平台底座之上，复用存储、日志、监控、鉴权、网关、配置和密钥能力。

---

## 一、项目概述

### 1.1 背景

Agent / Skill / Tool / MCP 四类能力资产散落在各种载体中，导致难治理、难复用、难安全消费。Hub 把这些能力变成**可治理、可校验、可扫描、可审批、可发布、可发现、可解析**的资产。

### 1.2 定位

| 角色 | 组件 | 职责 |
|------|------|------|
| 能力供给侧 | **Hub** | 能力资产治理中心 + Runtime Discover 服务 |
| 能力消费侧 | OpenClaw / Runtime | 执行 Agent / Skill / Tool |
| 部署运维 | Hermes | 部署、健康检查、环境配置 |
| 平台底座 | 统一底座 | 数据库、对象存储、日志、监控、鉴权、网关 |

**Hub 不是执行引擎，不托管 MCP Server，不做工作流编排。**

### 1.3 非目标

不执行 Agent/Skill/Tool · 不托管 MCP Server · 不做工作流编排 · 不做完整 IAM · 不做 AI Discover · 不做高并发生产承诺 · 不做商业化计费

---

## 二、总体架构

### 2.1 平台级架构

```
统一平台底座
├── 数据库 · 对象存储 · 日志 · 监控 · 鉴权 · 网关 · 配置 · 密钥

上层组件
├── Hub（治理 + 准入 + 发现 + 解析）
├── OpenClaw / Runtime（能力执行）
├── Hermes（部署运维 + 健康检查）
└── Harness / OfficeClaw（兼容调用方，适配方案已设计，真实 schema 待确认）
```

Hub 不孤立部署成自带全套底座的小系统，而是作为能力中心组件部署在统一底座上。

### 2.2 Hub 双入口双链路

**上传 / 管理链路：**

```
上传/导入 → Manifest 格式校验 → 提交审核自动扫描 → 审批 → 发布 → 治理状态沉淀
                                                    ↓
                                               blocking → 阻断
```

**Runtime 发现链路：**

```
Runtime / OpenClaw → Discover / Resolve / Tool Definition
  → 读取治理状态（已发布 + 可发现 + 非阻断）
  → 返回可消费能力契约
```

关键约束：Discover 不重新扫描；Resolve 不执行能力；Tool Definition 不调用 API。

---

## 三、核心设计原则

1. **统一治理，类型化管理**：四类资产统一生命周期/审批/关系模型，各自独立的 Manifest/Schema 校验
2. **上传先准入，发现走治理**：资产注册时完成校验扫描；Discover/Resolve 直接使用已沉淀的治理结果
3. **Hub 不执行能力**：只回应"有什么、长什么样、谁能用"
4. **Runtime Discover 是确定性治理过滤**：基于状态/风险/可见性/策略过滤，不是搜索，不是 AI 检索
5. **能力契约由准入阶段推动补齐**：Resolve 如实返回已有字段，不生成缺失内容
6. **外部系统通过 Adapter 对接**：兼容层只做映射，不复制 Hub 业务规则
7. **共享底座提供基础设施**：Hub 不重复建设存储、监控、鉴权、网关

---

## 四、技术选型

| 类别 | 技术 | 状态 |
|------|------|:---:|
| 语言 | Python 3.12 | ✅ |
| Web 框架 | FastAPI | ✅ |
| ORM | SQLAlchemy | ✅ |
| Schema | Pydantic v2 | ✅ |
| 数据库 | SQLite（PoC）/ PostgreSQL（准生产目标） | ✅ SQLite + PG 冒烟通过 |
| 迁移 | Alembic（脚手架 + docker-compose 就绪） | ✅ |
| 前端 | Vue 3 + Element Plus（PoC 展示） | ✅ |
| 测试 | pytest（438 passed） | ✅ |
| 日志 | 标准库 + request_id + JSON access log + 事件日志 | ✅ |
| 安全扫描 | 内置规则扫描器 + 外部扫描器适配接口 | ✅ 内置 / 📋 外部待接入 |
| 对象存储 | S3 兼容 Adapter 预留 | 📋 未接入 |
| RBAC | RBAC-0~4 已落地；RBAC-5（OIDC/Gateway）待实施 | ✅ 管理态 + Runtime 双线已落地 |
| AI 检索 | 未接入 | ❌ |

---

## 五、部署方案

### 5.1 PoC 部署

`./start.sh` 一键启动：FastAPI 单进程（SQLite）+ Vite dev server。

### 5.2 单独部署

适用于无统一底座环境的私有化部署。组件：Hub API + Hub Frontend + PostgreSQL + S3/MinIO + Nginx/Gateway + stdout JSON log。

### 5.3 平台集成部署

适用于已有统一底座的企业场景。Gateway 注入 X-Request-ID / actor_id / roles 并路由到 Hub。Hub 使用共享 PostgreSQL（独立 database/schema）和共享对象存储（独立 bucket/prefix），通过 API 对外服务。**其他组件不允许直接读 Hub 数据库。**

### 5.4 PostgreSQL 状态

PG 冒烟验证已通过（2026-05-28），约束条件：
- `HUB_AUTH_MODE=dev`（dev 模式，避免 RBAC Header 干扰）
- `HUB_GITLEAKS_ENABLED=false`（Gitleaks 已单独验证，不纳入 PG 兼容性验证变量）
- 使用 `postgres:15-alpine`（`postgres:16` 待环境就绪后切换）
- 验证 12 项核心 API：health / presets / import（minimal_openapi.json fixture）/ submit / approve / publish / discover / resolve / tool-def / scan-report / DB counts
- Alembic migration 一次通过，无 Enum 重复 CREATE TYPE
- 启动方式：仅后端 uvicorn（`DATABASE_URL=... uvicorn app.main:app`），不启动前端

详见 `docs/03_platform_integration.md`。

---

## 六、服务清单

| 服务能力 | 解决的问题 | 状态 | 后续 |
|----------|-----------|:---:|------|
| 管理态治理 | 资产 CRUD / 生命周期 / 审批 / 回滚 / 归档 | ✅ | — |
| 格式规范 | 四类型 Manifest 校验 | ✅ | Spec 后续版本 |
| 安全与格式准入 | 提交审核自动扫描 + blocking 阻断 | ✅ | 外部扫描器接入 |
| 能力关系 | 展示关系 + 运行依赖 | ✅ | — |
| Runtime Discover / Resolve | 按条件发现、按 ID 解析能力 | ✅ | AI Query Planner（P3） |
| OpenAPI 导入 | OpenAPI 3.x → Hub Tool | ✅ | — |
| Tool Definition 导出 | Hub Tool → Function Calling | ✅ | — |
| 下载与导出 | 版本包 + 管理态导出 | ✅ | 对象存储接入 |
| 可观测性基础 | request_id + access log + 事件日志 | ✅ | metrics / tracing |
| RBAC / 审批 | 角色 + 权限 + 审批流 | 📋 设计完成 | 代码落地 |
| 平台集成适配 | Gateway / Hermes / 底座对接 | 📋 设计完成 | 真实对接 |

---

## 七、核心模块设计

### 7.1 资产治理

四类资产统一生命周期：`draft → pending_review → approved → published → disabled / archived`。支持版本管理、回滚到历史版本、下架后已有引用可继续使用。版本更新默认需重新审批。

### 7.2 Manifest 与能力契约

Manifest Spec v0.1 为四类资产定义类型化契约，各类型有独立规范（Agent Manifest / Skill Package Spec / Tool Schema / MCP Config），共享通用字段（input_schema / output_schema / permission_json / runtime_compatibility）。当前版本为 v0.1，未升级。

详见 `docs/15_manifest_spec_v0_1.md`。

### 7.3 安全与格式准入

提交审核自动触发内置规则扫描：blocking 风险直接阻断（400）；契约完整性检查（Skill/Tool 的 schema/permission 补全）；Tool/MCP 专项检查（危险命令、密钥、不安全端点、无效 transport）。外部扫描器接口已预留，后续路线：Gitleaks / Semgrep / OSV-Scanner（许可证待确认，AGPL/GPL 不进入默认链路）。

详见 `docs/05_admission_security_design.md`。

### 7.4 审批与 RBAC

**已实现（RBAC-3C-0）：**
- 身份上下文标准化（14 字段 AuthContext + Header 注入 + dev/header/none 三种鉴权模式）；
- 管理态最小权限门（8 角色 × 24 权限矩阵，全部 26 个管理态 API 已加 `require_permission`）；
- 角色规范化（trim / lower / hyphen-space→underscore）；
- 权限判断在 API 层通过 FastAPI Depends 执行，不散落在 Service 层；
- ApprovalPolicy Protocol + AllowAllApprovalPolicy（Service + API 已接入）；
- operator → actor_id 可信审计身份迁移（resolve_effective_operator + mismatch 事件）。

**仍未实现：**
- 四眼原则（`HUB_FOUR_EYES_REQUIRED`，已实现 RBAC-3C）；
- 对象级 asset ownership（owner 只能操作自己资产，已实现 RBAC-3D）；
- Runtime Consumer 权限收紧（role/scope 入口检查 + ScopedCapabilityAccessPolicy，已实现 RBAC-4）；
- 多租户隔离（MT-1.1 + MT-2 管理态过滤已完成，MT-3+ Runtime/Storage 未实现）；
- 真实 IAM / OIDC / JWT 校验（计划 RBAC-5）。

详见 `docs/07_rbac_approval_design.md`（设计规格）和 `docs/13_rbac_auth_integration_plan.md`（实施计划）。

### 7.5 能力关系

资产间支持两类关系：展示关系（management scope）和运行依赖（runtime scope）。Resolve 支持 depth 1–3 递归依赖展开、required/optional 规则、循环检测、dependency_warnings。

### 7.6 Runtime Discover / Resolve

**Discover** 按 type / keyword / risk_level 查询，只返回已发布、可发现、非阻断资产。**Resolve** 返回完整能力契约（manifest / schema / permission / relations / dependencies），policy deny 时返回 404 隐藏资产存在性。**Tool Definition** 将已治理 Tool 导出为 Function Calling 格式，仅 Tool 类型可用。

Hub 不执行能力，Tool Definition 不是执行 Tool。

详见 `docs/04_runtime_discover_design.md`（后续补充）。

### 7.7 协议对齐

已实现 OpenAPI 3.x → Hub Tool → Function Calling 协议闭环。MCP Config / Agent Card / A2A / Skills Package 为后续路线。Harness / OfficeClaw 兼容方案已设计，真实 schema 待确认，不声称已兼容。

详见 `docs/06_protocol_alignment.md`（后续补充）。

### 7.8 可观测性

已实现：request_id 自动生成/透传/返回、JSON access log（hub.http.request）、业务事件日志（hub.event，覆盖 Runtime、扫描、OpenAPI 导入、生命周期）。未实现：/metrics、Prometheus、OpenTelemetry、tracing。stdout JSON 由统一底座日志平台采集。

详见 `docs/12_observability_logging_design.md`。

### 7.9 下载与导出

支持版本包下载和管理态导出。上传包、导出包、附件等尚未接入统一对象存储，当前以 PoC 本地/按需生成方式处理。

---

## 八、组件协同

### 8.1 OpenClaw / Runtime

Hub 提供 Discover / Resolve / Tool Definition；Runtime 负责执行，后续可回传执行反馈。Runtime 不直接读 Hub 数据库。

### 8.2 Hermes

负责部署、初始化、健康检查（消费 /health），可执行 Alembic migration。不需理解 Agent/Skill/Tool/MCP 业务语义。

### 8.3 Harness / OfficeClaw

通过薄 Adapter 对接，只做 request/response 映射。不复制 Hub 业务规则。**真实 schema 需后续确认，不声称已兼容。** 不直接读 Hub 数据库。

---

## 九、已知限制

| 限制 | 说明 |
|------|------|
| 无 AI Discover | Discover 是确定性过滤，不是 AI 检索 |
| 无真实 IAM / OIDC / JWT | 管理态 RBAC 已实现（Header 注入鉴权），但真实 IAM 未接入 |
| 无对象级 ownership | ✅ 已实现，基于 created_by，12 端点已保护，owner_id / group ownership 未实现 |
| 无外部扫描器实际接入 | 仅内置规则扫描器 |
| 无对象存储 | 📋 设计已完成（`docs/19_storage_adapter_design.md`），上传/导出包尚未接入统一对象存储 |
| 无 /metrics | Prometheus / Grafana 未接入 |
| PG 未真实冒烟 | 脚手架就绪，真实环境待测 |
| Harness / OfficeClaw 未兼容 | 设计方案完成，schema 待确认 |
| 不执行能力 | 超出 Hub 职责 |
| 不托管 MCP Server | 超出 Hub 职责 |
| 不做工作流编排 | 超出 Hub 职责 |

---

## 十、后续路线

**P1：**
- RBAC-3：审批流角色绑定 + 对象级 ownership 设计 ✅ 已完成
- RBAC-4：Runtime Consumer 权限收紧 + ScopedCapabilityAccessPolicy ✅ 已完成
- PostgreSQL 真实环境验证
- 外部扫描器 license 确认
- Harness / OfficeClaw schema 获取
- 对象存储方案 ✅ 已设计（`docs/19_storage_adapter_design.md`）
- /metrics 设计落地

**P2：**
- RBAC-5：OIDC / Gateway 真实对接
- 外部扫描器实际接入（Gitleaks / Semgrep）
- Response Profile / compatibility adapter
- 对象存储接入
- Prometheus / Grafana
- 正式前端

**P3：**
- AI Query Planner · Eval Sandbox · 运行态观测反馈 · 资产信誉 · CLI/CI · K8s/Helm

---

## 十一、文档索引

| 文档 | 说明 |
|------|------|
| `docs/00_docs_index.md` | 文档索引 |
| `docs/02_solution_design.md` | 本文档 — 最新整体方案设计主文档 |
| `docs/03_api_design.md` | API 设计 |
| `docs/03_platform_integration.md` | 平台集成部署设计 |
| `docs/04_runtime_discover_design.md` | Runtime Discover 设计（后续补充） |
| `docs/05_admission_security_design.md` | 安全与格式准入设计 |
| `docs/06_protocol_alignment.md` | 协议对齐设计（后续补充） |
| `docs/07_rbac_approval_design.md` | RBAC 与审批设计 |
| `docs/08_roadmap_workload.md` | Roadmap 与工作量 |
| `docs/09_demo_guide.md` | 演示指南（后续补充） |
| `docs/10_api_reference_for_demo.md` | 演示 API 参考（后续补充） |
| `docs/12_observability_logging_design.md` | 可观测性与结构化日志设计 |
| `docs/13_rbac_auth_integration_plan.md` | RBAC / 身份认证对接方案 |
| `docs/15_manifest_spec_v0_1.md` | Manifest Spec v0.1 |
| `docs/16_gateway_oidc_integration_design.md` | Gateway / OIDC 集成设计 |
| `docs/17_external_scanner_adapter_design.md` | 外部扫描器 Adapter 设计 |
| `docs/18_secret_scanner_provider_selection.md` | Secret Scanner Provider 选型记录 |
| `docs/19_storage_adapter_design.md` | 对象存储适配器设计 |
| `docs/23_secret_scanner_deployment_design.md` | Secret Scanner 部署方案设计 |
| `docs/24_multi_tenancy_design.md` | 多租户设计与数据模型影响分析（MT-0） |
| `docs/demo_samples/` | 四类资产演示样例 |
| `docs/validation/` | 技术验证方案目录 |
