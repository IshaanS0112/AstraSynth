"""Migrations and models must describe the same schema.

The failure this exists to prevent is quiet. Someone adds a column to a model;
the tests bootstrap with ``create_all`` and pass; the deployment runs
``alembic upgrade head`` and gets a table without that column. Nothing errors
until the first query touches it, in production, at which point the fix is a
hotfix migration written under pressure.

So the check is mechanical: build a database from the migrations alone, then ask
Alembic to autogenerate against the models. A non-empty result means they have
diverged, and the test prints what Alembic thinks is missing.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

ADMIN_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
)


def _database_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(ADMIN_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason=f"PostgreSQL not reachable at {ADMIN_URL}"
)


@pytest.fixture(scope="module")
def scratch_database():
    """A throwaway database, so the migrations run against nothing at all.

    Reusing the main test database would test whether migrations apply *on top
    of* a schema ``create_all`` already built, which is a different and much
    weaker claim.
    """
    from sqlalchemy import create_engine, text

    name = f"astra_mig_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    try:
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
                )
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def alembic_config(url: str):
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


class TestMigrations:
    def test_they_build_the_schema_from_nothing(self, scratch_database):
        from sqlalchemy import create_engine, inspect

        from alembic import command

        command.upgrade(alembic_config(scratch_database), "head")

        tables = set(inspect(create_engine(scratch_database)).get_table_names())
        assert {
            "missions",
            "terrain_analyses",
            "rover_configs",
            "rover_paths",
            "mission_risk_reports",
            "science_targets",
            "traverse_runs",
            "experiments",
            "jobs",
        } <= tables

    def test_models_and_migrations_agree(self, scratch_database):
        """The drift guard. A non-empty diff here is the bug this file exists for."""
        from sqlalchemy import create_engine

        import app.models  # noqa: F401 - registers every table on Base.metadata
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from app.db.session import Base

        command.upgrade(alembic_config(scratch_database), "head")

        engine = create_engine(scratch_database)
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            differences = compare_metadata(context, Base.metadata)

        assert not differences, (
            "models and migrations have diverged; run\n"
            "  alembic revision --autogenerate -m '<what changed>'\n"
            f"Alembic reports: {differences}"
        )

    def test_the_head_is_reachable_and_singular(self):
        """Two heads mean two branches merged badly and `upgrade head` is ambiguous."""
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(alembic_config(ADMIN_URL)).get_heads()
        assert len(heads) == 1, f"expected one migration head, found {heads}"

    def test_downgrade_is_defined_all_the_way_back(self, scratch_database):
        """A migration you cannot reverse is a migration you cannot deploy twice."""
        from sqlalchemy import create_engine, inspect

        from alembic import command

        config = alembic_config(scratch_database)
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        remaining = set(inspect(create_engine(scratch_database)).get_table_names())
        assert "missions" not in remaining
        assert "jobs" not in remaining


class TestMigratingDoesNotDisturbTheProcess:
    """Applying a migration must not reconfigure the application's logging.

    Alembic's ``env.py`` calls ``fileConfig`` on ``alembic.ini``, which is right
    for the CLI and destructive in-process: it defaults to
    ``disable_existing_loggers=True``, and that file sets the root logger to
    WARNING with a plain console handler. Because the API applies migrations on
    boot by default, the effect was that every structured request log line
    disappeared for the life of the process - the JSON handler was replaced and
    the level was raised above INFO. Nothing failed, and there was no error to
    find: the logs were simply gone.

    Both assertions matter. The handler identity catches the formatter being
    swapped; the level catches INFO being filtered out even if a handler survives.
    """

    def test_the_configured_handler_and_level_survive(self, scratch_database):
        import logging

        from app.db.migrate import upgrade_to_head
        from app.observability import configure_logging

        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        try:
            configure_logging("INFO", json_logs=True)
            handler_before = root.handlers[0]

            upgrade_to_head(scratch_database)

            assert root.handlers == [handler_before], (
                "alembic replaced the application's log handler; "
                "see alembic/env.py and the configure_logger attribute"
            )
            assert root.level == logging.INFO
            assert root.isEnabledFor(logging.INFO)
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

    def test_the_cli_path_still_configures_its_own_logging(self):
        """The guard must not disable alembic's logging for actual CLI use.

        ``configure_logger`` defaults to True, so a plain ``alembic upgrade head``
        is unaffected; only a caller that sets it False opts out. Asserted on the
        attribute rather than by running the CLI, because what is being checked is
        which side of the branch each caller lands on.
        """
        from app.db.migrate import _alembic_config

        programmatic = _alembic_config("postgresql+psycopg2://unused/none")
        assert programmatic.attributes["configure_logger"] is False

        from alembic.config import Config

        assert Config(str(BACKEND / "alembic.ini")).attributes.get("configure_logger") is None
