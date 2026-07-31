from app.core.enums import FindingSeverity
from app.models.hub_item_version import HubItemVersion


class FakeExternalScanner:
    """仅用于测试/验证的外部扫描器实现。

    不作为生产默认启用。
    """

    name = "fake-external"
    version = "fake-0.1"

    def __init__(
        self,
        findings: list[dict] | None = None,
        should_fail: bool = False,
        fail_error: str = "simulated scanner failure",
    ):
        self._findings = findings or [
            {
                "rule_id": "test:fake_issue",
                "severity": "high",
                "location": "manifest_json.fake_field",
                "message": "fake external finding for testing",
                "confidence": 0.85,
                "recommendation": "ignore, this is a test finding",
            }
        ]
        self._should_fail = should_fail
        self._fail_error = fail_error

    def scan(self, version: HubItemVersion) -> list[dict]:
        if self._should_fail:
            raise RuntimeError(self._fail_error)
        return list(self._findings)


def fake_critical_finding() -> list[dict]:
    return [
        {
            "rule_id": "test:fake_critical",
            "severity": "critical",
            "location": "config_json.secret_key",
            "message": "fake critical external finding",
            "confidence": 0.95,
            "remediation": "Remove the hardcoded secret",
        }
    ]


def fake_high_finding() -> list[dict]:
    return [
        {
            "rule_id": "test:fake_high",
            "severity": "high",
            "location": "manifest_json.description",
            "message": "fake high-severity external finding",
            "confidence": 0.8,
        }
    ]


def fake_multiple_findings() -> list[dict]:
    return [
        {
            "rule_id": "test:fake_critical",
            "severity": "critical",
            "location": "config_json.secret",
            "message": "hardcoded secret detected",
        },
        {
            "rule_id": "test:fake_medium",
            "severity": "medium",
            "location": "manifest_json.prompt",
            "message": "potential prompt injection",
        },
    ]
