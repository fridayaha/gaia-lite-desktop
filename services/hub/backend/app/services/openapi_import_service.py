import json
import re
import time
import uuid

from sqlalchemy.orm import Session

import yaml

from app.core.enums import HubItemStatus, RiskLevel, SourceType
from app.core.event_log import log_event
from app.core.tenancy import DEFAULT_VISIBILITY_SCOPE, resolve_tenant_ids
from app.manifests import validate_manifest
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion

_MAX_REF_DEPTH = 3


def _resolve_ref(ref: str, spec: dict, depth: int = 0) -> dict | None:
    if depth >= _MAX_REF_DEPTH:
        return None
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    current = spec
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    if isinstance(current, dict) and "$ref" in current:
        return _resolve_ref(current["$ref"], spec, depth + 1)
    return current if isinstance(current, dict) else None


def _schema_from_openapi(schema_obj, spec: dict) -> dict | None:
    if schema_obj is None:
        return None
    if isinstance(schema_obj, dict) and "$ref" in schema_obj:
        resolved = _resolve_ref(schema_obj["$ref"], spec)
        if resolved is not None:
            return resolved
        return {"type": "object"}
    if isinstance(schema_obj, dict):
        return schema_obj
    return None


def _sanitize_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_").lower()
    return name or "unnamed"


def _collect_names(spec: dict) -> set[str]:
    names: set[str] = set()
    for path, methods in (spec.get("paths") or {}).items():
        for method in ("get", "post", "put", "delete", "patch"):
            op = methods.get(method)
            if not op:
                continue
            op_id = op.get("operationId")
            if op_id:
                names.add(op_id.lower())
            else:
                names.add(_sanitize_name(f"{method}_{path}"))
    return names


def _get_server_url(spec: dict, path_item: dict, operation: dict) -> str | None:
    for source in (
        operation.get("servers"),
        path_item.get("servers"),
        spec.get("servers"),
    ):
        if source and isinstance(source, list) and len(source) > 0:
            url = source[0].get("url", "")
            if url:
                return url.rstrip("/") if isinstance(url, str) else ""
    return None


def _build_permission_json(spec: dict, operation: dict, server_url: str | None) -> dict:
    security_list = operation.get("security") or spec.get("security") or []
    schemes = spec.get("components", {}).get("securitySchemes", {})
    scheme_keys = list(schemes.keys()) if schemes else []
    auth_required = bool(security_list) or bool(scheme_keys)

    allowed_domains: list[str] = []
    if server_url:
        host = server_url.replace("https://", "").replace("http://", "").split("/")[0]
        if host:
            allowed_domains.append(host)

    return {
        "external_access": bool(allowed_domains),
        "allowed_domains": allowed_domains,
        "auth_required": auth_required,
        "security_schemes": scheme_keys[:5],
        "source": "openapi_import",
    }


def _build_runtime_compatibility(spec: dict) -> dict:
    return {
        "tool_protocol": "openapi",
        "source": "openapi_import",
        "openapi_version": spec.get("openapi", "unknown"),
        "supported_invocation": "http",
    }


def _build_input_schema(
    parameters: list | None, request_body: dict | None, spec: dict
) -> dict:
    schema: dict = {"type": "object", "properties": {}}
    required: list[str] = []

    for param in (parameters or []):
        if not isinstance(param, dict):
            continue
        param_name = param.get("name")
        if not param_name:
            continue
        param_schema = _schema_from_openapi(param.get("schema"), spec)
        prop: dict = {"type": param_schema.get("type", "string")} if param_schema else {"type": "string"}
        if param.get("description"):
            prop["description"] = param["description"]
        schema["properties"][param_name] = prop
        if param.get("required") and param_name not in required:
            required.append(param_name)

    if request_body and isinstance(request_body, dict):
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        body_schema = _schema_from_openapi(json_content.get("schema"), spec)
        if body_schema:
            schema["properties"]["body"] = body_schema
        else:
            schema["properties"]["body"] = {"type": "object"}

    if required:
        schema["required"] = required

    return schema


