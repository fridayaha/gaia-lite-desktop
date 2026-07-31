from app.core.enums import FindingSeverity
from app.models.hub_item_version import HubItemVersion
from app.scanners.secret_scanner import SecretFindingParser, SecretScannerConfig
from app.scanners.secret_scanner import redacted_evidence as _redacted_evidence


class NoOpSecretFindingParser:
    """Parsing pass-through for mock adapter.

    Real adapters have their own parsers that strip secrets.
    The mock adapter keeps the raw finding as-is since it contains
    no real secrets.
    """

    def parse(self, raw: dict) -> dict:
        return dict(raw)


class MockSecretScannerAdapter:
    """Provider-neutral mock secret scanner for testing.

    Implements the same contract that BetterleaksScannerAdapter
    and GitleaksScannerAdapter will follow so that tests can be
    written before real CLI adapters exist.

    This adapter:
      - never contains or emits secret plaintext
      - supports fake findings (default: one high-severity finding)
      - supports simulated failure
      - can be wired into CompositeScanner(externals=[...])
    """

    name = "mock-secret-scanner"
    version = "mock-0.1.0"

    def __init__(
        self,
        findings: list[dict] | None = None,
        should_fail: bool = False,
        fail_error: str = "simulated secret scanner failure",
        config: SecretScannerConfig | None = None,
    ):
        self._findings = findings or [_default_secret_finding()]
        self._should_fail = should_fail
        self._fail_error = fail_error
        self.config = config or SecretScannerConfig(enabled=True)

    def scan(self, version: HubItemVersion) -> list[dict]:
        if self._should_fail:
            raise RuntimeError(self._fail_error)
        return list(self._findings)


def _default_secret_finding() -> dict:
    return {
        "rule_id": "mock:hardcoded-secret",
        "severity": "high",
        "location": "config_json.api_key",
        "message": "mock secret finding for testing",
        "confidence": 0.9,
        "recommendation": "Replace hardcoded secret with environment variable",
    }


def mock_secret_critical_finding() -> list[dict]:
    return [
        {
            "rule_id": "mock:private-key",
            "severity": "critical",
            "location": "manifest_json.private_key",
            "message": "mock private key detected",
            "confidence": 0.95,
            "remediation": "Remove embedded private key",
        }
    ]


def mock_secret_multiple_findings() -> list[dict]:
    return [
        {
            "rule_id": "mock:api-key",
            "severity": "high",
            "location": "config_json.secret",
            "message": "generic API key found",
            "confidence": 0.88,
        },
        {
            "rule_id": "mock:cloud-key",
            "severity": "critical",
            "location": "config_json.aws_key",
            "message": "cloud credential detected",
            "confidence": 0.92,
            "remediation": "Use IAM role instead",
        },
    ]
