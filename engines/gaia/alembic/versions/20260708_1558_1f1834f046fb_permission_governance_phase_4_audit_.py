"""permission governance phase 4: audit logs + access requests (JIT)

Revision ID: 1f1834f046fb
Revises: a09c49b64c4e
Create Date: 2026-07-08 15:58:13.234244+00:00

ADR-016/017 Phase 4: audit trail + JIT access requests.

Creates:
  - ``audit_logs``: append-only permission decision log. Every
    AuthorizationService.check_access decision is recorded (principal,
    resource, action, result, layer, reason). The application exposes ONLY
    append() — no UPDATE/DELETE (immutability, design §1.6). The ``layer``
    field powers the Check Access debug panel + audit analysis.
  - ``access_requests``: JIT permission self-service requests (PENDING →
    APPROVED/REJECTED/EXPIRED). Approved requests create a time-limited
    RoleAssignment/MarkingGrant (auto-revoked on expiry). Reduces standing
    high privileges and zombie permissions (design §7.1).

Both tables are indexed for the query patterns the Check Access panel and
audit viewer use (by principal, by resource, by status, by time range).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f1834f046fb"
down_revision: str | Sequence[str] | None = "a09c49b64c4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_logs: append-only (no UPDATE/DELETE from the application).
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("principal_id", sa.String(length=32), nullable=True),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("result", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("layer", sa.String(length=20), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_timestamp"), "audit_logs", ["timestamp"], unique=False)
    op.create_index(op.f("ix_audit_logs_principal_id"), "audit_logs", ["principal_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_result"), "audit_logs", ["result"], unique=False)
    op.create_index(op.f("ix_audit_logs_layer"), "audit_logs", ["layer"], unique=False)
    op.create_index(op.f("ix_audit_logs_request_id"), "audit_logs", ["request_id"], unique=False)

    # access_requests: JIT self-service permission requests.
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("requester_id", sa.String(length=32), nullable=False),
        sa.Column("request_type", sa.String(length=20), nullable=False),
        sa.Column("requested_item", sa.String(length=100), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_id", sa.String(length=32), nullable=True),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("reviewer_id", sa.String(length=32), nullable=True),
        sa.Column("review_comment", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_access_requests_requester_id"), "access_requests",
                    ["requester_id"], unique=False)
    op.create_index(op.f("ix_access_requests_status"), "access_requests",
                    ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_access_requests_status"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_requester_id"), table_name="access_requests")
    op.drop_table("access_requests")
    op.drop_index(op.f("ix_audit_logs_request_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_layer"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_result"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_principal_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_timestamp"), table_name="audit_logs")
    op.drop_table("audit_logs")
