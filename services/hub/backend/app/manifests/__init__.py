from app.manifests.base import (
    ManifestValidationResult,
    normalize_common,
    SUPPORTED_MANIFEST_VERSIONS,
)
from app.manifests.errors import ManifestIssue
from app.manifests.validators import (
    validate_agent_manifest,
    validate_mcp_manifest,
    validate_skill_manifest,
    validate_tool_manifest,
)

_VALIDATOR_MAP = {
    "agent": validate_agent_manifest,
    "skill": validate_skill_manifest,
    "tool": validate_tool_manifest,
    "mcp": validate_mcp_manifest,
}


def validate_manifest(manifest: dict) -> ManifestValidationResult:
    normalized, norm_warnings = normalize_common(manifest)

    manifest_type = normalized.get("type", "")
    if manifest_type not in _VALIDATOR_MAP:
        return ManifestValidationResult(
            valid=False,
            errors=[
                ManifestIssue(
                    "type",
                    f"unknown type '{manifest_type}', must be one of: {sorted(_VALIDATOR_MAP.keys())}",
                    "error",
                )
            ],
            warnings=norm_warnings,
            normalized_manifest=normalized,
            asset_type=manifest_type,
        )

    result = _VALIDATOR_MAP[manifest_type](normalized)
    result.warnings = norm_warnings + result.warnings
    return result