def _build_output_schema(responses: dict | None, spec: dict) -> dict | None:
    if not responses:
        return None
    for code in ("200", "201", "default"):
        resp = responses.get(code)
        if not resp or not isinstance(resp, dict):
            continue
        content = resp.get("content", {})
        json_content = content.get("application/json", {})
        schema_obj = _schema_from_openapi(json_content.get("schema"), spec)
        if schema_obj:
            return schema_obj
    return None


def _convert_operation(
    path: str,
    method: str,
    operation: dict,
    path_item: dict,
    spec: dict,
    existing_names: set[str],
    warnings: list[dict],
) -> dict | None:
    op_id = operation.get("operationId")

    if op_id:
        name = op_id
    else:
        name = _sanitize_name(f"{method}_{path}")

    name_lower = name.lower()
    if name_lower in existing_names:
        suffix = 2
        while f"{name_lower}_{suffix}" in existing_names:
            suffix += 1
        warnings.append({
            "operation": op_id or name,
            "path": path,
            "method": method,
            "detail": f"name conflict, renamed to {name}_{suffix}",
        })
        name = f"{name}_{suffix}"
        existing_names.add(name.lower())
    else:
        existing_names.add(name_lower)

    description = operation.get("summary") or operation.get("description") or f"{method.upper()} {path}"

    server_url = _get_server_url(spec, path_item, operation)
    if not server_url:
        warnings.append({
            "operation": op_id or name,
            "path": path,
            "method": method,
            "detail": "no server URL found, invocation endpoint set to path only",
        })
    endpoint = f"{server_url}{path}" if server_url else path

    input_schema = _build_input_schema(
        operation.get("parameters"), operation.get("requestBody"), spec
    )
    output_schema = _build_output_schema(operation.get("responses"), spec)
    permission_json = _build_permission_json(spec, operation, server_url)
    runtime_compat = _build_runtime_compatibility(spec)

    tags = operation.get("tags") or []
    metadata = {
        "tags": tags,
        "original_operation_id": op_id,
        "path": path,
        "method": method,
        "source": "openapi_import",
    }

    return {
        "name": name,
        "type": "tool",
        "version": spec.get("info", {}).get("version", "0.1.0"),
        "manifest_version": "0.1",
        "description": description,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "invocation": {
            "method": method.upper(),
            "endpoint": endpoint,
            "timeout_ms": 30000,
        },
        "permission_json": permission_json,
        "runtime_compatibility": runtime_compat,
        "metadata": metadata,
    }


def _ensure_unique_name(base_name: str, db: Session,
                        org_id: str | None = None,
                        ws_id: str | None = None) -> str:
    name = base_name
    suffix = 2
    while True:
        query = db.query(HubItem).filter(HubItem.name.ilike(name))
        if org_id is not None and ws_id is not None:
            query = query.filter(
                HubItem.organization_id == org_id,
                HubItem.workspace_id == ws_id,
            )
        existing = query.first()
        if existing is None:
            return name
        name = f"{base_name}_{suffix}"
        suffix += 1


