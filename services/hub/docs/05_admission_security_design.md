# 安全与格式准入设计

版本：v0.6 | 日期：2026-06-02 | 状态：P2-2C Gitleaks fallback 已实现并真实验证。P2-2B Betterleaks primary scanner 已实现并验证（v1.3.1）。SVB-0 论文分析 + SVB-1 风险 taxonomy + SVB-2 Agentic Risk Dimensions 设计完成。

---

## 1. 定位

能力资产准入扫描不是传统主机漏洞扫描，也不是单纯 SAST，而是面向 Agent / Skill / Tool / MCP 四类能力资产的**能力供应链准入控制**。

核心目标：
- 格式可解析（Manifest 校验通过）；
- 权限边界可理解（permission_json 完整）；
- 高风险能力不能发布（blocking 阻断）；
- Runtime 只能发现可信能力（Discover 仅返回已发布、可发现、非阻断资产）。

---

## 2. 当前已实现能力

| 能力 | 说明 |
|------|------|
| Manifest Spec v0.1 类型化校验 | 四类资产独立格式校验，0 errors 方可入库 |
| submit-review 自动扫描 | 提交审核时触发内置规则扫描器 |
| blocking 阻断提交审核 | blocking 风险直接 400 阻断，不可进入 pending_review |
| approve / publish 兜底检查 | 审批和发布时再次校验已扫描 + 非 blocking |
| 契约完整性检查 | input_schema / output_schema / permission_json / runtime_compatibility 检查 |
| Tool 专项风险 | 不安全 endpoint（http）、external_url 无域名限制 |
| MCP 专项风险 | 危险命令（rm -rf 等）、硬编码密钥、无效 transport |
| Prompt 注入增强 | MCP command/args/env + Skill instruction + Tool description 风险检查 |
| 扫描结果记录 | 每次扫描生成 ScanReport + ScanFinding，沉淀到治理状态 |
| **CompositeScanner 框架** | 组合扫描器：内置规则 + 外部 scanner 串行执行（P2-1 已实现） |
| **ExternalFindingNormalizer** | 外部 finding 到内部格式的归一化器 |
| **FakeExternalScanner** | 测试用外部扫描器，用于框架验证 |
| **SecretScannerProvider scaffold** | Provider-neutral 抽象层 + Mock adapter（P2-2B-lite） |
| **GitleaksScannerAdapter** | Gitleaks fallback 已实现并真实验证（P2-2C），默认 `HUB_GITLEAKS_ENABLED=false`，使用 `gitleaks dir`。Betterleaks 已是 primary，Gitleaks 保留为 fallback |
| **BetterleaksScannerAdapter** | Betterleaks primary scanner 已实现并真实验证（P2-2B），默认 `HUB_BETTERLEAKS_ENABLED=false`，使用 `betterleaks dir` |
| **外部 Scanner 脱敏** | Gitleaks report 含 Secret/Match/Line 原文 → Adapter 强制剥离，evidence 仅保留 rule_id/fingerprint/file/line |

外部扫描器框架已完成并验证；Betterleaks primary 和 Gitleaks fallback 均已实现并验证（默认 disabled）。**Semgrep / OSV / TruffleHog 尚未接入**。

当前测试基线：详见 `docs/20_current_baseline_summary.md`。

---

## 3. 准入流程

```
创建 / 导入
    │
    ▼
Manifest 格式校验（类型化验证）
    │
    ▼
创建版本（draft）
    │
    ▼
submit-review → 自动触发扫描
    ├── blocking → 400 阻断
    └── 非阻断 → pending_review
                    │
                    ▼
                  审批
                    │
                    ▼
                  发布
                    │
                    ▼
              Runtime Discover 可见
```

关键约束：
- **Discover 不重新扫描**：安全判断依赖准入阶段已沉淀的 ScanReport；
- **Resolve 不执行能力**：只返回已发布版本的契约数据；
- 契约缺失由准入体系推动补齐，Resolve 不生成缺失内容。

---

## 4. 契约完整性检查

Stage 6B 新增能力契约完整性检查，作为扫描发现项：

