"""Unified motion chain (``motion_unified_v1``): one decode, fused evidence.

Per spec §3.2 / §4.2 / §10 P0-B: the video is downloaded and decoded EXACTLY
ONCE. The single sampled pass feeds:

  * MediaPipe pose (six-class rule matching, phases, joint angles, input quality),
  * the SlowFast Kinetics-400 clip (400-class top-k candidates, raw scores),
  * the timeline / key moments.

Degradation semantics:
  * Kinetics weights offline  -> ``kinetics.status == "unavailable"``, six-action
    pose path continues.
  * Pose engine unavailable    -> still emits Kinetics category candidates, but
    ``pose.available=False`` and no score.
  * Both unavailable           -> explicit non-retryable failure.
  * Empty video / no landmarks -> abstain and send NO visual content.

Cloud-vision keyframes are rendered as skeleton-on-solid-colour canvases
(never the original person frame) and pass a pixel-level desensitization check
before ``image_b64`` is attached. Whether any visual content leaves the worker at
all is gated by ``consent_deepseek_frames`` (default off).
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

from ..config import settings
from ..errors import ProcessingError
from ..models.kinetics_clip import (
    clip_tensor_from_selected,
    sample_indices_for,
)
from ..models.kinetics_runtime import (
    KINETICS_TO_EXERCISE,
    SLOWFAST_SEMAPHORE,
    get_kinetics400,
    slugify_kinetics,
)
from ..visualize import (
    assert_desensitized_canvas,
    render_skeleton_canvas,
)
from .analyzers import get_analyzer
from .motion import build_motion_score, explain_keyframes, extract_metrics
from .recognition import (
    SUPPORTED_EXERCISES,
    manual_recognition,
    recognize_exercise,
)

PIPELINE_VERSION = "motion-unified-v1"
MAX_PREVIEWS = 4
MAX_FRAMES = 200
PREVIEW_MAX_BYTES = 80 * 1024

# Keyframe priority: same event ordering the legacy path uses, so the unified
# timeline surfaces the most informative moments first.
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


def _pose_engine_available() -> bool:
    try:
        import cv2  # noqa: F401
        import mediapipe as mp

        return bool(getattr(mp, "solutions", None) and hasattr(mp.solutions, "pose"))
    except (ImportError, OSError):
        return False


def _run_single_pass_decode(
    video_path: Path,
    *,
    requested_types: tuple[str, ...],
    pose_ok: bool,
    progress,
):
    """Decode the video exactly once. Return pose samples + the SlowFast clip frames.

    This is the heart of "一次解码": ``cv2.VideoCapture`` is opened once and read
    sequentially. Frames that the SlowFast clip needs are collected on the fly
    (already resized/cropped for the model), and pose-stride frames are fed to
    MediaPipe in the same loop. No second ``VideoCapture`` / re-read happens.
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
        if duration > 120 or total > 30000:
            raise ProcessingError("video_too_long", "请使用不超过 120 秒的视频")

        clip_indices = set(sample_indices_for(total))
        stride = max(1, int(fps / 8))

        pose_engine = None
        sample_sets: dict[str, list[dict]] = {c: [] for c in requested_types}
        clip_frames: list = []
        sampled = 0
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
                if pose_engine is not None and idx % stride == 0:
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
                        for candidate in requested_types:
                            sample_sets[candidate].append(
                                extract_metrics(
                                    result.pose_landmarks.landmark,
                                    width,
                                    height,
                                    idx / fps,
                                    candidate,
                                )
                            )
                    pct = min(70, int(idx / max(1, total) * 60) + 15)
                    if progress:
                        progress(pct, "decode_pose_kinetics")
                idx += 1
                if idx > 30000:
                    raise ProcessingError("video_too_long", "视频实际帧数超过上限")
        finally:
            if pose_engine is not None:
                pose_engine.close()
    finally:
        capture.release()

    if not clip_frames and sampled == 0:
        raise ProcessingError("invalid_media", "视频为空或无法解码出任何帧")
    return {
        "fps": fps,
        "total_frames": total,
        "duration": duration,
        "sample_sets": sample_sets,
        "clip_frames": clip_frames,
        "sampled": sampled,
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
                "exercise_slug": slugify_kinetics(label),
                "mapped_exercise": KINETICS_TO_EXERCISE.get(label),
            }
        )
    return {
        "status": "available",
        "top_label": out.get("top_label", ""),
        "top_probability": round(float(out.get("top_probability", 0.0)), 4),
        "candidates": candidates[:5],
    }


