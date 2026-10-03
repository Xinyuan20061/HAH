"""Capability-honesty acceptance (capability plan §13.6).

The database-dependent audit clauses are exercised on a freshly migrated database,
so a missing local database can never hide a "claims Gold without evidence"
regression. The code-level clauses are asserted directly.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import build_engine
from app.models import (
    MediaAsset,
    MotionAnalysisRun,
    MotionGoldEvaluation,
    User,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

import sys

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.audit_capability_honesty import (  # noqa: E402
    PLAN_GOLD_THRESHOLDS,
    check_food_table_state,
    check_gold_claims,
    check_gold_gate_thresholds,
    check_planning_only_declared,
)


def _migrated(tmp_path):
    from alembic import command

    from app.core.config import settings

    url = "sqlite:///" + (tmp_path / "honesty.db").as_posix()
    previous = settings.database_url
    settings.database_url = url
    try:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        settings.database_url = previous
    return url, build_engine(url)


def test_gold_gate_thresholds_match_the_plan():
    """§13.6: a silently lowered threshold is how a fake Gold gets shipped."""
    assert check_gold_gate_thresholds() == []


def test_planning_only_exercises_are_declared():
    assert check_planning_only_declared() == []


def test_food_table_reports_its_review_state(tmp_path):
    _url, engine = _migrated(tmp_path)
    with Session(engine) as db:
        assert check_food_table_state(db) == []
        from app.services.food.references import table_status

        status = table_status(db)
        assert status["reviewed"] is False
        assert "粗略草稿" in status["policy"]
    engine.dispose()


def test_no_invented_gold_claim_on_a_fresh_database(tmp_path):
    _url, engine = _migrated(tmp_path)
    with Session(engine) as db:
        assert check_gold_claims(db) == []
    engine.dispose()


def test_audit_detects_a_fabricated_gold_claim(tmp_path, monkeypatch):
    """Injection test: the audit must fail when a catalogue entry claims Gold
    without a passing evaluation record."""
    from app.services.motion import catalog

    _url, engine = _migrated(tmp_path)
    real = catalog.list_capabilities()

    def fake_capabilities():
        rows = [dict(item) for item in real]
        rows[0] = dict(rows[0])
        rows[0]["capabilities"] = dict(rows[0]["capabilities"])
        rows[0]["capabilities"]["quality_scorer"] = "gold_v1_verified"
        return rows

    monkeypatch.setattr(catalog, "list_capabilities", fake_capabilities)
    with Session(engine) as db:
        problems = check_gold_claims(db)
        assert problems, "伪造的 Gold 声明必须被审计发现"
        assert "quality_scorer" in problems[0]
    engine.dispose()


def test_gold_claim_passes_once_a_passing_evaluation_exists(tmp_path, monkeypatch):
    from app.services.motion import catalog

    _url, engine = _migrated(tmp_path)
    exercise_id = catalog.list_capabilities()[0]["id"]
    with Session(engine) as db:
        user = User(openid="honesty-1")
        db.add(user)
        db.flush()
        asset = MediaAsset(
            user_id=user.id, storage_key="honesty-asset", media_type="video"
        )
        db.add(asset)
        db.flush()
        run = MotionAnalysisRun(
            user_id=user.id,
            media_asset_id=asset.id,
            requested_type=exercise_id,
            pipeline_version="motion-unified-v2",
            status="completed",
        )
        db.add(run)
        db.flush()
        db.add(
            MotionGoldEvaluation(
                run_id=run.id,
                user_id=user.id,
                exercise_id=exercise_id,
                evaluator_version="gold-eval-1.0.0",
                tier="gold",
                available=True,
                segments_json="[]",
                findings_json="[]",
                measurements_json="{}",
                gate_json="{}",
            )
        )
        db.commit()

        real = catalog.list_capabilities()

        def fake_capabilities():
            rows = [dict(item) for item in real]
            for index, row in enumerate(rows):
                if row["id"] == exercise_id:
                    row["capabilities"] = dict(row["capabilities"])
                    row["capabilities"]["quality_scorer"] = "gold_eval-1.0.0"
            return rows

        monkeypatch.setattr(catalog, "list_capabilities", fake_capabilities)
        assert check_gold_claims(db) == [], "有通过门禁的评测记录时应放行"
    engine.dispose()


def test_honesty_endpoint_exists_and_is_blunt(api):
    res = api.get("/api/v1/capabilities/honesty")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "motion" in body and "food" in body and "planning" in body
    assert body["claim_rules"], "必须公布当前允许的宣传口径"
    assert any("不等于" in rule for rule in body["claim_rules"])

    gold = api.get("/api/v1/capabilities/motion-gold")
    assert gold.status_code == 200
    assert gold.json()["policy"]

    table = api.get("/api/v1/capabilities/food-table")
    assert table.status_code == 200
    assert table.json()["entries"] > 0


def test_motion_gold_evaluate_reports_unavailable_without_the_worker(api, db):
    """Without the worker package the run is unavailable, never Gold."""
    asset = MediaAsset(
        user_id=api.user_id, storage_key=f"goldapi-{api.user_id}", media_type="video"
    )
    db.add(asset)
    db.flush()
    run = MotionAnalysisRun(
        user_id=api.user_id,
        media_asset_id=asset.id,
        requested_type="squat",
        pipeline_version="motion-unified-v2",
        status="completed",
    )
    db.add(run)
    db.commit()
    run_id = run.id

    res = api.post(f"/api/v1/capabilities/motion-gold/{run_id}/evaluate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tier"] in {"unavailable", "silver"}, body
    assert body["tier"] != "gold", "没有评测报告时不得返回 Gold"
    assert body["available"] is False


def test_food_question_and_answer_endpoints(api, db):
    import json

    from app.models import FoodAnalysisSession

    session = FoodAnalysisSession(
        user_id=api.user_id,
        status="analyzed",
        initial_json=json.dumps(
            {"dish_name": "鸡胸肉配米饭", "items": [{"name": "米饭"}, {"name": "鸡胸肉"}]},
            ensure_ascii=False,
        ),
    )
    db.add(session)
    db.commit()
    analysis_id = session.id

    questions = api.get(f"/api/v1/food/analysis/{analysis_id}/questions")
    assert questions.status_code == 200, questions.text
    body = questions.json()
    assert len(body["questions"]) <= 2
    assert body["policy"]

    first = body["questions"][0]
    answer = api.post(
        f"/api/v1/food/analysis/{analysis_id}/answers",
        json={"answers": [{"question_id": first["question_id"], "option_key": first["options"][0]["key"]}]},
    )
    assert answer.status_code == 200, answer.text
    payload = answer.json()
    assert payload["ok"] is True
    assert payload["calculation"]["items"]
    assert "policy" in payload["calculation"]

    bad_option = api.post(
        f"/api/v1/food/analysis/{analysis_id}/answers",
        json={"answers": [{"question_id": first["question_id"], "option_key": "nope"}]},
    )
    assert bad_option.status_code == 422
    assert bad_option.json()["error"]["code"] == "INVALID_OPTION"

    unknown = api.post(
        f"/api/v1/food/analysis/{analysis_id}/answers",
        json={"answers": [{"question_id": "q_missing", "option_key": "x"}]},
    )
    assert unknown.status_code == 404


def test_food_lookup_is_exact_only(api):
    hit = api.get("/api/v1/food/lookup", params={"name": "米饭"})
    assert hit.status_code == 200
    assert hit.json()["matched"] is True
    miss = api.get("/api/v1/food/lookup", params={"name": "鸡"})
    assert miss.json()["matched"] is False
    assert miss.json()["reason"] == "not_in_audited_table"


def test_food_prior_endpoints(api):
    assert api.get("/api/v1/food/priors").json()["priors"] == []
    created = api.post(
        "/api/v1/food/priors", json={"food_key": "rice_cooked", "mass_g": 200}
    )
    assert created.status_code == 200
    assert created.json()["recorded"] is True

    listed = api.get("/api/v1/food/priors").json()["priors"]
    assert len(listed) == 1
    assert listed[0]["food_key"] == "rice_cooked"

    removed = api.delete("/api/v1/food/priors/rice_cooked")
    assert removed.status_code == 200
    assert api.get("/api/v1/food/priors").json()["priors"] == []

    unknown = api.post(
        "/api/v1/food/priors", json={"food_key": "not_a_food", "mass_g": 100}
    )
    assert unknown.status_code == 404