| 检查项 | 适用类型 | 缺失策略 |
|--------|----------|----------|
| `input_schema` | Skill, Tool | medium finding |
| `output_schema` | Skill, Tool | low finding |
| `permission_json` | Tool, MCP | medium finding |
| `runtime_compatibility` | Tool, MCP | low finding |
| `instruction` | Skill | medium finding |
| `dependencies` | Agent | low finding |
| `mcp_server.command/env` | MCP | critical finding（如含危险模式） |
| `invocation.endpoint` | Tool | medium finding（如 http） |

契约完整性问题不一定是漏洞，但会影响 Runtime 是否能安全装配能力。P1 阶段先作为扫描发现项记录，不阻断发布（除非达到 critical/blocking）。

---

## 5. 类型化风险检查

**Tool：**
- endpoint 协议安全检查（http → warning，应使用 https）
- external_url 开启但无 allowed_domains 限制
- permission_json 权限声明完整性

**MCP：**
- transport 合法性（stdio / sse / streamable_http）
- command 危险模式（rm -rf、curl | sh、wget | bash → blocking）
- env 中的硬编码密钥（API_KEY / SECRET / TOKEN / PASSWORD → blocking）
- transport + command + env 组合风险

**Skill：**
- instruction 内容完整性（Prompt 注入风险增强）
- 输入输出 schema 声明
- permission 边界

**Agent：**
- dependencies 声明（是否依赖可信能力）
- 可调用能力边界描述
- runtime_compatibility 声明

---

## 5B. SkillVetBench 风险分类参考

参考论文 _SkillVetBench: Benchmarking Security Risk Detection and Verification in Open Agentic Skill Ecosystems_（arXiv:2606.00925，详见 `docs/26_skillvetbench_reference_analysis.md`），将三类七种风险分类纳入 Hub 安全准入 taxonomy。

### 5B.1 分类体系

| 威胁大类 | 攻击类别 | 关键指标 |
|----------|----------|----------|
| **Code-execution threats** | Command Injection | os.system(), subprocess, exec(), shell=True, shell pipe operators |
| | Unsafe File Operations | 路径穿越（../../）、写 /etc /tmp、shutil.rmtree |
| **Instruction-layer / agentic threats** | Prompt Injection | 外部内容作为 agent 指令、间接注入（retrieved docs/web-fetched content） |
| | Memory Poisoning | 未验证用户输入写入持久内存、跨 session 持久化恶意指令 |
| | Privilege Abuse | sudo、禁用安全控制、绕过认证、scope drift（声明范围外操作） |
| **Data / supply-chain threats** | Data Exposure | 外发 HTTP 请求、base64 编码敏感数据、硬编码 API key/credential |
| | Supply Chain | pip/npm install、wget/curl 无完整性校验下载远程脚本、域名仿冒 |

### 5B.2 Hub 当前覆盖映射

| SkillVetBench 分类 | Hub 当前覆盖 | 当前机制 | 缺口 | 后续建议 |
|----------|------|------|------|------|
| Command Injection | ✅ 部分 | MCP command 危险模式（rm -rf、curl \| sh、wget \| bash） | subprocess、shell=True、exec()、pipe operators 检测 | RuleScanner 增强细粒度检测 |
| Unsafe File Operations | ⚠️ 有限 | 无专项规则 | 路径穿越检测、/etc /tmp 写入、rmtree 检测 | 增加文件操作专项规则（SVB-1+） |
| Prompt Injection | ✅ 部分 | 中英文 prompt injection 检测、role confusion、MCP command/args/env + Tool description 间接注入 | 跨组件间接注入、retrieved content 注入 | Semantic Scanner（P3）/ Sandbox（P3） |
| Memory Poisoning | ❌ 无 | 未覆盖 | persistent memory writes、跨 session 持久化 | Eval Sandbox / Runtime evidence（P3） |
| Privilege Abuse | ⚠️ 部分 | dangerous command 检查 + permission_json 完整性 | sudo、disable security、auth bypass、scope drift | Permission Tier 增强（P2/P3） |
| Data Exposure | ✅ 部分 | Betterleaks/Gitleaks secret 扫描 + external_url 风险检查 | encoded exfiltration、runtime outbound evidence | Sandbox / egress monitoring（P3） |
| Supply Chain | ⚠️ 有限 | MCP env 硬编码密钥、Agent dependencies 检查 | pip/npm install、wget/curl remote script、unverified binary | OSV / Semgrep（P2）/ Sandbox（P3） |

