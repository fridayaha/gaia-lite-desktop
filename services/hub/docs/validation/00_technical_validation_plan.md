# 技术验证计划

> 文档编号：validation/00
> 版本：v0.1
> 日期：2026-05-15
> 用途：定义 Hub 能力市场后续技术路线的验证工作顶层计划

---

## 1. 验证背景与目标

### 1.1 当前 PoC 状态

当前 Hub PoC 已完成管理态核心闭环。引用 `docs/12_current_status.md`：

- 9 个数据模型、8 个枚举类型
- 完整生命周期管理（submit / publish / disable / archive / rollback）
- 发布审批流程（approve / reject / request_change + blocking 拦截）
- 5 类安全扫描规则（Prompt / Tool / Secret / Command / Permission）
- 能力包导入（JSON / YAML / ZIP）
- Vue 3 + Element Plus 前端管理控制台
- `start.sh` 一键启动
- 94 tests passed, 0 failed

### 1.2 待验证命题

本轮技术验证需要回答以下 8 条关键命题：

| # | 命题 | 核心问题 |
|---|------|----------|
| P1 | 当前 Hub 是否应继续作为主系统 | 治理层是否需要整体替换 |
| P2 | 是否存在可"低侵入集成"的开源能力市场 | 以 Adapter / Wrapper 模式复用 |
| P3 | Backstage Catalog 模型能否作为 Hub 的上层门户 | Catalog → 能力展示，治理逻辑仍走 Hub |
| P4 | AgentRegistry 的 Discover/Resolve 协议是否可兼容 | Runtime 接口层面保持协议对齐 |
| P5 | MCP Registry 的配置规范是否为事实标准 | MCP 类型资产的 manifest schema 对齐 |
| P6 | 安全扫描是否需要接入外部扫描器 | 当前规则引擎覆盖范围 vs 外部工具 |
| P7 | 对象存储和数据库迁移是否应引入成熟组件 | SQLite → PostgreSQL + 文件存储方案 |
| P8 | 是否需要 fork 任一开源方案 | 上游兼容性与维护成本评估 |

---

## 2. 独立 Worktree 说明

### 2.1 为什么使用隔离环境

验证工作涉及拉取、编译、运行多个外部开源项目。为避免：

- 依赖污染（Go / Java / TypeScript 运行时侵入 Python 环境）
- 配置混乱（外部项目的环境变量、数据库连接影响主 PoC）
- 文件冲突（多项目 node_modules / vendor 目录）

所有开源项目的拉取和运行应在 **独立 Git worktree** 或独立目录下进行：

```
UnionAgent-Hub/
├── backend/           ← PoC 主线（不动）
├── frontend/          ← PoC 主线（不动）
├── docs/validation/   ← 本文档体系
└── .spike/            ← 独立验证目录（已 gitignored）
    ├── agentregistry/
    ├── skillhub/
    ├── mcp-registry/
    ├── backstage/
    └── artifacthub/
```

### 2.2 与主分支的同步策略

- `.spike/` 目录不纳入主分支版本管理（已在 `.gitignore` 中建议忽略）
- 验证产出（结论、实验记录）回写到 `docs/validation/` 中
- 验证中产生的代码（Adapter、Wrapper、脚本）根据结论决定是否合并入主分支

---

## 3. 验证范围

### 3.1 管理态能力验证

| # | 验证项 | 验证目标 | 是否必须自研 |
|---|--------|----------|:---:|
| M1 | 多资产类型统一管理 | Agent / Skill / Tool / MCP 四类资产在 Hub 中统一管理 | 是 |
| M2 | 生命周期状态机 | draft → pending → published → disabled → archived | 是 |
| M3 | 发布审批流程 | approve / reject / request_change + blocking 拦截 | 是 |
| M4 | 安全扫描 | 当前规则引擎 vs 外部扫描器接入可行性 | 当前自研，验证可替代性 |
| M5 | 版本管理 + 回滚 | 多版本并存 + 回滚到历史版本 | 是 |
| M6 | 能力包导入导出 | manifest / yaml / zip 导入 + 下载 | 是 |
| M7 | Category + Tag 分类 | 树形分类 + 扁平标签 + AI suggested→verified | 是 |
| M8 | 关系管理 | HubItemRelation 模型 + 依赖解析 | 是 |

### 3.2 运行态能力验证

| # | 验证项 | 验证目标 | 是否必须自研 |
|---|--------|----------|:---:|
| R1 | Runtime Discover | 已发布能力搜索（type / keyword / category / tags） | 否，可参考 AgentRegistry 协议 |
| R2 | Runtime Resolve | 能力配置解析 + 依赖展开 | 否 |
| R3 | 能力包下载 | manifest / 包下载 | 否 |
| R4 | 权限上下文过滤 | agent_id / workspace_id 过滤 | 是（策略逻辑） |
| R5 | MCP Config 兼容 | 与 MCP 官方配置格式对齐 | 否，参考 MCP Registry |

