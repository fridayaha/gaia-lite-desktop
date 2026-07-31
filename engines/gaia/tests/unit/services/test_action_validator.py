"""Unit tests for ParameterValidator — action payload validation."""

import pytest

from ontology.core.exceptions import ValidationError
from ontology.core.schemas.action import ActionTypeParameter
from ontology.core.schemas.ontology import DataType
from ontology.services.action_validator import ParameterValidator


class TestParameterValidator:
    """ParameterValidator tests covering type checking, required fields, and edge cases."""

    @pytest.fixture
    def validator(self) -> ParameterValidator:
        return ParameterValidator()

    def _make_str_param(
        self, api_name: str, required: bool = True, default: object | None = None
    ) -> ActionTypeParameter:
        return ActionTypeParameter(
            api_name=api_name,
            display_name=api_name.title(),
            data_type=DataType.STRING,
            required=required,
            default=default,
        )

    # ── Success paths ──

    def test_validate_all_required_params_present(self, validator):
        """Validation passes when all required params are present."""
        params = [
            self._make_str_param("name"),
            self._make_str_param("status"),
        ]
        validator.validate(params, {"name": "test", "status": "active"})

    def test_validate_optional_param_missing(self, validator):
        """Optional parameters can be missing."""
        params = [
            self._make_str_param("name"),
            self._make_str_param("notes", required=False),
        ]
        validator.validate(params, {"name": "test"})

    def test_validate_with_empty_parameters(self, validator):
        """Validation with no parameter definitions and no payload."""
        validator.validate([], {})

    def test_validate_applies_default_when_none(self, validator):
        """When payload value is None and default exists, apply the default."""
        params = [
            ActionTypeParameter(
                api_name="count",
                display_name="Count",
                data_type=DataType.INTEGER,
                required=False,
                default=5,
            ),
        ]
        payload: dict[str, object] = {"count": None}
        validator.validate(params, payload)
        assert payload["count"] == 5

    def test_validate_integer_type(self, validator):
        """INTEGER type accepts int values."""
        params = [
            ActionTypeParameter(
                api_name="quantity",
                display_name="Quantity",
                data_type=DataType.INTEGER,
            ),
        ]
        validator.validate(params, {"quantity": 10})

    def test_validate_float_type_accepts_int(self, validator):
        """FLOAT type accepts int (Python int is subclass of float check)."""
        params = [
            ActionTypeParameter(
                api_name="price",
                display_name="Price",
                data_type=DataType.FLOAT,
            ),
        ]
        validator.validate(params, {"price": 100})  # int acceptable for float

    def test_validate_boolean_type(self, validator):
        """BOOLEAN type accepts bool."""
        params = [
            ActionTypeParameter(
                api_name="active",
                display_name="Active",
                data_type=DataType.BOOLEAN,
            ),
        ]
        validator.validate(params, {"active": True})

    def test_validate_complex_types_are_skipped(self, validator):
        """STRUCT, ARRAY, VECTOR types skip type validation."""
        params = [
            ActionTypeParameter(
                api_name="tags",
                display_name="Tags",
                data_type=DataType.ARRAY,
            ),
            ActionTypeParameter(
                api_name="location",
                display_name="Location",
                data_type=DataType.GEOPOINT,
            ),
        ]
        validator.validate(params, {"tags": ["a", "b"], "location": {"lat": 1.0}})

    # ── Error paths ──

    def test_validate_missing_required_param_raises(self, validator):
        """Missing required parameter raises ValidationError."""
        params = [self._make_str_param("name")]
        with pytest.raises(ValidationError, match="Missing required parameter"):
            validator.validate(params, {})

    def test_validate_unknown_parameter_raises(self, validator):
        """Unknown parameter raises ValidationError."""
        params = [self._make_str_param("name")]
        with pytest.raises(ValidationError, match="Unknown parameter"):
            validator.validate(params, {"name": "test", "extra": "bad"})

    def test_validate_wrong_type_raises(self, validator):
        """Wrong type for parameter raises ValidationError."""
        params = [
            ActionTypeParameter(
                api_name="count",
                display_name="Count",
                data_type=DataType.INTEGER,
            ),
        ]
        with pytest.raises(ValidationError, match="expected INTEGER"):
            validator.validate(params, {"count": "not-an-int"})

    def test_validate_boolean_rejects_string(self, validator):
        """BOOLEAN type rejects string values."""
        params = [
            ActionTypeParameter(
                api_name="flag",
                display_name="Flag",
                data_type=DataType.BOOLEAN,
            ),
        ]
        with pytest.raises(ValidationError, match="expected BOOLEAN"):
            validator.validate(params, {"flag": "true"})

    def test_validate_aggregates_multiple_errors(self, validator):
        """Multiple validation errors are aggregated in one message."""
        params = [
            self._make_str_param("name"),
            self._make_str_param("email"),
        ]
        with pytest.raises(ValidationError) as exc:
            validator.validate(params, {"extra": "bad"})
        # Should mention both the unknown param and the missing required params
        msg = str(exc.value)
        assert "Unknown parameter" in msg
        assert "Missing required parameter" in msg


