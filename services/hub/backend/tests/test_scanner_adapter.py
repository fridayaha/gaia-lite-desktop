from app.adapters.scanner import RuleScannerAdapter, ScannerAdapter
from app.core.enums import FindingSeverity
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


class FakeScannerAdapter(ScannerAdapter):
    version = "fake-1.0"

    def scan(self, version: HubItemVersion) -> list[dict]:
        return [
            {
                "risk_type": "test:fake_issue",
                "severity": FindingSeverity.high,
                "evidence": {
                    "field": "manifest_json",
                    "matched": "test",
                    "message": "fake finding for testing",
                },
                "recommendation": "ignore, this is a test",
            }
        ]


class TestRuleScannerAdapter:
    def test_scanner_same_version(self):
        from app.scanners.rule_scanner import RuleScanner, SCANNER_VERSION

        adapter = RuleScannerAdapter()
        inner = RuleScanner()
        assert adapter.version == inner.version
        assert adapter.version == SCANNER_VERSION

    def test_scan_returns_findings(self, db_session):
        from app.core.enums import HubItemStatus, RiskLevel, HubItemType

        item = HubItem(
            name="AdapterTest",
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

        adapter = RuleScannerAdapter()
        findings = adapter.scan(version)
        assert len(findings) >= 1
        assert any(
            f["risk_type"] == "rm -rf" for f in findings
        )

    def test_scan_no_findings_on_clean_version(self, db_session):
        from app.core.enums import HubItemStatus, RiskLevel, HubItemType

        item = HubItem(
            name="CleanTest",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()

        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            manifest_json={"description": "a clean manifest"},
        )
        db_session.add(version)
        db_session.flush()

        adapter = RuleScannerAdapter()
        findings = adapter.scan(version)
        assert len(findings) == 0


class TestScanServiceWithAdapter:
    def test_default_scanner_is_composite(self, db_session):
        from app.services.scan_service import ScanService
        from app.scanners.composite_scanner import CompositeScanner

        svc = ScanService(db_session)
        assert isinstance(svc.scanner, CompositeScanner)
        from app.adapters.scanner import RuleScannerAdapter
        assert isinstance(svc.scanner.built_in, RuleScannerAdapter)

    def test_fake_adapter_injection(self, db_session):
        from app.services.scan_service import ScanService
        from app.scanners.composite_scanner import CompositeScanner
        from app.scanners.fake_external_scanner import FakeExternalScanner, fake_high_finding
        from app.core.enums import HubItemStatus, RiskLevel, HubItemType

        item = HubItem(
            name="FakeTest",
            type=HubItemType.tool,
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

        fake_ext = FakeExternalScanner(findings=fake_high_finding())
        scanner = CompositeScanner(externals=[fake_ext])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level.value in ("high", "blocking")
        assert report.summary["total_findings"] >= 1
        assert report.summary.get("scanners") is not None
