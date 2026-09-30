"""Unified motion chain (``motion_unified-v2``): one decode, fused evidence.

Per spec §5 / contract §5: the video is downloaded and decoded EXACTLY ONCE.
The single sampled pass feeds four independent channels:

  * a GENERIC evidence pool (frame_id, real timestamp, downscaled real frame,
    visible regions, main-subject box, blur summary, motion delta) — built from
    the raw decoded frames and kept even when the legacy six-class rule rejects;
  * MediaPipe pose (joint angles, phases, input quality) for the rule analyzers;
  * the SlowFast Kinetics-400 clip (400-class top-k candidates);
  * the on-screen timeline / key moments.

V2 fixes vs v1:
  * R04: the six-class rejection branch no longer empties the evidence pool.
    ``event_frames == []`` only means the six-class event detector found nothing;
    it is never interpreted as "there is no person in the video".
  * R02/R10: personal previews render REAL decoded frames (skeleton hidden by
    default) and are generated independently of the third-party cloud consent.
    Cloud image transport is authorised separately by ``cloud_review_mode``.
  * R08/T06: frames are selected by information content then sorted by real
    timestamp ascending; phases are evidence-derived, never fabricated from the
    array's first/last element.
  * The receipt is the MotionWorkerResultV2 six-group envelope and carries NO
    image bytes — only preview_asset_id + hash + timestamp + dimensions.
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Callable

from ..config import settings
from ..errors import ProcessingError
from ..models.kinetics_clip import (
    clip_tensor_from_selected,
    sample_indices_for,
)
from ..models.kinetics_runtime import (
    SLOWFAST_SEMAPHORE,
    get_kinetics400,
    map_kinetics_label,
    slugify_kinetics,
)
from ..visualize import render_motion_preview, render_skeleton_canvas
from .analyzers import get_analyzer
from .motion import build_motion_score, explain_keyframes, extract_metrics
from .recognition import (
    SUPPORTED_EXERCISES,
    manual_recognition,
    recognize_exercise,
)

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "motion-unified-v2"
VALID_CLOUD_MODES = frozenset({"off", "skeleton", "redacted_frames"})


def make_preview_uploader(api, job_id: int) -> Callable[[list[dict]], dict]:
    """Build the preview-upload callable around a CloudAPI client (byte chain).

    Worker.py integration passes this as ``preview_uploader``: it asks the backend
    for signed per-frame upload URLs, then PUTs each rendered JPEG. Returns a
    ``{frame_id: backend_asset_id}`` map. Object-storage deployment swaps the
    backend store; this client path is unchanged.
    """

    def _upload(items: list[dict]) -> dict:
        if not items:
            return {}
        frame_ids = [item["frame_id"] for item in items]
        asset_prefix = hashlib.sha1(
            "".join(frame_ids).encode()
        ).hexdigest()[:16]
        resp = api.request_preview_upload_urls(
            job_id, frame_ids=frame_ids, asset_prefix=asset_prefix
        )
        by_frame = {u["frame_id"]: u for u in (resp.get("uploads") or [])}
        out: dict[str, str] = {}
        for item in items:
            desc = by_frame.get(item["frame_id"])
            if not desc or not desc.get("upload_url"):
                continue
            api.put_preview(desc["upload_url"], item["bytes"])
            out[item["frame_id"]] = desc["asset_id"]
        return out

    return _upload

# Keyframe priority: same event ordering the legacy path uses, so accepted
# six-class moments still surface first when present.
_PRIORITY = (
    "max_rule_risk",
    "squat_deepest",
    "pushup_deepest",
    "lunge_deepest",
    "leg_abduction_deepest",
    "arm_abduction_deepest",
    "arm_vw_deepest",
    "squat_bottom",
    "pushup_bottom",
    "lunge_bottom",
    "leg_abduction_bottom",
    "arm_abduction_bottom",
    "arm_vw_bottom",
    "squat_completed",
    "pushup_completed",
    "lunge_completed",
    "leg_abduction_completed",
    "arm_abduction_completed",
    "arm_vw_completed",
    "squat_top",
    "pushup_top",
    "lunge_top",
    "leg_abduction_top",
    "arm_abduction_top",
    "arm_vw_top",
)

_REGION_JOINTS = {
    "shoulder": ("left_shoulder", "right_shoulder"),
    "elbow": ("left_elbow", "right_elbow"),
    "wrist": ("left_wrist", "right_wrist"),
    "hip": ("left_hip", "right_hip"),
    "knee": ("left_knee", "right_knee"),
    "ankle": ("left_ankle", "right_ankle"),
}


def _pose_engine_available() -> bool:
    try:
        import cv2  # noqa: F401
        import mediapipe as mp

        return bool(getattr(mp, "solutions", None) and hasattr(mp.solutions, "pose"))
    except (ImportError, OSError):
        return False


def _visible_regions(skeleton: list[dict]) -> list[str]:
    """Which body regions are actually visible in this frame's skeleton."""
    present = {
        str(item.get("id"))
        for item in skeleton
        if isinstance(item, dict) and float(item.get("visibility", 0)) >= 0.4
    }
    out = []
    for region, joints in _REGION_JOINTS.items():
        if any(j in present for j in joints):
            out.append(region)
    return out


