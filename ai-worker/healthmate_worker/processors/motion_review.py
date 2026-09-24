"""DeepSeek vision review / fallback for exercise recognition.

Local rule matching is cheap and fast but limited to six movements. This module
sends ANONYMIZED stick-figure frames plus a numeric skeleton summary to the
configured vision model (deepseek-flash) to either confirm the local pick or
identify a movement the local rules cannot. It never uploads raw person frames:
faces are blurred and the review image is a skeleton overlay, so the review is
privacy-safe and still readable by the vision model.

Abstention survives: if the model returns unknown / low confidence, the caller
keeps the local abstention instead of inventing a label.
"""

from __future__ import annotations

import base64
import json
import math
from statistics import median

import httpx

from ..config import settings
from ..errors import ProcessingError
from .food import provider_config, preprocess_image
from ..visualize import LINKS


def select_review_rows(sample_sets: dict[str, list[dict]], count: int = 4) -> list[dict]:
    """Merge per-candidate skeleton rows, sort by time, pick `count` evenly."""
    merged = []
    for candidate, rows in sample_sets.items():
        for row in rows:
            if isinstance(row, dict) and float(row.get("visibility") or 0) >= 0.5:
                merged.append({"candidate": candidate, **row})
    if not merged:
        return []
    merged.sort(key=lambda row: float(row.get("t") or 0))
    if len(merged) <= count:
        return merged
    step = (len(merged) - 1) / max(1, count - 1)
    return [merged[round(i * step)] for i in range(count)]


