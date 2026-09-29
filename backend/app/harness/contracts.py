from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from sqlalchemy.orm import Session


ToolKind = Literal["read", "action"]
ToolHandler = Callable[["ToolContext", dict[str, Any]], Any]


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    role: str
    description: str
    system_prompt: str
    greeting: str
    placeholder: str
    voice_input: bool = False
    voice_output: bool = False
    max_reply_sentences: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "greeting": self.greeting,
            "placeholder": self.placeholder,
            "capabilities": {
                "text": True,
                "voice_input": self.voice_input,
                "voice_output": self.voice_output,
            },
        }


@dataclass
class ToolContext:
    db: Session
    user: Any
    agent_id: str
    channel: str = "text"
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    handler: ToolHandler
    kind: ToolKind = "read"
    risk_level: str = "low"
    requires_confirmation: bool = False
    allowed_agents: tuple[str, ...] = ("xiaojian", "xiaokang", "steward")
    input_schema: dict[str, Any] = field(default_factory=dict)
    proposal_only: bool = False

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "allowed_agents": list(self.allowed_agents),
            "input_schema": self.input_schema,
            "proposal_only": self.proposal_only,
        }


@dataclass
class ToolObservation:
    tool: str
    status: str
    output: Any
    summary: str
    step: int = 0

    def prompt_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "status": self.status, "output": self.output}

    def trace_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass
class HarnessLoopResult:
    result: dict[str, Any] | None
    provider: str
    observations: list[ToolObservation]
    stop_reason: str
    raw_text: str = ""

