# Agentic Risk Dimensions 设计

版本：v0.1 | 日期：2026-06-02 | 状态：SVB-2 设计阶段，不涉及代码实现。

> 参考论文：SkillVetBench SARS 五维评估体系（详见 `docs/26_skillvetbench_reference_analysis.md`）

---

## 1. 定位与边界

### 1.1 设计目标

将 SkillVetBench 的五维 Agentic Risk Dimensions 转化为 Hub 可理解的设计框架：

- **解释性维度**：帮助理解"为什么某个 Skill 是危险的"，而非替代准入决策；
- **不替代现有 risk_level**：当前 Gate 仍使用 blocking/high/medium/low 做准入；
- **不直接进入 blocking 主链**：五维度不作为 submit-review 的阻断条件；
- **初期只作为 evidence/report 中的解释字段**；
- **后续可用于 Eval Sandbox / Semantic Scanner**。

### 1.2 五维度来源

| 维度 | 缩写 | SkillVetBench 权重 | 含义 |
|------|:---:|:---:|------|
| Instruction Fidelity Risk | IFR | 2.0× | 指令被 prompt injection 劫持的可能性 |
| Data Gravity | DG | 1.5× | Skill 可读写的敏感数据等级 |
| Action Irreversibility | AI | 1.5× | 操作是否可撤销 |
| Blast Radius | BR | 2.0× | 单次攻击的影响范围 |
| Chain Amplification | CA | 2.0× | 与其他 Skill 组合时危险加剧程度 |

每个维度 0-3 分，论文加权归一化得到 SARS ∈ [0, 10]。

### 1.3 Hub 不使用 SARS 公式

- SARS 公式的权重（IFR/BR/CA=2.0×, DG/AI=1.5×）是论文基于 agentic 执行上下文设计的；
- Hub 当前不执行能力，直接套用无意义；
- 五维度在 Hub 中作为**解释性框架**，不作为**决策公式**；
- 后续 P3 Eval Sandbox 阶段可引入维度加权作为内部参考。

---

## 2. 五维度到 Hub 的映射

### 2.1 映射总表

| 维度 | SkillVetBench 含义 | Hub 可观测信号 | 当前可计算 | 当前覆盖 | 后续增强 |
|------|-------------------|--------------|:---:|:---:|------|
| IFR | 指令劫持风险 | prompt injection finding、role override、指令注入、user input in instruction | 部分 | RuleScanner 部分覆盖 | Semantic Scanner (P3) |
| DG | 数据敏感度 | secret finding、permission_json、敏感字段名 | 部分 | Betterleaks/Gitleaks + RuleScanner | permission_json 增强 (P2) |
| AI | 操作不可逆性 | HTTP method、exec/write_file、publish/send/delete | 部分 | Tool endpoint 检查 | 新增 side_effect 信号 (P2) |
| BR | 爆炸半径 | dependencies、visibility_scope、permission scope | 部分 | dependencies/relations + visibility_scope 字段 | Runtime workspace filtering (MT-3) |
| CA | 链式放大 | HubItemRelation、Agent dependencies、high-permission chain | 部分 | Runtime Resolve + 循环检测 | 链式风险评分 (P3) |

### 2.2 IFR：Instruction Fidelity Risk

**含义**：外部输入能否劫持 Skill 的指令流。分数越高，Skill 越容易被 prompt injection 操控。

**Hub 可观测信号**：

| 信号 | 数据来源 | 当前检测 |
|------|----------|:---:|
| Prompt injection finding | RuleScanner `PROMPT_RULES`（中文/英文） | ✅ |
| Role override / role confusion | RuleScanner "你现在的角色是" / "you are now a" | ✅ |
| Tool description 间接注入 | RuleScanner `INDIRECT_PROMPT_RULES` | ✅ |
| Skill instruction 含 override 模式 | RuleScanner "ignore previous instructions" / "忽略以上规则" | ✅ |
| User input 直接进入 instruction | `manifest_json.instruction` 字段检查 | 可增加 |
| MCP prompt/resource 含隐藏指令 | `mcp_server.command/args/env` 检查 | ⚠️ 部分（command 危险模式） |

