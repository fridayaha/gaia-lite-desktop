# SkillVetBench 论文参考分析

版本：v0.1 | 日期：2026-06-02 | 状态：论文分析阶段，不涉及代码实现。

> 论文：_Benchmarking Security Risk Detection and Verification in Open Agentic Skill Ecosystems_
> 作者：Ismail Hossain, Sai Puppala, Zhuoran Lu, Sajedul Talukder, Nan Jiang
> 来源：arXiv:2606.00925 (2026-05-30)
> 许可：CC BY 4.0

---

## 1. 论文核心结论

### 1.1 问题定位

开放 Agent Skill 生态（如 OpenClaw/ClawHub）允许社区贡献者发布可复用的 Skill。这种可扩展性创造了供应链风险：恶意贡献者可以将有害行为隐藏在看似无害的 Skill 中，仅通过表层检查无法识别。论文指出 ClawHavoc 供应链攻击中已有 **1,184 个恶意 Skill** 进入 ClawHub 市场。现有防御缺乏同时覆盖"恶意 Skill 检测"和"运行时验证"的评估基准。

### 1.2 两阶段安全评估框架

SkillVetBench 提出一个两阶段安全审查流水线：

| 阶段 | 名称 | 方法 | 产出 |
|:---:|------|------|------|
| Stage 1 | Semantic Vetting | LLM-as-a-Judge 对 Skill 自然语言声明进行语义分析，检测隐藏的恶意意图 | 可疑性分级（Benign/Suspicious/Malicious）|
| Stage 2 | Runtime Sandbox Verification | 将 flagged skill 放入 Docker 沙箱中执行，使用 instrumented agent 观测运行时行为 | 可审计证据（execution trace）|

### 1.3 五大 Agentic 风险维度（SARS 评估体系）

论文定义了五个风险维度用于评估 Skill 在 agentic 执行环境中的安全风险：

| 维度 | 缩写 | 权重 | 描述 |
|------|:---:|:---:|------|
| Instruction Fidelity Risk | IFR | 2.0× | 指令被 prompt injection 劫持的可能性 |
| Data Gravity | DG | 1.5× | Skill 可读写的敏感数据等级 |
| Action Irreversibility | AI | 1.5× | 操作是否可撤销（GET vs DELETE）|
| Blast Radius | BR | 2.0× | 单次攻击的影响范围 |
| Chain Amplification | CA | 2.0× | 与其他 Skill 组合时危险加剧程度 |

每个维度 0-3 分，加权归一化得到 SARS（Skill Agentic Risk Score）∈ [0, 10]。

SARS 判决边界：
- `Benign`: [0, 3.9]
- `Suspicious`: [4.0, 6.9] → 触发 Stage 2 沙箱执行
- `Malicious`: [7.0, 10.0] → 需沙箱确认 trace

### 1.4 漏洞分类体系（7 类）

论文定义了三大类七种攻击类别：

| 威胁大类 | 攻击类别 | 关键指标 |
|----------|----------|----------|
| **Code-execution threats** | Command Injection | os.system(), subprocess, exec(), shell=True |
| | Unsafe File Operations | 路径穿越、写 /etc /tmp、rmtree |
| **Instruction-layer/agentic threats** | Prompt Injection | 外部内容作为 agent 指令，间接注入 |
| | Memory Poisoning | 未验证用户输入写入持久内存 |
| | Privilege Abuse | sudo、禁用安全控制、绕过认证 |
| **Data/supply-chain threats** | Data Exposure | 外发 HTTP、base64 编码敏感数据、硬编码密钥 |
| | Supply Chain | pip/npm install、wget/curl 无完整性校验、域名仿冒 |

### 1.5 LLM-as-a-Judge 与 Multi-Model

Stage 1 使用多个 LLM 模型作为评估器（Qwen2.5-32B, Llama-3.1-7B, Mixtral-8x7B），验证了不同 LLM 的判定一致性。Stage 2 使用 GPT-3.5-turbo 作为沙箱执行代理。

### 1.6 关键实验发现