class TestParameterValidatorP1:
    """P1 (ADR-011): dynamic defaults, enum, pattern, error_message overrides."""

    @pytest.fixture
    def validator(self) -> ParameterValidator:
        return ParameterValidator()

    def test_resolve_defaults_current_user(self, validator: ParameterValidator):
        """Missing param with default_source=current_user is filled from context."""
        from ontology.core.schemas.action import ActionContext

        params = [
            ActionTypeParameter(
                api_name="createdBy",
                display_name="Created By",
                data_type=DataType.STRING,
                default_source="current_user",
            ),
        ]
        payload = validator.resolve_defaults(params, {}, ActionContext(current_user="alice"))
        assert payload["createdBy"] == "alice"

    def test_resolve_defaults_selected_object_field(self, validator: ParameterValidator):
        """default_source=selected_object_field reads from context.selected_object."""
        from ontology.core.schemas.action import ActionContext

        params = [
            ActionTypeParameter(
                api_name="owner",
                display_name="Owner",
                data_type=DataType.STRING,
                default_source="selected_object_field",
                default_source_field="owner",
            ),
        ]
        ctx = ActionContext(selected_object={"owner": "bob", "status": "open"})
        payload = validator.resolve_defaults(params, {}, ctx)
        assert payload["owner"] == "bob"

    def test_resolve_defaults_skips_static(self, validator: ParameterValidator):
        """Static default_source does nothing (validate handles static defaults)."""
        from ontology.core.schemas.action import ActionContext

        params = [
            ActionTypeParameter(
                api_name="status",
                display_name="Status",
                data_type=DataType.STRING,
                default_source="static",
                default="open",
            ),
        ]
        payload = validator.resolve_defaults(params, {}, ActionContext())
        # static defaults not applied by resolve_defaults
        assert "status" not in payload

    def test_resolve_defaults_preserves_existing(self, validator: ParameterValidator):
        """Existing payload values are not overwritten by dynamic defaults."""
        from ontology.core.schemas.action import ActionContext

        params = [
            ActionTypeParameter(
                api_name="createdBy",
                display_name="Created By",
                data_type=DataType.STRING,
                default_source="current_user",
            ),
        ]
        payload = validator.resolve_defaults(params, {"createdBy": "existing"}, ActionContext(current_user="alice"))
        assert payload["createdBy"] == "existing"

    def test_validate_enum_values_pass(self, validator: ParameterValidator):
        """Value in enum_values is accepted."""
        params = [
            ActionTypeParameter(
                api_name="priority",
                display_name="Priority",
                data_type=DataType.STRING,
                enum_values=["low", "medium", "high"],
            ),
        ]
        validator.validate(params, {"priority": "medium"})  # no exception

    def test_validate_enum_values_fail(self, validator: ParameterValidator):
        """Value not in enum_values raises ValidationError."""
        params = [
            ActionTypeParameter(
                api_name="priority",
                display_name="Priority",
                data_type=DataType.STRING,
                enum_values=["low", "medium", "high"],
            ),
        ]
        with pytest.raises(ValidationError, match="not in"):
            validator.validate(params, {"priority": "urgent"})

    def test_validate_pattern_pass(self, validator: ParameterValidator):
        """Value matching pattern is accepted."""
        params = [
            ActionTypeParameter(
                api_name="phone",
                display_name="Phone",
                data_type=DataType.STRING,
                pattern=r"\d{11}",
            ),
        ]
        validator.validate(params, {"phone": "13800138000"})

    def test_validate_pattern_fail(self, validator: ParameterValidator):
        """Value not matching pattern raises ValidationError."""
        params = [
            ActionTypeParameter(
                api_name="phone",
                display_name="Phone",
                data_type=DataType.STRING,
                pattern=r"\d{11}",
                error_message="Phone must be 11 digits",
            ),
        ]
        with pytest.raises(ValidationError, match="11 digits"):
            validator.validate(params, {"phone": "123"})

    def test_validate_custom_error_message(self, validator: ParameterValidator):
        """error_message override is used on type mismatch."""
        params = [
            ActionTypeParameter(
                api_name="count",
                display_name="Count",
                data_type=DataType.INTEGER,
                error_message="Count must be a whole number",
            ),
        ]
        with pytest.raises(ValidationError, match="whole number"):
            validator.validate(params, {"count": "not-a-number"})
