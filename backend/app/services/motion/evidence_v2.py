"""V2 worker-receipt → internal evidence adapter (spec §7.3).

The post-processing pipeline used to read the V1 keys ``recognition`` / ``pose``
/ ``score`` off a ``motion-worker-v2`` receipt. Those keys do not exist in a V2
receipt, so every V2 field was silently discarded and the run degraded to an
empty evidence bundle.

``evidence_from_worker_v2`` is the single adapter that fixes this: it validates
the receipt against the frozen ``MotionWorkerResultV2`` contract and translates
every group into the internal evidence shape the decision gate already consumes,
so a rejected six-class label can no longer erase the pose, frame or kinetics
evidence.

Forbidden by contract (and by the tests in
``backend/tests/test_motion_v2_live_contract.py``):

* reading V1 keys and returning an empty bundle when they are absent;
* dropping the frame pool because the final category is unknown;
* cross-sorting Kinetics softmax against pose rule scores (sources are kept in
  separate namespaces and only the kinetics block is passed to
  ``_normalize_kinetics``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.worker import (
    MotionWorkerResultV2,
    V2EvidenceFrame,
    V2Measurements,
    V2PoseEvidence,
    V2Subject,
    V2VideoQuality,
)

KINETICS_SOURCE = "kinetics"


@dataclass(frozen=True)
class MotionEvidenceBundle:
    """Validated, typed V2 evidence for one run (spec §7.3)."""

    video_quality: V2VideoQuality
    subject: V2Subject
    pose_evidence: V2PoseEvidence
    recognition_candidates: list[Any] = field(default_factory=list)
    frames: list[V2EvidenceFrame] = field(default_factory=list)
    measurements: V2Measurements | None = None
    model_versions: dict[str, str] = field(default_factory=dict)
    pipeline_version: str | None = None

    @property
    def pose_candidate(self):
        """The local pose namespace's top candidate, if any (never kinetics)."""
        for candidate in self.recognition_candidates:
            if candidate.source != KINETICS_SOURCE:
                return candidate
        return None

    @property
    def kinetics_candidates(self) -> list[Any]:
        return [
            candidate
            for candidate in self.recognition_candidates
            if candidate.source == KINETICS_SOURCE
        ]

    def kinds(self) -> list[dict[str, Any]]:
        """The six evidence groups, kept verbatim for diagnostics/tests."""
        return [
            {"group": "video_quality", "available": self.video_quality.available},
            {"group": "subject", "available": self.subject.available},
            {"group": "pose_evidence", "available": self.pose_evidence.available},
            {
                "group": "recognition_candidates",
                "count": len(self.recognition_candidates),
            },
            {"group": "frames", "count": len(self.frames)},
            {
                "group": "measurements",
                "available": bool(self.measurements and self.measurements.available),
            },
        ]

    def to_apply_post_review(self) -> dict[str, Any]:
        """Translate the V2 groups into the internal evidence dict.

        ``apply_post_review`` fuses local evidence with an already-produced
        CoachReview, so the shape below is the internal V1-compatible contract,
        not a second wire format.
        """
        pose_candidate = self.pose_candidate
        measurements = self.measurements
        pose_available = bool(self.pose_evidence.available)
        # A measured cycle is the only thing that may set ``reps``; pose frame
        # count alone is not a repetition count.
        reps = (
            measurements.reps
            if measurements is not None
            and measurements.available
            and isinstance(measurements.reps, int)
            else None
        )
        recognition: dict[str, Any] = {
            "accepted": pose_candidate is not None and pose_available,
            "selected_type": pose_candidate.canonical_id if pose_candidate else None,
            "requested_type": None,
            "method": self.pipeline_version or "motion_unified_v2",
            "reason": self.pose_evidence.measurement_summary or "",
            "candidates": [
                {
                    "exercise_type": candidate.canonical_id,
                    "source": candidate.source,
                    "source_label": candidate.source_label,
                    "raw_score": candidate.raw_score,
                    "score_type": candidate.score_type,
                }
                for candidate in self.recognition_candidates
                if candidate.canonical_id
            ][:5],
        }
        pose: dict[str, Any] = {
            "available": pose_available,
            "exercise_type": (
                measurements.exercise_id if measurements is not None else None
            )
            or (pose_candidate.canonical_id if pose_candidate else None),
            "reps": reps,
            "keypoint_valid_rate": self.pose_evidence.keypoint_valid_rate,
            "sample_count": self.pose_evidence.sample_count or len(
                self.pose_evidence.frame_ids
            ),
            "message": self.pose_evidence.measurement_summary,
            "errors": [],
        }
        quality = (
            dict(measurements.quality)
            if measurements is not None and isinstance(measurements.quality, dict)
            else {}
        )
        score_in: dict[str, Any] = {
            "available": bool(
                measurements is not None and measurements.available and reps
            ),
            "reps": reps,
            "duration_ms": (
                measurements.duration_ms if measurements is not None else None
            ),
        }
        if quality:
            score_in.update(quality)
        return {
            "recognition": recognition,
            "pose": pose,
            "score": score_in,
            # The frame pool survives even when the six-class label is rejected.
            "frames": [
                frame.model_dump(exclude_none=True) for frame in self.frames
            ],
            # Only true kinetics candidates reach ``_normalize_kinetics``; pose
            # rule scores are never numerically mixed into that namespace.
            "kinetics": {
                "candidates": [
                    candidate.model_dump(exclude_none=True)
                    for candidate in self.kinetics_candidates
                ]
            },
            "candidate_ids": {
                candidate.canonical_id
                for candidate in self.recognition_candidates
                if candidate.canonical_id
            },
            "summary": {},
            "model_versions": dict(self.model_versions),
            "video_quality": self.video_quality.model_dump(exclude_none=True),
            "subject": self.subject.model_dump(exclude_none=True),
        }


def evidence_from_worker_v2(receipt: dict) -> MotionEvidenceBundle:
    """Validate a V2 receipt and translate it into the internal evidence bundle.

    Raises ``pydantic.ValidationError`` for a drifted producer: a silent
    ``extra="ignore"`` parse is exactly the failure mode this replaces.
    """
    parsed = MotionWorkerResultV2.model_validate(receipt)
    return MotionEvidenceBundle(
        video_quality=parsed.video_quality,
        subject=parsed.subject,
        pose_evidence=parsed.pose_evidence,
        recognition_candidates=list(parsed.recognition_candidates),
        frames=list(parsed.frames),
        measurements=parsed.measurements,
        model_versions=dict(parsed.model_versions),
        pipeline_version=parsed.pipeline_version,
    )
