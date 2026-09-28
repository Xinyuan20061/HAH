"""Same-set three-way motion evaluation: rules-only vs Kinetics-400-only vs
rule+Kinetics fusion on the fixed REHAB24-6 120-clip manifest (plan §7.2.2).

One clip is fed to all three schemes so Top-1 / coverage / Macro-F1 / per-class
recall / abstention / latency are directly comparable. Kinetics stays a
candidate layer: this script only measures; it never changes production
behavior (KINETICS400_OVERRIDE_ENABLED stays False).

The REHAB24-6 set is the historical baseline (train split, two camera views).
An independent fixed set with non-overlapping subjects is required before any
threshold may be set on a validation set; until then all numbers are
experimental-candidate evidence, not an activation license.

Usage:
  ai-worker/.venv/Scripts/python.exe ai-worker/scripts/evaluate_kinetics_same_set.py --run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthmate_worker import __version__  # noqa: E402
from healthmate_worker.config import settings  # noqa: E402
from healthmate_worker.models.kinetics_runtime import recognize_video_kinetics400  # noqa: E402
from healthmate_worker.processors import analyze_motion  # noqa: E402
from healthmate_worker.processors.recognition import SUPPORTED_EXERCISES  # noqa: E402

DEFAULT_MANIFEST = ROOT / ".." / "benchmark" / "rehab24_action_manifest.jsonl"
DEFAULT_OUTPUT = ROOT / ".." / "benchmark-results" / "motion-v2-kinetics"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
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


def classification_metrics(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def score_scheme(rows: list[dict], scheme: str) -> dict:
    """scheme in {'rule','kinetics','fusion'}."""
    truths = [row["exercise_type"] for row in rows]
    preds = [row[f"{scheme}_pred"] for row in rows]
    n = len(truths)
    covered = sum(p in SUPPORTED_EXERCISES for p in preds)
    correct = sum(t == p for t, p in zip(truths, preds))
    abstain = sum(p is None for p in preds)
    confusion = {
        truth: {pred: 0 for pred in [*sorted(SUPPORTED_EXERCISES), "abstain"]}
        for truth in sorted(SUPPORTED_EXERCISES)
    }
    per_class = {}
    f1s = []
    for exercise in sorted(SUPPORTED_EXERCISES):
        tp = sum(t == exercise and p == exercise for t, p in zip(truths, preds))
        fp = sum(t != exercise and p == exercise for t, p in zip(truths, preds))
        fn = sum(t == exercise and p != exercise for t, p in zip(truths, preds))
        metrics = classification_metrics(tp, fp, fn)
        per_class[exercise] = metrics
        if metrics["f1"] is not None:
            f1s.append(metrics["f1"])
    for t, p in zip(truths, preds):
        confusion[t][p if p in SUPPORTED_EXERCISES else "abstain"] += 1
    latencies = [row[f"{scheme}_latency_ms"] for row in rows if row.get(f"{scheme}_latency_ms") is not None]
    latency = {}
    if latencies:
        sorted_lat = sorted(latencies)
        latency = {
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(sorted_lat[min(len(sorted_lat) - 1, int(0.95 * len(sorted_lat)))], 1),
        }
    return {
        "sample_size": n,
        "coverage_pct": round(100 * covered / n, 2),
        "top1_accuracy_pct": round(100 * correct / n, 2),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "abstain_rate_pct": round(100 * abstain / n, 2),
        "per_class_recall": {
            ex: metrics["recall"] for ex, metrics in sorted(per_class.items())
        },
        "per_class_f1": {ex: metrics["f1"] for ex, metrics in sorted(per_class.items())},
        "confusion": confusion,
        "latency_ms": latency,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N samples (0 = all)")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first M samples")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.run == bool(args.predictions):
        parser.error("choose exactly one: --run or --predictions")

    manifest_path = args.manifest.resolve()
    annotations = read_jsonl(manifest_path)
    if args.offset > 0:
        annotations = annotations[args.offset :]
    if args.limit > 0:
        annotations = annotations[: args.limit]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    if args.run:
        if not settings.kinetics400_checkpoint.strip():
            print("ERROR: KINETICS400_CHECKPOINT not configured; cannot run Kinetics.", file=sys.stderr)
            return 2
        rows = []
        for index, annotation in enumerate(annotations, 1):
            video = Path(annotation["video_path"])
            print(f"[{index}/{len(annotations)}] {annotation['sample_id']}")
            sample = {
                "sample_id": annotation["sample_id"],
                "exercise_type": annotation["exercise_type"],
                "subject_id": annotation.get("subject_id"),
                "camera": annotation.get("camera"),
                "view": annotation.get("view"),
                "video_path": annotation.get("video_path"),
            }
            try:
                t0 = time.perf_counter()
                result = analyze_motion(
                    video,
                    "auto",
                    start_seconds=float(annotation.get("start_seconds") or 0),
                    end_seconds=annotation.get("end_seconds"),
                )
                rule_latency_ms = (time.perf_counter() - t0) * 1000
                recognition = result.get("recognition") or {}
                rule_pred = (
                    recognition.get("selected_type")
                    if recognition.get("accepted") is True
                    else None
                )
                # Kinetics-only pass with its own latency (model already loaded).
                t1 = time.perf_counter()
                kinetics = recognize_video_kinetics400(video)
                kinetics_latency_ms = (time.perf_counter() - t1) * 1000
                if kinetics is None:
                    kinetics_pred = None
                    klabel = kprob = ""
                else:
                    klabel = kinetics.get("top_label", "")
                    kprob = float(kinetics.get("top_probability") or 0)
                    kinetics_pred = (
                        kinetics.get("mapped_exercise")
                        if kprob >= settings.kinetics400_min_confidence
                        else None
                    )
                # Fusion is simulated locally for measurement only: Kinetics
                # strongly agrees with its mapped exercise -> follow it,
                # otherwise keep the rule pick. Production gate stays False.
                fusion_pred = (
                    kinetics.get("mapped_exercise")
                    if kinetics and kinetics.get("mapped_exercise") and kprob >= settings.kinetics400_strong_confidence
                    else rule_pred
                )
                sample.update(
                    {
                        "rule_pred": rule_pred,
                        "kinetics_pred": kinetics_pred,
                        "fusion_pred": fusion_pred,
                        "kinetics_label": klabel,
                        "kinetics_prob": round(kprob, 4),
                        "rule_latency_ms": round(rule_latency_ms, 1),
                        "kinetics_latency_ms": round(kinetics_latency_ms, 1),
                        "status": "completed",
                    }
                )
            except Exception as error:
                sample.update(
                    {
                        "rule_pred": None,
                        "kinetics_pred": None,
                        "fusion_pred": None,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error)[:400],
                    }
                )
            rows.append(sample)
        prediction_path = output / "predictions.jsonl"
        write_jsonl(prediction_path, rows)
    else:
        prediction_path = args.predictions.resolve()
        rows = read_jsonl(prediction_path)

    completed = [row for row in rows if row.get("status") == "completed"]
    report = {
        "label": "Kinetics-400 同集三路对照（实验候选层）",
        "schemes": {scheme: score_scheme(completed, scheme) for scheme in ("rule", "kinetics", "fusion")},
        "gate_status": {
            "kinetics400_override_enabled": False,
            "candidate_layer": True,
            "activation_license": "未达门槛前不覆盖规则；阈值只能在独立验证集设定（本集为历史基线，无独立划分）。",
        },
        "failures": {
            "count": sum(row.get("status") != "completed" for row in rows),
            "samples": [row for row in rows if row.get("status") != "completed"][:10],
        },
        "error_cases": _error_cases(completed),
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "worker_version": __version__,
            "python": sys.version.split()[0],
            "manifest_file": manifest_path.name,
            "predictions_file": prediction_path.name,
            "manifest_sha256": sha256_file(manifest_path),
            "predictions_sha256": sha256_file(prediction_path),
            "min_confidence": settings.kinetics400_min_confidence,
            "strong_confidence": settings.kinetics400_strong_confidence,
            "device": settings.kinetics400_device,
        },
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Reports: {output / 'report.json'} | {output / 'report.md'}")
    return 0


def _error_cases(rows: list[dict]) -> list[dict]:
    """At least five real misclassification examples with reasons, no cherry-picking."""
    cases = []
    for row in rows:
        truth = row["exercise_type"]
        wrong = []
        for scheme in ("rule", "kinetics", "fusion"):
            if row[f"{scheme}_pred"] != truth:
                wrong.append(scheme)
        if wrong:
            cases.append(
                {
                    "sample_id": row["sample_id"],
                    "truth": truth,
                    "rule_pred": row["rule_pred"],
                    "kinetics_pred": row["kinetics_pred"],
                    "fusion_pred": row["fusion_pred"],
                    "kinetics_label": row.get("kinetics_label"),
                    "kinetics_prob": row.get("kinetics_prob"),
                    "wrong_schemes": wrong,
                    "reason_hint": _reason_hint(row),
                }
            )
    cases.sort(key=lambda c: c["kinetics_prob"] if isinstance(c["kinetics_prob"], (int, float)) else -1)
    return cases[:10]


def _reason_hint(row: dict) -> str:
    hints = []
    if row.get("rule_pred") != row["exercise_type"]:
        hints.append("规则识别与标注不一致（规则为实验基线，非临床标准）")
    if row.get("kinetics_pred") is None and row.get("kinetics_label"):
        hints.append(f"Kinetics 识别为 {row['kinetics_label']}，未映射到六动作或低于最低置信")
    if row.get("kinetics_pred") and row["kinetics_pred"] != row["exercise_type"]:
        hints.append("Kinetics 映射动作与标注不一致（400 类通用模型非六动作专用）")
    return "；".join(hints) or "预测未覆盖"


def render_markdown(report: dict) -> str:
    lines = [
        "# HealthMate 动作同集三路对照报告（Kinetics-400 实验候选层）",
        "",
        "- 标注样本：%d（REHAB24-6 子集：6 类 × 2 实例 × 双视角；全量 120 段基线见 benchmark-results/motion-v1）" % report["schemes"]["rule"]["sample_size"],
        "- 处理失败：%d（不删失败样本）" % report["failures"]["count"],
        "",
        "## 三路同集对照（同一固定集、同一输入视频）",
        "",
        "| 方案 | 覆盖率 | Top-1 全样本 | Macro-F1 | 拒识率 | 时延 P50/P95(ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for scheme, label in (("rule", "规则单独"), ("kinetics", "Kinetics-400 单独"), ("fusion", "规则+Kinetics 融合(模拟)")):
        metrics = report["schemes"][scheme]
        latency = metrics.get("latency_ms") or {}
        lines.append(
            "| %s | %.2f%% | %.2f%% | %s | %.2f%% | %s/%s |"
            % (
                label,
                metrics["coverage_pct"],
                metrics["top1_accuracy_pct"],
                ("%.4f" % metrics["macro_f1"]) if metrics["macro_f1"] is not None else "—",
                metrics["abstain_rate_pct"],
                latency.get("p50_ms", "—"),
                latency.get("p95_ms", "—"),
            )
        )
    lines += [
        "",
        "## 每类召回（Top-1 口径）",
        "",
        "| 动作 | 规则 | Kinetics | 融合 |",
        "|---|---:|---:|---:|",
    ]
    for exercise in sorted(SUPPORTED_EXERCISES):
        line = "| %s |" % exercise
        for scheme in ("rule", "kinetics", "fusion"):
            recall = report["schemes"][scheme]["per_class_recall"][exercise]
            line += " %s |" % ("%.4f" % recall if recall is not None else "—")
        lines.append(line)
    lines += [
        "",
        "> 注：Macro-F1 仅对存在正例（tp+fn>0）的类别求平均；覆盖率低的方案（如本集 Kinetics 仅 lunge 有映射预测）其 Macro-F1 不代表整体性能。",
        "",
        "## 门控状态",
        "",
        "- `kinetics400_override_enabled=false`：Kinetics-400 仍为候选层，不覆盖规则或用户手选。",
        "- 未达 §7.2 门槛（Top-1≥80%、覆盖率≥90%、Macro-F1≥0.78、任一目标动作召回≥65%）前不得开启覆盖。",
        "- 本集为历史基线且无独立验证集划分，softmax 未校准，**不可用于设定上线阈值**。",
        "",
        "## 错误案例（不挑选样本，按 Kinetics 概率排序展示前 10 条）",
        "",
        "| 样本 | 真值 | 规则 | Kinetics | 融合 | Kinetics 标签(概率) | 原因提示 |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in report["error_cases"]:
        lines.append(
            "| %s | %s | %s | %s | %s | %s (%.3f) | %s |"
            % (
                case["sample_id"],
                case["truth"],
                case["rule_pred"] or "—",
                case["kinetics_pred"] or "—",
                case["fusion_pred"] or "—",
                case["kinetics_label"] or "—",
                case["kinetics_prob"] if isinstance(case["kinetics_prob"], (int, float)) else -1,
                case["reason_hint"],
            )
        )
    lines += [
        "",
        "## 可复现信息",
        "",
        "- 生成时间（UTC）：%s" % report["provenance"]["generated_at"],
        "- Worker 版本：%s" % report["provenance"]["worker_version"],
        "- 清单 SHA-256：`%s`" % report["provenance"]["manifest_sha256"],
        "- 预测 SHA-256：`%s`" % report["provenance"]["predictions_sha256"],
        "- 门槛参数：min_confidence=%.2f strong_confidence=%.2f device=%s"
        % (
            report["provenance"]["min_confidence"],
            report["provenance"]["strong_confidence"],
            report["provenance"]["device"],
        ),
        "",
        "## 不得据此宣称",
        "",
        "- 准确率仅对本报告对应固定集有效；REHAB24-6 受试者、机位与光照覆盖有限。",
        "- Kinetics-400 是 400 类通用动作识别，不是六动作专属模型；覆盖六动作仅是其中 3 类有映射。",
        "- 本报告是实验候选层证据，不是上线许可；不启动训练、不删失败样本。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
