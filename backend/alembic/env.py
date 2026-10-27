"""Alembic environment.

The database URL is **not** in ``alembic.ini``. It defaults to the same
``Settings`` object the application uses, so a bare ``alembic upgrade head``
cannot be pointed at a different database than the one the API is about to talk
to - which is how a migration ends up applied to the wrong environment. A caller
that sets the URL explicitly keeps it, so targeting a scratch database is still
possible when that is what you actually mean.

``target_metadata`` is the application's own ``Base.metadata``, which is what
makes ``--autogenerate`` meaningful and what
``tests/test_migrations.py::test_models_and_migrations_agree`` compares against.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.models  # noqa: F401  - import for the side effect of registering every table
from app.config import get_settings
from app.db.session import Base

config = context.config

# fileConfig is for the CLI. In-process it is destructive: it disables existing
# loggers and alembic.ini sets root to WARNING, which used to switch off every
# structured log line for the life of the process. `configure_logger` is
# alembic's hook - the CLI leaves it unset, app/db/migrate.py sets it False.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Default to the app's database but never override a caller's URL, or the
# migration test's throwaway database would silently become the live one.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without these a widened column or changed default produces an
            # empty migration and the drift test passes while the schema diverges.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
