from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.harness.contracts import ToolContext, ToolObservation, ToolSpec


class ToolRegistry:
    """Allow-list for every capability visible to the agent loop."""

    def __init__(self, tools: Iterable[ToolSpec] = ()):
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or spec.name in self._tools:
            raise ValueError(f"重复或无效的工具名: {spec.name}")
        self._tools[spec.name] = spec

    def manifest(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return [
            spec.manifest()
            for spec in self._tools.values()
            if not agent_id or agent_id in spec.allowed_agents
        ]

    def scoped(self, names: Iterable[str]) -> "ToolRegistry":
        """Return a least-privilege view for one collaborating agent.

        A worker never receives the global registry by default. Unknown names
        are ignored so a stale worker profile cannot accidentally broaden its
        permissions when tools are added later.
        """
        allowed = set(names)
        return ToolRegistry(
            spec for name, spec in self._tools.items() if name in allowed
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: dict[str, Any] | None = None,
        *,
        confirmed: bool = False,
        step: int = 0,
    ) -> ToolObservation:
        spec = self._tools.get(name)
        if spec is None or context.agent_id not in spec.allowed_agents:
            return ToolObservation(name, "blocked", None, "工具未注册或当前智能体无权使用", step)
        if spec.kind == "action" and (spec.proposal_only or (spec.requires_confirmation and not confirmed)):
            return ToolObservation(
                name,
                "approval_required",
                {
                    "tool": name,
                    "title": spec.title,
                    "risk_level": spec.risk_level,
                    "requires_confirmation": True,
                },
                "写操作已停在用户确认前",
                step,
            )
        try:
            output = spec.handler(context, arguments or {})
            return ToolObservation(name, "ok", output, _summarize(output), step)
        except Exception as exc:
            return ToolObservation(name, "error", None, f"工具执行失败: {type(exc).__name__}", step)


def _summarize(value: Any) -> str:
    if isinstance(value, list):
        return f"返回 {len(value)} 项"
    if isinstance(value, dict):
        keys = list(value)[:5]
        return "返回字段 " + "、".join(str(key) for key in keys)
    if value is None:
        return "没有可用结果"
    return "返回一项结果"