1. **静态盲区**：纯语义/基于签名的基线方法遗漏高达 89% 的恶意 Skill（威胁隐藏在自然语言指令、多组件逻辑、跨组件交互中）— SkillVetBench 则零漏报（78/78）；
2. **运行时攻击密集点**：攻击集中在少量高权限原语——`exec`、`write_file`、`install_skill`、`spawn`；
3. **三层观测**：恶意行为跨 Host、Agent、Skill 三层展开，单一层次监测不充分；
4. **沙箱证据**：沙箱执行可生成直接支持恶意判定的运行时证据（cron 作业、明文凭证存储、内存投毒）；
5. **组合风险**：攻击类别常组合出现（Supply Chain + Unsafe FileOps + Privilege Abuse 等同一攻击链）；
6. **基线对比**：论文对比了 8 种基线方法（ClawScan、VirusTotal、ClawVet、LLM-only、LLM+CoT、CodeBERT、SkillProbe、SkillSieve），SkillVetBench 是唯一在全部 78 个恶意 Skill 上零漏报且零误报的系统。

### 1.7 基准数据集

基于真实 OpenClaw 生态中已确认的恶意 Skill 构建，包括 ClawHavoc 供应链攻击样本。基准共 100 个 Skill（**78 个 confirmed-malicious + 22 个 benign controls**），覆盖三类七种攻击类别，提供可复现的评估基准。论文实验表明 SkillVetBench 在全部 78 个恶意 Skill 上实现零漏报，在 22 个良性对照上实现零误报。

---

## 2. 对 Hub 当前能力的映射

### 2.1 已有对齐

| SkillVetBench 能力 | Hub 当前对应 | 对齐程度 |
|------|------|:---:|
| 代码级危险模式检测 | `RuleScanner`：MCP command 危险模式、Tool endpoint 不安全协议、硬编码密钥 | ✅ 高度对齐 |
| 签名/规则级 Skill 扫描 | `BuiltInRuleScanner`：Prompt 注入、契约完整性、Tool/MCP 专项 | ✅ 部分对齐（规则覆盖可扩展） |
| 外部扫描器组合 | `CompositeScanner`：BuiltIn + External 串行 | ✅ 对齐（Betterleaks/Gitleaks 已接入） |
| Secret 扫描 | `BetterleaksScannerAdapter` + `GitleaksScannerAdapter` | ✅ 对齐 |
| 风险发现记录 | `ScanFinding`：risk_level (blocking/high/medium/low) | ✅ 对齐 |
| 阻断决策 | `Gate` 模型：Block/Review/Warn/Observe | ✅ 对齐（blocking 阻断已实现） |
| 发布前审批 | `Approval` 流程：submit-review → pending_review → approve → publish | ✅ 对齐 |
| 运行时能力发现 | `Runtime Discover`：仅返回已发布、可发现、非阻断资产 | ✅ 对齐 |
| 多模型评估 | 无 | ❌ 不适用（Hub 不执行 LLM） |
| 沙箱执行验证 | 无 | ❌ 未实现（属于 P3 Eval Sandbox） |

### 2.2 缺失能力

| 缺失能力 | SkillVetBench 对应 | 优先级 |
|------|------|:---:|
| 语义分析层（LLM-based semantic vetting） | Stage 1: LLM-as-a-Judge | P3 |
| Agentic 风险维度评估（IFR/DG/AI/BR/CA） | SARS 五维评分 | P3 |
| 多维度加权评分 | SARS 公式 | P3 |
| 运行时沙箱执行验证 | Stage 2: Docker sandbox | P3 |
| 运行时证据 trace 收集 | Sandbox execution findings | P3 |
| 基准数据集 | SkillVetBench dataset | 参考价值 |
| CVSS v4.0 映射 | CVSS computation | 参考价值 |

### 2.3 架构差异

