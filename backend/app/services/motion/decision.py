"""Unified motion decision gate (spec 4.5).

Pure, deterministic code. No model confidence from DeepSeek is ever blended with
the local softmax into an external percentage: DeepSeek's self-reported confidence
is only an advisory reference and is intentionally NOT fed into decide_motion.

The gate errs on the side of doubt ("保留疑问"): insufficient evidence ->
abstain, missing/unknown review -> uncertain, unsupported label or disagreement
-> uncertain. Only when every layer agrees do we emit `recognized`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Six exercise families that ship a deterministic, validated rule scorer.
SUPPORTED_SCORERS = frozenset(
    {"squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw"}
)

EXERCISE_ID_RE = r"^[a-z][a-z0-9_]{0,40}$"

LABEL_ZH = {
    "squat": "深蹲",
    "pushup": "俯卧撑",
    "lunge": "弓步蹲",
    "leg_abduction": "站姿腿外展",
    "arm_abduction": "站姿臂外展",
    "arm_vw": "手臂靠墙静力",
}

# Initial safe-gate draft thresholds (spec 4.5 says production thresholds must be
# calibrated on an independent validation set; these are P0-B safety defaults).
MIN_USABLE_FRAMES = 3
MIN_KEYPOINT_VALID_RATE = 0.5


@dataclass(frozen=True)
class LocalEvidence:
    """What the local pose / Kinetics stack could establish on its own."""

    accepted: bool
    label_id: str | None


@dataclass(frozen=True)
class QualityEvidence:
    has_person: bool
    usable_frames: int
    pose_reliable: bool
    full_cycle: bool


@dataclass(frozen=True)
class ReviewEvidence:
    """Validated DeepSeek visual review (already schema-checked against the frame set)."""

    label_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class MotionDecision:
    state: Literal["recognized", "uncertain", "abstained"]
    label_id: str | None
    reason_code: str
    scoreable: bool
    sources: tuple[str, ...] = field(default_factory=tuple)


def quality_from_receipt(recognition: dict | None, pose: dict | None, frames: list) -> QualityEvidence:
    pose = pose or {}
    recognition = recognition or {}
    usable_frames = len([f for f in frames if isinstance(f, dict)])
    pose_available = bool(pose.get("available"))
    accepted = bool(recognition.get("accepted"))
    # The receipt contract guarantees pose.available iff keypoints were measured;
    # a recognized local candidate or any event frame is weak evidence of a person.
    has_person = bool(pose_available or accepted or usable_frames > 0)
    rate = pose.get("keypoint_valid_rate")
    pose_reliable = bool(
        pose_available
        and isinstance(rate, (int, float))
        and not isinstance(rate, bool)
        and rate >= MIN_KEYPOINT_VALID_RATE
    )
    reps = pose.get("reps")
    full_cycle = bool(
        pose_available and isinstance(reps, int) and reps >= 1
    )
    return QualityEvidence(
        has_person=has_person,
        usable_frames=usable_frames,
        pose_reliable=pose_reliable,
        full_cycle=full_cycle,
    )


def decide_motion(
    local: LocalEvidence,
    candidate_ids: set[str],
    review: ReviewEvidence | None,
    quality: QualityEvidence,
) -> MotionDecision:
    # Gate 1: no usable video evidence at all -> abstain outright, do not call models.
    if not quality.has_person or quality.usable_frames < MIN_USABLE_FRAMES:
        return MotionDecision(
            "abstained", None, "INSUFFICIENT_VIDEO_EVIDENCE", False,
            sources=("local_worker",),
        )

    sources: list[str] = ["local_worker"]
    if "kinetics" in (local.label_id or ""):
        pass
    sources.append("kinetics400" if candidate_ids else "mediapipe")

    # Gate 2: visual review missing / explicitly unknown -> keep the question.
    if review is None or review.label_id == "unknown":
        return MotionDecision(
            "uncertain", None, "REVIEW_UNAVAILABLE_OR_UNCERTAIN", False,
            sources=tuple(dict.fromkeys(sources)),
        )

    # Gate 3: the reviewer's label must be one of the offered candidates and it
    # must cite real frames; otherwise the review is unsupported -> do not adopt it.
    if review.label_id not in candidate_ids or not review.evidence_ids:
        return MotionDecision(
            "uncertain", None, "UNSUPPORTED_REVIEW", False,
            sources=tuple(dict.fromkeys(sources + ["deepseek_vision"])),
        )

    # Gate 4: local pose and the reviewer disagree while pose is reliable -> doubt.
    if (
        local.accepted
        and local.label_id
        and local.label_id != review.label_id
        and quality.pose_reliable
    ):
        return MotionDecision(
            "uncertain", None, "MODEL_DISAGREEMENT", False,
            sources=tuple(dict.fromkeys(sources + ["deepseek_vision"])),
        )

    label = review.label_id
    scoreable = bool(
        label in SUPPORTED_SCORERS and quality.full_cycle and quality.pose_reliable
    )
    return MotionDecision(
        "recognized",
        label,
        "EVIDENCE_AGREEMENT",
        scoreable,
        sources=tuple(dict.fromkeys(sources + ["deepseek_vision"])),
    )