class OpenAPIImportService:
    def __init__(self, db: Session):
        self.db = db

    def _parse_spec(self, content: bytes, filename: str) -> dict:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("yaml", "yml"):
            return yaml.safe_load(content)
        if ext == "json":
            return json.loads(content)
        raise ValueError("unsupported file format, expected .json/.yaml/.yml")

    def import_from_spec(
        self, content: bytes, filename: str,
        created_by: str | None = None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict:
        t0 = time.monotonic()
        spec = self._parse_spec(content, filename)

        if not isinstance(spec, dict):
            raise ValueError("not a valid OpenAPI 3.x spec")
        if "openapi" not in spec:
            raise ValueError("not a valid OpenAPI 3.x spec")
        if not spec.get("paths"):
            raise ValueError("no operations found in OpenAPI spec")

        batch_id = str(uuid.uuid4())
        self._save_original_spec(content, filename, batch_id)

        spec_title = spec.get("info", {}).get("title", "unknown")
        spec_version = spec.get("info", {}).get("version", "unknown")

        log_event(
            "openapi.import.started",
            spec_title=spec_title,
            spec_version=spec_version,
        )

        existing_names = _collect_names(spec)
        items: list[dict] = []
        warnings: list[dict] = []
        tool_count = 0

        for path, methods in list(spec["paths"].items()):
            if not isinstance(methods, dict):
                continue
            path_item = methods
            for method in ("get", "post", "put", "delete", "patch"):
                operation = path_item.get(method)
                if not operation or not isinstance(operation, dict):
                    continue

                manifest = _convert_operation(
                    path=path,
                    method=method,
                    operation=operation,
                    path_item=path_item,
                    spec=spec,
                    existing_names=existing_names,
                    warnings=warnings,
                )
                if manifest is None:
                    continue

                try:
                    result = self._create_tool(manifest, created_by=created_by,
                                               organization_id=organization_id,
                                               workspace_id=workspace_id)
                    items.append(result)
                    tool_count += 1
                except Exception as exc:
                    self.db.rollback()
                    warnings.append({
                        "operation": manifest.get("name"),
                        "path": path,
                        "method": method,
                        "detail": str(exc),
                    })

        duration_ms = round((time.monotonic() - t0) * 1000)

        if tool_count == 0:
            log_event(
                "openapi.import.failed",
                spec_title=spec_title,
                spec_version=spec_version,
                duration_ms=duration_ms,
                warnings_count=len(warnings),
                failed_count=len(existing_names),
            )
            raise ValueError("no tools could be created from the spec")

        log_event(
            "openapi.import.completed",
            spec_title=spec_title,
            spec_version=spec_version,
            tools_created=tool_count,
            warnings_count=len(warnings),
            duration_ms=duration_ms,
        )

        return {
            "tools_created": tool_count,
            "items": items,
            "warnings": warnings,
            "batch_id": batch_id,
        }

    def _create_tool(self, manifest: dict,
                     created_by: str | None = None,
                     organization_id: str | None = None,
                     workspace_id: str | None = None) -> dict:
        name = _ensure_unique_name(manifest["name"], self.db,
                                    org_id=organization_id, ws_id=workspace_id)
        if name != manifest["name"]:
            manifest["name"] = name

        result = validate_manifest(manifest)
        if not result.valid:
            error_msgs = [e.message for e in result.errors]
            raise ValueError(f"manifest validation failed: {', '.join(error_msgs)}")

        org_id, ws_id = resolve_tenant_ids(organization_id, workspace_id)

        item = HubItem(
            name=name,
            type="tool",
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            source_type=SourceType.upload,
            discoverable=True,
            organization_id=org_id,
            workspace_id=ws_id,
            visibility_scope=DEFAULT_VISIBILITY_SCOPE,
            created_by=created_by,
        )
        self.db.add(item)
        self.db.flush()

        version = HubItemVersion(
            hub_item_id=item.id,
            version=manifest.get("version", "0.1.0"),
            manifest_json=result.normalized_manifest,
            input_schema=manifest.get("input_schema"),
            output_schema=manifest.get("output_schema"),
            permission_json=manifest.get("permission_json"),
            runtime_compatibility=manifest.get("runtime_compatibility"),
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
            created_by=created_by,
        )
        self.db.add(version)
        self.db.flush()

        item.current_version_id = version.id
        self.db.commit()

        return {
            "item_id": str(item.id),
            "name": name,
            "type": "tool",
            "version": version.version,
        }

    def _save_original_spec(
        self, content: bytes, filename: str, batch_id: str
    ) -> None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "yaml"
        key = f"imports/openapi/{batch_id}/original.{ext}"
        try:
            from app.core.storage import get_storage
            storage = get_storage()
            storage.put_bytes(key, content)
        except Exception as e:
            log_event(
                "storage.put_failed",
                storage_key=key,
                reason=str(e)[:200],
                operation="save_openapi_spec",
            )
