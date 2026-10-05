"""Reviewed, user-configurable Harness capabilities and central authorization.

Built-ins are reviewed adapters, not user-supplied code. Every tool has an
explicit binding and machine-readable data scopes; missing bindings fail closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import HarnessCapabilityAudit, HarnessPluginInstallation


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    version: str
    title: str
    summary: str
    user_value: str
    data_needed: tuple[str, ...]
    privacy_summary: str
    tool_names: tuple[str, ...] = ()
    action_names: tuple[str, ...] = ()
    scope_definitions: tuple[tuple[str, str], ...] = ()
    goals: tuple[tuple[str, str], ...] = ()
    may_propose_actions: bool = False
    default_enabled: bool = False
    reversible: bool = True
    reviewed: bool = True

    def manifest_hash(self) -> str:
        payload = {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "api_version": "1",
            "review_status": "approved" if self.reviewed else "pending",
            "presentation": {
                "title": self.title,
                "summary": self.summary,
                "user_value": self.user_value,
                "data_needed": list(self.data_needed),
                "privacy_summary": self.privacy_summary,
            },
            "data_scopes": list(self.scope_definitions),
            "tool_bindings": list(self.tool_names),
            "configuration": {
                "goals": list(self.goals),
                "triggers": [("on_request", "你提出相关问题时")],
                "output_styles": [
                    ("balanced", "清晰自然"),
                    ("concise", "简短直接"),
                    ("evidence_first", "先说依据与限制"),
                ],
                "notification_frequencies": [
                    ("off", "不额外提示"),
                    ("on_request", "相关对话中轻提示"),
                ],
                "may_propose_actions": self.may_propose_actions,
                "defaults": {
                    "goal": self.goals[0][0] if self.goals else "daily_guidance",
                    "trigger": "on_request",
                    "output_style": "balanced",
                    "notification_frequency": "off",
                    "allow_action_proposals": False,
                },
            },
            "output_contracts": ["health.answer.v1", "health.evidence.v1"],
            "privacy": {"external_transfer": False, "retention_days": None},
            "safety": {
                "may_propose_action": self.may_propose_actions,
                "may_execute_action": False,
                "reversible": self.reversible,
            },
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def public_dict(self, *, enabled: bool = False, config: dict[str, Any] | None = None, include_internal: bool = False) -> dict[str, Any]:
        payload = {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "api_version": "1",
            "review_status": "approved" if self.reviewed else "pending",
            "manifest_hash": self.manifest_hash(),
            "title": self.title,
            "summary": self.summary,
            "user_value": self.user_value,
            "data_needed": list(self.data_needed),
            "data_scopes": [{"id": key, "label": label} for key, label in self.scope_definitions],
            "output_contracts": ["health.answer.v1", "health.evidence.v1"],
            "privacy": {"retention_days": None, "external_transfer": False},
            "privacy_summary": self.privacy_summary,
            "enabled": enabled,
            "capability_state": "enabled" if enabled else "paused",
            "reversible": self.reversible,
            "reviewed": self.reviewed,
            "config": config or {},
            "config_schema": {
                "goals": [{"id": key, "label": label} for key, label in self.goals],
                "triggers": [{"id": "on_request", "label": "你提出相关问题时"}],
                "output_styles": [
                    {"id": "balanced", "label": "清晰自然"},
                    {"id": "concise", "label": "简短直接"},
                    {"id": "evidence_first", "label": "先说依据与限制"},
                ],
                "notification_frequencies": [
                    {"id": "off", "label": "不额外提示"},
                    {"id": "on_request", "label": "相关对话中轻提示"},
                ],
                "allow_action_proposals": self.may_propose_actions,
            },
            "safety": {"may_propose_action": self.may_propose_actions, "may_execute_action": False},
        }
        if include_internal:
            payload["tool_bindings"] = list(self.tool_names)
            payload["tool_names"] = list(self.tool_names)
            payload["action_names"] = list(self.action_names)
        return payload


BUILTIN_PLUGINS: tuple[PluginManifest, ...] = (
    PluginManifest(
        "health_state", "1.1.0", "健康状态", "把记录整理成当前状态、缺口和约束。",
        "知道今天适合做什么，以及为什么暂时不能判断。",
        ("健康档案", "饮食、运动和打卡记录", "健康目标", "审核知识与教学资源"),
        "只读取本人数据，不向第三方开放。",
        (
            "health.context.read", "health.knowledge.search", "health.resources.search",
            "health.state.read", "health.state.history", "health.signals.read",
            "health.constraints.read", "harness.state.read",
            "harness.constraints.read", "harness.signals.read", "harness.actions.list",
            "harness.capabilities.read",
        ),
        scope_definitions=(
            ("health.profile.read", "健康档案"), ("health.records.read", "饮食、运动和打卡记录"),
            ("health.goals.read", "健康目标"), ("health.knowledge.read", "审核知识"),
            ("health.resources.read", "教学资源"),
        ),
        goals=(("daily_guidance", "日常状态与建议"), ("understand_limits", "看懂记录缺口与限制")),
    ),
    PluginManifest(
        "personal_policy", "1.2.0", "个人策略学习", "把一次建议变成可执行、可复查的个人周期。",
        "逐渐发现什么方法在什么情况下更适合你。",
        ("目标", "执行记录", "结果记录"), "只使用本人授权的记录；结论可撤销、可重置。",
        (
            "policy.templates.read", "policy.candidates.preview", "policy.episode.read",
            "policy.evidence.read", "policy.memory.read", "policy.decision.explain",
            "policy.acquisition.preview", "policy.certificate.read", "policy.knowledge.read",
            "policy.episode.start", "policy.episode.finish", "policy.episode.stop", "policy.memory.reset",
            "policy.episode.rereview",
        ),
        ("policy.episode.start", "policy.episode.finish", "policy.episode.stop", "policy.memory.reset", "policy.episode.rereview"),
        (
            ("policy.goals.read", "个人策略目标"), ("policy.execution.read", "周期执行记录"),
            ("policy.outcomes.read", "周期结果记录"), ("health.profile.read", "健康档案与约束"),
            ("health.records.read", "用于评估的健康记录"), ("health.knowledge.read", "审核健康知识"),
        ),
        (("execution_pattern", "了解执行节奏"), ("compare_outcomes", "复查周期结果")),
        may_propose_actions=True,
    ),
    PluginManifest(
        "motion_evidence", "1.1.0", "动作证据", "读取动作分析、关键帧和历史可比结果。",
        "看懂动作反馈，并知道哪些结论有真实画面依据。",
        ("本人上传的动作视频", "动作分析结果"), "视频和分析只用于本人账户，不把模型点评当作医学诊断。",
        ("motion.analysis.read", "motion.feedback.read", "motion.timeline.read", "motion.history.compare"),
        scope_definitions=(
            ("motion.analysis.read", "本人动作分析结果"), ("motion.frames.read", "关键帧与时间轴"),
            ("motion.feedback.read", "本人纠错反馈"),
        ),
        goals=(("review_form", "复盘动作表现"), ("compare_sessions", "比较可比的历史记录")),
    ),
    PluginManifest(
        "plan_outcome", "1.1.0", "计划与结果", "比较计划执行、负担和结果，帮助调整下一步。",
        "计划会根据真实执行情况变得更容易坚持。",
        ("健康目标", "计划任务", "执行结果", "用户偏好"), "只读取本人计划和结果，不自动替用户改计划。",
        (
            "plan.simulate", "decision.contract", "decision.next_best_action", "preferences.read",
            "outcomes.history.read", "experiment.result.read", "next_action.rank",
            "harness.plan.simulate", "harness.outcomes.history.read", "harness.next_action.rank",
            "health.outcomes.compare",
            "plan.apply", "plan.replan.apply", "goal.adjustment.apply", "experiment.start",
            "experiment.finish", "experiment.cancel",
        ),
        ("plan.apply", "plan.replan.apply", "goal.adjustment.apply", "experiment.start", "experiment.finish", "experiment.cancel"),
        (
            ("plan.goals.read", "计划目标"), ("plan.outcomes.read", "计划执行与结果"),
            ("health.profile.read", "健康档案与约束"), ("health.records.read", "健康与执行记录"),
            ("user.preferences.read", "本人表达与训练偏好"), ("plan.proposals", "提出计划调整申请"),
        ),
        (("sustainable_plan", "制定可持续计划"), ("review_progress", "复盘执行与负担")),
        may_propose_actions=True,
    ),
)


class PluginError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


PLUGIN_BY_ID = {item.plugin_id: item for item in BUILTIN_PLUGINS}
PRIVACY_TOOLS = {"privacy.export", "privacy.account.delete"}


def get_plugin(plugin_id: str) -> PluginManifest:
    try:
        return PLUGIN_BY_ID[plugin_id]
    except KeyError:
        raise PluginError("PLUGIN_NOT_FOUND", "健康能力不存在或尚未审核") from None


def _installation(db: Session, user_id: int, plugin_id: str) -> HarnessPluginInstallation | None:
    return db.scalar(select(HarnessPluginInstallation).where(
        HarnessPluginInstallation.user_id == user_id,
        HarnessPluginInstallation.plugin_id == plugin_id,
    ))


def _raw_config(row: HarnessPluginInstallation | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(row.config_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def default_config(manifest: PluginManifest) -> dict[str, Any]:
    return {
        "goal": manifest.goals[0][0] if manifest.goals else "daily_guidance",
        "data_scopes": [key for key, _ in manifest.scope_definitions],
        "trigger": "on_request",
        "output_style": "balanced",
        "notification_frequency": "off",
        "allow_action_proposals": False,
    }


def _clean_legacy_config(manifest: PluginManifest, value: dict[str, Any]) -> dict[str, Any]:
    clean = {key: item for key, item in value.items() if key in {
        "goal", "data_scopes", "data_scope", "trigger", "output_style",
        "notification_frequency", "allow_action_proposals",
    }}
    if "data_scopes" not in clean and isinstance(clean.get("data_scope"), list):
        clean["data_scopes"] = clean["data_scope"]
    clean.pop("data_scope", None)
    if "notification_frequency" not in clean and isinstance(value.get("notifications"), bool):
        clean["notification_frequency"] = "on_request" if value["notifications"] else "off"
    clean.pop("notifications", None)
    if clean.get("allow_action_proposals") and not manifest.may_propose_actions:
        clean["allow_action_proposals"] = False
    return clean


def normalize_config(manifest: PluginManifest, value: dict[str, Any], *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed_keys = {"goal", "data_scopes", "trigger", "output_style", "notification_frequency", "allow_action_proposals"}
    if set(value) - allowed_keys:
        raise PluginError("PLUGIN_CONFIG_INVALID", "配置包含未审核的选项")
    result = dict(base or default_config(manifest))
    result.update(value)
    goals = {key for key, _ in manifest.goals}
    if result.get("goal") not in goals:
        raise PluginError("PLUGIN_CONFIG_INVALID", "请选择该能力支持的目标")
    scopes = result.get("data_scopes", [])
    if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
        raise PluginError("PLUGIN_SCOPE_INVALID", "数据授权范围格式无效")
    scope_labels = {label: key for key, label in manifest.scope_definitions}
    allowed_scopes = {key for key, _ in manifest.scope_definitions}
    normalized = [scope_labels.get(item, item) for item in scopes]
    if set(normalized) - allowed_scopes:
        raise PluginError("PLUGIN_SCOPE_INVALID", "只能授权该健康能力声明的数据范围")
    result["data_scopes"] = list(dict.fromkeys(normalized))
    if result.get("trigger") != "on_request":
        raise PluginError("PLUGIN_CONFIG_INVALID", "当前只支持在你主动提问时触发")
    if result.get("output_style") not in {"balanced", "concise", "evidence_first"}:
        raise PluginError("PLUGIN_CONFIG_INVALID", "输出形式无效")
    if result.get("notification_frequency") not in {"off", "on_request"}:
        raise PluginError("PLUGIN_CONFIG_INVALID", "当前不支持后台定时推送")
    if not isinstance(result.get("allow_action_proposals"), bool):
        raise PluginError("PLUGIN_CONFIG_INVALID", "行动提案设置无效")
    if result["allow_action_proposals"] and not manifest.may_propose_actions:
        raise PluginError("PLUGIN_CONFIG_INVALID", "该能力不支持行动提案")
    return result


def effective_scopes(row: HarnessPluginInstallation | None, manifest: PluginManifest | None = None) -> set[str]:
    if row is None:
        return set()
    manifest = manifest or PLUGIN_BY_ID.get(row.plugin_id)
    if manifest is None:
        return set()
    config = _clean_legacy_config(manifest, _raw_config(row))
    values = config.get("data_scopes")
    if values is None:
        # Read only legacy consent values; unknown labels never widen access.
        values = config.get("data_scope", [])
    try:
        normalized = normalize_config(manifest, {"data_scopes": list(values)}, base=default_config(manifest))
    except (PluginError, TypeError):
        return set()
    return set(normalized["data_scopes"])


def capability_snapshot_hash(db: Session, user_id: int) -> str:
    """Hash the effective reviewed capability state for a confirm-time CAS."""
    rows = {
        row.plugin_id: row
        for row in db.scalars(select(HarnessPluginInstallation).where(
            HarnessPluginInstallation.user_id == user_id
        )).all()
    }
    snapshot = []
    for plugin_id in ("personal_policy", "health_state", "plan_outcome", "motion_evidence"):
        manifest = PLUGIN_BY_ID[plugin_id]
        row = rows.get(plugin_id)
        active = bool(
            row
            and row.enabled
            and row.reviewed_manifest_hash == manifest.manifest_hash()
        )
        snapshot.append({
            "plugin_id": plugin_id,
            "manifest_hash": manifest.manifest_hash(),
            "installation_id": row.id if row else None,
            "config_version": row.config_version if row else 0,
            "reviewed_manifest_hash": row.reviewed_manifest_hash if row else "",
            "enabled": active,
            "effective_scopes": sorted(effective_scopes(row, manifest)) if active else [],
        })
    return hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _public_installation(manifest: PluginManifest, row: HarnessPluginInstallation | None) -> dict[str, Any]:
    config = _raw_config(row)
    if row is None:
        config = default_config(manifest)
    else:
        try:
            config = normalize_config(manifest, _clean_legacy_config(manifest, config), base=default_config(manifest))
        except PluginError:
            config = {**default_config(manifest), "data_scopes": []}
    enabled = bool(row and row.enabled and row.reviewed_manifest_hash == manifest.manifest_hash())
    reconsent = bool(row and row.reviewed_manifest_hash != manifest.manifest_hash())
    payload = manifest.public_dict(enabled=enabled, config=config)
    selected = effective_scopes(row, manifest)
    active_scopes = selected if enabled else set()
    payload.update({
        "installation_id": row.id if row else None,
        "capability_state": "enabled" if enabled else "review_required" if reconsent else "paused" if row else "not_installed",
        "manifest_reconsent_required": reconsent,
        "effective_scopes": sorted(active_scopes),
        "reviewed_manifest_hash": row.reviewed_manifest_hash if row else "",
        "current_manifest_hash": manifest.manifest_hash(),
        "config_version": row.config_version if row else 0,
        "last_run_at": row.last_run_at.isoformat() + "Z" if row and row.last_run_at else None,
        "last_error": row.last_error if row else "",
    })
    return payload


def list_plugins(db: Session, user_id: int, *, include_internal: bool = False) -> list[dict[str, Any]]:
    rows = {row.plugin_id: row for row in db.scalars(select(HarnessPluginInstallation).where(HarnessPluginInstallation.user_id == user_id)).all()}
    output = []
    for manifest in BUILTIN_PLUGINS:
        payload = _public_installation(manifest, rows.get(manifest.plugin_id))
        if include_internal:
            payload["tool_names"] = list(manifest.tool_names)
            payload["action_names"] = list(manifest.action_names)
        output.append(payload)
    return output


def set_plugin_enabled(
    db: Session,
    user_id: int,
    plugin_id: str,
    *,
    enabled: bool,
    data_scope: list[str] | None = None,
) -> dict[str, Any]:
    """Trusted service/bootstrap helper; never exposed as a user API.

    User-facing changes must use the versioned configuration and explicit-consent
    endpoints. This helper remains for controlled service setup and test fixture
    impersonation.
    """
    manifest = get_plugin(plugin_id)
    row = _installation(db, user_id, plugin_id)
    current = _clean_legacy_config(manifest, _raw_config(row)) if row else {}
    if not current:
        current = default_config(manifest)
    if data_scope is not None:
        current["data_scopes"] = data_scope
    config = normalize_config(manifest, current, base=default_config(manifest))
    now = utc_now()
    if row is None:
        row = HarnessPluginInstallation(
            user_id=user_id, plugin_id=plugin_id, plugin_version=manifest.version,
            enabled=enabled, config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
            config_version=1, reviewed_manifest_hash=manifest.manifest_hash(),
            enabled_at=now if enabled else None, disabled_at=None if enabled else now,
        )
        db.add(row)
    else:
        row.enabled = enabled
        row.plugin_version = manifest.version
        row.config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        row.config_version = (row.config_version or 0) + 1
        row.reviewed_manifest_hash = manifest.manifest_hash()
        row.enabled_at = now if enabled else row.enabled_at
        row.disabled_at = None if enabled else now
    db.flush()
    return _public_installation(manifest, row)


@dataclass(frozen=True)
class ToolBinding:
    plugin_id: str
    required_scopes: tuple[str, ...]
    phase: str = "new_work"
    operation: str = "read"


def _tool_binding(tool_name: str) -> ToolBinding | None:
    if tool_name in PRIVACY_TOOLS:
        return None
    manifest = next((plugin for plugin in BUILTIN_PLUGINS if tool_name in plugin.tool_names), None)
    if manifest is None:
        return None
    if manifest.plugin_id == "health_state":
        if tool_name == "health.knowledge.search": scopes = ("health.knowledge.read",)
        elif tool_name == "health.resources.search": scopes = ("health.resources.read",)
        elif tool_name in {"harness.actions.list", "harness.capabilities.read"}: scopes = ("health.profile.read",)
        else: scopes = ("health.profile.read", "health.records.read", "health.goals.read")
    elif manifest.plugin_id == "motion_evidence":
        scopes = ("motion.frames.read",) if tool_name == "motion.timeline.read" else ("motion.analysis.read",)
        if tool_name == "motion.feedback.read": scopes = ("motion.feedback.read",)
    elif manifest.plugin_id == "personal_policy":
        if tool_name in {"policy.acquisition.preview", "policy.certificate.read"}:
            scopes = ("policy.execution.read",)
        elif tool_name == "policy.knowledge.read":
            scopes = ("health.knowledge.read", "policy.execution.read")
        elif tool_name in {"policy.episode.read", "policy.evidence.read", "policy.decision.explain", "policy.memory.read"}:
            scopes = ("policy.execution.read", "policy.outcomes.read")
        elif tool_name == "policy.candidates.preview": scopes = ("policy.goals.read", "policy.outcomes.read")
        elif tool_name in {"policy.episode.finish", "policy.episode.stop", "policy.episode.rereview"}:
            return ToolBinding(manifest.plugin_id, ("policy.execution.read",), "close_existing", "user_action")
        elif tool_name == "policy.memory.reset":
            return ToolBinding(manifest.plugin_id, ("policy.execution.read",), "delete", "user_action")
        else: scopes = ("policy.goals.read",)
    else:
        if tool_name in {"preferences.read"}: scopes = ("user.preferences.read",)
        elif tool_name in {"health.outcomes.compare", "outcomes.history.read", "experiment.result.read", "harness.outcomes.history.read", "next_action.rank", "harness.next_action.rank"}: scopes = ("plan.outcomes.read",)
        elif tool_name in {"experiment.finish", "experiment.cancel"}:
            return ToolBinding(manifest.plugin_id, ("plan.outcomes.read",), "close_existing", "user_action")
        elif tool_name == "experiment.start":
            return ToolBinding(manifest.plugin_id, ("plan.proposals", "health.records.read", "plan.goals.read", "plan.outcomes.read"), "new_work", "propose")
        elif tool_name in {"plan.apply", "plan.replan.apply", "goal.adjustment.apply"}:
            return ToolBinding(manifest.plugin_id, ("plan.proposals",), "new_work", "propose")
        elif tool_name in {"plan.simulate", "decision.contract", "decision.next_best_action", "harness.plan.simulate", "harness.next_action.rank"}:
            scopes = ("health.profile.read", "health.records.read", "plan.goals.read", "plan.outcomes.read", "user.preferences.read")
        else: scopes = ("plan.goals.read", "plan.outcomes.read")
    if tool_name in {"motion.analysis.read", "motion.timeline.read", "motion.history.compare", "motion.feedback.read", "policy.episode.read", "policy.evidence.read", "policy.decision.explain", "policy.memory.read", "policy.acquisition.preview", "policy.certificate.read", "policy.knowledge.read", "health.outcomes.compare", "outcomes.history.read", "harness.outcomes.history.read", "experiment.result.read"}:
        return ToolBinding(manifest.plugin_id, tuple(scopes), "read_history", "read")
    if tool_name in manifest.action_names:
        return ToolBinding(manifest.plugin_id, tuple(scopes), "new_work", "propose")
    return ToolBinding(manifest.plugin_id, tuple(scopes), "new_work", "read")


def plugin_for_tool(tool_name: str) -> str | None:
    binding = _tool_binding(tool_name)
    return binding.plugin_id if binding else None


def authorize_capability(
    db: Session,
    user_id: int,
    plugin_id: str,
    operation: str,
    resource_scope: str | Iterable[str] | None,
    phase: str,
) -> dict[str, Any]:
    """Central capability gate shared by Harness tools and direct APIs."""
    manifest = PLUGIN_BY_ID.get(plugin_id)
    row = _installation(db, user_id, plugin_id) if manifest else None
    required = {resource_scope} if isinstance(resource_scope, str) else set(resource_scope or ())
    permitted_phases = {"new_work", "read_history", "close_existing", "export", "delete"}
    if manifest is None or phase not in permitted_phases:
        return {"allowed": False, "reason": "capability_unavailable", "effective_scopes": []}
    # Trusted outbox maintenance only completes/invalidate previously authorized
    # work; it never creates a new user-facing read or proposal.
    if operation == "user_action" and phase in {"read_history", "close_existing", "delete"}:
        return {"allowed": True, "reason": None, "effective_scopes": sorted(effective_scopes(row, manifest)), "installation": row}
    if operation == "system_maintenance" and phase in {"close_existing", "delete"}:
        return {"allowed": True, "reason": None, "effective_scopes": sorted(effective_scopes(row, manifest)), "installation": row}
    if row is None:
        return {"allowed": False, "reason": "capability_unavailable", "effective_scopes": []}
    if not manifest.reviewed or row.reviewed_manifest_hash != manifest.manifest_hash():
        return {"allowed": False, "reason": "manifest_unreviewed", "effective_scopes": []}
    scopes = effective_scopes(row, manifest)
    if phase not in {"close_existing", "export", "delete"} and not required <= scopes:
        return {"allowed": False, "reason": "scope_not_granted", "effective_scopes": sorted(scopes)}
    if operation == "propose" and not bool(_clean_legacy_config(manifest, _raw_config(row)).get("allow_action_proposals")):
        return {"allowed": False, "reason": "proposals_disabled", "effective_scopes": sorted(scopes)}
    if phase == "new_work" and not row.enabled:
        return {"allowed": False, "reason": "capability_paused", "effective_scopes": sorted(scopes)}
    return {"allowed": True, "reason": None, "effective_scopes": sorted(scopes), "installation": row}


def authorize_tool(db: Session, user_id: int, tool_name: str) -> dict[str, Any]:
    binding = _tool_binding(tool_name)
    if binding is None:
        if tool_name in PRIVACY_TOOLS:
            return {"allowed": True, "reason": None, "plugin_id": None, "effective_scopes": []}
        return {"allowed": False, "reason": "unknown_tool", "plugin_id": None, "effective_scopes": []}
    result = authorize_capability(
        db, user_id, binding.plugin_id, binding.operation,
        binding.required_scopes, binding.phase,
    )
    return {**result, "plugin_id": binding.plugin_id, "phase": binding.phase, "operation": binding.operation}


def capability_scope_granted(db: Session, user_id: int, plugin_id: str, scope: str) -> bool:
    operation = "propose" if plugin_id == "plan_outcome" and scope == "plan.proposals" else "read"
    return bool(authorize_capability(db, user_id, plugin_id, operation, scope, "new_work").get("allowed"))


def health_state_excluded_sources(db: Session, user_id: int) -> set[str]:
    """Source domains a Harness read must omit without a live matching grant."""
    excluded: set[str] = set()
    if not capability_scope_granted(db, user_id, "health_state", "health.goals.read"):
        excluded.add("goal")
    if not capability_scope_granted(db, user_id, "motion_evidence", "motion.analysis.read"):
        excluded.add("motion_analysis")
    if not capability_scope_granted(db, user_id, "plan_outcome", "plan.outcomes.read"):
        excluded.update({"plan", "experiment"})
    if not capability_scope_granted(db, user_id, "plan_outcome", "plan.goals.read"):
        excluded.add("user_preference")
    if not capability_scope_granted(db, user_id, "plan_outcome", "plan.proposals"):
        excluded.add("plan_proposals")
    return excluded


def is_tool_enabled(db: Session, user_id: int, tool_name: str) -> bool:
    return bool(authorize_tool(db, user_id, tool_name).get("allowed"))


def record_tool_access(db: Session, user_id: int, tool_name: str, *, allowed: bool, status: str, reason: str | None = None) -> None:
    binding = _tool_binding(tool_name)
    if binding is None:
        return
    row = _installation(db, user_id, binding.plugin_id)
    if row is None:
        return
    if allowed:
        row.last_run_at = utc_now()
        row.last_error = "" if status in {"ok", "approval_required"} else "最近一次能力运行未能完成"
    elif reason:
        row.last_error = {
            "capability_paused": "能力已暂停",
            "scope_not_granted": "所需数据范围未授权",
            "proposals_disabled": "行动提案未开启",
            "manifest_unreviewed": "审核版本需要重新确认",
        }.get(reason, "能力访问未获授权")
    db.add(HarnessCapabilityAudit(
        user_id=user_id,
        plugin_id=binding.plugin_id,
        installation_id=row.id,
        event_type="tool_access" if allowed else "tool_blocked",
        idempotency_key=None,
        request_hash="",
        config_version=row.config_version,
        detail_json=json.dumps({"tool": tool_name, "status": status, "reason": reason}, ensure_ascii=False, sort_keys=True),
        response_json="{}",
    ))


def record_capability_api_access(
    db: Session,
    user_id: int,
    plugin_id: str,
    *,
    route: str,
    allowed: bool,
    reason: str | None = None,
) -> None:
    row = _installation(db, user_id, plugin_id)
    maintenance = route == "outbox_maintenance"
    context_access = route == "agent_context.read"
    db.add(HarnessCapabilityAudit(
        user_id=user_id, plugin_id=plugin_id, installation_id=row.id if row else None,
        event_type="maintenance" if maintenance else "context_access" if context_access and allowed else "api_access" if allowed else "api_blocked",
        idempotency_key=None, request_hash="", config_version=row.config_version if row else 0,
        detail_json=json.dumps({"surface": plugin_id, "route": route, "reason": reason}, ensure_ascii=False, sort_keys=True),
        response_json="{}",
    ))
    if allowed and row is not None and not maintenance:
        row.last_run_at = utc_now()
        row.last_error = ""
    elif row is not None and reason and not maintenance:
        row.last_error = "当前配置未授权这项个人策略操作"


def write_audit(
    db: Session,
    *,
    user_id: int,
    plugin_id: str,
    installation_id: int | None,
    event_type: str,
    idempotency_key: str | None,
    request_hash: str,
    config_version: int,
    detail: dict[str, Any],
    response: dict[str, Any],
) -> HarnessCapabilityAudit:
    row = HarnessCapabilityAudit(
        user_id=user_id, plugin_id=plugin_id, installation_id=installation_id,
        event_type=event_type, idempotency_key=idempotency_key,
        request_hash=request_hash, config_version=config_version,
        detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str),
        response_json=json.dumps(response, ensure_ascii=False, sort_keys=True, default=str),
    )
    db.add(row)
    return row


def audit_idempotent_result(db: Session, user_id: int, key: str, request_hash: str) -> dict[str, Any] | None:
    row = db.scalar(select(HarnessCapabilityAudit).where(
        HarnessCapabilityAudit.user_id == user_id,
        HarnessCapabilityAudit.idempotency_key == key,
    ))
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise PluginError("IDEMPOTENCY_PARAM_MISMATCH", "相同操作编号不能对应不同的配置内容")
    try:
        value = json.loads(row.response_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def audit_request_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def response_preferences(db: Session, user_id: int) -> dict[str, Any]:
    rows = db.scalars(select(HarnessPluginInstallation).where(
        HarnessPluginInstallation.user_id == user_id,
        HarnessPluginInstallation.enabled.is_(True),
    ).order_by(HarnessPluginInstallation.updated_at.desc(), HarnessPluginInstallation.id.desc())).all()
    preferences: list[dict[str, str]] = []
    for row in rows:
        manifest = PLUGIN_BY_ID.get(row.plugin_id)
        if manifest is None or row.reviewed_manifest_hash != manifest.manifest_hash():
            continue
        try:
            config = normalize_config(
                manifest,
                _clean_legacy_config(manifest, _raw_config(row)),
                base=default_config(manifest),
            )
        except PluginError:
            continue
        goal_label = next((label for goal_id, label in manifest.goals if goal_id == config.get("goal")), "")
        if config.get("output_style") not in {"balanced", "concise", "evidence_first"}:
            continue
        preferences.append({
            "plugin_id": manifest.plugin_id,
            "plugin": manifest.title,
            "goal": goal_label,
            "style": config["output_style"],
            "notification_frequency": str(config.get("notification_frequency") or "off"),
        })
    primary = preferences[0] if preferences else {}
    return {
        # Keep the legacy summary fields for callers that only need a single
        # default, while the agent prompt consumes every capability preference.
        "style": primary.get("style", "balanced"),
        "goal": primary.get("goal", ""),
        "notification_frequency": "on_request" if any(
            item["notification_frequency"] == "on_request" for item in preferences
        ) else "off",
        "capabilities": preferences,
    }
