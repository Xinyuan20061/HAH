from __future__ import annotations
import math
from pathlib import Path
from .analyzers import angle, get_analyzer
from .recognition import (
    SUPPORTED_EXERCISES,
    manual_recognition,
    recognize_exercise,
)
from .motion_review import review_exercise, explain_frames_deepseek
from ..models.semantic_runtime import infer_motion_semantics
from ..models.kinetics_runtime import recognize_video_kinetics400
from ..config import settings
from ..errors import ProcessingError
from ..visualize import annotate_keyframes


SEVERITY_WEIGHT = {"low": 6, "medium": 14, "high": 24}


def build_motion_score(pose: dict) -> dict:
    """Turn pose evidence into an explainable 0-100 coaching score.

    The score deliberately uses only measured 2-D evidence. It is a training aid,
    not a diagnosis or a claim about joint loading.
    """
    if not pose.get("available"):
        return {
            "available": False,
            "reason": pose.get("message", "关键点证据不足，无法评分"),
        }
    cycles = pose.get("cycles") or []
    errors = pose.get("errors") or []
    samples = pose.get("samples") or []
    thresholds = get_analyzer(pose.get("exercise_type", "squat")).thresholds
    primary = {
        "pushup": "elbow",
        "leg_abduction": "leg_abduction",
        "arm_abduction": "arm_abduction",
        "arm_vw": "arm_vw",
    }.get(pose.get("exercise_type"), "knee")
    primary_min = (pose.get("angles") or {}).get(primary + "_min", 180)
    depth_gap = max(0.0, float(primary_min) - thresholds.depth)
    depth_score = max(35.0, 100.0 - depth_gap * 1.5)
    completion = depth_score if cycles else min(depth_score, 45.0)

    visibility = float(pose.get("visibility_mean") or 0)
    if samples:
        trunk_values = [float(x.get("trunk") or 0) for x in samples]
        trunk_mean = sum(trunk_values) / len(trunk_values)
        trunk_variance = sum((x - trunk_mean) ** 2 for x in trunk_values) / len(
            trunk_values
        )
        trunk_std = trunk_variance**0.5
    else:
        trunk_std = 20.0
    stability = max(30.0, min(100.0, visibility * 100 - trunk_std * 1.2))

    periods = [
        float(x["duration"]) for x in cycles if float(x.get("duration") or 0) > 0
    ]
    if len(periods) >= 2:
        mean = sum(periods) / len(periods)
        cv = (sum((x - mean) ** 2 for x in periods) / len(periods)) ** 0.5 / mean
        rhythm_control = max(35.0, 100.0 - cv * 120)
        rhythm_evidence = f"{len(periods)} 个完整周期，周期变异系数 {cv:.2f}"
    else:
        rhythm_control = 65.0
        rhythm_evidence = "完整周期少于 2 个，节奏分采用保守基准值"

    risk_index = min(
        100.0,
        sum(SEVERITY_WEIGHT.get(str(x.get("severity")), 10) for x in errors),
    )
    overall = (
        completion * 0.38
        + stability * 0.28
        + rhythm_control * 0.24
        + (100 - risk_index) * 0.10
    )
    confidence = min(1.0, visibility * min(1.0, len(samples) / 12))
    return {
        "available": True,
        "completeness": round(completion),
        "stability": round(stability),
        "rhythm_control": round(rhythm_control),
        "risk_index": round(risk_index),
        "overall": round(overall),
        "confidence": round(confidence, 3),
        "basis": [
            f"动作深度：最小{primary}角 {float(primary_min):.1f}°，参考阈值 {thresholds.depth:.0f}°",
            f"稳定性：关键点平均可见度 {visibility:.0%}，躯干角波动 {trunk_std:.1f}°",
            f"节奏：{rhythm_evidence}",
            f"风险提示：命中 {len(errors)} 条二维规则",
        ],
        "disclaimer": "评分来自单机位二维姿态估算，仅用于一般训练反馈，不作为医学或损伤诊断。",
    }


