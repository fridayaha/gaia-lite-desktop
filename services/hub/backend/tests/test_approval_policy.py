from app.core.auth_context import AuthContext
from app.policies.approval_policy import (
    AllowAllApprovalPolicy,
    ApprovalPolicyContext,
    ApprovalPolicyDecision,
    DefaultApprovalPolicy,
    is_four_eyes_required,
)
from app.services.exceptions import ApprovalPolicyDeniedError


class TestApprovalPolicyDecision:
    def test_allow(self):
        d = ApprovalPolicyDecision.allow()
        assert d.allowed is True
        assert d.reason is None
        assert d.reason_code is None

    def test_deny(self):
        d = ApprovalPolicyDecision.deny("not allowed", "FOO_1")
        assert d.allowed is False
        assert d.reason == "not allowed"
        assert d.reason_code == "FOO_1"

    def test_deny_no_code(self):
        d = ApprovalPolicyDecision.deny("blocked")
        assert d.allowed is False
        assert d.reason == "blocked"
        assert d.reason_code is None


class TestAllowAllApprovalPolicy:
    def setup_method(self):
        self.policy = AllowAllApprovalPolicy()
        self.ctx = AuthContext(actor_id="u1", roles=["contributor"])

    def test_can_approve(self):
        d = self.policy.can_approve(self.ctx, None, None, "op", "ok")
        assert d.allowed is True

    def test_can_approve_with_policy_context(self):
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_approve(
            self.ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is True

    def test_can_reject(self):
        d = self.policy.can_reject(self.ctx, None, None, "op", "no")
        assert d.allowed is True

    def test_can_request_change(self):
        d = self.policy.can_request_change(self.ctx, None, None, "op", "fix")
        assert d.allowed is True

    def test_can_submit_review(self):
        d = self.policy.can_submit_review(self.ctx, None, None, "op", "ready")
        assert d.allowed is True

    def test_can_publish(self):
        d = self.policy.can_publish(self.ctx, None, None, "op", "release")
        assert d.allowed is True


class TestDefaultApprovalPolicy:
    def setup_method(self):
        self.policy = DefaultApprovalPolicy()
        self.ctx = AuthContext(actor_id="op", roles=["contributor"])

    def test_approve_always_allows_when_four_eyes_disabled(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "false")
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_approve(
            self.ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is True

    def test_approve_denies_same_submitter_when_four_eyes_enabled(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_approve(
            self.ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is False
        assert d.reason_code == "four_eyes_violation"

    def test_approve_allows_different_approver_when_four_eyes_enabled(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by="submitter")
        d = self.policy.can_approve(
            self.ctx, None, None, "approver", "ok", policy_context=pctx,
        )
        assert d.allowed is True

    def test_admin_exempt_from_four_eyes(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        admin_ctx = AuthContext(actor_id="op", roles=["platform_admin"])
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_approve(
            admin_ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is True

    def test_submitter_unknown_fail_open(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by=None)
        d = self.policy.can_approve(
            self.ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is True

    def test_submitter_unknown_fail_closed(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(
            submitted_by=None, fail_open_when_submitter_missing=False,
        )
        d = self.policy.can_approve(
            self.ctx, None, None, "op", "ok", policy_context=pctx,
        )
        assert d.allowed is False
        assert d.reason_code == "four_eyes_violation"

    def test_reject_not_affected_by_four_eyes(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_reject(
            self.ctx, None, None, "op", "no", policy_context=pctx,
        )
        assert d.allowed is True

    def test_request_change_not_affected_by_four_eyes(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_request_change(
            self.ctx, None, None, "op", "fix", policy_context=pctx,
        )
        assert d.allowed is True

    def test_publish_not_affected_by_four_eyes(self, monkeypatch):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        pctx = ApprovalPolicyContext(submitted_by="op")
        d = self.policy.can_publish(
            self.ctx, None, None, "op", "go", policy_context=pctx,
        )
        assert d.allowed is True

    def test_is_four_eyes_required_helper(self, monkeypatch):
        monkeypatch.delenv("HUB_FOUR_EYES_REQUIRED", raising=False)
        assert is_four_eyes_required() is False

        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        assert is_four_eyes_required() is True

        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "1")
        assert is_four_eyes_required() is True

        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "yes")
        assert is_four_eyes_required() is True

        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "false")
        assert is_four_eyes_required() is False

        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "0")
        assert is_four_eyes_required() is False


class FakeDenyPolicy:
    def __init__(self, deny_method: str):
        self.deny_method = deny_method
        self.call_count = 0

    def _maybe_deny(self, method: str):
        self.call_count += 1
        if method == self.deny_method:
            return ApprovalPolicyDecision.deny(f"denied by {method}", "TEST_DENY")
        return ApprovalPolicyDecision.allow()

    def can_submit_review(
        self, ctx, item, version, operator, reason,
        policy_context=None,
    ):
        return self._maybe_deny("submit_review")

    def can_approve(
        self, ctx, item, version, operator, comment,
        policy_context=None,
    ):
        return self._maybe_deny("approve")

    def can_reject(
        self, ctx, item, version, operator, comment,
        policy_context=None,
    ):
        return self._maybe_deny("reject")

    def can_request_change(
        self, ctx, item, version, operator, comment,
        policy_context=None,
    ):
        return self._maybe_deny("request_change")

    def can_publish(
        self, ctx, item, version, operator, reason,
        policy_context=None,
    ):
        return self._maybe_deny("publish")


class TestApprovalPolicyDeniedError:
    def test_message(self):
        exc = ApprovalPolicyDeniedError("blocked by security")
        assert str(exc) == "blocked by security"
        assert exc.reason == "blocked by security"

    def test_default_message(self):
        exc = ApprovalPolicyDeniedError()
        assert str(exc) == "approval policy denied"


class TestFakeDenyPolicyIntegration:
    def test_deny_approve(self):
        from app.db.session import Session
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("approve")
        svc = ApprovalService(Session(), policy=policy)
        svc.db = _FakeDB(name="pending_review")
        try:
            svc.approve_version("v1", "op", "ok", ctx=AuthContext())
        except ApprovalPolicyDeniedError as e:
            assert "denied by approve" in str(e)
        assert policy.call_count >= 1

    def test_deny_approve_no_state_change(self):
        from app.db.session import Session
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("approve")
        db = _FakeDB(name="pending_review")
        svc = ApprovalService(Session(), policy=policy)
        svc.db = db
        try:
            svc.approve_version("v1", "op", "ok", ctx=AuthContext())
        except ApprovalPolicyDeniedError:
            pass
        assert not db.did_commit

    def test_deny_reject(self):
        from app.db.session import Session
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("reject")
        svc = ApprovalService(Session(), policy=policy)
        svc.db = _FakeDB(name="pending_review")
        try:
            svc.reject_version("v1", "op", "ok", ctx=AuthContext())
        except ApprovalPolicyDeniedError as e:
            assert "denied by reject" in str(e)
        assert policy.call_count >= 1

    def test_deny_request_change(self):
        from app.db.session import Session
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("request_change")
        svc = ApprovalService(Session(), policy=policy)
        svc.db = _FakeDB(name="pending_review")
        try:
            svc.request_change("v1", "op", "ok", ctx=AuthContext())
        except ApprovalPolicyDeniedError as e:
            assert "denied by request_change" in str(e)
        assert policy.call_count >= 1

    def test_deny_submit_review(self):
        from app.db.session import Session
        from app.services.lifecycle_service import LifecycleService
        from app.services.exceptions import ApprovalPolicyDeniedError
        from unittest.mock import patch

        policy = FakeDenyPolicy("submit_review")
        svc = LifecycleService(Session(), policy=policy)
        svc.db = _FakeDB(name="draft")
        with patch.object(svc, "_auto_scan"):
            try:
                svc.submit_version("v1", "op", "ready", ctx=AuthContext())
            except ApprovalPolicyDeniedError as e:
                assert "denied by submit_review" in str(e)
        assert policy.call_count >= 1

    def test_deny_publish(self):
        from app.db.session import Session
        from app.services.lifecycle_service import LifecycleService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("publish")
        svc = LifecycleService(Session(), policy=policy)
        svc.db = _FakeDB(name="approved")
        try:
            svc.publish_version("v1", "op", "go", ctx=AuthContext())
        except ApprovalPolicyDeniedError as e:
            assert "denied by publish" in str(e)
        assert policy.call_count >= 1

    def test_deny_submit_item(self):
        from app.db.session import Session
        from app.services.lifecycle_service import LifecycleService
        from app.services.exceptions import ApprovalPolicyDeniedError

        policy = FakeDenyPolicy("submit_review")
        svc = LifecycleService(Session(), policy=policy)
        svc.db = _FakeDB(name="draft_item")
        try:
            svc.submit_item("i1", "op", "ready", ctx=AuthContext())
        except ApprovalPolicyDeniedError as e:
            assert "denied by submit_review" in str(e)
        assert policy.call_count >= 1


class _FakeDB:
    def __init__(self, name: str = "", submitter: str | None = None):
        self._name = name
        self._submitter = submitter
        self.did_commit = False

    def get(self, model, pk):
        from app.models.hub_item_version import HubItemVersion
        from app.models.hub_item import HubItem

        if model is HubItemVersion:
            v = HubItemVersion()
            v.id = pk
            v.hub_item_id = pk
            from app.core.enums import HubItemVersionStatus

            if self._name == "pending_review":
                v.status = HubItemVersionStatus.pending_review
            elif self._name == "approved":
                v.status = HubItemVersionStatus.approved
            elif self._name == "draft":
                v.status = HubItemVersionStatus.draft
            return v
        if model is HubItem:
            from app.core.enums import HubItemStatus

            i = HubItem()
            i.id = pk
            if self._name == "draft_item":
                i.status = HubItemStatus.draft
            else:
                i.status = HubItemStatus.draft
            return i
        return None

    def query(self, model):
        from app.models.scan_report import ScanReport
        from app.models.lifecycle_event import LifecycleEvent

        if model is ScanReport:
            return _FakeQuery(has_results=True)
        if model is LifecycleEvent:
            return _FakeQuery(
                has_results=self._submitter is not None,
                submitter=self._submitter,
            )
        return _FakeQuery(has_results=False)

    def add(self, obj):
        pass

    def commit(self):
        self.did_commit = True

    def refresh(self, obj):
        pass

    def flush(self):
        pass


class _FakeQuery:
    def __init__(self, has_results=False, submitter: str | None = None):
        self._has_results = has_results
        self._submitter = submitter

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        if self._has_results:
            class _FakeEvent:
                pass
            event = _FakeEvent()
            event.operator = self._submitter
            return event
        return None
