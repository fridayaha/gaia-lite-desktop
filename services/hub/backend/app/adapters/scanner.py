from typing import Protocol, runtime_checkable

from app.models.hub_item_version import HubItemVersion
from app.scanners.rule_scanner import RuleScanner


@runtime_checkable
class ScannerAdapter(Protocol):
    """安全扫描器适配器接口。

    内置 RuleScanner 和后续外部扫描器（Semgrep / Gitleaks 等）
    统一实现此接口。
    """

    version: str

    def scan(self, version: HubItemVersion) -> list[dict]:
        """扫描一个版本，返回 findings 列表。

        每条 finding:
          {risk_type, severity, evidence: {field, matched, message}, recommendation}
        """
        ...


class RuleScannerAdapter:
    """封装内置 RuleScanner，使其符合 ScannerAdapter 接口。"""

    def __init__(self):
        self._inner = RuleScanner()
        self.version = self._inner.version

    def scan(self, version: HubItemVersion) -> list[dict]:
        return self._inner.scan(version)
