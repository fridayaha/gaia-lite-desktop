# 外部扫描器 Adapter 设计

版本：v0.5 | 日期：2026-06-02 | 状态：P2-1 已实现，P2-2B-lite 已完成（provider-neutral scaffold），P2-2B BetterleaksScannerAdapter 已实现并验证（v1.3.1），P2-2C GitleaksScannerAdapter 已实现。SVB-3 Semantic Scanner + Eval Sandbox Adapter 预留设计。

---

## 一、架构

```
HubItemVersion
    │
    ▼
CompositeScanner
    ├── BuiltInRuleScanner（内置，必跑）
    ├── ExternalScanner 1（可选，失败→scanner_error finding）
    ├── ExternalScanner 2（可选）
    └── ...
    │
    ▼
FindingNormalizer（外部→内部格式归一化）
    │
    ▼
RiskAggregator（跨 scanner 汇总最高 risk_level）
    │
    ▼
ScanReport + ScanFinding[] → submit-review / approve / publish 准入
```

## 二、关键组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `CompositeScanner` | `backend/app/scanners/composite_scanner.py` | 组合扫描器，串行执行 built-in + externals |
| `ExternalScanner` Protocol | 同上 | 外部扫描器接口：`name` + `version` + `scan(version) → list[dict]` |
| `FindingNormalizer` | `backend/app/scanners/finding_normalizer.py` | severity 映射 + metadata 归一化 |
| `FakeExternalScanner` | `backend/app/scanners/fake_external_scanner.py` | 测试验证用，不作为生产默认 |

## 三、ExternalFindingNormalizer 行为

| 外部 severity | Hub FindingSeverity | Hub RiskLevel |
|---------------|---------------------|:---:|
| critical / error | critical | blocking |
| high | high | high |
| medium / warning | medium | medium |
| low / info / note | low | low |

- `risk_type` 格式：`ext:{scanner_name}:{rule_id}`
- scanner metadata（name/version/rule_id/confidence/location/external_ref）写入 `evidence` JSON
- P2-1 不改 DB 字段

## 四、scanner_error 行为

外部 scanner 异常时：
- 不抛出 500；
- 生成 `scanner_error:{scanner_name}` finding（severity=low）；
- 记录 `scanner.external_failed` 事件日志；
- 内置 scanner 失败仍视为系统错误。

## 五、P2 路线（已修订）

| 阶段 | 内容 |
|:---:|------|
| P2-1 | CompositeScanner + FakeExternalScanner + FindingNormalizer（✅ 已完成） |
| P2-2A | Secret Scanner Provider 选型 + CLI spike（✅ 已设计，`docs/18_secret_scanner_provider_selection.md`；环境未安装工具） |
| P2-2B-lite | provider-neutral scaffold：SecretScannerProvider / SecretFindingParser / SecretScannerConfig / redaction helper / MockSecretScannerAdapter（✅ 已完成，556 tests） |
| P2-2B | BetterleaksScannerAdapter（真实 CLI 实测后实现） | ✅ 已实现（v1.3.1，25 mock tests + 4 real CLI tests，默认 `HUB_BETTERLEAKS_ENABLED=false`） |
| P2-2C | GitleaksScannerAdapter（兼容 fallback，使用 `dir` 命令，默认 disabled） | ✅ 已实现（608 tests） |
| P2-3 | Semgrep CLI Adapter：调用 semgrep CLI → 解析输出 → 归一化 Finding |
| P3 | OSV-Scanner / OPA Conftest / 并行扫描 |
| 暂缓 | TruffleHog（AGPL-3.0）/ promptfoo / garak / PyRIT |

## 六、Secret Scanner Adapter 抽象（P2-2B/C 设计修订）

### 6.1 共同接口

```python
class SecretScannerAdapter:
    name: str           # "betterleaks" | "gitleaks"
    version: str        # detected from CLI
    bin_path: str       # configurable, default from PATH

    def scan(self, version: HubItemVersion) -> list[dict]:
        # 1. 写临时 JSON 文件
        # 2. 调用 CLI（subprocess.run, timeout）
        # 3. 读取 report JSON
        # 4. 归一化 findings
        # 5. 清理临时文件
        # 6. 异常 → scanner_error finding
```

### 6.2 CLI 命令

