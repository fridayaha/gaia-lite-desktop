import uuid

from sqlalchemy.orm import Session

from app.adapters.scanner import RuleScannerAdapter, ScannerAdapter
from app.core.enums import EventType, FindingSeverity, HubItemType, RiskLevel
from app.core.event_log import log_event
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent
from app.models.scan_finding import ScanFinding
from app.models.scan_report import ScanReport
from app.scanners.composite_scanner import CompositeScanner
from app.services.exceptions import HubItemVersionNotFoundError

SEVERITY_TO_RISK = {
    FindingSeverity.low: RiskLevel.low,
    FindingSeverity.medium: RiskLevel.medium,
    FindingSeverity.high: RiskLevel.high,
    FindingSeverity.critical: RiskLevel.blocking,
}

_VALID_MCP_TRANSPORTS = {"stdio", "sse", "streamable_http"}


def _build_externals():
    externals = []
    try:
        from app.scanners.betterleaks_scanner import BetterleaksScannerAdapter
        b = BetterleaksScannerAdapter()
        if b._enabled:
            externals.append(b)
    except Exception:
        pass
    try:
        from app.scanners.gitleaks_scanner import GitleaksScannerAdapter
        g = GitleaksScannerAdapter()
        if g._enabled:
            externals.append(g)
    except Exception:
        pass
    return externals


