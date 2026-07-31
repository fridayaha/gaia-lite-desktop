# 安全准入与外部扫描器工程证据

日期：2026-06-02 | 版本：v0.3 | 状态：Gitleaks fallback 已实现并真实验证；Betterleaks primary 已实现；SVB-0 论文分析 + SVB-1 taxonomy 增强 + SVB-2 Agentic Risk Dimensions 设计完成

---

## 1. 解决的问题

Hub 管理 Agent / Skill / Tool / MCP 四类能力资产。能力包上传后不能直接发布，需经过多层安全检查：

- **格式有效性**：Manifest 必须符合对应资产类型的 Schema；
- **契约完整性**：Skill 需 input_schema、Tool 需 output_schema、MCP 需 permission_json；
- **风险检测**：Prompt 注入、危险命令、硬编码密钥、不安全端点、无效 transport；
- **外部扫描**：接入 Gitleaks 检测 Secret（API key / token / 密钥）；
- **审批准入**：blocking 风险禁止提交审核，high risk 记录但不阻断；
- **Runtime 可见性**：仅已发布、可发现、非阻断资产对外可见。

---

## 2. 当前实现

| 层级 | 组件 | 说明 |
|------|------|------|
| 格式校验 | Manifest Spec v0.1 | 四类型独立校验，errors 阻断入库 |
| 契约完整性 | RuleScanner | input_schema / output_schema / permission_json 检查 |
| Prompt 注入 | RuleScanner | 中英文注入模式匹配（high/blocking） |
| Tool/MCP 专项 | RuleScanner | 危险命令、硬编码密钥、不安全端点、无效 transport |
| External 框架 | CompositeScanner | 内置规则 + 0..N external scanner 串行执行 |
| Finding 归一化 | FindingNormalizer | 外部 severity → Hub severity + metadata 归一化 |
| Secret Scanner scaffold | SecretScannerProvider | Provider-neutral 抽象（Protocol + Mock adapter） |
| **Gitleaks fallback** | **GitleaksScannerAdapter** | ✅ 已实现并真实验证 |
| Gate 决策 | submit-review / approve / publish | blocking → 400；high → 进入 pending_review |

---

## 3. 外部扫描器链路

```
RuleScanner (built-in)
    │
    ▼
CompositeScanner
    ├── RuleScannerAdapter
    └── GitleaksScannerAdapter (HUB_GITLEAKS_ENABLED=true)
            │
            ├── gitleaks dir <tmpdir>
            ├── read report JSON
            ├── strip Secret / Match / Line
            ├── normalize to Hub finding
            │
            ▼
    FindingNormalizer → risk_type: ext:gitleaks:{RuleID}
            │
            ▼
    ScanFinding (DB) → severity=high
            │
            ▼
    submit-review Gate
        ├── blocking → 400
        └── high → pending_review (not blocking)
```

---

## 4. 脱敏与安全边界

| 规则 | 实现 |
|------|------|
| Gitleaks report 含 Secret/Match/Line 原文 | ✅ 已验证 |
| Adapter 强制剥离 Secret/Match/Line | ✅ `_STRIP_FIELDS` frozenset |
| evidence 仅保留元数据 | ✅ RuleID、Fingerprint、File、Line、Entropy、Tags |
| Invalid JSON report → scanner_error | ✅ 不等同于 no findings |
| scanner_error → low severity | ✅ 不阻断 submit-review |
| scanner_error → 不抛 500 | ✅ CompositeScanner 捕获 |
| Gitleaks 默认 disabled | ✅ `HUB_GITLEAKS_ENABLED=false` |
| 版本自动检测 | ✅ `gitleaks version` |
| 临时目录自动清理 | ✅ `tempfile.TemporaryDirectory` |
| License 未法务确认 | ⚠️ 待确认 |
| Betterleaks primary scanner | ✅ 已实现并验证（P2-2B，v1.3.1，默认 `HUB_BETTERLEAKS_ENABLED=false`） |

---

## 5. 测试与结果

| 类别 | 数量 | 说明 |
|------|:---:|------|
| 真实 CLI 测试 | 7 | `@pytest.mark.skipif(not HAS_GITLEAKS)` |
| Mock 测试 | 13 | 覆盖 binary missing / timeout / exit code / invalid JSON |
| 集成测试 | 6 | ScanService / submit-review / redaction / CompositeScanner |
| 总 baseline | **608 passed，0 failed** | |

关键验证：
- Gitleaks detects fake stripe key → finding 生成 ✅
- Secret/Match/Line 不在 evidence 中 ✅
- `sk_test_...` 原文不在 evidence 中 ✅
- finding 进入 ScanReport → risk_level=high ✅
- submit-review high finding 不阻断 → pending_review ✅
- scanner_error 不阻断 submit-review ✅

---

## 6. 一句话价值总结

将 Hub 安全准入从内置规则扫描升级为可扩展外部扫描框架，并接入 Betterleaks primary + Gitleaks fallback，实现 Secret 风险检测、脱敏归一化和统一 Gate 决策。

## 7. 后续安全路线参考

**SkillVetBench** 论文（arXiv:2606.00925，详见 `docs/26_skillvetbench_reference_analysis.md`）作为 Hub 安全准入后续扩展的指导参考：

- 当前 Hub 已完成 **deterministic rule + external scanner + Gate** 准入主链；
- SkillVetBench 的三类七种风险分类已纳入 Hub taxonomy（`docs/05_admission_security_design.md` 第 5B 节）；
- 后续可扩展方向（P3）：
  - **Semantic Scanner**：LLM-as-a-Judge 语义评估（不进入准入主链，不成为 blocking 条件）；
  - **Eval Sandbox**：Docker 隔离执行 + 运行时证据 trace（独立服务，不进入 Hub Core）；
  - **Internal Benchmark**：覆盖三类七种攻击类别的恶意 Skill 测试样本集。
- 以上均未实现，当前仅为文档设计和路线参考。

**Agentic Risk Dimensions**（SVB-2，`docs/27_agentic_risk_dimensions_design.md`）：
- 五维度解释性风险模型已设计，不替代当前 risk_level/Gate；
- 设计放入 `ScanFinding.evidence` + `ScanReport.summary`（不改 DB）；
- 后续 P2 可做规则映射原型，P3 可通过 Semantic Scanner / Eval Sandbox 填充。
