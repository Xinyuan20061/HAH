"""Unified motion orchestrator: state machine, fusion, caching and ledger.

State machine (spec 3.1):
    queued -> decoding -> local_inference -> evidence_ready -> visual_review
           -> feedback_generation -> completed
    terminals: completed | partial (local evidence, model unavailable)
               | abstained (insufficient evidence) | failed | cancelled.

Worker receipt (MotionWorkerResultV1) is consumed as the local-inference output.
DeepSeek visual review + grounded summary are invoked HERE, server-side, and the
final unified contract is materialised into MotionAnalysisFeedback.result_json.
Reads (GET endpoints) only load that snapshot; they never trigger model calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    AIJob,
    MediaAsset,
    MotionAnalysisFeedback,
    MotionAnalysisRun,
)
from app.services.motion.decision import (
    LABEL_ZH,
    MIN_USABLE_FRAMES,
    LocalEvidence,
    MotionDecision,
    QualityEvidence,
    ReviewEvidence,
    decide_motion,
    quality_from_receipt,
)
from app.services.motion.provider_gateway import ProviderBudgetExceeded, ProviderGateway, Timer
from app.services.motion.text_summary import (
    TEXT_OPERATION,
    call_text_summary,
    clean_summary_text,
    fallback_summary,
)
from app.services.motion.vision_review import (
    VISION_OPERATION,
    ReviewValidationError,
    call_vision_review,
    decode_preview_jpeg,
    validate_review,
)

logger = logging.getLogger("healthmate.motion.orchestrator")

PIPELINE_VERSION = "motion-unified-v1"
CONSENT_VERSION = "deepseek-frames-v1"
UNIFIED_SCHEMA_VERSION = "motion-unified-v1"

REVIEW_STATUS_USED = "used"
REVIEW_STATUS_SKIPPED = "skipped"
REVIEW_STATUS_UNAVAILABLE = "unavailable"

_TERMINAL_STATUSES = {"completed", "partial", "abstained", "failed", "cancelled"}


def json_loads(value, default):
    try:
        out = json.loads(value or "")
        return out if out is not None else default
    except (ValueError, TypeError):
        return default


def make_trace_id() -> str:
    return "trace-" + uuid.uuid4().hex[:24]


# --------------------------------------------------------------------------- #
# Run creation (idempotent, spec 5)
# --------------------------------------------------------------------------- #

def _dedupe_key(user_id: int, asset_id: int, idem_key: str) -> str:
    return hashlib.sha256(
        f"motion-unified:{user_id}:{asset_id}:{idem_key}".encode()
    ).hexdigest()


def create_unified_run(
    db: Session,
    *,
    user_id: int,
    asset: MediaAsset,
    requested_exercise: str,
    consent_deepseek_frames: bool,
    pipeline_version: str,
    idempotency_key: str,
    analysis_revision: bool = False,
    parent_run_id: int | None = None,
) -> tuple[MotionAnalysisRun, bool, str | None]:
    """Create (or reuse) a unified run + its AIJob.

    Returns (run, created, conflict). conflict is set when the same idempotency
    key was used with different parameters -> caller answers 409.
    """
    key = _dedupe_key(user_id, asset.id, idempotency_key)
    existing = db.scalar(
        select(MotionAnalysisRun).where(MotionAnalysisRun.dedupe_key == key)
    )
    params = {
        "requested_type": requested_exercise,
        "consent": consent_deepseek_frames,
        "pipeline_version": pipeline_version,
    }
    if existing is not None:
        existing_params = {
            "requested_type": existing.requested_type,
            "consent": _run_consent(existing),
            "pipeline_version": existing.pipeline_version,
        }
        if existing_params != params:
            return existing, False, "同一幂等键的参数与已有任务不一致"
        return existing, False, None

    run = MotionAnalysisRun(
        user_id=user_id,
        media_asset_id=asset.id,
        parent_run_id=parent_run_id,
        dedupe_key=key,
        requested_type=requested_exercise,
        pipeline_version=pipeline_version,
        status="queued",
        consent_version=CONSENT_VERSION if consent_deepseek_frames else "",
        model_versions_json=json.dumps(
            {"consent_deepseek_frames": bool(consent_deepseek_frames)},
            ensure_ascii=False,
        ),
    )
    db.add(run)
    db.flush()

    media_sha256 = hashlib.sha256(asset.storage_key.encode("utf-8")).hexdigest()
    from app.services.ai_jobs import create_ai_job

    job = create_ai_job(
        db,
        user_id=user_id,
        job_type="motion_unified",
        media_asset_id=asset.id,
        payload={
            "media_url": asset.source_url or "",
            "media_sha256": media_sha256,
            "requested_exercise": requested_exercise,
            "consent_deepseek_frames": bool(consent_deepseek_frames),
            "analysis_id": run.id,
            "pipeline_version": pipeline_version,
        },
    )
    run.ai_job_id = job.id
    db.commit()
    db.refresh(run)
    return run, True, None


def _run_consent(run: MotionAnalysisRun) -> bool:
    return bool(run.model_versions.get("consent_deepseek_frames"))


# --------------------------------------------------------------------------- #
# Cache (spec 4.2): identical (media_sha256, pipeline, model, consent) review
# and summary are reused across runs; polling never re-bills.
# --------------------------------------------------------------------------- #

def _cache_key(
    run: MotionAnalysisRun,
    asset: MediaAsset | None,
    candidate_ids: set[str],
    pose: dict,
    preview_digests: list[str],
) -> str:
    storage_key = asset.storage_key if asset else f"deleted:{run.media_asset_id}"
    material = "|".join(
        [
            run.pipeline_version,
            run.consent_version,
            storage_key,
            ",".join(sorted(candidate_ids)),
            json.dumps(
                {
                    "pose_exercise": pose.get("exercise_type"),
                    "reps": pose.get("reps"),
                    "rate": pose.get("keypoint_valid_rate"),
                },
                sort_keys=True,
            ),
            ",".join(sorted(preview_digests)),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _load_cached_decision(db: Session, user_id: int, asset_id: int, cache_key: str, exclude_run_id: int):
    rows = db.scalars(
        select(MotionAnalysisFeedback)
        .join(MotionAnalysisRun, MotionAnalysisFeedback.run_id == MotionAnalysisRun.id)
        .where(
            MotionAnalysisRun.user_id == user_id,
            MotionAnalysisRun.media_asset_id == asset_id,
            MotionAnalysisRun.id != exclude_run_id,
        )
    ).all()
    for row in rows:
        meta = row.result.get("_meta") or {}
        if meta.get("cache_key") == cache_key:
            return row.result
    return None


# --------------------------------------------------------------------------- #
# Worker receipt -> unified result
# --------------------------------------------------------------------------- #

def _frame_image_data_url(frame: dict) -> str | None:
    encoded = frame.get("image_b64")
    if not isinstance(encoded, str) or not encoded:
        return None
    return f"data:image/jpeg;base64,{encoded}"


def _measurements(recognition: dict, pose: dict) -> dict:
    out: dict = {
        "requested_type": recognition.get("requested_type"),
        "local_accepted": bool(recognition.get("accepted")),
        "local_label": recognition.get("selected_type"),
        "method": recognition.get("method"),
    }
    if pose.get("available"):
        out.update(
            {
                "exercise_type": pose.get("exercise_type"),
                "reps": pose.get("reps"),
                "keypoint_valid_rate": pose.get("keypoint_valid_rate"),
                "error_count": len(pose.get("errors") or []),
            }
        )
    else:
        out["pose_message"] = str(pose.get("message") or "")[:120]
    return out


def handle_unified_worker_result(
    db: Session,
    job: AIJob,
    receipt: dict,
    metrics: dict | None = None,
) -> None:
    """Consume a validated MotionWorkerResultV1 and materialise the unified result."""
    run = db.scalar(
        select(MotionAnalysisRun).where(MotionAnalysisRun.ai_job_id == job.id)
    )
    if run is None:
        logger.warning("motion_unified job %s has no run row", job.id)
        return
    asset = db.get(MediaAsset, run.media_asset_id)
    payload = json_loads(job.payload_json, {})

    recognition = receipt.get("recognition") or {}
    pose = receipt.get("pose") or {}
    frames = receipt.get("frames") or []
    score_in = receipt.get("score") or {}

    stage_log: list[dict] = []

    def stage(name: str, started, finished, reason=None):
        stage_log.append(
            {
                "name": name,
                "started_at": started.isoformat() + "Z",
                "finished_at": finished.isoformat() + "Z",
                "reason": reason,
            }
        )

    t0 = utc_now()
    local = LocalEvidence(
        accepted=bool(recognition.get("accepted")),
        label_id=recognition.get("selected_type"),
    )
    candidates = {
        c.get("exercise_type")
        for c in (recognition.get("candidates") or [])[:5]
        if isinstance(c, dict) and c.get("exercise_type")
    }
    if local.accepted and local.label_id:
        candidates.add(local.label_id)
    quality = quality_from_receipt(recognition, pose, frames)
    run.status = "evidence_ready"
    run.model_versions_json = json.dumps(
        {
            "consent_deepseek_frames": bool(payload.get("consent_deepseek_frames")),
            "worker_method": str(recognition.get("method") or "")[:80],
            "worker_vision_model": "motion_unified_v1",
            "deepseek_vision_model": settings_vision_model(),
        },
        ensure_ascii=False,
    )
    db.flush()
    t1 = utc_now()
    stage("evidence_ready", t0, t1)

    # Indexed frames: frame:N ids are the ONLY evidence references the model may cite.
    indexed = [(f"frame:{i}", fr) for i, fr in enumerate(frames) if isinstance(fr, dict)]
    frame_ids = {fid for fid, _ in indexed}
    previews: list[tuple[str, bytes]] = []
    preview_digests: list[str] = []
    for fid, fr in indexed:
        raw = decode_preview_jpeg(fr)
        if raw:
            previews.append((fid, raw))
            preview_digests.append(
                hashlib.sha256(raw).hexdigest()
            )
    previews = previews[:4]

    gateway = ProviderGateway(db, user_id=job.user_id, run_id=run.id)
    consent = bool(payload.get("consent_deepseek_frames"))
    cache_key = _cache_key(run, asset or run, candidates, pose, preview_digests)

    review_status = REVIEW_STATUS_SKIPPED
    review: ReviewEvidence | None = None
    review_findings: list[dict] = []
    cached = _load_cached_decision(db, job.user_id, run.media_asset_id, cache_key, run.id)
    cached_summary_text: str | None = None

    want_review = bool(
        consent
        and quality.has_person
        and quality.usable_frames >= MIN_USABLE_FRAMES
        and previews
        and _deepseek_configured()
    )

    if cached and cached.get("recognition", {}).get("review_status") == REVIEW_STATUS_USED:
        rec = cached["recognition"]
        ev = [e for e in rec.get("evidence", []) if isinstance(e, str) and e.startswith("frame:")]
        review = ReviewEvidence(label_id=rec.get("label_id") or "unknown", evidence_ids=tuple(ev[:4]))
        review_findings = [
            f for f in (cached.get("keyframes") or []) if f.get("evidence_type") == "visual_observation"
        ]
        cached_summary_text = (cached.get("summary") or {}).get("text")
        review_status = REVIEW_STATUS_USED
        t2 = utc_now()
        stage("visual_review", t1, t2, reason="cache_hit")
    elif want_review:
        run.status = "visual_review"
        db.flush()
        facts = {
            "candidates": sorted(candidates) or [local.label_id or "unknown"],
            "measurements": _measurements(recognition, pose),
        }
        started = utc_now()
        try:
            gateway.check_budget(VISION_OPERATION)
            with Timer() as timer:
                review_obj, meta = call_vision_review(facts=facts, frames=previews)
            try:
                validate_review(review_obj, set(facts["candidates"]), frame_ids)
            except ReviewValidationError as exc:
                gateway.record(
                    operation=VISION_OPERATION,
                    request_fingerprint=cache_key,
                    status="rejected:" + exc.code,
                    latency_ms=timer.ms,
                )
                review = None
                review_status = REVIEW_STATUS_UNAVAILABLE
            else:
                gateway.record(
                    operation=VISION_OPERATION,
                    request_fingerprint=cache_key,
                    status="ok",
                    latency_ms=timer.ms,
                    tokens=meta.get("tokens"),
                    provider_request_id=meta.get("provider_request_id"),
                )
                review = ReviewEvidence(
                    label_id=review_obj.label_id,
                    evidence_ids=tuple(review_obj.evidence_ids),
                )
                review_findings = [f.model_dump() for f in review_obj.findings]
                review_status = REVIEW_STATUS_USED
        except (ProviderBudgetExceeded, Exception) as exc:  # noqa: BLE001 - degrade, never crash worker
            gateway.record(
                operation=VISION_OPERATION,
                request_fingerprint=cache_key,
                status="error:" + type(exc).__name__,
                latency_ms=getattr(locals().get("timer"), "ms", 0),
            )
            review = None
            review_status = REVIEW_STATUS_UNAVAILABLE
        stage("visual_review", started, utc_now(), reason=review_status)
    else:
        t2 = utc_now()
        reason = (
            "no_consent" if not consent
            else "no_evidence" if not quality.has_person or quality.usable_frames < MIN_USABLE_FRAMES
            else "no_previews" if not previews
            else "model_unconfigured"
        )
        stage("visual_review", t1, t2, reason=reason)

    decision: MotionDecision = decide_motion(local, candidates, review, quality)

    # ---- feedback / summary stage ----------------------------------------- #
    run.status = "feedback_generation"
    db.flush()
    started = utc_now()
    summary_text: str | None = cached_summary_text
    summary_source = "structured_fallback"
    summary_degraded = True
    if summary_text is None and _deepseek_configured() and consent and decision.state != "abstained":
        try:
            gateway.check_budget(TEXT_OPERATION)
            grounded_facts = {
                "label": decision.label_id,
                "frames": [
                    {"id": fid, "t_ms": int(float(fr.get("timestamp", 0)) * 1000), "event": fr.get("event")}
                    for fid, fr in indexed[:4]
                ],
                "measurements": _measurements(recognition, pose),
            }
            with Timer() as timer:
                raw_text, meta = call_text_summary(facts=grounded_facts)
            summary_text = clean_summary_text(raw_text)
            if summary_text:
                summary_source = "deepseek_grounded"
                summary_degraded = False
                gateway.record(
                    operation=TEXT_OPERATION,
                    request_fingerprint=hashlib.sha256(
                        (cache_key + ":summary").encode()
                    ).hexdigest(),
                    status="ok",
                    latency_ms=timer.ms,
                    tokens=meta.get("tokens"),
                    provider_request_id=meta.get("provider_request_id"),
                )
            else:
                gateway.record(
                    operation=TEXT_OPERATION,
                    request_fingerprint=hashlib.sha256(
                        (cache_key + ":summary").encode()
                    ).hexdigest(),
                    status="rejected:clean",
                    latency_ms=timer.ms,
                )
        except ProviderBudgetExceeded:
            pass
        except Exception as exc:  # noqa: BLE001
            gateway.record(
                operation=TEXT_OPERATION,
                request_fingerprint=hashlib.sha256((cache_key + ":summary").encode()).hexdigest(),
                status="error:" + type(exc).__name__,
                latency_ms=getattr(locals().get("timer"), "ms", 0),
            )
    if not summary_text:
        summary_text = fallback_summary(decision.state, decision.label_id, decision.reason_code)
        summary_source = "structured_fallback"
        summary_degraded = decision.state != "abstained"
    stage("feedback_generation", started, utc_now(), reason=summary_source)

    # ---- materialise the unified result contract (spec 4.4) --------------- #
    result = _build_result(
        run=run,
        decision=decision,
        recognition=recognition,
        pose=pose,
        score_in=score_in,
        indexed=indexed,
        review_status=review_status,
        review_findings=review_findings,
        summary_text=summary_text,
        summary_source=summary_source,
        summary_degraded=summary_degraded,
        stage_log=stage_log,
        cache_key=cache_key,
        worker_method=str(recognition.get("method") or "local_worker"),
    )

    if decision.state == "abstained":
        final_status = "abstained"
    elif review_status == REVIEW_STATUS_UNAVAILABLE:
        final_status = "partial"
    else:
        final_status = "completed"

    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run.id)
    )
    if feedback is None:
        feedback = MotionAnalysisFeedback(run_id=run.id, user_id=job.user_id)
        db.add(feedback)
    feedback.schema_version = UNIFIED_SCHEMA_VERSION
    feedback.result_json = json.dumps(result, ensure_ascii=False, default=str)

    run.status = final_status
    run.finished_at = utc_now()
    db.commit()


def _deepseek_configured() -> bool:
    from app.core.config import settings

    return bool(settings.deepseek_api_key)


def settings_vision_model() -> str:
    from app.core.config import settings

    return settings.deepseek_vision_model or settings.deepseek_model


def _build_result(
    *,
    run: MotionAnalysisRun,
    decision: MotionDecision,
    recognition: dict,
    pose: dict,
    score_in: dict,
    indexed: list[tuple[str, dict]],
    review_status: str,
    review_findings: list[dict],
    summary_text: str,
    summary_source: str,
    summary_degraded: bool,
    stage_log: list[dict],
    cache_key: str,
    worker_method: str,
) -> dict:
    label_id = decision.label_id
    best = None
    candidates = recognition.get("candidates") or []
    if candidates:
        best_candidate = max(candidates, key=lambda c: c.get("match_score", 0))
        best = round(float(best_candidate.get("match_score", 0)) / 100.0, 4)

    evidence: list[str] = []
    if decision.state == "recognized":
        evidence = [fid for fid, _ in indexed[:4]]
        if pose.get("available"):
            evidence.append("pose:joint_measurements")

    zh = LABEL_ZH.get(label_id or "", "")
    reason_map = {
        "INSUFFICIENT_VIDEO_EVIDENCE": "有效画面不足：没有检测到足够的人物动作帧。",
        "REVIEW_UNAVAILABLE_OR_UNCERTAIN": "云端视觉复核缺失或模型无法确认，已按谨慎原则保留判断。",
        "UNSUPPORTED_REVIEW": "云端复核给出的类别不在本地候选内或未引用有效关键帧。",
        "MODEL_DISAGREEMENT": "本地姿态与云端视觉复核意见不一致，暂不下结论。",
        "EVIDENCE_AGREEMENT": "本地姿态与云端视觉复核在引用关键帧上达成一致。",
    }

    # Score block: only emit numeric quality when the gate allows it.
    if decision.scoreable and score_in.get("available"):
        score_block = {
            "available": True,
            "reps": pose.get("reps"),
            "completeness": score_in.get("completeness"),
            "stability": score_in.get("stability"),
            "rhythm_control": score_in.get("rhythm_control"),
            "risk_index": score_in.get("risk_index"),
            "overall": score_in.get("overall"),
        }
    else:
        if decision.state != "recognized":
            reason_code = "NOT_RECOGNIZED"
        elif label_id and label_id not in {"squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw"}:
            reason_code = "NO_VALIDATED_SCORER"
        elif not pose.get("available"):
            reason_code = "POSE_UNAVAILABLE"
        else:
            reason_code = "INCOMPLETE_OR_LOW_QUALITY_CYCLE"
        score_block = {"available": False, "reason_code": reason_code}

    findings_by_frame = {f.get("frame_id"): f for f in review_findings if isinstance(f, dict)}
    n = len(indexed)
    keyframes = []
    for i, (fid, fr) in enumerate(indexed[:4]):
        t_ms = int(float(fr.get("timestamp", 0)) * 1000)
        if n <= 1:
            phase = "开始"
        elif i == 0:
            phase = "开始"
        elif i == n - 1:
            phase = "结束"
        else:
            phase = "中间"
        reviewed = findings_by_frame.get(fid)
        keyframes.append(
            {
                "id": fid,
                "t_ms": t_ms,
                "phase": phase,
                "finding": (reviewed or {}).get("observation") or str(fr.get("event") or "关键画面")[:100],
                "advice": (reviewed or {}).get("advice") or "保持站姿稳定，注意动作控制",
                "image_url": _frame_image_data_url(fr),
                "evidence_type": "visual_observation" if reviewed else "timeline_position",
            }
        )

    return {
        "analysis_id": run.id,
        "status": run.status,
        "pipeline_version": run.pipeline_version,
        "recognition": {
            "state": decision.state,
            "label_id": label_id,
            "label_zh": zh or None,
            "candidate_score": best,
            "calibrated_confidence": None,
            "evidence": evidence,
            "sources": list(decision.sources),
            "review_status": review_status,
            "reason": reason_map.get(decision.reason_code, decision.reason_code),
            "reason_code": decision.reason_code,
        },
        "score": score_block,
        "summary": {
            "text": summary_text,
            "source": summary_source,
            "degraded": summary_degraded,
        },
        "keyframes": keyframes,
        "limitations": [
            "结果仅供一般健身参考，不构成医疗诊断或康复建议",
            "candidate_score 为模型候选分值，不是识别准确率",
        ],
        "trace_id": make_trace_id(),
        "_meta": {
            "cache_key": cache_key,
            "stages": stage_log,
            "worker_method": worker_method,
            "model_versions": run.model_versions,
            "parent_run_id": run.parent_run_id,
        },
    }


# --------------------------------------------------------------------------- #
# Read-side helpers (pure reads; never trigger model calls)
# --------------------------------------------------------------------------- #

def get_unified_result(db: Session, run: MotionAnalysisRun) -> dict | None:
    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run.id)
    )
    if feedback is None:
        return None
    return feedback.result


def run_stage_view(db: Session, run: MotionAnalysisRun) -> dict:
    job = db.get(AIJob, run.ai_job_id) if run.ai_job_id else None
    status = run.status
    # While the worker is still grinding, derive the in-flight stage from the job.
    if status in {"queued", "decoding", "local_inference"} or (
        status not in _TERMINAL_STATUSES and job is not None and job.status == "processing"
    ):
        stage = "local_inference" if job and job.status == "processing" else "queued"
    else:
        stage = status
    return {
        "analysis_id": run.id,
        "status": status if status in _TERMINAL_STATUSES or status == "queued" else "processing",
        "stage": stage,
        "job_status": job.status if job else None,
        "progress": job.progress if job else 0,
        "pipeline_version": run.pipeline_version,
        "requested_type": run.requested_type,
        "created_at": run.created_at.isoformat() + "Z",
        "finished_at": (run.finished_at.isoformat() + "Z") if run.finished_at else None,
    }
