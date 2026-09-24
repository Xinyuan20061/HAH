"""Convert licensed local videos into compact 12-joint NPZ training artifacts."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.datasets.manifest import validate_manifest
from healthmate_worker.models.skeleton_schema import JOINT_NAMES


LANDMARK_INDICES = dict(
    zip(JOINT_NAMES, [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28])
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_local(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError("video path escapes dataset root") from None
    return path


def extract_pose(video_path: Path, sample_fps: float = 12.0) -> np.ndarray:
    try:
        import cv2
        import mediapipe as mp
    except (ImportError, OSError):
        raise RuntimeError("MediaPipe/OpenCV is required for pose extraction") from None
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("video cannot be opened")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if source_fps <= 0:
        capture.release()
        raise ValueError("video FPS is invalid")
    stride = max(1, round(source_fps / sample_fps))
    frames = []
    pose = mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    index = 0
    try:
        while capture.isOpened():
            ok, frame = capture.read()
            if not ok:
                break
            if index % stride == 0:
                result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if result.pose_landmarks:
                    frames.append(
                        [
                            [
                                float(result.pose_landmarks.landmark[landmark].x),
                                float(result.pose_landmarks.landmark[landmark].y),
                                float(result.pose_landmarks.landmark[landmark].visibility),
                            ]
                            for landmark in LANDMARK_INDICES.values()
                        ]
                    )
            index += 1
    finally:
        capture.release()
        pose.close()
    if len(frames) < 4:
        raise ValueError("fewer than four valid pose frames")
    return np.asarray(frames, dtype=np.float32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output_manifest", type=Path)
    parser.add_argument("--pose-dir", default="derived-pose")
    parser.add_argument("--sample-fps", type=float, default=12.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    rows = validate_manifest(read_jsonl(args.manifest))
    root = args.dataset_root.resolve()
    pose_root = resolve_local(root, args.pose_dir)
    pose_root.mkdir(parents=True, exist_ok=True)
    converted, failures = [], []
    for row in rows:
        if not row.get("video_path"):
            failures.append({"sample_id": row["sample_id"], "reason": "video_path missing"})
            continue
        relative_pose = f"{args.pose_dir.rstrip('/')}/{row['sample_id']}.npz"
        output = resolve_local(root, relative_pose)
        try:
            if output.exists() and not args.overwrite:
                raise ValueError("pose output already exists; use --overwrite to replace")
            sequence = extract_pose(resolve_local(root, row["video_path"]), args.sample_fps)
            np.savez_compressed(output, skeleton=sequence, joint_names=np.asarray(JOINT_NAMES))
            converted.append({**row, "pose_path": relative_pose})
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append({"sample_id": row["sample_id"], "reason": str(exc)[:240]})
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in converted) + ("\n" if converted else ""),
        encoding="utf-8",
    )
    print(json.dumps({"converted": len(converted), "failed": len(failures), "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if converted and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
