"""The report text says what changed a filing decision, not just that one changed."""

import json
from pathlib import Path

from docket.evaluation import write_report


def report_with(results, changed):
    report = json.loads(Path("evals/reports/2026-11-13-seed-11.json").read_text())
    report["docket_results"] = results
    report["choice_comparison"] = {
        **report["choice_comparison"],
        "filing_decision_changes": len(changed),
        "routing_changes": len(changed),
        "changed_document_ids": changed,
    }
    return report


def test_changes_from_unreadable_categories_are_named_as_such(tmp_path):
    stored = json.loads(Path("evals/reports/2026-11-13-seed-11.json").read_text())
    path = write_report(stored, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert "choose step changes" not in text
    assert (
        "Docket's filing decisions differ from the rules-only baseline on 10 of 180 documents."
        in text
    )
    assert (
        "10 of them went to review because the category answer was unreadable; "
        "the model's record suggestion changed none." in text
    )


def test_no_change_says_so(tmp_path):
    report = report_with([{"document_id": "doc-0001", "reason": "threshold_met"}], changed=[])
    text = write_report(report, tmp_path).with_suffix(".md").read_text()
    assert "differ from the rules-only baseline on 0 of 180 documents." in text
    assert "went to review because the category answer was unreadable" not in text


def test_other_changes_are_not_put_down_to_the_category(tmp_path):
    results = [
        {"document_id": "doc-0001", "reason": "category_unreadable"},
        {"document_id": "doc-0002", "reason": "insufficient_corroboration"},
    ]
    report = report_with(results, changed=["doc-0001", "doc-0002"])
    text = write_report(report, tmp_path).with_suffix(".md").read_text()
    assert "on 2 of 180 documents." in text
    assert (
        "1 of them went to review because the category answer was unreadable; "
        "1 changed for another reason." in text
    )
