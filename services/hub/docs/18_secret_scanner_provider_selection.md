# Secret Scanner Provider 选型记录

版本：v0.4 | 日期：2026-05-29 | 状态：P2-2C Gitleaks fallback 已完成并验证；P2-2B BetterleaksScannerAdapter 已实现并验证（v1.3.1 真实 CLI 实测）。

---

## 1. 背景

P2-1 已完成 CompositeScanner 框架（`f377066`），具备多 scanner 串行执行 + finding 归一化能力。

P2-2 原计划接入 Gitleaks 作为第一个真实外部 scanner。但 Gitleaks 官方 README 已明确声明：

> "Gitleaks is feature complete. I'm not merging new features into Gitleaks. Future releases will be security patches only. I'm shifting my focus to Betterleaks."

同时 Betterleaks 由 Gitleaks 原作者 + 原团队维护，代表了团队当前主攻方向。因此需要重新评估 secret scanner provider 选型。

---

## 2. 候选工具

### Gitleaks

| 维度 | 状态 |
|------|------|
| 维护状态 | **Feature complete** — 只做安全补丁，不接新功能 |
| 生态成熟度 | ⭐⭐⭐ 极高（27.3k stars, 2.1k forks） |
| 最新版本 | v8.30.1（Mar 21, 2026） |
| CLI 变更 | v8.19+ `detect`/`protect` deprecated → 使用 `dir`/`git`/`stdin` |
| 命令示例 | `gitleaks dir /tmp --report-format=json --report-path=r.json` |
| JSON 输出 | `RuleID`, `Description`, `File`, `StartLine`, `Match`, `Secret`, `Fingerprint` 等 |
| Secret validation | ❌ 无 |
| False positive 控制 | allowlist（path/regex/stopwords）、entropy |
| License | MIT |
| CI 可用性 | 高（Docker image, brew, pre-commit hook） |
| 建议定位 | **兼容性 fallback** |

### Betterleaks

| 维度 | 状态 |
|------|------|
| 维护状态 | **活跃维护** — Gitleaks 原团队/原作者主攻方向 |
| 生态成熟度 | ⭐⭐ 较新（1k stars, 70 forks, 197 commits） |
| 最新版本 | v1.3.1（May 22, 2026） |
| CLI 命令 | `betterleaks dir`, `betterleaks git`, `betterleaks stdin`, `betterleaks s3`, `betterleaks github` |
| 命令示例 | `betterleaks dir /tmp -v -f json -r report.json`（需实测确认） |
| CEL filtering | ✅ 上下文过滤，更精准的 false positive 控制 |
| Secret validation | ✅ HTTP validation（异步），可配置 |
| Token efficiency filtering | ✅ BPE tokenization 过滤自然语言误报 |
| Sources | ✅ git / dir / stdin / GitHub / S3 / R2 |
| Exit codes | 0=no leaks, 1=leaks or error, 126=unknown flag |
| License | MIT |
| 建议定位 | **主要候选（primary candidate）** |

---

## 3. 对比总结

| 维度 | Gitleaks | Betterleaks |
|------|:---:|:---:|
| 维护状态 | feature complete ⚠️ | active ✅ |
| 成熟度 | 极高 | 较新（需实测） |
| 社区生态 | 大 | 小 |
| CEL filtering | — | ✅ |
| Secret validation | — | ✅ |
| Token efficiency | — | ✅ |
| License | MIT | MIT |
| 输出格式稳定性 | 稳定 | 需实测 |
| Hub 集成难度 | 低 | 低（同构 CLI） |
| 推荐定位 | **fallback** | **primary** |

---

## 4. 推荐方案

```
SecretScannerAdapter
  ├── BetterleaksScannerAdapter（推荐 P2-2 优先实现）
  └── GitleaksScannerAdapter（兼容性 fallback）
```