| 维度 | SkillVetBench | Hub |
|------|---------------|-----|
| 定位 | 离线评估基准（benchmark + pipeline） | 在线治理平台（准入控制 + 生命周期） |
| 执行模型 | 批处理评估 | 事件驱动（上传/导入 → 校验 → 扫描 → 审批 → 发布） |
| LLM 使用 | Stage 1 LLM-as-a-Judge（核心评估组件） | 不使用 LLM |
| 沙箱 | Docker 沙箱（核心验证组件） | 不使用沙箱（P3 计划） |
| 风险模型 | 五维 agentic 风险（SARS） + CVSS v4.0 | 四级发现严重度（critical/high/medium/low）对应的 risk_level + 规则驱动 Gate（Block/Review/Warn/Observe） |

---

## 3. 可吸收设计

以下内容可作为 Hub 安全准入体系的长期参考，不要求立即实现代码，但应在设计文档和路线图中吸收。

### 3.1 风险分类增强

SkillVetBench 的七类漏洞分类比当前 Hub 的规则分类更系统化。建议吸收：

- 将当前 RuleScanner 的检查项映射到 SkillVetBench 三类七类框架；
- 为每个检查项标注 Threat Class（Code-execution / Instruction-layer/agentic / Data/supply-chain）；
- 在 `ScanFinding` 的 `risk_type` 中可考虑接近论文的分类命名，便于后续对比评估；
- 具体映射见下表：

| 论文分类 | Hub 当前检查项 |
|----------|---------------|
| Command Injection | MCP command 危险模式（rm -rf, curl \| sh） |
| Unsafe File Operations | 路径穿越（未显式覆盖，可增加规则） |
| Prompt Injection | Skill instruction / MCP command+args+env / Tool description 风险 |
| Memory Poisoning | 未覆盖 |
| Privilege Abuse | 未显式覆盖（可增加 sudo、disable security 规则） |
| Data Exposure | Betterleaks/Gitleaks secret 扫描、Tool endpoint http 检查 |
| Supply Chain | MCP env 硬编码密钥、Agent dependencies 检查 |

### 3.2 Agentic Risk Dimensions 作为安全准入增强

SARS 五维评分适合作为 Hub 安全评估体系的**参考框架**：

| 维度 | Hub 可映射内容 | 适用场景 |
|------|---------------|----------|
| IFR (指令劫持风险) | 当前 Prompt 注入检查 + Skill instruction 完整性 | 增强 Skill/Tool 的指令安全评估 |
| DG (数据敏感性) | `data_classification` 字段（如有）+ permission_json 权利声明 | 评估数据暴露风险 |
| AI (操作不可逆性) | Tool endpoint HTTP method 检查 | 评估高风险操作 |
| BR (爆炸半径) | Runtime compatibility + dependencies 声明 | 评估影响范围 |
| CA (链式放大) | HubItemRelation + cross-skill dependency 检查 | 评估组合风险 |

不建议直接复制 SARS 公式作为 blocking 决策，但可以作为后续 Eval Sandbox 阶段的参考权重。

### 3.3 Permission Tier 概念

论文实验中确认 `exec`、`write_file`、`install_skill`、`spawn`、`subagent` 是攻击密集的高权限原语。Hub 可吸收：

- 在 `permission_json` 和 `runtime_compatibility` 中追踪这五类高权限操作；
- 在 RuleScanner 中增加针对这些原语的**专项检查**（例如：Skill 声明了 `exec` 权限但未在 permission_json 中明确 → warning）；
- 在 Runtime Discover 阶段可按 permission tier 过滤高权限 Skill。

### 3.4 Evidence Trace 模式

论文强调"每个判定必须连接到具体 artifact、触发行为和可观察副作用"。Hub 当前 ScanFinding 已记录 evidence，但可增强：

- `evidence` 字段承载更结构化的信息：affected artifact component、attack category、observable side effects；
- 类似论文 Table 9 的 trace 格式（Skill → Attack Category → Layer → Finding Description）可作为后续 Eval Sandbox 阶段的 evidence 结构参考。

### 3.5 Benchmark Dataset 参考

SkillVetBench 的数据集构建方法（基于已知的 ClawHavoc 样本）对 Hub 有参考价值：

