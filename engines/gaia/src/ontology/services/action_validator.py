"""Action parameter validation engine.

Validates action execution payloads against ActionType parameter definitions.
Supports type checking, required field validation, and unknown parameter rejection.

Aligns with Palantir Action Type parameter validation in the OSv2 execution pipeline.
"""

from typing import Any

from ontology.core.exceptions import ValidationError
from ontology.core.schemas.action import ActionContext, ActionTypeParameter


class ParameterValidator:
    """Validate action payload against parameter definitions.

    Usage:
        validator = ParameterValidator()
        param_defs = [ActionTypeParameter(api_name="status", data_type="STRING", ...)]
        validator.validate(param_defs, {"status": "shipped"})
    """

    # System parameters that are always allowed without declaration
    _SYSTEM_PARAMS: set[str] = {"rid", "expected_version", "mutations"}

    # Type mapping for basic data type validation
    _TYPE_MAP: dict[str, type | tuple[type, ...]] = {
        "STRING": str,
        "INTEGER": int,
        "SHORT": int,
        "LONG": int,
        "BOOLEAN": bool,
        "BYTE": int,
        "FLOAT": (float, int),
        "DOUBLE": (float, int),
        "DECIMAL": (float, int),
    }

    def resolve_defaults(
        self,
        parameters: list[ActionTypeParameter],
        payload: dict[str, Any],
        context: ActionContext | None = None,
    ) -> dict[str, Any]:
        """Resolve dynamic default values for missing parameters (P1, ADR-011).

        For each declared parameter not present in payload (or present as None),
        if ``default_source`` is non-static, resolve the value from the
        ActionContext and inject it into payload.

        Sources:
            - current_user: context.current_user
            - current_timestamp: ISO 8601 string of context.current_timestamp
            - workspace_id: context.workspace_id
            - selected_object_field: context.selected_object[default_source_field]

        Returns the (mutated) payload. Static defaults are still applied by
        ``validate`` to keep that path unchanged.
        """
        context = context or ActionContext()
        for param in parameters:
            if payload.get(param.api_name) is not None:
                continue
            if param.default_source == "static":
                continue
            resolved = self._resolve_dynamic_default(param, context)
            if resolved is not None:
                payload[param.api_name] = resolved
        return payload

    @staticmethod
    def _resolve_dynamic_default(param: ActionTypeParameter, context: ActionContext) -> Any:
        source = param.default_source
        if source == "current_user":
            return context.current_user
        if source == "current_timestamp":
            return context.current_timestamp.isoformat()
        if source == "workspace_id":
            return context.workspace_id
        if source == "selected_object_field":
            if not param.default_source_field:
                return None
            selected = context.selected_object or {}
            return selected.get(param.default_source_field)
        return None

    def validate(
        self,
        parameters: list[ActionTypeParameter],
        payload: dict[str, Any],
    ) -> None:
        """Validate payload against parameter definitions.

        Args:
            parameters: Action type parameter definitions from the ActionType.
            payload: User-supplied parameter values to validate.

        Raises:
            ValidationError: If any validation rule fails, with all errors aggregated.

        P1 (ADR-011): also enforces ``pattern`` (regex) and ``enum_values``
        constraints declared on parameters.
        """
        import re

        errors: list[str] = []
        param_names = {p.api_name for p in parameters}

        # Check for unknown parameters (exclude system params)
        unknown = set(payload.keys()) - param_names - self._SYSTEM_PARAMS
        for key in sorted(unknown):
            errors.append(f"Unknown parameter: '{key}'")

        # Check required parameters and validate types
        for param in parameters:
            if param.api_name not in payload:
                if param.required:
                    errors.append(f"Missing required parameter: '{param.api_name}'")
                continue

            value = payload[param.api_name]

            # Apply default if None (do this before type checking)
            if value is None and param.default is not None:
                value = param.default
                payload[param.api_name] = param.default

            # Type validation for basic types
            expected_type = self._TYPE_MAP.get(param.data_type)
            if expected_type is not None:
                if not isinstance(value, expected_type):
                    msg = param.error_message or (
                        f"Parameter '{param.api_name}': expected {param.data_type}, got {type(value).__name__}"
                    )
                    errors.append(msg)

            # P1: enum constraint
            if param.enum_values and isinstance(value, str) and value not in param.enum_values:
                msg = param.error_message or (
                    f"Parameter '{param.api_name}': value '{value}' not in allowed values {param.enum_values}"
                )
                errors.append(msg)

            # P1: pattern (regex) constraint — only for string values
            if param.pattern and isinstance(value, str):
                if not re.fullmatch(param.pattern, value):
                    msg = param.error_message or (
                        f"Parameter '{param.api_name}': value '{value}' does not match pattern {param.pattern}"
                    )
                    errors.append(msg)

        if errors:
            raise ValidationError("; ".join(errors))