- P2-2B 先实现 BetterleaksScannerAdapter
- P2-2C 再实现 GitleaksScannerAdapter 作为 fallback
- 两者均可通过环境变量选择：`HUB_SECRET_SCANNER=betterleaks|gitleaks`

### 命令对照

| Provider | 现有命令（旧） | 推荐命令（新） |
|----------|--------------|--------------|
| Gitleaks v8.18- | `gitleaks detect --no-git --source=<dir>` | 不推荐 |
| Gitleaks v8.19+ | — | `gitleaks dir <dir> --report-format=json --report-path=<path>` |
| Betterleaks | — | `betterleaks dir <dir> -f json -r <path>`（需实测确认参数） |

---

## 5. CLI Spike 计划（P2-2A 设计确认后执行）

在进入代码实现前，必须先通过 CLI spike 验证：

| # | 测试项 | 说明 |
|:---:|------|------|
| 1 | betterleaks 安装 | `brew install betterleaks` 或 go install |
| 2 | `betterleaks dir` 是否可扫描临时 JSON | 写入含假 secret 的 JSON 文件，验证可扫描 |
| 3 | JSON report 输出格式 | 确认字段名（与 Gitleaks 是否兼容？） |
| 4 | exit code 行为 | 0/1/126 实际行为 |
| 5 | secret 原文是否出现在 report 中 | 如果包含 Secret/Match 原文 → 拦截层必须脱敏 |
| 6 | 是否支持 redaction | `--redact` flag? |
| 7 | 是否可禁用联网 validation | 默认是否需联网？离线环境行为？ |
| 8 | 与 Gitleaks 同一样例对比 | 同一临时文件，两边跑，对比 findings |

Spike 不做核心业务修改，只验证 CLI 可用性和输出格式。

---

## 6. 安全要求（不变）

不论最终选择 Betterleaks 还是 Gitleaks：

- 不记录 secret 原文到 ScanFinding.evidence
- 不记录 raw Match（如果包含 secret）
- 只保留 redacted/fingerprint/rule_id/file/line 到 evidence
- scanner failure → `scanner_error:provider_name` finding，不 500
- 默认 disabled（`HUB_SECRET_SCANNER_ENABLED=false`）
- 需显式配置启用
- license 未经法务确认前，不打包二进制

---

## 7. 推荐阶段拆分（已修订）

| 阶段 | 内容 | 状态 |
|:---:|------|:---:|
| P2-2A | Provider 选型文档（本文档）+ CLI spike | ✅ 已设计（spike 确认工具未安装） |
| P2-2B-lite | provider-neutral scaffold（SecretScannerProvider / Mock adapter / redaction） | ✅ 已完成（556 tests） |
| P2-2B | BetterleaksScannerAdapter（需先完成真实 CLI 实测） | ✅ 已实现并验证（633 tests，v1.3.1 真实 CLI 实测通过） |
| P2-2C | GitleaksScannerAdapter fallback（使用 `dir` 命令） | ✅ 已实现并验证（608 tests，3 real CLI tests + 3 integration tests，默认 `HUB_GITLEAKS_ENABLED=false`） |
| P2-2D | Provider 可配置切换（`HUB_SECRET_SCANNER` env var） | 🔲 |

### 7.1 P2-2B-lite 已完成内容（2026-05-28）

- `backend/app/scanners/secret_scanner.py`：SecretScannerProvider / SecretFindingParser Protocol、SecretScannerConfig dataclass、`redacted_evidence()` 脱敏辅助函数、`is_cli_not_found()` CLI 检测辅助函数
- `backend/app/scanners/secret_scanner_mock.py`：MockSecretScannerAdapter（实现 ExternalScanner 协议，配合 CompositeScanner 使用）
- `backend/tests/test_secret_scanner_provider.py`：26 tests（redaction、CLI not-found、Mock adapter 正常/失败/通过 CompositeScanner 归一化/通过 ScanService 集成）

### 7.2 Betterleaks adapter 阻塞条件