- 建议后续 P3 阶段构建 Hub 自己的内部测试集（`tests/fixtures/samples/malicious/`）；
- 测试集应覆盖三类七种攻击类别；
- 用于 RuleScanner 和后续 Eval Sandbox 的回归测试。

### 3.6 Sandbox Evaluation 架构

论文 Stage 2 的 Docker 沙箱执行架构可作为 Hub Eval Sandbox 的设计参考：

- 隔离执行环境（Docker）；
- Instrumented agent 记录工具调用、日志、错误；
- 三层观测（Host / Agent / Skill）；
- 任务 prompts 设计为"请求合法操作，不显式授权越权行为"——这是合理的触发策略。

---

## 4. 不建议立即实现

以下内容虽然论文中有价值，但不适合在当前阶段引入 Hub。

### 4.1 LLM-as-a-Judge 不得进入准入主链

论文 Stage 1 使用 LLM 做语义评估。但对于 Hub：

- AGENTS.md 明确约束"不引入模型调用"；
- Hub 当前不使用 LLM，不应为语义评估引入 LLM 依赖；
- LLM 判定不可靠（论文自身验证了不同 LLM 判定存在差异）；
- LLM 不适合作为 blocking 阻断决策的唯一来源。

**结论**：LLM-as-a-Judge 可作为后续 Eval Sandbox（P3）的辅助组件，但**不进入准入主链**。

### 4.2 Sandbox Execution 不得放入 Hub Core

论文 Stage 2 在 Docker 沙箱中执行 Skill。对于 Hub：

- AGENTS.md 明确："Hub 不是 Runtime，不执行能力"；
- 沙箱执行属于"能力评估"而非"能力治理"；
- Sandbox 应作为**独立评估服务**（Eval Sandbox），不耦合到 Hub 核心链路；
- Hub 可以：
  - 接收 Sandbox 的评估结果（类似接收外部扫描器 finding）；
  - 将 Sandbox 评估作为 `ExternalScanner` 接入（类似 Betterleaks/Gitleaks）；
  - 但不将沙箱作为 submit-review / publish 的主链环节。

### 4.3 不要直接复制论文 Scoring 作为 blocking 决策

- SARS 公式的权重是论文基于 agentic 执行上下文设计的，Hub 当前不执行能力，直接套用无意义；
- "Suspicious [4.0, 6.9] → 触发沙箱"对 Hub 不适用（Hub 不运行沙箱）；
- 论文的 scoring 更适合作为后续 Eval Sandbox 服务的内部参考，而非 Hub 准入决策。

### 4.4 不要将 Semantic Score 变 blocking

- 语义评估（LLM）结果的不确定性高；
- Hub 准入阻断应以确定性规则（静态分析、secret 扫描、契约完整性）为准；
- 语义结果可作为 finding 记录（类似 medium/high），但不应成为 blocking 条件。

### 4.5 不接入额外依赖

- 不引入 LLM 框架（langchain, llamaindex 等）；
- 不引入 Docker SDK（sandbox 为独立服务）；
- 不引入 CVSS 计算库；
- 不引入 OpenAI/Anthropic SDK。

---

## 5. 推荐路线

### 5.1 总览

| 阶段 | 内容 | 依赖 | 是否修改代码 |
|:---:|------|:---:|:---:|
| SVB-0 | 论文分析与文档输出（本文档） | — | 否 |
| SVB-1 | 风险分类 taxonomy 增强（`docs/05` 更新，RuleScanner 检查项映射） | — | 否（仅文档） |
| SVB-2 | Agentic Risk Dimension 设计（`docs/05` + 新文档） | SVB-1 | 否 |
| SVB-3 | Semantic Scanner 预留设计（`docs/17` 扩展为 External Semantic Scanner Adapter） | SVB-2 | 否 |
| SVB-4 | Eval Sandbox 设计（`docs/05` P3 章节细化） | SVB-3 | 否 |
| SVB-5 | Internal Benchmark 构建（测试样本集） | SVB-4 | 否（仅测试数据） |

### 5.2 SVB-0（当前阶段）✅

