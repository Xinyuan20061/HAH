from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.database import Base
from app.models import User, HealthAgentRun, AgentActionAudit
from app.services.safety import evaluate_message, review_generated_advice
from app.services.redaction import redact, redact_text
from app.services.agent.actions import execute_action, list_actions
from app.core.url_security import validate_ai_base_url


def test_safety_blocks_medication_and_emergency():
    assert evaluate_message("我胸痛而且呼吸困难").action == "block"
    d = evaluate_message("我的药量怎么改，能不能停药")
    assert d.category == "medication" and d.action == "block"
    assert evaluate_message("今天想快走二十分钟").action == "allow"
    assert evaluate_message("运动后晕厥而且无法负重").category == "exercise_red_flag"
    assert review_generated_advice("你可以自行停药并继续训练").action == "block"
    assert review_generated_advice("请按照医生建议用药").action == "allow"


def test_redaction_removes_api_keys_and_bearer_tokens():
    x = redact(
        {
            "api_key": "sk-secret123",
            "nested": {"Authorization": "Bearer abc.def.ghi"},
            "text": "api_key=sk-test999",
        }
    )
    assert x["api_key"] == "***REDACTED***"
    assert x["nested"]["Authorization"] == "***REDACTED***"
    assert "sk-test999" not in x["text"]
    assert "abc.def.ghi" not in redact_text("Bearer abc.def.ghi")


def test_action_registry_requires_confirmation_and_audits():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        u = User(openid="test-action")
        db.add(u)
        db.commit()
        db.refresh(u)
        result = execute_action(
            db, u.id, "plan.apply", lambda: {"ok": True}, confirmed=False, source="user"
        )
        assert not result["executed"] and result["requires_confirmation"]
        audit = db.get(AgentActionAudit, result["audit_id"])
        assert audit.status == "awaiting_confirmation"
        done = execute_action(
            db, u.id, "plan.apply", lambda: {"ok": True}, confirmed=True, source="user"
        )
        assert done["executed"] and done["result"]["ok"]
    assert any(
        x["key"] == "privacy.account.delete" and x["risk_level"] == "critical"
        for x in list_actions()
    )


def test_ai_base_url_accepts_https_public_host():
    assert (
        validate_ai_base_url("https://api.deepseek.com") == "https://api.deepseek.com"
    )
