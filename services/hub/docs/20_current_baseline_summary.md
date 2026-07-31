# Hub 当前稳定基线说明

日期：2026-06-02 | 版本：v2.2 | 标签：MT-2 管理态全部完成。MT-3A helper + MT-3B Runtime Discover tenant/visibility filtering 已完成（826 tests）。MT-3C/3D 未实现。SVB-0~2 完成。

---

## 1. 当前版本定位

Hub 已从 Stage 0 PoC 进入**准生产能力验证阶段**。核心治理链路（CRUD → 校验 → 扫描 → 审批 → 发布 → Discover/Resolve）在 SQLite + PostgreSQL 双环境下均已验证通过。

---

## 2. 已完成核心能力

| 能力域 | 能力 | 状态 |
|------|------|:--:|
| 资产治理 | Agent / Skill / Tool / MCP CRUD + 生命周期 | ✅ |
| 版本管理 | 版本创建、审核、发布、回滚、归档 | ✅ |
| Manifest 校验 | Manifest Spec v0.1 类型化校验 | ✅ |
| 安全准入 | submit-review 自动扫描 + blocking 阻断 + taxonomy 增强（SVB-1） | ✅ |
| Prompt 注入 | 中英文 prompt injection 检测 | ✅ |
| Tool/MCP 专项 | 危险命令、密钥、不安全端点、无效 transport | ✅ |
| 契约完整性 | input_schema / output_schema / permission 检查 | ✅ |
| 外部扫描器 | CompositeScanner + Gitleaks fallback + Betterleaks primary | ✅ |
| Gitleaks CLI | `gitleaks dir` + 脱敏归一化 | ✅ |
| 导入 | JSON/YAML/ZIP 导入 + OpenAPI 3.x → Tool | ✅ |
| Runtime Discover | 确定性过滤（type/risk/discoverable） | ✅ |
| Runtime Resolve | 递归展开 + 循环检测 + 依赖警告 | ✅ |
| Tool Definition | Tool → Function Calling 格式导出 | ✅ |
| 下载导出 | 版本包下载 + 管理态导出 + LocalStorage 缓存 | ✅ |
| RBAC-1 | AuthContext + actor_id 日志 | ✅ |
| RBAC-2 | 管理态 8 角色 × 24 权限矩阵 | ✅ |
| RBAC-3 | 四眼原则 + created_by + 对象级 ownership | ✅ |
| RBAC-4 | Runtime Consumer role/scope + ScopedCapabilityAccessPolicy | ✅ |
| RBAC-5 | Gateway/OIDC 设计已完成（代码未落地） | 📋 |
| 多租户 | MT-1.1 写入 + MT-2 管理态全部 + MT-3A helper + MT-3B Runtime Discover tenant filtering 已完成（826 tests）；Resolve/Storage 未实现（MT-3C~MT-5 待做） | 📋 |
| 可观测性 | request_id + JSON access log + 业务事件日志 | ✅ |
| 存储 | LocalStorageAdapter 已实现（默认 disabled，需 `STORAGE_BACKEND=local`） | ⚠️ |
| 数据库 | PostgreSQL 真实冒烟（12 API + Alembic） | ✅ |

---

## 3. 当前测试与验证

| 项目 | 结果 |
|------|:--:|
| pytest (SQLite) | **826 passed, 0 failed** |
| MT-2（管理态 tenant 过滤） | ✅ 全部完成（MT-2A~MT-2D） |
| MT-3A（Runtime tenant helper） | ✅ `can_runtime_access_item` + 28 tests |
| MT-3B（Discover tenant filtering） | ✅ 24 discover tests |
| MT-2D relation/export/import guard | ✅ 已接入 API（14 tests）+ import workspace 匹配修复 |
| PG 冒烟 API | 12 项全部通过 |
| Alembic upgrade head | 一次通过 |
| Gitleaks real CLI | 已验证（7 tests） |
| Storage P1 | 已验证（import/export cache） |

---

## 4. 当前未完成能力

| 能力 | 状态 |
|------|:--:|
| OIDC / JWT 真实对接 | 设计完成，代码未落地 |
| Betterleaks | ✅ 已实现并验证（v1.3.1，25 mock + 4 real CLI tests，默认 disabled） |
| Semgrep / OSV | 未接入 |
| tenant/workspace DB 字段 | ✅ MT-1 已完成（字段 nullable + migration backfill） |
| tenant-aware query filtering | MT-2 管理态全部完成（HubItem + lifecycle + approval + scan + relation + export + import） |
| Runtime workspace filtering | Discover 已实现（MT-3B ✅）；Resolve/Manifest/ToolDef **未实现**（MT-3C 待做） |
| Storage tenant prefix | MT-0 设计完成，代码未实现 |
| Scoped role binding per workspace | MT-0 设计完成，待 MT-5 |
| S3 / MinIO / 通用存储 | 暂停，等待统一存储模块架构确认 |
| PostgreSQL 压测 | 未执行 |
| 前端正式化 | PoC 级别 |
| K8s / Helm | 未接入 |
| 完整 IAM / 多租户全量 | MT-1 数据模型已落地；MT-2~MT-5 待后续阶段 |
| License 法务确认 | 未做 |
| Semantic Scanner | 未实现（P3，`docs/26` `docs/27` `docs/17` 已设计，不进入准入主链） |
| Eval Sandbox | 未实现（P3，`docs/26` `docs/27` `docs/17` 已设计，独立服务） |
| Agentic Risk Dimensions scoring | 未实现（SVB-2 设计完成，`docs/27`；P2 可做规则映射原型） |
| Internal malicious benchmark | 未实现（P3，SVB-5 计划） |
| Runtime evidence trace | 未实现（P3，依赖 Eval Sandbox） |

---

## 5. 推荐下一阶段

| 优先级 | 方向 | 前置条件 |
|:--:|------|------|
| A | Harness / OfficeClaw Discover schema 对接确认 | ✅ HOC-0 设计 + HOC-0.5 schema 对接包完成（`docs/21`、`docs/22`），真实 schema 待对接方回复 |
| B | Betterleaks 本地安装 + spike + Adapter 实现 | 安装 Go / Betterleaks |
| C | Semgrep CLI Adapter（P2-3） | semgrep CLI 可用 |
| D | S3 / MinIO / CommonStorageAdapter | 统一存储模块架构明确 |
| E | OIDC / Gateway 真实对接（RBAC-5） | Gateway 环境就绪 |
| F | SkillVetBench 作为安全路线参考（Eval Sandbox / Semantic Scanner / benchmark） | P3 阶段，`docs/26_skillvetbench_reference_analysis.md` |
