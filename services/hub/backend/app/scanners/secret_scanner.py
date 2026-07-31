from dataclasses import dataclass, field
from typing import Protocol

from app.models.hub_item_version import HubItemVersion


class SecretFindingParser(Protocol):
    """Parses a raw finding dict from a secret scanner JSON report.

    Secret scanners (Betterleaks, Gitleaks) produce JSON reports
    containing findings that may include plaintext secrets.  This
    parser defines the **minimum fields** an adapter must extract
    without ever reading or forwarding `Secret` / `Match` fields.

    Every SecretScannerProvider implementation MUST use a parser
    that conforms to this protocol.
    """

    def parse(self, raw: dict) -> dict:
        """Parse a raw finding dict into a safe, redacted dict.

        Returns a dict with at minimum:
          - rule_id
          - file
          - start_line
          - end_line
          - fingerprint
          - entropy
          - description

        Must NEVER forward `Secret`, `Match`, or any field that
        could contain the original plaintext secret.
        """
        ...


class SecretScannerProvider(Protocol):
    """Provider-neutral secret scanner interface.

    All real CLI-based adapters (BetterleaksScannerAdapter,
    GitleaksScannerAdapter) implement this protocol as a common
    contract, on top of the `ExternalScanner` protocol from
    `composite_scanner.py`.

    The adapter writes version content to a temp directory,
    invokes the CLI via subprocess with a timeout, reads the JSON
    report, normalizes findings through `SecretFindingParser`,
    cleans up temp files, and never places secret plaintext into
    evidence.
    """

    name: str
    version: str

    def scan(self, version: HubItemVersion) -> list[dict]:
        ...


@dataclass
class SecretScannerConfig:
    """Provider-neutral configuration for a secret scanner adapter.

    All real adapters share the same shape: an optional binary
    path (falls back to PATH lookup) and a timeout in seconds.
    """

    enabled: bool = False
    bin_path: str | None = None
    timeout: int = 30
    redact: bool = True


def redacted_evidence(raw: dict, strip_fields: tuple[str, ...] | None = None) -> dict:
    """Build a safe evidence dict from a raw scanner finding.

    Strips `Secret`, `Match`, and any additional fields in
    `strip_fields`.  Only structural metadata is retained.

    This helper should be called by every SecretFindingParser
    implementation.
    """
    _default_strip = ("Secret", "Match", "secret", "match", "SecretHash")
    strip = set(_default_strip)
    if strip_fields:
        strip.update(strip_fields)

    evidence: dict[str, object] = {}
    for key in (
        "rule_id", "ruleId", "RuleID",
        "description", "Description",
        "file", "File",
        "start_line", "StartLine",
        "end_line", "EndLine",
        "fingerprint", "Fingerprint",
        "entropy", "Entropy",
        "redacted_match_length",
    ):
        if key in raw and key not in strip:
            evidence[key] = raw[key]

    return evidence


_CMD_NOT_FOUND_TEMPLATES = (
    "No such file or directory",
    "not found",
    "command not found",
    "Cannot run program",
)


def is_cli_not_found(error: str) -> bool:
    """Detect whether a subprocess error means the CLI binary is missing."""
    lower = error.lower()
    return any(t.lower() in lower for t in _CMD_NOT_FOUND_TEMPLATES)