### 3.3 开源组件复用验证

| # | 验证项 | 候选组件 | 验证目标 |
|---|--------|----------|----------|
| C1 | 数据库迁移 | Alembic（已预留） | 验证 migration 初始化与 PostgreSQL 连接 |
| C2 | 对象存储 | MinIO / S3 SDK | 验证能力包文件存储与下载 |
| C3 | 外部扫描器 | Semgrep / Bandit（需先评估） | 验证是否可替代当前规则扫描器 |
| C4 | API 网关 | Kong / APISIX | 验证与 Hub 的鉴权集成模式 |

---

## 4. 验证阶段划分

### 阶段 A：开源方案能力对照（本阶段）

**性质**：纯文档，无代码

**活动**：
- 输出 `01_open_source_reuse_matrix.md`：10 候选 × 16 评估维度
- 输出 `08_final_recommendation.md`：最小自研治理层 + 可复用组件 + 推荐路线
- 对每个候选方案给出 `Spike 验证决策`（Yes / No / Optional）

**产出物**：3 份 validation 文档

### 阶段 B：AgentRegistry Adapter 实验

**性质**：轻量代码实验，独立 worktree

**活动**：
- 在 `.spike/agentregistry/` 中拉取并启动 AgentRegistry
- 验证其 Discover / Resolve 接口语义与 Hub 的对齐程度
- 编写 Hub → AgentRegistry 协议转换 Adapter（仅 mock 验证概念）
- 评估：如果未来需要与 AgentRegistry 生态互通，Adapter 的复杂度

**产出物**：`docs/validation/02_agentregistry_adapter_experiment.md`

### 阶段 C：Backstage Portal 实验

**性质**：轻量代码实验，独立 worktree

**活动**：
- 在 `.spike/backstage/` 中创建 Backstage 实例
- 自定义 Entity Kind 为 `AgentSkill` / `AgentTool` / `AgentMCP`
- 验证 Backstage Catalog Processor 是否能消费 Hub API 的数据
- 验证 Backstage TechDocs / Search 在能力市场上的适用性

**产出物**：`docs/validation/03_backstage_portal_experiment.md`

### 阶段 D：扫描 / 存储 / 迁移组件集成实验

**性质**：代码实验，在 PoC 主线分支上进行（不破坏现有功能）

**活动**：
- Alembic 初始化 + 第一条 migration → PostgreSQL 验证
- MinIO / boto3 接入能力包文件上传/下载验证
- （可选）Semgrep 作为外部扫描器的 POC 接入

**产出物**：`docs/validation/04_infra_components_experiment.md`

### 阶段 E：Runtime Discover + MCP Registry 兼容实验

**性质**：代码实验 + 文档对齐

**活动**：
- 实现 Runtime Discover / Resolve 的 Hub 版本（阶段 5）
- 验证与 AgentRegistry 的发现接口兼容性
- 验证 MCP 类型 manifest schema 与 MCP Registry 的对齐

**产出物**：`docs/validation/05_runtime_discover_experiment.md` + `docs/validation/06_mcp_registry_compat_experiment.md`

---

## 5. 每阶段产出物清单

| 阶段 | 文档产出 | 代码产出 | 决策产出 |
|------|----------|----------|----------|
| A | 01/08 两份文档 | 无 | Spike 决策矩阵 |
| B | 02 实验报告 | AgentRegistry Adapter（spike） | 是否实现 Adapter 层 |
| C | 03 实验报告 | Backstage Entity Kind 定义（spike） | 是否引入 Portal 层 |
| D | 04 实验报告 | Alembic 配置 / MinIO 接入代码 | 组件引入清单 |
| E | 05/06 实验报告 | Discover/Resolve 接口 + MCP Schema 对齐 | 接口规范定稿 |

---

## 6. 验证成功标准

### 6.1 技术可行性标准

| 标准 | 阈值 |
|------|------|
| 开源项目可本地启动 | 5 分钟内完成 clone → 启动 → 访问 |
| 核心 API 可调用 | 启动后 10 分钟内完成 API 调用验证 |
| 数据类型可映射 | HubItem type 与外部项目的数据类型可 1:1 或有损可接受映射 |
| 不在核心代码路径上 fork | 如需修改外部项目核心代码才能接入 → 视为不适用 |

### 6.2 性能阈值

| 指标 | PoC 阈值 | 准生产阈值 |
|------|----------|------------|
| Discover 响应时间 | < 500ms（千条能力） | < 200ms |
| Resolve（含依赖展开）| < 1s | < 500ms |
| 导入（5MB zip） | < 10s | < 5s |
| 扫描（千条规则） | < 30s | < 10s |

### 6.3 集成复杂度上限

| 复杂度指标 | 上限 |
|------------|------|
| 新增依赖数量（外部服务） | ≤ 3（如 PostgreSQL + MinIO + Redis） |
| 适配代码行数（per 外部方案） | ≤ 500 行 |
| 上游兼容性保障 | 使用稳定 API，不依赖 internal 包 |

