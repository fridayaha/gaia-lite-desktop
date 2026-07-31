from app.core.auth_context import AuthContext
from app.core.operator import (
    log_operator_mismatch,
    resolve_and_log_operator,
    resolve_effective_operator,
)


class TestResolveEffectiveOperator:
    def test_actor_id_exists(self):
        ctx = AuthContext(actor_id="user-1")
        result = resolve_effective_operator(ctx, "alice")
        assert result == "user-1"

    def test_actor_id_missing_fallback_body(self):
        ctx = AuthContext(actor_id=None)
        result = resolve_effective_operator(ctx, "bob")
        assert result == "bob"

    def test_both_none(self):
        ctx = AuthContext(actor_id=None)
        result = resolve_effective_operator(ctx, None)
        assert result == "unknown"

    def test_actor_id_empty_string(self):
        ctx = AuthContext(actor_id="  ")
        result = resolve_effective_operator(ctx, "bob")
        assert result == "bob"

    def test_actor_id_present_body_none(self):
        ctx = AuthContext(actor_id="user-1")
        result = resolve_effective_operator(ctx, None)
        assert result == "user-1"

    def test_actor_id_whitespace_only(self):
        ctx = AuthContext(actor_id="")
        result = resolve_effective_operator(ctx, "bob")
        assert result == "bob"


class TestLogOperatorMismatch:
    def test_same_no_log(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="user-1")
        log_operator_mismatch(ctx, "user-1", "approve")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 0

    def test_different_logs(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="user-1")
        log_operator_mismatch(ctx, "admin", "approve", version_id="v1")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 1
        rec = events[0]
        assert rec.action == "approve"
        assert rec.body_operator == "admin"
        assert rec.version_id == "v1"
        assert rec.result == "observed"

    def test_actor_id_missing_no_log(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id=None)
        log_operator_mismatch(ctx, "admin", "approve")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 0

    def test_body_operator_missing_no_log(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="user-1")
        log_operator_mismatch(ctx, None, "approve")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 0

    def test_actor_id_empty_no_log(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="")
        log_operator_mismatch(ctx, "admin", "approve")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 0

    def test_body_operator_empty_no_log(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="user-1")
        log_operator_mismatch(ctx, "  ", "approve")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 0


class TestResolveAndLogOperator:
    def test_returns_effective_operator(self):
        ctx = AuthContext(actor_id="user-1")
        result = resolve_and_log_operator(ctx, "admin", "approve")
        assert result == "user-1"

    def test_logs_mismatch(self, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="hub.event")
        ctx = AuthContext(actor_id="user-1")
        resolve_and_log_operator(ctx, "admin", "approve", version_id="v1")
        events = [
            r for r in caplog.records
            if getattr(r, "event", None) == "auth.operator_mismatch"
        ]
        assert len(events) == 1
        assert events[0].action == "approve"
        assert events[0].body_operator == "admin"
        assert events[0].version_id == "v1"
