# Hub Roadmap 与工作量

版本：v0.28 | 日期：2026-06-02 | 状态：MT-2 管理态全部完成。MT-3A helper + MT-3B Runtime Discover tenant/visibility filtering 已完成（826 tests）。SVB-0~2 完成。MT-3C/3D 未实现。

---

## 已完成

| 阶段 | 内容 | 测试 |
|:---:|------|:---:|
| Stage 0 | PoC 核心闭环（CRUD/生命周期/审批/扫描/回滚/导入/前端） | 94 passed |
| Stage 1 | 能力关系管理（HubItemRelation） | ✅ |
| Stage 2 | Manifest Spec v0.1 类型化校验框架 | ✅ |
| Stage 3 | Runtime Discover / Resolve P0 | ✅ |
| Stage 4 | 下载与导出 P0 | ✅ |
| Stage 5A | Alembic + PostgreSQL 脚手架 | ✅ |
| Stage 5B | ScannerAdapter Protocol + RuleScannerAdapter | ✅ |
| Stage 5C | AuthContext + CapabilityAccessPolicy | ✅ |
| Stage 6 | Runtime Discover P1 + 安全准入 P1 + 协议闭环 | ✅ |
| Stage 7A | request_id + JSON access log | ✅ |
| Stage 7B | 业务事件日志（discover/resolve/scan/import/lifecycle） | ✅ |
| Stage RBAC-0 | RBAC / 身份认证对接方案设计（文档阶段） | ✅ |
| Stage RBAC-1 | AuthContext 标准化 + Header 注入 + actor_id 日志 | ✅ 313 passed |
| Stage RBAC-2 | 管理态 RBAC 中间件 / Depends | ✅ 407 passed |
| Stage RBAC-3B | ApprovalPolicy 接口 + 默认 AllowAll + submit_item 一致性 | ✅ 424 passed |
| Stage RBAC-3C-0 | operator → actor_id 可信审计身份迁移 | ✅ 438 passed |
| RBAC 决策 | RBAC 策略决策收敛（`docs/14_rbac_decision_record.md`） | ✅ 已确认 |
| Stage RBAC-3C | 四眼原则实现（`HUB_FOUR_EYES_REQUIRED`，默认关闭） | ✅ 445 passed |
| Stage RBAC-3D-1 | created_by 写入端修复（AuthContext.actor_id 优先） | ✅ 458 passed |
| Stage RBAC-3D-2 | 对象级 ownership 策略实现 | ✅ 471 passed |
| Stage RBAC-4 | Runtime Consumer role/scope 入口权限 + ScopedCapabilityAccessPolicy | ✅ 509 passed |
| Stage SVB-0 | SkillVetBench 论文分析（`docs/26_skillvetbench_reference_analysis.md`） | ✅ |
| Stage SVB-1 | 风险分类 taxonomy 文档增强（docs/05 三类七种映射 + Permission Tier + Agentic Risk Dimensions） | ✅ |
| Stage SVB-2 | Agentic Risk Dimensions 设计（`docs/27_agentic_risk_dimensions_design.md`） | ✅ |

---

## 待实施

