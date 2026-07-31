import uuid

from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.enums import EventType, HubItemStatus, HubItemVersionStatus, RiskLevel
from app.core.event_log import log_event
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent
from app.models.scan_report import ScanReport
from app.policies.approval_policy import AllowAllApprovalPolicy, ApprovalPolicy
from app.services.exceptions import (
    ApprovalPolicyDeniedError,
    BlockingRiskSubmitError,
    HubItemNotFoundError,
    HubItemVersionNotFoundError,
    InvalidStateTransitionError,
    RollbackTargetInvalidError,
    VersionNotScannedError,
)


class LifecycleService:
    def __init__(self, db: Session, policy: ApprovalPolicy | None = None):
        self.db = db
        self.policy = policy or AllowAllApprovalPolicy()

    def _get_item(self, item_id: uuid.UUID) -> HubItem:
        item = self.db.get(HubItem, item_id)
        if item is None:
            raise HubItemNotFoundError(str(item_id))
        return item

    def _get_version(self, version_id: uuid.UUID) -> HubItemVersion:
        version = self.db.get(HubItemVersion, version_id)
        if version is None:
            raise HubItemVersionNotFoundError(str(version_id))
        return version

    def _record_event(
        self,
        item_id: uuid.UUID,
        version_id: uuid.UUID | None,
        event_type: EventType,
        from_status: str | None,
        to_status: str | None,
        operator: str,
        reason: str | None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        event = LifecycleEvent(
            hub_item_id=item_id,
            hub_item_version_id=version_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            reason=reason,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        self.db.add(event)

    def _ensure_scanned(self, version_id: uuid.UUID) -> None:
        scanned = (
            self.db.query(ScanReport)
            .filter(ScanReport.hub_item_version_id == version_id)
            .first()
        )
        if scanned is None:
            raise VersionNotScannedError(str(version_id))

    def _auto_scan(self, version_id: uuid.UUID, operator: str) -> None:
        from app.services.scan_service import ScanService

        scan_svc = ScanService(self.db)
        report = scan_svc.scan_version(version_id, operator=operator)
        if report.risk_level == RiskLevel.blocking:
            raise BlockingRiskSubmitError(str(version_id))

    def submit_item(
        self, item_id: uuid.UUID, operator: str,
        reason: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItem:
        item = self._get_item(item_id)
        if item.status != HubItemStatus.draft:
            raise InvalidStateTransitionError(
                item.status.value, HubItemStatus.pending_review.value, "HubItem"
            )

        _ctx = ctx or AuthContext()
        decision = self.policy.can_submit_review(_ctx, item, None, operator, reason)
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        old_status = item.status
        item.status = HubItemStatus.pending_review
        self._record_event(
            item.id, None, EventType.submitted,
            old_status.value, item.status.value, operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def submit_version(
        self, version_id: uuid.UUID, operator: str,
        reason: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItemVersion:
        version = self._get_version(version_id)
        allowed = (HubItemVersionStatus.draft, HubItemVersionStatus.change_required)
        if version.status not in allowed:
            raise InvalidStateTransitionError(
                version.status.value,
                HubItemVersionStatus.pending_review.value,
                "HubItemVersion",
            )

        self._auto_scan(version_id, operator)

        _ctx = ctx or AuthContext()
        item = self.db.get(HubItem, version.hub_item_id)
        decision = self.policy.can_submit_review(_ctx, item, version, operator, reason)
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        old_status = version.status
        version.status = HubItemVersionStatus.pending_review
        self._record_event(
            version.hub_item_id, version.id,
            EventType.submitted,
            old_status.value, version.status.value,
            operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        log_event(
            "lifecycle.submit_review",
            item_id=str(version.hub_item_id),
            version_id=str(version_id),
            action="submit_review",
            from_status=old_status.value,
            to_status=version.status.value,
            result="ok",
        )
        self.db.commit()
        self.db.refresh(version)
        return version

    def publish_version(
        self, version_id: uuid.UUID, operator: str,
        reason: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItemVersion:
        version = self._get_version(version_id)
        if version.status != HubItemVersionStatus.approved:
            raise InvalidStateTransitionError(
                version.status.value,
                HubItemVersionStatus.published.value,
                "HubItemVersion",
            )

        self._ensure_scanned(version_id)

        if version.risk_level == RiskLevel.blocking:
            raise BlockingRiskSubmitError(str(version_id))

        _ctx = ctx or AuthContext()
        item = self._get_item(version.hub_item_id)
        decision = self.policy.can_publish(_ctx, item, version, operator, reason)
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        if item.status in (HubItemStatus.disabled, HubItemStatus.archived):
            raise InvalidStateTransitionError(
                item.status.value,
                HubItemStatus.published.value,
                "HubItem",
            )
        old_version_status = version.status
        old_item_status = item.status
        old_current_id = item.current_version_id

        version.status = HubItemVersionStatus.published
        self._record_event(
            item.id, version.id,
            EventType.published,
            old_version_status.value, version.status.value,
            operator, reason,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )

        item.status = HubItemStatus.published
        item.current_version_id = version.id
        item.risk_level = version.risk_level
        self._record_event(
            item.id, None,
            EventType.published,
            old_item_status.value, item.status.value,
            operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )

        if old_current_id is not None and old_current_id != version.id:
            old_version = self.db.get(HubItemVersion, old_current_id)
            if old_version is not None and old_version.status == HubItemVersionStatus.published:
                old_prev_status = old_version.status
                old_version.status = HubItemVersionStatus.deprecated
                self._record_event(
                    item.id, old_version.id,
                    EventType.deprecated,
                    old_prev_status.value, old_version.status.value,
                    operator, reason,
                    organization_id=old_version.organization_id,
                    workspace_id=old_version.workspace_id,
                )

        log_event(
            "lifecycle.publish",
            item_id=str(item.id),
            version_id=str(version_id),
            action="publish",
            from_status=old_version_status.value,
            to_status=version.status.value,
            result="ok",
        )

        self.db.commit()
        self.db.refresh(version)
        return version

    def disable_item(
        self, item_id: uuid.UUID, operator: str, reason: str | None = None
    ) -> HubItem:
        item = self._get_item(item_id)
        if item.status != HubItemStatus.published:
            raise InvalidStateTransitionError(
                item.status.value, HubItemStatus.disabled.value, "HubItem"
            )
        old_status = item.status
        item.status = HubItemStatus.disabled
        item.discoverable = False
        item.allow_existing_references = True
        self._record_event(
            item.id, None, EventType.disabled,
            old_status.value, item.status.value, operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        log_event(
            "lifecycle.disable",
            item_id=str(item.id),
            action="disable",
            from_status=old_status.value,
            to_status=item.status.value,
            result="ok",
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def archive_item(
        self, item_id: uuid.UUID, operator: str, reason: str | None = None
    ) -> HubItem:
        item = self._get_item(item_id)
        if item.status not in (
            HubItemStatus.published,
            HubItemStatus.disabled,
        ):
            raise InvalidStateTransitionError(
                item.status.value, HubItemStatus.archived.value, "HubItem"
            )
        old_status = item.status
        item.status = HubItemStatus.archived
        item.discoverable = False
        self._record_event(
            item.id, None, EventType.archived,
            old_status.value, item.status.value, operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        log_event(
            "lifecycle.archive",
            item_id=str(item.id),
            action="archive",
            from_status=old_status.value,
            to_status=item.status.value,
            result="ok",
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def rollback_item(
        self,
        item_id: uuid.UUID,
        target_version_id: uuid.UUID,
        operator: str,
        reason: str | None = None,
    ) -> HubItem:
        item = self._get_item(item_id)
        if item.status != HubItemStatus.published:
            raise InvalidStateTransitionError(
                item.status.value, "rolled_back", "HubItem"
            )
        if item.current_version_id == target_version_id:
            raise RollbackTargetInvalidError(
                f"target version is already the current version"
            )
        target_version = self._get_version(target_version_id)
        if target_version.hub_item_id != item.id:
            raise RollbackTargetInvalidError(
                f"version {target_version_id} does not belong to item {item_id}"
            )
        if target_version.status not in (
            HubItemVersionStatus.published,
            HubItemVersionStatus.deprecated,
        ):
            raise RollbackTargetInvalidError(
                f"target version status is {target_version.status.value}, "
                f"must be published or deprecated"
            )
        old_current = self.db.get(HubItemVersion, item.current_version_id)
        old_version_id = item.current_version_id
        item.current_version_id = target_version.id
        item.risk_level = target_version.risk_level
        self._record_event(
            item.id, None, EventType.rolled_back,
            None, None, operator, reason,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        if old_current is not None:
            if old_current.status == HubItemVersionStatus.published:
                old_current.status = HubItemVersionStatus.deprecated
                self._record_event(
                    item.id, old_current.id,
                    EventType.deprecated,
                    HubItemVersionStatus.published.value,
                    old_current.status.value,
                    operator, reason,
                    organization_id=old_current.organization_id,
                    workspace_id=old_current.workspace_id,
                )
        if target_version.status == HubItemVersionStatus.deprecated:
            target_version.status = HubItemVersionStatus.published
            self._record_event(
                item.id, target_version.id,
                EventType.rolled_back,
                HubItemVersionStatus.deprecated.value,
                target_version.status.value,
                operator, reason,
                organization_id=target_version.organization_id,
                workspace_id=target_version.workspace_id,
            )
        log_event(
            "lifecycle.rollback",
            item_id=str(item.id),
            version_id=str(target_version_id),
            action="rollback",
            to_status=target_version.status.value,
            result="ok",
        )
        self.db.commit()
        self.db.refresh(item)
        return item