- 完成本文档（`docs/26_skillvetbench_reference_analysis.md`）；
- 更新 `docs/08_roadmap_workload.md`（增加 SVB 阶段）；
- 更新 `docs/20_current_baseline_summary.md`（增加论文参考记录）；
- 更新 `docs/00_docs_index.md`（增加本文档索引）。

### 5.3 SVB-1：风险分类 taxonomy 增强

**目标**：将论文的七类漏洞分类纳入 Hub 安全准入文档体系。

- 更新 `docs/05_admission_security_design.md` 第 5 节（类型化风险检查），增加分类映射表；
- 在 RuleScanner 文档中为每个检查项标注 Threat Class；
- 不做代码改动——仅文档增强。

### 5.4 SVB-2：Agentic Risk Dimension 设计

**目标**：为 Hub 设计 agentic 风险维度评估框架（设计层面，不实现代码）。

- 在 `docs/05` 中增加"Agentic Risk Dimension"章节；
- 设计五维度的 Hub 语义映射（IFR/DG/AI/BR/CA）；
- 说明每个维度对应的 Hub 检查项和数据来源；
- 不引入 SARS scoring，但保留作为 Eval Sandbox 的参考权重；
- 可选：新增 `docs/27_agentic_risk_dimensions_design.md` 独立文档。

### 5.5 SVB-3：Semantic Scanner 预留设计

**目标**：为将来可能的 LLM-based 语义评估预留接口设计。

- 在 `docs/17_external_scanner_adapter_design.md` 中增加"Semantic Scanner Adapter 预留"章节；
- 将 LLM-as-a-Judge 定义为一种特殊的 External Scanner（类似 Betterleaks/Gitleaks 的接入方式）；
- 明确该 adapter：
  - 不进入准入主链；
  - 不成为 blocking 决策来源；
  - 仅作为 P3 评估工具；
  - 输出 finding 而非 verdict。

### 5.6 SVB-4：Eval Sandbox 设计

**目标**：将论文 Stage 2 的沙箱设计纳入 Hub 的 P3 Eval Sandbox 规划。

- 更新 `docs/05` 第 9 节（后续路线）中 P3 Eval Sandbox 的描述；
- 吸收论文的三层观测架构（Host/Agent/Skill）；
- 吸收论文的任务 prompts 设计策略（请求合法操作，不显式授权越权）；
- 明确 Sandbox 作为独立服务，通过 ExternalScanner 协议接入 Hub。

### 5.7 SVB-5：Internal Benchmark 构建

**目标**：构建 Hub 自己的恶意/可疑 Skill 测试样本集。

- 在 `tests/fixtures/samples/malicious/` 下创建分类测试样本；
- 覆盖七类攻击类别（Command Injection / Unsafe File Operations / Prompt Injection / Memory Poisoning / Privilege Abuse / Data Exposure / Supply Chain）；
- 用于 RuleScanner 回归测试；
- 不做 LLM 评估——仅用于静态规则命中率验证。

---

## 6. 具体项目影响

### 6.1 文档更新计划

| 文档 | 更新内容 | 阶段 |
|------|----------|:---:|
| `docs/26_skillvetbench_reference_analysis.md` | 本文档（新增） | SVB-0 ✅ |
| `docs/08_roadmap_workload.md` | 增加 SVB-0 ~ SVB-5 阶段条目 | SVB-0 |
| `docs/20_current_baseline_summary.md` | 增加论文参考记录 + 未覆盖能力标注 | SVB-0 |
| `docs/00_docs_index.md` | 增加本文档索引 | SVB-0 |
| `docs/05_admission_security_design.md` | 增加风险分类映射表 + Agentic Risk Dimension 章节 + Eval Sandbox 细化 | SVB-1 ~ SVB-4 |
| `docs/17_external_scanner_adapter_design.md` | 增加 Semantic Scanner Adapter 预留章节 | SVB-3 |

### 6.2 Engineering Evidence