BetterleaksScannerAdapter 在以下条件满足**之前不应实现**：
- 本地安装 Betterleaks；
- 通过 `tools/spikes/secret_scanner_cli_spike.py` 实测 JSON report schema；
- 确认 `Secret` / `Match` 字段是否包含原文；
- 确认是否可关闭联网 validation；
- 确认 exit code 行为。


## 8. CLI Spike 结果

**Spike 日期**：2026-05-27（首次），2026-05-28（执行确认），2026-05-29（Betterleaks 真实 CLI 实测）  
**环境**：当前开发环境两者均已安装。  

### 8.1 安装状态

| 工具 | 是否安装 | 版本 |
|------|:---:|------|
| Betterleaks | ✅ | v1.3.1 (`/usr/local/bin/betterleaks`) |
| Gitleaks | ✅ | v8.30.1 (`/usr/local/bin/gitleaks`) |

### 8.2 推荐命令

| 工具 | 推荐命令 |
|------|----------|
| Gitleaks v8.19+ | `gitleaks dir <tmpdir> --report-format=json --report-path=<report.json> --redact --timeout=30` |
| Betterleaks | `betterleaks dir <tmpdir> -f json -r <report.json> --redact` |

### 8.3 JSON 输出格式对比

| 字段 | Gitleaks (v8.x) | Betterleaks (v1.x) |
|------|:---:|:---:|
| Rule ID | `RuleID` | `RuleID`（实测确认，与 Gitleaks 同名） |
| Description | `Description` | `Description` |
| File | `File` | `File` |
| StartLine | `StartLine` | `StartLine` |
| EndLine | `EndLine` | `EndLine` |
| Secret | `Secret` | `Secret`（包含原文） |
| Match | `Match` | `Match`（包含原文） |
| Entropy | `Entropy` | `Entropy` |
| Fingerprint | `Fingerprint` | `Fingerprint` |
| Tags | `Tags` | `Tags` |
| Attributes | — | `Attributes`（Betterleaks 独有） |
| Commit/Author/Email/Date/Message | `Commit`/`Author`/... | 同 Gitleaks（dir 模式下为 null） |
| Redaction | `--redact` flag | `--redact` flag（stdout only，不影响 report JSON） |

### 8.4 关键行为

| 项目 | Gitleaks | Betterleaks |
|------|----------|-------------|
| 是否输出 secret 原文 | ⚠️ 默认**包含** `Secret` 和 `Match` 字段。`--redact` 仅影响 stdout/logs，不影响 report file | ⚠️ 实测确认**包含** `Secret` 和 `Match`（字段名与 Gitleaks 一致），Adapter 必须强制剥离 |
| 是否支持 redaction | ✅ `--redact` flag（stdout/logs only） | ✅ `--redact` flag（stdout/logs only，不影响 report JSON） |
| exit code | 0=no leaks, 1=leaks or error, 126=unknown flag | 0=no leaks, 1=leaks or error, 126=unknown flag |
| 是否支持离线 | ✅ `dir` 命令纯本地 | ✅ `dir` 命令纯本地，`--validation` 为 opt-in |
| 是否默认联网 validation | ❌（纯本地扫描） | ❌ `--validation` 为 opt-in flag，默认不启用 |
| timeout | `--timeout` flag | `--timeout` global flag |
| 适合作为 primary provider | ⚠️（feature complete 风险） | ✅ 已验证（v1.3.1） |

### 8.5 关键发现

1. **两者 report JSON 均包含 Secret/Match 原文**：`--redact` 只影响 stdout/logs，不影响 report file。**Adapter 必须自行剥离这两个字段**。

2. **Betterleaks 与 Gitleaks JSON schema 几乎一致**：字段名均为 PascalCase（RuleID, Description, File, Secret, Match, Fingerprint 等）。Betterleaks 多了 `Attributes` 字段。

