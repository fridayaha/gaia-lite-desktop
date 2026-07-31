import pytest
from sqlalchemy.orm import Session

from app.core.enums import (
    FindingSeverity,
    HubItemStatus,
    HubItemType,
    RiskLevel,
)
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.scanners.composite_scanner import CompositeScanner
from app.scanners.secret_scanner import (
    SecretScannerConfig,
    is_cli_not_found,
    redacted_evidence,
)
from app.scanners.secret_scanner_mock import (
    MockSecretScannerAdapter,
    mock_secret_critical_finding,
    mock_secret_multiple_findings,
)


class TestRedactedEvidence:
    def test_strips_secret_and_match_fields(self):
        raw = {
            "RuleID": "generic-api-key",
            "Description": "found an API key",
            "File": "config.json",
            "StartLine": 10,
            "EndLine": 12,
            "Fingerprint": "abc123",
            "Entropy": 4.5,
            "Secret": "sk-this-is-a-real-secret-should-not-be-kept",
            "Match": "sk-this-is-a-real-secret-should-not-be-kept",
            "SecretHash": "sha256:deadbeef",
        }
        evidence = redacted_evidence(raw)
        assert "RuleID" in evidence
        assert evidence["RuleID"] == "generic-api-key"
        assert "Secret" not in evidence
        assert "Match" not in evidence
        assert "SecretHash" not in evidence

    def test_strips_lowercase_variants(self):
        raw = {
            "rule_id": "test-rule",
            "secret": "some-secret-value-should-be-redacted",
            "match": "some-secret-value-should-be-redacted",
            "file": "test.txt",
        }
        evidence = redacted_evidence(raw)
        assert evidence["rule_id"] == "test-rule"
        assert evidence["file"] == "test.txt"
        assert "secret" not in evidence
        assert "match" not in evidence

    def test_custom_strip_fields(self):
        raw = {
            "rule_id": "test",
            "Secret": "redact-me",
            "CustomField": "also-redact",
        }
        evidence = redacted_evidence(raw, strip_fields=("CustomField",))
        assert evidence["rule_id"] == "test"
        assert "Secret" not in evidence
        assert "CustomField" not in evidence

    def test_no_secret_fields_passes_through(self):
        raw = {
            "RuleID": "safe-rule",
            "Description": "no secrets here",
            "File": "safe.py",
            "Fingerprint": "ff0011",
        }
        evidence = redacted_evidence(raw)
        assert evidence["RuleID"] == "safe-rule"
        assert evidence["Description"] == "no secrets here"

    def test_entropy_preserved(self):
        raw = {
            "rule_id": "high-entropy",
            "Entropy": 5.2,
            "Secret": "must-not-appear",
        }
        evidence = redacted_evidence(raw)
        assert evidence["Entropy"] == 5.2
        assert "Secret" not in evidence


class TestCliNotFound:
    def test_no_such_file(self):
        assert is_cli_not_found("No such file or directory: gitleaks")

    def test_not_found(self):
        assert is_cli_not_found("gitleaks: not found")

    def test_command_not_found(self):
        assert is_cli_not_found("bash: betterleaks: command not found")

    def test_cannot_run_program(self):
        assert is_cli_not_found("java.io.IOException: Cannot run program")

    def test_normal_error_not_confused(self):
        assert not is_cli_not_found("timeout after 30s")
        assert not is_cli_not_found("exit code 1: 3 leaks found")


class TestSecretScannerConfig:
    def test_defaults(self):
        config = SecretScannerConfig()
        assert config.enabled is False
        assert config.bin_path is None
        assert config.timeout == 30
        assert config.redact is True

    def test_custom_values(self):
        config = SecretScannerConfig(
            enabled=True,
            bin_path="/usr/local/bin/betterleaks",
            timeout=60,
            redact=False,
        )
        assert config.enabled is True
        assert config.bin_path == "/usr/local/bin/betterleaks"
        assert config.timeout == 60
        assert config.redact is False