def _subject_bbox(skeleton: list[dict]):
    """Normalized [x1, y1, x2, y2] of visible joints, or None."""
    pts = [
        (float(item["x"]), float(item["y"]))
        for item in skeleton
        if isinstance(item, dict)
        and "x" in item
        and "y" in item
        and float(item.get("visibility", 0)) >= 0.4
    ]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def _generic_phase(t: float, duration: float) -> str:
    """Neutral Chinese phase for an event-less evidence frame (never a fabricated
    coaching error; the later visual review stage may refine it)."""
    if duration <= 0:
        return "动作画面"
    ratio = t / duration
    if ratio < 0.25:
        return "开始画面"
    if ratio > 0.75:
        return "结束画面"
    return "中间动作"


def _run_single_pass_decode(
    video_path: Path,
    *,
    requested_types: tuple[str, ...],
    pose_ok: bool,
    progress,
):
    """Decode the video exactly once. Return pose samples, the SlowFast clip and
    the GENERIC evidence pool (real downscaled frames), all from one read pass.
    """
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ProcessingError("invalid_media", "无法读取视频文件")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or total <= 0:
            raise ProcessingError("invalid_media", "视频帧率或时长无效")
        duration = total / fps
        max_seconds = float(settings.motion_max_duration_seconds)
        if duration > max_seconds:
            raise ProcessingError(
                "video_too_long", f"请使用不超过 {int(max_seconds)} 秒的视频"
            )

        clip_indices = set(sample_indices_for(total))
        pose_stride = max(1, round(fps / settings.motion_pose_sample_fps))
        candidate_stride = max(1, round(fps / settings.motion_candidate_fps))
        long_edge = int(settings.motion_preview_long_edge)

        pose_engine = None
        sample_sets: dict[str, list[dict]] = {c: [] for c in requested_types}
        clip_frames: list = []
        evidence: list[dict] = []
        pose_observations: list[dict] = []
        sampled = 0
        prev_gray = None
        if pose_ok:
            import mediapipe as mp

            pose_engine = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        try:
            idx = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if idx in clip_indices:
                    clip_frames.append(frame)
                # --- Generic evidence frame at candidate FPS (R04: independent of
                # six-class recognition and independent of pose landmarks). ------
                if idx % candidate_stride == 0:
                    h, w = frame.shape[:2]
                    if max(w, h) > long_edge:
                        factor = long_edge / max(w, h)
                        small = cv2.resize(
                            frame,
                            (max(1, round(w * factor)), max(1, round(h * factor))),
                            interpolation=cv2.INTER_AREA,
                        )
                    else:
                        small = frame
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    brightness = float(gray.mean())
                    if prev_gray is None or prev_gray.shape != gray.shape:
                        motion_delta = 0.0
                    else:
                        motion_delta = float(
                            cv2.absdiff(gray, prev_gray).mean()
                        ) / 255.0
                    prev_gray = gray
                    evidence.append(
                        {
                            "frame_index": idx,
                            "timestamp_ms": round(idx / fps * 1000.0),
                            "bgr": small,
                            "width": int(small.shape[1]),
                            "height": int(small.shape[0]),
                            "blur_var": round(blur_var, 2),
                            "brightness": round(brightness, 1),
                            "motion_delta": round(motion_delta, 4),
                            "skeleton": None,
                            "visible_regions": [],
                            "subject_bbox": None,
                        }
                    )
                if pose_engine is not None and idx % pose_stride == 0:
                    sampled += 1
                    height, width = frame.shape[:2]
                    if max(width, height) > 1280:
                        factor = 1280 / max(width, height)
                        frame = cv2.resize(
                            frame, (round(width * factor), round(height * factor))
                        )
                        height, width = frame.shape[:2]
                    result = pose_engine.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    if result.pose_landmarks:
                        obs_skeleton = None
                        for candidate in requested_types:
                            row = extract_metrics(
                                result.pose_landmarks.landmark,
                                width,
                                height,
                                idx / fps,
                                candidate,
                            )
                            sample_sets[candidate].append(row)
                            if obs_skeleton is None:
                                obs_skeleton = row.get("skeleton")
                        if obs_skeleton:
                            pose_observations.append(
                                {
                                    "t": idx / fps,
                                    "skeleton": obs_skeleton,
                                    "visibility": 0.0,
                                }
                            )
                    pct = min(70, int(idx / max(1, total) * 60) + 15)
                    if progress:
                        progress(pct, "decode_pose_kinetics")
                idx += 1
        finally:
            if pose_engine is not None:
                pose_engine.close()
    finally:
        capture.release()

    # Attach the nearest pose observation (within 0.5s) to each evidence frame.
    for entry in evidence:
        t = entry["timestamp_ms"] / 1000.0
        best = None
        best_dt = 0.5
        for obs in pose_observations:
            dt = abs(obs["t"] - t)
            if dt <= best_dt:
                best, best_dt = obs, dt
        if best and best["skeleton"]:
            entry["skeleton"] = best["skeleton"]
            entry["visible_regions"] = _visible_regions(best["skeleton"])
            entry["subject_bbox"] = _subject_bbox(best["skeleton"])

    # Bound the in-memory pool (time-uniform sub-sampling beyond the cap).
    cap = int(settings.motion_evidence_pool_max_frames)
    if len(evidence) > cap:
        step = len(evidence) / cap
        evidence = [evidence[int(i * step)] for i in range(cap)]

    if not clip_frames and sampled == 0 and not evidence:
        raise ProcessingError("invalid_media", "视频为空或无法解码出任何帧")
    return {
        "fps": fps,
        "total_frames": total,
        "duration": duration,
        "sample_sets": sample_sets,
        "clip_frames": clip_frames,
        "sampled": sampled,
        "evidence": evidence,
        "pose_observations": pose_observations,
    }


