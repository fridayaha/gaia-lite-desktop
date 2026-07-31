"""Admin 路由单测（ADR-021 §3.3 rebuild-for-virtual）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.permission import Principal
from ontology.routes import admin as admin_routes


def _principal(is_admin: bool = True) -> Principal:
    return Principal(
        id="u1",
        display_name="admin",
        roles=["PLATFORM_ADMIN"] if is_admin else ["VIEWER"],
        is_anonymous=False,
    )


def _authz(allowed: bool = True):
    authz = AsyncMock()
    result = MagicMock()
    result.allowed = allowed
    result.reason = "" if allowed else "denied"
    authz.check_access.return_value = result
    return authz


class TestRebuildForVirtualRoute:
    """ADR-021 §3.3：VIRTUAL 重建路由。"""

    @pytest.mark.asyncio
    async def test_non_admin_returns_403(self):
        from fastapi import HTTPException

        funnel = AsyncMock()
        admin_routes.container.service_overrides["object_index_funnel"] = funnel
        try:
            with pytest.raises(HTTPException) as exc:
                await admin_routes.rebuild_projections_for_virtual(
                    ontology_api_name="SC",
                    object_type_api_name="Order",
                    request=MagicMock(),
                    principal=_principal(is_admin=False),
                    authz=_authz(allowed=False),
                )
            assert exc.value.status_code == 403
            funnel.project_for_virtual_object_type.assert_not_awaited()
        finally:
            admin_routes.container.service_overrides.pop("object_index_funnel", None)

    @pytest.mark.asyncio
    async def test_admin_triggers_virtual_projection(self):
        funnel = AsyncMock()
        funnel.project_for_virtual_object_type.return_value = {
            "nodes": 10, "edges": 3, "cleaned": 1, "partial": False,
        }
        admin_routes.container.service_overrides["object_index_funnel"] = funnel
        try:
            result = await admin_routes.rebuild_projections_for_virtual(
                ontology_api_name="SC",
                object_type_api_name="Order",
                request=MagicMock(),
                principal=_principal(is_admin=True),
                authz=_authz(allowed=True),
            )
            assert result["nodes"] == 10
            assert result["edges"] == 3
            funnel.project_for_virtual_object_type.assert_awaited_once_with(
                ontology_api_name="SC", object_type_api_name="Order"
            )
        finally:
            admin_routes.container.service_overrides.pop("object_index_funnel", None)

    @pytest.mark.asyncio
    async def test_projection_partial_propagates(self):
        """partial 降级标记透传到响应。"""
        funnel = AsyncMock()
        funnel.project_for_virtual_object_type.return_value = {
            "nodes": 5, "edges": 0, "cleaned": 0, "partial": True,
        }
        admin_routes.container.service_overrides["object_index_funnel"] = funnel
        try:
            result = await admin_routes.rebuild_projections_for_virtual(
                ontology_api_name="SC",
                object_type_api_name="Note",
                request=MagicMock(),
                principal=_principal(is_admin=True),
                authz=_authz(allowed=True),
            )
            assert result["partial"] is True
        finally:
            admin_routes.container.service_overrides.pop("object_index_funnel", None)
