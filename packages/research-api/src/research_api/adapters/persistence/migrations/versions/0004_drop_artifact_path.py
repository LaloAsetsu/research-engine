"""Drop the unused path column of the artifacts catalog.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 01:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("path")


def downgrade() -> None:
    # Nullable: the old values are gone, so a row cannot get one back.
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("path", sa.String(length=255), nullable=True))
