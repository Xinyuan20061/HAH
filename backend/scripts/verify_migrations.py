"""Fresh -> legacy rows -> head -> repeated head, in a dedicated audit database only."""

import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text, inspect
from sqlalchemy.engine import make_url
from app.core.database import build_engine
from app.core.config import settings


def main():
    target = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    with tempfile.TemporaryDirectory(prefix="healthmate-migrations-") as directory:
        target = (
            target or "sqlite:///" + (Path(directory) / "incremental.db").as_posix()
        )
        if (
            not target.startswith("sqlite")
            and make_url(target).database != "healthmate_incremental"
        ):
            raise ValueError("Use dedicated healthmate_incremental database")
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        if not head:
            raise RuntimeError("Alembic migration graph has no head")
        engine = build_engine(target)
        try:
            if inspect(engine).get_table_names():
                raise ValueError("Migration audit requires an empty dedicated database")
            settings.env, settings.database_url = "test", target
            command.upgrade(config, "0003_health_goals")
            now = datetime(2026, 1, 1)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id,openid,nickname,avatar_url,created_at,updated_at) "
                        "VALUES (1,'incremental-audit',:name,'',:now,:now)"
                    ),
                    {"name": "迁移保留测试", "now": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO diet_records (user_id,name,meal_type,calories,protein,carbs,fat,recorded_at,source,created_at,updated_at) "
                        "VALUES (1,'legacy meal','other',321,20,30,10,:now,'manual',:now,:now)"
                    ),
                    {"now": now},
                )
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            command.current(config)
            with engine.connect() as connection:
                assert MigrationContext.configure(connection).get_current_revision() == head
                assert (
                    connection.execute(
                        text("SELECT nickname FROM users WHERE id=1")
                    ).scalar()
                    == "迁移保留测试"
                )
                assert (
                    connection.execute(
                        text("SELECT calories FROM diet_records WHERE user_id=1")
                    ).scalar()
                    == 321
                )
                assert (
                    connection.execute(
                        text("SELECT portion FROM diet_records WHERE user_id=1")
                    ).scalar()
                    == ""
                )
            print(
                f"[OK] {engine.dialect.name}: legacy 0003 rows preserved, head={head}, repeated upgrade unchanged"
            )
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