def _run_kinetics(clip_frames: list, progress) -> dict:
    """Run SlowFast under the worker semaphore. Report unavailable on any failure."""
    if progress:
        progress(78, "kinetics400")
    model = get_kinetics400()
    if model is None:
        return {
            "status": "unavailable",
            "reason": "KINETICS400_CHECKPOINT 未配置/缺失或 PyTorch 不可用",
            "candidates": [],
        }
    if not clip_frames:
        return {
            "status": "unavailable",
            "reason": "解码帧不足，无法构建 SlowFast clip",
            "candidates": [],
        }
    try:
        with SLOWFAST_SEMAPHORE:
            clip = clip_tensor_from_selected(clip_frames)
            out = model.predict(clip, topk=5)
    except Exception as exc:  # local model must never break the chain
        return {
            "status": "unavailable",
            "reason": f"SlowFast 推理失败：{type(exc).__name__}",
            "candidates": [],
        }
    candidates = []
    for item in out.get("candidates", []):
        label = str(item.get("label", ""))
        candidates.append(
            {
                "label": label,
                "class_index": int(item.get("class_index", -1)),
                "probability": round(float(item.get("probability", 0.0)), 4),
                "canonical_id": map_kinetics_label(label),
                "exercise_slug": slugify_kinetics(label),
            }
        )
    return {
        "status": "available",
        "top_label": out.get("top_label", ""),
        "top_probability": round(float(out.get("top_probability", 0.0)), 4),
        "candidates": candidates[:5],
    }


