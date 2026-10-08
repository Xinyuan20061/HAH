import os
import json
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


_KNOWLEDGE_MIGRATION_SEED: list[dict] = []


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
    with engine.connect() as connection:
        knowledge_table = Base.metadata.tables["knowledge_documents"]
        _KNOWLEDGE_MIGRATION_SEED[:] = [
            dict(row) for row in connection.execute(knowledge_table.select()).mappings().all()
        ]
    yield engine
    engine.dispose()


@pytest.fixture
def api(migrated_engine, monkeypatch, request):
    with migrated_engine.begin() as connection:
        mysql_fk_checks = connection.dialect.name == "mysql"
        if mysql_fk_checks:
            # MySQL rejects DELETE FROM a table with self-referential rows even
            # when the whole table is being reset. This fixture only targets the
            # dedicated healthmate_audit_tests schema, so suspend checks on this
            # connection while clearing mutable test rows and restore them before
            # any API request uses the engine.
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table in reversed(Base.metadata.sorted_tables):
                # Immutable migration seed data, not per-test/user state. Keep it just
                # as a deployed database would.
                # ``food_references`` belongs here too: it is the audited nutrition
                # table shipped with the application, and wiping it silently broke every
                # dependent calculation (deterministic totals fell back to zero).
                if table.name in {
                    "fitness_relations",
                    "fitness_concepts",
                    "dataset_registry",
                    "model_registry",
                    "food_references",
                    "knowledge_documents",
                }:
                    continue
                connection.execute(table.delete())
            knowledge_table = Base.metadata.tables["knowledge_documents"]
            connection.execute(knowledge_table.delete())
            if _KNOWLEDGE_MIGRATION_SEED:
                connection.execute(knowledge_table.insert(), _KNOWLEDGE_MIGRATION_SEED)
        finally:
            if mysql_fk_checks:
                connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
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
    # Route tests exercise Android endpoints under an explicitly enabled test
    # profile; production defaults remain disabled until deployment config opts in.
    monkeypatch.setattr(settings, "mobile_auth_enabled", True)
    monkeypatch.setattr(settings, "mobile_upload_enabled", True)

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
        # Most legacy tests exercise an already-consented product surface. The
        # dedicated plugin tests intentionally omit this seed so they can prove
        # that missing consent is fail-closed.
        if not request.module.__name__.endswith("test_harness_plugins"):
            from app.harness.plugins import BUILTIN_PLUGINS
            from app.models import HarnessPluginInstallation
            from app.core.time import utc_now
            for manifest in BUILTIN_PLUGINS:
                # Legacy domain tests represent users who had already reviewed
                # and consented to the full built-in capability. Dedicated
                # capability tests intentionally start with no grant. Keep this
                # compatibility fixture explicit; production defaults remain
                # deny-by-default and are not changed by the test setup.
                legacy_consent = {
                    "goal": manifest.goals[0][0] if manifest.goals else "daily_guidance",
                    "data_scopes": [key for key, _ in manifest.scope_definitions],
                    "allow_action_proposals": bool(manifest.may_propose_actions),
                    "notification_frequency": "on_request",
                }
                db.add(HarnessPluginInstallation(
                    user_id=user_id,
                    plugin_id=manifest.plugin_id,
                    plugin_version=manifest.version,
                    enabled=True,
                    config_json=json.dumps(legacy_consent, ensure_ascii=False),
                    enabled_at=utc_now(),
                    config_version=1,
                    reviewed_manifest_hash=manifest.manifest_hash(),
                ))
            db.commit()
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
