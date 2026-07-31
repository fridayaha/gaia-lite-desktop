import json
import shutil
import subprocess
import tempfile

from app.core.config import settings
from app.core.event_log import log_event
from app.models.hub_item_version import HubItemVersion

GITLEAKS_BIN = "gitleaks"
GITLEAKS_TIMEOUT = 30

_STRIP_FIELDS = frozenset({
    "Secret", "secret",
    "Match", "match",
    "Line", "line",
    "Commit", "commit",
    "Author", "author",
    "Email", "email",
    "Date", "date",
    "Message", "message",
    "SymlinkFile", "symlinkFile",
})


class GitleaksScannerAdapter:
    """Gitleaks fallback secret scanner adapter.

    默认不启用（HUB_GITLEAKS_ENABLED=false）。
    使用 gitleaks dir 命令，不使用 deprecated detect。
    """

    name = "gitleaks"
    version = "unknown"

    def __init__(self, enabled: bool | None = None):
        self._enabled = (
            enabled if enabled is not None else settings.gitleaks_enabled
        )
        self._bin = settings.gitleaks_bin or GITLEAKS_BIN
        self._timeout = settings.gitleaks_timeout_seconds or GITLEAKS_TIMEOUT
        self._version_detected = False

    def _detect_version(self) -> str:
        try:
            result = subprocess.run(
                [self._bin, "version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def _ensure_version(self) -> None:
        if not self._version_detected:
            self.version = self._detect_version()
            self._version_detected = True

    def _write_version_files(self, version: HubItemVersion, tmpdir: str) -> None:
        fields = {
            "manifest.json": version.manifest_json,
            "config.json": version.config_json,
            "input_schema.json": version.input_schema,
            "output_schema.json": version.output_schema,
            "permission.json": version.permission_json,
            "runtime.json": version.runtime_compatibility,
        }
        for fname, data in fields.items():
            if data is not None:
                path = tempfile.mktemp(dir=tmpdir, prefix="", suffix=f".{fname}")
                with open(path, "w") as f:
                    json.dump(data, f)

    def _run_gitleaks(self, tmpdir: str) -> list[dict]:
        report_path = tempfile.mktemp(dir=tmpdir, suffix=".report.json")
        cmd = [
            self._bin, "dir", tmpdir,
            "--report-format=json",
            "--report-path", report_path,
            "--redact",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=self._timeout,
        )

        if result.returncode == 0:
            return []
        elif result.returncode == 1:
            try:
                with open(report_path) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                raise RuntimeError(
                    "gitleaks report parse failed: invalid JSON"
                )
            except OSError:
                raise RuntimeError(
                    "gitleaks report not found or unreadable"
                )
        else:
            raise RuntimeError(
                f"gitleaks exited with code {result.returncode}: "
                + (result.stderr[:200] if result.stderr else "unknown error")
            )

    def _normalize(self, raw: dict) -> dict:
        rule_id = raw.get("RuleID", raw.get("ruleId", "unknown"))
        risk_type = f"ext:gitleaks:{rule_id}"

        file_path = raw.get("File", raw.get("file"))

        safe_keys = {
            "RuleID", "ruleId",
            "Description", "description",
            "File", "file",
            "StartLine", "StartColumn",
            "EndLine", "EndColumn",
            "Fingerprint", "fingerprint",
            "Entropy", "entropy",
            "Tags", "tags",
        }
        evidence: dict[str, object] = {
            "scanner_name": self.name,
            "scanner_version": self.version,
        }
        for key in safe_keys:
            if key in raw and key not in _STRIP_FIELDS:
                evidence[key] = raw[key]

        return {
            "risk_type": risk_type,
            "severity": "high",
            "file_path": file_path,
            "evidence": evidence,
            "recommendation": (
                "Remove hardcoded secret and use managed secret storage."
            ),
        }

    def scan(self, version: HubItemVersion) -> list[dict]:
        if not self._enabled:
            return []

        self._ensure_version()

        if not shutil.which(self._bin):
            log_event(
                "scanner.external_failed",
                scanner_name=self.name,
                scanner_version=self.version,
                error_type="binary_not_found",
                result="warning",
            )
            raise RuntimeError(
                f"gitleaks binary not found: {self._bin}"
            )

        with tempfile.TemporaryDirectory(prefix="hub_gitleaks_") as tmpdir:
            self._write_version_files(version, tmpdir)
            raw_findings = self._run_gitleaks(tmpdir)

        normalized = [self._normalize(raw) for raw in raw_findings]
        return normalized
