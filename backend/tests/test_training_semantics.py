from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FitnessConcept, FitnessRelation, User, UserTrainingIntent
from app.services.agent.tools import read_context


def test_training_intent_effect_graph_recommendations_and_agent_context(
    api, migrated_engine
):
    empty = api.get("/api/v1/fitness/training-intent")
    assert empty.status_code == 200
    assert empty.json()["confirmed"] is False
    assert len(empty.json()["catalog"]["body_parts"]) >= 8

    invalid = api.put(
        "/api/v1/fitness/training-intent",
        json={"target_body_parts": ["imaginary"], "goals": ["strength"]},
    )
    assert invalid.status_code == 422

    saved = api.put(
        "/api/v1/fitness/training-intent",
        json={
            "target_body_parts": ["chest", "core"],
            "goals": ["strength"],
            "constraints": ["徒手训练"],
            "preferred_equipment": ["瑜伽垫"],
            "notes": "优先动作质量",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["confirmed"] is True
    assert saved.json()["target_body_parts"] == ["chest", "core"]

    effect = api.get("/api/v1/fitness/exercise-effects/pushup")
    assert effect.status_code == 200, effect.text
    assert effect.json()["exercise_name"] == "俯卧撑"
    assert {item["key"] for item in effect.json()["target_body_parts"]} >= {
        "chest",
        "core",
    }
    assert effect.json()["inference_scope"].startswith("知识图谱映射")

    recommendations = api.get("/api/v1/fitness/exercise-recommendations")
    assert recommendations.status_code == 200
    assert recommendations.json()["items"][0]["exercise_type"] == "pushup"
    assert recommendations.json()["method"] == "fitness_knowledge_graph_v1"

    with Session(migrated_engine) as db:
        assert db.scalar(select(UserTrainingIntent).where(UserTrainingIntent.user_id == api.user_id))
        assert len(db.scalars(select(FitnessConcept)).all()) >= 20
        assert len(db.scalars(select(FitnessRelation)).all()) >= 30
        user = db.get(User, api.user_id)
        context = read_context(db, user)
        assert context["training_intent"]["confirmed"] is True
        assert context["exercise_recommendations"]["items"][0]["exercise_type"] == "pushup"

    agent = api.post("/api/v1/agent/respond", json={"message": "我想练胸部，怎么练？"})
    assert agent.status_code == 200, agent.text
    assert agent.json()["exercise_recommendations"]["items"][0]["exercise_type"] == "pushup"
    assert "fitness_knowledge_graph" in agent.json()["facts_used"]


def test_motion_semantic_endpoint_is_user_scoped_and_missing_is_404(api):
    assert api.get("/api/v1/fitness/motion-semantics/999999").status_code == 404
    registry = api.get("/api/v1/fitness/dataset-registry")
    assert registry.status_code == 200
    assert len(registry.json()["items"]) >= 6
    assert registry.json()["policy"] == "registry_metadata_only_no_automatic_download"
    assert all(item["redistribution_allowed"] is False for item in registry.json()["items"])
    readiness = api.get("/api/v1/fitness/model-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["current_stage"] == "engineering_baseline"
    assert readiness.json()["trained_model_available"] is False
    assert {item["model_key"] for item in readiness.json()["items"]} >= {
        "motion-rule-recognizer",
        "pose-compositional-semantics",
        "skeleton-stgcn-multilabel",
    }
