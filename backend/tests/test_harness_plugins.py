"""User-facing capability plugin contract and pause semantics."""

from sqlalchemy.orm import Session

from app.harness.contracts import ToolContext
from app.harness.tools import get_tool_registry
from app.models import User


def _install_personal_policy(api, *, allow_actions=False, suffix="test"):
    config = {
        "goal": "execution_pattern",
        "data_scopes": [
            "policy.goals.read", "policy.execution.read", "policy.outcomes.read",
            "health.profile.read", "health.records.read",
        ],
        "allow_action_proposals": allow_actions,
    }
    created = api.post(
        "/api/v1/harness/installations",
        json={"plugin_id": "personal_policy", "config": config},
        headers={"Idempotency-Key": f"install-{suffix}"},
    )
    assert created.status_code == 200, created.text
    installation = created.json()["installation"]
    preview = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/preview",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": f"preview-{suffix}"},
    )
    assert preview.status_code == 200, preview.text
    resumed = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/resume",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": f"resume-{suffix}"},
    )
    assert resumed.status_code == 200, resumed.text
    return resumed.json()["installation"]


def _install_configured_plugin(api, plugin_id, config, suffix):
    created = api.post(
        "/api/v1/harness/installations",
        json={"plugin_id": plugin_id, "config": config},
        headers={"Idempotency-Key": f"install-{suffix}"},
    )
    assert created.status_code == 200, created.text
    installation = created.json()["installation"]
    preview = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/preview",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": f"preview-{suffix}"},
    )
    assert preview.status_code == 200, preview.text
    resumed = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/resume",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": f"resume-{suffix}"},
    )
    assert resumed.status_code == 200, resumed.text
    return resumed.json()["installation"]


def test_plugin_catalog_is_user_facing_and_reversible(api):
    response = api.get("/api/v1/harness/plugins")
    assert response.status_code == 200
    rows = {item["plugin_id"]: item for item in response.json()["plugins"]}
    assert "personal_policy" in rows
    assert rows["personal_policy"]["user_value"]
    assert rows["personal_policy"]["privacy_summary"]
    assert rows["personal_policy"]["enabled"] is False
    assert "tool_names" not in rows["personal_policy"]
    assert "action_names" not in rows["personal_policy"]
    disabled = api.post("/api/v1/harness/plugins/personal_policy/disable")
    assert disabled.status_code == 409
    assert disabled.json()["error"]["code"] == "CAPABILITY_FLOW_REQUIRED"
    blocked = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "short"}})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PLUGIN_UNAVAILABLE"
    enabled = api.post("/api/v1/harness/plugins/personal_policy/enable", json={"data_scope": ["执行记录"]})
    assert enabled.status_code == 409
    assert enabled.json()["error"]["code"] == "CAPABILITY_FLOW_REQUIRED"
    installation = _install_personal_policy(api, allow_actions=True)
    assert installation["enabled"] is True

    paused = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/pause",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": "pause-catalog-test"},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["installation"]["enabled"] is False
    blocked = api.post("/api/v1/policy/compile", json={"template_id": "session_duration", "parameters": {"variant": "session_15m"}})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PLUGIN_DISABLED"

    manifest = api.get("/api/v1/harness/manifest")
    assert manifest.status_code == 200
    internal = {item["plugin_id"]: item for item in manifest.json()["plugins"]}
    assert internal["personal_policy"]["tool_names"]


def test_paused_plugin_blocks_its_harness_tools(api, migrated_engine):
    installation = _install_personal_policy(api, suffix="tool-test")
    paused = api.post(
        f"/api/v1/harness/installations/{installation['installation_id']}/pause",
        json={"config_version": installation["config_version"]},
        headers={"Idempotency-Key": "pause-tool-test"},
    )
    assert paused.status_code == 200, paused.text
    with Session(migrated_engine) as db:
        user = db.get(User, api.user_id)
        observation = get_tool_registry().execute("policy.candidates.preview", ToolContext(db=db, user=user, agent_id="planner"), {})
    assert observation.status == "blocked"
    assert "暂停" in observation.summary


def test_plugin_scope_cannot_exceed_reviewed_data_boundary(api):
    response = api.post(
        "/api/v1/harness/installations",
        json={"plugin_id": "personal_policy", "config": {"data_scopes": ["unreviewed_external_data"]}},
        headers={"Idempotency-Key": "invalid-scope-test"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_SCOPE_INVALID"


def test_agent_uses_preferences_per_enabled_capability(api, migrated_engine):
    from app.harness.plugins import response_preferences

    _install_configured_plugin(api, "health_state", {
        "goal": "understand_limits",
        "data_scopes": ["health.profile.read"],
        "output_style": "evidence_first",
        "notification_frequency": "off",
    }, "health-state-pref")
    _install_configured_plugin(api, "plan_outcome", {
        "goal": "review_progress",
        "data_scopes": ["plan.outcomes.read"],
        "output_style": "concise",
        "notification_frequency": "on_request",
    }, "plan-outcome-pref")

    with Session(migrated_engine) as db:
        preferences = response_preferences(db, api.user_id)

    by_plugin = {item["plugin_id"]: item for item in preferences["capabilities"]}
    assert by_plugin["health_state"]["goal"] == "看懂记录缺口与限制"
    assert by_plugin["health_state"]["style"] == "evidence_first"
    assert by_plugin["plan_outcome"]["goal"] == "复盘执行与负担"
    assert by_plugin["plan_outcome"]["style"] == "concise"
    assert preferences["notification_frequency"] == "on_request"