**当前状态**：
- 规则扫描部分覆盖（内联 + 间接注入规则）；
- 无语义分析（无法检测被改写的指令）；
- 无运行时观测（无法确认指令是否真的被劫持）。

**IFR 赋分建议（设计级）**：

| 分数 | 条件 |
|:---:|------|
| 0 | 无 prompt injection finding，无 user input in instruction |
| 1 | 低严重度 indirect prompt finding |
| 2 | high 严重度 prompt injection finding，或 instruction 包含外部输入 |
| 3 | critical prompt injection finding，或 role override + 外部输入组合 |

### 2.3 DG：Data Gravity

**含义**：Skill 能接触的数据敏感程度。分数越高，数据泄露影响越大。

**Hub 可观测信号**：

| 信号 | 数据来源 | 当前检测 |
|------|----------|:---:|
| Secret scanner finding | BetterleaksScanner / GitleaksScanner | ✅ |
| 硬编码密钥 | RuleScanner `SECRET_RULES` | ✅ |
| permission_json 含敏感权限 | `permission_json` 字段 | ⚠️ 粗粒度 |
| input_schema / output_schema 含敏感字段名 | `input_schema` / `output_schema` | 可增加 |
| external_url 启用 | permission_json.external_url | ✅ |
| allowed_domains 缺失或过宽 | permission_json.allowed_domains | ✅ |
| MCP env/args 含 credential key | `_scan_by_type` MCP block | ✅ |

**当前状态**：
- Betterleaks/Gitleaks 已覆盖 hardcoded secret；
- RuleScanner 覆盖内联密钥模式；
- permission_json 风险仍较粗（无法区分"读 public API" vs "读写财务数据"）。

**DG 赋分建议（设计级）**：

| 分数 | 条件 |
|:---:|------|
| 0 | 无 secret 发现，permission 为只读，无 external_url |
| 1 | permission 含 external_url 或读非公开数据 |
| 2 | secret scanner finding (high) 或 permission 含写敏感数据 |
| 3 | critical secret finding 或 permission 含 credential/PII 访问 |

### 2.4 AI：Action Irreversibility

**含义**：Skill 的操作是否可撤销。分数越高，误用后果越不可逆。

**Hub 可观测信号**：

| 信号 | 数据来源 | 当前检测 |
|------|----------|:---:|
| Tool invocation.method = POST/PUT/DELETE | `manifest_json.invocation.method` | ✅ 部分（仅 http 检查） |
| MCP command 含写操作 | `mcp_server.command` | ✅ 部分（rm -rf blocking） |
| exec / write_file / subprocess | `manifest_json` 或 `config_json` | 可增加 |
| publish / send / transfer / delete 动作描述 | `manifest_json` 或 instruction | 可增加 |
| Tool endpoint 不安全协议 | `tool:insecure_endpoint` | ✅ |

**当前状态**：
- Tool endpoint http/https 可部分判断；
- 缺少结构化 `side_effect` 字段；
- 无法区分"只读 API"和"发送消息/删除数据"。

**AI 赋分建议（设计级）**：

| 分数 | 条件 |
|:---:|------|
| 0 | 仅 GET，无写操作，声明为只读 |
| 1 | 含 POST/PUT 操作，有明确 undo 路径 |
| 2 | 含 DELETE 或 MCP command 有写操作（非 blocking 级） |
| 3 | 含 irreversible 操作（发送消息、金融交易、文件删除、权限变更） |

### 2.5 BR：Blast Radius

**含义**：单次攻击影响的范围。分数越高，影响越大。

**Hub 可观测信号**：

| 信号 | 数据来源 | 当前检测 |
|------|----------|:---:|
| visibility_scope | `hub_items.visibility_scope`（字段已有） | ✅ 字段存在（MT-1） |
| dependency count | `manifest_json.dependencies` / HubItemRelation | ✅ |
| agent 可调用 tool 数量 | `manifest_json` Agent dependencies | ✅ |
| permission_json 范围 | `permission_json` | ⚠️ 粗粒度 |
| allowed_domains 数量 | permission_json.allowed_domains | ✅ |
| workspace / organization / public 可见 | visibility_scope | ⚠️ 字段存在但 Runtime filtering 未实现 |

**当前状态**：
- dependencies/relations 已有；
- visibility_scope 字段已在 MT-1 写入；
- Runtime workspace filtering（MT-3）未实现。

