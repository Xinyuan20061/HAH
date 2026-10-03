"""User-facing capability plugin contract and pause semantics."""

from sqlalchemy.orm import Session

from app.harness.contracts import ToolContext
from app.harness.tools import get_tool_registry
from app.models import User


def test_plugin_catalog_is_user_facing_and_reversible(api):
    response = api.get("/api/v1/harness/plugins")
    assert response.status_code == 200
    rows = {item["plugin_id"]: item for item in response.json()["plugins"]}
    assert "personal_policy" in rows
    assert rows["personal_policy"]["user_value"]
    assert rows["personal_policy"]["privacy_summary"]
    assert "tool_names" not in rows["personal_policy"]
    assert "action_names" not in rows["personal_policy"]
    disabled = api.post("/api/v1/harness/plugins/personal_policy/disable")
    assert disabled.status_code == 200
    assert disabled.json()["plugin"]["enabled"] is False
    blocked = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "short"}})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PLUGIN_DISABLED"
    enabled = api.post("/api/v1/harness/plugins/personal_policy/enable", json={"data_scope": ["执行记录"]})
    assert enabled.status_code == 200
    assert enabled.json()["plugin"]["enabled"] is True

    manifest = api.get("/api/v1/harness/manifest")
    assert manifest.status_code == 200
    internal = {item["plugin_id"]: item for item in manifest.json()["plugins"]}
    assert internal["personal_policy"]["tool_names"]


def test_paused_plugin_blocks_its_harness_tools(api, migrated_engine):
    api.post("/api/v1/harness/plugins/personal_policy/disable")
    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        observation = get_tool_registry().execute("policy.candidates.preview", ToolContext(db=db, user=user, agent_id="planner"), {})
    assert observation.status == "blocked"
    assert "暂停" in observation.summary


def test_plugin_scope_cannot_exceed_reviewed_data_boundary(api):
    response = api.post(
        "/api/v1/harness/plugins/personal_policy/enable",
        json={"data_scope": ["unreviewed_external_data"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_SCOPE_INVALID"
