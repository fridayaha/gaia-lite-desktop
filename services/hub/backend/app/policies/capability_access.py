from typing import Protocol, runtime_checkable

from app.core.auth_context import AuthContext
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


@runtime_checkable
class CapabilityAccessPolicy(Protocol):
    def can_discover(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool: ...

    def can_resolve(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool: ...


class AllowAllCapabilityAccessPolicy:
    def can_discover(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool:
        return True

    def can_resolve(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool:
        return True


class ScopedCapabilityAccessPolicy:
    def _has_access(self, context: AuthContext) -> bool:
        if not context.is_authenticated:
            return False
        return "platform_admin" in context.roles or "runtime_consumer" in context.roles

    def can_discover(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool:
        return self._has_access(context)

    def can_resolve(
        self,
        item: HubItem,
        version: HubItemVersion,
        context: AuthContext,
    ) -> bool:
        return self._has_access(context)