def _build_keyframes(
    event_frames: list[dict],
    *,
    consent: bool,
) -> list[dict]:
    """Pick ≤4 key moments and render desensitized skeleton canvases when consent."""
    rank = {name: i for i, name in enumerate(_PRIORITY)}
    selected = sorted(
        event_frames,
        key=lambda f: (rank.get(str(f.get("event")), len(rank)), f.get("timestamp", 0)),
    )[:MAX_PREVIEWS]
    selected = explain_keyframes(list(selected), {"errors": []})

    out: list[dict] = []
    preview_attached = 0
    for event in selected:
        ts = float(event.get("timestamp") or 0)
        row = {
            "timestamp": round(ts, 3),
            "event": str(event.get("event") or "pose_event"),
            "phase": str(event.get("stage") or "动作证据"),
            "finding": str(event.get("finding") or event.get("reason") or "关键姿态"),
            "advice": str(event.get("advice") or "结合关节角度复核"),
            "evidence": (
                f"skeleton:{len(event.get('skeleton') or [])}pts@{ts:.1f}s "
                f"visibility={float(event.get('visibility') or 0):.2f}"
            ),
        }
        if consent and preview_attached < MAX_PREVIEWS and event.get("skeleton"):
            preview = render_skeleton_canvas(event, max_bytes=PREVIEW_MAX_BYTES)
            # Pixel-level desensitization gate before any visual byte leaves home.
            raw = base64.b64decode(preview["image_b64"])
            assert_desensitized_canvas(raw)
            row.update(preview)
            preview_attached += 1
        out.append(row)
    return out


def analyze_motion_unified(
    video_path: Path,
    *,
    requested_exercise: str = "auto",
    consent_deepseek_frames: bool = False,
    progress=None,
) -> dict:
    """Run the unified motion chain and return a MotionWorkerResultV1-shaped dict."""
    video_path = Path(video_path)
    if requested_exercise != "auto" and requested_exercise not in SUPPORTED_EXERCISES:
        raise ProcessingError("unsupported_exercise", "不支持的动作类型")

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

    kinetics = _run_kinetics(clip_frames, progress)

    # --- Pose / six-class recognition -------------------------------------
    if not pose_ok:
        if kinetics["status"] != "available":
            raise ProcessingError(
                "both_engines_unavailable",
                "姿态与 Kinetics 引擎均不可用，无法完成统一分析",
                retryable=False,
            )
        recognition = {
            "mode": "auto" if requested_exercise == "auto" else "manual",
            "requested_type": requested_exercise,
            "selected_type": None,
            "accepted": False,
            "confidence": 0.0,
            "margin": 0.0,
            "method": "kinetics400_candidates_only",
            "candidates": [],
            "reason": "姿态引擎不可用，仅提供 Kinetics-400 候选，未做六类评分。",
        }
        pose = {
            "engine": "mediapipe-pose",
            "exercise_type": None,
            "available": False,
            "sample_count": 0,
            "sampled_frames": sampled,
            "keypoint_valid_rate": 0.0,
            "measurement": "2D heuristic estimate",
            "message": "MediaPipe/OpenCV 姿态引擎不可用，未生成姿态评分。",
            "reps": 0,
            "errors": [],
        }
        score = {"available": False, "reason": "姿态引擎不可用，无评分。"}
        frames: list[dict] = []
        method = "single_decode + slowfast_kinetics400 (pose degraded)"
    else:
        if requested_exercise == "auto":
            recognition = recognize_exercise(sample_sets)
        else:
            recognition = manual_recognition(requested_exercise)

        accepted = bool(recognition.get("accepted"))
        selected_type = recognition.get("selected_type")
        event_frames: list[dict] = []
        if accepted and selected_type in SUPPORTED_EXERCISES:
            analyzer = get_analyzer(selected_type)
            pose, event_frames = analyzer.analyze(
                sample_sets[selected_type], sampled
            )
            if not pose.get("available"):
                # Insufficient landmarks even though rules fired: abstain scoring.
                recognition["accepted"] = False
                recognition["selected_type"] = None
                recognition["confidence"] = 0.0
                accepted, selected_type = False, None
        if not accepted or selected_type not in SUPPORTED_EXERCISES:
            # Contract: rejected results must not carry pose/score.
            visibility = 0.0
            pose = {
                "engine": "mediapipe-pose",
                "exercise_type": None,
                "available": False,
                "sample_count": max(
                    (len(rows) for rows in sample_sets.values()), default=0
                ),
                "sampled_frames": sampled,
                "keypoint_valid_rate": round(visibility, 3),
                "measurement": "2D heuristic estimate",
                "message": str(
                    recognition.get("reason") or "证据不足，未做姿态评分。"
                ),
                "reps": 0,
                "errors": [],
            }
            score = {
                "available": False,
                "reason": pose["message"],
            }
            event_frames = []
        else:
            if int(pose.get("reps") or 0) >= 1:
                score = build_motion_score(pose)
            else:
                score = {
                    "available": False,
                    "reason": "未检测到完整动作周期，不输出质量评分。",
                }
        frames = _build_keyframes(
            event_frames, consent=bool(consent_deepseek_frames)
        )
        method = (
            "single_decode + mediapipe_pose + rule_feature_matching_v1"
            + (" + slowfast_kinetics400_candidates" if kinetics["status"] == "available" else "")
        )

    if progress:
        progress(92, "assemble_result")

    return {
        "pipeline_version": PIPELINE_VERSION,
        "duration": round(duration, 2),
        "pose": pose,
        "score": score,
        "recognition": recognition,
        "kinetics": kinetics,
        "frames": frames[:MAX_FRAMES],
        "motion": {
            "rhythm": (
                "已输出六类识别与候选；质量评分仅在完整周期时给出。"
                if pose.get("available")
                else "动作证据不足或姿态引擎降级，未进行次数/节奏评价。"
            ),
        },
        "method": method,
        "source": "local",
    }
