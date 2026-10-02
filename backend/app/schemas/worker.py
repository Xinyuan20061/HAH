from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


# Canonical worker->server motion receipt contract. This is the P0-A contract;
# the downstream P0-B/C unified read-model (analysis_id / keyframes / summary)
# is a later layer and intentionally NOT this worker receipt shape.
MOTION_WORKER_RESULT_SCHEMA_VERSION = "motion-worker-result-v1"
_MOTION_EXERCISE_ID_RE = r"^[a-z][a-z0-9_]{0,40}$"


class MotionResultSchemaError(Exception):
    """A worker motion receipt violated MotionWorkerResultV1.

    Carries a SAFE summary plus the first failing field path only; the raw
    payload (which may contain image base64) is never attached.
    """

    def __init__(self, message: str, field_path: str | None = None):
        super().__init__(message)
        self.message = message
        self.field_path = field_path


def motion_result_first_error_path(exc: ValidationError) -> str | None:
    """Convert a pydantic loc tuple into a dotted/bracketed field path.

    ("frames", 2, "image_mime") -> "frames[2].image_mime"
    """
    loc = exc.errors()[0]["loc"] if exc.errors() else ()
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts) if parts else None


def _is_real_number(value) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class MotionPoseContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    available: bool
    message: str | None = None
    exercise_type: str | None = None
    keypoint_valid_rate: float | None = None
    reps: int | None = None
    errors: list = Field(default_factory=list)

    @field_validator("errors")
    @classmethod
    def _errors_is_list(cls, value):
        if not isinstance(value, list):
            raise ValueError("动作提示必须是列表")
        return value

    @model_validator(mode="after")
    def _check(self):
        if not self.available:
            if not self.message:
                raise ValueError("不可评价时必须提供原因")
            return self
        if type(self.reps) is not int or not 0 <= self.reps <= 10000:
            raise ValueError("动作次数无效")
        if not _is_real_number(self.keypoint_valid_rate) or not (
            0 <= self.keypoint_valid_rate <= 1
        ):
            raise ValueError("关键点有效率无效")
        return self


class MotionScoreContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    available: bool
    completeness: float | None = None
    stability: float | None = None
    rhythm_control: float | None = None
    risk_index: float | None = None
    overall: float | None = None
    confidence: float | None = None

    @model_validator(mode="after")
    def _check(self):
        if not self.available:
            return self
        for key in (
            "completeness",
            "stability",
            "rhythm_control",
            "risk_index",
            "overall",
        ):
            value = getattr(self, key)
            if not _is_real_number(value) or not 0 <= value <= 100:
                raise ValueError("动作评分必须位于 0 到 100")
        if not _is_real_number(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("动作评分置信度无效")
        return self


class RecognitionCandidateContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exercise_type: str = Field(pattern=_MOTION_EXERCISE_ID_RE)
    match_score: float = Field(ge=0, le=100)


class MotionRecognitionContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: Literal["auto", "manual"]
    requested_type: str
    selected_type: str | None = None
    accepted: bool
    confidence: float = Field(ge=0, le=1)
    margin: float = Field(ge=0, le=100)
    method: str = Field(min_length=1)
    candidates: list[RecognitionCandidateContract] = Field(
        default_factory=list, max_length=6
    )

    @model_validator(mode="after")
    def _check(self):
        if self.requested_type != "auto" and not re.match(
            _MOTION_EXERCISE_ID_RE, self.requested_type
        ):
            raise ValueError("requested_type 非法")
        if self.accepted and self.selected_type is None:
            raise ValueError("accepted=true 必须给出 selected_type")
        if not self.accepted and self.selected_type is not None:
            raise ValueError("accepted=false 不得给出 selected_type")
        if self.selected_type is not None and not re.match(
            _MOTION_EXERCISE_ID_RE, self.selected_type
        ):
            raise ValueError("selected_type 非法")
        seen: set[str] = set()
        for candidate in self.candidates:
            if candidate.exercise_type in seen:
                raise ValueError("识别候选重复")
            seen.add(candidate.exercise_type)
        return self


class MotionFrameContract(BaseModel):
    """One keyframe/event row. External image URLs are explicitly forbidden."""

    model_config = ConfigDict(extra="allow")

    event: str = Field(min_length=1)
    timestamp: float
    image_b64: str | None = None
    image_mime: str | None = None
    preview_sha256: str | None = None

    @model_validator(mode="after")
    def _check(self):
        extra = self.model_extra or {}
        if extra.get("url"):
            raise ValueError("关键帧不得提交外部图片 URL")
        if not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("关键帧时间无效")
        if self.image_b64 is None:
            return self
        if not isinstance(self.image_b64, str) or len(self.image_b64) > 112000:
            raise ValueError("关键帧预览大小超限")
        if self.image_mime != "image/jpeg":
            raise ValueError("关键帧预览类型必须为 image/jpeg")
        try:
            raw = base64.b64decode(self.image_b64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("关键帧预览编码无效") from None
        if (
            len(raw) > 80 * 1024
            or not raw.startswith(b"\xff\xd8")
            or not raw.endswith(b"\xff\xd9")
        ):
            raise ValueError("关键帧预览必须是 80KB 内的 JPEG")
        if (
            self.preview_sha256
            and self.preview_sha256 != hashlib.sha256(raw).hexdigest()
        ):
            raise ValueError("关键帧预览摘要不匹配")
        return self


class MotionWorkerResultV1(BaseModel):
    """Canonical motion worker receipt (schema_version = v1).

    Old workers that omit ``schema_version`` keep the legacy validation path
    for one version cycle; this model is the strict contract for new workers.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["motion-worker-result-v1"]
    pose: MotionPoseContract
    frames: list[MotionFrameContract] = Field(default_factory=list, max_length=200)
    score: MotionScoreContract | None = None
    recognition: MotionRecognitionContract | None = None
    # compositional_semantics is an optional advisory blob; the receipt contract
    # does not re-enumerate its internals here (legacy path still does).

    @model_validator(mode="after")
    def _check(self):
        previews = [f for f in self.frames if f.image_b64 is not None]
        if len(previews) > 4:
            raise ValueError("关键帧预览超过 4 张")
        recognition = self.recognition
        if recognition is None:
            return self
        if (
            recognition.accepted
            and self.pose.exercise_type not in (None, recognition.selected_type)
        ):
            raise ValueError("识别类型与姿态分析类型不一致")
        if not recognition.accepted and (
            (self.score is not None and self.score.available) or self.pose.available
        ):
            raise ValueError("拒识结果不得生成动作评分")
        return self


def validate_motion_worker_result_v1(result: dict) -> dict:
    """Validate a v1 motion receipt; raise MotionResultSchemaError on failure."""
    try:
        MotionWorkerResultV1.model_validate(result)
    except ValidationError as exc:
        raise MotionResultSchemaError(
            "动作分析结果格式不符合约定",
            field_path=motion_result_first_error_path(exc),
        ) from None
    return result


# --------------------------------------------------------------------------- #
# V2 unified receipt (contract section 5). Six evidence groups; the receipt
# carries REFERENCES (preview_asset_id), never image bytes. finding/advice/phase
# stay inline on frames[]. recognition_candidates mixes pose + kinetics sources;
# the legacy worker still emits kinetics.candidates, so that field is parsed too.
# --------------------------------------------------------------------------- #

MOTION_WORKER_RESULT_V2_SCHEMA_VERSION = "motion-worker-v2"


class V2VideoQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    decoded_ok: bool = True
    duration_ms: int | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, ge=0, le=1000)
    total_frames: int | None = Field(default=None, ge=0)
    blur_summary: str | None = None


class V2Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    subject_id: str | None = None
    visible_regions: list[str] = Field(default_factory=list, max_length=12)


class V2PoseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    fps: float | None = Field(default=None, ge=0, le=1000)
    frame_ids: list[str] = Field(default_factory=list, max_length=512)
    sample_count: int | None = Field(default=None, ge=0, le=100000)
    keypoint_valid_rate: float | None = Field(default=None, ge=0, le=1)
    measurement_summary: str | None = None


class V2RecognitionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=24)
    source_label: str = Field(min_length=1, max_length=80)
    canonical_id: str | None = Field(default=None, max_length=40)
    class_index: int | None = Field(default=None, ge=0, le=100000)
    raw_score: float | None = Field(default=None, ge=0, le=1)
    score_type: str | None = Field(default=None, max_length=24)


class V2EvidenceFrame(BaseModel):
    """Generic evidence pool row: reference only, no image bytes.

    finding/advice/phase stay inline (contract note: V2 keeps the V1 row shape).
    ``image_b64`` is explicitly forbidden: previews travel by ``preview_asset_id``.
    """

    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1, max_length=80)
    timestamp_ms: int = Field(ge=0)
    preview_asset_id: str | None = Field(default=None, max_length=128)
    preview_sha256: str | None = Field(default=None, max_length=64)
    preview_bytes: int | None = Field(default=None, ge=0)
    preview_dimensions: dict | None = None
    subject_id: str | None = Field(default=None, max_length=80)
    visible_regions: list[str] = Field(default_factory=list, max_length=12)
    blur: str | None = None
    motion_delta: float | None = None
    event: str | None = Field(default=None, max_length=60)
    phase: str | None = Field(default=None, max_length=40)
    finding: str | None = None
    advice: str | None = None
    next_step: str | None = None


class V2Measurements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    exercise_id: str | None = Field(default=None, max_length=40)
    reps: int | None = Field(default=None, ge=0, le=10000)
    duration_ms: int | None = Field(default=None, ge=0)
    quality: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=300)


class MotionWorkerResultV2(BaseModel):
    """V2 unified worker receipt (schema_version = motion-worker-v2).

    ``extra="forbid"`` is deliberate (spec §7.1): a producer that adds a field
    the consumer does not understand must fail loudly at the boundary instead of
    having its evidence silently dropped by a lenient parser.

    Every field the shipped local worker emits is therefore declared here:
    ``pipeline_version``, ``model_versions``, ``external_provider_calls``,
    ``cloud_review_mode`` and ``source``. V1 historical receipts keep parsing
    through ``MotionWorkerResultV1``. A V2 receipt must NOT carry image bytes;
    previews live in short-term storage and are referenced by ``preview_asset_id``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["motion-worker-v2"]
    video_quality: V2VideoQuality
    subject: V2Subject
    pose_evidence: V2PoseEvidence
    recognition_candidates: list[V2RecognitionCandidate] = Field(
        default_factory=list, max_length=50
    )
    frames: list[V2EvidenceFrame] = Field(default_factory=list, max_length=120)
    measurements: V2Measurements
    # Provenance the post-processing adapter forwards into the stored result.
    pipeline_version: str | None = Field(default=None, max_length=60)
    model_versions: dict[str, str] = Field(default_factory=dict)
    external_provider_calls: int | None = Field(default=None, ge=0)
    cloud_review_mode: str | None = Field(default=None, max_length=30)
    source: str | None = Field(default=None, max_length=30)
    # Migration window: a receipt produced while a worker still emitted the V1
    # view alongside the V2 groups may carry these three keys. They are declared
    # (so a *new* unknown group still fails loudly) but deliberately IGNORED:
    # the V2 groups are the single source of evidence, and reading these would
    # reintroduce the silent V1 path this contract removes (spec §7.3).
    pose: dict | None = None
    score: dict | None = None
    kinetics: dict | None = None


def validate_motion_worker_result_v2(result: dict) -> dict:
    """Validate a v2 motion receipt; raise MotionResultSchemaError on failure."""
    try:
        MotionWorkerResultV2.model_validate(result)
    except ValidationError as exc:
        raise MotionResultSchemaError(
            "动作分析结果(V2)格式不符合约定",
            field_path=motion_result_first_error_path(exc),
        ) from None
    return result


class WorkerHeartbeatIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    name: str = Field(default="HealthMate Local Worker", max_length=120)
    version: str = Field(default="1.0.0", max_length=40)
    gpu_name: str = Field(default="", max_length=160)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict = Field(default_factory=dict)


class WorkerClaimIn(BaseModel):
    request_id: str | None = Field(default=None, min_length=8, max_length=80)
    worker_id: str = Field(min_length=3, max_length=120)
    capabilities: list[str] = Field(
        default_factory=lambda: ["motion_pose", "food_vision", "kinetics400"],
        max_length=20,
    )


class WorkerProgressIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    progress: int = Field(ge=0, le=99)
    stage: str = Field(default="", max_length=120)


class WorkerCompleteIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    result: dict
    metrics: dict = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, value):
        latency = value.get("latency_ms", 0)
        if (
            type(latency) not in {float, int}
            or not math.isfinite(latency)
            or not 0 <= latency <= 86400000
        ):
            raise ValueError("latency_ms 无效")
        return value


class WorkerFailIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    error_code: str = Field(default="worker_error", max_length=80)
    error_message: str = Field(default="AI worker 执行失败", max_length=800)
    retryable: bool = True
