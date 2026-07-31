import json
import os
import zipfile
from io import BytesIO

from fastapi import UploadFile

import yaml

from app.core.enums import HubItemStatus, RiskLevel, SourceType
from app.core.event_log import log_event
from app.core.tenancy import DEFAULT_ORGANIZATION_ID, DEFAULT_WORKSPACE_ID, DEFAULT_VISIBILITY_SCOPE, resolve_tenant_ids
from app.manifests import validate_manifest
from app.manifests.base import split_normalized_manifest
from app.manifests.errors import ManifestIssue, ManifestValidationError
from app.services.exceptions import (
    DuplicateVersionError,
    InvalidManifestError,
    UnsupportedFormatError,
    ZipSlipError,
)

MANIFEST_NAMES = {"manifest.json", "manifest.yaml", "manifest.yml"}
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_ZIP_FILES = 50
MAX_ZIP_SINGLE_SIZE = 5 * 1024 * 1024
MAX_ZIP_TOTAL_SIZE = 10 * 1024 * 1024
VALID_TYPES = {"agent", "mcp", "skill", "tool"}


class ImportService:
    def __init__(self, db):
        self.db = db

    def _check_zip_slip(self, name: str) -> str:
        if not name or name.endswith("/") or name.endswith("\\"):
            raise ZipSlipError(f"empty or directory entry: {name!r}")
        forbidden = ("../", "..\\", "/", "\\")
        for prefix in forbidden:
            if name.startswith(prefix):
                raise ZipSlipError(name)
        if len(name) >= 2 and name[1] == ":" and name[2:3] in ("\\", "/"):
            raise ZipSlipError(name)
        if ".." in name.replace("\\", "/").split("/"):
            raise ZipSlipError(name)
        normalized = os.path.normpath(name)
        if normalized.startswith(".."):
            raise ZipSlipError(name)
        if normalized.startswith("/"):
            raise ZipSlipError(name)
        return name

    def _parse_zip(self, content: bytes) -> dict:
        buf = BytesIO(content)
        manifest = None
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            if len(names) > MAX_ZIP_FILES:
                raise InvalidManifestError(
                    f"zip contains {len(names)} files, max {MAX_ZIP_FILES}"
                )
            manifest_candidates = [
                n for n in names
                if os.path.basename(n) in MANIFEST_NAMES
            ]
            if not manifest_candidates:
                raise InvalidManifestError(
                    "zip does not contain manifest.json/yaml/yml"
                )
            manifest_name = manifest_candidates[0]

            for info in zf.infolist():
                self._check_zip_slip(info.filename)
                if info.file_size > MAX_ZIP_SINGLE_SIZE:
                    raise InvalidManifestError(
                        f"file '{info.filename}' exceeds {MAX_ZIP_SINGLE_SIZE} bytes"
                    )
            total = sum(info.file_size for info in zf.infolist())
            if total > MAX_ZIP_TOTAL_SIZE:
                raise InvalidManifestError(
                    f"zip total size {total} exceeds {MAX_ZIP_TOTAL_SIZE}"
                )

            with zf.open(manifest_name) as f:
                raw = f.read().decode("utf-8")
                manifest = self._parse_content(raw, manifest_name)

        return manifest

    def _parse_content(self, raw: str, filename: str) -> dict:
        fn = filename.lower()
        if fn.endswith(".json"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise InvalidManifestError(f"invalid JSON: {e}") from e
        elif fn.endswith((".yaml", ".yml")):
            try:
                data = yaml.safe_load(raw)
                if not isinstance(data, dict):
                    raise InvalidManifestError("manifest must be a mapping")
                return data
            except yaml.YAMLError as e:
                raise InvalidManifestError(f"invalid YAML: {e}") from e
        else:
            raise UnsupportedFormatError(filename)

    def _validate(self, manifest: dict) -> dict:
        result = validate_manifest(manifest)
        if not result.valid:
            raise ManifestValidationError(result.errors)

        normalized = result.normalized_manifest
        split = split_normalized_manifest(normalized)

        info = {
            "name": normalized["name"],
            "type": normalized["type"],
            "description": normalized.get("description"),
            "industry": normalized.get("industry"),
            "scenario": normalized.get("scenario"),
            "version": normalized.get("version", "0.1.0"),
            "manifest_json": split.get("manifest_json"),
            "config_json": split.get("config_json"),
            "input_schema": split.get("input_schema"),
            "output_schema": split.get("output_schema"),
            "permission_json": split.get("permission_json"),
            "runtime_compatibility": split.get("runtime_compatibility"),
            "_warnings": [
                {"field": w.field, "message": w.message, "level": w.level}
                for w in result.warnings
            ],
        }
        return info

    def import_package(self, file: UploadFile, created_by: str | None = None,
                       organization_id: str | None = None,
                       workspace_id: str | None = None) -> dict:
        if not file.filename:
            raise InvalidManifestError("no file provided")

        content = file.file.read()
        if len(content) > MAX_FILE_SIZE:
            raise InvalidManifestError(
                f"file size {len(content)} exceeds {MAX_FILE_SIZE} bytes"
            )

        fn = file.filename.lower()
        if fn.endswith(".zip"):
            manifest = self._parse_zip(content)
        elif fn.endswith((".json", ".yaml", ".yml")):
            raw = content.decode("utf-8")
            manifest = self._parse_content(raw, file.filename)
        else:
            raise UnsupportedFormatError(file.filename)

        info = self._validate(manifest)

        from app.models.hub_item import HubItem
        from app.models.hub_item_version import HubItemVersion

        org_id, ws_id = resolve_tenant_ids(organization_id, workspace_id)

        existing_item = (
            self.db.query(HubItem)
            .filter(
                HubItem.name.ilike(info["name"]),
                HubItem.type == info["type"],
                HubItem.organization_id == org_id,
                HubItem.workspace_id == ws_id,
            )
            .first()
        )

        if existing_item is not None:
            item = existing_item
        else:
            item = HubItem(
                name=info["name"],
                type=info["type"],
                description=info.get("description"),
                industry=info.get("industry"),
                scenario=info.get("scenario"),
                source_type=SourceType.upload,
                status=HubItemStatus.draft,
                risk_level=RiskLevel.low,
                discoverable=True,
                allow_existing_references=True,
                force_disabled=False,
                organization_id=org_id,
                workspace_id=ws_id,
                visibility_scope=DEFAULT_VISIBILITY_SCOPE,
                created_by=created_by,
            )
            self.db.add(item)
            self.db.flush()

        version_str = str(info["version"])
        existing_version = (
            self.db.query(HubItemVersion)
            .filter(
                HubItemVersion.hub_item_id == item.id,
                HubItemVersion.version == version_str,
            )
            .first()
        )
        if existing_version is not None:
            raise DuplicateVersionError(str(item.id), version_str)

        version = HubItemVersion(
            hub_item_id=item.id,
            version=version_str,
            description=info.get("description"),
            manifest_json=info.get("manifest_json"),
            config_json=info.get("config_json"),
            input_schema=info.get("input_schema"),
            output_schema=info.get("output_schema"),
            permission_json=info.get("permission_json"),
            runtime_compatibility=info.get("runtime_compatibility"),
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
            created_by=created_by,
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(item)
        self.db.refresh(version)

        self._save_original_package(content, file.filename, item.id, version.id)

        return {
            "item_id": str(item.id),
            "version_id": str(version.id),
            "name": item.name,
            "type": item.type.value,
            "version": version.version,
            "status": version.status.value,
            "message": "imported successfully",
            "warnings": info.get("_warnings", []),
        }

    def _save_original_package(
        self,
        content: bytes,
        filename: str,
        item_id,
        version_id,
    ) -> None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        if ext not in ("zip", "json", "yaml", "yml"):
            ext = "bin"
        key = f"packages/{item_id}/{version_id}/original.{ext}"
        try:
            from app.core.storage import get_storage
            storage = get_storage()
            storage.put_bytes(key, content)
        except Exception as e:
            log_event(
                "storage.put_failed",
                storage_key=key,
                reason=str(e)[:200],
                operation="save_import_package",
            )
