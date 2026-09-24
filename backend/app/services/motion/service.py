import math, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageChops, ImageStat


class MotionAnalysisUnavailable(RuntimeError):
    pass


def _run(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
    )


def _duration(path: Path) -> float:
    if not shutil.which("ffprobe"):
        raise MotionAnalysisUnavailable("服务器未安装 ffprobe/ffmpeg。")
    r = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return max(0.1, float(r.stdout.strip()))


def _motion_score(prev, cur):
    if not prev:
        return 0.0
    with Image.open(prev).convert("L") as a, Image.open(cur).convert("L") as b:
        d = ImageChops.difference(a.resize((160, 90)), b.resize((160, 90)))
        return round(min(100, ImageStat.Stat(d).mean[0] / 255 * 420), 1)


def analyze_keyframes(video_path: Path, public_prefix="/uploads"):
    if not shutil.which("ffmpeg"):
        raise MotionAnalysisUnavailable("服务器未安装 ffmpeg。")
    duration = _duration(video_path)
    count = 6 if duration >= 8 else 5
    ts = [duration * (i + 1) / (count + 1) for i in range(count)]
    out_dir = video_path.parent / "frames" / video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    prev = None
    for i, t in enumerate(ts):
        out = out_dir / f"frame_{i + 1:02d}.jpg"
        _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-q:v",
                "3",
                str(out),
            ]
        )
        score = _motion_score(prev, out)
        frames.append(
            {
                "index": i,
                "timestamp": round(t, 2),
                "label": f"{t:.1f}s",
                "url": f"{public_prefix}/frames/{video_path.stem}/{out.name}",
                "motion_score": score,
            }
        )
        prev = out
    scores = [x["motion_score"] for x in frames[1:]]
    avg = round(sum(scores) / len(scores), 1) if scores else 0
    peak = max(scores) if scores else 0
    return {
        "duration": round(duration, 2),
        "frame_count": len(frames),
        "frames": frames,
        "motion": {"average": avg, "peak": peak},
        "method": "ffmpeg_keyframe + grayscale_frame_difference",
    }


def _angle(a, b, c):
    ba = (a[0] - b[0], a[1] - b[1])
    bc = (c[0] - b[0], c[1] - b[1])
    den = math.hypot(*ba) * math.hypot(*bc)
    if den < 1e-8:
        return None
    x = max(-1, min(1, (ba[0] * bc[0] + ba[1] * bc[1]) / den))
    return math.degrees(math.acos(x))


def analyze_pose_video(video_path: Path, exercise_type="squat"):
    base = analyze_keyframes(video_path)
    try:
        import cv2, mediapipe as mp
    except Exception:
        base["pose"] = {
            "available": False,
            "engine": "mediapipe-not-installed",
            "message": "AI worker 未安装 MediaPipe，当前返回 FFmpeg 基础分析。生产建议使用 Python 3.11/3.12 的 pose-worker 镜像。",
        }
        base["method"] = "ffmpeg_fallback"
        return base
    cap = cv2.VideoCapture(str(video_path))
    pose = mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    stride = max(1, int(fps / 6))
    idx = 0
    samples = []
    sampled_frames = 0
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            sampled_frames += 1
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            r = pose.process(rgb)
            if r.pose_landmarks:
                lm = r.pose_landmarks.landmark
                P = mp.solutions.pose.PoseLandmark

                def pt(x):
                    q = lm[x.value]
                    return (q.x, q.y, q.visibility)

                side = "left" if pt(P.LEFT_HIP)[2] >= pt(P.RIGHT_HIP)[2] else "right"
                H, K, A, S = (
                    (P.LEFT_HIP, P.LEFT_KNEE, P.LEFT_ANKLE, P.LEFT_SHOULDER)
                    if side == "left"
                    else (P.RIGHT_HIP, P.RIGHT_KNEE, P.RIGHT_ANKLE, P.RIGHT_SHOULDER)
                )
                h, k, a, s = pt(H), pt(K), pt(A), pt(S)
                knee = _angle(h, k, a)
                hip = _angle(s, h, k)
                trunk = abs(math.degrees(math.atan2(s[0] - h[0], -(s[1] - h[1]))))
                samples.append(
                    {
                        "t": round(idx / fps, 2),
                        "knee": round(knee, 1) if knee else None,
                        "hip": round(hip, 1) if hip else None,
                        "trunk": round(trunk, 1),
                        "visibility": round(min(h[2], k[2], a[2], s[2]), 2),
                    }
                )
        idx += 1
    cap.release()
    pose.close()
    valid = [x for x in samples if x["knee"] is not None and x["visibility"] >= 0.5]
    if not valid:
        base["pose"] = {
            "available": False,
            "engine": "mediapipe",
            "message": "未稳定检测到全身关键点，请固定机位并保证全身入镜。",
        }
        return base
    knees = [x["knee"] for x in valid]
    min_k = min(knees)
    max_k = max(knees)
    reps = 0
    state = "up"
    for a in knees:
        if state == "up" and a < 115:
            state = "down"
        elif state == "down" and a > 155:
            reps += 1
            state = "up"
    errors = []
    if exercise_type == "squat":
        if min_k > 105:
            errors.append(
                {
                    "code": "depth_insufficient",
                    "label": "下蹲不足",
                    "severity": "medium",
                    "evidence": f"最小膝角 {min_k:.1f}°",
                }
            )
        if max(x["trunk"] for x in valid) > 45:
            errors.append(
                {
                    "code": "trunk_lean",
                    "label": "躯干前倾较大",
                    "severity": "medium",
                    "evidence": f"最大倾角 {max(x['trunk'] for x in valid):.1f}°",
                }
            )
    base["pose"] = {
        "available": True,
        "engine": "mediapipe-pose",
        "exercise_type": exercise_type,
        "sample_count": len(valid),
        "sampled_frames": sampled_frames,
        "keypoint_valid_rate": round(len(valid) / sampled_frames, 3)
        if sampled_frames
        else 0,
        "visibility_mean": round(sum(x["visibility"] for x in valid) / len(valid), 3),
        "reps": reps,
        "angles": {
            "knee_min": round(min_k, 1),
            "knee_max": round(max_k, 1),
            "hip_min": round(min(x["hip"] for x in valid if x["hip"] is not None), 1),
        },
        "errors": errors,
        "samples": valid[:: max(1, len(valid) // 80)],
    }
    base["method"] = "mediapipe_pose + rule_engine + ffmpeg"
    return base
