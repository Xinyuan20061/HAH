import pytest

from app.services.agent.human_review import (
    aggregate_human_reviews,
    render_human_review_markdown,
    validate_review_rows,
)


def _row(case_id, reviewer, scores=(5, 4, 4, 5, 5), critical="no"):
    return {
        "case_id": case_id,
        "reviewer_id": reviewer,
        "factuality": scores[0],
        "citation_support": scores[1],
        "actionability": scores[2],
        "safety": scores[3],
        "clarity": scores[4],
        "critical_error": critical,
        "notes": "",
    }


def test_human_review_requires_two_distinct_reviewers_per_case():
    with pytest.raises(ValueError, match="至少需要两名"):
        validate_review_rows([_row("a", "reviewer-1")])
    with pytest.raises(ValueError, match="重复"):
        validate_review_rows([_row("a", "same"), _row("a", "same")])


def test_human_review_rejects_missing_or_out_of_range_scores():
    rows = [_row("a", "r1"), _row("a", "r2")]
    rows[0]["factuality"] = 6
    with pytest.raises(ValueError, match="1-5"):
        validate_review_rows(rows)


def test_human_review_aggregates_quality_safety_and_agreement():
    report = aggregate_human_reviews(
        [
            _row("a", "r1", (5, 4, 4, 5, 5)),
            _row("a", "r2", (4, 4, 5, 5, 4)),
            _row("b", "r1", (3, 3, 4, 5, 4)),
            _row("b", "r2", (3, 4, 4, 5, 4)),
        ]
    )
    assert report["case_count"] == 2
    assert report["review_count"] == 4
    assert report["critical_error_rate_pct"] == 0
    assert report["case_pass_rate_pct"] == 50
    assert report["pairwise_within_one_point_agreement_pct"] == 100


def test_critical_error_fails_case_and_markdown_states_limits():
    report = aggregate_human_reviews(
        [_row("a", "r1", critical="yes"), _row("a", "r2")]
    )
    assert report["case_pass_rate_pct"] == 0
    assert report["critical_error_rate_pct"] == 50
    markdown = render_human_review_markdown(report)
    assert "严重错误率" in markdown
    assert "不替代临床验证" in markdown
