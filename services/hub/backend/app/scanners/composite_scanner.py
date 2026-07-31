from typing import Protocol, runtime_checkable

from app.core.enums import FindingSeverity
from app.models.hub_item_version import HubItemVersion
from app.scanners.finding_normalizer import FindingNormalizer


@runtime_checkable
class ExternalScanner(Protocol):
    """外部扫描器接口。

    所有外部扫描器（FakeExternalScanner、GitleaksAdapter 等）
    实现此接口。
    """

    name: str
    version: str

    def scan(self, version: HubItemVersion) -> list[dict]:
        """扫描一个版本，返回原始 findings 列表。

        返回 dict 格式：
          {rule_id, severity, location, message,
           evidence, confidence, recommendation}
        """
        ...


class CompositeScanner:
    """组合扫描器：内置规则 + 多个外部扫描器串行执行。"""

    def __init__(
        self,
        built_in=None,
        externals: list[ExternalScanner] | None = None,
    ):
        from app.adapters.scanner import RuleScannerAdapter

        self.built_in = built_in or RuleScannerAdapter()
        self.externals = externals or []
        self._normalizer = FindingNormalizer()

    @property
    def version(self) -> str:
        return self.built_in.version

    def _scanner_error_finding(self, scanner_name: str, error: str) -> dict:
        return {
            "risk_type": f"scanner_error:{scanner_name}",
            "severity": FindingSeverity.low,
            "file_path": scanner_name,
            "evidence": {
                "scanner_name": scanner_name,
                "error": str(error)[:500],
                "message": f"external scanner '{scanner_name}' failed: {str(error)[:200]}",
            },
            "recommendation": f"Check {scanner_name} installation or configuration",
        }

    def scan(self, version: HubItemVersion) -> list[dict]:
        findings: list[dict] = []

        findings.extend(self.built_in.scan(version))

        for ext in self.externals:
            try:
                raw = ext.scan(version)
                for r in raw:
                    normalized = self._normalizer.normalize(
                        r, scanner_name=ext.name, scanner_version=ext.version
                    )
                    findings.append(normalized)
            except Exception as e:
                findings.append(
                    self._scanner_error_finding(ext.name, str(e))
                )
                from app.core.event_log import log_event
                log_event(
                    "scanner.external_failed",
                    scanner_name=ext.name,
                    scanner_version=ext.version,
                    error_type=type(e).__name__,
                    result="warning",
                )

        return findings

    @property
    def scanner_names(self) -> list[str]:
        names = [self.built_in.__class__.__name__]
        for ext in self.externals:
            names.append(ext.name)
        return names
