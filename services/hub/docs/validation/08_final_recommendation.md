# 最终推荐方案

> 文档编号：validation/08
> 版本：v0.2
> 日期：2026-05-15
> 依赖文档：`validation/00`–`07`（全部验证文档）、`docs/13_open_source_tech_selection_evaluation.md`
>
> 本文档为技术验证的最终结论，汇总 `docs/validation/` 下全部验证文档的分析结果，给出可交付的技术路线决策。

---

## 1. 最终原则

```
统一治理面 + 类型化管理 + 独立执行面
```

| 层面 | 实现 | 要点 |
|------|------|------|
| **统一治理面** | Hub 自研 | HubItem / HubItemVersion / 生命周期 / 审批 / 风险 / 回滚 / 下架 / 可发现性判定 |
| **类型化管理** | 自研 + 借鉴开源 | 按 type 独立 manifest schema / validator / 扫描规则；借鉴 Backstage Catalog、AgentRegistry Discover 协议、SkillHub Skill Spec、MCP Registry Config Schema |
| **独立执行面** | Runtime 负责 | Agent 执行 / Skill 执行 / Tool 调用 / MCP Server 托管 / Runtime 装配与沙箱 |

---

## 2. 两个"不建议"

### 2.1 不建议直接整体采用任何单一开源框架

经过对 10 个候选方案的深度评估（详见 `validation/01`），结论如下：

**除当前自研 Hub 外，外部单一开源方案均无法低侵入覆盖 Hub 的核心能力。** 原因分三类：

| 类别 | 方案 | 无法采用的核心原因 |
|------|------|-------------------|
| **领域不匹配** | DataHub、OpenMetadata、CKAN | 数据目录/数据治理/开放数据门户，非 AI 能力市场 |
| **类型不完整** | SkillHub（仅 Skill）、MCP Registry（仅 MCP） | 只覆盖单一类型，无法统一管理四类资产 |
| **语言/架构/治理缺口** | AgentRegistry（Go）、Backstage（TS）、Artifact Hub（TS） | 语言栈不匹配 + 审批/扫描/回滚/下架等治理能力全量缺失 |

### 2.2 不建议完全自研所有通用能力

当前 Hub 的优势在**领域治理**（生命周期、审批、风险准入），不应在以下通用能力上重复造轮子：

| 不要自研 | 使用成熟开源组件 | 理由 |
|----------|-----------------|------|
| 数据库迁移工具 | Alembic | SQLAlchemy 官方工具，已预留目录 |
| 对象存储 SDK | MinIO / boto3 (S3) | 能力包文件存储，SDK 调用即可 |
| Web 框架 | FastAPI | 已使用，不切换 |
| ORM | SQLAlchemy | 已使用，不切换 |
| API 网关 | Kong / APISIX | Hub 外部独立部署 |
| 身份认证 | IAM 平台 | Hub 外部独立部署 |
| 外部安全扫描（可选） | Semgrep CLI | 子进程调用，增强当前规则引擎 |

---

## 3. 当前 Hub PoC 作为业务验证基线

当前 Hub PoC 已完成并被 94 个测试验证的能力，构成**最小治理层的业务基线**：

| 已实现 | 测试覆盖 | 在最终方案中的定位 |
|--------|:---:|------|
| HubItem / HubItemVersion 模型 | ✅ | 统一治理面的数据根基 |
| 生命周期状态机（6 个状态） | ✅ | 治理面状态流转 |
| 发布审批（approve/reject/request_change + blocking 拦截） | ✅ | 治理面审批控制 |
| 安全扫描（5 类规则） | ✅ | 治理面安全准入 |
| 能力包导入（JSON/YAML/ZIP） | ✅ | 类型化面的入口 |
| 版本回滚 + 下架 | ✅ | 治理面生命周期 |
| 前端管理控制台 | ✅ | PoC 管理端 |
| 一键启动 | ✅ | PoC 部署 |

这些能力不是"PoC 原型而已"，而是经过完整测试验证的业务规则基线和测试回归安全网。

---

## 4. 推荐开源组件优先 + 最小自研治理层

