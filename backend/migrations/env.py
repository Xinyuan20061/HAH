from logging.config import fileConfig

from alembic import context

from app.core.config import settings
from app.core.database import Base
from app.models import *

config = context.config
settings.validate_configuration()
config.set_main_option(
    "sqlalchemy.url", settings.effective_database_url.replace("%", "%%")
)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=settings.effective_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    from app.core.database import build_engine

    connectable = build_engine(settings.effective_database_url)
    try:
        with connectable.connect() as connection:
            sqlite_fk_check = connection.dialect.name == "sqlite"
            if sqlite_fk_check:
                # SQLite cannot alter a referenced parent table in place. Batch
                # migrations recreate it, so defer enforcement for this migration
                # connection and verify every relationship before committing.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
                    connection.rollback()
                    raise RuntimeError("Could not disable SQLite foreign keys for migration")
                connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            try:
                with context.begin_transaction():
                    context.run_migrations()
                    if sqlite_fk_check:
                        violations = connection.exec_driver_sql(
                            "PRAGMA foreign_key_check"
                        ).all()
                        if violations:
                            raise RuntimeError(
                                "SQLite migrations would leave foreign-key violations: "
                                f"{violations[:5]}"
                            )
            finally:
                if sqlite_fk_check:
                    connection.commit()
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
                    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                        raise RuntimeError("Could not restore SQLite foreign-key enforcement")
                    connection.commit()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
