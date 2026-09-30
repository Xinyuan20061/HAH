"""Unified motion decision gate (V2 rebuild, spec §3.3 / §5 / §6).

Pure, deterministic code. No model confidence is blended into an external
percentage. The gate no longer treats the cloud visual review as a mandatory
precondition for recognition (R03):

* A reliable local six-class result stands on its own. When the cloud review is
  absent, skipped or timed out, the local action name and its applicable
  repetitions are RETAINED; the cloud review is a supplement / correction source,
  never the gate that discards what the pose stack already established.
* The visual review is allowed to propose open categories (R06): a label that
  is not in the local candidate set is accepted when it is backed by grounded
  observations, surfaced as ``likely`` with the Chinese label the model gave.
  It is never force-mapped to the nearest legacy six-class action.
* Recognition order (spec §5 ``resolve_analysis``):
      vision grounded  -> recognition from vision
      local reliable   -> identified(local, source="pose")
      kinetics usable  -> likely(kinetics, source="video_model")
      else             -> unknown_with_observations
* Metrics are bound to the measured exercise (spec §6.4): local reps/scores are
  reused ONLY when the final recognised canonical id equals the locally measured
  exercise AND the local metrics are valid. If the cloud corrects the category,
  the old category's reps/scores are dropped (T05).

``supported_by_observations`` is NOT "the model says it is confident": it is
enforced upstream by C package (coach_review semantic validation) and means the
frame references exist, observations match visible body parts, dynamic claims
have a time window, and the category does not structurally contradict the
description. A reliable local result never needs "at least three key events" to
be kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# Six exercise families that ship a deterministic, validated rule scorer.
SUPPORTED_SCORERS = frozenset(
    {"squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw"}
)

EXERCISE_ID_RE = r"^[a-z][a-z0-9_]{0,40}$"

# Fallback zh names; the catalog accessor (21 actions) is the source of truth.
LABEL_ZH = {
    "squat": "深蹲",
    "pushup": "俯卧撑",
    "lunge": "弓步蹲",
    "leg_abduction": "站姿腿外展",
    "arm_abduction": "站姿臂外展",
    "arm_vw": "手臂靠墙静力",
}

# Initial safe-gate draft thresholds (production thresholds must be calibrated on
# an independent validation set; these are P0 safety defaults).
MIN_USABLE_FRAMES = 3
MIN_KEYPOINT_VALID_RATE = 0.5

RecognitionState = Literal["identified", "likely", "unknown"]


@dataclass(frozen=True)
class LocalEvidence:
    """What the local pose stack could establish on its own.

    ``accepted``      : the rule stack selected a local action.
    ``label_id``      : that local action id (may be None).
    ``measured_exercise_id`` : the exercise the counter/scorer actually measured.
                     Usually equals ``label_id``; kept separate so a category
                     correction can invalidate inherited metrics.
    ``metrics_valid`` : local reps/scores are usable for ``measured_exercise_id``.
    """

    accepted: bool
    label_id: str | None
    measured_exercise_id: str | None = None
    metrics_valid: bool = False


@dataclass(frozen=True)
class QualityEvidence:
    has_person: bool
    usable_frames: int
    pose_reliable: bool
    full_cycle: bool
    has_video: bool = True


@dataclass(frozen=True)
class KineticsCandidate:
    """A normalised Kinetics-400 candidate (spec §6.2).

    ``raw_score`` and pose scores are NOT comparable across sources: they are
    never sorted numerically against each other. ``canonical_id`` is None when
    the catalog has no mapping; the raw label is then kept as visual reference
    and never force-mapped to a legacy six-class action.
    """

    source_label: str
    canonical_id: str | None
    raw_score: float
    score_type: str = "softmax"


@dataclass(frozen=True)
class VisionEvidence:
    """Grounded visual review (already schema + semantics validated by C).

    ``canonical_id``        : catalog id the vision settled on, or None.
    ``novel_label_zh``      : open-category Chinese name when no catalog id maps.
    ``identification``      : identified | likely | unknown (vision's own read).
    ``supported_by_observations`` : grounded per the module docstring.
    """

    canonical_id: str | None
    novel_label_zh: str | None
    identification: Literal["identified", "likely", "unknown"]
    identification_reason: str
    supported_by_observations: bool
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recognition:
    """External recognition block (contract §3 display)."""

    state: RecognitionState
    canonical_id: str | None
    display_name: str
    reason: str
    source: Literal["vision", "pose", "video_model", "local"]
    novel_label_zh: str | None = None
    likely_label: str | None = None


@dataclass(frozen=True)
class MotionDecision:
    recognition: Recognition
    scoreable: bool
    metrics_applicable: bool
    sources: tuple[str, ...] = field(default_factory=tuple)
    reason_code: str = "OK"
    # The exercise the local metrics were measured for (None if category changed).
    measured_exercise_id: str | None = None


def quality_from_receipt(recognition: dict | None, pose: dict | None, frames: list) -> QualityEvidence:
    pose = pose or {}
    recognition = recognition or {}
    usable_frames = len([f for f in frames if isinstance(f, dict)])
    pose_available = bool(pose.get("available"))
    accepted = bool(recognition.get("accepted"))
    # The receipt contract guarantees pose.available iff keypoints were measured;
    # a recognized local candidate or any frame is weak evidence of a person.
    has_person = bool(pose_available or accepted or usable_frames > 0)
    rate = pose.get("keypoint_valid_rate")
    pose_reliable = bool(
        pose_available
        and isinstance(rate, (int, float))
        and not isinstance(rate, bool)
        and rate >= MIN_KEYPOINT_VALID_RATE
    )
    reps = pose.get("reps")
    full_cycle = bool(pose_available and isinstance(reps, int) and reps >= 1)
    has_video = bool(pose_available or usable_frames > 0)
    return QualityEvidence(
        has_person=has_person,
        usable_frames=usable_frames,
        pose_reliable=pose_reliable,
        full_cycle=full_cycle,
        has_video=has_video,
    )


def _zh_name(canonical_id: str | None, novel_label_zh: str | None = "") -> str:
    if novel_label_zh:
        return novel_label_zh
    if not canonical_id:
        return ""
    try:  # catalog accessor is a pure local read; never fail the gate on it.
        from app.services.motion import catalog

        action = catalog.get_action(canonical_id)
        if action:
            return action["name_zh"]
    except Exception:  # pragma: no cover - defensive; catalog always ships
        pass
    return LABEL_ZH.get(canonical_id, canonical_id)


def _display(state: RecognitionState, name_zh: str, unknown_hint: str) -> str:
    if state == "identified" and name_zh:
        return name_zh
    if state == "likely" and name_zh:
        return f"看起来是{name_zh}"
    return unknown_hint or "暂未确定动作类别"


def _best_kinetics(kinetics: tuple[KineticsCandidate, ...]) -> Optional[KineticsCandidate]:
    """Pick the most usable kinetics candidate WITHOUT numeric cross-source sort.

    Prefer a catalog-mapped candidate; among mapped ones prefer the highest raw
    score within the same score_type namespace. Unmapped raw labels are still
    retained by the caller for visual reference, but do not become a confident
    identification on a high softmax alone (spec §3.3.2).
    """
    mapped = [k for k in kinetics if k.canonical_id]
    if mapped:
        # same score_type namespace only; never compare softmax to rule scores.
        namespaces: dict[str, list[KineticsCandidate]] = {}
        for k in mapped:
            namespaces.setdefault(k.score_type, []).append(k)
        best_ns: list[KineticsCandidate] = max(namespaces.values(), key=len)
        return max(best_ns, key=lambda k: k.raw_score)
    return None


def decide_motion(
    local: LocalEvidence,
    vision: VisionEvidence | None,
    kinetics: tuple[KineticsCandidate, ...],
    evidence: QualityEvidence,
) -> MotionDecision:
    """Resolve the final recognition + metric applicability. Pure & deterministic."""
    sources: list[str] = ["local_worker"]
    kinetics = kinetics or ()
    if kinetics:
        sources.append("kinetics400")

    # Gate 0: no usable video evidence at all -> abstain, do not call models.
    if not evidence.has_person or evidence.usable_frames < MIN_USABLE_FRAMES:
        rec = Recognition(
            state="unknown",
            canonical_id=None,
            display_name="有效画面不足，暂时无法判断动作。",
            reason="没有检测到足够的人物动作帧。",
            source="local",
        )
        return MotionDecision(
            recognition=rec,
            scoreable=False,
            metrics_applicable=False,
            sources=("local_worker",),
            reason_code="INSUFFICIENT_VIDEO_EVIDENCE",
            measured_exercise_id=None,
        )

    local_reliable = bool(
        local.accepted and local.label_id and evidence.pose_reliable
    )

    rec: Recognition
    reason_code = "OK"

    # ---- Resolution order (spec §5) ------------------------------------- #
    # 1) Grounded vision review wins as a correction / independent identification.
    if vision is not None and vision.supported_by_observations and vision.identification != "unknown":
        sources.append("deepseek_vision")
        cid = vision.canonical_id
        zh = _zh_name(cid, vision.novel_label_zh)
        state: RecognitionState = vision.identification
        # Vision may propose an open (unmapped) category: never auto-register a
        # scorer, surface it as likely so the user can confirm (R06 / §6.3).
        if cid is None:
            state = "likely"
        rec = Recognition(
            state=state,
            canonical_id=cid,
            display_name=_display(state, zh, vision.identification_reason or "看起来是一段上肢训练。"),
            reason=vision.identification_reason or "依据画面关键帧观察判断。",
            source="vision",
            novel_label_zh=vision.novel_label_zh,
        )
        # Local pose disagreed with the vision call: keep vision (it observed the
        # actual frames) but soften to likely when local pose was reliable and the
        # vision said "identified", so the user can confirm (§3.3.4). We never
        # clear the known content; metrics binding handles score invalidation.
        if (
            state == "identified"
            and local_reliable
            and local.label_id
            and cid is not None
            and local.label_id != cid
        ):
            rec = Recognition(
                state="likely",
                canonical_id=cid,
                display_name=f"看起来是{zh}",
                reason=(vision.identification_reason or "画面观察判断") + "；与本地动作轨迹略有出入，可确认一下。",
                source="vision",
                novel_label_zh=vision.novel_label_zh,
                likely_label=local.label_id,
            )
            reason_code = "VISION_LOCAL_DISAGREE_LIKELY"

    # 2) No grounded vision: a reliable local result stands on its own (R03).
    elif local_reliable:
        zh = _zh_name(local.label_id)
        rec = Recognition(
            state="identified",
            canonical_id=local.label_id,
            display_name=zh or local.label_id or "动作",
            reason="本地动作轨迹识别可靠，已保留动作名称与可用次数。",
            source="pose",
        )
        reason_code = "LOCAL_RELIABLE"

    # 3) Only a usable kinetics candidate -> likely, never auto-identified/scored.
    else:
        best = _best_kinetics(kinetics)
        if best is not None and evidence.has_video:
            zh = _zh_name(best.canonical_id, best.source_label)
            rec = Recognition(
                state="likely",
                canonical_id=best.canonical_id,
                display_name=f"看起来是{zh}",
                reason=f"视频候选提示为{best.source_label}，证据有限，请确认动作类别。",
                source="video_model",
            )
            reason_code = "KINETICS_LIKELY"
        else:
            # 4) Unknown, but still describe visible motion (never "no person").
            hint = "能看到人物活动，但画面不足以确定动作类别。"
            if local.accepted and local.label_id:
                hint = f"本地识别为{_zh_name(local.label_id)}，但姿态质量不足以确认。"
            rec = Recognition(
                state="unknown",
                canonical_id=None,
                display_name=hint,
                reason="各来源证据不足以形成可靠类别判断。",
                source="local",
            )
            reason_code = "UNKNOWN_WITH_OBSERVATIONS"

    # ---- Metrics binding (spec §6.4 / §3.3.5) --------------------------- #
    measured_id = local.measured_exercise_id or (
        local.label_id if local.accepted else None
    )
    same_exercise = bool(
        rec.canonical_id is not None
        and measured_id is not None
        and rec.canonical_id == measured_id
    )
    metrics_applicable = bool(same_exercise and local.metrics_valid)
    scoreable = bool(
        metrics_applicable
        and rec.canonical_id in SUPPORTED_SCORERS
        and evidence.full_cycle
        and evidence.pose_reliable
    )

    return MotionDecision(
        recognition=rec,
        scoreable=scoreable,
        metrics_applicable=metrics_applicable,
        sources=tuple(dict.fromkeys(sources)),
        reason_code=reason_code,
        measured_exercise_id=measured_id if metrics_applicable else None,
    )