### 4.1 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                      Hub 能力市场                              │
│                                                              │
│  ┌────────────────────────────────────────────────────┐      │
│  │  Portal 层（可选）                                    │      │
│  │  · Backstage Portal（阶段 C 验证可行性）              │      │
│  │  · Vue 3 自研管理端（PoC 当前）                       │      │
│  └────────────────────┬───────────────────────────────┘      │
│                       │                                      │
│  ┌────────────────────▼───────────────────────────────┐      │
│  │  最小自研治理层（Hub 核心，Python / FastAPI）          │      │
│  │                                                     │      │
│  │  ✅ HubItem / HubItemVersion     ✅ 生命周期状态机     │      │
│  │  ✅ 发布审批 + blocking 拦截     ✅ 风险准入          │      │
│  │  ⬜ HubItemRelation (阶段 1)     ⬜ Manifest Spec (阶段 3) │
│  │  ⬜ Runtime Discover (阶段 5)    ⬜ 权限策略 (阶段 7)   │      │
│  └──┬───────────┬──────────────┬──────────────────────┘      │
│     │           │              │                             │
│  ┌──▼──────┐ ┌──▼───────┐ ┌───▼────────────┐                │
│  │开源组件层│ │Adapter层 │ │ 外部接口层       │                │
│  │· Alembic│ │·AgentReg │ │ · IAM (外部)    │                │
│  │· MinIO  │ │  Adapter │ │ · API Gateway   │                │
│  │· Semgrep│ │· MCP     │ │ · Runtime PE    │                │
│  │  (可选) │ │  Schema  │ │                 │                │
│  └─────────┘ └──────────┘ └────────────────┘                │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 最小自研治理层（7 项）

| # | 能力 | 状态 | 为什么必须自研 |
|---|------|:---:|------|
| 1 | HubItem / HubItemVersion | ✅ | 四类能力的统一模型根基 |
| 2 | HubItemRelation | ⬜ 阶段 1 | 跨类型依赖关系建模 |
| 3 | 生命周期状态机 | ✅ | 无开源方案覆盖完整治理流转 |
| 4 | 发布审批 | ✅ | blocking 拦截 + 双审计追踪 |
| 5 | 风险准入 | ✅ | 四级管控 + 扫描联动 |
| 6 | Manifest Spec v0.1 | ⬜ 阶段 3 | 平台级规范制定权 |
| 7 | Runtime Discover 策略 | ⬜ 阶段 5 | 可发现性判定 + 权限过滤 |

### 4.3 可低侵入引入的开源组件（8 项）

| 组件 | 用途 | 侵入程度 | 阶段 |
|------|------|:---:|:---:|
| Alembic | 数据库迁移 | 极低 | 当前 |
| MinIO / boto3 | 能力包文件存储 | 低 | 阶段 4+ |
| Semgrep CLI | 外部安全扫描（可选） | 低 | 待评估 |
| FastAPI | Web 框架 | 已使用 | ✅ |
| SQLAlchemy | ORM | 已使用 | ✅ |
| PostgreSQL | 生产数据库 | 低 | 准生产 |
| Kong / APISIX | API Gateway（Hub 外部部署） | 极低 | 生产 |
| IAM 平台 | 认证授权（Hub 外部部署） | 极低 | 阶段 7 |

### 4.4 后续 Spike 验证（2 项）

| 验证项 | 说明 | 阶段 |
|--------|------|:---:|
| AgentRegistry Adapter | Hub Discover 与 AgentRegistry 协议兼容性验证 | 阶段 B |
| Backstage Portal | Backstage 作为 Hub 上层能力门户的可行性 | 阶段 C |

---

## 5. 推荐下一阶段实施顺序

按依赖关系和业务价值排序，推荐以下实施顺序：

