import uuid

from sqlalchemy.orm import Session

from app.core.enums import HubItemVersionStatus, RiskLevel
from app.manifests import validate_manifest
from app.manifests.base import split_normalized_manifest
from app.manifests.errors import ManifestIssue, ManifestValidationError
from app.models.hub_item_version import HubItemVersion
from app.schemas.hub_item_version import HubItemVersionCreate
from app.services.exceptions import (
    DuplicateVersionError,
    HubItemNotFoundError,
    HubItemVersionNotFoundError,
)


class VersionService:
    def __init__(self, db: Session):
        self.db = db

    def _ensure_item_exists(self, item_id: uuid.UUID):
        from app.models.hub_item import HubItem

        item = self.db.get(HubItem, item_id)
        if item is None:
            raise HubItemNotFoundError(str(item_id))
        return item

    def create(
        self, item_id: uuid.UUID, data: HubItemVersionCreate
    ) -> HubItemVersion:
        item = self._ensure_item_exists(item_id)

        existing = (
            self.db.query(HubItemVersion)
            .filter(
                HubItemVersion.hub_item_id == item_id,
                HubItemVersion.version == data.version,
            )
            .first()
        )
        if existing is not None:
            raise DuplicateVersionError(str(item_id), data.version)

        raw_mj = data.manifest_json or {}

        conflict_errors: list[ManifestIssue] = []
        if raw_mj.get("type") and str(raw_mj["type"]).lower() != item.type.value:
            conflict_errors.append(
                ManifestIssue(
                    "type",
                    f"manifest type '{raw_mj['type']}' does not match item type '{item.type.value}'",
                    "error",
                )
            )
        if raw_mj.get("version") and str(raw_mj["version"]) != data.version:
            conflict_errors.append(
                ManifestIssue(
                    "version",
                    f"manifest version '{raw_mj['version']}' does not match version '{data.version}'",
                    "error",
                )
            )
        name_conflict: ManifestIssue | None = None
        if raw_mj.get("name") and str(raw_mj["name"]).strip() != item.name.strip():
            name_conflict = ManifestIssue(
                "name",
                f"manifest name '{raw_mj['name']}' differs from registered name '{item.name}'",
                "warning",
            )

        if conflict_errors:
            raise ManifestValidationError(conflict_errors)

        manifest = dict(raw_mj)
        manifest["name"] = item.name
        manifest["type"] = item.type.value
        manifest["version"] = data.version
        if data.description is not None:
            manifest["description"] = data.description
        if data.input_schema is not None:
            manifest["input_schema"] = data.input_schema
        if data.output_schema is not None:
            manifest["output_schema"] = data.output_schema
        if data.permission_json is not None:
            manifest["permission_json"] = data.permission_json
        if data.runtime_compatibility is not None:
            manifest["runtime_compatibility"] = data.runtime_compatibility
        if data.config_json is not None:
            manifest["config_json"] = data.config_json

        result = validate_manifest(manifest)

        if name_conflict is not None:
            result.warnings.append(name_conflict)

        if not result.valid:
            raise ManifestValidationError(result.errors)

        normalized = result.normalized_manifest
        normalized["name"] = item.name
        normalized["type"] = item.type.value
        normalized["version"] = data.version

        split = split_normalized_manifest(normalized)

        version = HubItemVersion(
            hub_item_id=item_id,
            version=data.version,
            description=data.description,
            manifest_json=split.get("manifest_json"),
            config_json=split.get("config_json"),
            input_schema=split.get("input_schema"),
            output_schema=split.get("output_schema"),
            permission_json=split.get("permission_json"),
            runtime_compatibility=split.get("runtime_compatibility"),
            status=HubItemVersionStatus.draft,
            risk_level=data.risk_level,
            package_hash=data.package_hash,
            change_log=data.change_log,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
            created_by=data.created_by,
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def list_by_item(self, item_id: uuid.UUID) -> list[HubItemVersion]:
        self._ensure_item_exists(item_id)
        return (
            self.db.query(HubItemVersion)
            .filter(HubItemVersion.hub_item_id == item_id)
            .order_by(HubItemVersion.created_at.desc())
            .all()
        )

    def get_by_id(
        self, item_id: uuid.UUID, version_id: uuid.UUID
    ) -> HubItemVersion:
        self._ensure_item_exists(item_id)
        version = (
            self.db.query(HubItemVersion)
            .filter(
                HubItemVersion.id == version_id,
                HubItemVersion.hub_item_id == item_id,
            )
            .first()
        )
        if version is None:
            raise HubItemVersionNotFoundError(str(version_id))
        return version