**BR 赋分建议（设计级）**：

| 分数 | 条件 |
|:---:|------|
| 0 | private，无依赖，仅影响单个用户 |
| 1 | workspace 范围，少量依赖（<3） |
| 2 | organization 范围，多依赖，高权限 |
| 3 | public，跨平台影响，外部依赖多，可被外部调用 |

### 2.6 CA：Chain Amplification

**含义**：该 Skill 与其他 Skill 组合时，危险是否显著放大。

**Hub 可观测信号**：

| 信号 | 数据来源 | 当前检测 |
|------|----------|:---:|
| HubItemRelation | `hub_item_relations` 表 | ✅ |
| Agent dependencies 含高风险 Skill | `manifest_json.dependencies` | ✅ |
| Tool/MCP/Skill 组合链 | Runtime Resolve dependencies | ✅ |
| 循环依赖 | Runtime Resolve 循环检测 | ✅ |
| high-permission dependency chain | 需要遍历依赖图 | 可增加 |

**当前状态**：
- Runtime Resolve 已有 dependencies 展开 + 循环检测；
- 已有 dependency warnings；
- 还没有链式风险评分；
- 不遍历依赖链上的 permission 级别。

**CA 赋分建议（设计级）**：

| 分数 | 条件 |
|:---:|------|
| 0 | 无依赖或仅依赖低风险 Skill，自包含 |
| 1 | 少量依赖，依赖均为低风险 |
| 2 | 依赖中含高风险 Skill 或 Tool，可能组成攻击链 |
| 3 | 依赖链中包含 exec/write_file/install Skill，可作为攻击放大器 |

---

## 3. 与当前 risk_level / Gate 的关系

### 3.1 当前准入 Gate（不变）

```
FindingSeverity → RiskLevel → Gate
    low    → low      → Warn
    medium → medium   → Warn
    high   → high     → Warn
    critical → blocking → Block
```

**Agentic Risk Dimensions 不改变这一机制。**

### 3.2 五维度的角色

```
┌─────────────────────────────────────────────┐
│           当前准入主链（不变）                  │
│  RuleScanner + ExternalScanner               │
│       │                                       │
│       ▼                                       │
│  FindingSeverity → RiskLevel → Gate           │
│  (critical→blocking, high→high, ...)          │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│        Agentic Risk Dimensions（新增）         │
│  解释性叠加层，不直接 blocking                  │
│       │                                       │
│       ▼                                       │
│  ScanFinding.evidence.agentic_risk_dimensions │
│  ScanReport.summary.agentic_risk_dimensions   │
│       │                                       │
│       ▼                                       │
│  用于：安全报告 / Discover 过滤 / Eval Sandbox  │
└─────────────────────────────────────────────┘
```

### 3.3 未来可补充的 Gate 增强（P3，本阶段不实现）

以下策略仅在后续阶段考虑，当前不做：

| 组合条件 | 建议决策 | 阶段 |
|----------|----------|:---:|
| 高 DG + 高 AI + 高 CA | review required（需安全审核确认） | P3 |
| 高 IFR + 高 CA | semantic review（需语义扫描确认） | P3+ |
| high-permission primitive + public visibility | enhanced review（增强审核） | P3 |
| 涉及 L4/L5 permission tier 的 Skill | 阻断（或需 Eval Sandbox 验证） | P3+ |

**本阶段不实现任何组合 Gate 逻辑。**

---

## 4. risk_dimensions 数据结构设计

### 4.1 设计原则

- 不改 DB 字段，放入现有 `ScanFinding.evidence`（JSON 列）和 `ScanReport.summary`（JSON 列）；
- 初期可以不计算分数（score=null），只填 signals；
- rationale 不应由 LLM 自动生成（除非进入 P3 Semantic Scanner）；
- 当前 deterministic scanner 可以只填 signals，不填分数。

### 4.2 ScanFinding.evidence 中的 agentic_risk_dimensions