| 优先级 | 阶段 | 内容 | 依赖 | 预估工作量 |
|:---:|:---:|------|------|:---:|
| **1** | 阶段 1 | **HubItemRelation** — 关系模型 + API + 前端 + 循环依赖检测 | 无 | 2-3 周 |
| **2** | 阶段 2 | **Category + Tag** — 前端筛选/编辑 + 导入支持 + AI 标注预留 | 无 | 1-2 周 |
| **3** | 阶段 3 | **Manifest Spec v0.1** — 校验器 + 自动修正 + Warning/Error 机制 + 类型特有字段 | 阶段 1（relations 校验依赖） | 2-3 周 |
| **4** | 阶段 4 | **Download P0** — manifest 下载 + 能力包下载 + hash + 访问控制 | 阶段 3（manifest 内容规范） | 1-2 周 |
| **5** | 阶段 5 | **Runtime Discover / Resolve P0** — 搜索 + 过滤 + 依赖展开 + 快照表 | 阶段 1（依赖展开依赖） + 阶段 4（下载接口依赖） | 2-3 周 |
| **6** | 阶段 6 | **Skill 专项规范** — Skill Package Spec + 示例包 + 专项扫描 | 阶段 3（Manifest Spec） | 1-2 周 |
| **7** | 阶段 7 | **身份权限接入** — 角色 + 操作矩阵 + Agent 上下文过滤 | Gateway/IAM 就绪 | 2-3 周 |
| **8** | 阶段 8 | **AI 标注** — 自动推荐 category/tags/relations + 人工确认 | 阶段 2（Tag/Category）+ 阶段 1（Relation） | 待定 |

### 依赖关系图

```
阶段 1 (Relation) ────────────────────────────────┐
    │                                              │
    ├──▶ 阶段 3 (Manifest Spec v0.1) ──▶ 阶段 4 (Download P0)
    │         │                                     │
    │         └──▶ 阶段 6 (Skill 专项)              │
    │                                              │
    └──▶ 阶段 5 (Runtime Discover/Resolve) ◀────────┘
              │                           (依赖展开需要 Relation
              │                            manifest 格式需要 Spec)
              │
阶段 2 (Category/Tag) ──▶ 阶段 8 (AI 标注)

阶段 7 (身份权限) ←── 独立（依赖 Gateway/IAM 就绪）
```

**可并行的组合**：
- 阶段 1 + 阶段 2 可并行
- 阶段 1 完成后 → 阶段 3 + 阶段 5 可并行
- 阶段 7 可独立于其他阶段进行，取决于外部 IAM/Gateway 就绪时间

---

## 6. 各验证文档结论汇总

| 验证文档 | 核心结论 |
|----------|----------|
| **00** — 验证计划 | 5 阶段验证（A-E），9 条失败退出标准，P1-P5 执行优先级 |
| **01** — 开源复用矩阵 | 10 候选 × 16 维度 → 2 个进入 Spike（AgentRegistry Adapter/Backstage Portal），2 个 Optional，6 个仅作参考 |
| **02** — 统一 vs 分散治理 | 统一治理面 + 类型化内容层是最优分割；四类资产治理需求高度同构，类型差异通过 manifest schema 承载 |
| **03** — 关系模型 | HubItemRelation（uses/invokes/depends_on/provides）+ scope(management/runtime) + 版本策略 + 循环依赖检测 |
| **04** — Runtime Discover | Discover（硬过滤：published+discoverable+non-blocking）+ Resolve（递归依赖展开+aggregated_permissions+dependency_risk_level）|
| **05** — 下载导出 | 三类下载（Manifest/运行态能力包/管理态导出包）+ manifest_hash/package_hash + 签名预留 |
| **06** — Manifest Spec | v0.1：5 必填 + 10 可选通用字段 + 按类型特有字段 + 三级校验（自动修正/Warning/Error）+ 4 份完整示例 |
| **07** — 身份权限 | 6 人类角色 + Agent 权限边界 + Hub 不做 IAM + Gateway 注入身份 context + Runtime Policy Engine 独立 |

---

## 7. 确定性结论与待验证项

### 已确定

| # | 结论 | 依据 |
|---|------|------|
| 1 | Hub 治理层自研是正确的 | 94 tests + 完整闭环，无开源替代 |
| 2 | 外部单一开源方案无法直接替代 Hub | 01 矩阵：领域不匹配 / 类型不完整 / 治理缺口 |
| 3 | 统一治理面 + 类型化管理是最优模型 | 02 分析：四类资产治理同构，内容差异可承载 |
| 4 | Backstage Catalog/Relation 模型是最高优先级参考 | 01 深度评估：Backstage §4 |
| 5 | AgentRegistry Discover 协议是 Runtime Discover 的核心参考 | 01 深度评估：AgentRegistry §1 |
| 6 | MCP Config Schema 对齐 MCP Registry 是 MCP 类型资产的规范基线 | 01 深度评估：MCP Registry §3 |
| 7 | SkillHub Skill Spec 是 Skill 包结构的核心参考 | 01 深度评估：SkillHub §2 |