| 阶段 | 内容 | 优先级 |
|:---:|------|:---:|
| Stage RBAC-5 | OIDC / Gateway 真实对接 | P2 |
| Stage HOC-0 | Harness / OfficeClaw schema 对接确认（`docs/21_harness_officeclaw_compat_design.md`） | ✅ 设计完成 |
| Stage HOC-0.5 | Schema 对接包（`docs/22_harness_officeclaw_schema_request.md`） | ✅ 待发送对接方 |
| Stage HOC-1 | ResponseProfileFormatter + 测试 | 📋 设计完成，待 Build |
| Stage HOC-2 | Harness compat endpoint | 🔲 |
| Stage HOC-3 | OfficeClaw compat endpoint | 🔲 |
| Stage HOC-4 | 真实客户端联调 | 🔲 |
| Stage 7D | OpenTelemetry tracing + metrics + logs 统一 | P2 |
| Stage 8A | PostgreSQL 实测（环境就绪后） | ✅ 冒烟验证通过（12 API，608 pytest） |
| Stage 8B-0 | 对象存储适配器设计（`docs/19_storage_adapter_design.md`） | ✅ 已设计 |
| Stage 8B-1 | LocalStorageAdapter 实现 + 上传/导出/缓存接入 | ✅ 584 passed |
| Stage 8B-2 | S3StorageAdapter + pre-signed URL + 扫描附件 | P2 |
| Stage 8B-3 | 生命周期管理（temp 清理 / retention / SBOM 附件） | P3 |
| Stage 8C | 外部扫描器 Adapter（Semgrep/OSV） | P1 |
| Stage 8C-1 | CompositeScanner + FakeExternalScanner + Normalizer（P2-1） | ✅ 530 passed |
| Stage 8C-2 | Secret Scanner Provider 选型评估（Betterleaks primary, Gitleaks fallback） | ✅ 已设计（`docs/18_secret_scanner_provider_selection.md`） |
| Stage 8C-2B-lite | provider-neutral scaffold：SecretScannerProvider / MockSecretScannerAdapter / redaction helpers（P2-2B-lite） | ✅ 556 passed |
| Stage 8C-2B | BetterleaksScannerAdapter（P2-2B，真实 CLI 实测后实现） | ✅ 633 passed（默认 `HUB_BETTERLEAKS_ENABLED=false`） |
| Stage 8C-2C | GitleaksScannerAdapter（compatibility fallback，P2-2C） | ✅ 608 passed（默认 disabled） |
| Stage 8C-2D | Secret Scanner 部署方案设计（CLI/Worker/Sidecar/Platform） | ✅ 已设计（`docs/23_secret_scanner_deployment_design.md`） |
| Stage MT-0 | 多租户设计与数据模型影响分析 | ✅ 已设计（`docs/24_multi_tenancy_design.md`） |
| Stage MT-1 | 多租户数据模型：新增列 + DB migration + 回填 | ✅ 681 passed（字段 nullable，查询过滤未实现，PG 离线验证通过） |
| Stage MT-1.1 | tenant 写入路径修复：create/import/approval/lifecycle/scan | ✅ 681 passed（所有写入路径从 AuthContext 或继承 item/version tenant） |
| Stage MT-2A | 管理态 TenantPolicy 基础策略：`can_access_tenant` / `is_same_tenant` / `is_legacy_tenant` + `HUB_TENANT_LEGACY_VISIBLE` | ✅ 720 passed（39 tenant policy tests，未接入 API） |
| Stage MT-2B | HubItem list/detail/update + version list/detail tenant guard | ✅ 737 passed（17 filter tests，已接入 API） |
| Stage MT-2C | lifecycle/approval/scan guard | ✅ 757 passed（20 filter tests，已接入 API） |
| Stage MT-2D | relation/export/import guard + import workspace 匹配修复 | ✅ 771 passed（14 filter tests，已接入 API） |
| Stage MT-3 | Runtime workspace 过滤：Discover/Resolve + visibility_scope | P1 |
| Stage MT-3A | `can_runtime_access_item` helper + unit tests | ✅ 802 passed（28 runtime tests） |
| Stage MT-3B | Runtime Discover tenant/visibility filter + pagination 修正 | ✅ 826 passed（24 discover tests） |
| Stage MT-3C | Resolve/Manifest/Tool Definition tenant guard | 📋 未实现 |
| Stage MT-3D | Dependency cross-tenant behavior（递归展开） | 📋 未实现 |
| Stage MT-4 | Storage tenant prefix：TenantPrefixStorageAdapter | P2 |
| Stage MT-5 | Scoped roles（per workspace role binding，如 IAM 支持） | P2 |
| Stage SVB-2 | Agentic Risk Dimensions 设计（`docs/27_agentic_risk_dimensions_design.md`） | ✅ |
| Stage SVB-3 | Semantic Scanner Adapter 预留设计（docs/17 已有预留章节） | P3 |
| Stage SVB-4 | Eval Sandbox 设计（参考 SkillVetBench Stage 2） | P3 |
| Stage SVB-5 | Internal malicious skill benchmark 样本集 | P3 |
| Stage 9 | IAM / Gateway 身份对接 | P2 |
| Stage 10 | 正式前端接入 | P2 |

## RBAC 决策记录

详见 `docs/14_rbac_decision_record.md`：10 项决策已确认，RBAC-3C 实现边界已明确。

## RBAC-2 已知限制（待后续阶段处理）

| 限制 | 说明 | 计划 |
|------|------|:---:|
| 对象级 ownership | asset_owner 是角色级全局权限，不区分 own/other 资产 | RBAC-3 |
| export:download 偏宽 | 当前给了几乎所有管理角色 | RBAC-3 |
| body.operator 兼容字段 | 不参与鉴权，不是可信身份，DB 审计仍写 body.operator | RBAC-3 |
| Runtime API 已纳入 RBAC-4 | Runtime Consumer role/scope 入口检查 + ScopedCapabilityAccessPolicy | ✅ RBAC-4 |
| header mode 依赖可信 Gateway | 不校验 JWT，默认信任 Gateway 注入 | RBAC-5 |
| OIDC / JWT 未接入 | Hub 自身不校验身份凭证 | RBAC-5 |

---

## 暂不做

| 事项 | 原因 |
|------|------|
| Runtime 执行 | 超出 Hub 职责 |
| MCP Server 托管 | 超出 Hub 职责 |
| 工作流编排 | 超出 Hub 职责 |
| 完整 IAM | 待 RBAC-5 |
| 多租户 MT-3C ~ MT-5 | 待后续阶段（MT-3A/3B Discover 已完成） |
| 完整计费系统 | 非 MVP |
| 推荐/评分/图谱 | 非 MVP |
| Redis / Celery / OpenSearch | 明确禁止 |
| AI Discover | P2 增强 |
| Semantic Scanner | P3（`docs/26` 已分析，不进入准入主链） |
| Eval Sandbox | P3（`docs/26` 已分析，不进入 Hub Core） |

---

## 文档参考

| 文档 | 说明 |
|------|------|
| `docs/02_solution_design.md` | 方案设计 |
| `docs/12_observability_logging_design.md` | 可观测性设计 |
| `docs/13_rbac_auth_integration_plan.md` | RBAC 身份认证对接方案 |
| `docs/07_rbac_approval_design.md` | RBAC 与审批设计 |
| `docs/14_rbac_decision_record.md` | RBAC 策略决策记录 |
| `docs/26_skillvetbench_reference_analysis.md` | SkillVetBench 论文参考分析 |
| `docs/27_agentic_risk_dimensions_design.md` | Agentic Risk Dimensions 设计 |
