"""Unit tests for ActionRuleEngine — declarative rule evaluation."""

import pytest

from ontology.core.schemas.action import ActionContext, ActionRule, SubmissionCriterion
from ontology.services.action_rule_engine import ActionRuleEngine


@pytest.fixture
def engine() -> ActionRuleEngine:
    return ActionRuleEngine()


class TestActionRuleEngine:
    """ActionRuleEngine tests covering derivation, constraint, and error handling."""

    # ── Derivation rules ──

    def test_derivation_computes_new_value(self, engine: ActionRuleEngine):
        """Derivation rule computes a value from input parameters."""
        rules = [
            ActionRule(
                type="derivation",
                target="total",
                expression="quantity * unit_price",
                description="Compute total",
            ),
        ]
        params: dict[str, object] = {"quantity": 5, "unit_price": 10.0}
        derived, errors = engine.evaluate(rules, params)

        assert len(errors) == 0
        assert derived["total"] == 50.0
        assert params["total"] == 50.0  # Available for subsequent rules

    def test_derivation_can_reference_prior_derived(self, engine: ActionRuleEngine):
        """Derived values are available to subsequent derivation rules."""
        rules = [
            ActionRule(type="derivation", target="subtotal", expression="quantity * unit_price"),
            ActionRule(type="derivation", target="tax", expression="subtotal * 0.1"),
            ActionRule(type="derivation", target="total", expression="subtotal + tax"),
        ]
        params: dict[str, object] = {"quantity": 10, "unit_price": 5.0}
        derived, errors = engine.evaluate(rules, params)

        assert len(errors) == 0
        assert derived["subtotal"] == 50.0
        assert derived["tax"] == 5.0
        assert derived["total"] == 55.0

    def test_derivation_uses_safe_functions(self, engine: ActionRuleEngine):
        """Safe built-in functions can be used in expressions."""
        rules = [
            ActionRule(type="derivation", target="len_name", expression="len(name)"),
        ]
        params: dict[str, object] = {"name": "hello"}
        derived, errors = engine.evaluate(rules, params)

        assert len(errors) == 0
        assert derived["len_name"] == 5

    # ── Constraint rules ──

    def test_constraint_passes(self, engine: ActionRuleEngine):
        """Constraint that evaluates to True passes validation."""
        rules = [
            ActionRule(
                type="constraint",
                target="quantity",
                expression="quantity > 0",
                description="Quantity must be positive",
            ),
        ]
        _, errors = engine.evaluate(rules, {"quantity": 10})
        assert len(errors) == 0

    def test_constraint_fails(self, engine: ActionRuleEngine):
        """Constraint that evaluates to False returns error."""
        rules = [
            ActionRule(
                type="constraint",
                target="quantity",
                expression="quantity > 0",
                description="Quantity must be positive",
            ),
        ]
        _, errors = engine.evaluate(rules, {"quantity": -5})
        assert len(errors) == 1
        assert "Quantity must be positive" in errors[0]

    def test_constraint_uses_description_fallback(self, engine: ActionRuleEngine):
        """When description is empty, expression is used in error message."""
        rules = [
            ActionRule(type="constraint", target="x", expression="x > 0"),
        ]
        _, errors = engine.evaluate(rules, {"x": -1})
        assert len(errors) == 1
        assert "x > 0" in errors[0]

    def test_validation_rule_behaves_like_constraint(self, engine: ActionRuleEngine):
        """Validation rules are evaluated the same as constraints."""
        rules = [
            ActionRule(type="validation", target="email", expression='email != ""'),
        ]
        _, errors = engine.evaluate(rules, {"email": ""})
        assert len(errors) == 1

    # ── Mixed rules ──

    def test_mixed_derivation_and_constraint(self, engine: ActionRuleEngine):
        """Derivation runs before constraint checking and values are shared."""
        rules = [
            ActionRule(type="derivation", target="total", expression="quantity * unit_price"),
            ActionRule(
                type="constraint",
                target="total",
                expression="total < 1000",
                description="Total must be under 1000",
            ),
        ]
        _, errors = engine.evaluate(rules, {"quantity": 100, "unit_price": 5.0})
        # total = 500 < 1000, should pass
        assert len(errors) == 0

    def test_mixed_rules_constraint_fails(self, engine: ActionRuleEngine):
        """When constraint fails after derivation, error is captured."""
        rules = [
            ActionRule(type="derivation", target="total", expression="quantity * unit_price"),
            ActionRule(
                type="constraint",
                target="total",
                expression="total < 1000",
                description="Total must be under 1000",
            ),
        ]
        _, errors = engine.evaluate(rules, {"quantity": 500, "unit_price": 10.0})
        # total = 5000 >= 1000, should fail
        assert len(errors) == 1
        assert "Total must be under 1000" in errors[0]

    # ── Edge cases ──

    def test_no_rules_returns_empty(self, engine: ActionRuleEngine):
        """No rules should return empty derived values and no errors."""
        derived, errors = engine.evaluate([], {"x": 1})
        assert derived == {}
        assert len(errors) == 0

    def test_derivation_error_captured(self, engine: ActionRuleEngine):
        """Derivation rule errors are captured as validation errors."""
        rules = [
            ActionRule(type="derivation", target="result", expression="undefined_var / 0"),
        ]
        _, errors = engine.evaluate(rules, {"x": 1})
        assert len(errors) == 1
        assert "Derivation rule" in errors[0]

    def test_constraint_error_captured(self, engine: ActionRuleEngine):
        """Constraint rule errors are captured."""
        rules = [
            ActionRule(type="constraint", target="x", expression="unknown_function(x)"),
        ]
        _, errors = engine.evaluate(rules, {"x": 1})
        assert len(errors) == 1
        assert "evaluation failed" in errors[0]

    def test_derivation_returns_false_does_not_trigger_constraint_error(self, engine: ActionRuleEngine):
        """A derivation that returns False is valid — only constraint rules check truthiness."""
        rules = [
            ActionRule(type="derivation", target="flag", expression="1 < 0"),
            ActionRule(type="constraint", target="x", expression="x > 0"),
        ]
        _, errors = engine.evaluate(rules, {"x": 1})
        # derivation returning False is fine for derivation rules
        assert len(errors) == 0


