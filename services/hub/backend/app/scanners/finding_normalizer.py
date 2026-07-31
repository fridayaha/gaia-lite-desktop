from app.core.enums import FindingSeverity


_SEVERITY_MAP: dict[str, FindingSeverity] = {
    "critical": FindingSeverity.critical,
    "error": FindingSeverity.critical,
    "high": FindingSeverity.high,
    "medium": FindingSeverity.medium,
    "warning": FindingSeverity.medium,
    "low": FindingSeverity.low,
    "info": FindingSeverity.low,
    "note": FindingSeverity.low,
}


class FindingNormalizer:
    """将外部 scanner raw finding dict 归一化为统一内部格式。

    这不是完整 SARIF 解析器。只取最小公共字段。
    """

    def _map_severity(self, raw_severity: str | None) -> FindingSeverity:
        if raw_severity is None:
            return FindingSeverity.medium
        key = str(raw_severity).strip().lower()
        return _SEVERITY_MAP.get(key, FindingSeverity.medium)

    def normalize(
        self,
        raw: dict,
        scanner_name: str = "external",
        scanner_version: str = "unknown",
    ) -> dict:
        rule_id = raw.get("rule_id", raw.get("ruleId", "unknown"))
        risk_type = f"ext:{scanner_name}:{rule_id}"

        severity = self._map_severity(raw.get("severity"))

        location = raw.get("location", raw.get("file_path", raw.get("filePath")))
        file_path = location if isinstance(location, (str, type(None))) else str(location) if location else None

        evidence = {
            "scanner_name": scanner_name,
            "scanner_version": scanner_version,
            "rule_id": rule_id,
            "confidence": raw.get("confidence"),
            "external_ref": raw.get("external_ref", raw.get("externalRef")),
        }
        if location:
            evidence["location"] = location
        if raw.get("message"):
            evidence["message"] = raw["message"]
        if raw.get("evidence") and isinstance(raw["evidence"], dict):
            evidence.update(raw["evidence"])

        recommendation = raw.get(
            "remediation",
            raw.get("recommendation", f"Review external finding: {rule_id}"),
        )

        return {
            "risk_type": risk_type,
            "severity": severity,
            "file_path": file_path,
            "evidence": evidence,
            "recommendation": recommendation,
        }
