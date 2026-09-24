from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings


def build_engine(url: str):
    parsed = make_url(url)
    kwargs = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
    elif parsed.get_backend_name() == "mysql":
        parsed = parsed.update_query_dict({"charset": "utf8mb4"})
        kwargs.update(
            pool_recycle=1800,
            connect_args={
                "connect_timeout": 5,
                "read_timeout": 10,
                "write_timeout": 10,
            },
        )
    result = create_engine(parsed, **kwargs)
    if parsed.get_backend_name() == "sqlite":

        @event.listens_for(result, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return result


engine = build_engine(
    settings.effective_database_url
    if not settings.configuration_errors()
    else "mysql+pymysql://invalid-configuration/healthmate"
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
