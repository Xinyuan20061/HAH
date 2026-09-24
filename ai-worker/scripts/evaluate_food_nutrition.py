"""Run or score the frozen local Nutrition5k RGB subset."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from healthmate_worker.evaluation import evaluate_food
from healthmate_worker.processors import analyze_food


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(dataset_root: Path) -> dict[str, list[str]]:
    result = {}
    for path in sorted(dataset_root.glob("dish_metadata_*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.reader(stream):
                if len(row) >= 6 and row[0].startswith("dish_"):
                    result[row[0]] = row
    return result


def build_annotations(dataset_root: Path) -> list[dict]:
    metadata = _metadata(dataset_root)
    image_root = dataset_root / "nutrition5k_samples" / "rgb"
    annotations = []
    for image in sorted(image_root.glob("dish_*.png")):
        row = metadata.get(image.stem)
        if not row:
            continue
        ingredients = []
        for index in range(6, len(row) - 6, 7):
            if index + 6 >= len(row):
                break
            try:
                ingredients.append(
                    {
                        "name": row[index + 1].strip(),
                        "weight_g": float(row[index + 2]),
                        "calories": float(row[index + 3]),
                    }
                )
            except (ValueError, IndexError):
                continue
        names = [item["name"] for item in sorted(ingredients, key=lambda item: -item["calories"]) if item["name"]]
        calories = float(row[1])
        annotations.append(
            {
                "sample_id": image.stem,
                "image_path": image.resolve().as_posix(),
                # Nutrition5k does not publish a plated dish-name class. The
                # frozen proxy label is the top caloric visible ingredient set.
                "dish_name": " + ".join(names[:3]) or image.stem,
                "dish_aliases": names[:3],
                "calories": calories,
                "weight_g": float(row[2]),
                "fat": float(row[3]),
                "carbs": float(row[4]),
                "protein": float(row[5]),
                "items": names or [image.stem],
                "strata": {
                    "calorie_band": "low" if calories < 300 else ("medium" if calories < 600 else "high")
                },
            }
        )
    return annotations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results/food-v1"))
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--run-vlm", action="store_true")
    args = parser.parse_args()
    if args.run_vlm == bool(args.predictions):
        parser.error("choose exactly one: --run-vlm or --predictions")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    annotations = build_annotations(args.dataset_root.resolve())
    annotation_path = output / "annotations.jsonl"
    _write_jsonl(annotation_path, annotations)
    if args.run_vlm:
        predictions = []
        for index, annotation in enumerate(annotations, 1):
            print(f"[{index}/{len(annotations)}] {annotation['sample_id']}")
            try:
                result = analyze_food(Path(annotation["image_path"]))
                predictions.append({"sample_id": annotation["sample_id"], "status": "completed", "result": result})
            except Exception as exc:
                predictions.append(
                    {
                        "sample_id": annotation["sample_id"],
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
        prediction_path = output / "predictions.jsonl"
        _write_jsonl(prediction_path, predictions)
    else:
        prediction_path = args.predictions.resolve()
        predictions = _read_jsonl(prediction_path)
    report = evaluate_food(annotations, predictions)
    report["benchmark_label"] = "VLM 基线，非最终精度"
    report["dish_name_ground_truth"] = "Nutrition5k 无菜名分类标签；Top-1/Top-3 使用前三个热量贡献食材的冻结代理标签。"
    report["provenance"] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_size": len(annotations),
        "annotations_sha256": _sha256(annotation_path),
        "predictions_sha256": _sha256(prediction_path),
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