### 待验证

| # | 待验证项 | 验证方式 | 阶段 |
|---|----------|----------|:---:|
| 1 | AgentRegistry Adapter 的协议兼容性 | 独立 worktree 验证，≤ 500 行 Python Adapter | 阶段 B |
| 2 | Backstage 作为 Portal 的可行性 | 独立 worktree，自定义 Entity Kind + Provider 插件 | 阶段 C |
| 3 | Semgrep 作为外部扫描器的接入效果 | 子进程调用验证，与当前规则引擎的互补性评估 | 阶段 D |

---

## 8. 当前 PoC 代码处理原则

当前 PoC 代码不必视为最终生产代码，但应作为业务规则、测试用例和最小治理层的基线。后续如进行重构，应优先保留测试、领域模型、状态机规则和文档，而不是盲目保留实现细节。

### 优先保留

| 保留项 | 说明 |
|--------|------|
| **测试用例** | 94 个测试覆盖了完整的生命周期、审批、扫描、回滚、下架、导入等场景，这些测试定义了 Hub 的核心行为契约。重构后必须以这些测试为回归基线。 |
| **领域模型** | HubItem / HubItemVersion 的字段定义、唯一约束、关系映射是对四类能力资产统一建模的核心成果，应作为数据模型升级的起点。 |
| **状态机规则** | 生命周期状态流转条件、blocking 拦截逻辑、回滚目标校验等业务规则已被 94 个测试验证，应在任何重构中保持语义等价。 |
| **文档** | `docs/` 目录下的范围确认、数据模型、API 设计、安全扫描、验收清单等文档记录了业务决策和设计取舍，是后续重构的设计依据。 |

### 允许替换

| 替换项 | 说明 |
|--------|------|
| **Web 路由实现** | API 路径、参数解析方式可随框架升级调整，只要接口语义不变（已有 API 文档和测试保证）。 |
| **Service 层实现细节** | 只要对外行为通过测试验证，内部实现可重构（如合并/拆分方法、引入依赖注入模式）。 |
| **前端组件实现** | Vue 3 组件可替换为其他 UI 框架或 Portal 方案，只要管理操作和展示语义不变。 |
| **数据库访问方式** | ORM 查询方式可优化（如 N+1 消除、批量操作），只要数据一致性和完整性约束保持一致。 |
| **扫描规则实现** | 规则匹配算法和存储方式可调整，只要风险等级计算逻辑保持不变。 |

### 不应盲目的替换

- **不应**为了统一框架而迁移到 Django / DRF — 无业务收益；
- **不应**为了引入微服务而拆分单进程 Hub — PoC 阶段不必要；
- **不应**为了"最佳实践"而重写状态机（如引入工作流引擎）— 当前状态机简单、可测试、已验证；
- **不应**在无测试覆盖的情况下大规模重构 — 重构以测试为安全网，不以"代码更整洁"为唯一动机。

---

## 9. 关键决策点

| # | 决策 | 输入 | 时机 |
|---|------|------|------|
| D1 | 是否实现 AgentRegistry Adapter | 阶段 B 实验报告 | 阶段 5 之前 |
| D2 | 是否引入 Backstage Portal | 阶段 C 实验报告 | 阶段 6 之前 |
| D3 | 是否引入外部扫描器 | 阶段 D 实验报告 | 阶段 6 之前 |
| D4 | 是否全面切换 PostgreSQL | 阶段 D 实验报告 | 准生产之前 |

---

> 完整验证文档体系：
> - `validation/00` — 验证计划
> - `validation/01` — 开源复用矩阵
> - `validation/02` — 统一 vs 分散治理
> - `validation/03` — 能力关系模型
> - `validation/04` — Runtime Discover / Resolve
> - `validation/05` — 下载与导出
> - `validation/06` — Manifest Spec v0.1
> - `validation/07` — 身份权限边界
> - `validation/08` — 最终推荐方案（本文档）