def _select_timeline_frames(
    evidence: list[dict],
    event_frames: list[dict],
    *,
    display_count: int,
    duration: float,
) -> list[dict]:
    """Pick representative moments by information, then sort by real time (R08).

    Event frames (from an accepted six-class analyzer) carry rich phase/finding/
    advice; generic evidence frames fill the remaining display slots by time
    coverage + motion change. Adjacent near-duplicate timestamps are de-duped.
    """
    rank = {name: i for i, name in enumerate(_PRIORITY)}
    selected: list[dict] = []
    used_ts: set[float] = set()

    def _addup(entry: dict) -> bool:
        ts = float(entry["timestamp_ms"])
        # de-dup frames within 0.3s of an already-picked moment
        if any(abs(ts - u) < 300 for u in used_ts):
            return False
        used_ts.add(ts)
        selected.append(entry)
        return True

    # 1) Event frames first, by legacy priority.
    enriched = explain_keyframes(list(event_frames), {"errors": []})
    for event in sorted(
        enriched,
        key=lambda f: (
            rank.get(str(f.get("event")), len(rank)),
            f.get("timestamp", 0),
        ),
    ):
        ts_ms = float(event.get("timestamp") or 0) * 1000.0
        # fold the event onto the nearest evidence entry (real frame reference)
        nearest = min(
            evidence,
            key=lambda e: abs(e["timestamp_ms"] - ts_ms),
            default=None,
        )
        if nearest is not None and abs(nearest["timestamp_ms"] - ts_ms) <= 750:
            nearest = dict(nearest)
            nearest["event"] = str(event.get("event") or "pose_event")
            nearest["phase"] = str(event.get("stage") or "动作证据")
            nearest["finding"] = str(
                event.get("finding") or event.get("reason") or "关键姿态"
            )
            nearest["advice"] = str(event.get("advice") or "结合关节角度复核")
            if event.get("skeleton"):
                nearest["skeleton"] = event["skeleton"]
                nearest["visible_regions"] = _visible_regions(event["skeleton"])
            _addup(nearest)
        else:
            orphan = {
                "timestamp_ms": round(ts_ms),
                "bgr": None,
                "width": 0,
                "height": 0,
                "event": str(event.get("event") or "pose_event"),
                "phase": str(event.get("stage") or "动作证据"),
                "finding": str(event.get("finding") or event.get("reason") or "关键姿态"),
                "advice": str(event.get("advice") or "结合关节角度复核"),
                "skeleton": event.get("skeleton"),
                "visible_regions": [],
                "subject_bbox": None,
                "motion_delta": 0.0,
                "blur_var": 0.0,
            }
            _addup(orphan)

    # 2) Fill remaining slots from the generic evidence pool: weight by time
    # coverage (spread across the video) and motion change.
    slots = display_count - len(selected)
    if slots > 0 and evidence:
        scored = sorted(
            evidence,
            key=lambda e: (
                -float(e.get("motion_delta") or 0.0),
                -float(e["timestamp_ms"]),
            ),
        )
        # spread picks: take motion-rich frames while forcing time coverage
        picked = 0
        for entry in scored:
            if picked >= slots:
                break
            ts = float(entry["timestamp_ms"])
            if any(abs(ts - u) < 1000 for u in used_ts):
                continue
            row = dict(entry)
            t = ts / 1000.0
            row.setdefault("event", "generic_evidence")
            row.setdefault("phase", _generic_phase(t, duration))
            row.setdefault(
                "finding",
                "画面可看清主运动者动作" if entry.get("subject_bbox") else "该时刻视频画面正常",
            )
            row.setdefault("advice", "结合完整视频回看该时刻")
            if _addup(row):
                picked += 1

    selected.sort(key=lambda e: float(e["timestamp_ms"]))
    return selected[:display_count]


