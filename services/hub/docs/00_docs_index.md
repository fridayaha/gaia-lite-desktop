# Hub 文档索引

## 主文档

| 编号 | 文档 | 说明 |
|:---:|------|------|
| 00 | `docs/00_docs_index.md` | 本文档 |
| 01 | `docs/01_executive_brief.md` | 执行摘要（后续补充） |
| 02 | `docs/02_solution_design.md` | 最新整体方案设计主文档 |
| 03 | `docs/03_platform_integration.md` | 平台集成部署设计 |
| 04 | `docs/04_runtime_discover_design.md` | Runtime Discover 设计（后续补充） |
| 05 | `docs/05_admission_security_design.md` | 安全与格式准入设计 |
| 06 | `docs/06_protocol_alignment.md` | 协议对齐设计（后续补充） |
| 07 | `docs/07_rbac_approval_design.md` | RBAC 与审批设计 |
| 08 | `docs/08_roadmap_workload.md` | Roadmap 与工作量 |
| 09 | `docs/09_demo_guide.md` | 演示指南（后续补充） |
| 10 | `docs/10_api_reference_for_demo.md` | 演示 API 参考（后续补充） |
| — | `docs/20_current_baseline_summary.md` | 当前稳定基线说明 + 未完成能力清单 |
| — | `docs/24_multi_tenancy_design.md` | 多租户设计与数据模型影响分析（MT-0，设计完成，代码未实现） |
| — | `docs/25_mt1_implementation_plan.md` | MT-1 多租户数据模型实现计划 |
| — | `docs/26_skillvetbench_reference_analysis.md` | SkillVetBench 论文参考分析与安全路线对齐 |
| — | `docs/27_agentic_risk_dimensions_design.md` | Agentic Risk Dimensions 设计 |

## 专项设计

| 编号 | 文档 | 说明 |
|:---:|------|------|
| — | `docs/03_api_design.md` | API 设计 |
| — | `docs/12_observability_logging_design.md` | 可观测性与结构化日志设计 |
| — | `docs/13_rbac_auth_integration_plan.md` | RBAC / 身份认证对接实施方案（本阶段输出） |
| — | `docs/14_rbac_decision_record.md` | RBAC 策略决策记录（决策收敛，已确认） |
| — | `docs/15_manifest_spec_v0_1.md` | Manifest Spec v0.1 |
| — | `docs/16_gateway_oidc_integration_design.md` | RBAC-5 Gateway / OIDC 对接设计方案 |
| — | `docs/17_external_scanner_adapter_design.md` | 外部扫描器 Adapter 设计 |
| — | `docs/18_secret_scanner_provider_selection.md` | Secret Scanner Provider 选型记录 |
| — | `docs/19_storage_adapter_design.md` | 对象存储适配器设计 |
| — | `docs/21_harness_officeclaw_compat_design.md` | Harness / OfficeClaw 兼容层设计 |
| — | `docs/22_harness_officeclaw_schema_request.md` | Harness / OfficeClaw Schema 对接包 |
| — | `docs/23_secret_scanner_deployment_design.md` | Secret Scanner 部署方案设计 |

## 历史文档

历史文档已从主仓库移除，可通过 Git 历史追溯：

```bash
git log -- docs/
```

如需查找特定历史文档，查看 commit 历史即可找到完整内容。

## 演示样例

| 目录 | 说明 |
|------|------|
| `docs/demo_samples/` | 四类资产演示样例（Agent/Skill/Tool/MCP） |

## 技术验证

| 目录 | 说明 |
|------|------|
| `docs/validation/` | 技术验证方案目录 |

## 工程能力证据

工程复盘与能力沉淀材料，不作为产品主方案入口：

| 文档 | 说明 |
|------|------|
| `docs/engineering_evidence/README.md` | 工程能力证据沉淀规范 |
| `docs/engineering_evidence/feature_evidence_template.md` | 功能证据模板 |
| `docs/engineering_evidence/rbac_runtime_consumer_policy.md` | RBAC-4 Runtime Consumer 权限工程证据 |
