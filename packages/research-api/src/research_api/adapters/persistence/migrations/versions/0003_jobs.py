"""The benchmark jobs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.String(length=128), nullable=True),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint("kind IN ('run', 'score')", name=op.f("ck_jobs_kind")),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'cancelling', 'cancelled', 'done', 'failed',"
            " 'interrupted')",
            name=op.f("ck_jobs_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_state_kind_queued_at", "jobs", ["state", "kind", "queued_at"])
    op.create_index("ix_jobs_owner_state", "jobs", ["owner", "state"])


def downgrade() -> None:
    op.drop_index("ix_jobs_owner_state", table_name="jobs")
    op.drop_index("ix_jobs_state_kind_queued_at", table_name="jobs")
    op.drop_table("jobs")
