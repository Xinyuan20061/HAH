"""User-facing, evidence-aware Harness capability plugins.

Plugins are reviewed capability packages, not arbitrary user-uploaded code.  A
plugin describes a useful outcome, its data boundary and whether it may propose
an action.  Runtime tools still pass through the existing least-privilege
ToolRegistry and action-confirmation protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import HarnessPluginInstallation


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
    default_enabled: bool = True
    reversible: bool = True
    reviewed: bool = True

    def public_dict(self, *, enabled: bool = False, config: dict[str, Any] | None = None, include_internal: bool = False) -> dict[str, Any]:
        payload = {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "user_value": self.user_value,
            "data_needed": list(self.data_needed),
            "privacy_summary": self.privacy_summary,
            "enabled": enabled,
            "reversible": self.reversible,
            "reviewed": self.reviewed,
            "config": config or {},
        }
        if include_internal:
            payload["tool_names"] = list(self.tool_names)
            payload["action_names"] = list(self.action_names)
        return payload


BUILTIN_PLUGINS: tuple[PluginManifest, ...] = (
    PluginManifest(
        "health_state", "1.0.0", "健康状态", "把记录整理成当前状态、缺口和约束。",
        "知道今天适合做什么，以及为什么暂时不能判断。",
        ("健康档案", "饮食、运动和打卡记录"), "只读取本人数据，不向第三方开放。",
        ("health.context.read", "harness.state.read", "harness.constraints.read"),
    ),
    PluginManifest(
        "personal_policy", "1.0.0", "个人策略学习", "把一次建议变成可执行、可复查的个人周期。",
        "逐渐发现什么方法在什么情况下更适合你。",
        ("目标", "执行记录", "结果记录"), "只使用本人授权的记录；结论可撤销、可重置。",
        ("policy.templates.read", "policy.candidates.preview", "policy.episode.read", "policy.evidence.read", "policy.memory.read"),
        ("policy.episode.start", "policy.episode.finish", "policy.episode.stop", "policy.memory.reset"),
    ),
    PluginManifest(
        "motion_evidence", "1.0.0", "动作证据", "读取动作分析、关键帧和历史可比结果。",
        "看懂动作反馈，并知道哪些结论有真实画面依据。",
        ("本人上传的动作视频", "动作分析结果"), "视频和分析只用于本人账户，不把模型点评当作医学诊断。",
        ("motion.analysis.read", "motion.timeline.read", "motion.history.compare"),
    ),
    PluginManifest(
        "plan_outcome", "1.0.0", "计划与结果", "比较计划执行、负担和结果，帮助调整下一步。",
        "计划会根据真实执行情况变得更容易坚持。",
        ("计划任务", "执行结果", "用户反馈"), "只读取本人计划和结果，不自动替用户改计划。",
        ("harness.plan.simulate", "harness.outcomes.history.read", "harness.next_action.rank"),
    ),
)

TOOL_PLUGIN_PREFIXES = (
    ("policy.", "personal_policy"),
    ("motion.", "motion_evidence"),
    ("health.", "health_state"),
    ("harness.state.", "health_state"),
    ("harness.constraints.", "health_state"),
    ("harness.signals.", "health_state"),
    ("harness.plan.", "plan_outcome"),
    ("harness.outcomes.", "plan_outcome"),
    ("harness.next_action.", "plan_outcome"),
)


class PluginError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def get_plugin(plugin_id: str) -> PluginManifest:
    for item in BUILTIN_PLUGINS:
        if item.plugin_id == plugin_id:
            return item
    raise PluginError("PLUGIN_NOT_FOUND", "健康能力不存在或尚未审核")


def _installation(db: Session, user_id: int, plugin_id: str) -> HarnessPluginInstallation | None:
    return db.scalar(select(HarnessPluginInstallation).where(HarnessPluginInstallation.user_id == user_id, HarnessPluginInstallation.plugin_id == plugin_id))


def list_plugins(db: Session, user_id: int, *, include_internal: bool = False) -> list[dict[str, Any]]:
    rows = {row.plugin_id: row for row in db.scalars(select(HarnessPluginInstallation).where(HarnessPluginInstallation.user_id == user_id)).all()}
    output = []
    for manifest in BUILTIN_PLUGINS:
        row = rows.get(manifest.plugin_id)
        enabled = row.enabled if row is not None else manifest.default_enabled
        output.append(manifest.public_dict(enabled=enabled, config=_config(row), include_internal=include_internal))
    return output


def _config(row: HarnessPluginInstallation | None) -> dict[str, Any]:
    if row is None:
        return {}
    import json
    try:
        value = json.loads(row.config_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def set_plugin_enabled(db: Session, user_id: int, plugin_id: str, *, enabled: bool, data_scope: list[str] | None = None) -> dict[str, Any]:
    manifest = get_plugin(plugin_id)
    import json
    if data_scope is not None:
        # A client may only narrow a reviewed capability's declared boundary;
        # silently accepting arbitrary labels would make the consent record
        # misleading and would be impossible to audit later.
        allowed = set(manifest.data_needed)
        unknown = [item for item in data_scope if item not in allowed]
        if unknown:
            raise PluginError("PLUGIN_SCOPE_INVALID", "只能授权该健康能力声明的数据范围")
        data_scope = list(dict.fromkeys(data_scope))
    row = _installation(db, user_id, plugin_id)
    if row is None:
        row = HarnessPluginInstallation(user_id=user_id, plugin_id=plugin_id, plugin_version=manifest.version, enabled=enabled, config_json=json.dumps({"data_scope": data_scope or list(manifest.data_needed), "notifications": True}, ensure_ascii=False), enabled_at=utc_now() if enabled else None, disabled_at=None if enabled else utc_now())
        db.add(row)
    else:
        row.enabled = enabled
        row.plugin_version = manifest.version
        config = _config(row)
        if data_scope is not None:
            config["data_scope"] = data_scope
        row.config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        row.enabled_at = utc_now() if enabled else row.enabled_at
        row.disabled_at = None if enabled else utc_now()
    db.flush()
    return manifest.public_dict(enabled=enabled, config=_config(row))


def enabled_plugin_ids(db: Session, user_id: int) -> set[str]:
    return {item["plugin_id"] for item in list_plugins(db, user_id) if item["enabled"]}


def plugin_for_tool(tool_name: str) -> str | None:
    for prefix, plugin_id in TOOL_PLUGIN_PREFIXES:
        if tool_name.startswith(prefix):
            return plugin_id
    return None


def is_tool_enabled(db: Session, user_id: int, tool_name: str) -> bool:
    plugin_id = plugin_for_tool(tool_name)
    return plugin_id is None or plugin_id in enabled_plugin_ids(db, user_id)