def _make_item_and_version(
    db_session: Session,
    name: str = "SecretScnTest",
    item_type: HubItemType = HubItemType.tool,
    config: dict | None = None,
) -> tuple[HubItem, HubItemVersion]:
    item = HubItem(
        name=name,
        type=item_type,
        status=HubItemStatus.draft,
        risk_level=RiskLevel.low,
    )
    db_session.add(item)
    db_session.flush()
    version = HubItemVersion(
        hub_item_id=item.id,
        version="1.0.0",
        config_json=config or {"name": "test-item"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission_json={"scope": ["internal"]},
        runtime_compatibility={"platform": "linux"},
    )
    db_session.add(version)
    db_session.flush()
    return item, version


class TestMockSecretScannerAdapter:
    def test_provider_name_and_version(self):
        adapter = MockSecretScannerAdapter()
        assert adapter.name == "mock-secret-scanner"
        assert adapter.version == "mock-0.1.0"

    def test_returns_finding_by_default(self):
        adapter = MockSecretScannerAdapter()
        dummy = HubItemVersion(version="1.0.0")
        findings = adapter.scan(dummy)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "mock:hardcoded-secret"
        assert findings[0]["severity"] == "high"

    def test_no_secret_raw_in_finding(self):
        adapter = MockSecretScannerAdapter()
        dummy = HubItemVersion(version="1.0.0")
        findings = adapter.scan(dummy)
        for f in findings:
            for v in f.values():
                if isinstance(v, str):
                    assert "sk-" not in v
                    assert "password" not in v.lower()

    def test_simulated_failure_raises(self):
        adapter = MockSecretScannerAdapter(should_fail=True, fail_error="boom")
        dummy = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="boom"):
            adapter.scan(dummy)

    def test_critical_finding(self):
        adapter = MockSecretScannerAdapter(findings=mock_secret_critical_finding())
        dummy = HubItemVersion(version="1.0.0")
        findings = adapter.scan(dummy)
        assert findings[0]["severity"] == "critical"
        assert findings[0]["rule_id"] == "mock:private-key"

    def test_multiple_findings(self):
        adapter = MockSecretScannerAdapter(findings=mock_secret_multiple_findings())
        dummy = HubItemVersion(version="1.0.0")
        findings = adapter.scan(dummy)
        assert len(findings) == 2
        severities = {f["severity"] for f in findings}
        assert "high" in severities
        assert "critical" in severities


class TestMockSecretScannerViaComposite:
    def test_mock_via_composite_finding_normalized(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter()
        scanner = CompositeScanner(externals=[mock])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"] == "ext:mock-secret-scanner:mock:hardcoded-secret"]
        assert len(ext_findings) == 1
        assert ext_findings[0]["severity"] == FindingSeverity.high
        assert ext_findings[0]["evidence"]["scanner_name"] == "mock-secret-scanner"

    def test_mock_failure_produces_scanner_error(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter(should_fail=True, fail_error="mock crash")
        scanner = CompositeScanner(externals=[mock])
        findings = scanner.scan(version)

        error_findings = [
            f for f in findings
            if f["risk_type"] == "scanner_error:mock-secret-scanner"
        ]
        assert len(error_findings) == 1
        assert error_findings[0]["severity"] == FindingSeverity.low
        assert "mock crash" in error_findings[0]["evidence"]["error"]

    def test_mock_critical_via_composite_risk(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter(findings=mock_secret_critical_finding())
        scanner = CompositeScanner(externals=[mock])
        findings = scanner.scan(version)

        severities = {f["severity"] for f in findings}
        assert FindingSeverity.critical in severities

    def test_mock_evidence_no_secret(self, db_session: Session):
        _, version = _make_item_and_version(db_session, name="NoLeak")
        mock = MockSecretScannerAdapter(
            findings=[
                {
                    "rule_id": "mock:no-leak-test",
                    "severity": "high",
                    "location": "config_json.api_key",
                    "message": "mock secret finding",
                }
            ]
        )
        scanner = CompositeScanner(externals=[mock])
        findings = scanner.scan(version)
        ext = [f for f in findings if f["risk_type"].startswith("ext:")]
        assert len(ext) == 1
        evidence_str = str(ext[0]["evidence"])
        assert "sk-" not in evidence_str
        assert "password" not in evidence_str.lower()

    def test_mock_externals_empty_does_not_produce_secret_findings(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        scanner = CompositeScanner(externals=[])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:mock-secret")]
        assert len(ext_findings) == 0


class TestMockSecretScannerAdapterWithScanService:
    def test_mock_high_finding_sets_risk(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter()
        scanner = CompositeScanner(externals=[mock])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high
        assert "mock-secret-scanner" in report.summary["scanners"]

        from app.models.scan_finding import ScanFinding as SF
        ext_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("ext:mock-secret-scanner"),
        ).all()
        assert len(ext_findings) == 1
        assert ext_findings[0].severity == FindingSeverity.high

    def test_mock_critical_finding_blocking(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter(findings=mock_secret_critical_finding())
        scanner = CompositeScanner(externals=[mock])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.blocking

    def test_mock_scanner_error_not_blocking_through_service(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = _make_item_and_version(db_session)
        mock = MockSecretScannerAdapter(should_fail=True)
        scanner = CompositeScanner(externals=[mock])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level != RiskLevel.blocking

        from app.models.scan_finding import ScanFinding as SF
        error_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("scanner_error:"),
        ).all()
        assert len(error_findings) >= 1
