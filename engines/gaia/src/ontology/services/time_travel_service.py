"""TimeTravelService — Iceberg snapshot/historical queries."""

from typing import TYPE_CHECKING, cast

from ontology.core.exceptions import ForbiddenError
from ontology.core.permission_roles import OP_OBJECT_VIEW
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry

if TYPE_CHECKING:
    # engine 仅类型注解；移入 TYPE_CHECKING 避免 lite 版拉 trino 重依赖（A3）。
    # 按 QueryEngine 契约注解（Trino/DuckDB 共实现，B2）。
    from ontology.core.schemas.permission import Principal
    from ontology.layers.engine.base import QueryEngine
    from ontology.services.authorization_service import AuthorizationService


class TimeTravelService:
    """Time-travel / historical snapshot query service."""

    def __init__(
        self,
        catalog: GravitinoRegistry,
        engine: "QueryEngine",
        authorization_service: "AuthorizationService | None" = None,
    ) -> None:
        self._catalog = catalog
        self._engine = engine
        self._authz = authorization_service

    async def load_objects_as_of(
        self,
        object_type_api_name: str,
        ids: list[str],
        properties: list[str],
        snapshot_id: int,
        *,
        principal: "Principal | None" = None,
    ) -> list[dict[str, object]]:
        """Load historical versions of objects as of a specific snapshot."""
        # Permission check (fail-closed via PDP when principal is given).
        if principal is not None and self._authz is not None:
            result = await self._authz.check_access(principal, "OBJECT_TYPE", object_type_api_name, OP_OBJECT_VIEW)
            if not result.allowed:
                raise ForbiddenError(f"Access denied to {object_type_api_name}: {result.reason}")

        table_info = await self._catalog.resolve_backing_table(object_type_api_name)
        props = ", ".join(properties)
        id_list = ", ".join(f"'{id}'" for id in ids)
        table = f"iceberg_catalog.{table_info['schema']}.{table_info['table']}"

        sql = f"SELECT {props} FROM {table} FOR VERSION AS OF {snapshot_id} WHERE id IN ({id_list})"
        rows = await self._engine.query(sql)
        return cast(list[dict[str, object]], rows)
