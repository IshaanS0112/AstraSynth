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
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Default to the application's database, but never override a URL the caller
# supplied. Clobbering it would mean `alembic -x` and programmatic use could not
# target a scratch database - and the migration test, which builds a throwaway
# database precisely to check the migrations in isolation, would silently run
# against the live one instead.
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
            # Without compare_type a widened column silently produces an empty
            # migration, and the drift test would pass while the schema diverged.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
