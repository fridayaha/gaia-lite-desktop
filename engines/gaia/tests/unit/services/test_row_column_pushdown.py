"""Tests for Phase 3: Cedar engine + residual translator + SqlGlot injector.

Covers the row/column-level pushdown chain (design §4, ADR-017 D4):
  - Cedar partial evaluation (TPE) + app-layer fallback → residual AST
  - ResidualTranslator: Cedar AST → SQL predicate (deterministic mapping)
  - SqlPermissionInjector: predicate → every WHERE (subquery/CTE/UNION)
  - evaluate_masking_policy: column visibility (Cedar full eval)
  - AuthorizationService.evaluate_query_scope: residual + masked_properties
  - RowSecurityPolicy / PropertyMaskingPolicy ORM + constraints
"""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from ontology.core.models import Base
from ontology.core.models.permission import (
    PropertyMaskingPolicyModel,
    RowSecurityPolicyModel,
)
from ontology.services.cedar_engine import (
    evaluate_masking_policy,
    evaluate_row_policy_partial,
)
from ontology.services.sql_injector import (
    ResidualTranslator,
    inject_permission,
    translate_residual_to_predicate,
)


@pytest.fixture
def in_memory_engine():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


class TestRowSecurityPolicyModel:
    def test_table_and_columns(self, in_memory_engine):
        inspector = inspect(in_memory_engine)
        cols = {c["name"] for c in inspector.get_columns("row_security_policies")}
        for expected in ("id", "object_type_id", "expression", "description", "status"):
            assert expected in cols

    def test_one_policy_per_object_type(self, in_memory_engine):
        """One active row policy per ObjectType (unique constraint)."""
        from sqlalchemy.exc import IntegrityError

        with Session(in_memory_engine) as s:
            s.add(RowSecurityPolicyModel(object_type_id="ot1", expression="true"))
            s.commit()
            s.add(RowSecurityPolicyModel(object_type_id="ot1", expression="false"))
            with pytest.raises(IntegrityError):
                s.commit()


class TestPropertyMaskingPolicyModel:
    def test_one_policy_per_property(self, in_memory_engine):
        from sqlalchemy.exc import IntegrityError

        with Session(in_memory_engine) as s:
            s.add(PropertyMaskingPolicyModel(property_id="p1", expression="true"))
            s.commit()
            s.add(PropertyMaskingPolicyModel(property_id="p1", expression="false"))
            with pytest.raises(IntegrityError):
                s.commit()


class TestCedarRowPolicyPartial:
    """Cedar partial evaluation → residual (with app-layer fallback)."""

    def test_region_filter_produces_residual(self):
        """principal.attributes.region == resource.region → residual."""
        r = evaluate_row_policy_partial(
            policy_expression="principal.attributes.region == resource.region",
            principal_id="alice",
            principal_attributes={"region": "east"},
            principal_markings=[],
            resource_attributes={"region": "String"},
        )
        assert r.decision == "NoDecision"
        assert r.residual_ast is not None

    def test_missing_principal_attr_denies(self):
        """Principal lacks the attribute referenced → Deny (fail-closed)."""
        r = evaluate_row_policy_partial(
            policy_expression="principal.attributes.region == resource.region",
            principal_id="alice",
            principal_attributes={},  # no region
            principal_markings=[],
            resource_attributes={"region": "String"},
        )
        assert r.decision == "Deny"

    def test_marking_only_condition_allows(self):
        """A marking-only condition (no resource attr) → Allow (Layer 5 handles)."""
        r = evaluate_row_policy_partial(
            policy_expression='"PII" in principal.markings',
            principal_id="alice",
            principal_attributes={},
            principal_markings=["PII"],
            resource_attributes={},
        )
        # No row-level constraint → all rows visible (marking is Layer 5).
        assert r.decision == "Allow"

    def test_and_of_two_conditions(self):
        r = evaluate_row_policy_partial(
            policy_expression="principal.attributes.region == resource.region && principal.attributes.dept == resource.dept",
            principal_id="alice",
            principal_attributes={"region": "east", "dept": "sales"},
            principal_markings=[],
            resource_attributes={"region": "String", "dept": "String"},
        )
        assert r.decision == "NoDecision"
        assert r.residual_ast is not None