| Provider | 命令 |
|----------|------|
| Gitleaks v8.19+ | `gitleaks dir <tmpdir> --report-format=json --report-path=<report.json> --redact --timeout=30` |
| Betterleaks | `betterleaks dir <tmpdir> -f json -r <report.json> --redact`（v1.3.1 实测确认） |

### 6.3 共同脱敏策略

无论哪个 provider，Adapter 必须：
- **不读取** report JSON 中的 `Secret` / `Match` 字段
- **不写入** `Secret` / `Match` 原文到 `ScanFinding.evidence`
- 只提取：`rule_id`, `file`, `start_line`, `end_line`, `fingerprint`, `entropy`, `description`
- `evidence` 包含：`scanner_name`, `scanner_version`, `rule_id`, `file`, `line`, `fingerprint`, `entropy`, `redacted_match_length`
- `recommendation` 使用固定文案 + provider name

### 6.4 Severity 策略

| Provider | Severity | 说明 |
|----------|:---:|------|
| Betterleaks | 所有 finding → `high` | P2-2B 默认，后续可配置按 rule_id 升 critical |
| Gitleaks | 所有 finding → `high` | P2-2C 默认，同上 |

### 6.5 错误处理统一

| 异常 | 处理 |
|------|------|
| binary 不在 PATH | `scanner_error:provider_name` finding |
| timeout | `scanner_error:provider_name` finding |
| exit_code != 0/1 | `scanner_error:provider_name` finding |
| report JSON parse error | `scanner_error:provider_name` finding（不等同于 no findings） |
| 临时文件 I/O 错误 | `scanner_error:provider_name` finding |

### 6.6 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HUB_SECRET_SCANNER` | ``（空=禁用） | `betterleaks` 或 `gitleaks` |
| `HUB_SECRET_SCANNER_BIN` | ``（从 PATH 查找） | 二进制路径覆盖 |
| `HUB_SECRET_SCANNER_TIMEOUT` | `30` | 秒 |

### 6.7 Spike 验证脚本

`tools/spikes/secret_scanner_cli_spike.py` — 本地手工验证 CLI 可用性和 JSON 输出格式。不纳入 CI。

## 七、P2-2B-lite：Provider-Neutral Scaffold（✅ 已完成，2026-05-28）

在 Betterleaks / Gitleaks 均未安装的环境下，先建立 provider-neutral 抽象层，不实现任何真实 CLI 适配器。

### 7.1 新增文件

| 文件 | 内容 |
|------|------|
| `backend/app/scanners/secret_scanner.py` | `SecretScannerProvider` Protocol、`SecretFindingParser` Protocol、`SecretScannerConfig` dataclass、`redacted_evidence()` 脱敏辅助函数、`is_cli_not_found()` CLI 检测辅助函数 |
| `backend/app/scanners/secret_scanner_mock.py` | `MockSecretScannerAdapter`（实现 ExternalScanner 协议，配合 CompositeScanner 测试） |
| `backend/tests/test_secret_scanner_provider.py` | 26 tests（redaction、CLI not-found、Mock adapter 正常/失败/通过 CompositeScanner/通过 ScanService） |

### 7.2 关键约束

- `SecretFindingParser.parse()` **禁止**读取 `Secret` / `Match` 字段
- `redacted_evidence()` 默认剥离 `Secret` / `Match` / `secret` / `match` / `SecretHash`
- 支持自定义 `strip_fields` 传入额外需剥离的字段
- `SecretScannerConfig` 默认 `enabled=False`，`redact=True`
- `is_cli_not_found()` 用于适配器在 binary 缺失时生成 `scanner_error` 而非 500

### 7.3 MockSecretScannerAdapter

- 实现 ExternalScanner 协议（`name` + `version` + `scan()`）
- 默认生成一个 `high` severity finding
- 支持 `should_fail` 模式模拟 CLI 故障
- 不输出任何 secret 原文
- 可注入 `CompositeScanner(externals=[mock])` 进行端到端测试

## 八、CLI Spike 状态与 Adapter 实现决策（2026-05-28，修订）

### 8.1 当前 CLI 可用性

| 工具 | 安装状态 | 实测状态 |
|------|:---:|:---:|
| Betterleaks | ❌ 未安装 | 未实测（无 go/brew/docker） |
| Gitleaks | ✅ v8.30.1 | ✅ 已实测 — `gitleaks dir` 可用，JSON report 含 Secret/Match 原文 |

