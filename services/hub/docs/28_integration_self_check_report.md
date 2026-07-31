# 联调前自检报告

> 生成时间：2026-06-09
> 分支：`feature/runtime-discover-p1`
> 自检范围：功能状态校准、核心闭环验证、防泄露检查

## 1. 环境信息

| 项目 | 值 |
|------|-----|
| 分支 | `feature/runtime-discover-p1` |
| 最新 commit | `7ff505c feat: add tenant filtering for runtime discover` |
| 测试基线 | **826 passed** (SQLite backend, 0 failed, 1 warning) |
| Python | 3.12.3 |
| DB | SQLite (PoC) / PostgreSQL 15 (target) |
| Auth mode | `dev` (default, equiv. platform_admin) |
| Betterleaks | v0.x (binary at `/usr/local/bin/betterleaks`) |
| Gitleaks | v8.x (binary at `/usr/local/bin/gitleaks`) |
| Working tree | not clean (staged deletions + modified .gitignore) |

## 2. 功能闭环结果

### 2.1 管理态治理闭环

| 步骤 | 状态 | 说明 |
|------|------|------|
| OpenAPI import | 已实现 | `POST /api/hub/imports/openapi` - multipart file upload |
| 生成 Tool draft | 已实现 | 从 OpenAPI spec 抽取 operations 创建 tool items |
| submit-review | 已实现 | `POST /api/hub/items/{id}/lifecycle/submit` |
| 自动 scan | 已实现 | submit 时触发 CompositeScanner (RuleScanner + 可选 external) |
| approve | 已实现 | `POST /api/hub/approvals/{version_id}/approve` |
| publish | 已实现 | `POST /api/hub/items/{id}/lifecycle/publish` |
| export | 已实现 | `GET /api/hub/exports/items/{id}/versions/{vid}/package` |
| scan-report | 已实现 | `GET /api/hub/scans/{version_id}/report` |

**备注**：OpenAPI import 需有效的 OpenAPI 3.x spec 文件；minimal_openapi.json 夹具因 manifest validation 被跳过属于正常行为（GET endpoint 不在默认 tool generation 范围内）。

### 2.2 Runtime Discover / Resolve

| 端点 | 状态 | 说明 |
|------|------|------|
| `GET /runtime/capabilities/discover` | 已实现 | 过滤 published + discoverable + non-blocking；type/keyword/risk_level_max 参数；分页 |
| `GET /runtime/capabilities/{id}/resolve` | 已实现 | 返回 manifest_json/config_json/input_schema/output_schema/permission_json/runtime_compatibility；关系深度限制（1-3）；依赖展开+警告 |
| `GET /runtime/capabilities/{id}/manifest` | 已实现 | 复用 resolve 逻辑，strip status，添加 exported_at |
| `GET /runtime/capabilities/{id}/tool-definition` | 已实现 | OpenAI function-calling 格式；仅 tool 类型；输入 schema 规范化 |
| 关系 dependency depth | 已实现 | BFS 递归展开；cycle/max_depth/optional_unavailable/policy_denied 警告 |
| Relation dependency warnings | 已实现 | 5 种警告类型 |
| Runtime role/scope | 已实现 | `runtime_consumer` 角色 + 4 种 scope 检查；`platform_admin` bypass |
| Workload/版本化 | 未实现 | 不在本阶段 |

### 2.3 RBAC

| 项目 | 状态 | 说明 |
|------|------|------|
| AuthContext | 已实现 | actor_id, actor_type, roles, scopes, organization_id, workspace_id, display_name, email, groups |
| HUB_AUTH_MODE | 已实现 | `dev` / `header` / `none`；默认 `dev` |
| 管理态 RBAC（角色+权限+端点检查） | 已实现 | 8 个角色，19 个权限；42 处端点检查 |
| ApprovalPolicy 接口 | 已实现 | Protocol with 5 methods |
| AllowAllApprovalPolicy | 已实现 | **当前默认** |
| DefaultApprovalPolicy（四眼原则） | 已实现 | `can_approve` 含 submitter != approver 检查；`HUB_FOUR_EYES_REQUIRED` 默认 false；仅在测试中使用 |
| 对象级 ownership | 已实现 | `require_asset_ownership` / `require_asset_ownership_from_version`；12 处端点检查 |
| Runtime Consumer 角色 | 已实现 | empty management perms；role + scope 双检 |
| RBAC-5 Gateway/OIDC | **仅设计** | 无代码实现；设计文档 `docs/16_gateway_oidc_integration_design.md` |
| 四眼原则生产默认 | **未启用** | 当前默认 AllowAll；需显式设置 `HUB_FOUR_EYES_REQUIRED=true` |

### 2.4 Multi-Tenancy

