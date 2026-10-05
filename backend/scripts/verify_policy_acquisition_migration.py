"""Run the acquisition migration, schema drift check, and reversible downgrade in isolation."""

from pathlib import Path
import sys
import tempfile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings
from app.core.database import Base, build_engine
import app.models  # noqa: F401 - register all mapped tables in metadata


ACQUISITION_TABLES = {
    "policy_observation_refs", "policy_acquisition_sessions", "policy_acquisition_questions",
    "policy_acquisition_commands", "policy_acquisition_daily_usage", "policy_acquisition_events",
    "policy_decision_certificates", "policy_certificate_dependencies",
    "policy_evidence_revisions", "policy_acquisition_fences",
}


def assert_no_acquisition_schema_drift(engine) -> int:
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    relevant = [operation for operation in differences
                if any(f"'{table_name}'" in repr(operation) or f'"{table_name}"' in repr(operation)
                       for table_name in ACQUISITION_TABLES)]
    assert not relevant, f"acquisition ORM/migration drift: {relevant!r}"
    return len(differences)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    previous = (settings.env, settings.database_url)
    with tempfile.TemporaryDirectory(prefix="healthmate-acquisition-migration-") as directory:
        database_url = "sqlite:///" + (Path(directory) / "acquisition.db").as_posix()
        settings.env = "test"
        settings.database_url = database_url
        engine = build_engine(database_url)
        try:
            command.upgrade(config, "head")
            unrelated_drift = assert_no_acquisition_schema_drift(engine)
            expected_tables = {
                "policy_acquisition_sessions", "policy_acquisition_questions",
                "policy_acquisition_commands", "policy_acquisition_daily_usage",
                "policy_acquisition_events", "policy_decision_certificates",
                "policy_certificate_dependencies", "policy_evidence_revisions",
                "policy_acquisition_fences",
            }
            assert expected_tables <= set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                assert MigrationContext.configure(connection).get_current_revision() == "0038_low_burden_evidence_acquisition"
            command.downgrade(config, "0037_policy_decision_idempotency")
            tables_after_downgrade = set(inspect(engine).get_table_names())
            assert not expected_tables.intersection(tables_after_downgrade)
            assert "revision" not in {column["name"] for column in inspect(engine).get_columns("policy_observation_refs")}
            command.upgrade(config, "head")
            unrelated_drift = max(unrelated_drift, assert_no_acquisition_schema_drift(engine))
            print(f"[OK] isolated 0038 upgrade -> targeted schema check -> downgrade -> upgrade -> targeted schema check; unrelated existing metadata diffs={unrelated_drift}")
        finally:
            engine.dispose()
            settings.env, settings.database_url = previous


if __name__ == "__main__":
    main()
