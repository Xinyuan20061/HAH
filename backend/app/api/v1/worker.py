from __future__ import annotations
from app.core.time import utc_now, utc_iso

import json
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.worker_deps import require_worker_token
from app.core.config import settings
from app.core.database import get_db
from app.models import (
    AIJob,
    FoodAnalysisSession,
    MotionScore,
    MotionEvent,
    MotionAnalysisRun,
    MotionEvidenceFrame,
)
from app.schemas.worker import (
    MOTION_WORKER_RESULT_SCHEMA_VERSION,
    MOTION_WORKER_RESULT_V2_SCHEMA_VERSION,
    MotionResultSchemaError,
    WorkerClaimIn,
    WorkerCompleteIn,
    WorkerFailIn,
    WorkerHeartbeatIn,
    WorkerProgressIn,
    validate_motion_worker_result_v1,
    validate_motion_worker_result_v2,
)
from app.schemas.ai_results import FoodResult
from pydantic import ValidationError
from app.services.ai_jobs import (
    claim_next_job,
    extend_lease,
    heartbeat_worker,
    job_source,
    json_loads,
)
from app.services.evaluation import record_metric
from app.services.timeline import add_event
from app.services.training_semantics import build_motion_semantics
from app.services.motion.stage_tasks import (
    enqueue_postprocessing_stages,
    complete_stage,
    fail_stage,
    STAGE_VISION_REVIEW,
    STAGE_FEEDBACK,
)

router = APIRouter(
    prefix="/worker", tags=["ai-worker"], dependencies=[Depends(require_worker_token)]
)
logger = logging.getLogger("healthmate.worker")


@router.post("/heartbeat")
def heartbeat(body: WorkerHeartbeatIn, db: Session = Depends(get_db)):
    node = heartbeat_worker(
        db,
        worker_id=body.worker_id,
        name=body.name,
        version=body.version,
        gpu_name=body.gpu_name,
        capabilities=body.capabilities,
        metadata=body.metadata,
    )
    return {"ok": True, "worker_id": node.worker_id, "server_time": utc_iso(utc_now())}


@router.post("/jobs/claim")
def claim(body: WorkerClaimIn, db: Session = Depends(get_db)):
    # The worker sends a dedicated heartbeat. Do not overwrite its GPU/name metadata while polling.
    job = claim_next_job(
        db,
        worker_id=body.worker_id,
        capabilities=body.capabilities,
        request_id=body.request_id,
    )
    if not job:
        return {"job": None}
    source = job_source(db, job)
    return {
        "job": {
            "job_id": job.id,
            "job_type": job.job_type,
            "lease_token": job.lease_token,
            "lease_seconds": settings.worker_lease_seconds,
            "payload": json_loads(job.payload_json, {}),
            "source": source,
        }
    }


