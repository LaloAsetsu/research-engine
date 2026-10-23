"""The catalog of question-set versions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("md5", sa.String(length=32), nullable=False),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("corpus", sa.String(length=128), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('qset')", name=op.f("ck_artifacts_kind")),
        sa.CheckConstraint("status IN ('draft', 'frozen')", name=op.f("ck_artifacts_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint("kind", "name", "version", name="uq_artifacts_kind_name_version"),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