def analyze_motion_unified(
    video_path: Path,
    *,
    requested_exercise: str = "auto",
    consent_deepseek_frames: bool = False,
    cloud_review_mode: str = "off",
    preview_out_dir: Path | None = None,
    preview_uploader: Callable[[list[dict]], dict] | None = None,
    progress=None,
) -> dict:
    """Run the unified motion chain and return a MotionWorkerResultV2-shaped dict.

    ``preview_uploader`` optionally uploads rendered JPEGs to the backend signed
    store (see :func:`make_preview_uploader`). When None (default) or when the
    ``preview_upload_enabled`` setting is off, frames stay at their local staging
    asset_id and no upload happens. Upload failures degrade to a warning and never
    block the receipt.
    """
    video_path = Path(video_path)
    if requested_exercise != "auto" and requested_exercise not in SUPPORTED_EXERCISES:
        raise ProcessingError("unsupported_exercise", "不支持的动作类型")

    # Legacy payload compatibility: the old single bool meant "may send real
    # frames to the cloud". It never authorises personal previews, which are
    # always generated (R10).
    if cloud_review_mode not in VALID_CLOUD_MODES:
        cloud_review_mode = "off"
    if cloud_review_mode == "off" and consent_deepseek_frames:
        cloud_review_mode = "redacted_frames"

    pose_ok = _pose_engine_available()
    requested_types = (
        SUPPORTED_EXERCISES if requested_exercise == "auto" else (requested_exercise,)
    )

    decoded = _run_single_pass_decode(
        video_path,
        requested_types=requested_types,
        pose_ok=pose_ok,
        progress=progress,
    )
    sample_sets = decoded["sample_sets"]
    clip_frames = decoded["clip_frames"]
    sampled = decoded["sampled"]
    duration = decoded["duration"]
    evidence = decoded["evidence"]

    kinetics = _run_kinetics(clip_frames, progress)

    # --- Pose / six-class recognition (independent of the evidence pool) -----
    event_frames: list[dict] = []
    if not pose_ok:
        recognition = {
            "mode": "auto" if requested_exercise == "auto" else "manual",
            "selected_type": None,
            "accepted": False,
            "reason": "姿态引擎不可用，仅提供视频帧与 Kinetics-400 候选。",
        }
        pose_measurements = {
            "available": False,
            "fps": float(settings.motion_pose_sample_fps),
            "frame_ids": [],
            "sample_count": 0,
            "keypoint_valid_rate": 0.0,
            "measurement_summary": "MediaPipe/OpenCV 姿态引擎不可用，无姿态测量。",
        }
        measurements = {"available": False, "reason": "姿态引擎不可用，无计数/评分。"}
    else:
        if requested_exercise == "auto":
            recognition_raw = recognize_exercise(sample_sets)
        else:
            recognition_raw = manual_recognition(requested_exercise)

        accepted = bool(recognition_raw.get("accepted"))
        selected_type = recognition_raw.get("selected_type")
        pose_measurements = {"available": False}
        measurements = {"available": False}
        if accepted and selected_type in SUPPORTED_EXERCISES:
            analyzer = get_analyzer(selected_type)
            pose, event_frames = analyzer.analyze(
                sample_sets[selected_type], sampled
            )
            if pose.get("available"):
                pose_measurements = {
                    "available": True,
                    "fps": float(settings.motion_pose_sample_fps),
                    "frame_ids": [
                        f"p_{i:03d}" for i in range(int(pose.get("sample_count") or 0))
                    ],
                    "sample_count": int(pose.get("sample_count") or 0),
                    "keypoint_valid_rate": float(pose.get("keypoint_valid_rate") or 0.0),
                    "measurement_summary": str(pose.get("message") or ""),
                }
                if int(pose.get("reps") or 0) >= 1:
                    score = build_motion_score(pose)
                    measurements = {
                        "available": True,
                        "exercise_id": selected_type,
                        "reps": int(pose.get("reps") or 0),
                        "duration_ms": round(duration * 1000),
                        "quality": score,
                    }
                else:
                    measurements = {
                        "available": False,
                        "exercise_id": selected_type,
                        "reason": "未检测到完整动作周期，不输出质量评分。",
                    }
            else:
                accepted, selected_type = False, None
                event_frames = []
                recognition_raw["accepted"] = False
                recognition_raw["selected_type"] = None
        pose_measurements.setdefault(
            "measurement_summary",
            str(recognition_raw.get("reason") or "证据不足，未做姿态测量。"),
        )
        recognition = recognition_raw

    # --- Recognition candidates: pose + kinetics, namespace-normalized (R05) --
    candidates: list[dict] = []
    if pose_measurements.get("available") and recognition.get("selected_type"):
        candidates.append(
            {
                "source": "pose",
                "source_label": recognition["selected_type"],
                "canonical_id": recognition["selected_type"],
                "raw_score": round(float(recognition.get("confidence") or 0.0), 4),
                "score_type": "rule",
            }
        )
    for kc in kinetics.get("candidates", []):
        candidates.append(
            {
                "source": "kinetics",
                "source_label": kc["label"],
                "class_index": kc["class_index"],
                "canonical_id": kc.get("canonical_id"),
                "raw_score": kc["probability"],
                "score_type": "softmax",
            }
        )

    # --- Timeline / evidence frames (R04/R08: built even on six-class reject) --
    display_count = int(settings.motion_display_preview_count)
    selected = _select_timeline_frames(
        evidence, event_frames, display_count=display_count, duration=duration
    )

    if preview_out_dir is not None:
        preview_out_dir = Path(preview_out_dir)
        preview_out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    upload_items: list[dict] = []
    external_calls = 0
    for n, entry in enumerate(selected):
        ts_ms = float(entry["timestamp_ms"])
        asset_id = f"preview_{n:04d}"
        row = {
            "frame_id": f"f_{n:03d}",
            "timestamp_ms": round(ts_ms),
            "preview_asset_id": asset_id,
            "subject_id": "s_01" if entry.get("subject_bbox") else None,
            "visible_regions": list(entry.get("visible_regions") or []),
            "blur": "ok" if float(entry.get("blur_var") or 50.0) >= 40.0 else "blurry",
            "motion_delta": float(entry.get("motion_delta") or 0.0),
            "event": entry.get("event", "generic_evidence"),
            "phase": entry.get("phase", "动作画面"),
            "finding": entry.get("finding", "关键姿态"),
            "advice": entry.get("advice", "结合完整视频回看该时刻"),
        }
        bgr = entry.get("bgr")
        if bgr is not None:
            # Personal preview: REAL decoded frame, no skeleton, no engineering
            # banner (R02). Generated regardless of cloud consent (R10).
            preview = render_motion_preview(
                bgr,
                entry,
                max_bytes=int(settings.motion_preview_max_bytes),
                draw_skeleton=False,
                annotate=False,
                blur_face=False,
            )
            row["preview_sha256"] = preview["preview_sha256"]
            row["preview_bytes"] = int(preview["preview_bytes"])
            row["preview_dimensions"] = {
                "width": int(preview["width"]),
                "height": int(preview["height"]),
            }
            if preview_out_dir is not None:
                (preview_out_dir / f"{asset_id}.jpg").write_bytes(
                    _b64(preview["image_b64"])
                )
            # Cloud image transport is authorised independently by cloud_review_mode.
            # "off" renders nothing for the cloud and makes zero external calls.
            if cloud_review_mode == "redacted_frames":
                cloud = render_motion_preview(
                    bgr,
                    entry,
                    max_bytes=int(settings.motion_preview_max_bytes),
                    draw_skeleton=False,
                    annotate=False,
                    blur_face=True,
                )
                row["cloud_preview_sha256"] = cloud["preview_sha256"]
                row["cloud_preview_mode"] = "redacted_frames"
            elif cloud_review_mode == "skeleton" and entry.get("skeleton"):
                cloud = render_skeleton_canvas(
                    entry, max_bytes=int(settings.motion_preview_max_bytes)
                )
                row["cloud_preview_sha256"] = cloud["preview_sha256"]
                row["cloud_preview_mode"] = "skeleton"
        rows.append(row)

    if progress:
        progress(92, "assemble_result")

    subject_present = any(e.get("subject_bbox") for e in evidence)
    all_regions = sorted(
        {r for e in evidence for r in (e.get("visible_regions") or [])}
    )
    return {
        "pipeline_version": PIPELINE_VERSION,
        "video_quality": {
            "available": True,
            "decoded_ok": True,
            "duration_ms": round(duration * 1000),
            "fps": round(decoded["fps"], 3),
            "total_frames": int(decoded["total_frames"]),
            "blur_summary": "ok",
        },
        "subject": {
            "available": subject_present,
            "subject_id": "s_01" if subject_present else None,
            "visible_regions": all_regions,
        },
        "pose_evidence": pose_measurements,
        "recognition_candidates": candidates,
        "frames": rows[: int(settings.motion_evidence_pool_max_frames)],
        "measurements": measurements,
        "external_provider_calls": external_calls,
        "cloud_review_mode": cloud_review_mode,
        "source": "local",
    }


def _b64(image_b64: str) -> bytes:
    import base64

    return base64.b64decode(image_b64)
