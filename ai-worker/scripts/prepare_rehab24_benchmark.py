"""Build leakage-safe REHAB24-6 action manifests from official segmentation rows.

The script records frame ranges; it does not copy the external dataset into the
repository. Benchmark samples are selected only from held-out subjects 7/8/9.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ACTION_BY_ID = {
    "1": "arm_abduction",
    "2": "arm_vw",
    "3": "pushup",
    "4": "leg_abduction",
    "5": "lunge",
    "6": "squat",
}
TEST_SUBJECTS = {"7", "8", "9"}
VALIDATION_SUBJECTS = {"6"}


def _split(subject_id: str) -> str:
    if subject_id in TEST_SUBJECTS:
        return "test"
    if subject_id in VALIDATION_SUBJECTS:
        return "validation"
    return "train"


def _video_path(root: Path, exercise_id: str, video_id: str, camera: int) -> Path:
    suffix = (
        f"{video_id}-Camera17-30fps.mp4"
        if camera == 17
        else f"{video_id}-Camera18-30fps-transposed.mp4"
    )
    return root / f"Ex{exercise_id}" / suffix


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segmentation", type=Path, required=True)
    parser.add_argument("--video-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--per-class", type=int, default=20)
    args = parser.parse_args()
    if args.per_class < 20 or args.per_class % 2:
        parser.error("--per-class must be an even number of at least 20")

    with args.segmentation.open("r", encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream, delimiter=";"))
    eligible = [
        row
        for row in source
        if row.get("exercise_id") in ACTION_BY_ID
        and row.get("mocap_erroneous") == "0"
        and row.get("correctness") == "1"
        and int(row.get("last_frame") or 0) > int(row.get("first_frame") or 0)
    ]
    manifest = []
    by_action: dict[str, list[dict]] = defaultdict(list)
    for row in eligible:
        exercise_id = row["exercise_id"]
        action = ACTION_BY_ID[exercise_id]
        for camera in (17, 18):
            video = _video_path(args.video_root, exercise_id, row["video_id"], camera)
            if not video.is_file():
                continue
            first, last = int(row["first_frame"]), int(row["last_frame"])
            sample_id = (
                f"rehab-{action}-{row['video_id']}-r{int(row['repetition_number']):02d}-c{camera}"
            )
            item = {
                "schema_version": "healthmate-motion-action-manifest-v1",
                "sample_id": sample_id,
                "subject_id": row["person_id"],
                "instance_id": f"{row['video_id']}-r{row['repetition_number']}",
                "source_dataset": "REHAB24-6",
                "exercise_type": action,
                "exercise_id": int(exercise_id),
                "camera": camera,
                "view": row.get("cam17_orientation") or "unknown",
                "video_path": video.resolve().as_posix(),
                "first_frame": first,
                "last_frame": last,
                "source_fps": 30,
                "start_seconds": round(first / 30, 6),
                "end_seconds": round((last + 1) / 30, 6),
                "split": _split(row["person_id"]),
                "license_status": "upstream_terms_apply_no_redistribution",
            }
            manifest.append(item)
            by_action[action].append(item)

    benchmark = []
    target_segments = args.per_class // 2
    for action in ACTION_BY_ID.values():
        rows = [item for item in by_action[action] if item["split"] == "test"]
        segments: dict[str, list[dict]] = defaultdict(list)
        for item in rows:
            segments[item["instance_id"]].append(item)
        candidates = [
            items for items in segments.values() if {x["camera"] for x in items} == {17, 18}
        ]
        # Round-robin by subject prevents a benchmark dominated by one person.
        subjects: dict[str, list[list[dict]]] = defaultdict(list)
        for items in sorted(candidates, key=lambda x: x[0]["sample_id"]):
            subjects[items[0]["subject_id"]].append(items)
        selected = []
        while len(selected) < target_segments and any(subjects.values()):
            for subject in sorted(subjects):
                if subjects[subject] and len(selected) < target_segments:
                    selected.append(subjects[subject].pop(0))
        if len(selected) < target_segments:
            raise RuntimeError(f"{action} has only {len(selected) * 2} held-out camera samples")
        for pair in selected:
            for item in pair:
                benchmark.append(
                    {
                        "sample_id": item["sample_id"],
                        "exercise_type": action,
                        "video_path": item["video_path"],
                        "start_seconds": item["start_seconds"],
                        "end_seconds": item["end_seconds"],
                        "subject_id": item["subject_id"],
                        "camera": item["camera"],
                        "should_evaluate": True,
                        "reps": 1,
                        "errors": [],
                        "events": [],
                        "quality_score": None,
                        "annotation_note": "次数来自官方单次动作分段；未提供最低点或人工质量分，相关指标不作声明。",
                    }
                )

    manifest.sort(key=lambda item: item["sample_id"])
    benchmark.sort(key=lambda item: item["sample_id"])
    _jsonl(args.manifest, manifest)
    _jsonl(args.benchmark, benchmark)
    summary = {
        "manifest_samples": len(manifest),
        "benchmark_samples": len(benchmark),
        "benchmark_by_action": dict(sorted(Counter(x["exercise_type"] for x in benchmark).items())),
        "benchmark_subjects": sorted({x["subject_id"] for x in benchmark}),
        "manifest_sha256": _sha256(args.manifest),
        "benchmark_sha256": _sha256(args.benchmark),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