class TestActionRuleEngineP1:
    """P1 (ADR-011): context injection + submission criteria."""

    def test_evaluate_injects_current_user(self, engine: ActionRuleEngine):
        """Derivation rule can reference currentUser from context."""
        rules = [
            ActionRule(type="derivation", target="performed_by", expression="currentUser"),
        ]
        ctx = ActionContext(current_user="alice")
        derived, errors = engine.evaluate(rules, {}, ctx)
        assert errors == []
        assert derived["performed_by"] == "alice"

    def test_evaluate_injects_selected_object_field(self, engine: ActionRuleEngine):
        """Constraint rule can reference selectedObject fields."""
        rules = [
            ActionRule(
                type="constraint",
                target="amount",
                expression="amount <= selectedObject['credit_limit']",
                description="Amount exceeds credit limit",
            ),
        ]
        ctx = ActionContext(selected_object={"credit_limit": 1000})
        _, errors = engine.evaluate(rules, {"amount": 500}, ctx)
        assert errors == []

    def test_evaluate_constraint_fails_with_context(self, engine: ActionRuleEngine):
        """Constraint referencing context fails when condition is false."""
        rules = [
            ActionRule(
                type="constraint",
                target="amount",
                expression="amount <= selectedObject['credit_limit']",
                description="Amount exceeds credit limit",
            ),
        ]
        ctx = ActionContext(selected_object={"credit_limit": 100})
        _, errors = engine.evaluate(rules, {"amount": 500}, ctx)
        assert len(errors) == 1
        assert "credit limit" in errors[0]

    def test_evaluate_default_context_is_anonymous(self, engine: ActionRuleEngine):
        """Without context, currentUser defaults to 'anonymous'."""
        rules = [
            ActionRule(type="derivation", target="who", expression="currentUser"),
        ]
        derived, _ = engine.evaluate(rules, {}, None)
        assert derived["who"] == "anonymous"

    def test_evaluate_submission_criteria_pass(self, engine: ActionRuleEngine):
        """All criteria pass → empty error list."""
        criteria = [
            SubmissionCriterion(expression="quantity > 0", error_message="Quantity must be positive"),
            SubmissionCriterion(expression="status == 'open'", error_message="Order must be open"),
        ]
        errors = engine.evaluate_submission_criteria(criteria, {"quantity": 5, "status": "open"}, ActionContext())
        assert errors == []

    def test_evaluate_submission_criteria_fail(self, engine: ActionRuleEngine):
        """Failing criterion returns its error_message."""
        criteria = [
            SubmissionCriterion(expression="quantity > 0", error_message="Quantity must be positive"),
        ]
        errors = engine.evaluate_submission_criteria(criteria, {"quantity": -1}, ActionContext())
        assert len(errors) == 1
        assert errors[0] == "Quantity must be positive"

    def test_evaluate_submission_criteria_empty(self, engine: ActionRuleEngine):
        """No criteria → no errors."""
        errors = engine.evaluate_submission_criteria([], {}, ActionContext())
        assert errors == []

    def test_evaluate_submission_criteria_uses_context(self, engine: ActionRuleEngine):
        """Criteria can reference currentUser."""
        criteria = [
            SubmissionCriterion(
                expression="currentUser == 'admin' or currentUser == 'manager'",
                error_message="Only admin/manager may submit",
            ),
        ]
        errors_ok = engine.evaluate_submission_criteria(criteria, {}, ActionContext(current_user="manager"))
        assert errors_ok == []
        errors_fail = engine.evaluate_submission_criteria(criteria, {}, ActionContext(current_user="intern"))
        assert len(errors_fail) == 1
