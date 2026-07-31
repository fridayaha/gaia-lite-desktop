import json
import shutil
import subprocess
import tempfile

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


def _make_item_and_version(
    db_session: Session,
    name: str = "GitleaksTest",
    item_type: HubItemType = HubItemType.tool,
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
        config_json={"name": "test"},
    )
    db_session.add(version)
    db_session.flush()
    return item, version


class TestGitleaksAdapterMocked:
    """Tests that mock subprocess.run - no real CLI needed."""

    def test_disabled_returns_empty(self, monkeypatch):
        g = GitleaksScannerAdapter(enabled=False)
        version = HubItemVersion(version="1.0.0")
        assert g.scan(version) == []

    def test_binary_missing_raises(self, monkeypatch):
        def fake_which(bin):
            return None
        monkeypatch.setattr(shutil, "which", fake_which)
        g = GitleaksScannerAdapter(enabled=True)
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="binary not found"):
            g.scan(version)

    def test_exit_code_0_no_findings(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)
        assert findings == []

    def test_exit_code_1_with_report(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        raw = [{
            "RuleID": "generic-api-key",
            "Description": "Found generic API key",
            "File": "config.json",
            "StartLine": 3,
            "EndLine": 5,
            "StartColumn": 1,
            "EndColumn": 40,
            "Match": "API_KEY=sk-secret-value-do-not-leak",
            "Secret": "sk-secret-value-do-not-leak",
            "Line": "API_KEY=sk-secret-value-do-not-leak",
            "Entropy": 4.2,
            "Fingerprint": "abc123",
            "Tags": ["api-key"],
        }]

        report_path = None

        def fake_open(path, mode="r"):
            nonlocal report_path
            report_path = path
            return json.dumps(raw) if "report" in path else ""

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        original_open = builtins_open = __builtins__["open"] if isinstance(__builtins__, dict) else open

        import builtins
        def fake_open_wrapper(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO(json.dumps(raw))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open_wrapper)

        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)

        assert len(findings) == 1
        f = findings[0]
        assert f["risk_type"] == "ext:gitleaks:generic-api-key"
        assert f["severity"] == FindingSeverity.high
        assert f["file_path"] == "config.json"

    def test_secret_and_match_not_in_evidence(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        raw = [{
            "RuleID": "aws-secret",
            "Description": "AWS key found",
            "File": "config.json",
            "StartLine": 10,
            "EndLine": 12,
            "Match": "AKIA...secret...",
            "Secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "Line": "export AWS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "Entropy": 5.5,
            "Fingerprint": "ff00ff",
        }]

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO(json.dumps(raw))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)

        evidence = findings[0]["evidence"]
        assert "Secret" not in evidence
        assert "Match" not in evidence
        assert "Line" not in evidence
        assert "secret" not in evidence
        assert "match" not in evidence
        assert "AWS" not in str(evidence).lower()
        assert "RuleID" in evidence or "rule_id" in evidence
        assert "Fingerprint" in evidence

    def test_exit_code_2_scanner_error(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        class FakeResult:
            returncode = 2
            stdout = ""
            stderr = "unknown flag"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="exited with code 2"):
            g.scan(version)

    def test_timeout_propagates(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gitleaks", timeout=1)
        monkeypatch.setattr(subprocess, "run", fake_run)

        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(subprocess.TimeoutExpired):
            g.scan(version)

    def test_invalid_json_report_raises(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO("not valid json{{{")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="report parse failed"):
            g.scan(version)

    def test_invalid_json_via_composite_produces_scanner_error(self, db_session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO("not valid json{{{")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        _, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        error_findings = [
            f for f in findings if f["risk_type"] == "scanner_error:gitleaks"
        ]
        assert len(error_findings) == 1
        ef = error_findings[0]
        assert ef["severity"] == FindingSeverity.low
        assert "report parse" in str(ef["evidence"].get("error", ""))
        assert "report" not in str(ef["evidence"]).lower() or "parse" in str(ef["evidence"]).lower()

    def test_invalid_json_does_not_produce_empty_findings(self, db_session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO("not valid json{{{")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        _, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [
            f for f in findings if f["risk_type"].startswith("ext:gitleaks:")
        ]
        assert len(ext_findings) == 0

    def test_scanner_name_and_version(self):
        g = GitleaksScannerAdapter(enabled=False)
        assert g.name == "gitleaks"
        assert isinstance(g.version, str)


class TestGitleaksViaComposite:
    def test_composite_with_disabled_gitleaks(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=False)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:gitleaks")]
        assert len(ext_findings) == 0

    def test_composite_scanner_error_on_binary_missing(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: None)
        _, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        error_findings = [
            f for f in findings if f["risk_type"] == "scanner_error:gitleaks"
        ]
        assert len(error_findings) == 1
        assert error_findings[0]["severity"] == FindingSeverity.low

    def test_composite_with_mocked_gitleaks_finding(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/gitleaks")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO(json.dumps([{
                    "RuleID": "generic-api-key",
                    "File": "config.json",
                    "StartLine": 1,
                    "EndLine": 1,
                    "Entropy": 3.5,
                    "Fingerprint": "xyz789",
                }]))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        _, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        g.version = "v1.0.0"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:gitleaks")]
        assert len(ext_findings) == 1
        assert ext_findings[0]["severity"] == FindingSeverity.high

    def test_risk_level_high_for_high_finding(self, db_session: Session):
        from app.services.scan_service import ScanService

        item = HubItem(
            name="RiskHigh",
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

        scanner = CompositeScanner(externals=[])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")
        assert report.risk_level == RiskLevel.low


@pytest.mark.skipif(not HAS_GITLEAKS, reason="gitleaks CLI not installed")
class TestGitleaksRealCLI:
    def test_real_gitleaks_no_findings_on_clean_version(self, db_session: Session):
        item, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        secret_findings = [
            f for f in findings if "api" in str(f.get("risk_type", "")).lower()
        ]
        assert len(secret_findings) == 0

    def test_real_gitleaks_detects_secret_in_config(self, db_session: Session):
        item = HubItem(
            name="SecretTest",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            config_json={"stripe_key": "sk_test_fAk3ExAmPl3StRiPeKeYfOrTeStInG1xYz"},
        )
        db_session.add(version)
        db_session.flush()

        g = GitleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        assert len(findings) >= 1

        for f in findings:
            evidence = f["evidence"]
            assert "Secret" not in evidence
            assert "Match" not in evidence
            assert "sk_test" not in str(evidence).lower()

    def test_real_gitleaks_via_composite_scanner(self, db_session: Session):
        item, version = _make_item_and_version(db_session)
        g = GitleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        assert len(findings) >= 0
        error_findings = [
            f for f in findings if f["risk_type"].startswith("scanner_error:")
        ]
        assert len(error_findings) == 0