---

## 7. 验证失败退出标准

本验证工作的每一轮实验都有明确的退出条件。任一条件触发，该方案即退出 Spike 验证，仅保留参考价值。

| # | 退出条件 | 触发方案 | 后果 |
|---|----------|----------|------|
| E1 | **需要 fork 并修改核心代码**才能满足 Hub 需求 | 任一开源项目 | 不适合直接采用，降级为设计参考 |
| E2 | **License 不适合商用**（AGPL、未明确的 "Other"） | CKAN（AGPL）、MCP Registry（Other） | 直接淘汰，不可在 Hub 代码中引用 |
| E3 | **无法低侵入接入**（Adapter > 500 行、需改上游源码） | Backstage、DataHub | 降级为参考，不作为运行时依赖 |
| E4 | **不支持多资产类型**（只能管理单一 Kind） | SkillHub、MCP Registry | 不作为主系统，参考其单类型规范 |
| E5 | **只能做门户/展示**（不承载治理逻辑） | Backstage | 可作为上层 Portal，但治理面保留在 Hub |
| E6 | **强绑定特定基础设施**（Elasticsearch / Kafka / Neo4j 等） | DataHub | 超出 PoC 可接受复杂度，仅参考模型 |
| E7 | **语言栈不兼容**（Java / 纯 TypeScript 后端）且无 Bridge 方案 | SkillHub（Java）、Backstage（TS） | 需要 Polyglot 架构时可重新评估，当前阶段排除 |
| E8 | **社区不活跃**（3 个月以上无 push + 无活跃 maintainer） | 任一候选 | 不可作为运行时依赖，仅参考代码 |
| E9 | **无版本管理 / 无多版本并存** | CKAN、通用框架 | 无法承载 Hub 核心版本管理需求 |

---

## 8. 验证执行优先级

Spike 验证任务按以下优先级执行。优先级综合考量：需求紧迫度、外部方案成熟度、集成风险、依赖关系。

| 优先级 | 验证项 | 阶段 | 理由 |
|:---:|------|:---:|------|
| **P1** | AgentRegistry Adapter — 验证 Runtime Discover / Resolve 协议兼容 | 阶段 B | Runtime Discover 是运行态核心接口，协议设计影响大，需尽早验证方向；AgentRegistry 提供的多资产 Registry 协议最贴近 Hub 需求 |
| **P2** | Alembic + PostgreSQL — 验证工程化数据库迁移 | 阶段 D | 数据库迁移是准生产部署的前提；Alembic 已预留目录，连接 PG 仅需变更 DATABASE_URL，风险最低 |
| **P3** | MinIO / S3 — 验证下载包和相关文件存储 | 阶段 D | 能力包下载依赖文件存储；MinIO 作为对象存储 SDK 引入，侵入度低；可先实现 manifest 下载（无需存储），再扩展文件下载 |
| **P4** | Backstage Portal — 验证作为上层门户的可行性 | 阶段 C | Portal 是可选增强层，非核心治理依赖；Backstage 开发门槛高（TypeScript 插件），优先级低于核心功能验证 |
| **P5** | MCP Registry Schema 对齐 — 不引用代码，仅文档对照 | 阶段 E | 纯文档级对齐验证，无代码实验；MCP 类型并非 Hub 最活跃资产类型（当前以 Agent/Skill/Tool 为主）；License 未确认前不做运行时引用 |

### 优先级依赖关系

```
P1 (AgentRegistry Adapter) ──▶ P5 (MCP Schema 对齐)
    │                              依赖：Discover 协议方向确定后，MCP 对齐更高效
    │
    ├── P2 (Alembic + PG) ──▶ 无下游依赖，可独立执行
    │
    ├── P3 (MinIO / S3)   ──▶ 无下游依赖，可独立执行
    │
    └── P4 (Backstage)    ──▶ 无下游依赖，可独立执行
```

P2 / P3 / P4 之间无依赖关系，可并行执行。

---

## 9. 不做事项

| 项 | 原因 |
|----|------|
| 全量迁移到任一外部项目 | 无候选直接覆盖 Hub 全部需求（详见 matrix） |
| 推倒重写 Hub | 当前 94 tests + 完整治理闭环已验证技术路线可行 |
| 在验证阶段引入新语言 | PoC 阶段保持 Python，不引入 Go/Java/TS 后端 |
| 验证阶段引入新基础设施依赖 | Redis / Elasticsearch / Kafka 等不在 PoC 引入 |
| 替换当前 FastAPI Web 层 | FastAPI 已满足需求，无替换动机 |
| 在验证阶段修改生产后端代码 | 所有实验代码在 `.spike/` 隔离或独立分支进行 |

---

> 下一文档：`docs/validation/01_open_source_reuse_matrix.md`
