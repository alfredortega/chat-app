"""
Tests for the Alembic migration tooling.

Migrations are forward tooling only — the app still bootstraps at runtime via
``db.create_all()`` + self-heal helpers. The baseline migration must recreate
the full model schema on a fresh database, and a migrated database must remain
usable by the application factory.
"""

import os

from alembic import command
from alembic.config import Config as AlembicConfig

import database as db_module

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALEMBIC_INI = os.path.join(BASE, "alembic.ini")
MIGRATIONS_DIR = os.path.join(BASE, "migrations")

REQUIRED_TABLES = {
    "folders", "personas", "endpoints", "conversations", "messages",
    "conv_files", "linked_folders", "settings", "research_sources",
    "artifacts", "artifact_deps", "artifact_traces", "artifact_assumptions",
    "change_events", "propagation_jobs", "agent_issues", "artifact_requests",
}


def _table_names(db_path: str) -> set:
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _run_alembic(db_uri: str, fn) -> None:
    cfg = AlembicConfig(ALEMBIC_INI)
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    old = os.environ.get("ALEMBIC_DATABASE_URL")
    os.environ["ALEMBIC_DATABASE_URL"] = db_uri
    try:
        fn(cfg, db_uri)
    finally:
        if old is None:
            os.environ.pop("ALEMBIC_DATABASE_URL", None)
        else:
            os.environ["ALEMBIC_DATABASE_URL"] = old


class TestMigrations:
    def test_upgrade_head_creates_full_schema(self, tmp_path):
        db_path = str(tmp_path / "migrated.db")
        open(db_path, "w").close()  # sqlite file must exist for empty DB
        db_uri = f"sqlite:///{db_path}"

        _run_alembic(db_uri, lambda cfg, _u: command.upgrade(cfg, "head"))

        tables = _table_names(db_path)
        missing = REQUIRED_TABLES - tables
        assert not missing, f"migration missing tables: {sorted(missing)}"

    def test_migrated_db_works_with_app_factory(self, tmp_path):
        db_path = str(tmp_path / "app.db")
        open(db_path, "w").close()
        db_uri = f"sqlite:///{db_path}"

        _run_alembic(db_uri, lambda cfg, _u: command.upgrade(cfg, "head"))

        # App factory + init_db must tolerate an already-migrated schema.
        from app import create_app
        app = create_app(config={
            "SQLALCHEMY_DATABASE_URI": db_uri,
            "ENCRYPTION_KEY": "test-encryption-key-must-be-32-bytes-long-!!!!",
            "SKIP_DOTENV_WRITE": "1",
        })
        with app.app_context():
            db_module.init_db(app)
            assert db_module.create_folder("Migrated OK")["id"] > 0

    def test_downgrade_to_base_then_reupgrade(self, tmp_path):
        db_path = str(tmp_path / "cycle.db")
        open(db_path, "w").close()
        db_uri = f"sqlite:///{db_path}"

        _run_alembic(db_uri, lambda cfg, _u: command.upgrade(cfg, "head"))
        assert REQUIRED_TABLES <= _table_names(db_path)

        _run_alembic(db_uri, lambda cfg, _u: command.downgrade(cfg, "base"))
        assert not REQUIRED_TABLES & _table_names(db_path)

        _run_alembic(db_uri, lambda cfg, _u: command.upgrade(cfg, "head"))
        assert REQUIRED_TABLES <= _table_names(db_path)