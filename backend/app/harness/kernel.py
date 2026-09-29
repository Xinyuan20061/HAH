from __future__ import annotations

import json
from typing import Any

from app.core.json_output import json_object
from app.harness.contracts import (
    AgentProfile,
    HarnessLoopResult,
    ToolContext,
    ToolObservation,
)
from app.harness.registry import ToolRegistry


HARNESS_VERSION = "health-harness-v2"


class ReActKernel:
    """Small, bounded ReAct loop with an allow-listed tool boundary.

    The model never receives permission to execute arbitrary functions.  It can
    request a registered tool, observe the result and continue.  Private chain
    of thought is neither requested nor persisted; the audit trace only stores
    tool names, statuses and compact result summaries.
    """

    def __init__(self, registry: ToolRegistry, max_steps: int = 3):
        self.registry = registry
        self.max_steps = max(1, min(5, int(max_steps)))

    async def run(
        self,
        *,
        provider,
        persona: AgentProfile,
        tool_context: ToolContext,
        system: str,
        task_prompt: str,
        observations: list[ToolObservation] | None = None,
    ) -> HarnessLoopResult:
        history = list(observations or [])
        used = {item.tool for item in history}
        last_text = ""
        provider_name = getattr(provider, "provider_name", "unknown")
        for step in range(1, self.max_steps + 1):
            prompt = self._prompt(persona, task_prompt, history)
            response = await provider.chat(system + "\n" + persona.system_prompt, prompt)
            provider_name = response.provider
            last_text = response.text
            decision = _parse_decision(last_text)
            if decision.get("type") == "final":
                return HarnessLoopResult(
                    decision.get("result"), provider_name, history, "final", last_text
                )
            if decision.get("type") != "tool":
                return HarnessLoopResult(None, provider_name, history, "invalid_output", last_text)
            name = str(decision.get("tool") or "")
            if name in used:
                history.append(ToolObservation(name, "blocked", None, "同一轮不重复调用工具", step))
                return HarnessLoopResult(None, provider_name, history, "repeated_tool", last_text)
            observation = self.registry.execute(
                name,
                tool_context,
                decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {},
                confirmed=False,
                step=step,
            )
            history.append(observation)
            used.add(name)
            if observation.status in {"blocked", "approval_required"}:
                continue
        return HarnessLoopResult(None, provider_name, history, "step_limit", last_text)

    def _prompt(
        self,
        persona: AgentProfile,
        task_prompt: str,
        observations: list[ToolObservation],
    ) -> str:
        manifest = self.registry.manifest(persona.id)
        obs = [item.prompt_dict() for item in observations]
        protocol = {
            "tool_call": {
                "action": "tool",
                "tool": "必须来自 tools.name",
                "arguments": {},
            },
            "final": {
                "action": "final",
                "result": {
                    "reply": "给用户的回答",
                    "facts_used": [],
                    "plan": None,
                },
            },
        }
        return (
            task_prompt
            + "\n\n你运行在 HealthMate ReAct Harness 中。不要输出思维过程。"
            + "每一步只返回一个 JSON：调用工具或给出最终结果。写操作只可提出申请，不能绕过用户确认。"
            + "\nTOOLS="
            + json.dumps(manifest, ensure_ascii=False)
            + "\nOBSERVATIONS="
            + json.dumps(obs, ensure_ascii=False, default=str)
            + "\nPROTOCOL="
            + json.dumps(protocol, ensure_ascii=False)
        )


def _parse_decision(text: str) -> dict[str, Any]:
    try:
        data = json_object(text)
    except Exception:
        return {"type": "invalid"}
    if not isinstance(data, dict):
        return {"type": "invalid"}
    # Backward-compatible final contract used by the existing provider prompts.
    if isinstance(data.get("reply"), str):
        return {"type": "final", "result": data}
    action = str(data.get("action") or "").lower()
    if action == "tool":
        return {
            "type": "tool",
            "tool": data.get("tool"),
            "arguments": data.get("arguments") or {},
        }
    if action == "final" and isinstance(data.get("result"), dict):
        return {"type": "final", "result": data["result"]}
    return {"type": "invalid"}
