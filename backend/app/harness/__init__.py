"""HealthMate health-agent harness kernel.

The package is deliberately independent from the Mini Program UI. It owns
user-facing agent profiles, Router/Worker/Decision orchestration, bounded
ReAct loops, tool registration and voice-provider adapters; domain services
remain in ``app.services`` and are exposed through tools.
"""

from app.harness.collaboration import MULTI_AGENT_VERSION, MultiAgentKernel, list_workers
from app.harness.kernel import HARNESS_VERSION, ReActKernel
from app.harness.personas import get_persona, list_personas
from app.harness.tools import get_tool_registry

__all__ = [
    "HARNESS_VERSION",
    "MULTI_AGENT_VERSION",
    "MultiAgentKernel",
    "ReActKernel",
    "get_persona",
    "list_personas",
    "list_workers",
    "get_tool_registry",
]