后续 SVB-1 ~ SVB-5 各阶段可在 `docs/engineering_evidence/` 下创建对应证据目录：
- `docs/engineering_evidence/svb_risk_taxonomy/`
- `docs/engineering_evidence/svb_agentic_risk_dimensions/`
- `docs/engineering_evidence/svb_semantic_scanner/`
- `docs/engineering_evidence/svb_eval_sandbox/`
- `docs/engineering_evidence/svb_internal_benchmark/`

### 6.3 不修改内容

| 不修改项 | 原因 |
|----------|------|
| backend 业务代码 | 本文仅分析，不实现 |
| 数据库 schema | 不改 DB 字段 |
| 前端代码 | PoC 阶段不处理 |
| pytest 测试代码 | 不新增测试 |
| 依赖（pyproject.toml） | 不引入新依赖 |
| demo worktree | 不改展示环境 |

---

## 7. 结论

### 7.1 总体判断

SkillVetBench 适合作为 Hub 安全准入体系和 Eval Sandbox 的**指导参考**，不适合作为 Hub 整体架构替代。

**适合吸收的**：
- 风险分类体系（三类七类）→ 增强 RuleScanner 检查项分类；
- Agentic 风险维度 → 作为安全评估设计参考框架；
- Permission Tier 概念 → 增强高权限原语专项检查；
- Evidence Trace 模式 → 增强 ScanFinding 结构化证据；
- Sandbox 评估架构 → 指导 P3 Eval Sandbox 设计。

**不适合立即实现的**：
- LLM-as-a-Judge 主链阻断；
- Sandbox Execution 放入 Hub Core；
- 直接复制 SARS 评分作为 blocking 决策；
- 引入 LLM/CVSS/Docker 等新依赖。

### 7.2 与 Hub 架构的关系

```
SkillVetBench（论文）
├── Stage 1: Semantic Vetting → 对应 Hub 潜在 P3 Semantic Scanner（不进入主链）
├── Stage 2: Sandbox Execution → 对应 Hub P3 Eval Sandbox（独立服务，通过 ExternalScanner 接入）
├── SARS Scoring          → 参考框架（不直接复制）
└── Vulnerability Taxonomy → 增强 Hub RuleScanner 分类
```

### 7.3 后续行动

1. ✅ SVB-0：本文档 + 更新 roadmap/baseline/docs_index（立即）；
2. 📋 SVB-1~SVB-5：按阶段推进，优先为文档增强，不做代码实现；
3. 🔜 当 P3 Eval Sandbox 启动时，以本文档和论文为设计参考。

---

## 附录 A：论文元数据

| 字段 | 值 |
|------|-----|
| 标题 | Benchmarking Security Risk Detection and Verification in Open Agentic Skill Ecosystems |
| 作者 | Ismail Hossain, Sai Puppala, Zhuoran Lu, Sajedul Talukder, Nan Jiang |
| arXiv ID | 2606.00925 |
| 提交日期 | 2026-05-30 |
| 许可 | CC BY 4.0 |
| 领域 | cs.CR (Cryptography and Security), cs.AI (Artificial Intelligence) |
| 相关事件 | ClawHavoc 供应链攻击（1,184 个恶意 Skill 进入 ClawHub）；Koi Security 报告 341 个恶意 Skill 被同一 bot 发现；基准使用 78 confirmed-malicious + 22 benign controls |

## 附录 B：文档参考

| 文档 | 说明 |
|------|------|
| `docs/05_admission_security_design.md` | 安全准入设计（SVB-1~SVB-4 将更新） |
| `docs/08_roadmap_workload.md` | Roadmap（SVB-0 已更新） |
| `docs/17_external_scanner_adapter_design.md` | 外部扫描器 Adapter 设计（SVB-3 将更新） |
| `docs/18_secret_scanner_provider_selection.md` | Secret Scanner 选型 |
| `docs/20_current_baseline_summary.md` | 当前基线说明（SVB-0 已更新） |
| `docs/24_multi_tenancy_design.md` | 多租户设计 |
| `docs/00_docs_index.md` | 文档索引（SVB-0 已更新） |
