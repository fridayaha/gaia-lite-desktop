# Betterleaks Secret Scanning — 工程能力证据

版本：v0.1 | 日期：2026-05-29 | 功能：P2-2B Betterleaks primary secret scanner adapter

---

## 功能概述

BetterleaksScannerAdapter 将 Betterleaks CLI（v1.3.1）接入 Hub 安全准入链路，作为 primary secret scanning provider。Betterleaks 是 Gitleaks 原作者主攻方向，维护活跃。

## 解决问题

1. **提供更先进的 secret scanning**：Betterleaks 支持 CEL filtering、BPE tokenization、HTTP validation（opt-in），比 Gitleaks 功能更强
2. **双 provider 冗余**：Betterleaks（primary）+ Gitleaks（fallback），提高发现覆盖率
3. **类型化资产扫描**：自动将 HubItemVersion 内容（manifest/config/schema/permission/runtime）写入临时 JSON 文件，喂入 CLI 扫描

## 实现方式

### 架构

```
HubItemVersion
  └─> BetterleaksScannerAdapter
        ├─ _write_version_files() → tmpdir/*.json
        ├─ subprocess.run("betterleaks dir tmpdir -f json -r report.json --redact")
        ├─ _parse_report_json() → raw findings
        └─ _normalize() → redacted evidence dict
           ├─ 剥离 Secret/Match/Line 原文
           └─ 保留 RuleID/Fingerprint/File/StartLine/EndLine/Entropy/Tags/Attributes
```

### CLI 命令

```bash
betterleaks dir <tmpdir> -f json -r <report.json> --redact
```

| 参数 | 说明 |
|------|------|
| `dir` | 扫描目录（非 git history） |
| `-f json` | JSON 格式输出 |
| `-r <path>` | 报告写入文件（非 stdout） |
| `--redact` | 脱敏 stdout/logs（不影响 report JSON） |
| `--validation` | **opt-in**，默认不联网 |

### 脱敏策略

- `--redact` flag 仅影响 stdout/logs，**report JSON 仍包含 Secret/Match 原文**
- Adapter 在 `_normalize()` 中强制剥离 `Secret`, `Match`, `Line`, `Commit`, `Author`, `Email`, `Date`, `Message`, `SymlinkFile`
- evidence 仅保留：`scanner_name`, `scanner_version`, `RuleID`, `Description`, `File`, `StartLine`, `EndLine`, `Fingerprint`, `Entropy`, `Tags`, `Attributes`, `redacted_match_length`
- 测试验证：`sk_test_...` / `ghp_...` / `wJalrXUtn...` 等 fake secret 不在 evidence 中

### JSON Schema 映射

Betterleaks v1.3.1 与 Gitleaks v8.30.1 采用相同字段名（PascalCase），几乎完全兼容。Betterleaks 多了 `Attributes` 字段。

### Severity 策略

所有 Betterleaks finding → `high`。后续可按 validation_result / rule_id 做 override（critical/blocking）。

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HUB_BETTERLEAKS_ENABLED` | `false` | 默认禁用 |
| `HUB_BETTERLEAKS_BIN` | `betterleaks` | 二进制路径 |
| `HUB_BETTERLEAKS_TIMEOUT_SECONDS` | `30` | 超时秒数 |
| `HUB_BETTERLEAKS_CONFIG` | `` | 可选自定义 config |

## scanner_error 行为

| 错误场景 | 行为 |
|----------|------|
| binary 不在 PATH | `scanner_error:betterleaks`，severity=low |
| subprocess timeout | `scanner_error:betterleaks`，severity=low |
| exit_code != 0/1 | `scanner_error:betterleaks`，severity=low |
| JSON parse failure | `scanner_error:betterleaks`，severity=low |
| report file missing | `scanner_error:betterleaks`，severity=low |

均不阻断 submit-review，不产生 500。

## 测试结果

**测试日期**：2026-05-29
**总测试**：633 passed，0 failed（+25 新增 Betterleaks tests）

### Mock tests（21 tests）

| 测试 | 说明 |
|------|------|
| `test_disabled_returns_empty` | 禁用时返回空 |
| `test_env_disabled_by_default` | env 默认禁用 |
| `test_binary_missing_raises` | binary 缺失抛 RuntimeError |
| `test_exit_code_0_no_findings` | rc=0 → no findings |
| `test_exit_code_1_with_report` | rc=1 + report → finding |
| `test_secret_and_match_not_in_evidence` | Secret/Match/Line 不进入 evidence |
| `test_exit_code_2_scanner_error` | rc=2 → scanner_error |
| `test_timeout_propagates` | timeout → TimeoutExpired |
| `test_invalid_json_report_raises` | invalid JSON → RuntimeError |
| `test_invalid_json_via_composite_produces_scanner_error` | invalid JSON via CompositeScanner → scanner_error |
| `test_scanner_name_and_version` | name/version 正确 |
| `test_fingerprint_rule_id_preserved` | Fingerprint/RuleID/Attributes/Entropy 保留 |
| `test_redacted_match_length_in_evidence` | redacted_match_length 记录长度 |
| `test_composite_with_disabled` | 禁用 via CompositeScanner |
| `test_composite_scanner_error_on_binary_missing` | binary missing via CompositeScanner |
| `test_composite_with_mocked_finding` | mock finding via CompositeScanner |
| `test_betterleaks_and_gitleaks_both_enabled` | 两者同时启用 |
| `test_risk_level_high_for_high_finding` | high finding → risk=high |
| `test_scanner_error_not_blocking_through_service` | scanner_error 不阻断 ScanService |
| `test_composite_finding_in_scan_service` | finding 进入 ScanService |
| `test_submit_review_not_blocked_by_high_finding` | high finding 不阻断 submit-review |

### Real CLI tests（4 tests，`skipif` 保护）

| 测试 | 说明 |
|------|------|
| `test_real_no_findings_on_clean_version` | clean version → no findings |
| `test_real_detects_secret_in_config` | fake stripe key detected |
| `test_real_via_composite_scanner` | via CompositeScanner |
| `test_real_evidence_redaction` | fake secret 原文不在 evidence 中 |

## 与 Gitleaks fallback 的关系

- **Betterleaks 为 primary**（`HUB_BETTERLEAKS_ENABLED=false` 默认禁用）
- **Gitleaks 为 fallback**（`HUB_GITLEAKS_ENABLED=false` 默认禁用）
- 两者同时启用时：Betterleaks 先执行，Gitleaks 后执行
- 本阶段不做 finding dedup（后续再做）
- 两者均可通过独立环境变量控制

## 边界

| 边界 | 说明 |
|------|------|
| license | MIT，但未法务确认 |
| Gitleaks fallback | 保留，不作为默认 |
| Semgrep | 不接 |
| OSV-Scanner | 不接 |
| TruffleHog | 不接（AGPL-3.0） |
| finding dedup | 不做（后续阶段） |
| DB 变更 | 无 |
| Python 依赖 | 无新增 |
| 前端变更 | 无 |
| Betterleaks 二进制 | 不内置 |

## 文件清单

| 文件 | 说明 |
|------|------|
| `backend/app/scanners/betterleaks_scanner.py` | BetterleaksScannerAdapter 实现 |
| `backend/tests/test_betterleaks_scanner.py` | 25 tests（21 mock + 4 real CLI） |
| `backend/app/core/config.py` | `betterleaks_enabled` / `betterleaks_bin` / `betterleaks_timeout_seconds` / `betterleaks_config` |
| `backend/app/services/scan_service.py` | `_build_externals()` 加入 Betterleaks |
