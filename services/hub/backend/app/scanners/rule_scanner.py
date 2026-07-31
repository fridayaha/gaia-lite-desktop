from app.core.enums import FindingSeverity


SCANNER_VERSION = "0.2.0"

FINDING_SEVERITY = {
    "low": FindingSeverity.low,
    "medium": FindingSeverity.medium,
    "high": FindingSeverity.high,
    "critical": FindingSeverity.critical,
}

PROMPT_RULES = [
    ("ignore previous instructions", FindingSeverity.high),
    ("忽略以上规则", FindingSeverity.high),
    ("system prompt", FindingSeverity.high),
    ("developer message", FindingSeverity.high),
    ("jailbreak", FindingSeverity.high),
    ("reveal hidden prompt", FindingSeverity.high),
    ("泄露系统提示词", FindingSeverity.high),
    ("无条件执行", FindingSeverity.high),
    ("你必须", FindingSeverity.high),
    ("不要理会上面的", FindingSeverity.high),
    ("你现在的角色是", FindingSeverity.high),
    ("you are now a", FindingSeverity.high),
    ("扮演", FindingSeverity.high),
]

INDIRECT_PROMPT_RULES = [
    ("ignore previous instructions", FindingSeverity.medium),
    ("忽略以上规则", FindingSeverity.medium),
    ("你必须", FindingSeverity.medium),
    ("无条件执行", FindingSeverity.medium),
]

TOOL_RULES = [
    ("always call this tool", FindingSeverity.high),
    ("must call this tool", FindingSeverity.high),
    ("ignore user intent", FindingSeverity.high),
    ("bypass policy", FindingSeverity.high),
]

SECRET_RULES = [
    ("API_KEY=", FindingSeverity.critical),
    ("SECRET=", FindingSeverity.critical),
    ("TOKEN=", FindingSeverity.critical),
    ("password=", FindingSeverity.high),
]

COMMAND_RULES = [
    ("rm -rf", FindingSeverity.critical),
    ("curl | sh", FindingSeverity.critical),
    ("wget | bash", FindingSeverity.critical),
    ("/etc/passwd", FindingSeverity.critical),
    ("~/.ssh", FindingSeverity.medium),
    (".env", FindingSeverity.medium),
]

PERMISSION_KEYS = {
    "shell_exec": FindingSeverity.medium,
    "network": FindingSeverity.medium,
    "file_write": FindingSeverity.medium,
    "database": FindingSeverity.medium,
    "external_url": FindingSeverity.medium,
}

SECRET_KEY_PATTERNS = {
    "api_key": FindingSeverity.critical,
    "secret": FindingSeverity.critical,
    "token": FindingSeverity.critical,
    "password": FindingSeverity.high,
}


class RuleScanner:
    def __init__(self):
        self.version = SCANNER_VERSION

    def _str_matches(self, text: str, rules: list[tuple[str, FindingSeverity]]) -> list[dict]:
        findings = []
        lower = text.lower()
        for pattern, severity in rules:
            if pattern.lower() in lower:
                findings.append({
                    "risk_type": pattern,
                    "severity": severity,
                    "evidence": {
                        "matched": pattern,
                        "message": f"string match: {pattern}",
                    },
                    "recommendation": f"Review and remove '{pattern}' reference",
                })
        return findings

    def _json_recursive_check(
        self,
        data: dict,
        path: str = "",
        keys: dict[str, FindingSeverity] | None = None,
    ) -> list[dict]:
        if keys is None:
            keys = PERMISSION_KEYS
        findings = []
        if not isinstance(data, dict):
            return findings
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if key in keys:
                passes = value is True or (
                    isinstance(value, str) and value.lower() == "true"
                )
                if key == "external_url":
                    passes = bool(value)
                if passes:
                    findings.append({
                        "risk_type": f"permission:{key}",
                        "severity": keys[key],
                        "evidence": {
                            "field": current_path,
                            "value": str(value),
                            "message": f"permission '{key}' is enabled",
                        },
                        "recommendation": f"Review permission '{key}' setting",
                    })
            if isinstance(value, dict):
                findings.extend(
                    self._json_recursive_check(value, current_path, keys)
                )
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        findings.extend(
                            self._json_recursive_check(
                                item, f"{current_path}[{idx}]", keys
                            )
                        )
        return findings

    def _check_secret_keys(
        self,
        data: dict | list,
        path: str = "",
    ) -> list[dict]:
        findings = []
        if isinstance(data, dict):
            for key, value in data.items():
                current_path = f"{path}.{key}" if path else key
                key_lower = key.lower()
                for pattern, severity in SECRET_KEY_PATTERNS.items():
                    if pattern in key_lower and value:
                        findings.append({
                            "risk_type": f"secret:{pattern}",
                            "severity": severity,
                            "evidence": {
                                "field": current_path,
                                "matched": str(value)[:50],
                                "message": f"secret key '{key}' detected",
                            },
                            "recommendation": f"Remove hardcoded secret in '{key}'",
                        })
                if isinstance(value, (dict, list)):
                    findings.extend(
                        self._check_secret_keys(value, current_path)
                    )
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    findings.extend(
                        self._check_secret_keys(item, f"{path}[{idx}]")
                    )
        return findings

    def _scan_field(
        self,
        content: dict | None,
        field_name: str,
    ) -> list[dict]:
        findings = []
        if content is None:
            return findings
        text = str(content).lower()

        findings.extend(self._str_matches(text, PROMPT_RULES))
        findings.extend(self._str_matches(text, TOOL_RULES))
        findings.extend(self._str_matches(text, COMMAND_RULES))
        findings.extend(self._str_matches(text, SECRET_RULES))
        findings.extend(self._check_secret_keys(content))
        findings.extend(self._json_recursive_check(content))

        for f in findings:
            f["evidence"]["field"] = field_name

        return findings

    def scan(self, version) -> list[dict]:
        findings = []
        for field_name in (
            "manifest_json",
            "config_json",
            "input_schema",
            "output_schema",
        ):
            content = getattr(version, field_name, None)
            if content:
                findings.extend(self._scan_field(content, field_name))

        permission = getattr(version, "permission_json", None)
        if permission:
            findings.extend(
                self._json_recursive_check(permission, "permission_json")
            )
            findings.extend(
                self._check_secret_keys(permission, "permission_json")
            )
            text = str(permission).lower()
            findings.extend(self._str_matches(text, COMMAND_RULES))
            findings.extend(self._str_matches(text, SECRET_RULES))

        runtime = getattr(version, "runtime_compatibility", None)
        if runtime:
            findings.extend(self._scan_field(runtime, "runtime_compatibility"))

        description = getattr(version, "description", None)
        if description:
            findings.extend(
                self._str_matches(str(description), INDIRECT_PROMPT_RULES)
            )
            for f in findings[-len(INDIRECT_PROMPT_RULES):]:
                f.setdefault("evidence", {})["field"] = "description"

        return findings
