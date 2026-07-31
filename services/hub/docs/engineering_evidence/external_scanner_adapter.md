# 外部扫描器适配框架（P2-1）

## 1. 功能名称

CompositeScanner + ExternalFindingNormalizer + FakeExternalScanner

## 2. 对应能力方向

安全准入 / 工具适配

## 3. 解决的问题

此前安全扫描仅有一条内置规则扫描链路，无法接入外部扫描器（Gitleaks/Semgrep）。P2-1 实现组合扫描框架，打通多 scanner 串行执行、finding 归一化、错误容忍全链路。

## 4. 设计要点

- CompositeScanner 串行执行内置规则 + 0~N 外部 scanner
- 内置规则必跑，外部 scanner 可选
- 外部 scanner 失败不 500，生成 scanner_error finding
- FindingNormalizer 将外部 severity/format 归一到内部格式
- P2-1 不改 DB，scanner metadata 全部进入 evidence JSON

## 5. 关键实现

| 模块 | 说明 |
|------|------|
| `backend/app/scanners/composite_scanner.py` | `CompositeScanner` + `ExternalScanner` Protocol |
| `backend/app/scanners/finding_normalizer.py` | `FindingNormalizer` — severity mapping + evidence 聚合 |
| `backend/app/scanners/fake_external_scanner.py` | 测试用 scanner，可返回任意 findings / 模拟 failure |
| `backend/app/services/scan_service.py` | 默认 scanner 改为 `CompositeScanner()` |

## 6. 测试与结果

- 新增测试数：21（P2-1 时）
- 当前总基线：608 passed，0 failed
- 覆盖：归一化器 9 项 + CompositeScanner 5 项 + ScanService 集成 7 项

## 7. 日志 / 审计 / 安全边界

- 事件日志：`scanner.external_failed`（result=warning，不影响扫描）
- 不写独立 access log
- 内置扫描器失败仍为系统错误

## 8. 边界

本阶段已实现：
- GitleaksScannerAdapter fallback（P2-2C，详见 `docs/17_external_scanner_adapter_design.md`）

未实现：
- BetterleaksScannerAdapter（primary candidate，待实测 JSON schema）
- Semgrep CLI 接入（P2-3）
- 并行扫描
- scanner timeout（Gitleaks 例外：`--timeout` flag）
- license 合规确认

## 9. 一句话价值总结

打通 Hub 安全扫描的外部扫描器接入框架，内置规则 + N 外部 scanner 同链路协同，为真实 secret/SAST 扫描器接入奠定架构基础。
