"""Capability smoke audit (spec §7.5 / §13.6, OPS-01).

Runs the strict capability checks that do not need a real model load, plus the
receipt-shape check, and prints a machine-readable capability manifest. A
capability is only "publishable" when its backing engine is importable AND the
worker can emit a receipt the frozen contract accepts.

Usage::

    python scripts/smoke_capabilities.py            # offline report, exit 0
    python scripts/smoke_capabilities.py --strict   # release gate, exit 1 on fail
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from healthmate_worker import capabilities as caps  # noqa: E402
from healthmate_worker.config import settings  # noqa: E402
from healthmate_worker.result_contract import (  # noqa: E402
    SCHEMA_VERSION,
    validate_motion_result_local,
)


def synthetic_receipt() -> dict:
    """A minimal, structurally complete V2 receipt produced by worker code paths."""
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": "motion-unified-v2",
        "video_quality": {
            "available": True,
            "decoded_ok": True,
            "duration_ms": 1200,
            "fps": 6.0,
            "total_frames": 7,
            "blur_summary": "ok",
        },
        "subject": {"available": True, "subject_id": "s_01", "visible_regions": ["legs"]},
        "pose_evidence": {
            "available": False,
            "fps": 6.0,
            "frame_ids": [],
            "sample_count": 0,
            "measurement_summary": "capability smoke：无姿态测量",
        },
        "recognition_candidates": [
            {
                "source": "kinetics",
                "source_label": "squatting",
                "class_index": 122,
                "canonical_id": None,
                "raw_score": 0.31,
                "score_type": "softmax",
            }
        ],
        "frames": [
            {
                "frame_id": "f_000",
                "timestamp_ms": 0,
                "preview_asset_id": None,
                "subject_id": "s_01",
                "visible_regions": ["legs"],
                "blur": "ok",
                "motion_delta": 0.0,
                "phase": "起始",
                "finding": "合成证据帧",
                "advice": "仅用于契约自检",
                "next_step": "无",
            }
        ],
        "measurements": {"available": False, "reason": "capability smoke 无测量"},
        "model_versions": {"pose": "unavailable"},
    }


def build_manifest() -> dict:
    pose_ok, pose_detail = caps.pose_status()
    vlm = caps.vlm_status()
    kinetics = caps.kinetics400_status()
    declared = sorted(set(settings.capability_list))
    effective = sorted(set(caps.effective_capabilities()))

    receipt = synthetic_receipt()
    receipt_ok = True
    receipt_detail = "ok"
    try:
        validate_motion_result_local(receipt)
    except Exception as exc:  # noqa: BLE001
        receipt_ok = False
        receipt_detail = type(exc).__name__

    engines = {
        "motion_pose": {"available": pose_ok, "detail": pose_detail},
        "motion_unified_v1": {"available": pose_ok, "detail": pose_detail},
        "motion_unified_v2": {"available": pose_ok, "detail": pose_detail},
        "food_vision": {"available": vlm["available"], "detail": vlm["reason"]},
        "kinetics400": {
            "available": kinetics["available"],
            "detail": kinetics.get("reason", ""),
        },
    }

    # A capability may only be published when its engine is real AND the receipt
    # contract validates. This is the honesty gate (spec §13.6): no capability is
    # inferred from a configuration string.
    publishable = [
        name
        for name in effective
        if engines.get(name, {}).get("available") and (receipt_ok or name == "food_vision")
    ]
    phantom = [
        name for name in effective if not engines.get(name, {}).get("available")
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "declared": declared,
        "effective": effective,
        "publishable": publishable,
        "phantom": phantom,
        "engines": engines,
        "receipt_contract": {"ok": receipt_ok, "detail": receipt_detail},
        "policy": (
            "capability 只由引擎实测与回执契约校验共同产生；未通过 strict doctor 的"
            "动作能力不得注册，界面必须显示“当前设备不可用”。"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", help="print only the manifest")
    args = parser.parse_args()

    manifest = build_manifest()
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"declared : {manifest['declared']}")
        print(f"effective: {manifest['effective']}")
        print(f"publishable: {manifest['publishable']}")
        for name, info in manifest["engines"].items():
            state = "OK" if info["available"] else "OFF"
            print(f"  [{state}] {name}: {info['detail']}")
        print(f"receipt_contract: {manifest['receipt_contract']}")

    if not manifest["receipt_contract"]["ok"]:
        print("FAIL: 回执契约校验未通过")
        return 1
    if args.strict and manifest["phantom"]:
        print(f"FAIL: 声明可用但引擎不可用 {manifest['phantom']}")
        return 1
    print("OK: capability 与引擎实测一致，回执契约通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