3. **Betterleaks 默认不联网**：`--validation` 为 opt-in flag，`dir` 命令纯本地扫描。

4. **两者 exit code 一致**：0/1/126 语义相同，Adapter 可以统一处理。

5. **Betterleaks `--timeout` 为 global flag**：不是 `dir` 子命令 flag，但可在命令中正常使用。

### 8.6 Spike 结论

- **Betterleaks** 已通过真实 CLI 实测（v1.3.1），JSON schema 与 Gitleaks 高度兼容；
- **BetterleaksScannerAdapter 已实现**（P2-2B），默认 `HUB_BETTERLEAKS_ENABLED=false`；
- **Gitleaks** 保留为 fallback，已知 Secret 原文泄露风险，Adapter 强制过滤；
- **P2-2B 已完成**：25 mock tests + 4 real CLI tests（`skipif` 保护）。

### 8.7 Betterleaks CLI 实测结果

**Spike 日期**：2026-05-29  
**版本**：v1.3.1  
**状态**：✅ 已实测

| 项目 | 结果 |
|------|------|
| 是否安装 | ✅ v1.3.1（`/usr/local/bin/betterleaks`） |
| 版本 | betterleaks version v1.3.1 |
| 推荐命令 | ✅ `betterleaks dir <tmpdir> -f json -r <report.json> --redact` |
| `dir` 命令可用性 | ✅ 支持 |
| JSON 字段 | `RuleID`, `Description`, `File`, `StartLine`, `EndLine`, `StartColumn`, `EndColumn`, `Match`, `Secret`, `Attributes`, `Tags`, `Fingerprint`, `Entropy` 等 |
| 是否输出 Secret/Match 原文 | ⚠️ **是** — `Secret` 和 `Match` 字段包含完整原文。`--redact` 不影响 report JSON |
| 是否支持 redaction | ✅ `--redact` flag（stdout only，不影响 report JSON） |
| 是否支持离线扫描 | ✅ `dir` 命令纯本地。`--validation` 为 opt-in |
| 是否默认联网 validation | ❌ 不默认联网，`--validation` 为 opt-in flag |
| exit code | ✅ 0=no leaks, 1=leaks found |
| 适合作为 primary provider | ✅ 已验证。Adapter 强制剥离 `Secret`/`Match`/`Line` |
| Adapter 已实现 | ✅ `BetterleaksScannerAdapter`（默认 `HUB_BETTERLEAKS_ENABLED=false`） |
| 真实 CLI 集成测试 | ✅ 4 tests（no findings / detect / redaction / via composite） |
| scan_service 接入 | ✅ Betterleaks → high finding → scan_report with betterleaks finding |
| submit-review | ✅ high finding 不 blocking，进入 pending_review |

**关键验证**：
- report JSON 中 `Secret` / `Match` 包含完整原文 → Adapter 强制剥离 → evidence 不含原文 ✅
- `sk_test_...` fake stripe key 不在 evidence 中 ✅
- `ghp_...` fake github token 不在 evidence 中 ✅
- real betterleaks finding 进入 `ScanFinding`，severity=high ✅
- `scanner_error:betterleaks` 不阻断 submit-review ✅
- Betterleaks + Gitleaks 同时启用两者都执行 ✅

### 8.8 BetterleaksScannerAdapter 已实现

P2-2B 已完成（2026-05-29）。文件：`backend/app/scanners/betterleaks_scanner.py`。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `HUB_BETTERLEAKS_ENABLED` | `false` | 默认禁用 |
| `HUB_BETTERLEAKS_BIN` | `betterleaks` | 二进制路径 |
| `HUB_BETTERLEAKS_TIMEOUT_SECONDS` | `30` | 超时秒数 |
| `HUB_BETTERLEAKS_CONFIG` | ``（空） | 可选自定义 config 文件 |

### 8.9 Gitleaks fallback CLI 实测结果（已实现，保留作为 fallback）