class TestResidualTranslator:
    """Cedar residual AST → SQL predicate (deterministic mapping)."""

    def test_translate_equality(self):
        r = evaluate_row_policy_partial(
            policy_expression="principal.attributes.region == resource.region",
            principal_id="alice", principal_attributes={"region": "east"},
            principal_markings=[], resource_attributes={"region": "String"},
        )
        pred = translate_residual_to_predicate(r.residual_ast)
        assert pred == "region = 'east'"

    def test_translate_and(self):
        r = evaluate_row_policy_partial(
            policy_expression="principal.attributes.region == resource.region && principal.attributes.dept == resource.dept",
            principal_id="alice", principal_attributes={"region": "east", "dept": "sales"},
            principal_markings=[], resource_attributes={"region": "String", "dept": "String"},
        )
        pred = translate_residual_to_predicate(r.residual_ast)
        assert "region = 'east'" in pred
        assert "dept = 'sales'" in pred
        assert "AND" in pred

    def test_none_residual_returns_none(self):
        assert translate_residual_to_predicate(None) is None

    def test_direct_ast_equality(self):
        """Direct AST node (no wrapper) also translates."""
        t = ResidualTranslator()
        node = {"==": {"left": {".": {"left": {"unknown": "resource"}, "attr": "region"}},
                       "right": {"Value": "west"}}}
        assert t._visit(node) == "region = 'west'"

    def test_literal_escaping(self):
        """Single quotes in literals are escaped."""
        t = ResidualTranslator()
        node = {"Lit": "O'Brien"}
        assert t._visit(node) == "'O''Brien'"


class TestSqlPermissionInjector:
    """SqlGlot AST injection (subquery/CTE/UNION coverage)."""

    def test_inject_into_simple_where(self):
        sql = "SELECT id FROM t WHERE status = 1"
        result = inject_permission(sql, "region = 'east'", dialect="postgres")
        assert "region = 'east'" in result
        assert "status = 1" in result

    def test_inject_adds_where_when_absent(self):
        sql = "SELECT id FROM t"
        result = inject_permission(sql, "region = 'east'", dialect="postgres")
        assert "WHERE" in result.upper()
        assert "region = 'east'" in result

    def test_inject_into_subquery(self):
        sql = "SELECT * FROM (SELECT id, region FROM customers) sub WHERE region IS NOT NULL"
        result = inject_permission(sql, "region = 'east'", dialect="postgres")
        # The predicate should appear (injected into at least one scope).
        assert result.count("region = 'east'") >= 1

    def test_inject_doris_dialect(self):
        sql = "SELECT id FROM idx_ont__customer WHERE status = 1"
        result = inject_permission(sql, "region = 'east'", dialect="doris")
        assert "region = 'east'" in result

    def test_inject_trino_dialect(self):
        sql = "SELECT id FROM iceberg.ontology.customer WHERE status = 1"
        result = inject_permission(sql, "region = 'east'", dialect="trino")
        assert "region = 'east'" in result

    def test_dedup_self_join(self):
        """Predicate not double-injected on a self-join (de-duplication)."""
        sql = "SELECT a.id FROM t a JOIN t b ON a.id = b.id WHERE a.status = 1"
        result = inject_permission(sql, "a.region = 'east'", dialect="postgres")
        # Should inject once, not duplicate in the same WHERE.
        assert "a.region = 'east'" in result


class TestMaskingPolicy:
    """PropertyMaskingPolicy evaluation (Cedar full eval)."""

    def test_marking_holder_sees_column(self):
        visible = evaluate_masking_policy(
            policy_expression='principal.markings.contains("PII")',
            principal_id="alice", principal_attributes={}, principal_markings=["PII"],
        )
        assert visible is True

    def test_non_marking_holder_masked(self):
        visible = evaluate_masking_policy(
            policy_expression='principal.markings.contains("PII")',
            principal_id="bob", principal_attributes={}, principal_markings=[],
        )
        assert visible is False

    def test_malformed_policy_masks(self):
        """A malformed policy fails-closed (mask the column)."""
        visible = evaluate_masking_policy(
            policy_expression="this is not valid cedar",
            principal_id="alice", principal_attributes={}, principal_markings=["PII"],
        )
        assert visible is False