def render_stick_frames(video_path, rows: list[dict]) -> list[str]:
    """Re-open the video at each row timestamp and render a skeleton-only frame.

    The person is NOT shown: only coloured skeleton lines on the original frame
    with the face region blurred, so the vision model sees geometry, not identity.
    """
    if not rows:
        return []
    import cv2

    frames: list[str] = []
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    try:
        for row in rows:
            timestamp = float(row.get("t") or 0)
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if max(width, height) > 1280:
                factor = 1280 / max(width, height)
                frame = cv2.resize(
                    frame, (round(width * factor), round(height * factor))
                )
                height, width = frame.shape[:2]
            skeleton = row.get("skeleton") or []
            points = {
                str(item.get("id")): (
                    int(float(item.get("x", 0)) * width),
                    int(float(item.get("y", 0)) * height),
                )
                for item in skeleton
                if isinstance(item, dict)
                and float(item.get("visibility", 0)) >= 0.25
            }
            # Face blur via shoulder band (same logic as visualize._blur_face).
            left, right = points.get("left_shoulder"), points.get("right_shoulder")
            if left and right:
                shoulder_width = max(24, abs(right[0] - left[0]))
                center_x = (left[0] + right[0]) // 2
                shoulder_y = (left[1] + right[1]) // 2
                half_width = int(shoulder_width * 0.48)
                x1, x2 = max(0, center_x - half_width), min(
                    width, center_x + half_width
                )
                y2 = max(1, shoulder_y - int(shoulder_width * 0.12))
                y1 = max(0, y2 - int(shoulder_width * 1.05))
                if x2 - x1 >= 10 and y2 - y1 >= 10:
                    region = frame[y1:y2, x1:x2]
                    kernel = max(15, (min(region.shape[:2]) // 3) | 1)
                    frame[y1:y2, x1:x2] = cv2.GaussianBlur(
                        region, (kernel, kernel), 0
                    )
            for start, end in LINKS:
                if start in points and end in points:
                    cv2.line(
                        frame,
                        points[start],
                        points[end],
                        (93, 238, 183),
                        4,
                        cv2.LINE_AA,
                    )
            for name, point in points.items():
                focus = any(token in name for token in ("knee", "hip", "elbow"))
                color = (63, 116, 255) if focus else (244, 249, 247)
                cv2.circle(frame, point, 7 if focus else 5, color, -1, cv2.LINE_AA)
            encoded = None
            for _ in range(5):
                for quality in (82, 68, 54, 42):
                    ok, buffer = cv2.imencode(
                        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
                    )
                    if ok and len(buffer) <= 512 * 1024:
                        encoded = buffer
                        break
                if encoded is not None:
                    break
                frame = cv2.resize(
                    frame, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA
                )
            if encoded is not None:
                frames.append(base64.b64encode(encoded.tobytes()).decode("ascii"))
    finally:
        capture.release()
    return frames


def skeleton_summary(sample_sets: dict[str, list[dict]]) -> str:
    """Numeric angle profile per candidate, so the model sees motion geometry."""

    def values(rows: list[dict], key: str) -> list[float]:
        out = []
        for row in rows:
            value = row.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                out.append(float(value))
        return out

    def describe(rows: list[dict], name: str):
        if not rows:
            return f"{name}: 无有效样本"
        lines = []
        for key, label in (
            ("knee", "膝角"),
            ("elbow", "肘角"),
            ("hip", "髋角"),
            ("trunk", "躯干倾角"),
            ("body_line", "身体直线角"),
        ):
            vals = values(rows, key)
            if not vals:
                continue
            lines.append(
                f"{label} 中位 {median(vals):.1f}° 范围 {max(vals) - min(vals):.1f}°"
            )
        return f"{name}: " + "；".join(lines)

    parts = []
    for candidate in sample_sets:
        parts.append(describe(sample_sets[candidate], candidate))
    return "\n".join(parts)


MOTION_REVIEW_PROMPT = """你是谨慎的健身动作识别复核器。下面提供一段健身视频的 2-4 张匿名骨架关键帧（绿色连线+蓝色关节=膝/髋/肘）和本地规则引擎的骨架数值摘要。

请判断这段视频最可能进行的健身动作。要求：
- 返回 JSON，不要 Markdown。字段：exercise_type（英文小写下划线；无法判断时必须是 "unknown"）、confidence（0~1）、reason（一句中文判断依据）、suggested_fix（给用户的改进建议，中文，可为空字符串）。
- 本地引擎候选（供参考，不必拘泥）：squat / pushup / lunge / leg_abduction / arm_abduction / arm_vw。
- 常见居家动作（可从中判断）：bicep_curl 哑铃弯举、deadlift 硬拉、shoulder_press 肩推、lateral_raise 侧平举、leg_raise 抬腿、side_plank 侧平板、plank 平板支撑、situp 卷腹、crunch 卷腹、pullup 引体向上、row 划船、chest_press 卧推、hip_thrust 臀桥、calf_raise 提踵、mountain_climber 登山跑、jumping_jack 开合跳、burpee 波比跳、high_knees 高抬腿。
- 只要画面中有人、骨架和角度证据足够，就给出最可能的动作并给出中等以上置信度；证据不足才 unknown。
- 只有画面中确有人且骨架/角度证据足够时才给出具体动作；证据不足或画面无人时 exercise_type 必须为 "unknown" 并降低 confidence。
- 这是动作复核/兜底识别，不是医疗诊断。"""


def _request_review(
    frame_b64_list: list[str], summary: str, prompt_suffix: str
) -> dict:
    config = provider_config("deepseek")
    if not config["model"]:
        raise ProcessingError("vlm_model_missing", "请先配置 DEEPSEEK_VISION_MODEL")
    if not config["api_key"]:
        raise ProcessingError("vlm_auth", "请先配置 DEEPSEEK_API_KEY")
    content: list[dict] = [
        {
            "type": "text",
            "text": MOTION_REVIEW_PROMPT + prompt_suffix + "\n\n骨架数值摘要：\n" + summary,
        }
    ]
    for frame_b64 in frame_b64_list:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + frame_b64,
                },
            }
        )
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    if config["provider"] == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    headers = (
        {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
    )
    try:
        with httpx.Client(
            timeout=settings.local_vlm_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                config["base_url"] + "/chat/completions",
                headers=headers,
                json=payload,
            )
        if not response.is_success:
            status = response.status_code
            raise ProcessingError(
                "vlm_http",
                f"动作复核视觉模型 HTTP {status}",
                status in {408, 429} or status >= 500,
            )
        body = response.json()
        content_text = body["choices"][0]["message"]["content"]
        if isinstance(content_text, list):
            content_text = "".join(
                x.get("text", "") for x in content_text if isinstance(x, dict)
            )
        value = json.loads(content_text)
        if not isinstance(value, dict):
            raise ProcessingError("vlm_invalid_json", "动作复核 JSON 无效")
        exercise_type = str(value.get("exercise_type") or "").strip().lower()
        return {
            "exercise_type": exercise_type,
            "confidence": max(0.0, min(1.0, float(value.get("confidence") or 0))),
            "reason": str(value.get("reason") or "")[:200],
            "suggested_fix": str(value.get("suggested_fix") or "")[:200],
            "provider": "deepseek",
            "model": config["model"],
        }
    except httpx.TimeoutException:
        raise ProcessingError("vlm_timeout", "动作复核视觉模型超时", True) from None
    except httpx.TransportError:
        raise ProcessingError(
            "vlm_unavailable", "无法连接动作复核视觉模型", True
        ) from None
    except (ValueError, KeyError, TypeError):
        raise ProcessingError(
            "vlm_invalid_result", "动作复核视觉模型返回无效", True
        ) from None


def review_exercise(
    video_path,
    sample_sets: dict[str, list[dict]],
    *,
    local_pick: str | None,
    local_accepted: bool,
    local_reason: str,
) -> dict | None:
    """Confirm or rescue the local recognition with the vision model.

    Returns a review dict, or None when the review is unavailable (no VLM /
    network failure) so the caller can fall back to the local decision.
    """
    rows = select_review_rows(sample_sets, count=6)
    if not rows:
        return None, "骨架可见样本不足（可见关键点少于8个），视觉兜底无法取帧"
    frames = render_stick_frames(video_path, rows)
    if not frames:
        return None, "关键帧渲染失败，视觉兜底未执行"
    summary = skeleton_summary(sample_sets)
    if local_accepted:
        suffix = (
            f"\n本地引擎判定为 {local_pick}，请确认是否合理；若明显不符，给出你认为最可能的动作。"
        )
        fallback_pick = local_pick
    else:
        suffix = (
            f"\n本地引擎无法可靠识别（{local_reason[:120]}）。"
            "请根据骨架与数值给出最可能的动作；确实无法判断则返回 unknown。"
        )
        fallback_pick = None
    try:
        review = _request_review(frames, summary, suffix)
    except ProcessingError as exc:
        return None, f"视觉模型调用失败（{exc.code}）"
    if not review or not review.get("exercise_type") or review["exercise_type"] == "unknown":
        return None, "视觉模型无法判断动作类型（unknown）"
    if review["confidence"] < 0.35:
        return None, f"视觉模型置信度不足（{review['confidence']:.0%}）"
    chosen = review["exercise_type"] if not local_accepted else (
        review["exercise_type"] if review["confidence"] >= 0.55 else fallback_pick
    )
    review.update(chosen_type=chosen, frames_used=len(frames))
    return review, ""


def is_supported(exercise_type: str) -> bool:
    from .recognition import SUPPORTED_EXERCISES

    return exercise_type in SUPPORTED_EXERCISES


FRAME_EXPLAIN_PROMPT = """你是谨慎的健身教练，为动作分析结果写逐帧讲解。
动作类型：{exercise}。下面是动作分析出的关键时刻帧：每帧有时间点、阶段、发现和建议。
请为每一帧写一段 55-90 字的中文详细讲解：说明该时刻身体姿态的意义、常见错误和正确做法，
语气平实、可执行，不要 Markdown。
返回 JSON：{{"frames":[{{"index":0,"explanation":"..."}}]}}，帧数与输入一致。"""


def explain_frames_deepseek(
    frames: list[dict], exercise_type: str
) -> dict[int, str]:
    """Generate a richer per-frame coaching explanation with the DeepSeek text model.

    Text-only call: no person image is uploaded. Returns {frame_index: text}
    or an empty dict when the model is unavailable, so callers keep local copy.
    """
    selected = [frame for frame in frames if isinstance(frame, dict)][:4]
    if not selected:
        return {}
    config = provider_config("deepseek")
    if not config["model"] or not config["api_key"]:
        return {}
    rows = []
    for frame in selected:
        rows.append(
            {
                "index": int(frame.get("index") or 0),
                "timestamp_s": round(float(frame.get("timestamp") or 0), 1),
                "stage": str(frame.get("stage") or ""),
                "finding": str(frame.get("finding") or ""),
                "advice": str(frame.get("advice") or ""),
            }
        )
    prompt = FRAME_EXPLAIN_PROMPT.format(
        exercise=str(exercise_type), count=len(rows)
    ) + "\n帧数据：\n" + json.dumps(rows, ensure_ascii=False)
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    if config["provider"] == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    headers = (
        {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
    )
    try:
        with httpx.Client(
            timeout=settings.local_vlm_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                config["base_url"] + "/chat/completions",
                headers=headers,
                json=payload,
            )
        if not response.is_success:
            return {}
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
        value = json.loads(content)
        explained = {}
        for item in (value.get("frames") or []):
            index = int(item.get("index") or 0)
            text = str(item.get("explanation") or "").strip()
            if text:
                explained[index] = text
        return explained
    except (httpx.TimeoutException, httpx.TransportError, ValueError, KeyError, TypeError):
        return {}