```json
{
  "field": "manifest_json.instruction",
  "matched": "prompt_injection:role_override_zh",
  "message": "instruction contains role override pattern",
  "agentic_risk_dimensions": {
    "instruction_fidelity_risk": {
      "score": null,
      "signals": ["prompt_injection:role_override"],
      "rationale": ""
    },
    "data_gravity": {
      "score": null,
      "signals": [],
      "rationale": ""
    },
    "action_irreversibility": {
      "score": null,
      "signals": [],
      "rationale": ""
    },
    "blast_radius": {
      "score": null,
      "signals": [],
      "rationale": ""
    },
    "chain_amplification": {
      "score": null,
      "signals": [],
      "rationale": ""
    }
  }
}
```

**score 范围 0-3，null 表示未计算。**

### 4.3 ScanReport.summary 中的 agentic_risk_dimensions

```json
{
  "total_findings": 5,
  "severity_counts": {"critical": 1, "high": 2, "medium": 2},
  "risk_types": ["mcp:dangerous_command", "secret:betterleaks:generic-api-key", "prompt_injection:role_override"],
  "scanners": ["BuiltInRuleScanner", "BetterleaksScannerAdapter"],
  "agentic_risk_dimensions": {
    "instruction_fidelity_risk": {
      "max_score": null,
      "signals": ["prompt_injection:role_override"],
      "summary": "instruction-layer threat detected"
    },
    "data_gravity": {
      "max_score": null,
      "signals": ["secret:betterleaks:generic-api-key"],
      "summary": "hardcoded credential detected"
    },
    "action_irreversibility": {
      "max_score": null,
      "signals": ["mcp:dangerous_command"],
      "summary": "irreversible shell command"
    },
    "blast_radius": {
      "max_score": null,
      "signals": ["visibility:public"],
      "summary": "public visibility"
    },
    "chain_amplification": {
      "max_score": null,
      "signals": ["dependency:agent_with_exec_permission"],
      "summary": "may amplify through dependencies"
    }
  }
}
```

**`max_score` 取所有 finding 中该维度的最高分（如有）；`signals` 为聚合的去重信号列表。**

### 4.4 维度填充规则（设计级）

| 维度 | 由哪些 finding 填充 | 来源 scanner |
|------|-------------------|-------------|
| IFR | `prompt_injection:*`, `contract:missing_instruction` | RuleScanner |
| DG | `secret:*`, `mcp:hardcoded_credential` | Betterleaks/Gitleaks/RuleScanner |
| AI | `mcp:dangerous_command`, `tool:insecure_endpoint`, `contract:missing_permission_json` | RuleScanner |
| BR | `visibility:*`, `contract:missing_dependencies` | HubItem metadata |
| CA | `dependency:*`, Agent dependencies | HubItemRelation/Runtime Resolve |

---

## 5. Permission Tier 与风险维度的结合

### 5.1 映射表

| Permission Tier | 影响的风险维度 | 影响程度 |
|:---:|------|:---:|
| L0 read-only | DG/BR 低 | 低 |
| L1 network read | DG/BR 中 | 低-中 |
| L2 file write | AI/BR 中高 | 中-高 |
| L3 external call / webhook | DG/BR 高 | 高 |
| L4 install / spawn / exec | AI/CA 高 | 高 |
| L5 irreversible / privileged | AI/BR/CA 极高 | 极高 |

### 5.2 使用方式

- Permission Tier 为 L4+ 的 Skill → IFR/CA 信号自动标记为关注；
- Permission Tier 为 L5 的 Skill → AI/BR 维度自动提升为最高关注；
- 当前不改 permission_json 字段结构，仅作为设计参考。

---

## 6. 候选计算方式

### 6.1 方式 A：规则映射（确定性）

基于现有 findings + metadata 确定性赋分。

| 维度 | 映射来源 | 优点 | 缺点 |
|------|----------|------|------|
| IFR | RuleScanner prompt injection findings | 可解释、低成本、确定性 | 只能检测已知模式 |
| DG | Betterleaks/Gitleaks findings + permission_json | 可解释、低成本 | 无法区分数据敏感级别 |
| AI | Tool endpoint method + MCP command 模式 | 可解释、低成本 | 缺少 side_effect 字段 |
| BR | visibility_scope + dependencies count | 可解释、低成本 | 缺少外部影响评估 |
| CA | HubItemRelation + dependency 遍历 | 可解释、低成本 | 不评估依赖的实际风险 |

**推荐作为 P2 初期实现方式。**

