"""Evidence-gated personal policy learning.

The package intentionally has a pure algorithm layer and a persistence/API layer.
The language model may propose a reviewed template, but it cannot write beliefs or
mark an episode successful.
"""

from .algorithm import (
    ALGORITHM_VERSION,
    GATE_VERSION,
    Adjudication,
    BetaBelief,
    Candidate,
    EpisodeEvidence,
    Point,
    Protocol,
    Scope,
    adjudicate,
    rank_candidates,
)
from .templates import TEMPLATE_REGISTRY, get_template

__all__ = [
    "ALGORITHM_VERSION", "GATE_VERSION", "Adjudication", "BetaBelief",
    "Candidate", "EpisodeEvidence", "Point", "Protocol", "Scope",
    "adjudicate", "rank_candidates", "TEMPLATE_REGISTRY", "get_template",
]
