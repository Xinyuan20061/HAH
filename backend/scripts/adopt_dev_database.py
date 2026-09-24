"""Stamp only a development SQLite schema matching a migrated reference database.

Production and uncertain legacy schemas are refused. Back up the database first.
"""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect
from alembic.config import Config
from alembic import command
from alembic.script import ScriptDirectory
from app.core.database import engine, build_engine
from app.core.config import settings


def signature(connection):
    inspector = inspect(connection)
    result = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        columns = {
            (c["name"], str(c["type"]), bool(c["nullable"]), c.get("default"))
            for c in inspector.get_columns(table)
        }
        foreign_keys = {
            (
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in inspector.get_foreign_keys(table)
        }
        unique = {
            tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)
        }
        unique |= {
            tuple(i["column_names"])
            for i in inspector.get_indexes(table)
            if i["unique"]
        }
        primary = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
        indexes = {tuple(i["column_names"]) for i in inspector.get_indexes(table)}
        result[table] = (columns, foreign_keys, unique, primary, indexes)
    return result


def main():
    if (
        settings.env.lower() not in {"development", "test"}
        or engine.dialect.name != "sqlite"
    ):
        print(
            "Refused: legacy adoption is for development/test SQLite only. Production needs reviewed migrations and a backup."
        )
        return 2
    tables = set(inspect(engine).get_table_names())
    if not tables or "alembic_version" in tables:
        print("No adoption required. Run alembic current / alembic upgrade head.")
        return 0
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    revisions = list(
        reversed(list(ScriptDirectory.from_config(config).walk_revisions()))
    )
    actual = signature(engine)
    original_url = settings.database_url
    matched = None
    with tempfile.TemporaryDirectory(
        prefix="healthmate-schema-reference-"
    ) as directory:
        url = "sqlite:///" + (Path(directory) / "reference.db").as_posix()
        reference = build_engine(url)
        try:
            settings.database_url = url
            for revision in revisions:
                command.upgrade(config, revision.revision)
                if signature(reference) == actual:
                    matched = revision.revision
        finally:
            settings.database_url = original_url
            reference.dispose()
    if not matched:
        print(
            "Refused: schema does not exactly match a migration revision (columns, nullability, keys, indexes). No business database changes made."
        )
        return 2
    command.stamp(config, matched)
    print(
        f"Stamped verified development schema at {matched}. Next: alembic upgrade head"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