### 6.2 方式 B：LLM Semantic Scanner

基于 manifest/instruction/description 做语义判断。

- 优点：能覆盖 instruction-layer risk（如隐藏的恶意意图改写）；
- 缺点：不稳定、成本高、不能直接 blocking；
- 位置：P3 + `docs/17` Semantic Scanner Adapter 预留。

### 6.3 方式 C：Eval Sandbox Evidence

基于运行时 trace 计算。

- 优点：证据最强（如论文 Table 9 的沙箱 findings）；
- 缺点：需要独立 Docker 沙箱服务、执行成本高；
- 位置：P3 + `docs/17` Eval Sandbox Adapter 预留。

### 6.4 推荐路线

```
P2（当前）: 文档设计 + 规则映射原型
    └── RuleScanner findings 自动填充 evidence.agentic_risk_dimensions.signals

P3: Eval Sandbox
    └── Sandbox findings 填充 evidence.agentic_risk_dimensions 完整信息（含 score）

P3+: Semantic Scanner
    └── LLM 语义评估补充 IFR/CA 维度的 instruction-layer 检测
```

---

## 7. 测试与 Benchmark 设计

### 7.1 测试样本目录结构（SVB-5 阶段）

```
tests/fixtures/security_samples/
├── benign/
│   ├── simple_search_tool/
│   │   ├── manifest.json
│   │   ├── expected_taxonomy.json
│   │   └── expected_agentic_dimensions.json
│   └── read_only_skill/
│       └── ...
├── command_injection/
├── prompt_injection/
├── unsafe_file_ops/
├── memory_poisoning/
├── data_exposure/
├── supply_chain/
└── privilege_abuse/
```

### 7.2 每类样本需要的元数据

```json
{
  "sample_id": "prompt_injection_001",
  "attack_category": "prompt_injection",
  "threat_class": "instruction-layer/agentic",
  "expected_taxonomy": {
    "category": "Prompt Injection",
    "threat_class": "Instruction-layer/agentic threats",
    "key_indicators": ["ignore previous instructions", "role override"]
  },
  "expected_agentic_dimensions": {
    "IFR": {"min_score": 2, "signals": ["prompt_injection:direct"]},
    "DG": {"min_score": 0, "signals": []},
    "AI": {"min_score": 0, "signals": []},
    "BR": {"min_score": 0, "signals": []},
    "CA": {"min_score": 0, "signals": []}
  },
  "expected_gate": {
    "should_block": false,
    "severity_level": "high"
  }
}
```

**本阶段不创建样本，先完成设计。**

---

## 8. 与现有文档的关系

| 文档 | 关系 |
|------|------|
| `docs/26_skillvetbench_reference_analysis.md` | 论文分析，本文档的设计基础 |
| `docs/05_admission_security_design.md` | 5D 节已含 Agentic Risk Dimensions 摘要，引用本文档 |
| `docs/17_external_scanner_adapter_design.md` | Semantic Scanner / Eval Sandbox 预留，未来可通过这些 adapter 填充 dimensions |
| `docs/08_roadmap_workload.md` | SVB-2~SVB-5 阶段 |
| `docs/20_current_baseline_summary.md` | 当前基线（标注未实现） |

---

## 9. 后续步骤

| 阶段 | 内容 | 状态 |
|:---:|------|:---:|
| SVB-0 | 论文分析 | ✅ |
| SVB-1 | 风险分类 taxonomy 增强 | ✅ |
| SVB-2 | Agentic Risk Dimensions 设计（本文档） | ✅ |
| SVB-3 | Semantic Scanner 预留设计 | 📋 后续 |
| SVB-4 | Eval Sandbox 设计 | 📋 后续 |
| SVB-5 | Internal Benchmark 样本集 | 📋 后续 |

---

## 10. 文档参考

| 文档 | 说明 |
|------|------|
| `docs/26_skillvetbench_reference_analysis.md` | SkillVetBench 论文参考分析 |
| `docs/05_admission_security_design.md` | 安全准入设计 |
| `docs/17_external_scanner_adapter_design.md` | 外部扫描器 Adapter 设计 |
| `docs/08_roadmap_workload.md` | Roadmap |
| `docs/20_current_baseline_summary.md` | 当前基线说明 |