**说明**：
- ✅ 部分 = 已有覆盖但可增强；⚠️ 有限 = 少量覆盖或间接覆盖；❌ 无 = 未覆盖
- 所有缺口均为文档级参考，不要求立即修改代码

---

## 5C. 高权限原语与 Permission Tier

参考 SkillVetBench 论文发现，运行时攻击集中在以下高权限原语：`exec`、`write_file`、`install_skill`、`spawn`、`subagent`。

### 5C.1 Permission Tier 设计（文档级）

| Tier | 级别 | 典型操作 | 风险 |
|:---:|------|----------|------|
| L0 | read-only | GET、查询、只读 API | 低 |
| L1 | network read | 外部 HTTP GET、web_fetch | 低-中 |
| L2 | file write | 本地文件写入、日志写入 | 中 |
| L3 | external call / webhook | POST to external URL、API 调用 | 中-高 |
| L4 | install / spawn / exec | pip install、npm install、subprocess、exec() | 高 |
| L5 | irreversible / privileged | sudo、disable security、DELETE、权限提升 | 严重 |

### 5C.2 对 Hub 的影响

- 当前 Hub 还没有完整 permission tier 实现；
- 可作为 P2/P3 `permission_json` 增强方向；
- Runtime Discover 阶段可按 tier 过滤高权限 Skill；
- 不改变当前代码。

---

## 5D. Agentic Risk Dimensions（参考）

参考 SkillVetBench 论文的 SARS（Skill Agentic Risk Score）五维评估体系，作为 Hub 安全评估设计的长期参考：

| 维度 | 缩写 | 描述 | Hub 可映射内容 |
|------|:---:|------|---------------|
| Instruction Fidelity Risk | IFR | 指令被 prompt injection 劫持的可能性 | 当前 Prompt 注入检查 + Skill instruction 完整性 |
| Data Gravity | DG | Skill 可读写的敏感数据等级 | `data_classification` 字段（如有）+ permission_json |
| Action Irreversibility | AI | 操作是否可撤销 | Tool endpoint HTTP method 检查 |
| Blast Radius | BR | 单次攻击的影响范围 | Runtime compatibility + dependencies 声明 |
| Chain Amplification | CA | 与其他 Skill 组合时危险加剧程度 | HubItemRelation + cross-skill dependency 检查 |

**重要说明**：
- SARS 是论文提出的评估参考框架，**Hub 当前不实现 SARS scoring**；
- Hub 当前仍使用 `risk_level`（blocking/high/medium/low）+ Gate 模型做准入决策；
- SARS 维度可作为后续 Eval Sandbox（P3）或高级安全报告的参考；
- **不进入当前 blocking 主链**。

详细设计见 `docs/27_agentic_risk_dimensions_design.md`：包含五维度到 Hub 的完整信号映射、risk_dimensions 数据结构（放入 `ScanFinding.evidence` + `ScanReport.summary`）、Permission Tier 结合设计、三种候选计算方式（规则映射 / Semantic Scanner / Eval Sandbox）。

---

### 6.1 架构

```
内置规则扫描器（已实现）
    └── Hub 特有风险：MCP command、Prompt 注入、契约完整性

外部扫描器（通过统一接入接口补充）
    ├── Secret 扫描
    ├── SAST
    ├── 依赖漏洞
    └── 结构化策略
```

### 6.2 候选扫描器

