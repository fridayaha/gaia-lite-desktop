"""Action rule engine — declarative rule evaluation with safe expression execution.

Supports three rule types (Palantir Action rules equivalent):
    - derivation: Compute derived parameter values from inputs
    - constraint: Validate parameter combinations
    - validation: Check business rules

Security: Uses simpleeval for safe expression evaluation (no arbitrary code execution).
Falls back gracefully if simpleeval is not installed.
"""

from typing import Any

from ontology.core.schemas.action import ActionContext, ActionRule, SubmissionCriterion


class ActionRuleEngine:
    """Evaluate declarative rules for an Action Type.

    Rules are evaluated in two phases:
    1. Derivation rules run first, producing new parameter values
    2. Constraint/validation rules run second, checking conditions

    Derived values are available to subsequent rules within the same evaluation.
    """

    # Safe built-in functions whitelist for expression evaluation
    _SAFE_FUNCTIONS: dict[str, Any] = {
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
    }

    def __init__(self) -> None:
        self._evaluator: Any = None
        self._evaluator_class: Any = None
        try:
            from simpleeval import SimpleEval

            self._evaluator_class = SimpleEval
            self._evaluator = SimpleEval(functions=self._SAFE_FUNCTIONS)
        except ImportError:
            self._evaluator = None
            self._evaluator_class = None

    def _safe_eval(self, expression: str, names: dict[str, Any]) -> Any:
        """Evaluate an expression with the given namespace."""
        if self._evaluator is None:
            raise ImportError("simpleeval not installed")
        self._evaluator.names = names
        return self._evaluator.eval(expression)

    def evaluate(
        self,
        rules: list[ActionRule],
        parameters: dict[str, Any],
        context: ActionContext | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Evaluate all rules, return derived values and validation errors.

        Args:
            rules: List of rule definitions from the ActionType.
            parameters: Input parameters (will be mutated with derived values).
            context: Execution context (P1, ADR-011) carrying built-in global
                variables (currentUser, currentTimestamp, workspaceId,
                selectedObject). When None, a default anonymous context is used.

        Returns:
            Tuple of (derived_parameters, validation_errors).
        """
        if self._evaluator is None:
            return {}, ["Rule engine not available (simpleeval not installed)"]

        context = context or ActionContext()
        # P1 (ADR-011): inject built-in global variables into the evaluation
        # namespace so rule expressions can reference currentUser /
        # currentTimestamp / workspaceId / selectedObject — mirrors Palantir
        # Foundry's Action context injection.
        builtins: dict[str, Any] = {
            "currentUser": context.current_user,
            "currentTimestamp": context.current_timestamp,
            "workspaceId": context.workspace_id,
            "selectedObject": context.selected_object or {},
        }

        errors: list[str] = []
        derived: dict[str, Any] = {}

        # Phase 1: Execute derivation rules first
        derivation_rules = [r for r in rules if r.type == "derivation"]
        for rule in derivation_rules:
            try:
                result = self._safe_eval(
                    rule.expression,
                    {**builtins, **parameters, **derived},
                )
                derived[rule.target] = result
                parameters[rule.target] = result  # Available to subsequent rules
            except Exception as e:
                errors.append(f"Derivation rule '{rule.target}' failed: {e}")

        # Phase 2: Execute constraint/validation rules
        check_rules = [r for r in rules if r.type in ("constraint", "validation")]
        for rule in check_rules:
            try:
                # Bind `value` to the rule's target parameter so constraint
                # expressions like `value in ['0','1','2']` work without the
                # caller having to repeat the parameter name. Also expose the
                # raw target value under the parameter's own name.
                target_value = parameters.get(rule.target)
                rule_names = {**builtins, **parameters, **derived, "value": target_value, rule.target: target_value}
                result = self._safe_eval(
                    rule.expression,
                    rule_names,
                )
                if result is False:
                    msg = rule.description or rule.expression
                    errors.append(f"Validation failed: {msg}")
            except Exception as e:
                errors.append(f"Rule '{rule.target}' evaluation failed: {e}")

        return derived, errors

    def evaluate_submission_criteria(
        self,
        criteria: list[SubmissionCriterion],
        parameters: dict[str, Any],
        context: ActionContext | None = None,
    ) -> list[str]:
        """Evaluate global submission criteria (P1, ADR-011).

        Each criterion's expression is evaluated against the parameter
        namespace plus built-in context variables. A falsy result yields the
        criterion's error_message.

        Returns:
            List of error messages for failed criteria (empty = all passed).
        """
        if self._evaluator is None or not criteria:
            return []
        context = context or ActionContext()
        builtins: dict[str, Any] = {
            "currentUser": context.current_user,
            "currentTimestamp": context.current_timestamp,
            "workspaceId": context.workspace_id,
            "selectedObject": context.selected_object or {},
        }
        errors: list[str] = []
        for criterion in criteria:
            try:
                result = self._safe_eval(
                    criterion.expression,
                    {**builtins, **parameters},
                )
                if result is False:
                    errors.append(criterion.error_message)
            except Exception as e:
                errors.append(f"Submission criterion failed to evaluate ('{criterion.expression}'): {e}")
        return errors