### 8.2 BetterleaksScannerAdapter 是否可以实现

**✅ 已实现**（2026-05-29）：
- 使用 `betterleaks dir` 命令
- JSON report 格式与 Gitleaks 几乎一致（PascalCase 字段名：RuleID, Description, File, Secret, Match, Fingerprint 等）
- Secret/Match/Line 强制剥离，不进入 evidence
- `--redact` flag 仅影响 stdout，不影响 report JSON
- `--validation` 为 opt-in flag，默认不联网
- Invalid JSON report → `scanner_error:betterleaks`（非 no findings）
- 默认 `HUB_BETTERLEAKS_ENABLED=false`
- 25 mock tests + 4 real CLI tests（`skipif` 保护）
- Betterleaks + Gitleaks 同时启用时两者都执行（Betterleaks 先，Gitleaks 后）
- 不做 finding dedup（后续阶段再做）

### 8.3 GitleaksScannerAdapter 状态

**✅ 已实现并真实验证**（commit `458df16` + `74bc36e` + `d165ec3`）：
- 使用 `gitleaks dir`（不用 deprecated `detect`）
- Report JSON 解析 + 归一化 through `ExternalFindingNormalizer`
- Secret/Match/Line 强制剥离，不进入 evidence
- Invalid JSON report → `scanner_error:gitleaks`（非 no findings）
- 默认 `HUB_GITLEAKS_ENABLED=false`
- 7 real CLI tests + 13 mock tests
- 真实 CLI 验证通过（detect/redact/submit-review）

### 8.4 Provider-Specific Parser

每个真实 CLI adapter 需要独立的 `SecretFindingParser` 实现：

```
BetterleaksFindingParser:
  field mapping: {rule_id, description, file, start_line, end_line, fingerprint, entropy}
  strip: {secret, match, Secret, Match}
  （字段名需实测后确定）

GitleaksFindingParser:
  field mapping: {RuleID→rule_id, Description, File, StartLine, EndLine, Fingerprint, Entropy}
  strip: {Secret, Match}
```

### 8.5 脱敏字段约束（重申）

无论 parser 如何实现：
- `Secret` / `Match` / `secret` / `match` / `SecretHash` **必须剥离**，不得进入 evidence
- evidence 只能包含：`rule_id`, `file`, `start_line`, `end_line`, `fingerprint`, `entropy`, `scanner_name`, `scanner_version`, `description`
- 如果 provider report 包含 `Secret` 原文，**bypass parser 不应该是一个选项**
- 所有 real CLI test 使用 `skipif`，仅在有工具环境中执行

## 九、Secret Scanner 部署方案

详见 `docs/23_secret_scanner_deployment_design.md`。

### 当前部署模型：方案 A（随 Hub API 安装 CLI）

```
Hub API 进程
├── BuiltInRuleScanner
├── BetterleaksScannerAdapter
│   └── subprocess.run("betterleaks dir tmpdir ...")
└── GitleaksScannerAdapter
    └── subprocess.run("gitleaks dir tmpdir ...")
```

| 方案 | 说明 | 阶段 |
|:---:|------|:---:|
| A | 随 Hub API 安装 CLI（当前） | P1 ✅ |
| B | 扫描 Worker（队列 + 独立进程） | P2/P3 |
| C | 工具容器 Sidecar/Job | P3+ |
| D | 平台统一扫描服务 | P2+ |

### Betterleaks / Gitleaks 命令（实测确认）

| Provider | 命令 |
|----------|------|
| Betterleaks v1.3.1 | `betterleaks dir <tmpdir> -f json -r <report.json> --redact` |
| Gitleaks v8.30.1 | `gitleaks dir <tmpdir> --report-format=json --report-path=<report.json> --redact --timeout=30` |

两者 JSON schema 几乎一致（PascalCase 字段名），`Secret`/`Match` 均包含原文，`--redact` 仅影响 stdout。

## 十、Semantic Scanner Adapter 预留

参考 SkillVetBench 论文 Stage 1（LLM-as-a-Judge 语义评估，详见 `docs/26_skillvetbench_reference_analysis.md`），预留 Semantic Scanner Adapter 接口设计。

### 10.1 定位