@router.post("/jobs/{job_id}/progress")
def progress(job_id: int, body: WorkerProgressIn, db: Session = Depends(get_db)):
    job = db.get(AIJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    try:
        extend_lease(
            db,
            job,
            worker_id=body.worker_id,
            lease_token=body.lease_token,
            progress=body.progress,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "job_id": job.id, "progress": job.progress, "stage": body.stage}


def _validate_food_result(result: dict) -> dict:
    try:
        return FoodResult.model_validate(result).model_dump()
    except ValidationError:
        raise HTTPException(422, "识餐结果字段或数值范围无效")


def _validate_motion_result(result: dict) -> dict:
    import base64
    import binascii
    import hashlib
    import math

    pose = result.get("pose")
    if not isinstance(pose, dict) or not isinstance(pose.get("available"), bool):
        raise HTTPException(422, "动作结果缺少可评价状态")
    if not pose["available"] and not pose.get("message"):
        raise HTTPException(422, "不可评价时必须提供原因")
    if pose["available"]:
        rate = pose.get("keypoint_valid_rate")
        reps = pose.get("reps")
        if (
            type(reps) is not int
            or reps < 0
            or reps > 10000
            or type(rate) not in {int, float}
            or not math.isfinite(rate)
            or not 0 <= rate <= 1
        ):
            raise HTTPException(422, "动作次数或关键点有效率无效")
    frames = result.get("frames", [])
    if not isinstance(frames, list) or len(frames) > 200:
        raise HTTPException(422, "关键帧格式无效")
    preview_count = 0
    for frame in frames:
        if not isinstance(frame, dict) or not frame.get("event") or frame.get("url"):
            raise HTTPException(422, "关键帧必须提供真实事件，且不得提交外部图片 URL")
        timestamp = frame.get("timestamp")
        if (
            type(timestamp) not in {float, int}
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise HTTPException(422, "关键帧时间无效")
        image_b64 = frame.get("image_b64")
        if image_b64 is not None:
            preview_count += 1
            if (
                preview_count > 4
                or not isinstance(image_b64, str)
                or len(image_b64) > 112000
                or frame.get("image_mime") != "image/jpeg"
            ):
                raise HTTPException(422, "关键帧预览数量、类型或大小无效")
            try:
                image = base64.b64decode(image_b64, validate=True)
            except (ValueError, binascii.Error):
                raise HTTPException(422, "关键帧预览编码无效") from None
            if (
                len(image) > 80 * 1024
                or not image.startswith(b"\xff\xd8")
                or not image.endswith(b"\xff\xd9")
            ):
                raise HTTPException(422, "关键帧预览必须是 80KB 内的 JPEG")
            digest = frame.get("preview_sha256")
            if digest and digest != hashlib.sha256(image).hexdigest():
                raise HTTPException(422, "关键帧预览摘要不匹配")
    if not isinstance(pose.get("errors", []), list):
        raise HTTPException(422, "动作提示必须是列表")
    score = result.get("score")
    if score is not None:
        if not isinstance(score, dict) or not isinstance(score.get("available"), bool):
            raise HTTPException(422, "动作评分格式无效")
        if score["available"]:
            for key in [
                "completeness",
                "stability",
                "rhythm_control",
                "risk_index",
                "overall",
            ]:
                value = score.get(key)
                if (
                    type(value) not in {int, float}
                    or not math.isfinite(value)
                    or not 0 <= value <= 100
                ):
                    raise HTTPException(422, "动作评分必须位于 0 到 100")
            confidence = score.get("confidence")
            if (
                type(confidence) not in {int, float}
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                raise HTTPException(422, "动作评分置信度无效")
    recognition = result.get("recognition")
    if recognition is not None:
        if not isinstance(recognition, dict):
            raise HTTPException(422, "动作识别结果格式无效")
        mode = recognition.get("mode")
        requested_type = recognition.get("requested_type")
        selected_type = recognition.get("selected_type")
        accepted = recognition.get("accepted")
        _exercise_id = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
        if (
            not isinstance(mode, str)
            or mode not in {"auto", "manual"}
            or not isinstance(requested_type, str)
            or (
                requested_type != "auto"
                and not _exercise_id.match(requested_type)
            )
            or type(accepted) is not bool
            or (
                selected_type is not None
                and (
                    not isinstance(selected_type, str)
                    or not _exercise_id.match(selected_type)
                )
            )
            or (accepted and selected_type is None)
            or (not accepted and selected_type is not None)
        ):
            raise HTTPException(422, "动作识别状态无效")
        confidence = recognition.get("confidence")
        if (
            type(confidence) not in {int, float}
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise HTTPException(422, "动作识别置信度无效")
        margin = recognition.get("margin")
        if (
            type(margin) not in {int, float}
            or not math.isfinite(margin)
            or not 0 <= margin <= 100
        ):
            raise HTTPException(422, "动作识别候选差值无效")
        if not isinstance(recognition.get("method"), str) or not recognition["method"]:
            raise HTTPException(422, "动作识别方法缺失")
        candidates = recognition.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) > 6:
            raise HTTPException(422, "动作识别候选格式无效")
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise HTTPException(422, "动作识别候选格式无效")
            candidate_type = candidate.get("exercise_type")
            match_score = candidate.get("match_score")
            if (
                not isinstance(candidate_type, str)
                or not _exercise_id.match(candidate_type)
                or candidate_type in seen
                or type(match_score) not in {int, float}
                or not math.isfinite(match_score)
                or not 0 <= match_score <= 100
            ):
                raise HTTPException(422, "动作识别候选数值无效")
            seen.add(candidate_type)
        if accepted and pose.get("exercise_type") not in (None, selected_type):
            raise HTTPException(422, "动作识别类型与姿态分析类型不一致")
        if not accepted and ((score or {}).get("available") or pose.get("available")):
            raise HTTPException(422, "拒识结果不得生成动作评分")
    semantics = result.get("compositional_semantics")
    if semantics is not None:
        allowed_patterns = {
            "knee_dominant",
            "bilateral_lower_body",
            "unilateral_lower_body",
            "elbow_flexion_extension",
            "horizontal_upper_body",
            "static_or_low_amplitude",
        }
        allowed_regions = {"lower_body", "upper_body", "trunk_stability"}
        if (
            not isinstance(semantics, dict)
            or type(semantics.get("available")) is not bool
            or not isinstance(semantics.get("method"), str)
            or semantics.get("method")
            not in {"pose_compositional_rules_v1", "stgcn_multilabel_v1"}
        ):
            raise HTTPException(422, "动作组合语义格式无效")
        semantic_confidence = semantics.get("confidence")
        if (
            type(semantic_confidence) not in {int, float}
            or not math.isfinite(semantic_confidence)
            or not 0 <= semantic_confidence <= 1
        ):
            raise HTTPException(422, "动作组合语义置信度无效")
        patterns = semantics.get("movement_patterns", [])
        regions = semantics.get("observed_regions", [])
        if not isinstance(patterns, list) or len(patterns) > 8:
            raise HTTPException(422, "动作模式列表无效")
        if not isinstance(regions, list) or len(regions) > 6:
            raise HTTPException(422, "观察身体区域列表无效")
        for item in patterns:
            if (
                not isinstance(item, dict)
                or item.get("key") not in allowed_patterns
                or type(item.get("score")) not in {int, float}
                or not math.isfinite(item["score"])
                or not 0 <= item["score"] <= 100
                or not isinstance(item.get("evidence"), str)
                or len(item["evidence"]) > 200
            ):
                raise HTTPException(422, "动作模式条目无效")
        for item in regions:
            if (
                not isinstance(item, dict)
                or item.get("key") not in allowed_regions
                or not isinstance(item.get("basis"), str)
                or len(item["basis"]) > 200
            ):
                raise HTTPException(422, "观察身体区域条目无效")
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError):
        raise HTTPException(422, "动作结果包含无效数值")
    if len(encoded) > 1024 * 1024:
        raise HTTPException(413, "动作结果超过 1MB")
    return result


def _validate_motion_result_with_contract(result: dict, request: Request) -> dict:
    """Route a motion receipt to the v1 contract or the legacy compat path.

    Compat rule (spec 6.3): a receipt WITHOUT ``schema_version`` comes from an
    old worker and keeps the legacy hand-written validation for one version
    cycle. A receipt WITH ``schema_version`` must equal the current contract
    version; any other value is a permanent 422 pointing at the version field.
    The raw payload is never logged (it may contain image base64).
    """
    version = result.get("schema_version")
    rid = getattr(request.state, "request_id", "-")
    if version is None:
        # Old worker on the deprecation path: legacy rules, same 422 semantics.
        return _validate_motion_result(result)
    if version == MOTION_WORKER_RESULT_V2_SCHEMA_VERSION:
        try:
            return validate_motion_worker_result_v2(result)
        except MotionResultSchemaError as exc:
            logger.info(
                "motion v2 result rejected rid=%s field_path=%s reason=%s",
                rid,
                exc.field_path,
                exc.message,
            )
            raise
    if version != MOTION_WORKER_RESULT_SCHEMA_VERSION:
        logger.info(
            "motion result rejected rid=%s field_path=schema_version version=%r",
            rid,
            version,
        )
        raise MotionResultSchemaError(
            "不支持的回执 schema 版本", field_path="schema_version"
        )
    try:
        return validate_motion_worker_result_v1(result)
    except MotionResultSchemaError as exc:
        logger.info(
            "motion result rejected rid=%s field_path=%s reason=%s",
            rid,
            exc.field_path,
            exc.message,
        )
        raise


def _validate_kinetics400_result(result: dict) -> dict:
    import math

    if not isinstance(result.get("method"), str) or not result["method"]:
        raise HTTPException(422, "识别方法缺失")
    top_label = result.get("top_label")
    top_probability = result.get("top_probability")
    if not isinstance(top_label, str) or not top_label:
        raise HTTPException(422, "识别标签缺失")
    if (
        type(top_probability) not in {int, float}
        or not math.isfinite(top_probability)
        or not 0 <= top_probability <= 1
    ):
        raise HTTPException(422, "识别概率无效")
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 10:
        raise HTTPException(422, "候选列表无效")
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise HTTPException(422, "候选条目格式无效")
        label = candidate.get("label")
        probability = candidate.get("probability")
        class_index = candidate.get("class_index")
        if (
            not isinstance(label, str)
            or not label
            or label in seen
            or type(probability) not in {int, float}
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
            or type(class_index) is not int
            or not 0 <= class_index <= 2000
        ):
            raise HTTPException(422, "候选条目数值无效")
        seen.add(label)
    if top_label not in seen:
        raise HTTPException(422, "top_label 不在候选中")
    return result


def _persist_motion_evidence(db: Session, run: MotionAnalysisRun, result: dict) -> None:
    """Idempotently store the generic evidence pool into motion_evidence_frames.

    V2 receipts carry frame_id/timestamp_ms/preview_asset_id; legacy V1 receipts
    carry event/timestamp(seconds). Image bytes are never stored here -- only
    references. Rows expire via expires_at and are swept by the purge worker.
    """
    from datetime import timedelta

    cutoff = utc_now() + timedelta(days=settings.motion_preview_retention_days)
    raw_frames = result.get("frames") or []
    if not isinstance(raw_frames, list):
        return
    for index, frame in enumerate(raw_frames):
        if not isinstance(frame, dict):
            continue
        frame_id = frame.get("frame_id") or f"frame:{index}"
        ts_ms = frame.get("timestamp_ms")
        if not isinstance(ts_ms, int):
            ts = frame.get("timestamp")
            ts_ms = int(float(ts) * 1000) if isinstance(ts, (int, float)) else 0
        observation = {
            k: v
            for k, v in frame.items()
            if k not in {"image_b64", "image_mime", "preview_bytes"}
        }
        existing = db.scalar(
            select(MotionEvidenceFrame).where(
                MotionEvidenceFrame.run_id == run.id,
                MotionEvidenceFrame.frame_id == frame_id,
            )
        )
        values = dict(
            timestamp_ms=ts_ms,
            preview_asset_id=frame.get("preview_asset_id"),
            subject_id=frame.get("subject_id"),
            observation_json=json.dumps(observation, ensure_ascii=False, default=str),
            expires_at=cutoff,
        )
        if existing is None:
            db.add(
                MotionEvidenceFrame(run_id=run.id, frame_id=frame_id, **values)
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    db.flush()


@router.post("/jobs/{job_id}/complete")
def complete(
    job_id: int,
    body: WorkerCompleteIn,
    request: Request,
    db: Session = Depends(get_db),
):
    job = db.get(AIJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if (
        job.status == "done"
        and job.worker_id == body.worker_id
        and job.lease_token == body.lease_token
    ):
        return {
            "ok": True,
            "job_id": job.id,
            "status": "done",
            "already_completed": True,
        }
    result = dict(body.result or {})
    if job.job_type == "food_vision":
        result = _validate_food_result(result)
    elif job.job_type == "kinetics400":
        result = _validate_kinetics400_result(result)
    else:
        result = _validate_motion_result_with_contract(result, request)
    try:
        extend_lease(
            db,
            job,
            worker_id=body.worker_id,
            lease_token=body.lease_token,
            progress=99,
            commit=False,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    now = utc_now()

    if job.job_type == "food_vision":
        result = _validate_food_result(result)
        image_sha256 = str(result.pop("image_sha256", ""))[:64]
        session = FoodAnalysisSession(
            user_id=job.user_id,
            image_sha256=image_sha256,
            provider=result.get("provider", "local-vlm"),
            model=result.get("model", "local-vlm"),
            confidence=float(result.get("confidence") or 0),
            initial_json=json.dumps(result, ensure_ascii=False, default=str),
            status="analyzed",
        )
        db.add(session)
        db.flush()
        result = {
            "analysis_id": session.id,
            **result,
            "editable": True,
            "requires_confirmation": True,
        }
        latency_ms = float((body.metrics or {}).get("latency_ms") or 0)
        record_metric(
            db,
            job.user_id,
            "food_analysis_latency_ms",
            latency_ms,
            "ms",
            "local_ai_worker",
            True,
            {
                "provider": result.get("provider"),
                "confidence": result.get("confidence"),
                "worker_id": body.worker_id,
            },
        )
        add_event(
            db,
            job.user_id,
            "food_analysis_completed",
            {
                "job_id": job.id,
                "analysis_id": session.id,
                "provider": result.get("provider"),
                "confidence": result.get("confidence"),
            },
            source="local_ai_worker",
            ref_type="ai_job",
            ref_id=job.id,
        )

    elif job.job_type == "kinetics400":
        latency_ms = float((body.metrics or {}).get("latency_ms") or 0)
        record_metric(
            db,
            job.user_id,
            "kinetics400_processing_ms",
            latency_ms,
            "ms",
            "local_ai_worker",
            True,
            {
                "top_label": str(result.get("top_label", ""))[:80],
                "worker_id": body.worker_id,
            },
        )
        add_event(
            db,
            job.user_id,
            "kinetics400_analysis_completed",
            {
                "job_id": job.id,
                "top_label": str(result.get("top_label", ""))[:80],
                "top_probability": result.get("top_probability"),
                "mapped_exercise": result.get("mapped_exercise"),
            },
            source="local_ai_worker",
            ref_type="ai_job",
            ref_id=job.id,
        )

    elif job.job_type == "motion_pose":
        requested_type = json_loads(job.payload_json, {}).get("exercise_type", "squat")
        recognition = result.get("recognition") or {}
        if requested_type == "auto":
            if (
                recognition.get("mode") != "auto"
                or recognition.get("requested_type") != "auto"
            ):
                raise HTTPException(422, "自动识别任务缺少可信的识别结果")
            exercise_type = (
                recognition.get("selected_type")
                if recognition.get("accepted") is True
                else None
            )
        else:
            if recognition and (
                recognition.get("mode") != "manual"
                or recognition.get("selected_type") != requested_type
            ):
                raise HTTPException(422, "手动动作类型与识别结果不一致")
            exercise_type = requested_type
        recognition_confidence = float(
            recognition.get("confidence") if recognition else 1.0
        )
        result["training_semantics"] = build_motion_semantics(
            db,
            user_id=job.user_id,
            job_id=job.id,
            exercise_type=exercise_type,
            recognition_confidence=recognition_confidence,
            observed_semantics=result.get("compositional_semantics"),
        )
        score = result.get("score") or {}
        if score.get("available"):
            if exercise_type not in {"squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw"}:
                raise HTTPException(422, "未确认动作类型时不得保存动作评分")
            db.add(
                MotionScore(
                    user_id=job.user_id,
                    job_id=job.id,
                    exercise_type=exercise_type,
                    requested_exercise_type=requested_type,
                    recognition_method=str(
                        recognition.get("method") or "legacy_user_selected"
                    )[:80],
                    recognition_confidence=float(
                        recognition.get("confidence") if recognition else 1.0
                    ),
                    completeness=float(score["completeness"]),
                    stability=float(score["stability"]),
                    rhythm_control=float(score["rhythm_control"]),
                    risk_index=float(score["risk_index"]),
                    overall=float(score["overall"]),
                    confidence=float(score["confidence"]),
                    evidence_json=json.dumps(score, ensure_ascii=False, default=str),
                )
            )
        for index, frame in enumerate(result.get("frames", [])[:200]):
            event_evidence = {
                key: value
                for key, value in frame.items()
                if key not in {"image_b64", "image_mime"}
            }
            db.add(
                MotionEvent(
                    user_id=job.user_id,
                    job_id=job.id,
                    event_index=index,
                    event_type=str(frame.get("event", "event"))[:80],
                    timestamp_seconds=float(frame.get("timestamp", 0)),
                    severity=str(frame.get("severity", "info"))[:20],
                    evidence_json=json.dumps(
                        event_evidence, ensure_ascii=False, default=str
                    ),
                )
            )
        latency_ms = float((body.metrics or {}).get("latency_ms") or 0)
        record_metric(
            db,
            job.user_id,
            "motion_processing_ms",
            latency_ms,
            "ms",
            "local_ai_worker",
            True,
            {
                "exercise_type": exercise_type or "unrecognized",
                "requested_exercise_type": requested_type,
                "worker_id": body.worker_id,
            },
        )
        pose = result.get("pose") or {}
        if pose.get("available") and pose.get("keypoint_valid_rate") is not None:
            record_metric(
                db,
                job.user_id,
                "pose_keypoint_valid_rate_pct",
                float(pose["keypoint_valid_rate"]) * 100,
                "%",
                "local_ai_worker",
                True,
                {"worker_id": body.worker_id},
            )
        add_event(
            db,
            job.user_id,
            "motion_analysis_completed",
            {
                "job_id": job.id,
                "exercise_type": exercise_type,
                "requested_exercise_type": requested_type,
                "recognition": {
                    "accepted": recognition.get("accepted"),
                    "confidence": recognition.get("confidence"),
                    "method": recognition.get("method"),
                }
                if recognition
                else None,
                "result_summary": {
                    "method": result.get("method"),
                    "reps": pose.get("reps"),
                    "errors": pose.get("errors", [])[:4],
                    "overall_score": score.get("overall"),
                    "goal_alignment": result.get("training_semantics", {}).get(
                        "goal_alignment"
                    ),
                },
            },
            source="local_ai_worker",
            ref_type="ai_job",
            ref_id=job.id,
        )
    elif job.job_type == "motion_unified":
        # R14 / spec 9.1: the receipt transaction ONLY validates, stores local
        # evidence and enqueues post-processing stage rows; it commits before any
        # external (DeepSeek) call. The post-processing then advances the persisted
        # stages with compare-and-set versioning, so a restart resumes from the rows
        # and a stale consumer cannot overwrite a newer result.
        run = db.scalar(
            select(MotionAnalysisRun).where(
                MotionAnalysisRun.ai_job_id == job.id
            )
        )
        if run is None:
            logger.warning("motion_unified job %s has no run row", job.id)
        else:
            _persist_motion_evidence(db, run, result)
            consent = bool(json_loads(job.payload_json, {}).get("consent_deepseek_frames"))
            run.result_version = 1
            run.effective_pipeline_version = run.pipeline_version
            # B-package contract: legacy consent_deepseek_frames=True maps to
            # redacted_frames; any explicit cloud_review_mode in payload wins.
            explicit_mode = json_loads(job.payload_json, {}).get("cloud_review_mode")
            run.cloud_review_mode = explicit_mode or ("redacted_frames" if consent else "off")
            run.status = "evidence_ready"
            run.error_code = None
            db.flush()
            # Spec 9.1: commit the local evidence + queued stages and return.
            # DeepSeek visual review / summary happen in the background consumer
            # (stage_tasks.process_pending_stages); the HTTP receipt MUST NOT wait.
            enqueue_postprocessing_stages(db, run_id=run.id, payload={"job_id": job.id})
            db.commit()
    else:
        raise HTTPException(422, "不支持的 AI 任务类型")

    job.result_json = json.dumps(result, ensure_ascii=False, default=str)
    job.status = "done"
    job.progress = 100
    job.finished_at = now
    job.lease_expires_at = None
    db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status}


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, body: WorkerFailIn, db: Session = Depends(get_db)):
    job = db.get(AIJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    try:
        extend_lease(
            db,
            job,
            worker_id=body.worker_id,
            lease_token=body.lease_token,
            progress=job.progress,
            commit=False,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    retry = bool(
        body.retryable
        and job.attempts < settings.worker_max_attempts
        and body.error_code not in {"unsafe_media_url", "invalid_media"}
    )
    job.error_code = body.error_code
    job.error_message = body.error_message[:800]
    job.lease_expires_at = None
    job.lease_token = ""
    job.worker_id = ""
    job.claim_request_id = None
    if body.error_code in {"media_url_expired", "media_url_missing"}:
        job.status = "waiting_source_refresh"
        job.finished_at = None
        # A download rejection did not consume an inference attempt.
        job.attempts = max(0, job.attempts - 1)
        retry = False
    elif retry:
        job.status = "queued"
        job.progress = 0
        job.next_attempt_at = utc_now() + timedelta(seconds=min(60, 2**job.attempts))
    else:
        job.status = "failed"
        job.finished_at = utc_now()
        metric_name = (
            "motion_processing_ms"
            if job.job_type == "motion_pose"
            else (
                "kinetics400_processing_ms"
                if job.job_type == "kinetics400"
                else "food_analysis_latency_ms"
            )
        )
        record_metric(
            db,
            job.user_id,
            metric_name,
            0,
            "ms",
            "local_ai_worker",
            False,
            {"error_code": body.error_code, "error_type": "worker_failure"},
        )
    db.commit()
    return {"ok": True, "job_id": job.id, "status": job.status, "retrying": retry}