| 项目 | 状态 | 说明 |
|------|------|------|
| MT 模型字段（org_id / ws_id / visibility_scope） | 已实现 | HubItem + HubItemVersion + HubItemRelation；**nullable**（非 NOT NULL） |
| 写路径 tenant 传播 | 已实现 | create/import/relation/version/lifecycle 均传播；**update 有 gap** |
| 管理态 API tenant 过滤 | 已实现 | list/detail/update/lifecycle/approval/scan/export/version 均 guard；`GET /hub/items/{id}/relations` 有 gap |
| Runtime Discover tenant 过滤 | 已实现 | `can_runtime_access_item` 检查；post-query Python 层过滤 |
| Runtime Resolve tenant guard | **未实现** | `resolve()` 不调用 `can_runtime_access_item` |
| Runtime Manifest tenant guard | **未实现** | 复用 resolve，同 gap |
| Runtime Tool Definition tenant guard | **未实现** | 复用 resolve，同 gap |
| Dependency cross-tenant 检查 | **未实现** | 关系创建和依赖展开均无跨租户校验 |
| Storage tenant prefix | **未实现** | MT-4 设计已出，代码未落地 |
| Scoped role binding | **仅设计** | MT-5 明确推迟，设计文档标记 P2 |

### 2.5 安全扫描

| 项目 | 状态 | 说明 |
|------|------|------|
| RuleScanner | 已实现 | 内置规则（prompt injection / tool abuse / secret / command / permission）；critical=blocking |
| CompositeScanner | 已实现 | 串联 RuleScanner + external adapters |
| BetterleaksScannerAdapter | 已实现 | `betterleaks dir` CLI; default timeout 30s; evidence sanitized（strip Secret/Match/Line） |
| GitleaksScannerAdapter | 已实现 | `gitleaks dir` CLI; default timeout 30s; evidence sanitized |
| scanner_error 处理 | 已实现 | graceful degradation（`scanner_error:<name>` finding, low severity, never blocking） |
| evidence 脱敏 | 已实现 | `_STRIP_FIELDS` frozenset（Secret/Match/Line/Commit/Author/Email/Date/Message） |
| Betterleaks 默认 disabled | 已实现 | `betterleaks_enabled: bool = False` |
| Gitleaks 默认 disabled | 已实现 | `gitleaks_enabled: bool = False` |
| License 确认 | **待法务** | Betterleaks/Gitleaks MIT license，法务未确认 |

### 2.6 Storage

| 项目 | 状态 | 说明 |
|------|------|------|
| LocalStorageAdapter | 已实现 | 写入 `.hub_storage/`；path-traversal 保护 |
| InMemoryStorageAdapter | 已实现 | 测试专用 |
| StorageAdapter Protocol | 已实现 | put/get/exists/delete/presign 接口 |
| Import package original 保存 | 已实现 | `packages/{item_id}/{version_id}/original.{ext}`；best-effort |
| OpenAPI spec 保存 | 已实现 | `imports/openapi/{batch_id}/original.{ext}`；best-effort |
| Export cache | 已实现 | `exports/items/{id}/versions/{vid}/capability.zip`；cache_hit/cache_miss event log |
| **Storage default** | **disabled** | 需 `STORAGE_BACKEND=local` 才能启用 |
| S3 / MinIO / CommonStorage | **未实现** | 仅设计注释 |
| .hub_storage git 保护 | 已实现 | `.gitignore` 中无对应规则；需确认**未入库** |

### 2.7 PostgreSQL / Alembic

| 项目 | 状态 | 说明 |
|------|------|------|
| docker-compose.pg.yml | 已实现 | postgres:15-alpine; hub_user/hub_password/hub_poc; pgdata volume |
| DATABASE_URL 配置 | 已实现 | `postgresql+psycopg://hub_user:hub_password@localhost:5432/hub_poc` |
| tenant 字段 migration | 已实现 | `ce89fa2e4f30_add_tenant_metadata.py` |
| **Live PG smoke** | **待验证** | 最近 MT migration 未在 live PG 上验证；标记为"联调前需人工确认" |

### 2.8 HOC (Harness / OfficeClaw Compatibility)

| 项目 | 状态 | 说明 |
|------|------|------|
| docs/21 (compat design) | 已设计 | 完整设计文档；状态标注"真实 schema 待对接方确认，代码未落地" |
| docs/22 (schema request) | 已设计 | 对接方问卷；待发送 |
| compat API | **未实现** | 无代码；等待下游提供真实 schema |
| Harness/OfficeClaw schema | **待外部** | 未获取 |

## 3. 安全自检结果

### 3.1 Secret 文本扫描

