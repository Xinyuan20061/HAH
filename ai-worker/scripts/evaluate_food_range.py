"""Food three-way reporting supplement (plan §7.3 / stage-2 task 4).

Re-scores the frozen food-v1 predictions with three comparable calorie
perspectives on the SAME sample set: raw point MAE, interval (coverage +
width), and a fixed, non-cherry-picked correction proxy (interval midpoint).
Nothing is removed or re-sampled; corrections are reported as a proxy of user
effort, not as achieved accuracy.

Usage:
  ai-worker/.venv/Scripts/python.exe ai-worker/scripts/evaluate_food_range.py
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".." / "benchmark-results" / "food-v1"


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    annotations = _read_jsonl(BASE / "annotations.jsonl")
    predictions = _read_jsonl(BASE / "predictions.jsonl")
    truth_by_id = {row["sample_id"]: float(row["calories"]) for row in annotations}
    completed = [row for row in predictions if row.get("status") == "completed"]
    with_range = []
    raw_errors_all = []
    for row in completed:
        result = row.get("result") or {}
        truth = truth_by_id.get(row["sample_id"])
        if truth is None or not isinstance(result.get("calories"), (int, float)):
            continue
        raw_errors_all.append(abs(float(result["calories"]) - truth))
        low, high = result.get("calorie_range_low"), result.get("calorie_range_high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and float(high) >= float(low):
            with_range.append((row["sample_id"], truth, float(low), float(high), float(result["calories"])))

    widths = [high - low for _, _, low, high, _ in with_range]
    mid_errors = []
    raw_errors_with_range = []
    for _, truth, low, high, raw in with_range:
        mid = (low + high) / 2
        mid_errors.append(abs(mid - truth))
        raw_errors_with_range.append(abs(raw - truth))

    report = {
        "label": "识餐三口径补充（原始 / 区间 / 校正代理）",
        "sample_size": len(annotations),
        "completed": len(completed),
        "with_range": len(with_range),
        "raw": {
            "mae_kcal_all": round(statistics.mean(raw_errors_all), 2) if raw_errors_all else None,
            "mae_kcal_with_range": (
                round(statistics.mean(raw_errors_with_range), 2) if raw_errors_with_range else None
            ),
        },
        "interval": {
            "coverage_pct": (
                round(100 * sum(1 for _, t, lo, hi, _ in with_range if lo <= t <= hi) / len(with_range), 2)
                if with_range
                else None
            ),
            "width_mean_kcal": round(statistics.mean(widths), 2) if widths else None,
            "width_median_kcal": round(statistics.median(widths), 2) if widths else None,
            "width_p95_kcal": (
                round(sorted(widths)[min(len(widths) - 1, int(0.95 * len(widths)))], 2) if widths else None
            ),
        },
        "correction_proxy": {
            "midpoint_mae_kcal": round(statistics.mean(mid_errors), 2) if mid_errors else None,
            "midpoint_improvement_pct": (
                round(
                    100
                    * (1 - statistics.mean(mid_errors) / statistics.mean(raw_errors_all))
                    if mid_errors and raw_errors_all and statistics.mean(raw_errors_all) > 0
                    else None,
                    2,
                )
                if mid_errors and raw_errors_all
                else None
            ),
            "method": "用户不做精细估计、直接采用区间中点的固定代理；不允许事后挑选样本",
        },
        "notes": [
            "区间中点只是校正成本的保守代理，不代表产品已实现该准确率。",
            "区间覆盖率必须与区间宽度一起解读；窄区间高覆盖才有意义。",
            "本报告不将营养估算视为医学测量；不把示例数据作为比赛结论。",
        ],
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "annotations_sha256": _sha256(BASE / "annotations.jsonl"),
            "predictions_sha256": _sha256(BASE / "predictions.jsonl"),
        },
    }
    (BASE / "report_range.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
