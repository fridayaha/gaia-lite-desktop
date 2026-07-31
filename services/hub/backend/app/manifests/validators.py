from app.manifests.base import (
    SUPPORTED_MANIFEST_VERSIONS,
    ManifestValidationResult,
)
from app.manifests.errors import ManifestIssue


def _validate_common(
    normalized: dict, manifest_type: str
) -> ManifestValidationResult:
    errors: list[ManifestIssue] = []
    warnings: list[ManifestIssue] = []

    name = normalized.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append(
            ManifestIssue(
                "name",
                "required and must be a non-empty string",
                "error",
            )
        )

    mv = normalized.get("manifest_version", "")
    if mv and mv not in SUPPORTED_MANIFEST_VERSIONS:
        errors.append(
            ManifestIssue(
                "manifest_version",
                f"unsupported version '{mv}', supported: {sorted(SUPPORTED_MANIFEST_VERSIONS)}",
                "error",
            )
        )

    ver = normalized.get("version")
    if not ver or not isinstance(ver, str) or not ver.strip():
        errors.append(
            ManifestIssue(
                "version",
                "required and must be a non-empty string",
                "error",
            )
        )

    t = normalized.get("type", "")
    if t != manifest_type:
        errors.append(
            ManifestIssue(
                "type",
                f"expected '{manifest_type}', got '{t}'",
                "error",
            )
        )

    if normalized.get("permission_json") is None:
        warnings.append(
            ManifestIssue(
                "permission_json",
                "missing",
                "warning",
            )
        )

    return ManifestValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        normalized_manifest=normalized,
        asset_type=manifest_type,
    )


def validate_agent_manifest(normalized: dict) -> ManifestValidationResult:
    result = _validate_common(normalized, "agent")
    return result


def validate_skill_manifest(normalized: dict) -> ManifestValidationResult:
    result = _validate_common(normalized, "skill")

    if normalized.get("input_schema") is None:
        result.warnings.append(
            ManifestIssue("input_schema", "missing", "warning")
        )
    if normalized.get("output_schema") is None:
        result.warnings.append(
            ManifestIssue("output_schema", "missing", "warning")
        )
    if normalized.get("instruction") is None:
        result.warnings.append(
            ManifestIssue("instruction", "missing", "warning")
        )

    return result


def validate_tool_manifest(normalized: dict) -> ManifestValidationResult:
    result = _validate_common(normalized, "tool")

    if normalized.get("input_schema") is None:
        result.warnings.append(
            ManifestIssue("input_schema", "missing", "warning")
        )
    if normalized.get("output_schema") is None:
        result.warnings.append(
            ManifestIssue("output_schema", "missing", "warning")
        )
    if normalized.get("invocation") is None:
        result.warnings.append(
            ManifestIssue("invocation", "missing", "warning")
        )

    return result


def validate_mcp_manifest(normalized: dict) -> ManifestValidationResult:
    result = _validate_common(normalized, "mcp")

    transport = normalized.get("transport")
    if transport and transport not in ("stdio", "sse", "streamable_http"):
        result.errors.append(
            ManifestIssue(
                "transport",
                f"invalid transport '{transport}', must be one of: stdio, sse, streamable_http",
                "error",
            )
        )
        result.valid = False

    if normalized.get("mcp_server") is None:
        result.warnings.append(
            ManifestIssue("mcp_server", "missing", "warning")
        )

    return result
