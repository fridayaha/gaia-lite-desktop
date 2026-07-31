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
from app.scanners.betterleaks_scanner import BetterleaksScannerAdapter
from app.scanners.composite_scanner import CompositeScanner


HAS_BETTERLEAKS = shutil.which("betterleaks") is not None


def _make_item_and_version(
    db_session: Session,
    name: str = "BetterleaksTest",
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


class TestBetterleaksAdapterMocked:
    """Tests that mock subprocess.run - no real CLI needed."""

    def test_disabled_returns_empty(self, monkeypatch):
        g = BetterleaksScannerAdapter(enabled=False)
        version = HubItemVersion(version="1.0.0")
        assert g.scan(version) == []

    def test_env_disabled_by_default(self, monkeypatch):
        monkeypatch.setenv("HUB_BETTERLEAKS_ENABLED", "false")
        g = BetterleaksScannerAdapter()
        assert g._enabled is False

    def test_binary_missing_raises(self, monkeypatch):
        def fake_which(bin):
            return None
        monkeypatch.setattr(shutil, "which", fake_which)
        g = BetterleaksScannerAdapter(enabled=True)
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="binary not found"):
            g.scan(version)

    def test_exit_code_0_no_findings(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)
        assert findings == []

    def test_exit_code_1_with_report(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)

        assert len(findings) == 1
        f = findings[0]
        assert f["risk_type"] == "ext:betterleaks:generic-api-key"
        assert f["severity"] == FindingSeverity.high
        assert f["file_path"] == "config.json"

    def test_secret_and_match_not_in_evidence(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
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
        assert "wJalrXUtn" not in str(evidence)
        assert "RuleID" in evidence or "rule_id" in evidence
        assert "Fingerprint" in evidence

    def test_exit_code_2_scanner_error(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        class FakeResult:
            returncode = 2
            stdout = ""
            stderr = "unknown flag"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="exited with code 2"):
            g.scan(version)

    def test_timeout_propagates(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="betterleaks", timeout=1)
        monkeypatch.setattr(subprocess, "run", fake_run)

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(subprocess.TimeoutExpired):
            g.scan(version)

    def test_invalid_json_report_raises(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        with pytest.raises(RuntimeError, match="report parse failed"):
            g.scan(version)

    def test_invalid_json_via_composite_produces_scanner_error(self, db_session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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
        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        error_findings = [
            f for f in findings if f["risk_type"] == "scanner_error:betterleaks"
        ]
        assert len(error_findings) == 1
        ef = error_findings[0]
        assert ef["severity"] == FindingSeverity.low
        assert "report parse" in str(ef["evidence"].get("error", ""))

    def test_scanner_name_and_version(self):
        g = BetterleaksScannerAdapter(enabled=False)
        assert g.name == "betterleaks"
        assert isinstance(g.version, str)

    def test_fingerprint_rule_id_preserved(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        raw = [{
            "RuleID": "github-token",
            "Description": "GitHub token found",
            "File": "env.json",
            "StartLine": 4,
            "EndLine": 4,
            "Fingerprint": "deadbeef12345",
            "Entropy": 3.1,
            "Attributes": ["val_new", "val_http"],
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

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)

        evidence = findings[0]["evidence"]
        assert evidence["RuleID"] == "github-token"
        assert evidence["Fingerprint"] == "deadbeef12345"
        assert evidence["Attributes"] == ["val_new", "val_http"]
        assert evidence["scanner_name"] == "betterleaks"
        assert evidence["scanner_version"] == "betterleaks version v1.3.1"
        assert evidence["Entropy"] == 3.1

    def test_redacted_match_length_in_evidence(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        match = "sk-this-is-a-fake-secret-key-for-testing-purposes"
        raw = [{
            "RuleID": "generic-api-key",
            "Description": "Generic API key",
            "File": "creds.json",
            "StartLine": 1,
            "EndLine": 1,
            "Match": match,
            "Fingerprint": "abc123",
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

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        version = HubItemVersion(version="1.0.0")
        findings = g.scan(version)

        evidence = findings[0]["evidence"]
        assert "Match" not in evidence
        assert "match" not in evidence
        assert match not in str(evidence)
        assert evidence["redacted_match_length"] == len(match)


class TestBetterleaksViaComposite:
    def test_composite_with_disabled_betterleaks(self, db_session: Session):
        _, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=False)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:betterleaks")]
        assert len(ext_findings) == 0

    def test_composite_scanner_error_on_binary_missing(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: None)
        _, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        error_findings = [
            f for f in findings if f["risk_type"] == "scanner_error:betterleaks"
        ]
        assert len(error_findings) == 1
        assert error_findings[0]["severity"] == FindingSeverity.low

    def test_composite_with_mocked_betterleaks_finding(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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
        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        ext_findings = [f for f in findings if f["risk_type"].startswith("ext:betterleaks")]
        assert len(ext_findings) == 1
        assert ext_findings[0]["severity"] == FindingSeverity.high

    def test_betterleaks_and_gitleaks_both_enabled(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: f"/usr/bin/{x}")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                if "betterleaks" in str(path):
                    return io.StringIO(json.dumps([{
                        "RuleID": "generic-api-key",
                        "File": "config.json",
                        "Fingerprint": "bl001",
                    }]))
                return io.StringIO(json.dumps([{
                    "RuleID": "aws-key",
                    "File": "env.json",
                    "Fingerprint": "gl001",
                }]))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        from app.scanners.gitleaks_scanner import GitleaksScannerAdapter
        _, version = _make_item_and_version(db_session)
        bl = BetterleaksScannerAdapter(enabled=True)
        bl.version = "betterleaks version v1.3.1"
        bl._version_detected = True
        gl = GitleaksScannerAdapter(enabled=True)
        gl.version = "v8.30.1"
        gl._version_detected = True
        scanner = CompositeScanner(externals=[bl, gl])
        findings = scanner.scan(version)

        bl_findings = [f for f in findings if f["risk_type"].startswith("ext:betterleaks")]
        gl_findings = [f for f in findings if f["risk_type"].startswith("ext:gitleaks")]
        assert len(bl_findings) == 1
        assert len(gl_findings) == 1
        assert bl_findings[0]["severity"] == FindingSeverity.high
        assert gl_findings[0]["severity"] == FindingSeverity.high

    def test_risk_level_high_for_high_finding(self, db_session: Session):
        from app.services.scan_service import ScanService

        item = HubItem(
            name="RiskHighBL",
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

    def test_scanner_error_not_blocking_through_service(self, db_session: Session, monkeypatch):
        from app.services.scan_service import ScanService

        monkeypatch.setattr(shutil, "which", lambda x: None)
        _, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level != RiskLevel.blocking
        from app.models.scan_finding import ScanFinding as SF
        error_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("scanner_error:"),
        ).all()
        assert len(error_findings) >= 1

    def test_composite_betterleaks_finding_in_scan_service(self, db_session: Session, monkeypatch):
        from app.services.scan_service import ScanService

        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

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
                    "Fingerprint": "svc001",
                }]))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        _, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high
        assert "betterleaks" in report.summary["scanners"]

        from app.models.scan_finding import ScanFinding as SF
        ext_findings = db_session.query(SF).filter(
            SF.scan_report_id == report.id,
            SF.risk_type.startswith("ext:betterleaks"),
        ).all()
        assert len(ext_findings) == 1
        assert ext_findings[0].severity == FindingSeverity.high

    def test_submit_review_not_blocked_by_high_finding(self, db_session: Session, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/betterleaks")

        import builtins
        original_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "report.json" in str(path):
                import io
                return io.StringIO(json.dumps([{
                    "RuleID": "generic-api-key",
                    "File": "config.json",
                    "Fingerprint": "submit001",
                }]))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", fake_open)

        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

        _, version = _make_item_and_version(db_session)
        from app.services.scan_service import ScanService
        from app.services.lifecycle_service import LifecycleService
        from app.core.auth_context import AuthContext

        g = BetterleaksScannerAdapter(enabled=True)
        g.version = "betterleaks version v1.3.1"
        g._version_detected = True
        scanner = CompositeScanner(externals=[g])
        svc = ScanService(db_session, scanner=scanner)
        report = svc.scan_version(version.id, operator="test")

        assert report.risk_level == RiskLevel.high

        lc_svc = LifecycleService(db_session)
        lc_svc.submit_item(
            item_id=version.hub_item_id,
            operator="test",
            ctx=AuthContext(),
        )

        db_session.refresh(version)
        item = db_session.get(HubItem, version.hub_item_id)
        assert item.status == HubItemStatus.pending_review


@pytest.mark.skipif(not HAS_BETTERLEAKS, reason="betterleaks CLI not installed")
class TestBetterleaksRealCLI:
    def test_real_betterleaks_no_findings_on_clean_version(self, db_session: Session):
        item, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        secret_findings = [
            f for f in findings if "api" in str(f.get("risk_type", "")).lower()
        ]
        assert len(secret_findings) == 0

    def test_real_betterleaks_detects_secret_in_config(self, db_session: Session):
        item = HubItem(
            name="SecretTestBL",
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

        g = BetterleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        assert len(findings) >= 1

        for f in findings:
            evidence = f["evidence"]
            assert "Secret" not in evidence
            assert "Match" not in evidence
            assert "sk_test" not in str(evidence).lower()

    def test_real_betterleaks_via_composite_scanner(self, db_session: Session):
        item, version = _make_item_and_version(db_session)
        g = BetterleaksScannerAdapter(enabled=True)
        scanner = CompositeScanner(externals=[g])
        findings = scanner.scan(version)

        assert len(findings) >= 0
        error_findings = [
            f for f in findings if f["risk_type"].startswith("scanner_error:")
        ]
        assert len(error_findings) == 0

    def test_real_betterleaks_evidence_redaction(self, db_session: Session):
        item = HubItem(
            name="RedactTestBL",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            config_json={
                "github_token": "ghp_fAk3tOkEn1234567890AbCdEfGhIjKlMnOpQrSt",
                "aws_secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
        )
        db_session.add(version)
        db_session.flush()

        g = BetterleaksScannerAdapter(enabled=True)
        findings = g.scan(version)

        assert len(findings) >= 1

        for f in findings:
            ev_str = str(f["evidence"])
            assert "ghp_" not in ev_str.lower()
            assert "wJalrXUtn" not in ev_str
            assert "EXAMPLEKEY" not in ev_str
            assert "Secret" not in ev_str
            assert "Match" not in ev_str