- LLM-as-a-Judge 可被建模为一种特殊的 `ExternalScanner`；
- 类似 Betterleaks/Gitleaks 的接入方式，通过 `CompositeScanner` 串行执行；
- **P3 之前不实现代码**。

### 10.2 关键约束

| 约束 | 说明 |
|------|------|
| 不进入准入主链 | Semantic Scanner 的 finding 不成为 blocking 决策来源 |
| 默认 disabled | 通过环境变量控制启用（`HUB_SEMANTIC_SCANNER_ENABLED=false`） |
| 仅输出 finding | 不输出 verdict（Benign/Suspicious/Malicious 判定不进入 Hub Gate） |
| 需要 evidence | 每个 finding 需附带 rationale、model version、confidence、affected artifact component |
| 不引入 LLM 依赖 | P3 实现时才评估具体 LLM SDK 选择 |

### 10.3 预期接口

```python
class SemanticScannerAdapter:
    """参考 SkillVetBench Stage 1 语义评估模式，P3 预留。"""
    name: str           # "semantic-scanner"
    version: str        # LLM model version (e.g., "qwen2.5-32b")
    enabled: bool       # default False

    def scan(self, version: HubItemVersion) -> list[dict]:
        # 1. 提取 Skill 的自然语言声明（instruction/manifest/config_json）
        # 2. 调用 LLM 进行语义分析（不执行能力）
        # 3. 输出 finding list（attack category, rationale, confidence, evidence）
        # 4. 异常 → scanner_error finding
```

### 10.4 安全边界

- 不向 LLM 发送 secret 原文；
- 不发送完整 MCP env 值（仅发送 key name）；
- 不发送用户数据；
- 所有 LLM 调用需超时 + 重试 + 熔断。

---

## 十一、Eval Sandbox Adapter 预留

参考 SkillVetBench 论文 Stage 2（Docker 沙箱执行验证），预留 Eval Sandbox 对接接口。

### 11.1 定位

- Sandbox **不属于 Hub Core**，作为独立评估服务；
- Hub 通过类似 ExternalScanner 的协议消费 Sandbox 输出；
- Hub 只接收结果（findings/evidence），不执行能力；
- **P3 之前不实现代码**。

### 11.2 架构

```
Eval Sandbox（独立服务，参考 SkillVetBench Stage 2）
    ├── Docker 隔离执行环境
    ├── Instrumented agent（记录 tool calls / logs / errors）
    ├── 三层观测（Host / Agent / Skill）
    └── 输出：findings + evidence traces
        │
        ▼
Hub CompositeScanner
    ├── ... existing scanners ...
    └── EvalSandboxAdapter（P3 预留）
        └── consume sandbox findings → normalize → ScanFinding
```

### 11.3 关键约束

| 约束 | 说明 |
|------|------|
| Sandbox 为独立服务 | Hub 不自建 Docker 运行环境 |
| 不进入准入主链 | Sandbox 结果不成为 submit-review blocking 条件 |
| 默认 disabled | 通过环境变量控制启用 |
| 仅输出 finding | 不输出 verdict，由 Hub Gate 决策 |
| 不做能力执行 | Hub 不运行 Skill，Sandbox 自行管理执行环境 |

### 11.4 Evidence Trace 格式（参考论文 Table 9）

```json
{
  "skill": "xiaohongshu-mcp",
  "attack_category": "Supply Chain",
  "layer": "Host",
  "finding": "Untrusted third-party binaries executed at runtime",
  "evidence": {
    "tool_calls": ["exec", "process"],
    "side_effects": ["cron jobs scheduled", "orphaned sessions"],
    "timestamp": "2026-06-02T00:42:00Z"
  }
}
```

### 11.5 安全边界

- Sandbox 与 Hub 网络隔离；
- Sandbox 不使用生产凭证；
- Sandbox 执行超时自动终止；
- Sandbox 输出不包含 secret 原文；
- Hub 不直接控制 Sandbox 内的 agent 行为。

---

## 文档参考

| 文档 | 说明 |
|------|------|
| `docs/05_admission_security_design.md` | 安全准入设计 |
| `docs/18_secret_scanner_provider_selection.md` | Secret Scanner 选型 |
| `docs/23_secret_scanner_deployment_design.md` | Secret Scanner 部署方案 |
| `docs/26_skillvetbench_reference_analysis.md` | SkillVetBench 论文参考分析 |