def explain_keyframes(frames: list[dict], pose: dict) -> list[dict]:
    errors = pose.get("errors") or []
    first_error = errors[0] if errors else None
    for frame in frames:
        event = str(frame.get("event", ""))
        if event.endswith("_top"):
            frame.update(
                stage="开始/伸展阶段",
                finding="身体处于伸展位",
                advice="保持核心收紧，准备平稳下放",
            )
        elif event.endswith("_bottom") or event.endswith("_deepest"):
            frame.update(
                stage="最低点",
                finding="已到达本次动作最低位置",
                advice="控制方向与节奏，避免借助惯性",
            )
        elif event.endswith("_completed"):
            frame.update(
                stage="恢复阶段",
                finding="完成一次伸展回程",
                advice="保持呼吸与动作速度稳定",
            )
        elif event == "max_rule_risk" and first_error:
            frame.update(
                stage="重点纠正时刻",
                finding=first_error.get("label", "规则偏差较大"),
                risk=first_error.get("evidence", "请结合画面复核"),
                advice={
                    "depth_insufficient": "减小速度，在活动度允许范围内逐步增加下放深度",
                    "trunk_lean": "降低负荷并保持核心稳定，避免躯干过度前倾",
                    "body_alignment": "保持肩、髋、踝尽量在同一直线上",
                    "back_leg_depth": "缩短步幅并控制后膝下放，保持躯干稳定",
                }.get(first_error.get("code"), "降低速度并对照标准动作重新练习"),
                severity=first_error.get("severity", "medium"),
            )
        else:
            frame.update(
                stage="动作证据",
                finding=frame.get("reason", "关键姿态"),
                advice="结合关节角度与完整视频复核",
            )
    return frames


def extract_metrics(
    landmarks, width: int, height: int, timestamp: float, exercise_type: str
):
    def point(index):
        item = landmarks[index]
        # Pixel aspect ratio matters: normalized x/y must not be treated as equal units.
        return (item.x * width, item.y * height, item.visibility)

    skeleton = [
        {
            "id": name,
            "x": round(float(landmarks[index].x), 4),
            "y": round(float(landmarks[index].y), 4),
            "visibility": round(float(landmarks[index].visibility), 3),
        }
        for name, index in [
            ("left_shoulder", 11),
            ("right_shoulder", 12),
            ("left_elbow", 13),
            ("right_elbow", 14),
            ("left_wrist", 15),
            ("right_wrist", 16),
            ("left_hip", 23),
            ("right_hip", 24),
            ("left_knee", 25),
            ("right_knee", 26),
            ("left_ankle", 27),
            ("right_ankle", 28),
        ]
    ]

    sides = []
    for side, indices in [
        ("left", (11, 13, 15, 23, 25, 27)),
        ("right", (12, 14, 16, 24, 26, 28)),
    ]:
        shoulder, elbow, wrist, hip, knee, ankle = [point(i) for i in indices]
        needed = (
            [shoulder, elbow, wrist, hip, ankle]
            if exercise_type in {"pushup", "arm_abduction", "arm_vw"}
            else [shoulder, hip, knee, ankle]
        )
        visibility = min(p[2] for p in needed)
        length = math.hypot(ankle[0] - shoulder[0], ankle[1] - shoulder[1])
        offset = (
            (hip[0] - shoulder[0]) * (ankle[1] - shoulder[1])
            - (hip[1] - shoulder[1]) * (ankle[0] - shoulder[0])
        ) / max(length * length, 1e-8)
        row = {
            "t": round(timestamp, 3),
            "side": side,
            "visibility": visibility,
            "knee": angle(hip, knee, ankle),
            "hip": angle(shoulder, hip, ankle if exercise_type == "pushup" else knee),
            "elbow": angle(shoulder, elbow, wrist),
            "body_line": angle(shoulder, hip, ankle),
            "body_offset": offset,
            "trunk": abs(
                math.degrees(math.atan2(shoulder[0] - hip[0], -(shoulder[1] - hip[1])))
            ),
            "shin_verticality": abs(knee[0] - ankle[0])
            / max(math.hypot(knee[0] - ankle[0], knee[1] - ankle[1]), 1e-8),
            "skeleton": skeleton,
        }
        sides.append(row)
    left_shoulder, left_elbow, left_wrist = point(11), point(13), point(15)
    right_shoulder, right_elbow, right_wrist = point(12), point(14), point(16)
    left_hip, left_knee = point(23), point(25)
    right_hip, right_knee = point(24), point(26)
    mid_hip = (
        (left_hip[0] + right_hip[0]) / 2,
        (left_hip[1] + right_hip[1]) / 2,
    )
    shoulder_angles = [
        angle(left_elbow, left_shoulder, left_hip),
        angle(right_elbow, right_shoulder, right_hip),
    ]
    elbow_angles = [
        angle(left_shoulder, left_elbow, left_wrist),
        angle(right_shoulder, right_elbow, right_wrist),
    ]
    leg_spread = angle(left_knee, mid_hip, right_knee)
    common_motion = {
        # These transformed values begin near 180 and decrease at the active
        # phase, matching the analyzer's existing extension/down hysteresis.
        "leg_abduction": 180 - leg_spread if leg_spread is not None else None,
        "arm_abduction": 180 - sum(shoulder_angles) / 2
        if all(value is not None for value in shoulder_angles)
        else None,
        "arm_vw": sum(elbow_angles) / 2
        if all(value is not None for value in elbow_angles)
        else None,
    }
    for row in sides:
        row.update(common_motion)
    if exercise_type == "lunge" and all(row["visibility"] >= 0.5 for row in sides):
        # Side-view heuristic: the front shin is closer to vertical. Report selected anatomical side.
        chosen = min(sides, key=lambda x: x["shin_verticality"])
    else:
        chosen = max(sides, key=lambda x: x["visibility"])
    back = next(row for row in sides if row is not chosen)
    chosen.update(back_knee=back["knee"], back_visibility=back["visibility"])
    return chosen