| 扫描器 | 定位 | 许可 | 优先级 |
|--------|------|------|:---:|
| 内置规则扫描器 | Hub 特有风险 | — | ✅ 已实现 |
| Betterleaks | Secret 扫描（primary） | MIT | ✅ 已实现（P2-2B） |
| Gitleaks | Secret 扫描（fallback） | MIT | ✅ 已实现（P2-2C） |
| Semgrep CE | 代码/结构化规则 | LGPL | P2 |
| OSV-Scanner | 依赖漏洞 | Apache 2.0 | P2 |
| OPA / Conftest | 结构化策略 | Apache 2.0 | P3 |
| promptfoo / garak / PyRIT | LLM 红队评估 | MIT / Apache / MIT | P3 |
| Semantic Scanner（预留） | LLM-as-a-Judge 语义评估 | — | P3（不进入准入主链） |
| Eval Sandbox（预留） | Docker 沙箱执行验证 | — | P3（独立服务） |

promptfoo / garak / PyRIT 不进入第一阶段准入阻断主链，放在 P2/P3 红队评估中使用。

---

## 7. License 风险

| 扫描器 | 许可 | 风险 | 处理 |
|--------|------|------|------|
| Gitleaks | MIT | 低 | ✅ 可进入默认链路 |
| OSV-Scanner | Apache 2.0 | 低 | ✅ 可进入默认链路 |
| OPA / Conftest | Apache 2.0 | 低 | ✅ 可进入默认链路 |
| Semgrep CE | LGPL | 需确认分发义务 | 📋 待法务确认 |
| TruffleHog | AGPL-3.0 | 高（再分发限制） | ❌ 不进入默认链路 |
| ShellCheck | GPL-3.0 | 如进入链路需确认 | 📋 待确认 |

**当前未完成法务审查，不得写成已确认。**

---

## 8. Gate 模型

| 级别 | 行为 | 当前实现 |
|------|------|:---:|
| **Block** | 阻断操作 | ✅ submit-review 400 阻断 blocking |
| **Review** | 需安全审核确认 | 📋 high risk 需 SecReviewer 确认 |
| **Warn** | 放行但记录 finding | ✅ medium/low 记录到 ScanReport |
| **Observe** | 仅观察，不记录 | 🔜 后续 |

当前主要落地 blocking 阻断；完整 Gate 模型（Review 多级审批、waiver、baseline）是后续路线。

---

## 9. 后续路线

**P1（当前阶段）：**
- 完成安全准入设计文档（本文档）
- 继续增强内置规则（Prompt 注入、Tool/MCP 专项）
- 确认外部扫描器 license
- 准备外部扫描器统一接入方案

**P2：**
- 接入 Semgrep / OSV-Scanner
- SARIF 导入/导出
- waiver / baseline 机制
- 外部扫描器聚合与统一 Gate 决策
- Permission Tier 增强（RuleScanner 高权限原语专项检查）

**P3：**
- Eval Sandbox（参考 SkillVetBench Stage 2 架构：Docker 隔离执行 + 三层观测 Host/Agent/Skill + 运行时证据 trace）
- Semantic Scanner（参考 SkillVetBench Stage 1 LLM-as-a-Judge，不进入准入主链）
- garak / PyRIT 红队评估
- 运行态安全监控
- 资产供应链透明度（SBOM / provenance）
- Internal malicious skill benchmark（测试样本集，覆盖三类七种攻击类别）

---

## 10. 文档参考

| 文档 | 说明 |
|------|------|
| `docs/02_solution_design.md` | 整体方案设计 |
| `docs/03_platform_integration.md` | 平台集成部署设计 |
| `docs/07_rbac_approval_design.md` | RBAC 与审批设计 |
| `docs/12_observability_logging_design.md` | 可观测性设计 |
| `docs/15_manifest_spec_v0_1.md` | Manifest Spec v0.1 |
| `docs/17_external_scanner_adapter_design.md` | 外部扫描器 Adapter 设计 |
| `docs/18_secret_scanner_provider_selection.md` | Secret Scanner Provider 选型记录 |
| `docs/23_secret_scanner_deployment_design.md` | Secret Scanner 部署方案设计 |
| `docs/26_skillvetbench_reference_analysis.md` | SkillVetBench 论文参考分析 |
| `docs/27_agentic_risk_dimensions_design.md` | Agentic Risk Dimensions 设计 |
