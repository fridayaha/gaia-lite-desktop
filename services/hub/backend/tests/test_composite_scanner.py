import pytest
from sqlalchemy.orm import Session

from app.adapters.scanner import RuleScannerAdapter
from app.core.enums import (
    FindingSeverity,
    HubItemStatus,
    HubItemType,
    RiskLevel,
)
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.scanners.composite_scanner import CompositeScanner
from app.scanners.fake_external_scanner import (
    FakeExternalScanner,
    fake_critical_finding,
    fake_high_finding,
    fake_multiple_findings,
)
from app.scanners.finding_normalizer import FindingNormalizer


class TestFindingNormalizer:
    def test_map_severity_critical(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "test:1", "severity": "critical", "location": "x", "message": "msg"},
            scanner_name="test", scanner_version="1.0",
        )
        assert result["severity"] == FindingSeverity.critical
        assert result["risk_type"] == "ext:test:test:1"
        assert result["evidence"]["scanner_name"] == "test"
        assert result["evidence"]["scanner_version"] == "1.0"
        assert result["evidence"]["rule_id"] == "test:1"
        assert result["evidence"]["location"] == "x"

    def test_map_severity_high(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "test:2", "severity": "high"},
            scanner_name="gitleaks",
        )
        assert result["severity"] == FindingSeverity.high

    def test_map_severity_medium_warning(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "test:3", "severity": "warning"},
            scanner_name="semgrep",
        )
        assert result["severity"] == FindingSeverity.medium

    def test_map_severity_low_info_note(self):
        n = FindingNormalizer()
        for sev in ("low", "info", "note"):
            result = n.normalize(
                {"rule_id": "test:4", "severity": sev},
                scanner_name="fake",
            )
            assert result["severity"] == FindingSeverity.low

    def test_map_severity_unknown_defaults_to_medium(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "test:5", "severity": "unknown_level"},
            scanner_name="fake",
        )
        assert result["severity"] == FindingSeverity.medium

    def test_normalize_preserves_rule_id_in_risk_type(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "gitleaks:generic-api-key", "severity": "high"},
            scanner_name="gitleaks", scanner_version="8.18.0",
        )
        assert result["risk_type"] == "ext:gitleaks:gitleaks:generic-api-key"

    def test_normalize_uses_camelcase_fallback(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"ruleId": "camel-rule", "severity": "high", "filePath": "/tmp/test"},
            scanner_name="test",
        )
        assert result["risk_type"] == "ext:test:camel-rule"
        assert result["file_path"] == "/tmp/test"

    def test_normalize_fallback_recommendation(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "no-rec", "severity": "low"},
            scanner_name="test",
        )
        assert "Review external finding" in result["recommendation"]

    def test_normalize_uses_remediation(self):
        n = FindingNormalizer()
        result = n.normalize(
            {"rule_id": "test:rem", "severity": "high", "remediation": "Fix this"},
            scanner_name="test",
        )
        assert result["recommendation"] == "Fix this"