- `git grep` 对源代码（排除 tests/、tools/spikes/、docs/）搜索 secret 模式：仅命中 scanner 规则定义代码和 docker-compose.pg.yml（本地开发凭证），无真实 secret 泄露。
- 测试文件中的 `sk-*` 字符串均为明确标记的 fake/dummy 数据。
- docker-compose.pg.yml 中的 `POSTGRES_PASSWORD` 为本地开发凭证，不构成泄露风险。

### 3.2 Betterleaks / Gitleaks 仓库扫描

| 工具 | Findings | 说明 |
|------|----------|------|
| Betterleaks | 8 | 均为测试 fixture 中的 fake secret 模式匹配（`--redact` 已脱敏） |
| Gitleaks | 17 | 同上（`--redact` 已脱敏） |

Temporary report files 已清理，未提交、未查看原文。

### 3.3 Staged diff 检查

当前 staged changes:
- `D hub-demo` - git submodule 引用删除
- `D docs/整体架构图.pptx` - 二进制文档删除
- `D docs/设计文档样例.docx` - 二进制文档删除
- `M .gitignore` - 新增 hub-demo/ hub-stage6-demo/ 忽略规则（**注意：该文件的修改尚未 staged**）

**staged 内容不含 secret/token/敏感信息。**

### 3.4 不应入库文件检查

| 检查项 | 结果 |
|--------|------|
| `.env` | 未找到（gitignored） |
| `*.db` / `*.sqlite` | 残留 `test_verify.db` 已清理 |
| `.hub_storage/` | 不存在 |
| `report.json` | 不存在 |
| `*.log` | gitignored |
| `.venv/` | gitignored |
| `__pycache__/` | gitignored |
| `opencode.json` | gitignored |
| Betterleaks/Gitleaks 二进制 | 不在仓库内 |
| fake secret 文件 | 未提交临时文件 |
| `docs/paper/` (PDF) | untracked，未 staged |

## 4. 未完成 / 待联调事项

| 事项 | 状态 | 优先级 |
|------|------|--------|
| Runtime Resolve tenant guard | **未实现** | P1（联调前建议补） |
| Runtime Manifest tenant guard | **未实现** | P1 |
| Runtime Tool Definition tenant guard | **未实现** | P1 |
| Dependency cross-tenant | **未实现** | P1 |
| Storage tenant prefix | **未实现** | P2 |
| Scoped role binding | 仅设计 | P2 |
| Gateway/OIDC (RBAC-5) | 仅设计 | P2 |
| HOC compat API | 未实现（等 schema） | P2 |
| S3 / CommonStorage | 未实现 | P2 |
| Live PG migration | 待验证 | P1（联调前建议） |
| 四眼原则生产默认 | 未启用（AllowAll） | P2 |
| MT NOT NULL migration | 未执行 | P2 |
| Betterleaks/Gitleaks license 法务确认 | 待确认 | P2 |

## 5. 结论

### 5.1 是否可进入联调

**核心管理态链路 + Runtime Discover 可进入联调。** 826 tests 全部通过，管理态 CRUD/生命周期/审批/扫描/导入导出均可用。

### 5.2 联调优先接口

| 优先级 | 接口 | 说明 |
|--------|------|------|
| 1 | `GET /api/runtime/capabilities/discover` | Runtime Discover，已有 tenant 过滤 |
| 2 | `GET /api/runtime/capabilities/{id}/resolve` | Runtime Resolve（注意：无 tenant guard） |
| 3 | `GET /api/runtime/capabilities/{id}/tool-definition` | Tool Definition（注意：无 tenant guard） |
| 4 | OpenAPI import → publish → runtime discover | 端到端管理态→消费态闭环 |
| 5 | RBAC Header 注入（`X-Actor-ID`/`X-Roles`/`X-Organization-ID`/`X-Workspace-ID`） | header mode 验证 |
| 6 | tenant workspace header 过滤 | 跨 workspace 隔离验证 |

### 5.3 联调前建议修复（P0/P1）

1. **Runtime Resolve/Manifest/Tool Definition tenant guard** - 当前仅 Discover 有 tenant 过滤，Resolve 系列未做（MT-3C）
2. **Live PG migration 验证** - 确认 `ce89fa2e4f30_add_tenant_metadata` 在真实 PG 上可执行
3. **Storage default** - 联调前设置 `STORAGE_BACKEND=local` 以启用本地存储

### 5.4 需人工确认事项

- [ ] Live PostgreSQL 是否可用（`docker-compose -f docker-compose.pg.yml up -d`）
- [ ] `alembic upgrade head` 在 live PG 上是否成功
- [ ] Betterleaks / Gitleaks license 法务确认
- [ ] Harness / OfficeClaw schema 是否已获取（等对接方）
- [ ] 联调时 RBAC Header 注入方式确认（Gateway 还是直连）
- [ ] `HUB_AUTH_MODE=header` 下 runtime_consumer 角色行为验证
