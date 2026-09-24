from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.evaluation import evaluate_motion, render_markdown
from healthmate_worker import __version__
from healthmate_worker.processors import analyze_motion
from healthmate_worker.processors.analyzers import THRESHOLDS


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: each line must be a JSON object")
        rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_videos(
    annotations: list[dict], manifest_path: Path, auto_recognition: bool = False
) -> list[dict]:
    predictions = []
    for index, annotation in enumerate(annotations, 1):
        video_value = annotation.get("video_path")
        if not isinstance(video_value, str) or not video_value:
            raise ValueError(f"sample {annotation.get('sample_id', index)} has no video_path")
        video_path = Path(video_value)
        if not video_path.is_absolute():
            video_path = (manifest_path.parent / video_path).resolve()
        requested_type = "auto" if auto_recognition else annotation["exercise_type"]
        print(f"[{index}/{len(annotations)}] {annotation['sample_id']} -> {requested_type}")
        try:
            result = analyze_motion(
                video_path,
                requested_type,
                start_seconds=float(annotation.get("start_seconds") or 0),
                end_seconds=annotation.get("end_seconds"),
            )
            predictions.append({"sample_id": annotation["sample_id"], "status": "completed", "result": result})
        except Exception as error:
            predictions.append(
                {
                    "sample_id": annotation["sample_id"],
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and score a fixed HealthMate motion benchmark dataset.")
    parser.add_argument("--annotations", type=Path, required=True, help="JSONL annotation manifest")
    parser.add_argument("--predictions", type=Path, help="Existing JSONL predictions")
    parser.add_argument("--run-videos", action="store_true", help="Run the Worker against video_path in the manifest")
    parser.add_argument(
        "--auto-recognition",
        action="store_true",
        help="With --run-videos, infer the exercise type instead of using its annotation",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results"))
    parser.add_argument("--event-tolerance", type=float, default=0.35)
    args = parser.parse_args()
    if args.run_videos == bool(args.predictions):
        parser.error("choose exactly one: --run-videos or --predictions")

    annotation_path = args.annotations.resolve()
    annotations = read_jsonl(annotation_path)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.run_videos:
        predictions = run_videos(
            annotations, annotation_path, auto_recognition=args.auto_recognition
        )
        prediction_path = output_dir / "predictions.jsonl"
        write_jsonl(prediction_path, predictions)
    else:
        prediction_path = args.predictions.resolve()
        predictions = read_jsonl(prediction_path)

    report = evaluate_motion(annotations, predictions, args.event_tolerance)
    report["provenance"] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "worker_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "annotations_file": annotation_path.name,
        "predictions_file": prediction_path.name,
        "annotations_sha256": sha256_file(annotation_path),
        "predictions_sha256": sha256_file(prediction_path),
        "thresholds": {
            exercise: {
                "down": values.down,
                "up": values.up,
                "depth": values.depth,
                "trunk": values.trunk,
                "visibility": values.visibility,
                "min_samples": values.min_samples,
                "min_valid_rate": values.min_valid_rate,
                "min_rep_seconds": values.min_rep_seconds,
            }
            for exercise, values in THRESHOLDS.items()
        },
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Reports: {output_dir / 'report.json'} | {output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
