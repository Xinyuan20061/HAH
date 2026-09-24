from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.database import build_engine
from scripts import adopt_dev_database as adoption


def test_known_legacy_development_schema_is_verified_before_stamp(
    tmp_path, monkeypatch
):
    url = "sqlite:///" + (tmp_path / "legacy.db").as_posix()
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "database_url", url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0003_health_goals")
    engine = build_engine(url)
    monkeypatch.setattr(adoption, "engine", engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
        assert adoption.main() == 0
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0003_health_goals"
            )
    finally:
        engine.dispose()


def test_partial_legacy_schema_is_not_guessed(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "partial.db").as_posix()
    engine = build_engine(url)
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(adoption, "engine", engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        assert adoption.main() == 2
        assert inspect(engine).get_table_names() == ["users"]
    finally:
        engine.dispose()


def test_production_legacy_adoption_is_forbidden(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    assert adoption.main() == 2
