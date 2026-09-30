"""Unified motion orchestrator: state machine, fusion, caching and ledger.

V2 rebuild (work package A). The worker receipt is consumed as the local
inference output. The recognition gate (decision.py) is rebuilt so that:

* a reliable LOCAL result stands on its own (R03) - the cloud review is a
  supplement / correction source, not a precondition;
* Kinetics-400 candidates are normalised by namespace and retained alongside pose
  candidates, never numerically cross-sorted or force-mapped (R05);
* the visual review may propose open categories and is not rejected for being
  outside the local six-class candidate set (R06);
* local reps/scores are bound to the measured exercise and dropped when the
  category changes (spec §6.4 / T05);
* the result follows contract §3 (recognition/capabilities/summary/metrics/
  timeline/notices) and the prose is natural Chinese only (R01/R09).

The C package (vision_review.run_visual_review / text_summary.build_summary) is
called through a defensive seam: when C is not deployed yet the chain degrades
to a local-only result instead of crashing. No real model/network call happens
offline; the gateway ledger records the invocation attempts.
"""

from __future__ import annotations

import base64
import binascii
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
    KineticsCandidate,
    LocalEvidence,
    MotionDecision,
    QualityEvidence,
    VisionEvidence,
    decide_motion,
    quality_from_receipt,
)
from app.services.motion.provider_gateway import ProviderBudgetExceeded, ProviderGateway, Timer

logger = logging.getLogger("healthmate.motion.orchestrator")

PIPELINE_VERSION = "motion-unified-v1"
RESULT_VERSION = 1
CONSENT_VERSION = "deepseek-frames-v1"
UNIFIED_SCHEMA_VERSION = "motion-unified-v2"

VISION_OPERATION = "deepseek_vision_review"
TEXT_OPERATION = "deepseek_grounded_summary"

REVIEW_STATUS_USED = "used"
REVIEW_STATUS_SKIPPED = "skipped"
REVIEW_STATUS_UNAVAILABLE = "unavailable"

_TERMINAL_STATUSES = {"completed", "partial", "abstained", "failed", "cancelled"}

# Receipt JPEG previews are capped at 4 (V1 receipt contract). V2 raises this to
# 8 via the B/E package receipt + storage work; the limit lives there.
MAX_PREVIEW_FRAMES = 4


def json_loads(value, default):
    try:
        out = json.loads(value or "")
        return out if out is not None else default
    except (ValueError, TypeError):
        return default


def make_trace_id() -> str:
    return "trace-" + uuid.uuid4().hex[:24]


# --------------------------------------------------------------------------- #
# Run creation (idempotent, spec §2)
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
    """Create (or reuse) a unified run + its AIJob."""
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
# Cache (identical media/pipeline/consent -> reuse review+summary, no re-bill).
# --------------------------------------------------------------------------- #

