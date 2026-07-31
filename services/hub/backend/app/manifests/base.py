from dataclasses import dataclass, field

from app.manifests.errors import ManifestIssue

SUPPORTED_MANIFEST_VERSIONS = {"0.1"}

KNOWN_FIELDS = {
    "name",
    "type",
    "version",
    "manifest_version",
    "description",
    "input_schema",
    "output_schema",
    "permission_json",
    "runtime_compatibility",
    "config_json",
    "relations",
    "metadata",
    "extensions",
    "scenario",
    "dependencies",
    "instruction",
    "invocation",
    "transport",
    "mcp_server",
}


@dataclass
class ManifestValidationResult:
    valid: bool
    errors: list[ManifestIssue]
    warnings: list[ManifestIssue]
    normalized_manifest: dict
    asset_type: str


def normalize_common(manifest: dict) -> tuple[dict, list[ManifestIssue]]:
    warnings: list[ManifestIssue] = []
    normalized = dict(manifest)

    if isinstance(normalized.get("name"), str):
        normalized["name"] = normalized["name"].strip()

    if isinstance(normalized.get("type"), str):
        normalized["type"] = normalized["type"].lower()

    mv = normalized.get("manifest_version")
    mv_str = str(mv).strip() if mv and isinstance(mv, (str, int, float)) else ""
    if not mv_str:
        normalized["manifest_version"] = "0.1"
        warnings.append(
            ManifestIssue(
                "manifest_version",
                "missing, defaulting to '0.1'",
                "warning",
            )
        )
    else:
        normalized["manifest_version"] = mv_str

    ver = normalized.get("version")
    ver_str = str(ver).strip() if ver and isinstance(ver, (str, int, float)) else ""
    if not ver_str:
        normalized["version"] = "0.1.0"
        warnings.append(
            ManifestIssue(
                "version",
                "missing, defaulting to '0.1.0'",
                "warning",
            )
        )
    else:
        normalized["version"] = ver_str

    for key in list(manifest.keys()):
        if key not in KNOWN_FIELDS and not key.startswith("x_"):
            warnings.append(
                ManifestIssue(
                    key,
                    f"unknown field '{key}'",
                    "warning",
                )
            )

    return normalized, warnings


def split_normalized_manifest(normalized: dict) -> dict:
    column_keys = {
        "input_schema",
        "output_schema",
        "permission_json",
        "runtime_compatibility",
        "config_json",
    }
    result: dict = {}
    for key in column_keys:
        result[key] = normalized.get(key)

    manifest_json: dict = {}
    for key, val in normalized.items():
        if key not in column_keys and val is not None and key != "name" and key != "type" and key != "version" and key != "description":
            manifest_json[key] = val

    result["manifest_json"] = manifest_json if manifest_json else None
    return result
