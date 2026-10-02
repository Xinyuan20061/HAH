import os
from pathlib import Path
from uuid import uuid4

os.environ["ENV"] = "test"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, build_engine, get_db
from app.core.security import create_access_token
from app.main import app
from app.models import User


@pytest.fixture(scope="session")
def migrated_engine(tmp_path_factory):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        if make_url(url).database != "healthmate_audit_tests":
            raise ValueError("TEST_DATABASE_URL 必须使用专用 healthmate_audit_tests 库")
    else:
        url = "sqlite:///" + (tmp_path_factory.mktemp("database") / "api.db").as_posix()
    previous = settings.database_url
    settings.database_url = url
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")
    settings.database_url = previous
    engine = build_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def api(migrated_engine, monkeypatch):
    with migrated_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            # The exercise-effect ontology is immutable migration seed data, not
            # per-test/user state. Keep it just as a deployed database would.
            if table.name in {
                "fitness_relations",
                "fitness_concepts",
                "dataset_registry",
                "model_registry",
            }:
                continue
            connection.execute(table.delete())
    import app.core.diagnostics as diagnostics
    import app.main as main

    monkeypatch.setattr(main, "engine", migrated_engine)
    monkeypatch.setattr(diagnostics, "engine", migrated_engine)
    monkeypatch.setattr(settings, "worker_token", "test-worker-token")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setattr(settings, "local_llm_model_dir", "")
    monkeypatch.setattr(settings, "env", "test")
    # Deterministic isolated tests: the V2 background stage consumer would claim
    # rows across tests; disable it (stage_tasks are exercised directly instead).
    monkeypatch.setattr(settings, "motion_stage_consumer_enabled", False)
    # Deterministic isolated tests: the in-memory rate limiter is a production
    # guard, not behaviour under test; repeated /agent/ calls must not 429.
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    # Keep repository-local deployment settings from leaking into isolated tests.
    # Test media fixtures deliberately use the synthetic test.env CloudBase id.
    monkeypatch.setattr(settings, "cloudbase_env_id", "test.env")

    # Each request uses an independent transaction/session, matching deployed FastAPI.
    def dependency():
        with Session(migrated_engine) as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    with Session(migrated_engine) as db:
        user = User(openid="audit-" + uuid4().hex)
        db.add(user)
        db.commit()
        user_id = user.id
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + create_access_token(str(user_id))
        client.user_id = user_id
        client.worker_headers = {"X-Worker-Token": settings.worker_token}
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def db(migrated_engine):
    """Direct session on the migrated test database.

    Rolls back after the test: the ``api`` fixture writes through its own
    connection, and leaving an open SQLite read transaction would both lock the
    file and hide rows committed by the API.
    """
    with Session(migrated_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def food_result():
    return {
        "dish_name": "测试餐食",
        "calories": 300.0,
        "protein": 20.0,
        "carbs": 30.0,
        "fat": 12.0,
        "confidence": 0.5,
        "tips": ["请按真实份量校正"],
    }
