"""Motion V2 end-to-end smoke (spec §7.5).

Proves the *whole* V2 chain on one controlled clip without any cloud call:

    decode → pose/timeline → render previews → V2 receipt → local contract check
    → backend contract check (same Pydantic model the API validates with)

The last step imports the backend ``MotionWorkerResultV2`` when the backend
checkout is importable, so a field drift between the worker and the API fails
here instead of at runtime. When the backend is not importable the step is
reported as SKIP with the reason, never as a pass.

Usage::

    python scripts/smoke_motion_v2.py --fixture tests/fixtures/squat-short.mp4
    python scripts/smoke_motion_v2.py                 # synthesise a clip
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from healthmate_worker.result_contract import (  # noqa: E402
    SCHEMA_VERSION,
    validate_motion_result_local,
)


def synthesise_clip(path: Path) -> bool:
    """A deterministic 3-second clip; never a real user recording."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False
    for fourcc in ("mp4v", "avc1", "MJPG"):
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*fourcc), 12.0, (240, 320)
        )
        if writer.isOpened():
            for index in range(36):
                frame = np.full((320, 240, 3), 30, dtype=np.uint8)
                # A moving bright blob gives the timeline something to track.
                y = 60 + (index % 12) * 12
                cv2.circle(frame, (120, y), 26, (220, 220, 220), -1)
                writer.write(frame)
            writer.release()
            return True
        writer.release()
    return False


def backend_contract_check(receipt: dict) -> tuple[str, str]:
    """Validate with the API's own model when the backend checkout is importable.

    Returns ``("ok" | "drift" | "skip", detail)``. A missing/broken backend
    environment is a **skip** (reported, never counted as a pass); a receipt the
    real model rejects is **drift** and fails the smoke.
    """
    backend = WORKER_ROOT.parent / "backend"
    if not backend.is_dir():
        return "skip", "backend checkout 不存在，跳过跨端契约校验"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from app.schemas.worker import MotionWorkerResultV2
    except ImportError as exc:
        return "skip", f"backend 依赖不可用（{type(exc).__name__}），跳过跨端契约校验"
    try:
        MotionWorkerResultV2.model_validate(receipt)
    except Exception as exc:  # noqa: BLE001 - a drift is the finding
        return "drift", f"跨端契约校验失败：{type(exc).__name__}: {exc}"
    return "ok", "backend MotionWorkerResultV2 接受该回执"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(args.fixture) if args.fixture else Path(tmp) / "synthetic.mp4"
        if not fixture.is_file():
            if args.fixture:
                print(f"[FAIL] fixture 不存在：{args.fixture}")
                return 1
            if not synthesise_clip(fixture):
                print("[FAIL] 无法合成受控片段（OpenCV 编码器不可用）")
                return 1
            print(f"[OK] 合成受控片段 {fixture.name}")
        else:
            print(f"[OK] 使用受控片段 {fixture.name}")

        try:
            from healthmate_worker.processors.motion_unified import (
                analyze_motion_unified,
            )

            out_dir = Path(args.out) if args.out else Path(tmp) / "previews"
            receipt = analyze_motion_unified(
                str(fixture),
                requested_exercise="auto",
                cloud_review_mode="off",
                preview_out_dir=out_dir,
                preview_uploader=None,
            )
        except Exception as exc:  # noqa: BLE001 - the reason is the finding
            print(f"[FAIL] V2 链路执行失败：{type(exc).__name__}: {exc}")
            return 1

    receipt["schema_version"] = SCHEMA_VERSION
    groups = [
        "video_quality",
        "subject",
        "pose_evidence",
        "recognition_candidates",
        "frames",
        "measurements",
    ]
    missing = [name for name in groups if name not in receipt]
    if missing:
        failures.append(f"回执缺少分组 {missing}")
        print(f"[FAIL] 回执缺少分组 {missing}")
    else:
        print(f"[OK] 六个 V2 分组齐全；frames={len(receipt['frames'])}")

    # A receipt must never carry image bytes (spec §7.1).
    if any("image_b64" in frame for frame in receipt.get("frames") or []):
        failures.append("回执携带图像字节")
        print("[FAIL] 回执携带 image_b64")
    else:
        print("[OK] 回执只带预览引用，无图像字节")

    try:
        validate_motion_result_local(receipt)
        print("[OK] worker 本地回执契约校验通过")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"worker 本地契约校验失败 {type(exc).__name__}")
        print(f"[FAIL] worker 本地契约校验失败：{type(exc).__name__}")

    state, detail = backend_contract_check(receipt)
    label = {"ok": "OK", "drift": "FAIL", "skip": "SKIP"}[state]
    print(f"[{label}] {detail}")
    if state == "drift":
        failures.append(detail)

    if args.out:
        target = Path(args.out) / "motion_v2_receipt.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8")
        print(f"[OK] 回执已写出 {target}")

    if failures:
        print("\nFAIL: motion_unified_v2 不允许注册")
        for item in failures:
            print(" -", item)
        return 1
    print("\nOK: motion_unified_v2 smoke 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
