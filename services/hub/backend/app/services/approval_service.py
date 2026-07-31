import uuid

from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.enums import (
    ApprovalAction,
    EventType,
    HubItemVersionStatus,
    RiskLevel,
)
from app.core.event_log import log_event
from app.models.approval_record import ApprovalRecord
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent
from app.policies.approval_policy import (
    AllowAllApprovalPolicy,
    ApprovalPolicy,
    ApprovalPolicyContext,
    is_four_eyes_required,
)
from app.services.exceptions import (
    ApprovalPolicyDeniedError,
    ApprovalStateInvalidError,
    BlockingRiskApprovalError,
    HubItemVersionNotFoundError,
    VersionNotScannedError,
)


class ApprovalService:
    def __init__(self, db: Session, policy: ApprovalPolicy | None = None):
        self.db = db
        self.policy = policy or AllowAllApprovalPolicy()

    def _get_version(self, version_id: uuid.UUID) -> HubItemVersion:
        version = self.db.get(HubItemVersion, version_id)
        if version is None:
            raise HubItemVersionNotFoundError(str(version_id))
        return version

    def _record_approval(
        self,
        hub_item_id: uuid.UUID,
        version_id: uuid.UUID,
        action: ApprovalAction,
        from_status: str,
        to_status: str,
        operator: str,
        comment: str | None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        record = ApprovalRecord(
            hub_item_id=hub_item_id,
            hub_item_version_id=version_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            comment=comment,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        self.db.add(record)

    def _record_event(
        self,
        hub_item_id: uuid.UUID,
        version_id: uuid.UUID,
        event_type: EventType,
        from_status: str,
        to_status: str,
        operator: str,
        reason: str | None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        event = LifecycleEvent(
            hub_item_id=hub_item_id,
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

    def _get_latest_submitter(
        self, version_id: uuid.UUID,
    ) -> ApprovalPolicyContext:
        submit_event = (
            self.db.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version_id,
                LifecycleEvent.event_type == EventType.submitted,
            )
            .order_by(LifecycleEvent.created_at.desc())
            .first()
        )
        return ApprovalPolicyContext(
            submitted_by=submit_event.operator if submit_event else None,
            four_eyes_required=is_four_eyes_required(),
            fail_open_when_submitter_missing=True,
        )

    def _require_pending_review(self, version: HubItemVersion) -> None:
        if version.status != HubItemVersionStatus.pending_review:
            raise ApprovalStateInvalidError(version.status.value)

    def approve_version(
        self, version_id: uuid.UUID, operator: str,
        comment: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItemVersion:
        version = self._get_version(version_id)
        self._require_pending_review(version)

        from app.models.scan_report import ScanReport

        scanned = (
            self.db.query(ScanReport)
            .filter(ScanReport.hub_item_version_id == version_id)
            .first()
        )
        if scanned is None:
            raise VersionNotScannedError(str(version_id))

        if version.risk_level == RiskLevel.blocking:
            raise BlockingRiskApprovalError(str(version_id))

        _ctx = ctx or AuthContext()
        item = self.db.get(HubItem, version.hub_item_id)
        policy_context = self._get_latest_submitter(version_id)
        decision = self.policy.can_approve(
            _ctx, item, version, operator, comment, policy_context=policy_context,
        )
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        old_status = version.status
        version.status = HubItemVersionStatus.approved

        self._record_approval(
            version.hub_item_id,
            version.id,
            ApprovalAction.approve,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )
        self._record_event(
            version.hub_item_id,
            version.id,
            EventType.approved,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )

        log_event(
            "lifecycle.approve",
            item_id=str(version.hub_item_id),
            version_id=str(version_id),
            action="approve",
            from_status=old_status.value,
            to_status=version.status.value,
            result="ok",
        )

        self.db.commit()
        self.db.refresh(version)
        return version

    def reject_version(
        self, version_id: uuid.UUID, operator: str,
        comment: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItemVersion:
        version = self._get_version(version_id)
        self._require_pending_review(version)

        _ctx = ctx or AuthContext()
        item = self.db.get(HubItem, version.hub_item_id)
        decision = self.policy.can_reject(_ctx, item, version, operator, comment)
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        old_status = version.status
        version.status = HubItemVersionStatus.rejected

        self._record_approval(
            version.hub_item_id,
            version.id,
            ApprovalAction.reject,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )
        self._record_event(
            version.hub_item_id,
            version.id,
            EventType.rejected,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )

        log_event(
            "lifecycle.reject",
            item_id=str(version.hub_item_id),
            version_id=str(version_id),
            action="reject",
            from_status=old_status.value,
            to_status=version.status.value,
            result="ok",
        )

        self.db.commit()
        self.db.refresh(version)
        return version

    def request_change(
        self, version_id: uuid.UUID, operator: str,
        comment: str | None = None,
        ctx: AuthContext | None = None,
    ) -> HubItemVersion:
        version = self._get_version(version_id)
        self._require_pending_review(version)

        _ctx = ctx or AuthContext()
        item = self.db.get(HubItem, version.hub_item_id)
        decision = self.policy.can_request_change(_ctx, item, version, operator, comment)
        if not decision.allowed:
            raise ApprovalPolicyDeniedError(decision.reason or "approval policy denied")

        old_status = version.status
        version.status = HubItemVersionStatus.change_required

        self._record_approval(
            version.hub_item_id,
            version.id,
            ApprovalAction.request_change,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )
        self._record_event(
            version.hub_item_id,
            version.id,
            EventType.change_requested,
            old_status.value,
            version.status.value,
            operator,
            comment,
            organization_id=version.organization_id,
            workspace_id=version.workspace_id,
        )

        log_event(
            "lifecycle.request_change",
            item_id=str(version.hub_item_id),
            version_id=str(version_id),
            action="request_change",
            from_status=old_status.value,
            to_status=version.status.value,
            result="ok",
        )

        self.db.commit()
        self.db.refresh(version)
        return version