class TestCompositeScanner:
    def test_aggregates_builtin_and_fake_external(self, db_session: Session):
        item = HubItem(
            name="CompositeTest",
            type=HubItemType.mcp,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            config_json={"setup": "rm -rf /tmp"},
        )
        db_session.add(version)
        db_session.flush()

        fake = FakeExternalScanner(findings=fake_high_finding())
        scanner = CompositeScanner(externals=[fake])
        findings = scanner.scan(version)

        risk_types = {f["risk_type"] for f in findings}
        assert "rm -rf" in risk_types
        assert any(rt.startswith("ext:") for rt in risk_types)

    def test_externals_empty_keeps_builtin(self, db_session: Session):
        item = HubItem(
            name="EmptyExt",
            type=HubItemType.agent,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            config_json={"ignore previous instructions": ""},
        )
        db_session.add(version)
        db_session.flush()

        scanner = CompositeScanner(externals=[])
        findings = scanner.scan(version)
        assert len(findings) >= 1
        assert all(not f["risk_type"].startswith("ext:") for f in findings)

    def test_default_composite_uses_rule_scanner(self):
        scanner = CompositeScanner()
        assert isinstance(scanner.built_in, RuleScannerAdapter)
        assert scanner.externals == []

    def test_scanner_names(self):
        fake = FakeExternalScanner()
        scanner = CompositeScanner(externals=[fake])
        names = scanner.scanner_names
        assert "RuleScannerAdapter" in names
        assert "fake-external" in names

    def test_external_scanner_error_produces_scanner_error_finding(self, db_session: Session):
        item = HubItem(
            name="ErrorTest",
            type=HubItemType.agent,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
        )
        db_session.add(version)
        db_session.flush()

        fake = FakeExternalScanner(should_fail=True, fail_error="boom")
        scanner = CompositeScanner(externals=[fake])
        findings = scanner.scan(version)

        error_findings = [f for f in findings if f["risk_type"] == "scanner_error:fake-external"]
        assert len(error_findings) == 1
        assert error_findings[0]["severity"] == FindingSeverity.low
        assert error_findings[0]["evidence"]["scanner_name"] == "fake-external"
        assert "boom" in error_findings[0]["evidence"]["error"]


class TestScanServiceWithComposite:
    def _make_item(self, db_session: Session, item_type=HubItemType.tool) -> tuple[HubItem, HubItemVersion]:
        item = HubItem(
            name="SvcComposite",
            type=item_type,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_json={"scope": ["internal"]},
            runtime_compatibility={"platform": "linux"},
        )
        db_session.add(version)
        db_session.flush()
        return item, version

    def test_default_service_uses_composite_scanner(self, db_session: Session):
        from app.services.scan_service import ScanService

        svc = ScanService(db_session)
        assert isinstance(svc.scanner, CompositeScanner)

    def test_externals_empty_behavior_unchanged(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        scanner = CompositeScanner(externals=[])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.low
        assert isinstance(report.summary, dict)
        assert report.summary.get("scanners") == ["RuleScannerAdapter"]

    def test_fake_external_high_finding(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        fake = FakeExternalScanner(findings=fake_high_finding())
        scanner = CompositeScanner(externals=[fake])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high
        assert report.summary["scanners"] == ["RuleScannerAdapter", "fake-external"]

    def test_fake_external_critical_blocking(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        fake = FakeExternalScanner(findings=fake_critical_finding())
        scanner = CompositeScanner(externals=[fake])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.blocking
        findings = svc.db.query(
            scan_finding_model
        ).filter(
            scan_finding_model.scan_report_id == report.id
        ).all()
        ext_findings = [f for f in findings if f.risk_type.startswith("ext:")]
        assert len(ext_findings) == 1
        assert ext_findings[0].evidence["scanner_name"] == "fake-external"

    def test_external_scanner_error_not_blocking(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        fake = FakeExternalScanner(should_fail=True, fail_error="simulated crash")
        scanner = CompositeScanner(externals=[fake])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level != RiskLevel.blocking
        assert report.summary["scanners"] == ["RuleScannerAdapter", "fake-external"]

    def test_scanner_error_finding_persisted(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        fake = FakeExternalScanner(should_fail=True)
        scanner = CompositeScanner(externals=[fake])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        findings = svc.db.query(
            scan_finding_model
        ).filter(
            scan_finding_model.scan_report_id == report.id
        ).all()
        error_findings = [f for f in findings if f.risk_type.startswith("scanner_error:")]
        assert len(error_findings) >= 1

    def test_fake_external_critical_blocking_submit_via_scan(self, db_session: Session):
        from app.services.scan_service import ScanService

        _, version = self._make_item(db_session)
        fake = FakeExternalScanner(findings=fake_critical_finding())
        scanner = CompositeScanner(externals=[fake])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.blocking
        assert report.summary["total_findings"] >= 1

        version_refreshed = db_session.get(HubItemVersion, version.id)
        assert version_refreshed.risk_level == RiskLevel.blocking


from app.models.scan_finding import ScanFinding as scan_finding_model
