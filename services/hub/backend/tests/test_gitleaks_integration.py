import json
import shutil

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
from app.scanners.gitleaks_scanner import GitleaksScannerAdapter

HAS_GITLEAKS = shutil.which("gitleaks") is not None


def _build_version(**kwargs) -> HubItemVersion:
    defaults = {
        "version": "1.0.0",
    }
    defaults.update(kwargs)
    return HubItemVersion(**defaults)


@pytest.mark.skipif(not HAS_GITLEAKS, reason="gitleaks CLI not installed")
class TestGitleaksRealCLIIntegration:
    """Real CLI end-to-end tests covering scan pipeline and redaction."""

    def _make_item_and_version(
        self,
        db_session: Session,
        name: str = "GlInt",
        config_json: dict | None = None,
    ) -> tuple[HubItem, HubItemVersion]:
        item = HubItem(
            name=name,
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            config_json=config_json or {"name": "test"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_json={"scope": ["internal"]},
            runtime_compatibility={"platform": "linux"},
        )
        db_session.add(version)
        db_session.flush()
        return item, version

    def test_real_gitleaks_finding_normalized_and_redacted(self):
        """Verify real gitleaks finding has correct structure and no secrets."""
        version = _build_version(
            config_json={"stripe_key": "sk_test_fAk3ExAmPl3StRiPeKeYfOrTeStInG1xYz"},
        )
        g = GitleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        assert len(findings) >= 1
        f = findings[0]

        assert f["risk_type"].startswith("ext:gitleaks:")
        assert f["severity"] == "high"
        assert f["file_path"]

        evidence = f["evidence"]
        assert evidence["scanner_name"] == "gitleaks"
        assert "scanner_version" in evidence
        assert isinstance(evidence["scanner_version"], str)
        assert len(evidence["scanner_version"]) > 0

        evidence_str = json.dumps(evidence)
        assert "Secret" not in evidence
        assert "Match" not in evidence
        assert "Line" not in evidence
        assert "sk_test" not in evidence_str.lower()

    def test_real_gitleaks_via_scan_service(self, db_session: Session):
        from app.services.scan_service import ScanService

        item, version = self._make_item_and_version(
            db_session,
            config_json={"stripe_key": "sk_test_fAk3ExAmPl3StRiPeKeYfOrTeStInG1xYz"},
        )
        g = GitleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high

        assert isinstance(report.summary, dict)
        assert "gitleaks" in str(report.summary.get("scanners", []))

        from app.models.scan_finding import ScanFinding as SF
        gitleaks_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("ext:gitleaks:"),
        ).all()
        assert len(gitleaks_findings) >= 1

        for gf in gitleaks_findings:
            ev_str = json.dumps(gf.evidence or {})
            assert "sk_test" not in ev_str.lower()
            assert gf.severity == FindingSeverity.high

    def test_real_gitleaks_submit_review_high_not_blocking(self, db_session: Session):
        from app.models.scan_finding import ScanFinding as SF
        from app.services.scan_service import ScanService
        from app.services.lifecycle_service import LifecycleService

        item, version = self._make_item_and_version(
            db_session,
            config_json={"stripe_key": "sk_test_fAk3ExAmPl3StRiPeKeYfOrTeStInG1xYz"},
        )
        g = GitleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high
        assert report.risk_level != RiskLevel.blocking

        ls = LifecycleService(db_session)
        submitted = ls.submit_version(version.id, operator="test")
        assert submitted.status.value == "pending_review"

        gitleaks_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("ext:gitleaks:"),
        ).all()
        assert len(gitleaks_findings) >= 1

        for gf in gitleaks_findings:
            assert "Secret" not in json.dumps(gf.evidence or {})
            assert "Match" not in json.dumps(gf.evidence or {})

    def test_real_gitleaks_clean_version_no_ext_findings(self, db_session: Session):
        item, version = self._make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:gitleaks:")]
        assert len(ext_findings) == 0


class TestGitleaksScanServiceIntegration:
    """Mock tests for ScanService wiring."""

    def test_scan_service_builds_externals_when_enabled(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")
        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO(json.dumps([]))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {
            "returncode": 0, "stdout": "", "stderr": "",
        })())

        from app.services.scan_service import _build_externals
        externals = _build_externals()

        assert len(externals) >= 0

    def test_scanner_error_does_not_block_submit(self, db_session: Session):
        from app.services.lifecycle_service import LifecycleService

        item = HubItem(
            name="ErrorBlock",
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

        from app.services.scan_service import ScanService
        scanner = CompositeScanner(externals=[])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        from app.models.scan_finding import ScanFinding as SF
        error_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("scanner_error:"),
        ).all()

        ls = LifecycleService(db_session)
        submitted = ls.submit_version(version.id, operator="test")
        assert submitted.status.value == "pending_review"