def _cache_key(
    run: MotionAnalysisRun,
    asset: MediaAsset | None,
    candidate_ids: set[str],
    kinetics_key: str,
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
            kinetics_key,
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
# Receipt decoding helpers (self-contained; no C-package import at module load)
# --------------------------------------------------------------------------- #

def _decode_preview_jpeg(frame: dict) -> bytes | None:
    """Return raw JPEG bytes of a receipt frame, or None. Desensitized only."""
    encoded = frame.get("image_b64")
    if not isinstance(encoded, str):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
        return None
    return raw


def _frame_image_data_url(frame: dict) -> str | None:
    raw = _decode_preview_jpeg(frame)
    if not raw:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _normalize_kinetics(kinetics_raw: dict | None) -> list[KineticsCandidate]:
    """Normalise receipt.kinetics.candidates by namespace (spec §6.2 / R05).

    Each candidate keeps source/source_label/canonical_id/raw_score/score_type.
    Values from different sources are NOT comparable and never numerically
    cross-sorted. Catalog-unmapped labels keep canonical_id=None so they remain
    a visual reference; we never force-map them to a legacy six-class action.
    """
    from app.services.motion import catalog

    out: list[KineticsCandidate] = []
    for c in (kinetics_raw or {}).get("candidates") or []:
        if not isinstance(c, dict):
            continue
        label = c.get("source_label") or c.get("label")
        if not label:
            continue
        canonical = catalog.map_kinetics_label(str(label))
        score = c.get("raw_score")
        out.append(
            KineticsCandidate(
                source_label=str(label),
                canonical_id=canonical,
                raw_score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else 0.0,
                score_type=str(c.get("score_type") or "softmax"),
            )
        )
    return out


def _kinetics_cache_key(kinetics: list[KineticsCandidate]) -> str:
    return ";".join(
        f"{k.source_label}:{k.canonical_id}:{k.score_type}" for k in kinetics
    )


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


# --------------------------------------------------------------------------- #
# C-package integration seams (defensive: C ships in parallel)
# --------------------------------------------------------------------------- #

def _call_visual_review(*, frames, context, catalog):
    """Call C: vision_review.run_visual_review(frames, context, catalog) -> CoachReview.

    Raises RuntimeError when C has not deployed the new contract yet; the caller
    degrades to a local-only result. Tests monkeypatch
    ``vision_review.run_visual_review``; the getattr at call time makes that work.
    """
    from app.services.motion import vision_review as vr

    fn = getattr(vr, "run_visual_review", None)
    if fn is None:
        raise RuntimeError("vision_review.run_visual_review not deployed")
    return fn(frames, context, catalog)


def _call_text_summary(*, coach_review, metrics, evidence, catalog):
    """Call C: text_summary.build_summary(coach_review, metrics, evidence, catalog)."""
    from app.services.motion import text_summary as ts

    fn = getattr(ts, "build_summary", None)
    if fn is None:
        raise RuntimeError("text_summary.build_summary not deployed")
    return fn(coach_review, metrics, evidence, catalog)


def _coach_to_vision(coach_review) -> VisionEvidence:
    """Adapt a C CoachReview (§4) into the decision gate's VisionEvidence."""
    cid = getattr(coach_review, "canonical_id", None)
    novel = getattr(coach_review, "novel_label_zh", None)
    ident = getattr(coach_review, "identification", "unknown")
    reason = getattr(coach_review, "identification_reason", "") or ""
    notes = getattr(coach_review, "frame_notes", None) or []
    ev_ids = tuple(
        getattr(n, "frame_id", None) for n in notes if getattr(n, "frame_id", None)
    )
    # C has already run structural + semantic validation (frame refs exist, time
    # windows, no structural contradiction). A returned CoachReview is therefore
    # treated as observation-grounded; decision still gates on identification.
    return VisionEvidence(
        canonical_id=cid,
        novel_label_zh=novel,
        identification=ident,
        identification_reason=reason,
        supported_by_observations=True,
        evidence_ids=ev_ids,
    )


def _coach_frame_notes(coach_review) -> dict:
    """Index C CoachReview.frame_notes by frame_id -> note dict."""
    out: dict[str, dict] = {}
    notes = getattr(coach_review, "frame_notes", None) or []
    for n in notes:
        fid = getattr(n, "frame_id", None)
        if not fid:
            continue
        refs = []
        for r in (getattr(n, "evidence_refs", None) or []):
            refs.append(
                {
                    "frame_ids": list(getattr(r, "frame_ids", []) or []),
                    "start_ms": int(getattr(r, "start_ms", 0) or 0),
                    "end_ms": int(getattr(r, "end_ms", 0) or 0),
                }
            )
        out[fid] = {
            "phase": getattr(n, "phase", "") or "",
            "observation": getattr(n, "observation", "") or "",
            "explanation": getattr(n, "explanation", "") or "",
            "next_step": getattr(n, "next_step", "") or "",
            "advice_kind": getattr(n, "advice_kind", "general_tip"),
            "evidence_refs": refs,
        }
    return out


# --------------------------------------------------------------------------- #
# Local (degraded) summary + timeline wording - Chinese only (R01 / R09)
# --------------------------------------------------------------------------- #

_GENERIC_OBSERVATION = "这一时刻记录到动作画面，身体在连续完成动作。"
_GENERIC_NEXT_STEP = "下一遍放慢一点，留意动作控制与节奏。"


def _local_summary_text(
    decision: MotionDecision,
    pose: dict,
    score_in: dict,
    indexed: list[tuple[str, dict]],
) -> tuple[str, str, str]:
    """Deterministic Chinese summary when C text summary is unavailable.

    Prefers any Chinese finding/advice the Worker already produced in frames[];
    never emits English event codes and never the standing-only template (R09).
    """
    rec = decision.recognition
    reps = pose.get("reps") if pose.get("available") else None

    head = rec.display_name or "这段动作"
    if rec.state == "identified" and rec.canonical_id:
        head = f"这段{rec.display_name}"
    elif rec.state == "likely":
        head = f"{rec.display_name}的片段"

    parts = [f"{head}的动作轨迹可以看清。"]
    if isinstance(reps, int) and reps >= 1 and decision.metrics_applicable:
        parts.append(f"本次记录到约 {reps} 次动作。")

    # Prefer a Worker-authored Chinese observation as the grounded sentence.
    chinese_obs = ""
    for _, fr in indexed:
        cand = fr.get("finding") or fr.get("observation")
        if isinstance(cand, str) and len(cand) >= 4:
            chinese_obs = cand
            break
    if chinese_obs:
        parts.append(chinese_obs)

    primary = _GENERIC_NEXT_STEP
    for _, fr in indexed:
        cand = fr.get("advice") or fr.get("next_step")
        if isinstance(cand, str) and len(cand) >= 4:
            primary = cand
            break

    text = "".join(parts)
    if decision.reason_code == "LOCAL_RELIABLE":
        text += "本次使用动作轨迹分析，AI 补充讲解暂未完成。"
    return text, primary, "local_fallback"


# --------------------------------------------------------------------------- #
# Worker receipt -> unified result
# --------------------------------------------------------------------------- #

def handle_unified_worker_result(
    db: Session,
    job: AIJob,
    receipt: dict,
    metrics: dict | None = None,
) -> None:
    """Consume a validated worker receipt and materialise the §3 unified result."""
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
    kinetics_raw = receipt.get("kinetics") or {}  # R05: was silently dropped before

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

    kinetics_cands = _normalize_kinetics(kinetics_raw)
    local = LocalEvidence(
        accepted=bool(recognition.get("accepted")),
        label_id=recognition.get("selected_type"),
        measured_exercise_id=pose.get("exercise_type") or recognition.get("selected_type"),
        metrics_valid=bool(score_in.get("available")),
    )
    candidate_ids = {
        c.get("exercise_type")
        for c in (recognition.get("candidates") or [])[:5]
        if isinstance(c, dict) and c.get("exercise_type")
    }
    if local.accepted and local.label_id:
        candidate_ids.add(local.label_id)
    quality = quality_from_receipt(recognition, pose, frames)

    run.status = "evidence_ready"
    run.model_versions_json = json.dumps(
        {
            "consent_deepseek_frames": bool(payload.get("consent_deepseek_frames")),
            "worker_method": str(recognition.get("method") or "")[:80],
            "worker_vision_model": "motion_unified_v2",
            "catalog_version": _catalog_version(),
            "deepseek_vision_model": settings_vision_model(),
        },
        ensure_ascii=False,
    )
    db.flush()
    t1 = utc_now()
    stage("evidence_ready", t0, t1)

    # Indexed frames: frame:N refs are the internal evidence ids the model cites.
    indexed = [(f"frame:{i}", fr) for i, fr in enumerate(frames) if isinstance(fr, dict)]
    frame_ids = {fid for fid, _ in indexed}
    previews: list[tuple[str, bytes]] = []
    preview_digests: list[str] = []
    for fid, fr in indexed:
        raw = _decode_preview_jpeg(fr)
        if raw:
            previews.append((fid, raw))
            preview_digests.append(hashlib.sha256(raw).hexdigest())
    previews = previews[:MAX_PREVIEW_FRAMES]

    gateway = ProviderGateway(db, user_id=job.user_id, run_id=run.id)
    consent = bool(payload.get("consent_deepseek_frames"))
    k_key = _kinetics_cache_key(kinetics_cands)
    cache_key = _cache_key(run, asset or run, candidate_ids, k_key, pose, preview_digests)

    review_status = REVIEW_STATUS_SKIPPED
    coach_review = None
    cached_summary_text: str | None = None
    cached_summary_next: str | None = None

    cached = _load_cached_decision(db, job.user_id, run.media_asset_id, cache_key, run.id)
    if cached and cached.get("recognition", {}).get("review_status") == REVIEW_STATUS_USED:
        # Cache hit: rebuild a lightweight vision evidence from the stored result.
        rec_cached = cached.get("recognition") or {}
        coach_review = _coerce_cached_coach(rec_cached, cached.get("timeline", {}))
        cached_summary_text = (cached.get("summary") or {}).get("text")
        cached_summary_next = (cached.get("summary") or {}).get("primary_next_step")
        review_status = REVIEW_STATUS_USED
        t2 = utc_now()
        stage("visual_review", t1, t2, reason="cache_hit")

    want_review = bool(
        consent
        and quality.has_person
        and quality.usable_frames >= 3
        and previews
        and _deepseek_configured()
    )

    if review_status != REVIEW_STATUS_USED and want_review:
        run.status = "visual_review"
        db.flush()
        context = {
            "candidates": sorted(candidate_ids),
            "kinetics_candidates": [
                {
                    "source": "kinetics",
                    "source_label": k.source_label,
                    "canonical_id": k.canonical_id,
                    "raw_score": k.raw_score,
                    "score_type": k.score_type,
                }
                for k in kinetics_cands
            ],
            "measurements": _measurements(recognition, pose),
            "visible_regions": sorted(
                {
                    r
                    for _, fr in indexed
                    for r in (fr.get("visible_regions") or [])
                    if isinstance(r, str)
                }
            ),
        }
        started = utc_now()
        try:
            gateway.check_budget(VISION_OPERATION)
            with Timer() as timer:
                coach_review = _call_visual_review(
                    frames=previews, context=context, catalog=_catalog_module()
                )
            gateway.record(
                operation=VISION_OPERATION,
                request_fingerprint=cache_key,
                status="ok",
                latency_ms=timer.ms,
                tokens=getattr(coach_review, "_meta", {}).get("tokens") if not isinstance(coach_review, dict) else None,
                provider_request_id=getattr(coach_review, "_meta", {}).get("provider_request_id") if not isinstance(coach_review, dict) else None,
            )
            review_status = REVIEW_STATUS_USED
        except ProviderBudgetExceeded:
            review_status = REVIEW_STATUS_UNAVAILABLE
            coach_review = None
        except Exception as exc:  # noqa: BLE001 - degrade, never crash the worker
            gateway.record(
                operation=VISION_OPERATION,
                request_fingerprint=cache_key,
                status="error:" + type(exc).__name__,
                latency_ms=getattr(locals().get("timer"), "ms", 0),
            )
            review_status = REVIEW_STATUS_UNAVAILABLE
            coach_review = None
        stage("visual_review", started, utc_now(), reason=review_status)
    elif review_status != REVIEW_STATUS_USED:
        t2 = utc_now()
        reason = (
            "no_consent" if not consent
            else "no_evidence" if not quality.has_person or quality.usable_frames < 3
            else "no_previews" if not previews
            else "model_unconfigured"
        )
        stage("visual_review", t1, t2, reason=reason)

    vision_evidence = _coach_to_vision(coach_review) if coach_review is not None else None
    decision: MotionDecision = decide_motion(local, vision_evidence, tuple(kinetics_cands), quality)

    # ---- summary stage --------------------------------------------------- #
    run.status = "feedback_generation"
    db.flush()
    started = utc_now()
    if cached_summary_text is not None:
        summary_text = cached_summary_text
        primary_next_step = cached_summary_next or ""
        summary_source = "cached"
        summary_degraded = True
    else:
        summary_text, primary_next_step, summary_source = _resolve_summary(
            db=db,
            gateway=gateway,
            cache_key=cache_key,
            consent=consent,
            decision=decision,
            coach_review=coach_review,
            recognition=recognition,
            pose=pose,
            score_in=score_in,
            indexed=indexed,
        )
        summary_degraded = summary_source not in ("visual_coach", "deepseek_grounded")
    stage("feedback_generation", started, utc_now(), reason=summary_source)

    # ---- materialise the §3 result -------------------------------------- #
    result = _build_result(
        run=run,
        decision=decision,
        recognition=recognition,
        pose=pose,
        score_in=score_in,
        indexed=indexed,
        kinetics_cands=kinetics_cands,
        review_status=review_status,
        coach_review=coach_review,
        summary_text=summary_text,
        primary_next_step=primary_next_step,
        summary_source=summary_source,
        summary_degraded=summary_degraded,
        stage_log=stage_log,
        cache_key=cache_key,
        worker_method=str(recognition.get("method") or "local_worker"),
    )

    if decision.recognition.state == "unknown" and decision.reason_code == "INSUFFICIENT_VIDEO_EVIDENCE":
        final_status = "abstained"
    elif review_status == REVIEW_STATUS_UNAVAILABLE:
        # Local result retained but cloud coaching incomplete (R03).
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


def _resolve_summary(
    *,
    db: Session,
    gateway: ProviderGateway,
    cache_key: str,
    consent: bool,
    decision: MotionDecision,
    coach_review,
    recognition: dict,
    pose: dict,
    score_in: dict,
    indexed,
) -> tuple[str, str, str]:
    """Use C build_summary when available; otherwise a Chinese local fallback."""
    if (
        consent
        and coach_review is not None
        and _deepseek_configured()
        and decision.recognition.state != "unknown"
    ):
        try:
            gateway.check_budget(TEXT_OPERATION)
            metrics_payload = _metrics_payload(decision, pose, score_in)
            evidence_payload = {"frames": [fid for fid, _ in indexed[:4]]}
            with Timer() as timer:
                out = _call_text_summary(
                    coach_review=coach_review,
                    metrics=metrics_payload,
                    evidence=evidence_payload,
                    catalog=_catalog_module(),
                )
            text = (out or {}).get("text") if isinstance(out, dict) else getattr(out, "text", None)
            nxt = (out or {}).get("primary_next_step") if isinstance(out, dict) else getattr(out, "primary_next_step", None)
            src = (out or {}).get("source") if isinstance(out, dict) else getattr(out, "source", None)
            if isinstance(text, str) and text.strip():
                gateway.record(
                    operation=TEXT_OPERATION,
                    request_fingerprint=hashlib.sha256(
                        (cache_key + ":summary").encode()
                    ).hexdigest(),
                    status="ok",
                    latency_ms=timer.ms,
                )
                return text.strip(), (nxt or "") if isinstance(nxt, str) else "", src or "visual_coach"
        except ProviderBudgetExceeded:
            pass
        except Exception as exc:  # noqa: BLE001
            gateway.record(
                operation=TEXT_OPERATION,
                request_fingerprint=hashlib.sha256((cache_key + ":summary").encode()).hexdigest(),
                status="error:" + type(exc).__name__,
                latency_ms=getattr(locals().get("timer"), "ms", 0),
            )
    return _local_summary_text(decision, pose, score_in, indexed)


def _metrics_payload(decision: MotionDecision, pose: dict, score_in: dict) -> list[dict]:
    """Build the §3 metrics list (empty when category changed / no scorer)."""
    if not decision.metrics_applicable:
        return []
    out: list[dict] = []
    cid = decision.recognition.canonical_id
    reps = pose.get("reps") if pose.get("available") else None
    if isinstance(reps, int) and reps >= 1:
        out.append(
            {"id": "reps", "value": reps, "unit": "次", "source": "local_pose",
             "scorer_version": "rules_v1", "applicable_to": cid}
        )
    if decision.scoreable and score_in.get("available"):
        for key, unit in (
            ("completeness", "分"),
            ("stability", "分"),
            ("rhythm_control", "分"),
            ("risk_index", "风险分"),
            ("overall", "分"),
        ):
            v = score_in.get(key)
            if isinstance(v, (int, float)):
                out.append(
                    {"id": key, "value": v, "unit": unit, "source": "local_pose",
                     "scorer_version": "rules_v1", "applicable_to": cid}
                )
    return out


def _catalog_module():
    from app.services.motion import catalog
    return catalog


def _catalog_version() -> str:
    try:
        return _catalog_module().catalog_version()
    except Exception:  # pragma: no cover
        return "unknown"


def _coerce_cached_coach(rec_cached: dict, timeline: dict):
    """Best-effort CoachReview stand-in when restoring a cached recognition."""
    try:
        from types import SimpleNamespace

        notes = []
        for fr in (timeline or {}).get("frames") or []:
            notes.append(
                SimpleNamespace(
                    frame_id=fr.get("id"),
                    phase=fr.get("phase", ""),
                    observation=fr.get("observation", ""),
                    explanation=fr.get("explanation", ""),
                    next_step=fr.get("next_step", ""),
                    advice_kind=fr.get("advice_kind", "general_tip"),
                    evidence_refs=[],
                )
            )
        return SimpleNamespace(
            canonical_id=rec_cached.get("canonical_id"),
            novel_label_zh=rec_cached.get("novel_label_zh"),
            identification=rec_cached.get("state", "identified"),
            identification_reason=rec_cached.get("reason", ""),
            frame_notes=notes,
        )
    except Exception:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# §3 result builder
# --------------------------------------------------------------------------- #

def _build_result(
    *,
    run: MotionAnalysisRun,
    decision: MotionDecision,
    recognition: dict,
    pose: dict,
    score_in: dict,
    indexed: list[tuple[str, dict]],
    kinetics_cands: list[KineticsCandidate],
    review_status: str,
    coach_review,
    summary_text: str,
    primary_next_step: str,
    summary_source: str,
    summary_degraded: bool,
    stage_log: list[dict],
    cache_key: str,
    worker_method: str,
) -> dict:
    rec = decision.recognition
    metrics = _metrics_payload(decision, pose, score_in)
    notes_by_frame = _coach_frame_notes(coach_review) if coach_review is not None else {}

    # ---- timeline frames (Chinese only; prefer Worker chinese finding/advice) -- #
    timeline_frames: list[dict] = []
    duration_ms = 0
    for fid, fr in indexed:
        real_id = fr.get("frame_id") or fid
        t_ms = int(float(fr.get("timestamp_ms") or (float(fr.get("timestamp", 0)) * 1000)))
        duration_ms = max(duration_ms, t_ms)
        note = notes_by_frame.get(fid) or notes_by_frame.get(real_id) or {}

        # R09: prefer Worker-authored Chinese inline keys; never raw English event.
        phase = (
            note.get("phase")
            or (fr.get("phase") if isinstance(fr.get("phase"), str) else None)
            or "动作阶段"
        )
        observation = (
            note.get("observation")
            or (fr.get("finding") if isinstance(fr.get("finding"), str) and fr.get("finding") else None)
            or _GENERIC_OBSERVATION
        )
        explanation = note.get("explanation") or ""
        next_step = (
            note.get("next_step")
            or (fr.get("advice") if isinstance(fr.get("advice"), str) and fr.get("advice") else None)
            or _GENERIC_NEXT_STEP
        )
        advice_kind = note.get("advice_kind") or (
            "general_tip" if note else "timeline_position"
        )
        refs = note.get("evidence_refs") or [
            {"frame_ids": [real_id], "start_ms": t_ms, "end_ms": t_ms}
        ]
        timeline_frames.append(
            {
                "id": real_id,
                "timestamp_ms": t_ms,
                "preview_asset_id": fr.get("preview_asset_id"),
                "preview_url": _frame_image_data_url(fr),
                "phase": phase,
                "observation": observation,
                "explanation": explanation,
                "next_step": next_step,
                "advice_kind": advice_kind,
                "evidence_refs": refs,
            }
        )
    timeline_frames.sort(key=lambda f: f["timestamp_ms"])

    # ---- capabilities (5 independent keys) ------------------------------ #
    has_frames = bool(timeline_frames)
    capabilities = {
        "recognition": "available" if rec.state != "unknown" else "unavailable",
        "timeline": "available" if has_frames else "unavailable",
        "coaching": "available" if bool(summary_text) else "unavailable",
        "repetitions": "available" if any(m["id"] == "reps" for m in metrics) else "unavailable",
        "quality_score": "available" if decision.scoreable else "unavailable",
    }

    # ---- notices --------------------------------------------------------- #
    notices: list[dict] = []
    if review_status == REVIEW_STATUS_UNAVAILABLE and rec.state != "unknown":
        notices.append(
            {"kind": "cloud_review_incomplete", "text": "本次使用动作轨迹分析，AI 补充讲解暂未完成。"}
        )
    if rec.state != "unknown" and not decision.scoreable:
        notices.append(
            {"kind": "metric_unavailable", "text": "这类动作本次提供画面讲解，暂不显示数值评分。"}
        )
    if rec.state == "likely" and rec.novel_label_zh:
        notices.append(
            {"kind": "confirm_label", "text": "这是根据画面给出的初步判断，可在结果页手动确认动作类别。"}
        )

    return {
        "analysis_id": run.id,
        "status": run.status,
        "pipeline_version": run.pipeline_version,
        "result_version": RESULT_VERSION,
        "recognition": {
            "state": rec.state,
            "canonical_id": rec.canonical_id,
            "display_name": rec.display_name,
            "reason": rec.reason,
            "source": rec.source,
            "novel_label_zh": rec.novel_label_zh,
            "review_status": review_status,
        },
        "capabilities": capabilities,
        "summary": {
            "text": summary_text,
            "primary_next_step": primary_next_step,
            "source": summary_source,
            "degraded": summary_degraded,
        },
        "metrics": metrics,
        "timeline": {"duration_ms": duration_ms, "frames": timeline_frames},
        "notices": notices,
        # Internal bookkeeping (stripped before user display by F/D; drives cache).
        "_meta": {
            "cache_key": cache_key,
            "stages": stage_log,
            "worker_method": worker_method,
            "model_versions": run.model_versions,
            "parent_run_id": run.parent_run_id,
            "reason_code": decision.reason_code,
            "sources": list(decision.sources),
            "kinetics_candidates": [
                {
                    "source": "kinetics",
                    "source_label": k.source_label,
                    "canonical_id": k.canonical_id,
                    "raw_score": k.raw_score,
                    "score_type": k.score_type,
                }
                for k in kinetics_cands
            ],
        },
    }


# --------------------------------------------------------------------------- #
# E-package post-processing hook (async stage-task consumer)
# --------------------------------------------------------------------------- #

def apply_post_review(
    db: Session,
    *,
    run_id: int,
    coach_review,
    evidence: dict,
) -> dict:
    """Rebuild a persisted run's result after an async CoachReview lands.

    Called by the E-package stage-task consumer once the (previously deferred)
    visual review / summary stage completes. It does NOT call the model itself;
    it fuses the already-produced CoachReview with the stored local evidence and
    rewrites the stored result_json.

    Signature (contract §8 / work-package A delivery):

        apply_post_review(
            db: Session,
            *,
            run_id: int,
            coach_review: CoachReview,          # C §4 pydantic model (already validated)
            evidence: dict = {
                "recognition": dict,             # worker receipt.recognition
                "pose": dict,                   # worker receipt.pose
                "score": dict,                   # worker receipt.score
                "frames": list[dict],            # worker receipt.frames
                "kinetics": dict,                # worker receipt.kinetics (may be {})
                "candidate_ids": set[str],
                "summary": {"text": str|None, "primary_next_step": str|None,
                            "source": str},       # already produced summary (optional)
            },
        ) -> dict                               # the rebuilt result_json
    """
    run = db.get(MotionAnalysisRun, run_id)
    if run is None:
        raise LookupError(f"run {run_id} not found")

    recognition = evidence.get("recognition") or {}
    pose = evidence.get("pose") or {}
    score_in = evidence.get("score") or {}
    frames = evidence.get("frames") or []
    kinetics_cands = _normalize_kinetics(evidence.get("kinetics") or {})

    local = LocalEvidence(
        accepted=bool(recognition.get("accepted")),
        label_id=recognition.get("selected_type"),
        measured_exercise_id=pose.get("exercise_type") or recognition.get("selected_type"),
        metrics_valid=bool(score_in.get("available")),
    )
    quality = quality_from_receipt(recognition, pose, frames)
    vision_evidence = _coach_to_vision(coach_review) if coach_review is not None else None
    decision = decide_motion(local, vision_evidence, tuple(kinetics_cands), quality)

    indexed = [(f"frame:{i}", fr) for i, fr in enumerate(frames) if isinstance(fr, dict)]
    summary = evidence.get("summary") or {}
    summary_text = summary.get("text") or ""
    primary_next_step = summary.get("primary_next_step") or ""
    summary_source = summary.get("source") or "visual_coach"
    if not summary_text:
        summary_text, primary_next_step, summary_source = _local_summary_text(
            decision, pose, score_in, indexed
        )

    result = _build_result(
        run=run,
        decision=decision,
        recognition=recognition,
        pose=pose,
        score_in=score_in,
        indexed=indexed,
        kinetics_cands=kinetics_cands,
        review_status=REVIEW_STATUS_USED if coach_review is not None else REVIEW_STATUS_SKIPPED,
        coach_review=coach_review,
        summary_text=summary_text,
        primary_next_step=primary_next_step,
        summary_source=summary_source,
        summary_degraded=summary_source not in ("visual_coach", "deepseek_grounded"),
        stage_log=list(evidence.get("stages") or []),
        cache_key=str(evidence.get("cache_key") or "post-review"),
        worker_method=str(recognition.get("method") or "local_worker"),
    )

    feedback = db.scalar(
        select(MotionAnalysisFeedback).where(MotionAnalysisFeedback.run_id == run.id)
    )
    if feedback is None:
        feedback = MotionAnalysisFeedback(run_id=run.id, user_id=run.user_id)
        db.add(feedback)
    feedback.schema_version = UNIFIED_SCHEMA_VERSION
    feedback.result_json = json.dumps(result, ensure_ascii=False, default=str)
    run.status = "completed"
    run.finished_at = utc_now()
    db.commit()
    return result


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
        "result_version": RESULT_VERSION,
        "requested_type": run.requested_type,
        "created_at": run.created_at.isoformat() + "Z",
        "finished_at": (run.finished_at.isoformat() + "Z") if run.finished_at else None,
    }


def _deepseek_configured() -> bool:
    from app.core.config import settings

    return bool(settings.deepseek_api_key)


def settings_vision_model() -> str:
    from app.core.config import settings

    return settings.deepseek_vision_model or settings.deepseek_model