class ScanService:
    def __init__(self, db: Session, scanner: CompositeScanner | None = None):
        self.db = db
        if scanner is not None:
            self.scanner = scanner
        else:
            externals = _build_externals()
            self.scanner = CompositeScanner(externals=externals)

    def _get_version(self, version_id: uuid.UUID) -> HubItemVersion:
        version = self.db.get(HubItemVersion, version_id)
        if version is None:
            raise HubItemVersionNotFoundError(str(version_id))
        return version

    def _compute_risk_level(self, findings: list[dict]) -> RiskLevel:
        if not findings:
            return RiskLevel.low
        severities = [
            SEVERITY_TO_RISK[f["severity"]]
            for f in findings
            if f["severity"] in SEVERITY_TO_RISK
        ]
        if not severities:
            return RiskLevel.low
        priority = [RiskLevel.blocking, RiskLevel.high, RiskLevel.medium, RiskLevel.low]
        for level in priority:
            if level in severities:
                return level
        return RiskLevel.low

    def _make_finding(
        self,
        risk_type: str,
        severity: FindingSeverity,
        field: str,
        message: str,
        recommendation: str,
    ) -> dict:
        return {
            "risk_type": risk_type,
            "severity": severity,
            "evidence": {
                "field": field,
                "matched": risk_type,
                "message": message,
            },
            "recommendation": recommendation,
        }

    def _scan_by_type(
        self, version: HubItemVersion, item_type: HubItemType
    ) -> list[dict]:
        findings: list[dict] = []
        mk = self._make_finding

        if item_type in (HubItemType.skill, HubItemType.tool):
            if not version.input_schema:
                findings.append(mk(
                    "contract:missing_input_schema",
                    FindingSeverity.medium,
                    "input_schema",
                    "input_schema is missing",
                    "Define input_schema as a JSON Schema object describing expected inputs",
                ))
            if not version.output_schema:
                findings.append(mk(
                    "contract:missing_output_schema",
                    FindingSeverity.low,
                    "output_schema",
                    "output_schema is missing",
                    "Define output_schema as a JSON Schema object describing expected outputs",
                ))
            if item_type == HubItemType.skill and not getattr(
                version, "manifest_json", {}
            ).get("instruction"):
                findings.append(mk(
                    "contract:missing_instruction",
                    FindingSeverity.medium,
                    "manifest_json.instruction",
                    "skill instruction is missing",
                    "Add instruction field describing the skill's task",
                ))

        if item_type in (HubItemType.tool, HubItemType.mcp):
            if not version.permission_json:
                findings.append(mk(
                    "contract:missing_permission_json",
                    FindingSeverity.medium,
                    "permission_json",
                    "permission_json is missing",
                    "Declare permission boundaries for this capability",
                ))
            if not version.runtime_compatibility:
                findings.append(mk(
                    "contract:missing_runtime_compatibility",
                    FindingSeverity.low,
                    "runtime_compatibility",
                    "runtime_compatibility is missing",
                    "Declare platform and runtime requirements",
                ))

        if item_type == HubItemType.tool:
            manifest = version.manifest_json or {}
            invocation = manifest.get("invocation", {})
            endpoint = invocation.get("endpoint", "")
            if isinstance(endpoint, str) and endpoint.startswith("http://"):
                findings.append(mk(
                    "tool:insecure_endpoint",
                    FindingSeverity.medium,
                    "manifest_json.invocation.endpoint",
                    "Tool endpoint uses http instead of https",
                    "Use https for tool invocation endpoint",
                ))
            perm = version.permission_json or {}
            if perm.get("external_url") and not perm.get("allowed_domains"):
                findings.append(mk(
                    "tool:external_url_no_domains",
                    FindingSeverity.medium,
                    "permission_json.external_url",
                    "external_url enabled without allowed_domains restriction",
                    "Add allowed_domains to restrict external URL access",
                ))

        if item_type == HubItemType.mcp:
            manifest = version.manifest_json or {}
            mcp_server = manifest.get("mcp_server", {})
            transport = manifest.get("transport", "")
            if transport and transport not in _VALID_MCP_TRANSPORTS:
                findings.append(mk(
                    "mcp:invalid_transport",
                    FindingSeverity.critical,
                    "manifest_json.transport",
                    f"invalid MCP transport: {transport}",
                    "Use one of: stdio, sse, streamable_http",
                ))
            command = mcp_server.get("command", "")
            if isinstance(command, str):
                cmd_lower = command.lower()
                for dangerous in ("rm -rf", "curl | sh", "wget | bash"):
                    if dangerous in cmd_lower:
                        findings.append(mk(
                            "mcp:dangerous_command",
                            FindingSeverity.critical,
                            "manifest_json.mcp_server.command",
                            f"dangerous command pattern in MCP server command: {dangerous}",
                            "Remove dangerous shell command from MCP server config",
                        ))
                        break
            env_vars = mcp_server.get("env", {})
            if isinstance(env_vars, dict):
                for key in env_vars:
                    key_upper = key.upper()
                    if any(p in key_upper for p in ("API_KEY", "SECRET", "TOKEN", "PASSWORD")):
                        findings.append(mk(
                            "mcp:hardcoded_credential",
                            FindingSeverity.critical,
                            f"manifest_json.mcp_server.env.{key}",
                            f"hardcoded credential key in MCP env: {key}",
                            "Remove hardcoded credentials from MCP env config",
                        ))
                        break

        if item_type == HubItemType.agent:
            manifest = version.manifest_json or {}
            if not manifest.get("dependencies"):
                findings.append(mk(
                    "contract:missing_dependencies",
                    FindingSeverity.low,
                    "manifest_json.dependencies",
                    "agent has no declared dependencies",
                    "Declare the capabilities this agent depends on",
                ))

        return findings

    def scan_version(
        self,
        version_id: uuid.UUID,
        operator: str | None = None,
    ) -> ScanReport:
        version = self._get_version(version_id)

        item = self.db.get(HubItem, version.hub_item_id)
        item_type = item.type.value if item else None

        log_event(
            "scan.started",
            version_id=str(version_id),
            item_id=str(version.hub_item_id),
            item_type=item_type,
        )

        raw_findings = list(self.scanner.scan(version))

        item = self.db.get(HubItem, version.hub_item_id)
        if item is not None:
            type_findings = self._scan_by_type(version, item.type)
            raw_findings.extend(type_findings)

        risk_level = self._compute_risk_level(raw_findings)

        severity_counts: dict[str, int] = {}
        risk_types: list[str] = []
        for f in raw_findings:
            sev = f["severity"].value
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            risk_types.append(f["risk_type"])

        report = ScanReport(
            hub_item_id=version.hub_item_id,
            hub_item_version_id=version.id,
            risk_level=risk_level,
            summary={
                "total_findings": len(raw_findings),
                "severity_counts": severity_counts,
                "risk_types": list(set(risk_types)),
                "scanners": self.scanner.scanner_names,
            },
            scanner_version=self.scanner.version,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )
        self.db.add(report)
        self.db.flush()

        for f in raw_findings:
            finding = ScanFinding(
                scan_report_id=report.id,
                risk_type=f["risk_type"],
                severity=f["severity"],
                file_path=f["evidence"].get("field"),
                evidence=f["evidence"],
                recommendation=f["recommendation"],
            )
            self.db.add(finding)

        version.risk_level = risk_level

        item = self.db.get(HubItem, version.hub_item_id)
        if item is not None and item.current_version_id == version.id:
            item.risk_level = risk_level

        event = LifecycleEvent(
            hub_item_id=version.hub_item_id,
            hub_item_version_id=version.id,
            event_type=EventType.scanned,
            from_status=None,
            to_status=None,
            operator=operator or "scanner",
            reason=f"Scan completed: {risk_level.value}",
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )
        self.db.add(event)

        self.db.commit()
        self.db.refresh(report)

        total_findings = len(raw_findings)
        blocking_count = severity_counts.get(FindingSeverity.critical.value, 0)

        log_event(
            "scan.completed",
            version_id=str(version_id),
            item_id=str(version.hub_item_id),
            item_type=item_type,
            risk_level=risk_level.value,
            total_findings=total_findings,
            blocking_count=blocking_count,
            scanner_version=self.scanner.version,
        )

        if risk_level == RiskLevel.blocking:
            log_event(
                "scan.blocked",
                version_id=str(version_id),
                item_id=str(version.hub_item_id),
                item_type=item_type,
                risk_level=risk_level.value,
                total_findings=total_findings,
                blocking_count=blocking_count,
            )

        return report

    def get_latest_report(self, version_id: uuid.UUID) -> ScanReport | None:
        self._get_version(version_id)
        return (
            self.db.query(ScanReport)
            .filter(ScanReport.hub_item_version_id == version_id)
            .order_by(ScanReport.created_at.desc())
            .first()
        )
