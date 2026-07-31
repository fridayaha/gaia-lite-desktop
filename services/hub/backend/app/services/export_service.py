import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.core.event_log import log_event
from app.models.hub_item import HubItem
from app.models.hub_item_relation import HubItemRelation
from app.models.hub_item_version import HubItemVersion


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    return safe.strip("_") or "export"


class ExportService:
    def __init__(self, db: Session):
        self.db = db

    def build_version_package(
        self, item_id: uuid.UUID, version_id: uuid.UUID
    ) -> tuple[io.BytesIO, str] | None:
        item = self.db.get(HubItem, item_id)
        if item is None:
            return None

        version = self.db.get(HubItemVersion, version_id)
        if version is None or version.hub_item_id != item_id:
            return None

        cache_key = f"exports/items/{item_id}/versions/{version_id}/capability.zip"

        cached = self._get_from_cache(cache_key)
        if cached is not None:
            buf = io.BytesIO(cached)
            buf.seek(0)
            filename = f"{_safe_filename(item.name)}-v{_safe_filename(version.version)}.zip"
            log_event(
                "storage.cache_hit",
                storage_key=cache_key,
                operation="export_version_package",
            )
            return buf, filename

        log_event(
            "storage.cache_miss",
            storage_key=cache_key,
            operation="export_version_package",
        )

        relations = self._build_relations_payload(item_id)

        manifest = self._build_canonical_manifest(item, version)
        manifest["relations"] = relations

        readme = self._build_readme(item, version)

        zip_items: list[tuple[str, str | bytes]] = [
            (
                "manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
            ),
            ("relations.json",
             json.dumps(relations, indent=2, ensure_ascii=False, default=str)),
            ("README.md", readme),
        ]
        if version.config_json:
            zip_items.append(
                ("config.json",
                 json.dumps(version.config_json, indent=2, ensure_ascii=False, default=str))
            )
        if version.input_schema:
            zip_items.append(
                ("input_schema.json",
                 json.dumps(version.input_schema, indent=2, ensure_ascii=False, default=str))
            )
        if version.output_schema:
            zip_items.append(
                ("output_schema.json",
                 json.dumps(version.output_schema, indent=2, ensure_ascii=False, default=str))
            )
        if version.permission_json:
            zip_items.append(
                ("permission.json",
                 json.dumps(version.permission_json, indent=2, ensure_ascii=False, default=str))
            )
        if version.runtime_compatibility:
            zip_items.append(
                ("runtime_compatibility.json",
                 json.dumps(version.runtime_compatibility, indent=2, ensure_ascii=False,
                           default=str))
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, data in zip_items:
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zf.writestr(info, data)

        buf.seek(0)
        self._save_to_cache(cache_key, buf.read())
        buf.seek(0)
        filename = f"{_safe_filename(item.name)}-v{_safe_filename(version.version)}.zip"
        return buf, filename

    def _get_from_cache(self, key: str) -> bytes | None:
        try:
            from app.core.storage import get_storage
            storage = get_storage()
            return storage.get_bytes(key)
        except (KeyError, Exception):
            return None

    def _save_to_cache(self, key: str, data: bytes) -> None:
        try:
            from app.core.storage import get_storage
            storage = get_storage()
            storage.put_bytes(key, data)
        except Exception as e:
            log_event(
                "storage.put_failed",
                storage_key=key,
                reason=str(e)[:200],
                operation="cache_export_package",
            )

    def build_item_export(
        self, item_id: uuid.UUID
    ) -> tuple[io.BytesIO, str] | None:
        item = self.db.get(HubItem, item_id)
        if item is None:
            return None

        versions = (
            self.db.query(HubItemVersion)
            .filter(HubItemVersion.hub_item_id == item_id)
            .order_by(HubItemVersion.created_at.desc())
            .all()
        )

        relations = self._build_relations_payload(item_id)

        item_dict = {
            "id": str(item.id),
            "name": item.name,
            "type": item.type.value,
            "description": item.description,
            "industry": item.industry,
            "scenario": item.scenario,
            "source_type": item.source_type.value,
            "status": item.status.value,
            "risk_level": item.risk_level.value,
            "current_version_id": str(item.current_version_id) if item.current_version_id else None,
            "discoverable": item.discoverable,
            "allow_existing_references": item.allow_existing_references,
            "force_disabled": item.force_disabled,
            "created_by": item.created_by,
            "created_at": str(item.created_at),
            "updated_at": str(item.updated_at),
        }

        versions_list = []
        for v in versions:
            versions_list.append({
                "id": str(v.id),
                "version": v.version,
                "description": v.description,
                "status": v.status.value,
                "risk_level": v.risk_level.value,
                "manifest_json": v.manifest_json,
                "config_json": v.config_json,
                "input_schema": v.input_schema,
                "output_schema": v.output_schema,
                "permission_json": v.permission_json,
                "runtime_compatibility": v.runtime_compatibility,
                "package_hash": v.package_hash,
                "change_log": v.change_log,
                "created_by": v.created_by,
                "created_at": str(v.created_at) if v.created_at else None,
                "updated_at": str(v.updated_at) if v.updated_at else None,
            })

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            readme = self._build_readme(item)
            zip_items = [
                ("item.json", json.dumps(item_dict, indent=2, ensure_ascii=False, default=str)),
                ("versions.json", json.dumps(versions_list, indent=2, ensure_ascii=False, default=str)),
                ("relations.json", json.dumps(relations, indent=2, ensure_ascii=False, default=str)),
                ("README.md", readme),
            ]
            for arcname, data in zip_items:
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zf.writestr(info, data)

        buf.seek(0)
        filename = f"{_safe_filename(item.name)}-export.zip"
        return buf, filename

    def _build_canonical_manifest(
        self,
        item: HubItem,
        version: HubItemVersion,
    ) -> dict:
        mj = version.manifest_json or {}
        manifest: dict = {
            "manifest_version": mj.get("manifest_version", "0.1"),
            "name": item.name,
            "type": item.type.value,
            "version": version.version,
            "description": version.description,
        }
        for key, val in mj.items():
            if key not in (
                "manifest_version", "name", "type", "version", "description",
            ):
                manifest[key] = val

        manifest["config_json"] = version.config_json
        manifest["input_schema"] = version.input_schema
        manifest["output_schema"] = version.output_schema
        manifest["permission_json"] = version.permission_json
        manifest["runtime_compatibility"] = version.runtime_compatibility
        return manifest

    def _build_relations_payload(self, item_id: uuid.UUID) -> dict:
        items = (
            self.db.query(HubItemRelation)
            .options(
                joinedload(HubItemRelation.source_item),
                joinedload(HubItemRelation.target_item),
            )
            .filter(
                (HubItemRelation.source_item_id == item_id)
                | (HubItemRelation.target_item_id == item_id)
            )
            .all()
        )

        outgoing = [r for r in items if r.source_item_id == item_id]
        incoming = [r for r in items if r.target_item_id == item_id]

        def _serialize(r: HubItemRelation) -> dict:
            return {
                "id": str(r.id),
                "relation_type": r.relation_type.value,
                "relation_scope": r.relation_scope.value,
                "required": r.required,
                "description": r.description,
                "source_item": {
                    "id": str(r.source_item_id),
                    "name": r.source_item.name if r.source_item else "",
                    "type": r.source_item.type.value if r.source_item else "",
                },
                "target_item": {
                    "id": str(r.target_item_id),
                    "name": r.target_item.name if r.target_item else "",
                    "type": r.target_item.type.value if r.target_item else "",
                },
            }

        return {
            "outgoing": [_serialize(r) for r in outgoing],
            "incoming": [_serialize(r) for r in incoming],
        }

    def _build_readme(
        self,
        item: HubItem,
        version: HubItemVersion | None = None,
    ) -> str:
        lines = [
            f"# {item.name}",
            "",
            f"- **Type**: {item.type.value}",
        ]
        if version:
            lines.append(f"- **Version**: {version.version}")
        if item.description:
            lines.append(f"- **Description**: {item.description}")
        lines.append(
            f"- **Exported at**: {datetime.now(timezone.utc).isoformat()}"
        )
        lines.append("")
        return "\n".join(lines)
