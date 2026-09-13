"""Alembic environment — wired to the application's SQLAlchemy metadata.

Migrations are not wired into app start-up on purpose: ``db.create_all()`` +
the self-heal helpers remain the runtime path so existing installs keep
working unchanged. Alembic is the *forward* tooling: schema changes land here
first as a codified, reviewable migration.

Set ``ALEMBIC_DATABASE_URL`` to point migrations at a specific database
(default: the app's SQLite path).
"""

import os
from logging.config import fileConfig

from alembic import context

import database as db_module

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = (
    os.environ.get("ALEMBIC_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
    or f"sqlite:///{db_module.DB_PATH}"
)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = db_module.db.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    connectable = create_engine(config.get_main_option("sqlalchemy.url"))
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()