def analyze_motion(
    video_path: Path,
    exercise_type: str = "squat",
    progress=None,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
) -> dict:
    if exercise_type not in {*SUPPORTED_EXERCISES, "auto"}:
        raise ProcessingError("unsupported_exercise", "不支持的动作类型")
    try:
        import cv2
        import mediapipe as mp

        if not hasattr(mp, "solutions"):
            raise ImportError("MediaPipe solutions absent")
    except (ImportError, OSError):
        raise ProcessingError(
            "pose_unavailable", "MediaPipe/OpenCV 不可用，请运行 doctor.py"
        ) from None
    capture = cv2.VideoCapture(str(video_path))
    pose_engine = None
    try:
        if not capture.isOpened():
            raise ProcessingError("invalid_media", "无法读取视频文件")
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ProcessingError("invalid_media", "视频帧率或时长无效")
        full_duration = count / fps
        start_seconds = max(0.0, float(start_seconds or 0))
        end_seconds = full_duration if end_seconds is None else min(full_duration, float(end_seconds))
        if end_seconds <= start_seconds:
            raise ProcessingError("invalid_media", "动作片段时间范围无效")
        start_frame = max(0, round(start_seconds * fps))
        end_frame = min(count, round(end_seconds * fps))
        segment_count = end_frame - start_frame
        duration = segment_count / fps
        if duration > 120 or segment_count > 30000:
            raise ProcessingError("video_too_long", "请使用不超过 120 秒的视频")
        if start_frame:
            capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        stride = max(1, int(fps / 8))
        pose_engine = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        index, sampled, last_progress = start_frame, 0, -1
        requested_types = (
            SUPPORTED_EXERCISES if exercise_type == "auto" else (exercise_type,)
        )
        sample_sets = {candidate: [] for candidate in requested_types}
        while capture.isOpened():
            if index >= end_frame:
                break
            ok, frame = capture.read()
            if not ok:
                break
            local_index = index - start_frame
            if local_index % stride == 0:
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
                                local_index / fps,
                                candidate,
                            )
                        )
                percentage = min(85, int(local_index / segment_count * 65) + 20)
                if progress and percentage >= last_progress + 5:
                    progress(percentage, "pose_inference")
                    last_progress = percentage
            index += 1
            if index - start_frame > 30000:
                raise ProcessingError("video_too_long", "视频实际帧数超过上限")
    finally:
        capture.release()
        if pose_engine is not None:
            pose_engine.close()
    recognition = (
        recognize_exercise(sample_sets)
        if exercise_type == "auto"
        else manual_recognition(exercise_type)
    )
    compositional_semantics = infer_motion_semantics(sample_sets)

    # Local SlowFast Kinetics-400 second opinion. In auto mode the 400-class
    # model always runs (~0.8s CPU) and its candidates are attached to the
    # result for review. Overriding the rule pick (confirm / correct / rescue)
    # is gated behind KINETICS400_OVERRIDE_ENABLED, which stays False until the
    # same-set evaluation registers the model as active (plan §3.3). With the
    # gate closed the rule result is never rewritten. No cloud call.
    kinetics = None
    if exercise_type == "auto":
        if progress:
            progress(88, "kinetics400")
        try:
            kinetics = recognize_video_kinetics400(video_path)
        except Exception as exc:  # local model must never break the chain
            recognition["kinetics_note"] = f"本地 Kinetics-400 推理失败，已跳过：{exc}"
        if kinetics:
            recognition["kinetics400"] = kinetics
            if not settings.kinetics400_override_enabled:
                recognition["kinetics_note"] = (
                    "Kinetics-400 仅作为候选层记录，未启用规则覆盖（评测门禁未通过）"
                )
            else:
                k_prob = float(kinetics.get("top_probability", 0))
                k_label = kinetics.get("top_label", "")
                mapped = kinetics.get("mapped_exercise")
                k_selected = mapped or kinetics.get("exercise_slug")
                rule_selected = recognition.get("selected_type")
                rule_accepted = bool(recognition.get("accepted"))
                strong = k_prob >= settings.kinetics400_strong_confidence
                usable = k_prob >= settings.kinetics400_min_confidence
                if usable and rule_accepted and mapped == rule_selected:
                    # Both agree on an analyzer-backed exercise: confirm + boost.
                    recognition["method"] = (
                        f"{recognition.get('method')} + slowfast_kinetics400_confirm"
                    )
                    recognition["confidence"] = round(
                        max(float(recognition.get("confidence") or 0), k_prob), 3
                    )
                    recognition["reason"] = (
                        f"规则与 SlowFast Kinetics-400 均判定为 {k_label}"
                        f"（{k_prob:.0%}），结论一致。"
                    )
                elif strong and (not rule_accepted or k_selected != rule_selected):
                    # Kinetics strongly disagrees, or the rules abstained: trust it.
                    recognition["accepted"] = True
                    recognition["selected_type"] = k_selected
                    recognition["rescued_by"] = "slowfast_kinetics400"
                    recognition["confidence"] = round(k_prob, 3)
                    recognition["method"] = "rule_feature_matching_v1 + slowfast_kinetics400"
                    lead = (
                        "本地规则未确认，"
                        if not rule_accepted
                        else f"本地规则判定为 {rule_selected}，但 "
                    )
                    tail = (
                        f"已映射到 {mapped} 专属评估。"
                        if mapped in SUPPORTED_EXERCISES
                        else "当前无专属评分器，仅识别动作类型。"
                    )
                    recognition["reason"] = (
                        f"{lead}SlowFast Kinetics-400 识别为 {k_label}"
                        f"（{k_prob:.0%}），{tail}"
                    )
                # else Kinetics weak/uncertain: keep the rule pick unchanged.

    # DeepSeek vision review. Skip the cloud call when a local model already
    # accepted the movement (local-first, saves cost); it stays as the final
    # fallback for movements neither the rules nor Kinetics could confirm.
    local_high_conf = bool(recognition.get("accepted")) and float(
        recognition.get("confidence") or 0
    ) >= settings.kinetics400_min_confidence
    if local_high_conf:
        review, review_error = None, None
    else:
        review, review_error = review_exercise(
            video_path,
            sample_sets,
            local_pick=recognition.get("selected_type"),
            local_accepted=bool(recognition.get("accepted")),
            local_reason=str(recognition.get("reason") or ""),
        )
    if review_error:
        recognition["review_note"] = review_error
    if review:
        recognition["review"] = review
        chosen = str(review.get("chosen_type") or "").strip().lower().replace("-", "_")
        if not recognition.get("accepted") and chosen:
            # Local rules abstained; the vision model identified a movement.
            recognition["accepted"] = True
            recognition["selected_type"] = chosen
            recognition["rescued_by"] = "deepseek_vision"
            recognition["confidence"] = round(review.get("confidence") or 0, 3)
            recognition["method"] = (
                "rule_feature_matching_v1 + deepseek_vision_fallback"
            )
            recognition["reason"] = (
                f"本地规则未识别，DeepSeek 视觉兜底判定为 {chosen}（置信 "
                f"{review.get('confidence'):.0%}）：{review.get('reason') or ''}"
            )
        elif recognition.get("accepted") and chosen in SUPPORTED_EXERCISES:
            if chosen != recognition.get("selected_type"):
                recognition["selected_type"] = chosen
                recognition["method"] = (
                    str(recognition.get("method") or "") + "+ deepseek_vision_review"
                )
                recognition["reason"] = (
                    f"DeepSeek 视觉复查修正为 {chosen}：{review.get('reason') or ''}"
                )
            else:
                # Vision model confirmed the local pick: still mark the review.
                review["confirmed"] = True
                recognition["method"] = (
                    str(recognition.get("method") or "") + "+ deepseek_vision_review"
                )
                recognition["reason"] = (
                    f"DeepSeek 视觉复查确认为 {chosen}（置信 "
                    f"{review.get('confidence'):.0%}）：{review.get('reason') or ''}"
                )
    selected_type = recognition.get("selected_type")
    if not recognition.get("accepted") or selected_type not in SUPPORTED_EXERCISES:
        if selected_type not in SUPPORTED_EXERCISES and recognition.get("accepted"):
            # Vision-rescued movement without a dedicated analyzer: still report
            # the recognized type with basic pose evidence, no per-movement score.
            rescued_by = recognition.get("rescued_by", "deepseek_vision")
            recognizer_cn = (
                "SlowFast Kinetics-400 本地模型"
                if rescued_by == "slowfast_kinetics400"
                else "DeepSeek 视觉模型"
            )
            method_full = (
                "mediapipe_pose + slowfast_kinetics400"
                if rescued_by == "slowfast_kinetics400"
                else "mediapipe_pose + deepseek_vision_fallback"
            )
            visibility = float(
                max(
                    (
                        sum(
                            float(row.get("visibility") or 0) for row in rows
                        )
                        / max(1, len(rows))
                        for rows in sample_sets.values()
                        if rows
                    ),
                    default=0.0,
                )
            )
            pose = {
                "engine": "mediapipe-pose",
                "exercise_type": selected_type,
                "available": True,
                "sample_count": max(
                    (len(rows) for rows in sample_sets.values()), default=0
                ),
                "sampled_frames": sampled,
                "keypoint_valid_rate": round(visibility, 3),
                "measurement": "2D heuristic estimate",
                "message": f"已识别为 {selected_type}（{recognizer_cn}），当前版本暂不提供该动作的专业评分。",
                "errors": [],
            }
            return {
                "duration": round(duration, 2),
                "frame_count": 0,
                "frames": [],
                "pose": pose,
                "score": {
                    "available": False,
                    "reason": f"{selected_type} 暂无专属评估器，已识别动作类型并给出姿态反馈。",
                },
                "recognition": recognition,
                "compositional_semantics": compositional_semantics,
                "motion": {
                    "rhythm": f"已识别动作：{selected_type}；专业次数与节奏评估暂未开放。",
                    "period_mean_seconds": 0,
                    "hint": f"该动作由{recognizer_cn}识别；可在支持列表中手动选择以获得完整评估。",
                },
                "method": method_full,
                "source": "local",
            }
        message = (
            recognition.get("reason") or "无法可靠识别动作，请手动选择动作后重试。"
        )
        pose = {
            "engine": "mediapipe-pose",
            "exercise_type": None,
            "available": False,
            "sample_count": max(
                (len(rows) for rows in sample_sets.values()), default=0
            ),
            "sampled_frames": sampled,
            "keypoint_valid_rate": 0.0,
            "measurement": "2D heuristic estimate",
            "message": message,
            "errors": [],
        }
        return {
            "duration": round(duration, 2),
            "frame_count": 0,
            "frames": [],
            "pose": pose,
            "score": {"available": False, "reason": message},
            "recognition": recognition,
            "compositional_semantics": compositional_semantics,
            "motion": {
                "rhythm": "动作类型未确认，未进行次数与节奏评价",
                "period_mean_seconds": 0,
                "hint": "系统在证据不足或候选接近时会拒绝猜测，可手动选择动作后重试。",
            },
            "method": "mediapipe_pose + rule_feature_matching_v1 + abstention",
            "source": "local",
        }

    analyzer = get_analyzer(selected_type)
    pose, frames = analyzer.analyze(sample_sets[selected_type], sampled)
    periods = [cycle["duration"] for cycle in pose.get("cycles", [])]
    mean = sum(periods) / len(periods) if periods else 0
    coefficient = (
        ((sum((period - mean) ** 2 for period in periods) / len(periods)) ** 0.5 / mean)
        if mean > 0
        else None
    )
    rhythm = (
        "完整动作周期不足，无法评价节奏"
        if len(periods) < 2
        else ("动作周期较稳定" if coefficient < 0.25 else "动作周期波动较大")
    )
    frames = explain_keyframes(frames, pose)
    frames = annotate_keyframes(video_path, frames, max_frames=4)
    frames = [dict(frame, index=index) for index, frame in enumerate(frames)]
    frame_explanations = explain_frames_deepseek(frames, selected_type)
    if frame_explanations:
        for frame in frames:
            index = frame.get("index")
            if index in frame_explanations:
                frame["explanation"] = frame_explanations[index]
    score = build_motion_score(pose)
    return {
        "duration": round(duration, 2),
        "frame_count": len(frames),
        "frames": frames,
        "pose": pose,
        "score": score,
        "recognition": recognition,
        "compositional_semantics": compositional_semantics,
        "motion": {
            "rhythm": rhythm,
            "period_mean_seconds": round(mean, 3),
            "hint": "关键事件帧已叠加匿名骨骼证据；人脸区域会在本机生成预览时自动模糊。",
        },
        "method": (
            "mediapipe_pose + rule_feature_matching_v1 + exercise_analyzer + event_keyframes"
            if exercise_type == "auto"
            else "mediapipe_pose + user_selected_exercise + exercise_analyzer + event_keyframes"
        ),
        "source": "local",
    }
