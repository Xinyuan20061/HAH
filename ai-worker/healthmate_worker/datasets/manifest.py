"""Strict local manifest protocol for licensed motion datasets.

The manifest contains metadata and relative paths only. It never downloads data,
changes licenses, or makes public datasets redistributable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import PurePosixPath
import re


SCHEMA_VERSION = "healthmate-semantic-manifest-v1"
SPLITS = {"train", "validation", "test"}
CONSENT_STATES = {"license_governed", "consented", "synthetic"}


def _strings(value, field: str, line: int) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"manifest line {line}: {field} must be a non-empty string list")
    return list(dict.fromkeys(item.strip() for item in value))


def _relative_path(value, field: str, line: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest line {line}: {field} must be a relative POSIX path")
    if "\\" in value or ":" in value:
        raise ValueError(f"manifest line {line}: {field} must not be absolute or platform-specific")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"manifest line {line}: {field} escapes the dataset root")
    return path.as_posix()


def validate_manifest(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("manifest must be a list of JSON objects")
    seen: set[str] = set()
    normalized = []
    for line, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"manifest line {line}: row must be an object")
        sample_id = row.get("sample_id")
        if (
            not isinstance(sample_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", sample_id)
            or sample_id in seen
        ):
            raise ValueError(f"manifest line {line}: sample_id must be unique and non-empty")
        seen.add(sample_id)
        required_strings = ["subject_id", "instance_id", "source_dataset", "action_text", "action_family"]
        for field in required_strings:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"manifest line {line}: {field} must be non-empty")
        media = {}
        for field in ["video_path", "pose_path"]:
            if row.get(field):
                media[field] = _relative_path(row[field], field, line)
        if not media:
            raise ValueError(f"manifest line {line}: video_path or pose_path is required")
        license_record = row.get("license_record")
        if not isinstance(license_record, dict):
            raise ValueError(f"manifest line {line}: license_record is required")
        for field in ["dataset_key", "terms_reviewed_on", "allowed_use"]:
            if not isinstance(license_record.get(field), str) or not license_record[field].strip():
                raise ValueError(f"manifest line {line}: license_record.{field} is required")
        consent_status = row.get("consent_status")
        if consent_status not in CONSENT_STATES:
            raise ValueError(f"manifest line {line}: unsupported consent_status")
        split = row.get("split")
        if split is not None and split not in SPLITS:
            raise ValueError(f"manifest line {line}: split must be train, validation, or test")
        is_unknown_action = row.get("is_unknown_action", False)
        if type(is_unknown_action) is not bool:
            raise ValueError(f"manifest line {line}: is_unknown_action must be boolean")
        normalized.append(
            {
                "schema_version": SCHEMA_VERSION,
                "sample_id": sample_id.strip(),
                "subject_id": row["subject_id"].strip(),
                "instance_id": row["instance_id"].strip(),
                "source_dataset": row["source_dataset"].strip(),
                **media,
                "action_text": row["action_text"].strip(),
                "action_family": row["action_family"].strip(),
                "movement_patterns": _strings(row.get("movement_patterns", []), "movement_patterns", line),
                "observed_regions": _strings(row.get("observed_regions", []), "observed_regions", line),
                "target_body_parts": _strings(row.get("target_body_parts", []), "target_body_parts", line),
                "view": str(row.get("view") or "unknown")[:40],
                "consent_status": consent_status,
                "license_record": {
                    "dataset_key": license_record["dataset_key"].strip(),
                    "terms_reviewed_on": license_record["terms_reviewed_on"].strip(),
                    "allowed_use": license_record["allowed_use"].strip(),
                },
                "annotation_status": str(row.get("annotation_status") or "unreviewed")[:40],
                "is_unknown_action": is_unknown_action,
                "split": split,
            }
        )
    return normalized


def _subject_bucket(subject_id: str, seed: str) -> float:
    digest = hashlib.sha256(f"{seed}:{subject_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)


def assign_subject_splits(
    rows: list[dict],
    *,
    seed: str = "healthmate-v1",
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
    unseen_action_families: set[str] | None = None,
) -> list[dict]:
    if train_ratio <= 0 or validation_ratio < 0 or train_ratio + validation_ratio >= 1:
        raise ValueError("split ratios must leave a positive test partition")
    normalized = validate_manifest(rows)
    unseen = unseen_action_families or set()
    forced_test_subjects = {
        row["subject_id"] for row in normalized if row["action_family"] in unseen
    }
    subject_split = {}
    for subject_id in sorted({row["subject_id"] for row in normalized}):
        if subject_id in forced_test_subjects:
            split = "test"
        else:
            bucket = _subject_bucket(subject_id, seed)
            split = "train" if bucket < train_ratio else (
                "validation" if bucket < train_ratio + validation_ratio else "test"
            )
        subject_split[subject_id] = split
    assigned = [{**row, "split": subject_split[row["subject_id"]]} for row in normalized]
    verify_no_leakage(assigned, unseen_action_families=unseen)
    return assigned


def verify_no_leakage(rows: list[dict], *, unseen_action_families: set[str] | None = None) -> None:
    by_subject: dict[str, set[str]] = defaultdict(set)
    by_instance: dict[str, set[str]] = defaultdict(set)
    family_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split = row.get("split")
        if split not in SPLITS:
            raise ValueError("every sample must have a valid split")
        by_subject[row["subject_id"]].add(split)
        by_instance[row["instance_id"]].add(split)
        family_splits[row["action_family"]].add(split)
    leaking_subjects = sorted(key for key, splits in by_subject.items() if len(splits) > 1)
    leaking_instances = sorted(key for key, splits in by_instance.items() if len(splits) > 1)
    if leaking_subjects or leaking_instances:
        raise ValueError(f"split leakage: subjects={leaking_subjects}, instances={leaking_instances}")
    for family in unseen_action_families or set():
        if family_splits.get(family, set()) - {"test"}:
            raise ValueError(f"unseen action family {family} leaked outside test")


def summarize_manifest(rows: list[dict]) -> dict:
    verify_no_leakage(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_count": len(rows),
        "subject_count": len({row["subject_id"] for row in rows}),
        "instance_count": len({row["instance_id"] for row in rows}),
        "by_split": dict(sorted(Counter(row["split"] for row in rows).items())),
        "by_dataset": dict(sorted(Counter(row["source_dataset"] for row in rows).items())),
        "by_action_family": dict(sorted(Counter(row["action_family"] for row in rows).items())),
        "license_records_present": all(bool(row.get("license_record")) for row in rows),
    }


def manifest_sha256(rows: list[dict]) -> str:
    payload = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in sorted(rows, key=lambda item: item["sample_id"])
    )
    return hashlib.sha256(payload.encode()).hexdigest()
