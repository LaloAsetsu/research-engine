"""Alembic environment. The engine comes from ``Database`` (``config.attributes["engine"]``)."""

from __future__ import annotations

from alembic import context

from research_api.adapters.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = config.attributes["engine"]
    with engine.connect() as connection:
        # SQLite cannot ALTER most things; batch mode copies the table instead.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
