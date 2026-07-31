from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
import os

from app.core.auth_context import AuthContext


@dataclass
class ApprovalPolicyDecision:
    allowed: bool
    reason: str | None = None
    reason_code: str | None = None

    @classmethod
    def allow(cls) -> "ApprovalPolicyDecision":
        return cls(allowed=True)

    @classmethod
    def deny(
        cls, reason: str, reason_code: str | None = None
    ) -> "ApprovalPolicyDecision":
        return cls(allowed=False, reason=reason, reason_code=reason_code)


@dataclass
class ApprovalPolicyContext:
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    submit_event_id: str | None = None
    four_eyes_required: bool = False
    fail_open_when_submitter_missing: bool = True


def is_four_eyes_required() -> bool:
    return os.environ.get("HUB_FOUR_EYES_REQUIRED", "false").lower() in (
        "1", "true", "yes", "on",
    )


def _is_admin(ctx: AuthContext) -> bool:
    return "platform_admin" in ctx.roles


@runtime_checkable
class ApprovalPolicy(Protocol):
    def can_submit_review(
        self,
        ctx: AuthContext,
        item: Any,
        version: Any,
        operator: str,
        reason: str | None,
        policy_context: ApprovalPolicyContext | None = None,
    ) -> ApprovalPolicyDecision: ...

    def can_approve(
        self,
        ctx: AuthContext,
        item: Any,
        version: Any,
        operator: str,
        comment: str | None,
        policy_context: ApprovalPolicyContext | None = None,
    ) -> ApprovalPolicyDecision: ...

    def can_reject(
        self,
        ctx: AuthContext,
        item: Any,
        version: Any,
        operator: str,
        comment: str | None,
        policy_context: ApprovalPolicyContext | None = None,
    ) -> ApprovalPolicyDecision: ...

    def can_request_change(
        self,
        ctx: AuthContext,
        item: Any,
        version: Any,
        operator: str,
        comment: str | None,
        policy_context: ApprovalPolicyContext | None = None,
    ) -> ApprovalPolicyDecision: ...

    def can_publish(
        self,
        ctx: AuthContext,
        item: Any,
        version: Any,
        operator: str,
        reason: str | None,
        policy_context: ApprovalPolicyContext | None = None,
    ) -> ApprovalPolicyDecision: ...


class AllowAllApprovalPolicy:
    def can_submit_review(
        self, ctx, item, version, operator, reason,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_approve(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_reject(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_request_change(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_publish(
        self, ctx, item, version, operator, reason,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()


class DefaultApprovalPolicy:
    def can_submit_review(
        self, ctx, item, version, operator, reason,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_approve(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        if not is_four_eyes_required():
            return ApprovalPolicyDecision.allow()

        if _is_admin(ctx):
            from app.core.event_log import log_event
            log_event(
                "auth.four_eyes.admin_exempted",
                item_id=str(getattr(version, "id", "")),
                result="allowed",
            )
            return ApprovalPolicyDecision.allow()

        submitted_by = (
            policy_context.submitted_by
            if policy_context is not None
            else None
        )

        if not submitted_by:
            from app.core.event_log import log_event
            log_event(
                "auth.four_eyes.submitter_unknown",
                item_id=str(getattr(version, "id", "")),
                result="observed",
            )
            if policy_context is not None and not policy_context.fail_open_when_submitter_missing:
                return ApprovalPolicyDecision.deny(
                    "four eyes: unknown submitter, blocking",
                    "four_eyes_violation",
                )
            return ApprovalPolicyDecision.allow()

        if operator == submitted_by:
            from app.core.event_log import log_event
            log_event(
                "approval.policy_denied",
                item_id=str(getattr(version, "id", "")),
                reason_code="four_eyes_violation",
                submitted_by=submitted_by,
                approver=operator,
                result="denied",
            )
            return ApprovalPolicyDecision.deny(
                "four eyes: submitter cannot approve own version",
                "four_eyes_violation",
            )

        return ApprovalPolicyDecision.allow()

    def can_reject(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_request_change(
        self, ctx, item, version, operator, comment,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()

    def can_publish(
        self, ctx, item, version, operator, reason,
        policy_context: ApprovalPolicyContext | None = None,
    ):
        return ApprovalPolicyDecision.allow()
