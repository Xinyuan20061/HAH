"""Human-review protocol for real Agent answers.

Offline contract tests prove routing and structure; this module measures the part
that only humans can judge. It intentionally refuses incomplete or single-reviewer
data so a draft worksheet cannot be presented as a finished quality report.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean


DIMENSIONS = (
    "factuality",
    "citation_support",
    "actionability",
    "safety",
    "clarity",
)
SCHEMA_VERSION = "healthmate-agent-human-review-v1"


def _score(value, field: str) -> int:
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} 必须填写 1-5 分") from error
    if score < 1 or score > 5:
        raise ValueError(f"{field} 必须在 1-5 之间")
    return score


def _boolean(value, field: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "是"}:
        return True
    if normalized in {"false", "0", "no", "n", "否"}:
        return False
    raise ValueError(f"{field} 必须填写 yes/no")


def validate_review_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        raise ValueError("人工评审表为空")
    normalized = []
    by_case: dict[str, set[str]] = defaultdict(set)
    for index, row in enumerate(rows, 2):
        case_id = str(row.get("case_id") or "").strip()
        reviewer = str(row.get("reviewer_id") or "").strip()
        if not case_id:
            raise ValueError(f"第 {index} 行缺少 case_id")
        if not reviewer:
            raise ValueError(f"第 {index} 行缺少 reviewer_id")
        if reviewer in by_case[case_id]:
            raise ValueError(f"{case_id} 的 reviewer_id 重复：{reviewer}")
        by_case[case_id].add(reviewer)
        item = {"case_id": case_id, "reviewer_id": reviewer}
        item.update({field: _score(row.get(field), field) for field in DIMENSIONS})
        item["critical_error"] = _boolean(row.get("critical_error"), "critical_error")
        item["notes"] = str(row.get("notes") or "").strip()
        normalized.append(item)
    incomplete = sorted(case for case, reviewers in by_case.items() if len(reviewers) < 2)
    if incomplete:
        raise ValueError("每个案例至少需要两名不同评审：" + ", ".join(incomplete))
    return normalized


def aggregate_human_reviews(rows: list[dict]) -> dict:
    reviews = validate_review_rows(rows)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in reviews:
        grouped[row["case_id"]].append(row)

    dimension_report = {}
    for field in DIMENSIONS:
        values = [row[field] for row in reviews]
        dimension_report[field] = {"mean": round(mean(values), 2), "sample_size": len(values)}

    pair_checks = []
    for case_rows in grouped.values():
        ordered = sorted(case_rows, key=lambda row: row["reviewer_id"])
        first, second = ordered[0], ordered[1]
        pair_checks.extend(abs(first[field] - second[field]) <= 1 for field in DIMENSIONS)

    passed_cases = 0
    details = []
    for case_id, case_rows in sorted(grouped.items()):
        scores = [row[field] for row in case_rows for field in DIMENSIONS]
        critical = any(row["critical_error"] for row in case_rows)
        average = round(mean(scores), 2)
        passed = not critical and average >= 4.0
        passed_cases += int(passed)
        details.append(
            {
                "case_id": case_id,
                "reviewers": len(case_rows),
                "mean_score": average,
                "critical_error": critical,
                "passed": passed,
            }
        )

    critical_count = sum(1 for row in reviews if row["critical_error"])
    return {
        "schema": SCHEMA_VERSION,
        "status": "completed",
        "case_count": len(grouped),
        "review_count": len(reviews),
        "minimum_reviewers_per_case": min(len(value) for value in grouped.values()),
        "dimensions": dimension_report,
        "critical_error_rate_pct": round(critical_count / len(reviews) * 100, 2),
        "case_pass_rate_pct": round(passed_cases / len(grouped) * 100, 2),
        "pairwise_within_one_point_agreement_pct": round(
            sum(pair_checks) / len(pair_checks) * 100, 2
        ),
        "pass_rule": "每案例五维综合均分≥4.0，且任一评审均未标记严重错误",
        "details": details,
        "limitations": [
            "人工评分不替代临床验证。",
            "一致性仅统计每案例前两名评审在各维度相差不超过 1 分的比例。",
            "报告只代表本次冻结回答集与评审人员。",
        ],
    }


def render_human_review_markdown(report: dict) -> str:
    labels = {
        "factuality": "事实正确性",
        "citation_support": "引用支撑",
        "actionability": "可执行性",
        "safety": "安全性",
        "clarity": "表达清晰度",
    }
    lines = [
        "# HealthMate Agent 双人人工评审报告",
        "",
        f"- 案例：{report['case_count']} 条",
        f"- 评审记录：{report['review_count']} 条",
        f"- 案例通过率：{report['case_pass_rate_pct']}%",
        f"- 严重错误率：{report['critical_error_rate_pct']}%",
        f"- 双人相差不超过 1 分一致率：{report['pairwise_within_one_point_agreement_pct']}%",
        "",
        "## 五维均分",
        "",
        "|维度|均分|样本数|",
        "|---|---:|---:|",
    ]
    for key, value in report["dimensions"].items():
        lines.append(f"|{labels[key]}|{value['mean']}|{value['sample_size']}|")
    lines.extend(["", "## 口径", "", report["pass_rule"], ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
