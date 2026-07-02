"""add job idempotency/duplicate-request tracking fields

Revision ID: 0002_job_idempotency
Revises: 0001_initial
Create Date: 2026-07-03

Adds request fingerprinting + duplicate-request bookkeeping to ``ai_jobs`` so
``POST /internal/jobs`` can distinguish a genuine duplicate submission (same
fingerprint) from a job_id reused for a different request (different
fingerprint -> 409 conflict), and can safely no-op instead of re-enqueuing an
already-running job.

``job_id`` already has a unique index via its primary key (from migration
0001), so no separate unique index is added here.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_job_idempotency"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("ai_jobs", sa.Column("request_fingerprint_version", sa.Integer(), nullable=True))
    op.add_column("ai_jobs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column(
        "ai_jobs", sa.Column("last_duplicate_request_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "ai_jobs",
        sa.Column("duplicate_request_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_index("ix_ai_jobs_fingerprint", "ai_jobs", ["request_fingerprint"])
    op.create_index(
        "ix_ai_jobs_tenant_project_job", "ai_jobs", ["tenant_id", "project_id", "job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_tenant_project_job", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_fingerprint", table_name="ai_jobs")

    op.drop_column("ai_jobs", "duplicate_request_count")
    op.drop_column("ai_jobs", "last_duplicate_request_at")
    op.drop_column("ai_jobs", "idempotency_key")
    op.drop_column("ai_jobs", "request_fingerprint_version")
    op.drop_column("ai_jobs", "request_fingerprint")
