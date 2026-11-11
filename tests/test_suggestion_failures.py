import json
from dataclasses import replace
from pathlib import Path

from docket.evaluation import compare_choices, compare_review_suggestions, write_report
from tests.test_evaluation import label, outcome


def test_failed_suggestions_do_not_count_as_abstentions_or_change_proposals():
    gold = label("no_record", "review", None)
    rule = outcome("would_review", record_id="UNIT-0001")
    failed = replace(
        rule, suggestion={"record_id": None, "suggestion_failure_reason": "parse_error"}
    )
    missing = compare_review_suggestions([gold], [failed], [rule])
    assert missing["review_documents"] == 0
    assert missing["excluded_suggestion_failures"] == 1
    assert missing["suggestion_correct"] == missing["rules_correct"] == 0
    assert missing["suggestion_accuracy"] is None and missing["rules_accuracy"] is None
    assert missing["suggestion_wilson_95"] is None and missing["documents"] == []
    assert compare_choices([failed], [rule])["review_proposal_changes"] == 0

    abstained = replace(rule, suggestion={"record_id": None})
    counted = compare_review_suggestions([gold], [abstained], [rule])
    assert counted["review_documents"] == counted["suggestion_correct"] == 1
    assert counted["suggestion_accuracy"] == 1
    assert "excluded_suggestion_failures" not in counted
    assert compare_choices([abstained], [rule])["review_proposal_changes"] == 1

    filed = replace(failed, status="would_file")
    excluded = compare_review_suggestions([gold], [filed], [rule])
    assert excluded["review_documents"] == 0
    assert "excluded_suggestion_failures" not in excluded


def test_mixed_review_suggestions_use_only_available_answers():
    labels = [label("no_record", "review", None) for _ in range(3)]
    for i, gold in enumerate(labels):
        gold.document_id = str(i)
    rules = [replace(outcome("would_review"), document_id=str(i)) for i in range(3)]
    suggestions = [
        replace(
            rules[0], suggestion={"record_id": None, "suggestion_failure_reason": "replay_miss"}
        ),
        replace(rules[1], suggestion={"record_id": None}),
        replace(rules[2], suggestion={"record_id": "UNIT-0001"}),
    ]
    result = compare_review_suggestions(labels, suggestions, rules)
    assert result["review_documents"] == 2 and result["excluded_suggestion_failures"] == 1
    assert result["suggestion_correct"] == 1 and result["suggestion_accuracy"] == 0.5
    assert result["rules_correct"] == 0 and result["rules_accuracy"] == 0
    assert [row["document_id"] for row in result["documents"]] == ["1", "2"]


def test_report_states_zero_and_nonzero_failure_counts(tmp_path):
    report = json.loads(Path("evals/reports/2026-11-11-seed-11.json").read_text())
    for count in (0, 2):
        if count:
            report["review_suggestion_comparison"]["excluded_suggestion_failures"] = count
        path = write_report(report, tmp_path)
        assert (
            f"Suggestion failures excluded from this comparison: {count}."
            in path.with_suffix(".md").read_text()
        )
